#!/usr/bin/env python3
"""FFmpeg Frontend - Interface graphique pour FFmpeg avec gestion de queue et multi-threading"""

import os
import json
import subprocess
import sys
import threading
import queue
import tempfile
import copy
from pathlib import Path
from tkinter import Tk, filedialog, ttk, Menu, messagebox, StringVar, BooleanVar, Text, Toplevel, IntVar, DoubleVar
import tkinter as tk
import ffmpeg
import time

# Ajout des imports pour les nouvelles fonctionnalités
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

try:
    import psutil  # type: ignore
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    psutil = None
    DND_AVAILABLE = False

# Ajout des imports pour watchdog (surveillance de dossier)
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    Observer = None
    class FileSystemEventHandler:
        pass

CONFIG_DIR = Path.home() / ".ffmpeg_frontend"
CONFIG_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "concurrency": 4,
    "video_concurrency": 1,
    "progress_refresh_interval": 2,
    "default_video_encoder": "libx264",
    "default_audio_encoder": "aac",
    "custom_flags": "",
    "keep_folder_structure": True,
    "default_image_encoder": "libwebp",
    "presets": {
        "H264 High Quality": {
            "mode": "video",
            "codec": "h264",
            "encoder": "libx264",
            "quality": "18",
            "cq_value": "",
            "preset": "slow",
            "container": "mp4",
            "custom_flags": ""
        },
        "H264 Fast": {
            "mode": "video", 
            "codec": "h264",
            "encoder": "libx264",
            "quality": "23",
            "cq_value": "",
            "preset": "fast", 
            "container": "mp4",
            "custom_flags": ""
        },
        "WebP Images": {
            "mode": "image",
            "codec": "webp",
            "encoder": "libwebp",
            "quality": "80",
            "cq_value": "",
            "preset": "",
            "container": "webp",
            "custom_flags": ""
        }
    }
}


class Settings:
    data: dict = DEFAULT_SETTINGS.copy()

    @classmethod
    def load(cls):
        if SETTINGS_FILE.exists():
            try:
                cls.data.update(json.loads(SETTINGS_FILE.read_text()))
            except Exception:
                print("Failed to parse settings.json, using defaults")
        else:
            cls.save()

    @classmethod
    def save(cls):
        SETTINGS_FILE.write_text(json.dumps(cls.data, indent=2))


class FFmpegHelpers:
    """Utility functions to query ffmpeg."""

    _encoders_cache = None
    _codecs_cache = None
    _hw_encoders_cache = None

    @classmethod
    def available_encoders(cls):
        if cls._encoders_cache is None:
            try:
                result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
                encoders = []
                for line in result.stdout.split('\n'):
                    if line.startswith(' V') or line.startswith(' A'):
                        parts = line.split()
                        if len(parts) >= 3:
                            encoder_name = parts[1]
                            description = ' '.join(parts[2:])
                            # Taguer les encodeurs hardware
                            if cls.is_hardware_encoder(encoder_name):
                                description += " (Hardware)"
                            encoders.append((encoder_name, description))
                cls._encoders_cache = encoders
            except FileNotFoundError:
                cls._encoders_cache = []
        return cls._encoders_cache

    @classmethod
    def is_hardware_encoder(cls, encoder_name: str) -> bool:
        """Détermine si un encodeur utilise l'accélération hardware"""
        hw_patterns = [
            # NVIDIA
            'nvenc', 'h264_nvenc', 'hevc_nvenc', 'av1_nvenc',
            # AMD
            'amf', 'h264_amf', 'hevc_amf',
            # Intel QuickSync
            'qsv', 'h264_qsv', 'hevc_qsv', 'av1_qsv',
            # Apple VideoToolbox
            'videotoolbox', 'h264_videotoolbox', 'hevc_videotoolbox',
            # ARM Mali
            'v4l2m2m',
            # Other hardware
            'vaapi', 'vdpau', 'mediacodec'
        ]
        return any(pattern in encoder_name.lower() for pattern in hw_patterns)

    @classmethod
    def get_hardware_encoders(cls):
        """Retourne uniquement les encodeurs hardware disponibles"""
        if cls._hw_encoders_cache is None:
            all_encoders = cls.available_encoders()
            cls._hw_encoders_cache = [(name, desc) for name, desc in all_encoders 
                                     if cls.is_hardware_encoder(name)]
        return cls._hw_encoders_cache

    @classmethod
    def available_codecs(cls):
        if cls._codecs_cache is None:
            try:
                result = subprocess.run(["ffmpeg", "-hide_banner", "-codecs"], capture_output=True, text=True)
                video, audio, image = set(), set(), set()
                for line in result.stdout.splitlines():
                    if line.startswith(" ") and "(" not in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            flags, name = parts[0], parts[1]
                            if "V" in flags:
                                video.add(name)
                            elif "A" in flags:
                                audio.add(name)
                            # Accept more for image: known image codecs/extensions
                            if name.lower() in {"jpg", "jpeg", "webp", "png", "avif", "tiff", "bmp", "jxl", "jpegxl"}:
                                image.add(name)
                            elif "S" in flags or "I" in flags:
                                image.add(name)
                # Add common image codecs if missing
                for extra in ["jpg", "jpeg", "webp", "png", "avif", "tiff", "bmp", "jxl", "jpegxl"]:
                    image.add(extra)
                cls._codecs_cache = {
                    "video": sorted(video),
                    "audio": sorted(audio),
                    "image": sorted(image),
                }
            except FileNotFoundError:
                messagebox.showerror("FFmpeg not found", "ffmpeg executable not found in PATH.")
                sys.exit(1)
        return cls._codecs_cache


class EncodeJob:
    def __init__(self, src_path: Path, dst_path: Path, mode: str):
        self.src_path = src_path
        self.dst_path = dst_path
        self.mode = mode
        self.encoder: str = ""
        self.quality: str = ""
        self.cq_value: str = ""
        self.preset: str = ""
        self.custom_flags: str = ""
        self.status: str = "pending"  # pending, running, paused, done, error, cancelled
        self.progress: float = 0.0
        self.duration: float = None
        self.process: subprocess.Popen = None
        self.is_paused: bool = False
        self.is_cancelled: bool = False
        
        # Nouveaux paramètres d'encodage
        self.video_mode: str = "quality"  # quality, bitrate
        self.bitrate: str = ""  # Pour l'encodage basé sur le bitrate
        self.multipass: bool = False  # Multi-pass encoding
        self.longest_side: str = ""  # Pour les images
        self.megapixels: str = ""  # Pour les images
        
        # Filtres avancés
        self.filters = {
            "brightness": 0,    # -100 à 100
            "contrast": 0,      # -100 à 100
            "saturation": 0,    # -100 à 100
            "gamma": 1.0,       # 0.1 à 3.0
            "hue": 0,          # -180 à 180
            "sharpness": 0,     # -10 à 10
            "noise_reduction": 0, # 0 à 100
            "scale_width": 0,   # 0 = pas de resize
            "scale_height": 0,  # 0 = pas de resize
            "crop_x": 0,
            "crop_y": 0,
            "crop_w": 0,
            "crop_h": 0,
            "rotate": 0,        # 0, 90, 180, 270
            "flip_h": False,    # flip horizontal
            "flip_v": False,    # flip vertical
        }
        
        # Configuration des pistes audio
        self.audio_config = {
            "mode": "auto",  # auto, copy, encode, remove
            "selected_tracks": [],  # Liste des indices de pistes à garder
            "audio_codec": "aac",
            "audio_bitrate": "128k"
        }

    def cancel(self):
        """Annule le job et termine le processus FFmpeg"""
        self.is_cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.status = "cancelled"

    def pause(self):
        """Met en pause le job (suspend le processus)"""
        if self.process and self.process.poll() is None and not self.is_paused:
            try:
                import signal
                self.process.send_signal(signal.SIGSTOP)
                self.is_paused = True
                self.status = "paused"
            except:
                pass

    def resume(self):
        """Reprend le job en pause"""
        if self.process and self.process.poll() is None and self.is_paused:
            try:
                import signal
                self.process.send_signal(signal.SIGCONT)
                self.is_paused = False
                self.status = "running"
            except:
                pass

    def __str__(self):
        return f"EncodeJob({self.src_path.name} -> {self.dst_path.name}, {self.status})"


def build_ffmpeg_stream(job: EncodeJob):
    """Construit le stream FFmpeg pour un job donné avec filtres"""
    input_stream = ffmpeg.input(str(job.src_path))
    
    # Appliquer les filtres si présents
    if any(v != 0 and v != 1.0 and v != False for v in job.filters.values()):
        filter_chain = []
        
        # Filtres de couleur et luminosité
        eq_filters = []
        if job.filters["brightness"] != 0:
            eq_filters.append(f"brightness={job.filters['brightness'] / 100.0}")
        if job.filters["contrast"] != 0:
            eq_filters.append(f"contrast={1 + (job.filters['contrast'] / 100.0)}")
        if job.filters["saturation"] != 0:
            eq_filters.append(f"saturation={1 + (job.filters['saturation'] / 100.0)}")
        if job.filters["gamma"] != 1.0:
            eq_filters.append(f"gamma={job.filters['gamma']}")
        
        if eq_filters:
            input_stream = input_stream.filter('eq', **dict(param.split('=') for param in eq_filters))
        
        # Hue
        if job.filters["hue"] != 0:
            input_stream = input_stream.filter('hue', h=job.filters["hue"])
        
        # Crop
        if job.filters["crop_w"] > 0 and job.filters["crop_h"] > 0:
            input_stream = input_stream.filter('crop', 
                                             job.filters["crop_w"], 
                                             job.filters["crop_h"],
                                             job.filters["crop_x"], 
                                             job.filters["crop_y"])
        
        # Scale (priorité aux nouveaux contrôles image)
        if job.mode == "image" and (job.longest_side or job.megapixels):
            # Gestion spéciale pour les images
            if job.longest_side and job.longest_side != "Original":
                try:
                    longest = int(job.longest_side)
                    input_stream = input_stream.filter('scale', f'if(gt(iw,ih),{longest},-1)', f'if(gt(ih,iw),{longest},-1)')
                except ValueError:
                    pass
            elif job.megapixels and job.megapixels != "Original":
                try:
                    mp = float(job.megapixels)
                    # Calculer la résolution basée sur les mégapixels en gardant le ratio
                    input_stream = input_stream.filter('scale', f'trunc(sqrt({mp}*1000000*iw/ih)/2)*2', f'trunc(sqrt({mp}*1000000*ih/iw)/2)*2')
                except ValueError:
                    pass
        elif job.filters["scale_width"] > 0 or job.filters["scale_height"] > 0:
            w = job.filters["scale_width"] if job.filters["scale_width"] > 0 else -1
            h = job.filters["scale_height"] if job.filters["scale_height"] > 0 else -1
            input_stream = input_stream.filter('scale', w, h)
        
        # Rotation
        if job.filters["rotate"] == 90:
            input_stream = input_stream.filter('transpose', 1)
        elif job.filters["rotate"] == 180:
            input_stream = input_stream.filter('transpose', 1).filter('transpose', 1)
        elif job.filters["rotate"] == 270:
            input_stream = input_stream.filter('transpose', 2)
        
        # Flip
        if job.filters["flip_h"]:
            input_stream = input_stream.filter('hflip')
        if job.filters["flip_v"]:
            input_stream = input_stream.filter('vflip')
        
        # Sharpness
        if job.filters["sharpness"] != 0:
            if job.filters["sharpness"] > 0:
                input_stream = input_stream.filter('unsharp', 5, 5, job.filters["sharpness"] * 0.5)
            else:
                input_stream = input_stream.filter('unsharp', 5, 5, job.filters["sharpness"] * 0.2)
        
        # Noise reduction (uniquement si disponible)
        if job.filters["noise_reduction"] > 0:
            try:
                input_stream = input_stream.filter('bm3d', sigma=job.filters["noise_reduction"] * 0.1)
            except:
                # Fallback vers nlmeans si bm3d n'est pas disponible
                input_stream = input_stream.filter('nlmeans', s=job.filters["noise_reduction"] * 0.3)
    
    # Configuration de l'encodeur
    output_kwargs = {}
    
    # Configuration audio
    if job.mode in ["video", "audio"] and hasattr(job, 'audio_config'):
        audio_mode = job.audio_config.get("mode", "auto")
        
        if audio_mode == "remove":
            output_kwargs['an'] = None  # Remove audio
        elif audio_mode == "copy":
            output_kwargs['acodec'] = 'copy'
        elif audio_mode == "encode":
            output_kwargs['acodec'] = job.audio_config.get("audio_codec", "aac")
            output_kwargs['ab'] = job.audio_config.get("audio_bitrate", "128k")
        # auto mode: laisser FFmpeg décider
        
        # Sélection de pistes spécifiques
        selected_tracks = job.audio_config.get("selected_tracks", [])
        if selected_tracks and audio_mode != "remove":
            # Construire la map des pistes audio
            for i, track_index in enumerate(selected_tracks):
                output_kwargs[f'map'] = f'0:a:{track_index}' if i == 0 else [output_kwargs.get('map', []), f'0:a:{track_index}']
    
    if job.encoder:
        if job.mode == "video":
            output_kwargs['vcodec'] = job.encoder
            
            # Gestion du mode d'encodage (qualité vs bitrate)
            if hasattr(job, 'video_mode') and job.video_mode == "bitrate" and job.bitrate:
                # Mode bitrate
                output_kwargs['b:v'] = job.bitrate
                if hasattr(job, 'multipass') and job.multipass:
                    # Multi-pass encoding (nécessite une implémentation spéciale)
                    output_kwargs['pass'] = '1'  # Premier pass sera géré séparément
            else:
                # Mode qualité (par défaut)
                # Utiliser CQ pour les encodeurs hardware, CRF/quality pour les software
                if 'nvenc' in job.encoder or 'qsv' in job.encoder or 'amf' in job.encoder or 'videotoolbox' in job.encoder:
                    # Encodeurs hardware - utiliser le champ CQ
                    if job.cq_value:
                        if 'nvenc' in job.encoder:
                            output_kwargs['cq'] = job.cq_value
                        elif 'qsv' in job.encoder:
                            output_kwargs['q'] = job.cq_value
                        elif 'amf' in job.encoder:
                            output_kwargs['qp_i'] = job.cq_value
                        elif 'videotoolbox' in job.encoder:
                            output_kwargs['q:v'] = job.cq_value
                else:
                    # Encodeurs software - utiliser le champ quality/CRF
                    if job.quality:
                        output_kwargs['crf'] = job.quality
            
            if job.preset:
                output_kwargs['preset'] = job.preset
        elif job.mode == "audio":
            output_kwargs['acodec'] = job.encoder
            # Pour les codecs audio, utiliser le preset pour le bitrate s'il est défini
            if job.preset and job.preset.endswith('k'):
                # Le preset contient un bitrate (ex: "128k")
                output_kwargs['ab'] = job.preset
            elif job.quality:
                if job.quality.isdigit():
                    output_kwargs['ab'] = f"{job.quality}k"
                else:
                    output_kwargs['aq'] = job.quality
        else:  # image
            output_kwargs['vcodec'] = job.encoder
            if job.quality:
                output_kwargs['q:v'] = job.quality
    
    # Ajouter des flags personnalisés depuis le job
    if hasattr(job, 'custom_flags') and job.custom_flags:
        # Parser les flags personnalisés (format simple: -flag value -flag2 value2)
        flags = job.custom_flags.split()
        i = 0
        while i < len(flags):
            if flags[i].startswith('-'):
                flag_name = flags[i][1:]  # Retirer le -
                if i + 1 < len(flags) and not flags[i + 1].startswith('-'):
                    # Flag avec valeur
                    flag_value = flags[i + 1]
                    output_kwargs[flag_name] = flag_value
                    i += 2
                else:
                    # Flag booléen (sans valeur)
                    output_kwargs[flag_name] = None
                    i += 1
            else:
                i += 1
    
    output_stream = ffmpeg.output(input_stream, str(job.dst_path), **output_kwargs)
    return output_stream


class WorkerPool:
    def __init__(self, max_workers: int, progress_callback=None, log_callback=None):
        self.max_workers = max_workers
        self.job_queue = queue.Queue()
        self.threads = []
        self.running = False
        self.progress_callback = progress_callback
        self.log_callback = log_callback

    def start(self):
        """Démarre les threads workers"""
        if not self.running:
            self.running = True
            for i in range(self.max_workers):
                thread = threading.Thread(target=self._worker, daemon=True)
                thread.start()
                self.threads.append(thread)

    def stop(self):
        """Arrête les workers proprement"""
        self.running = False
        # Ajouter des sentinelles pour débloquer les workers
        for _ in range(self.max_workers):
            self.job_queue.put(None)
        # Attendre que tous les threads se terminent
        for thread in self.threads:
            thread.join(timeout=1.0)
        self.threads.clear()

    def _worker(self):
        """Boucle principale du worker thread"""
        while self.running:
            try:
                job = self.job_queue.get(timeout=1.0)
                if job is None:  # Sentinelle pour arrêter
                    break
                self._run_job(job)
                self.job_queue.task_done()
            except queue.Empty:
                continue

    def _run_job(self, job: EncodeJob):
        try:
            # Vérifier si le job a été annulé avant de commencer
            if job.is_cancelled:
                return
                
            # Log du début du job
            if self.log_callback:
                self.log_callback(job, f"Starting encoding: {job.src_path.name} -> {job.dst_path.name}", "info")
                
            # Obtenir la durée du fichier pour le calcul du progrès
            try:
                rr = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(job.src_path)], 
                                   capture_output=True, text=True, timeout=30)
                if rr.returncode == 0 and rr.stdout.strip():
                    duration_str = rr.stdout.strip()
                    if duration_str != "N/A":
                        job.duration = float(duration_str)
                        if self.log_callback:
                            self.log_callback(job, f"Duration detected: {job.duration:.2f}s", "info")
            except (subprocess.TimeoutExpired, ValueError) as e:
                if self.log_callback:
                    self.log_callback(job, f"Could not detect duration: {e}", "warning")

            # Construire la commande FFmpeg
            stream = build_ffmpeg_stream(job)
            args = ffmpeg.compile(stream, overwrite_output=True) + ["-progress", "-", "-nostats"]
            
            if self.log_callback:
                self.log_callback(job, f"FFmpeg command: {' '.join(args)}", "info")
            
            job.status = "running"
            if self.progress_callback:
                self.progress_callback(job)
            
            # Lancer le processus FFmpeg
            job.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
            
            # Créer un thread pour lire stderr
            stderr_thread = threading.Thread(target=self._read_stderr, args=(job,), daemon=True)
            stderr_thread.start()
            
            # Lire les informations de progrès
            while True:
                # Vérifier si le job a été annulé
                if job.is_cancelled:
                    job.cancel()
                    break
                    
                line_bytes = job.process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode('utf-8').strip()
                if line.startswith("out_time_ms="):
                    try:
                        time_str = line.split("=")[1].strip()
                        if time_str != "N/A":
                            ms = int(time_str)
                            if job.duration:
                                job.progress = min(ms / 1e6 / job.duration, 1.0)
                                if self.progress_callback:
                                    self.progress_callback(job)
                    except (ValueError, IndexError):
                        # Skip invalid progress values
                        pass
                elif line.startswith("progress=") and line.endswith("end"):
                    job.progress = 1.0
                    if self.progress_callback:
                        self.progress_callback(job)
            
            # Attendre la fin du processus
            job.process.wait()
            
            # Définir le statut final
            if job.is_cancelled:
                job.status = "cancelled"
                if self.log_callback:
                    self.log_callback(job, "Job cancelled by user", "warning")
            elif job.process.returncode == 0:
                job.status = "done"
                job.progress = 1.0
                if self.log_callback:
                    self.log_callback(job, "Encoding completed successfully", "info")
            else:
                job.status = "error"
                if self.log_callback:
                    self.log_callback(job, f"Encoding failed with return code {job.process.returncode}", "error")
                
        except Exception as e:
            if not job.is_cancelled:
                job.status = "error"
            error_msg = f"Encoding error: {e}"
            print(error_msg)
            if self.log_callback:
                self.log_callback(job, error_msg, "error")
        finally:
            if self.progress_callback:
                self.progress_callback(job)
    
    def _read_stderr(self, job: EncodeJob):
        """Lit la sortie stderr de FFmpeg dans un thread séparé"""
        if not job.process or not job.process.stderr:
            return
            
        try:
            while True:
                line_bytes = job.process.stderr.readline()
                if not line_bytes:
                    break
                    
                line = line_bytes.decode('utf-8', errors='ignore').strip()
                if line and self.log_callback:
                    # Déterminer le type de log basé sur le contenu
                    log_type = "info"
                    if "error" in line.lower() or "failed" in line.lower():
                        log_type = "error"
                    elif "warning" in line.lower():
                        log_type = "warning"
                    elif "frame=" in line or "fps=" in line:
                        log_type = "progress"
                    
                    self.log_callback(job, line, log_type)
        except Exception as e:
            if self.log_callback:
                self.log_callback(job, f"Error reading stderr: {e}", "error")

    def submit(self, job: EncodeJob):
        self.job_queue.put(job)


class MainWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("FFmpeg Frontend")
        self.root.geometry("1200x800")
        
        # Liste des jobs
        self.jobs: list[EncodeJob] = []
        self.job_counter = 0
        
        # Variables d'état
        self.is_running = False
        self.input_folder = StringVar()
        self.output_folder = StringVar()
        
        # Variable pour la surveillance de dossier
        self.watch_var = BooleanVar(value=False)
        self.log_viewer = None
        
        # Variables pour l'inspecteur média
        self.resolution_var = StringVar(value="N/A")
        self.duration_var = StringVar(value="N/A")
        self.vcodec_var = StringVar(value="N/A")
        self.vbitrate_var = StringVar(value="N/A")
        self.acodec_var = StringVar(value="N/A")
        self.abitrate_var = StringVar(value="N/A")
        self.achannels_var = StringVar(value="N/A")
        
        # Pool de workers
        self.pool = WorkerPool(
            max_workers=Settings.data.get("concurrency", 4),
            progress_callback=self._on_job_progress,
            log_callback=self._on_job_log
        )
        
        self._build_menu()
        self._build_layout()
        self._setup_drag_drop()
        self._update_preset_list()
        
        # Démarrer le pool
        self.pool.start()

    # === GUI construction ===
    def _build_menu(self):
        menubar = Menu(self.root)
        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(label="Add Files…", command=self._add_files)
        file_menu.add_command(label="Add Folder…", command=self._add_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Add Files or Folder…", command=self._add_files_or_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)
        
        # Menu Edit
        edit_menu = Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Batch Operations", command=self._batch_operations)
        edit_menu.add_command(label="Advanced Filters", command=self._advanced_filters)
        edit_menu.add_command(label="Audio Tracks", command=self._configure_audio_tracks)
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear Queue", command=self._clear_queue)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        # Menu Presets
        preset_menu = Menu(menubar, tearoff=0)
        preset_menu.add_command(label="Save Current as Preset…", command=self._save_preset)
        preset_menu.add_separator()
        # Ajouter les presets existants au menu
        for preset_name in Settings.data["presets"].keys():
            preset_menu.add_command(
                label=preset_name, 
                command=lambda name=preset_name: self._load_preset_by_name(name)
            )
        menubar.add_cascade(label="Presets", menu=preset_menu)
        
        # Menu View
        view_menu = Menu(menubar, tearoff=0)
        view_menu.add_command(label="Show Log Viewer", command=self._show_log_viewer)
        menubar.add_cascade(label="View", menu=view_menu)

        settings_menu = Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Preferences…", command=self._open_settings)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menubar)

    def _build_layout(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Onglets pour différents types de contenu
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        file_section = ttk.LabelFrame(notebook, text="File Selection", padding=15)
        file_section.pack(fill="x", pady=(0, 15))

        settings_section = ttk.LabelFrame(notebook, text="Encoding Settings", padding=15)
        settings_section.pack(fill="x", pady=(0, 15))

        queue_section = ttk.LabelFrame(notebook, text="Encoding Queue", padding=10)
        queue_section.pack(fill="both", expand=True, pady=(0, 15))

        inspector_frame = ttk.LabelFrame(notebook, text="Inspecteur média", padding=10)
        inspector_frame.pack(fill="x", pady=(0, 15))

        notebook.add(file_section, text="Sélection de fichiers")
        notebook.add(settings_section, text="Paramètres d'encodage")
        notebook.add(queue_section, text="File d'attente")
        notebook.add(inspector_frame, text="Inspecteur média")

        # === MEDIA INSPECTOR SECTION ===
        paned_window = ttk.PanedWindow(inspector_frame, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True)

        # Left pane: file list
        inspector_list_frame = ttk.Frame(paned_window, width=300)
        paned_window.add(inspector_list_frame, weight=1)

        ttk.Label(inspector_list_frame, text="Fichiers en file d'attente", font=("Helvetica", 10, "bold")).pack(pady=(0, 5))
        
        self.inspector_tree = ttk.Treeview(inspector_list_frame, columns=("file",), show="headings", height=10)
        self.inspector_tree.heading("file", text="Fichier")
        self.inspector_tree.pack(fill=tk.BOTH, expand=True)
        self.inspector_tree.bind("<<TreeviewSelect>>", self._on_inspector_selection_change)

        # Right pane: media info (will be populated dynamically)
        self.inspector_info_frame = ttk.Frame(paned_window)
        paned_window.add(self.inspector_info_frame, weight=2)
        
        # === FILE SELECTION SECTION ===
        self.input_folder = StringVar(value="No input folder selected")
        self.output_folder = StringVar(value="No output folder selected")

        # Clean folder selection grid
        folder_grid = ttk.Frame(file_section)
        folder_grid.pack(fill="x")

        # Input folder
        ttk.Label(folder_grid, text="Input:", font=("Helvetica", 11, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.input_folder_entry = ttk.Entry(folder_grid, textvariable=self.input_folder, width=60, font=("Helvetica", 10))
        self.input_folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Button(folder_grid, text="Browse", command=self._select_input_folder, width=8).grid(row=0, column=2)

        # Output folder
        ttk.Label(folder_grid, text="Output:", font=("Helvetica", 11, "bold")).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        self.output_folder_entry = ttk.Entry(folder_grid, textvariable=self.output_folder, width=60, font=("Helvetica", 10))
        self.output_folder_entry.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(8, 0))
        ttk.Button(folder_grid, text="Browse", command=self._select_output_folder, width=8).grid(row=1, column=2, pady=(8, 0))
        
        # Info label for output behavior
        info_label = ttk.Label(folder_grid, text="💡 Optional: If no output folder is selected, files will be saved in the same folder as source with encoder suffix (e.g., filename_x265.mp4)", 
                              font=("Helvetica", 9), foreground="gray")
        info_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(5, 0))

        folder_grid.columnconfigure(1, weight=1)

        # Add buttons row
        buttons_row = ttk.Frame(file_section)
        buttons_row.pack(fill="x", pady=(15, 0))
        
        ttk.Button(buttons_row, text="Add Files", command=self._add_files).pack(side="left", padx=(0, 10))
        ttk.Button(buttons_row, text="Add Folder", command=self._add_folder).pack(side="left", padx=(0, 10))
        ttk.Button(buttons_row, text="Find Files in Input Folder", command=self._find_and_add_files).pack(side="left")

        # Ajout du cadre pour la surveillance de dossier
        watch_frame = ttk.LabelFrame(file_section, text="Surveillance de dossier", padding="5")
        watch_frame.pack(fill=tk.X, pady=(15, 0))
        watch_toggle = ttk.Checkbutton(watch_frame, text="Surveiller le dossier d'entrée", variable=self.watch_var, command=self._toggle_watch)
        watch_toggle.pack(side=tk.TOP, fill=tk.X)
        
        preset_frame = ttk.Frame(watch_frame)
        preset_frame.pack(fill=tk.X, pady=5)
        ttk.Label(preset_frame, text="Préréglage pour les nouveaux fichiers:").pack(side=tk.LEFT)
        self.watch_preset_combo = ttk.Combobox(preset_frame, state="readonly")
        self.watch_preset_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        preset_names = list(Settings.data.get("presets", {}).keys())
        if preset_names:
            self.watch_preset_combo['values'] = preset_names
            self.watch_preset_combo.set(preset_names[0])
        
        self.watch_status = ttk.Label(watch_frame, text="Statut: Inactif")
        self.watch_status.pack(side=tk.BOTTOM, fill=tk.X, pady=5)

        # === ENCODING SETTINGS SECTION ===
        # Top row - Media Type and Quick Presets
        top_row = ttk.Frame(settings_section)
        top_row.pack(fill="x", pady=(0, 15))

        ttk.Label(top_row, text="Media Type:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.global_type_var = StringVar(value="video")
        type_combo = ttk.Combobox(top_row, textvariable=self.global_type_var, 
                                 values=["video", "audio", "image"], width=10, state="readonly")
        type_combo.pack(side="left", padx=(0, 20))
        type_combo.bind("<<ComboboxSelected>>", lambda e: self._update_codec_choices())

        ttk.Label(top_row, text="Quick Presets:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.preset_name_var = StringVar(value="")
        self.preset_combo = ttk.Combobox(top_row, textvariable=self.preset_name_var, width=18, state="readonly")
        self.preset_combo.pack(side="left", padx=(0, 10))
        self.preset_combo.bind("<<ComboboxSelected>>", self._load_preset)

        ttk.Button(top_row, text="Save", command=self._save_preset, width=4).pack(side="left", padx=(2, 2))
        ttk.Button(top_row, text="Delete", command=self._delete_preset, width=5).pack(side="left")

        # Codec and Encoder rows
        codec_encoder_frame = ttk.Frame(settings_section)
        codec_encoder_frame.pack(fill="x", pady=(0, 15))

        # Codec selection row
        codec_row = ttk.Frame(codec_encoder_frame)
        codec_row.pack(fill="x", pady=(0, 8))

        ttk.Label(codec_row, text="1. Codec:", font=("Helvetica", 10, "bold"), width=10).pack(side="left", padx=(0, 5))
        self.global_codec_var = StringVar(value="")
        self.global_codec_combo = ttk.Combobox(codec_row, textvariable=self.global_codec_var, width=25, state="readonly")
        self.global_codec_combo.pack(side="left", padx=(0, 10))
        self.global_codec_combo.bind("<<ComboboxSelected>>", lambda e: self._update_encoder_choices())

        # Help text for codec button
        ttk.Label(codec_row, text="Smart apply", font=("Helvetica", 8), foreground="gray").pack(side="right", padx=(5, 5))
        ttk.Button(codec_row, text="Apply Codec", command=self._apply_codec_smart).pack(side="right")

        # Encoder selection row  
        encoder_row = ttk.Frame(codec_encoder_frame)
        encoder_row.pack(fill="x")

        ttk.Label(encoder_row, text="2. Encoder:", font=("Helvetica", 10, "bold"), width=10).pack(side="left", padx=(0, 5))
        self.global_encoder_var = StringVar(value="")
        self.global_encoder_combo = ttk.Combobox(encoder_row, textvariable=self.global_encoder_var, width=50, state="readonly")
        self.global_encoder_combo.pack(side="left", fill="x", expand=True)
        self.global_encoder_combo.bind("<<ComboboxSelected>>", lambda e: self._update_quality_preset_controls())
        
        # Add help text
        help_label = ttk.Label(encoder_row, text="(Only compatible encoders)", font=("Helvetica", 9), foreground="gray")
        help_label.pack(side="right", padx=(10, 0))

        # Quality Controls
        quality_frame = ttk.Frame(settings_section)
        quality_frame.pack(fill="x", pady=(0, 15))

        # Quality settings row
        quality_row = ttk.Frame(quality_frame)
        quality_row.pack(fill="x", pady=(0, 8))

        # Video encoding mode selector
        self.video_mode_var = StringVar(value="quality")  # quality, bitrate
        self.video_mode_frame = ttk.Frame(quality_row)
        
        ttk.Label(quality_row, text="Quality/CRF:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.quality_var = StringVar(value="")
        self.quality_entry = ttk.Entry(quality_row, textvariable=self.quality_var, width=8)
        self.quality_entry.pack(side="left", padx=(0, 10))

        ttk.Label(quality_row, text="CQ:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.cq_var = StringVar(value="")
        self.cq_entry = ttk.Entry(quality_row, textvariable=self.cq_var, width=8)
        self.cq_entry.pack(side="left", padx=(0, 15))

        # Bitrate controls (initially hidden)
        self.bitrate_label = ttk.Label(quality_row, text="Bitrate:", font=("Helvetica", 10, "bold"))
        self.bitrate_var = StringVar(value="")
        self.bitrate_entry = ttk.Entry(quality_row, textvariable=self.bitrate_var, width=8)
        
        # Multi-pass controls (initially hidden)
        self.multipass_var = BooleanVar(value=False)
        self.multipass_check = ttk.Checkbutton(quality_row, text="Multi-pass", variable=self.multipass_var)

        ttk.Label(quality_row, text="Preset:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.preset_var = StringVar(value="")
        self.preset_combo_quality = ttk.Combobox(quality_row, textvariable=self.preset_var, width=10, state="readonly")
        self.preset_combo_quality.pack(side="left", padx=(0, 15))

        ttk.Label(quality_row, text="Container:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.container_var = StringVar(value="MP4")
        self.container_combo = ttk.Combobox(quality_row, textvariable=self.container_var, 
                                           width=12, state="readonly")
        self.container_combo.pack(side="left")
        self._update_container_choices()

        # Mode selector row (for video)
        mode_row = ttk.Frame(quality_frame)
        self.mode_row = mode_row  # Store reference for showing/hiding
        
        ttk.Label(mode_row, text="Encoding Mode:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        quality_radio = ttk.Radiobutton(mode_row, text="Quality (CRF)", variable=self.video_mode_var, value="quality", command=self._on_video_mode_change)
        quality_radio.pack(side="left", padx=(0, 10))
        bitrate_radio = ttk.Radiobutton(mode_row, text="Bitrate (CBR/VBR)", variable=self.video_mode_var, value="bitrate", command=self._on_video_mode_change)
        bitrate_radio.pack(side="left")

        # Resolution row
        resolution_row = ttk.Frame(quality_frame)
        resolution_row.pack(fill="x", pady=(8, 0))

        ttk.Label(resolution_row, text="Resolution:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.resolution_var_settings = StringVar(value="Original")
        self.resolution_combo = ttk.Combobox(resolution_row, textvariable=self.resolution_var_settings, 
                                           values=["Original", "3840x2160 (4K)", "1920x1080 (1080p)", "1280x720 (720p)", 
                                                  "854x480 (480p)", "640x360 (360p)", "Custom"], 
                                           width=20, state="readonly")
        self.resolution_combo.pack(side="left", padx=(0, 10))
        self.resolution_combo.bind("<<ComboboxSelected>>", self._on_resolution_change)
        
        # Image-specific resolution controls (hidden by default)
        self.image_res_frame = ttk.Frame(resolution_row)
        ttk.Label(self.image_res_frame, text="Longest side:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(10, 5))
        self.longest_side_var = StringVar(value="")
        self.longest_side_combo = ttk.Combobox(self.image_res_frame, textvariable=self.longest_side_var,
                                             values=["Original", "5200", "4096", "3840", "2560", "1920", "1280", "Custom"],
                                             width=10, state="readonly")
        self.longest_side_combo.pack(side="left", padx=(0, 10))
        self.longest_side_combo.bind("<<ComboboxSelected>>", self._on_longest_side_change)
        
        ttk.Label(self.image_res_frame, text="Megapixels:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(10, 5))
        self.megapixels_var = StringVar(value="")
        self.megapixels_combo = ttk.Combobox(self.image_res_frame, textvariable=self.megapixels_var,
                                           values=["Original", "50", "25", "16", "12", "8", "4", "2", "Custom"],
                                           width=8, state="readonly")
        self.megapixels_combo.pack(side="left")
        self.megapixels_combo.bind("<<ComboboxSelected>>", self._on_megapixels_change)
        
        # Custom resolution fields (hidden by default)
        self.custom_width_var = StringVar(value="")
        self.custom_height_var = StringVar(value="")
        self.width_entry = ttk.Entry(resolution_row, textvariable=self.custom_width_var, width=8)
        self.height_entry = ttk.Entry(resolution_row, textvariable=self.custom_height_var, width=8)
        self.x_label = ttk.Label(resolution_row, text="x")
        
        # Custom longest side entry (hidden by default)
        self.custom_longest_var = StringVar(value="")
        self.custom_longest_entry = ttk.Entry(self.image_res_frame, textvariable=self.custom_longest_var, width=8)
        
        # Custom megapixels entry (hidden by default)
        self.custom_mp_var = StringVar(value="")
        self.custom_mp_entry = ttk.Entry(self.image_res_frame, textvariable=self.custom_mp_var, width=8)

        # Custom flags row
        custom_row = ttk.Frame(quality_frame)
        custom_row.pack(fill="x", pady=(8, 0))

        ttk.Label(custom_row, text="Custom Flags:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.custom_flags_var = StringVar(value="")
        self.custom_flags_entry = ttk.Entry(custom_row, textvariable=self.custom_flags_var, width=50)
        self.custom_flags_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        # Help label for custom flags
        help_label = ttk.Label(custom_row, text="(Advanced: additional FFmpeg parameters)", 
                              font=("Helvetica", 9), foreground="gray")
        help_label.pack(side="right")

        # Action buttons row
        action_row = ttk.Frame(quality_frame)
        action_row.pack(fill="x", pady=(8, 0))

        apply_btn = ttk.Button(action_row, text="Apply Settings", command=self._apply_settings_smart)
        apply_btn.pack(side="left", padx=(0, 5))
        
        ttk.Button(action_row, text="Duplicate Selected", command=self._duplicate_selected).pack(side="left", padx=(0, 10))
        
        # Help text for Apply behavior
        help_text = ttk.Label(action_row, text="Applies to selected jobs or all jobs of current type if none selected", 
                             font=("Helvetica", 8), foreground="gray")
        help_text.pack(side="left", padx=(10, 0))

        self._update_preset_list()
        self._update_codec_choices()
        self._update_quality_preset_controls()
        
        # Initialiser l'interface pour le type par défaut
        self._update_media_type_ui(self.global_type_var.get())

        # === ENCODING QUEUE SECTION ===
        # Queue treeview with scrollbar
        queue_frame = ttk.Frame(queue_section)
        queue_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(queue_frame, columns=("file", "encoder", "quality", "progress", "status"), show="headings", height=12)
        for col, label in zip(self.tree["columns"], ["File", "Encoder", "Quality", "Progress", "Status"]):
            self.tree.heading(col, text=label)
            if col == "progress":
                self.tree.column(col, width=80)
            elif col == "status":
                self.tree.column(col, width=80)
            else:
                self.tree.column(col, width=150)

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_queue_selection_change)
        self.tree.bind("<Button-2>", self._on_right_click)  # macOS right-click
        self.tree.bind("<Button-3>", self._on_right_click)  # Windows/Linux right-click

        # Scrollbar for queue
        queue_scrollbar = ttk.Scrollbar(queue_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=queue_scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        queue_scrollbar.pack(side="right", fill="y")

        # Context menu for jobs
        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Edit Job", command=self._edit_selected_job)
        self.context_menu.add_command(label="Advanced Filters", command=self._advanced_filters)
        self.context_menu.add_command(label="Audio Tracks", command=self._configure_audio_tracks)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Pause", command=self._pause_selected_job)
        self.context_menu.add_command(label="Resume", command=self._resume_selected_job)
        self.context_menu.add_command(label="Cancel", command=self._cancel_selected_job)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Batch Operations", command=self._batch_operations)
        self.context_menu.add_command(label="Remove", command=self._remove_selected_job)

        # === CONTROL PANEL ===
        control_panel = ttk.Frame(main_frame)
        control_panel.pack(fill="x")

        # Progress bar
        self.progress_var = StringVar(value="0%")
        self.progress_bar = ttk.Progressbar(control_panel, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 10))

        # Control buttons
        button_panel = ttk.Frame(control_panel)
        button_panel.pack(fill="x")

        # Main action button (larger and prominent)
        self.start_btn = ttk.Button(button_panel, text="Start Encoding", command=self._start_encoding)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # Control buttons (compact icons)
        control_buttons = ttk.Frame(button_panel)
        control_buttons.pack(side="right")

        self.pause_btn = ttk.Button(control_buttons, text="Pause", command=self._pause_all, state="disabled", width=5)
        self.pause_btn.pack(side="left", padx=(0, 2))

        self.resume_btn = ttk.Button(control_buttons, text="Resume", command=self._resume_all, state="disabled", width=6)
        self.resume_btn.pack(side="left", padx=(0, 2))

        self.cancel_btn = ttk.Button(control_buttons, text="Cancel", command=self._cancel_all, state="disabled", width=6)
        self.cancel_btn.pack(side="left", padx=(0, 10))

        ttk.Button(control_buttons, text="Clear", command=self._clear_queue, width=5).pack(side="left")

        # Initialize drag & drop
        if DND_AVAILABLE:
            self._setup_drag_drop()

    # === Callbacks ===
    def _add_files(self):
        paths = filedialog.askopenfilenames(title="Select input files")
        if not paths:
            return
        self._enqueue_paths([Path(p) for p in paths])

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select input folder")
        if not folder:
            return
        root_path = Path(folder)
        all_files = [p for p in root_path.rglob("*") if p.is_file()]
        self._enqueue_paths(all_files)

    def _add_files_or_folder(self):
        """Offre un choix entre ajouter des fichiers ou un dossier"""
        from tkinter import messagebox
        
        choice = messagebox.askyesnocancel(
            "Add Files or Folder",
            "What would you like to add?\n\n"
            "• Yes = Select multiple files\n"
            "• No = Select a folder\n"
            "• Cancel = Nothing",
            icon='question'
        )
        
        if choice is True:  # Yes - Files
            self._add_files()
        elif choice is False:  # No - Folder
            self._add_folder()
        # choice is None = Cancel, do nothing

    def _enqueue_paths(self, paths: list[Path]):
        out_root = Path(self.output_folder.get()) if self.output_folder.get() and not self.output_folder.get().startswith("(no") else None
        keep_structure = Settings.data.get("keep_folder_structure", True)
        input_folder = self.input_folder.get()
        
        for p in paths:
            mode = self._detect_mode(p)
            if self.global_type_var.get() == "unknown":
                self.global_type_var.set(mode)
            
            # Calculer le chemin relatif de manière sécurisée
            if out_root and keep_structure and input_folder and not input_folder.startswith("(no"):
                try:
                    # Essayer de calculer le chemin relatif
                    input_path = Path(input_folder)
                    relative = p.relative_to(input_path)
                except (ValueError, OSError):
                    # Le fichier n'est pas dans le dossier d'entrée ou erreur de calcul
                    # Utiliser juste le nom du fichier
                    relative = p.name
            else:
                relative = p.name
            
            # Génération intelligente du chemin de sortie
            container = self._get_container_from_display(self.container_var.get())
            
            if out_root:
                # Dossier de sortie spécifié
                dst_basename = relative if isinstance(relative, Path) else Path(relative)
                dst_path = out_root / dst_basename
                dst_path = dst_path.with_suffix("." + container)
                dst_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                # Pas de dossier de sortie - utiliser le même dossier que la source avec suffixe
                # Déterminer le suffixe basé sur l'encodeur/codec sélectionné
                encoder_display = self.global_encoder_var.get()
                encoder_name = self._get_encoder_name_from_display(encoder_display) if encoder_display else ""
                
                # Générer un suffixe approprié
                if "x265" in encoder_name or "hevc" in encoder_name:
                    suffix = "_x265"
                elif "x264" in encoder_name or "h264" in encoder_name:
                    suffix = "_x264"
                elif "av1" in encoder_name:
                    suffix = "_av1"
                elif "vp9" in encoder_name:
                    suffix = "_vp9"
                elif "nvenc" in encoder_name:
                    suffix = "_nvenc"
                elif "qsv" in encoder_name:
                    suffix = "_qsv" 
                elif "amf" in encoder_name:
                    suffix = "_amf"
                elif "videotoolbox" in encoder_name:
                    suffix = "_vt"
                elif mode == "audio":
                    if "aac" in encoder_name:
                        suffix = "_aac"
                    elif "mp3" in encoder_name:
                        suffix = "_mp3"
                    elif "opus" in encoder_name:
                        suffix = "_opus"
                    elif "flac" in encoder_name:
                        suffix = "_flac"
                    else:
                        suffix = "_audio"
                elif mode == "image":
                    if "webp" in encoder_name:
                        suffix = "_webp"
                    elif "avif" in encoder_name:
                        suffix = "_avif"
                    else:
                        suffix = "_img"
                else:
                    suffix = "_encoded"
                
                # Créer le nouveau nom avec le suffixe
                stem = p.stem
                dst_path = p.parent / f"{stem}{suffix}.{container}"
            
            job = EncodeJob(src_path=p, dst_path=dst_path, mode=mode)
            # Apply default encoder based on mode
            if mode == "video":
                job.encoder = Settings.data.get("default_video_encoder")
            elif mode == "audio":
                job.encoder = Settings.data.get("default_audio_encoder")
            else:
                job.encoder = Settings.data.get("default_image_encoder")
            self.jobs.append(job)
            self.tree.insert("", "end", iid=str(id(job)), values=(p.name, "-", "-", "0%", "pending"))
            # do not submit yet; submission happens when user presses Start Encoding
        
        self._update_inspector_file_list()
        # Mettre à jour l'état des boutons après avoir ajouté des jobs
        if not any(j.status in ["running", "paused"] for j in self.jobs):
            self._update_control_buttons_state("idle")

    def _detect_mode(self, path: Path) -> str:
        ext = path.suffix.lower()
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}
        if ext in video_exts:
            return "video"
        if ext in audio_exts:
            return "audio"
        if ext in image_exts:
            return "image"
        return "unknown"

    def _open_settings(self):
        SettingsWindow(self.root)

    def _on_double_click(self, event):
        item_id = self.tree.identify("item", event.x, event.y)
        if item_id:
            job = next((j for j in self.jobs if str(id(j)) == item_id), None)
            if job:
                JobEditWindow(self.root, job)

    def _select_input_folder(self):
        folder = filedialog.askdirectory(title="Select input folder")
        if folder:
            self.input_folder.set(folder)
            # Optionally, auto-enqueue files from this folder

    def _select_output_folder(self):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.output_folder.set(folder)

    def _find_and_add_files(self):
        folder = self.input_folder.get()
        if not folder or folder.startswith("(no input"):
            messagebox.showwarning("No Input Folder", "Please select an input folder first.")
            return
        root_path = Path(folder)
        if not root_path.exists() or not root_path.is_dir():
            messagebox.showerror("Invalid Folder", "The selected input folder does not exist or is not a directory.")
            return
        # Only add media files (video, audio, image)
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}
        all_files = [p for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() in (video_exts | audio_exts | image_exts)]
        if not all_files:
            messagebox.showinfo("No Media Files Found", "No media files found in the selected input folder.")
            return
        self._enqueue_paths(all_files)

    def _update_codec_choices(self):
        """Met à jour les choix de codecs basés sur le type de média sélectionné"""
        media_type = self.global_type_var.get()
        
        # Définir des codecs communs avec des noms conviviaux
        if media_type == "video":
            codec_choices = [
                ("H.264/AVC", "h264"),
                ("H.265/HEVC", "hevc"), 
                ("AV1", "av1"),
                ("VP9", "vp9"),
                ("VP8", "vp8"),
                ("MPEG-4", "mpeg4"),
                ("MPEG-2", "mpeg2video"),
                ("ProRes", "prores"),
                ("DNxHD", "dnxhd")
            ]
        elif media_type == "audio":
            codec_choices = [
                ("AAC", "aac"),
                ("MP3", "mp3"),
                ("Opus", "opus"),
                ("Vorbis", "vorbis"),
                ("FLAC", "flac"),
                ("AC3", "ac3"),
                ("PCM", "pcm_s16le"),
                ("WAV", "wav")
            ]
        else:  # image
            codec_choices = [
                ("WebP", "webp"),
                ("PNG", "png"),
                ("JPEG", "mjpeg"),
                ("BMP", "bmp"),
                ("TIFF", "tiff"),
                ("AVIF", "libaom-av1")
            ]
        
        # Obtenir les codecs disponibles depuis FFmpeg
        available_codecs = FFmpegHelpers.available_codecs()
        codec_list = available_codecs.get(media_type, [])
        
        # Filtrer par codecs disponibles ou par codecs communs que nous savons fonctionner
        filtered_choices = []
        for display, codec in codec_choices:
            # Vérifier si le codec exact est disponible OU si on a des encodeurs pour ce codec
            if (codec in codec_list or 
                codec.lower() in [c.lower() for c in codec_list] or
                self._has_encoders_for_codec(codec)):
                filtered_choices.append((display, codec))
        
        # Si aucun codec trouvé, utiliser une liste par défaut
        if not filtered_choices:
            if media_type == "video":
                filtered_choices = [("H.264/AVC", "h264"), ("MPEG-4", "mpeg4")]
            elif media_type == "audio":
                filtered_choices = [("AAC", "aac"), ("MP3", "mp3")]
            else:  # image
                filtered_choices = [("JPEG", "mjpeg"), ("PNG", "png")]
        
        # Mettre à jour le combobox avec les noms affichables
        display_values = [display for display, _ in filtered_choices]
        self.global_codec_combo['values'] = display_values
        self._current_codec_choices = filtered_choices  # Stocker les paires display/codec
        
        if display_values:
            self.global_codec_var.set(display_values[0])
        else:
            self.global_codec_var.set("")
        
        self._update_encoder_choices()
        self._update_container_choices()
        self._update_quality_preset_controls()
    
    def _has_encoders_for_codec(self, codec: str) -> bool:
        """Vérifie si nous avons des encodeurs disponibles pour un codec donné"""
        codec_encoder_map = {
            'h264': ['libx264', 'h264_nvenc', 'h264_qsv', 'h264_amf', 'h264_videotoolbox'],
            'hevc': ['libx265', 'hevc_nvenc', 'hevc_qsv', 'hevc_amf', 'hevc_videotoolbox'],
            'av1': ['libsvtav1', 'libaom-av1', 'av1_nvenc', 'av1_qsv'],
            'vp9': ['libvpx-vp9'],
            'vp8': ['libvpx'],
            'mpeg4': ['libxvid', 'mpeg4'],
            'mpeg2video': ['mpeg2video'],
            'prores': ['prores_ks'],
            'dnxhd': ['dnxhd'],
            'aac': ['aac', 'libfdk_aac'],
            'mp3': ['libmp3lame'],
            'opus': ['libopus'],
            'vorbis': ['libvorbis'],
            'flac': ['flac'],
            'ac3': ['ac3'],
            'pcm_s16le': ['pcm_s16le'],
            'wav': ['pcm_s16le'],
            'webp': ['libwebp'],
            'png': ['png'],
            'mjpeg': ['mjpeg'],
            'bmp': ['bmp'],
            'tiff': ['tiff']
        }
        
        expected_encoders = codec_encoder_map.get(codec.lower(), [])
        if not expected_encoders:
            return False
            
        # Vérifier si au moins un encodeur est disponible
        all_encoders = FFmpegHelpers.available_encoders()
        available_encoder_names = [name for name, _ in all_encoders]
        
        return any(encoder in available_encoder_names for encoder in expected_encoders)

    def _update_container_choices(self):
        """Met à jour les choix de containers basés sur le type de média"""
        media_type = self.global_type_var.get()
        
        if media_type == "video":
            container_choices = [
                ("MP4", "mp4"),
                ("MKV (Matroska)", "mkv"), 
                ("MOV (QuickTime)", "mov"),
                ("AVI", "avi"),
                ("MXF", "mxf"),
                ("WebM", "webm")
            ]
        elif media_type == "audio":
            container_choices = [
                ("M4A (AAC)", "m4a"),
                ("MP3", "mp3"),
                ("FLAC", "flac"),
                ("OGG", "ogg"),
                ("WAV", "wav"),
                ("AC3", "ac3")
            ]
        else:  # image
            container_choices = [
                ("WebP", "webp"),
                ("PNG", "png"),
                ("JPEG", "jpg"),
                ("BMP", "bmp"),
                ("TIFF", "tiff"),
                ("AVIF", "avif")
            ]
        
        # Mettre à jour le combobox
        display_values = [display for display, _ in container_choices]
        self.container_combo['values'] = display_values
        self._current_container_choices = container_choices
        
        # Sélectionner le premier par défaut
        if display_values:
            self.container_var.set(display_values[0])

    def _get_container_from_display(self, display_text: str) -> str:
        """Extrait la vraie extension de container à partir du texte affiché"""
        if hasattr(self, '_current_container_choices'):
            for display, container in self._current_container_choices:
                if display == display_text:
                    return container
        return display_text.lower()

    def _on_resolution_change(self, event=None):
        """Gère le changement de résolution dans le dropdown"""
        resolution = self.resolution_var_settings.get()
        if resolution == "Custom":
            # Afficher les champs de saisie personnalisés
            self.width_entry.pack(side="left", padx=(5, 2))
            self.x_label.pack(side="left")
            self.height_entry.pack(side="left", padx=(2, 5))
        else:
            # Cacher les champs personnalisés
            self.width_entry.pack_forget()
            self.x_label.pack_forget()
            self.height_entry.pack_forget()

    def _on_longest_side_change(self, event=None):
        """Gère le changement de la plus longue dimension pour les images"""
        longest_side = self.longest_side_var.get()
        if longest_side == "Custom":
            self.custom_longest_entry.pack(side="left", padx=(5, 0))
        else:
            self.custom_longest_entry.pack_forget()

    def _on_megapixels_change(self, event=None):
        """Gère le changement de mégapixels pour les images"""
        megapixels = self.megapixels_var.get()
        if megapixels == "Custom":
            self.custom_mp_entry.pack(side="left", padx=(5, 0))
        else:
            self.custom_mp_entry.pack_forget()

    def _on_video_mode_change(self):
        """Gère le changement de mode d'encodage vidéo (qualité vs bitrate)"""
        mode = self.video_mode_var.get()
        if mode == "quality":
            # Afficher les contrôles de qualité, cacher les contrôles de bitrate
            self.quality_entry.pack(side="left", padx=(0, 10))
            self.cq_entry.pack(side="left", padx=(0, 15))
            self.bitrate_label.pack_forget()
            self.bitrate_entry.pack_forget()
            self.multipass_check.pack_forget()
        else:  # bitrate
            # Cacher les contrôles de qualité, afficher les contrôles de bitrate
            self.quality_entry.pack_forget()
            self.cq_entry.pack_forget()
            self.bitrate_label.pack(side="left", padx=(0, 5))
            self.bitrate_entry.pack(side="left", padx=(0, 10))
            self.multipass_check.pack(side="left", padx=(0, 15))

    def _get_resolution_values(self):
        """Retourne les valeurs de résolution (width, height) selon la sélection"""
        resolution = self.resolution_var_settings.get()
        if resolution == "Original":
            return 0, 0
        elif resolution == "Custom":
            try:
                width = int(self.custom_width_var.get()) if self.custom_width_var.get() else 0
                height = int(self.custom_height_var.get()) if self.custom_height_var.get() else 0
                return width, height
            except ValueError:
                return 0, 0
        else:
            # Parser les résolutions prédéfinies
            resolution_map = {
                "3840x2160 (4K)": (3840, 2160),
                "1920x1080 (1080p)": (1920, 1080),
                "1280x720 (720p)": (1280, 720),
                "854x480 (480p)": (854, 480),
                "640x360 (360p)": (640, 360)
            }
            return resolution_map.get(resolution, (0, 0))

    def _update_encoder_choices(self):
        """Met à jour la liste des encodeurs basée sur le codec sélectionné"""
        codec_display = self.global_codec_var.get()
        if not codec_display:
            self.global_encoder_combo['values'] = []
            self.global_encoder_var.set("")
            return
        
        # Obtenir le vrai nom du codec à partir du display
        codec = self._get_codec_from_display(codec_display).lower()
        
        # Obtenir tous les encodeurs avec descriptions
        all_encoders = FFmpegHelpers.available_encoders()
        
        # Filtrer les encodeurs compatibles avec le codec
        compatible_encoders = []
        
        # Pour certains codecs professionnels, ne montrer que l'encodeur principal
        primary_encoders = {
            'prores': 'prores_ks',
            'dnxhd': 'dnxhd'
        }
        
        if codec.lower() in primary_encoders:
            # Ne montrer que l'encodeur principal pour ces codecs
            primary_encoder = primary_encoders[codec.lower()]
            for encoder_name, description in all_encoders:
                if encoder_name == primary_encoder:
                    display_text = f"{encoder_name} - {description}"
                    compatible_encoders.append((encoder_name, display_text))
                    break
        else:
            # Logique normale pour les autres codecs
            for encoder_name, description in all_encoders:
                if codec in encoder_name.lower() or self._encoder_supports_codec(encoder_name, codec):
                    # Marquer les encodeurs hardware
                    if FFmpegHelpers.is_hardware_encoder(encoder_name):
                        display_text = f"{encoder_name} - {description} (Hardware)"
                    else:
                        display_text = f"{encoder_name} - {description}"
                    compatible_encoders.append((encoder_name, display_text))
        
        # Séparer les encodeurs hardware et software
        hw_encoders = [(name, desc) for name, desc in compatible_encoders 
                      if FFmpegHelpers.is_hardware_encoder(name)]
        sw_encoders = [(name, desc) for name, desc in compatible_encoders 
                      if not FFmpegHelpers.is_hardware_encoder(name)]
        
        # Organiser la liste avec hardware en premier, puis software
        display_values = []
        encoder_mapping = {}  # Pour mapper display vers encoder name
        
        if hw_encoders:
            for name, desc in hw_encoders:
                display_values.append(desc)
                encoder_mapping[desc] = name
                
        if sw_encoders:
            for name, desc in sw_encoders:
                display_values.append(desc)
                encoder_mapping[desc] = name
        
        # Mettre à jour le combobox
        self.global_encoder_combo['values'] = display_values
        self._current_encoder_mapping = encoder_mapping
        
        # Sélectionner le premier encodeur par défaut
        if display_values:
            self.global_encoder_var.set(display_values[0])
        else:
            self.global_encoder_var.set("")
            
        # Mettre à jour les contrôles qualité/preset
        self._update_quality_preset_controls()

    def _encoder_supports_codec(self, encoder_name: str, codec: str) -> bool:
        """Détermine si un encodeur supporte un codec donné"""
        codec_encoder_map = {
            'h264': ['libx264', 'h264_nvenc', 'h264_qsv', 'h264_amf', 'h264_videotoolbox'],
            'hevc': ['libx265', 'hevc_nvenc', 'hevc_qsv', 'hevc_amf', 'hevc_videotoolbox'],
            'av1': ['libsvtav1', 'libaom-av1', 'av1_nvenc', 'av1_qsv'],
            'vp9': ['libvpx-vp9'],
            'vp8': ['libvpx'],
            'aac': ['aac', 'libfdk_aac'],
            'mp3': ['libmp3lame'],
            'opus': ['libopus'],
            'vorbis': ['libvorbis'],
            'webp': ['libwebp']
        }
        return encoder_name in codec_encoder_map.get(codec, [encoder_name])

    def _update_quality_preset_controls(self):
        """Met à jour les contrôles qualité/preset basés sur le codec/encodeur sélectionné"""
        codec_display = self.global_codec_var.get()
        encoder_display = self.global_encoder_var.get()
        media_type = self.global_type_var.get()
        
        # Extraire les vrais noms depuis les displays
        codec = self._get_codec_from_display(codec_display).lower() if codec_display else ""
        encoder = self._get_encoder_name_from_display(encoder_display).lower() if encoder_display else ""
        
        # Gérer l'affichage des contrôles spécifiques au type de média
        self._update_media_type_ui(media_type)
        
        # Réinitialiser les états
        self.quality_entry.config(state="normal")
        self.cq_entry.config(state="normal")
        self.preset_combo_quality.config(state="readonly")

    def _update_media_type_ui(self, media_type):
        """Met à jour l'interface selon le type de média sélectionné"""
        if media_type == "video":
            # Afficher les contrôles vidéo
            self.mode_row.pack(fill="x", pady=(0, 8), before=self.mode_row.master.children[list(self.mode_row.master.children.keys())[0]])
            self.resolution_combo.pack(side="left", padx=(0, 10))
            self.image_res_frame.pack_forget()
            # Appliquer le mode vidéo actuel
            self._on_video_mode_change()
            
        elif media_type == "image":
            # Masquer les contrôles vidéo, afficher les contrôles image
            self.mode_row.pack_forget()
            self.resolution_combo.pack_forget()
            self.image_res_frame.pack(side="left", padx=(10, 0))
            # Forcer le mode qualité pour les images
            self.quality_entry.pack(side="left", padx=(0, 10))
            self.cq_entry.pack_forget()
            self.bitrate_label.pack_forget()
            self.bitrate_entry.pack_forget()
            self.multipass_check.pack_forget()
            
        elif media_type == "audio":
            # Masquer tous les contrôles de résolution et mode vidéo
            self.mode_row.pack_forget()
            self.resolution_combo.pack_forget()
            self.image_res_frame.pack_forget()
            # Configuration audio basique
            self.quality_entry.pack(side="left", padx=(0, 10))
            self.cq_entry.pack_forget()
            self.bitrate_label.pack_forget()
            self.bitrate_entry.pack_forget()
            self.multipass_check.pack_forget()
        
        # Obtenir l'encodeur actuel à partir de la variable globale avec une valeur par défaut
        encoder_display = self.global_encoder_var.get()
        encoder = ""
        if encoder_display:
            try:
                encoder = self._get_encoder_name_from_display(encoder_display).lower()
            except Exception:
                encoder = ""
        
        if media_type == "video":
            # Déterminer le type de qualité basé sur l'encodeur
            if any(hw in encoder for hw in ["nvenc", "qsv", "amf", "videotoolbox"]):
                # Encodeurs hardware - utiliser CQ/qualité appropriée
                if "nvenc" in encoder:
                    self.quality_entry.config(state="disabled")
                    self.quality_var.set("")
                    self.cq_entry.config(state="normal")
                    self.cq_var.set(self.cq_var.get() or "23")
                    self.preset_combo_quality.config(state="readonly")
                    self.preset_combo_quality['values'] = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
                    self.preset_var.set(self.preset_var.get() or "p4")
                elif "qsv" in encoder:
                    self.quality_entry.config(state="disabled")
                    self.quality_var.set("")
                    self.cq_entry.config(state="normal")
                    self.cq_var.set(self.cq_var.get() or "23")
                    self.preset_combo_quality.config(state="readonly")
                    self.preset_combo_quality['values'] = ["veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
                    self.preset_var.set(self.preset_var.get() or "medium")
                elif "amf" in encoder:
                    self.quality_entry.config(state="disabled")
                    self.quality_var.set("")
                    self.cq_entry.config(state="normal")
                    self.cq_var.set(self.cq_var.get() or "23")
                    self.preset_combo_quality.config(state="readonly")
                    self.preset_combo_quality['values'] = ["speed", "balanced", "quality"]
                    self.preset_var.set(self.preset_var.get() or "balanced")
                elif "videotoolbox" in encoder:
                    self.quality_entry.config(state="disabled")
                    self.quality_var.set("")
                    self.cq_entry.config(state="normal")
                    self.cq_var.set(self.cq_var.get() or "23")
                    self.preset_combo_quality.config(state="disabled")
                    self.preset_var.set("")
            elif any(sw in encoder for sw in ["x264", "x265", "libx264", "libx265"]):
                # Encodeurs software x264/x265 - CRF + presets
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "23")
                self.cq_entry.config(state="disabled")
                self.cq_var.set("")
                self.preset_combo_quality.config(state="readonly")
                self.preset_combo_quality['values'] = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"]
                self.preset_var.set(self.preset_var.get() or "medium")
            elif any(av1 in encoder for av1 in ["av1", "svt-av1", "aom"]):
                # Encodeurs AV1
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "28")
                self.cq_entry.config(state="disabled")
                self.cq_var.set("")
                self.preset_combo_quality.config(state="readonly")
                self.preset_combo_quality['values'] = ["0", "1", "2", "3", "4", "5", "6", "7", "8"]
                self.preset_var.set(self.preset_var.get() or "4")
            elif "vp9" in encoder:
                # VP9
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "28")
                self.cq_entry.config(state="disabled")
                self.cq_var.set("")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
            else:
                # Autres encodeurs vidéo
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "23")
                self.cq_entry.config(state="disabled")
                self.cq_var.set("")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
                
        elif media_type == "audio":
            # Encodeurs audio - CQ non applicable
            self.cq_entry.config(state="disabled")
            self.cq_var.set("")
            if "flac" in encoder:
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "5")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
            elif any(lossy in encoder for lossy in ["aac", "mp3", "opus", "vorbis"]):
                # Pour les codecs avec perte, utiliser un sélecteur de bitrate
                self.quality_entry.config(state="disabled")
                self.quality_var.set("")
                self.preset_combo_quality.config(state="readonly")
                # Configurer les bitrates communs selon le codec
                if "aac" in encoder:
                    bitrates = ["96k", "128k", "192k", "256k", "320k"]
                elif "mp3" in encoder:
                    bitrates = ["96k", "128k", "160k", "192k", "256k", "320k"]
                elif "opus" in encoder:
                    bitrates = ["64k", "96k", "128k", "160k", "192k", "256k"]
                elif "vorbis" in encoder:
                    bitrates = ["96k", "128k", "160k", "192k", "256k", "320k"]
                else:
                    bitrates = ["128k", "192k", "256k", "320k"]
                
                self.preset_combo_quality['values'] = bitrates
                self.preset_var.set(self.preset_var.get() or "128k")
            else:
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "128")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
                
        elif media_type == "image":
            # Encodeurs image - CQ non applicable
            self.cq_entry.config(state="disabled")
            self.cq_var.set("")
            if any(img in encoder for img in ["jpeg", "webp", "avif", "jpegxl", "jxl"]):
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "90")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
            elif "png" in encoder:
                self.quality_entry.config(state="disabled")
                self.quality_var.set("")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
            else:
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "90")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
        else:
            # Mode non défini
            self.quality_entry.config(state="disabled")
            self.quality_var.set("")
            self.cq_entry.config(state="disabled")
            self.cq_var.set("")
            self.preset_combo_quality.config(state="disabled")
            self.preset_var.set("")

    def _apply_quality_all_type(self):
        media_type = self.global_type_var.get()
        quality = self.quality_var.get()
        cq_value = self.cq_var.get()
        preset = self.preset_var.get()
        custom_flags = self.custom_flags_var.get()
        width, height = self._get_resolution_values()
        
        for job in self.jobs:
            if job.mode == media_type:
                job.quality = quality
                job.cq_value = cq_value
                job.custom_flags = custom_flags
                job.preset = preset
                
                # Paramètres spécifiques au type de média
                if media_type == "video":
                    if hasattr(self, 'video_mode_var'):
                        job.video_mode = self.video_mode_var.get()
                        job.bitrate = self.bitrate_var.get() if self.video_mode_var.get() == "bitrate" else ""
                        job.multipass = self.multipass_var.get() if self.video_mode_var.get() == "bitrate" else False
                    # Appliquer la résolution aux filtres
                    job.filters["scale_width"] = width
                    job.filters["scale_height"] = height
                elif media_type == "image":
                    # Appliquer les paramètres spécifiques aux images
                    if hasattr(self, 'longest_side_var'):
                        longest_side = self.longest_side_var.get()
                        if longest_side == "Custom":
                            job.longest_side = self.custom_longest_var.get()
                        else:
                            job.longest_side = longest_side
                    
                    if hasattr(self, 'megapixels_var'):
                        megapixels = self.megapixels_var.get()
                        if megapixels == "Custom":
                            job.megapixels = self.custom_mp_var.get()
                        else:
                            job.megapixels = megapixels
        # Update the queue display
        for iid in self.tree.get_children():
            job = next((j for j in self.jobs if str(id(j)) == iid), None)
            if job and job.mode == media_type:
                values = list(self.tree.item(iid, 'values'))
                values[2] = job.quality
                self.tree.item(iid, values=values)

    def _apply_quality_selected(self):
        quality = self.quality_var.get()
        cq_value = self.cq_var.get()
        preset = self.preset_var.get()
        custom_flags = self.custom_flags_var.get()
        width, height = self._get_resolution_values()
        media_type = self.global_type_var.get()
        
        selected = self.tree.selection()
        for iid in selected:
            job = next((j for j in self.jobs if str(id(j)) == iid), None)
            if job:
                job.quality = quality
                job.cq_value = cq_value
                job.custom_flags = custom_flags
                job.preset = preset
                
                # Paramètres spécifiques au type de média
                if job.mode == "video":
                    if hasattr(self, 'video_mode_var'):
                        job.video_mode = self.video_mode_var.get()
                        job.bitrate = self.bitrate_var.get() if self.video_mode_var.get() == "bitrate" else ""
                        job.multipass = self.multipass_var.get() if self.video_mode_var.get() == "bitrate" else False
                    # Appliquer la résolution aux filtres
                    job.filters["scale_width"] = width
                    job.filters["scale_height"] = height
                elif job.mode == "image":
                    # Appliquer les paramètres spécifiques aux images
                    if hasattr(self, 'longest_side_var'):
                        longest_side = self.longest_side_var.get()
                        if longest_side == "Custom":
                            job.longest_side = self.custom_longest_var.get()
                        else:
                            job.longest_side = longest_side
                    
                    if hasattr(self, 'megapixels_var'):
                        megapixels = self.megapixels_var.get()
                        if megapixels == "Custom":
                            job.megapixels = self.custom_mp_var.get()
                        else:
                            job.megapixels = megapixels
                
                values = list(self.tree.item(iid, 'values'))
                values[2] = job.quality or job.bitrate or job.preset
                self.tree.item(iid, values=values)

    def _duplicate_selected(self):
        selected = self.tree.selection()
        for iid in selected:
            job = next((j for j in self.jobs if str(id(j)) == iid), None)
            if job:
                new_job = EncodeJob(src_path=job.src_path, dst_path=job.dst_path, mode=job.mode)
                new_job.encoder = job.encoder
                new_job.quality = job.quality
                new_job.cq_value = job.cq_value
                new_job.preset = job.preset
                new_job.custom_flags = job.custom_flags
                self.jobs.append(new_job)
                self.tree.insert("", "end", iid=str(id(new_job)), values=(new_job.src_path.name, new_job.encoder or "-", new_job.quality or "-", "0%", "pending"))

    def _set_codec_for_all(self):
        """Applique tous les paramètres d'encodage globaux à tous les jobs du type sélectionné"""
        target_type = self.global_type_var.get()
        encoder_display = self.global_encoder_var.get()
        encoder_name = self._get_encoder_name_from_display(encoder_display)
        quality = self.quality_var.get()
        cq_value = self.cq_var.get()
        preset = self.preset_var.get()
        container = self._get_container_from_display(self.container_var.get())
        custom_flags = self.custom_flags_var.get()
        width, height = self._get_resolution_values()
        
        count = 0
        for job in self.jobs:
            if job.mode == target_type:
                job.encoder = encoder_name
                job.quality = quality
                job.cq_value = cq_value
                job.preset = preset
                job.custom_flags = custom_flags
                # Appliquer la résolution aux filtres
                job.filters["scale_width"] = width
                job.filters["scale_height"] = height
                # Mettre à jour le chemin de destination avec le nouveau container
                if container:
                    job.dst_path = job.dst_path.with_suffix("." + container)
                count += 1
        
        # Mettre à jour l'affichage
        for item_id in self.tree.get_children():
            job = next((j for j in self.jobs if str(id(j)) == item_id), None)
            if job and job.mode == target_type:
                self._update_job_row(job)
        
        messagebox.showinfo("Applied", f"All encoding settings applied to {count} {target_type} job(s).")

    def _apply_settings_smart(self):
        """Applique les paramètres intelligemment selon la sélection"""
        selected = self.tree.selection()
        
        if selected:
            # Il y a des éléments sélectionnés - appliquer uniquement à ceux-ci
            self._apply_quality_selected()
            messagebox.showinfo("Applied", f"Settings applied to {len(selected)} selected job(s).")
        else:
            # Aucune sélection - appliquer à tous les jobs du type actuel
            self._apply_quality_all_type()

    def _apply_codec_smart(self):
        """Applique le codec intelligemment selon la sélection"""
        selected = self.tree.selection()
        
        # Vérifier qu'un encodeur est sélectionné
        encoder_display = self.global_encoder_var.get()
        if not encoder_display:
            messagebox.showwarning("No Encoder", "Please select an encoder first.")
            return
        
        encoder_name = self._get_encoder_name_from_display(encoder_display)
        if not encoder_name:
            messagebox.showwarning("Invalid Encoder", "Could not determine encoder name.")
            return
        
        if selected:
            # Il y a des éléments sélectionnés - appliquer codec/encodeur à ceux-ci
            target_type = self.global_type_var.get()
            container = self._get_container_from_display(self.container_var.get())
            
            count = 0
            for item_id in selected:
                job = next((j for j in self.jobs if str(id(j)) == item_id), None)
                if job and job.mode == target_type:
                    job.encoder = encoder_name
                    # Mettre à jour le chemin de destination avec le nouveau container
                    if container:
                        job.dst_path = job.dst_path.with_suffix("." + container)
                    count += 1
            
            # Mettre à jour l'affichage
            for item_id in selected:
                job = next((j for j in self.jobs if str(id(j)) == item_id), None)
                if job and job.mode == target_type:
                    self._update_job_row(job)
            
            messagebox.showinfo("Applied", f"Encoder '{encoder_name}' applied to {count} selected job(s).")
        else:
            # Aucune sélection - appliquer à tous les jobs du type actuel
            self._set_codec_for_all()

    def _get_encoder_name_from_display(self, display_text: str) -> str:
        """Extrait le nom de l'encodeur à partir du texte affiché"""
        if not display_text:
            return ""
        
        # Utiliser la nouvelle mapping si disponible
        if hasattr(self, '_current_encoder_mapping') and display_text in self._current_encoder_mapping:
            return self._current_encoder_mapping[display_text]
        
        # Fallback vers l'ancienne méthode
        if " - " in display_text:
            return display_text.split(" - ")[0]
        
        return display_text

    def _get_codec_from_display(self, display_text: str) -> str:
        """Extrait le vrai nom du codec à partir du texte affiché"""
        if hasattr(self, '_current_codec_choices'):
            for display, codec in self._current_codec_choices:
                if display == display_text:
                    return codec
        return display_text.lower()

    def _start_encoding(self):
        """Commence l'encodage de tous les jobs en attente"""
        pending_jobs = [job for job in self.jobs if job.status == "pending"]
        if not pending_jobs:
            messagebox.showinfo("No Jobs", "No pending jobs to encode.")
            return

        # Mettre à jour l'état des boutons de contrôle
        self._update_control_buttons_state("encoding")
        
        # Démarrer les pools de workers
        self.pool.start()
        
        # Soumettre les jobs aux pools appropriés
        for job in pending_jobs:
            self.pool.submit(job)

    def _update_control_buttons_state(self, mode: str):
        """Met à jour l'état des boutons de contrôle selon le mode"""
        if mode == "idle":
            # Aucun encodage en cours - vérifier s'il y a des jobs pending
            pending_jobs = [job for job in self.jobs if job.status == "pending"]
            
            # Le bouton Start est activé s'il y a des jobs en attente
            self.start_btn.config(state="normal" if pending_jobs else "disabled")
            self.pause_btn.config(state="disabled")
            self.resume_btn.config(state="disabled")
            self.cancel_btn.config(state="disabled")
        elif mode == "encoding":
            # Encodage en cours
            self.start_btn.config(state="disabled")
            self.pause_btn.config(state="normal")
            self.resume_btn.config(state="disabled")
            self.cancel_btn.config(state="normal")
        elif mode == "paused":
            # Encodage en pause
            self.start_btn.config(state="disabled")
            self.pause_btn.config(state="disabled")
            self.resume_btn.config(state="normal")
            self.cancel_btn.config(state="normal")

    def _on_job_progress(self, job: EncodeJob):
        """Met à jour l'affichage quand un job progresse"""
        self._update_job_row(job)
        self._update_overall_progress()
        
        # Vérifier si tous les jobs sont terminés
        active_jobs = [j for j in self.jobs if j.status in ["pending", "running", "paused"]]
        if not active_jobs:
            # Tous les jobs sont terminés, revenir à l'état idle
            self._update_control_buttons_state("idle")
            self._show_encoding_completion_notification()

    def _show_encoding_completion_notification(self):
        """Affiche une notification quand tous les encodages sont terminés"""
        completed_jobs = [j for j in self.jobs if j.status == "done"]
        failed_jobs = [j for j in self.jobs if j.status == "error"]
        cancelled_jobs = [j for j in self.jobs if j.status == "cancelled"]
        
        if completed_jobs or failed_jobs or cancelled_jobs:
            # Créer un message de résumé
            message_parts = []
            if completed_jobs:
                message_parts.append(f"✅ {len(completed_jobs)} job(s) terminé(s) avec succès")
            if failed_jobs:
                message_parts.append(f"❌ {len(failed_jobs)} job(s) échoué(s)")
            if cancelled_jobs:
                message_parts.append(f"🚫 {len(cancelled_jobs)} job(s) annulé(s)")
            
            message = "🎬 Encodage terminé!\n\n" + "\n".join(message_parts)
            
            # Afficher la notification
            messagebox.showinfo("Encodage terminé", message)
            
            # Optionnel: jouer un son système si disponible
            try:
                import winsound
                winsound.SystemSound("SystemAsterisk")
            except ImportError:
                try:
                    import os
                    os.system("afplay /System/Library/Sounds/Glass.aiff")  # macOS
                except:
                    pass  # Pas de son disponible

    def _update_job_row(self, job):
        iid = str(id(job))
        if self.tree.exists(iid):
            values = list(self.tree.item(iid, 'values'))
            # Mettre à jour l'encodeur si défini
            if job.encoder:
                values[1] = job.encoder
            # Mettre à jour la qualité si définie
            if job.quality:
                values[2] = job.quality
            # Mettre à jour la progression
            values[3] = f"{int(job.progress*100)}%"
            # Mettre à jour le statut
            status = job.status
            if len(values) < 5:
                values.append(status)
            else:
                values[4] = status
            self.tree.item(iid, values=values)
        self._update_overall_progress()

    def _update_overall_progress(self):
        if not self.jobs:
            self.progress_bar['value'] = 0
            return
        avg = sum(j.progress for j in self.jobs) / len(self.jobs)
        self.progress_bar['value'] = avg * 100

    def _pause_all(self):
        """Met en pause tous les jobs en cours d'exécution"""
        paused_count = 0
        for job in self.jobs:
            if job.status == "running":
                job.pause()
                paused_count += 1
        
        if paused_count > 0:
            self._update_control_buttons_state("paused")

    def _resume_all(self):
        """Reprend tous les jobs en pause"""
        resumed_count = 0
        for job in self.jobs:
            if job.status == "paused":
                job.resume()
                resumed_count += 1
        
        if resumed_count > 0:
            self._update_control_buttons_state("encoding")

    def _cancel_all(self):
        """Annule tous les jobs en cours"""
        cancelled_count = 0
        for job in self.jobs:
            if job.status in ["running", "paused", "pending"]:
                job.cancel()
                cancelled_count += 1
        
        if cancelled_count > 0:
            # Arrêter les pools de workers
            self.pool.stop()
            self._update_control_buttons_state("idle")

    def _clear_queue(self):
        """Vide complètement la queue d'encodage"""
        # Annuler tous les jobs en cours
        for job in self.jobs:
            if job.status in ["running", "paused", "pending"]:
                job.cancel()
        
        # Arrêter les pools de workers
        self.pool.stop()
        
        # Vider la liste et l'interface
        self.jobs.clear()
        self.tree.delete(*self.tree.get_children())
        self.progress_bar['value'] = 0
        self._update_inspector_file_list()
        
        # Remettre les boutons à l'état idle
        self._update_control_buttons_state("idle")

    def _on_right_click(self, event):
        item_id = self.tree.identify("item", event.x, event.y)
        if item_id:
            self.tree.selection_set(item_id)
            self.context_menu.post(event.x_root, event.y_root)

    def _edit_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                JobEditWindow(self.root, job)

    def _pause_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                job.pause()

    def _resume_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                job.resume()

    def _cancel_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                job.cancel()

    def _remove_selected_job(self):
        """Supprime le job sélectionné de la queue"""
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                # Annuler le job s'il est en cours
                if job.status in ["running", "paused"]:
                    job.cancel()
                    
                self.jobs.remove(job)
                self.tree.delete(selected[0])
                self._update_inspector_file_list()
                
                # Mettre à jour l'état des boutons si aucun job n'est actif
                if not any(j.status in ["running", "paused"] for j in self.jobs):
                    self._update_control_buttons_state("idle")

    def _setup_drag_drop(self):
        """Configure les zones de drop pour le drag & drop"""
        if not DND_AVAILABLE:
            return
            
        # Drop sur le champ input folder
        self.input_folder_entry.drop_target_register(DND_FILES)
        self.input_folder_entry.dnd_bind('<<Drop>>', self._on_drop_input_folder)
        
        # Drop sur la queue (treeview)
        self.tree.drop_target_register(DND_FILES)
        self.tree.dnd_bind('<<Drop>>', self._on_drop_queue)

    def _on_drop_input_folder(self, event):
        """Gère le drop de fichiers/dossiers sur le champ input folder"""
        files = self.root.tk.splitlist(event.data)
        if files:
            first_path = Path(files[0])
            if first_path.is_dir():
                self.input_folder.set(str(first_path))
            elif first_path.is_file():
                # Si c'est un fichier, utiliser son dossier parent
                self.input_folder.set(str(first_path.parent))

    def _on_drop_queue(self, event):
        """Gère le drop de fichiers/dossiers directement dans la queue"""
        files = self.root.tk.splitlist(event.data)
        paths = []
        for file_path in files:
            path = Path(file_path)
            if path.is_file():
                paths.append(path)
            elif path.is_dir():
                # Ajouter tous les fichiers du dossier récursivement
                paths.extend([p for p in path.rglob("*") if p.is_file()])
        
        if paths:
            self._enqueue_paths(paths)

    def _update_preset_list(self):
        """Met à jour la liste des presets disponibles"""
        preset_names = list(Settings.data["presets"].keys())
        self.preset_combo['values'] = preset_names
        if not self.preset_name_var.get() and preset_names:
            self.preset_name_var.set(preset_names[0])

    def _save_preset(self):
        """Sauvegarde le preset actuel ou crée un nouveau preset"""
        current_preset = self.preset_name_var.get()
        
        # Demander le nom du preset
        if not current_preset or current_preset in ["H264 High Quality", "H264 Fast", "WebP Images"]:
            # Créer un nouveau preset
            preset_name = self._ask_preset_name()
        else:
            # Demander si on veut écraser le preset existant
            result = messagebox.askyesno(
                "Save Preset", 
                f"Update existing preset '{current_preset}'?",
                icon='question'
            )
            if result:  # Yes = update existing
                preset_name = current_preset
            else:  # No = create new
                preset_name = self._ask_preset_name()
        
        if not preset_name:
            return
            
        # Créer le preset
        preset_data = {
            "mode": self.global_type_var.get(),
            "codec": self.global_codec_var.get(),
            "encoder": self._get_encoder_name_from_display(self.global_encoder_var.get()),
            "quality": self.quality_var.get(),
            "cq_value": self.cq_var.get(),
            "preset": self.preset_var.get(),
            "container": self.container_var.get(),
            "custom_flags": self.custom_flags_var.get()
        }
        
        Settings.data["presets"][preset_name] = preset_data
        Settings.save()
        self._update_preset_list()
        self.preset_name_var.set(preset_name)
        messagebox.showinfo("Success", f"Preset '{preset_name}' saved successfully!")

    def _ask_preset_name(self) -> str:
        """Demande le nom d'un nouveau preset"""
        from tkinter.simpledialog import askstring
        name = askstring("New Preset", "Enter preset name:")
        if name and name.strip():
            return name.strip()
        return ""

    def _load_preset(self, event=None):
        """Charge un preset sélectionné"""
        selected = self.preset_name_var.get()
        if selected and selected in Settings.data["presets"]:
            preset = Settings.data["presets"][selected]
            
            # Charger les valeurs du preset
            self.global_type_var.set(preset["mode"])
            self.global_codec_var.set(preset["codec"])
            self.quality_var.set(preset.get("quality", ""))
            self.cq_var.set(preset.get("cq_value", ""))
            self.preset_var.set(preset.get("preset", ""))
            self.container_var.set(preset.get("container", "mp4"))
            self.custom_flags_var.set(preset.get("custom_flags", ""))
            
            # Mettre à jour les listes de codecs/encodeurs
            self._update_codec_choices()
            self._update_encoder_choices()
            
            # Définir l'encodeur (avec gestion du format display)
            encoder = preset.get("encoder", "")
            if encoder:
                # Chercher l'encodeur dans la liste des encodeurs disponibles
                for encoder_name, description in FFmpegHelpers.available_encoders():
                    if encoder_name == encoder:
                        display_text = f"{encoder_name} - {description}"
                        self.global_encoder_var.set(display_text)
                        break
                else:
                    # Fallback si l'encodeur n'est pas trouvé
                    self.global_encoder_var.set(encoder)

    def _delete_preset(self):
        """Supprime le preset sélectionné"""
        selected = self.preset_name_var.get()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a preset to delete.")
            return
            
        if selected in ["H264 High Quality", "H264 Fast", "WebP Images"]:
            messagebox.showwarning("Cannot Delete", "Cannot delete default presets.")
            return
            
        result = messagebox.askyesno("Confirm Delete", f"Delete preset '{selected}'?")
        if result:
            del Settings.data["presets"][selected]
            Settings.save()
            self._update_preset_list()
            self.preset_name_var.set("")
            messagebox.showinfo("Deleted", f"Preset '{selected}' deleted successfully!")

    def _load_preset_by_name(self, preset_name: str):
        """Charge un preset par son nom (utilisé par le menu)"""
        self.preset_name_var.set(preset_name)
        self._load_preset()

    def _show_log_viewer(self):
        self.log_viewer = LogViewerWindow(self.root)

    def _on_job_log(self, job: EncodeJob, message: str, log_type: str = "info"):
        """Callback pour recevoir les logs des jobs et les transmettre au log viewer"""
        if self.log_viewer:
            # Utiliser after_idle pour s'assurer que les mises à jour GUI se font sur le thread principal
            self.root.after_idle(lambda: self.log_viewer.add_log(job, message, log_type))

    def _batch_operations(self):
        """Ouvre la fenêtre de batch operations pour les jobs sélectionnés"""
        selected_item_ids = self.tree.selection()
        if not selected_item_ids:
            messagebox.showwarning("No Selection", "Please select one or more jobs for batch operations.")
            return
            
        # Récupérer les jobs correspondants aux IDs sélectionnés
        selected_jobs = []
        for item_id in selected_item_ids:
            job = next((j for j in self.jobs if str(id(j)) == item_id), None)
            if job:
                selected_jobs.append(job)
        
        if selected_jobs:
            BatchOperationsWindow(self.root, selected_jobs)
        else:
            messagebox.showwarning("No Jobs Found", "Could not find jobs for selected items.")

    def _advanced_filters(self):
        """Ouvre la fenêtre de filtres avancés pour le job sélectionné"""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a job to configure filters.")
            return
            
        job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
        if job:
            AdvancedFiltersWindow(self.root, job)
        else:
            messagebox.showwarning("Job Not Found", "Could not find the selected job.")

    def _configure_audio_tracks(self):
        """Ouvre la fenêtre de configuration des pistes audio"""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a job to configure audio tracks.")
            return
            
        job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
        if job:
            if job.mode == "image":
                messagebox.showinfo("Not Applicable", "Audio track configuration is not applicable to image files.")
                return
            AudioTracksWindow(self.root, job)
        else:
            messagebox.showwarning("Job Not Found", "Could not find the selected job.")

    def _toggle_watch(self):
        if self.watch_var.get():
            if Observer is None:
                messagebox.showerror("Dépendance manquante", "Le module 'watchdog' n'est pas installé. Veuillez l'installer avec 'pip install watchdog'.")
                self.watch_var.set(False)
                return
            if not self.input_folder.get():
                messagebox.showerror("Erreur", "Veuillez d'abord sélectionner un dossier d'entrée")
                self.watch_var.set(False)
                return
            
            self.stop_event = threading.Event()
            self.watcher = FolderWatcher(
                Path(self.input_folder.get()),
                self._handle_new_watched_file,
                self.stop_event
            )
            self.watcher.start()
            self.watch_status.config(text="Statut: Surveillance active...")
        else:
            if hasattr(self, 'watcher'):
                self.stop_event.set()
                self.watcher.join()
                self.watch_status.config(text="Statut: Inactif")

    def _handle_new_watched_file(self, file_path):
        self.root.after_idle(lambda: self._enqueue_watched_file(file_path))

    def _enqueue_watched_file(self, file_path):
        preset_name = self.watch_preset_combo.get()
        if preset_name in Settings.data.get("presets", {}):
            self._enqueue_paths([file_path])
            new_job = self.jobs[-1] if self.jobs else None
            if new_job:
                preset = Settings.data["presets"][preset_name]
                new_job.encoder = preset.get("encoder", "")
                new_job.quality = preset.get("quality", "")
                new_job.cq_value = preset.get("cq_value", "")
                new_job.preset = preset.get("preset", "")
                new_job.custom_flags = preset.get("custom_flags", "")
                self._update_job_row(new_job)
                print(f"Fichier surveillé ajouté: {file_path}")
                # Démarrer automatiquement l'encodage si configuré
                if Settings.data.get("auto_start_watched_files", False):
                    self._start_encoding()

    def _on_queue_selection_change(self, event=None):
        selection = self.tree.selection()
        if not selection:
            self._clear_inspector()
            return
        
        job_id = selection[0]
        
        # Select the same item in the inspector tree
        if self.inspector_tree.exists(job_id):
            self.inspector_tree.selection_set(job_id)
            self.inspector_tree.focus(job_id)
    
    def _on_inspector_selection_change(self, event=None):
        selection = self.inspector_tree.selection()
        if not selection:
            self._clear_inspector()
            return

        job_id = selection[0]
        # The job id is stored in the item's iid
        job = next((j for j in self.jobs if str(id(j)) == job_id), None)

        if job:
            self._clear_inspector() # Show loading message
            ttk.Label(self.inspector_info_frame, text="Inspection en cours...").pack(padx=10, pady=10)
            threading.Thread(
                target=self._run_probe_and_update_inspector, 
                args=(job,),
                daemon=True
            ).start()

    def _run_probe_and_update_inspector(self, job: EncodeJob):
        try:
            cmd = [
                "ffprobe", "-v", "quiet", 
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(job.src_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            
            # Extraire les informations pertinentes
            info = self._parse_ffprobe_data(data, job.mode)
            self.root.after_idle(lambda: self._update_inspector_ui(info))
        except Exception as e:
            print(f"Erreur d'inspection: {e}")
            self.root.after_idle(self._clear_inspector)

    def _parse_ffprobe_data(self, data, mode):
        def format_duration(seconds):
            if not seconds or seconds == 'N/A':
                return "N/A"
            try:
                seconds = float(seconds)
                hours, remainder = divmod(int(seconds), 3600)
                minutes, seconds = divmod(remainder, 60)
                return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            except:
                return "N/A"

        def format_bitrate(bps):
            if not bps or bps == 'N/A':
                return "N/A"
            try:
                bps = int(bps)
                if bps >= 1000000:
                    return f"{bps/1000000:.2f} Mbps"
                elif bps >= 1000:
                    return f"{bps/1000:.2f} kbps"
                else:
                    return f"{bps} bps"
            except:
                return "N/A"

        def format_file_size(bytes_size):
            if not bytes_size or bytes_size == 'N/A':
                return "N/A"
            try:
                bytes_size = int(bytes_size)
                if bytes_size >= 1024**3:
                    return f"{bytes_size/(1024**3):.2f} GB"
                elif bytes_size >= 1024**2:
                    return f"{bytes_size/(1024**2):.2f} MB"
                elif bytes_size >= 1024:
                    return f"{bytes_size/1024:.2f} KB"
                else:
                    return f"{bytes_size} bytes"
            except:
                return "N/A"

        format_info = data.get('format', {})
        streams = data.get('streams', [])
        info = {}

        if mode == 'video':
            video_stream = next((s for s in streams if s.get('codec_type') == 'video'), {})
            audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
            
            # Informations vidéo principales
            width = video_stream.get('width', 'N/A')
            height = video_stream.get('height', 'N/A')
            fps = video_stream.get('r_frame_rate', 'N/A')
            if fps != 'N/A' and '/' in str(fps):
                try:
                    num, den = map(int, fps.split('/'))
                    fps = f"{num/den:.2f}" if den != 0 else 'N/A'
                except:
                    pass
            
            # Calculer le ratio d'aspect
            aspect_ratio = "N/A"
            if width != 'N/A' and height != 'N/A':
                try:
                    from math import gcd
                    w, h = int(width), int(height)
                    divisor = gcd(w, h)
                    aspect_ratio = f"{w//divisor}:{h//divisor}"
                except:
                    pass
            
            info = {
                "📹 Résolution": f"{width}x{height}",
                "📐 Ratio d'aspect": aspect_ratio,
                "⏱️ Durée": format_duration(format_info.get('duration', 'N/A')),
                "🎞️ Images/sec": f"{fps} fps" if fps != 'N/A' else 'N/A',
                "🎬 Codec Vidéo": video_stream.get('codec_long_name', video_stream.get('codec_name', 'N/A')),
                "📊 Débit Vidéo": format_bitrate(video_stream.get('bit_rate', 'N/A')),
                "🎨 Format Pixel": video_stream.get('pix_fmt', 'N/A'),
                "📁 Taille Fichier": format_file_size(format_info.get('size', 'N/A')),
            }
            
            # Informations audio si présentes
            if audio_streams:
                main_audio = audio_streams[0]
                info.update({
                    "🔊 Codec Audio": main_audio.get('codec_long_name', main_audio.get('codec_name', 'N/A')),
                    "🎵 Débit Audio": format_bitrate(main_audio.get('bit_rate', 'N/A')),
                    "📢 Canaux Audio": main_audio.get('channel_layout', str(main_audio.get('channels', 'N/A'))),
                    "🎼 Fréq. Échantillonnage": f"{main_audio.get('sample_rate', 'N/A')} Hz" if main_audio.get('sample_rate') else 'N/A',
                })
                
                # Si plusieurs pistes audio
                if len(audio_streams) > 1:
                    info["🎙️ Pistes Audio"] = f"{len(audio_streams)} pistes"
            else:
                info["🔇 Audio"] = "Aucune piste audio"
                
        elif mode == 'audio':
            audio_stream = next((s for s in streams if s.get('codec_type') == 'audio'), {})
            
            # Calculer le débit moyen si pas disponible directement
            duration = format_info.get('duration')
            file_size = format_info.get('size')
            calculated_bitrate = "N/A"
            if duration and file_size:
                try:
                    duration_sec = float(duration)
                    size_bytes = int(file_size)
                    bitrate_bps = (size_bytes * 8) / duration_sec
                    calculated_bitrate = format_bitrate(bitrate_bps)
                except:
                    pass
            
            # Déterminer la qualité audio
            quality_indicator = "N/A"
            bitrate = audio_stream.get('bit_rate')
            if bitrate:
                try:
                    br = int(bitrate)
                    if br >= 320000:
                        quality_indicator = "🟢 Très haute (≥320kbps)"
                    elif br >= 192000:
                        quality_indicator = "🟡 Haute (≥192kbps)"
                    elif br >= 128000:
                        quality_indicator = "🟠 Moyenne (≥128kbps)"
                    else:
                        quality_indicator = "🔴 Basse (<128kbps)"
                except:
                    pass
            
            info = {
                "⏱️ Durée": format_duration(format_info.get('duration', 'N/A')),
                "🎵 Codec": audio_stream.get('codec_long_name', audio_stream.get('codec_name', 'N/A')),
                "📊 Débit": format_bitrate(audio_stream.get('bit_rate', calculated_bitrate)),
                "📈 Qualité": quality_indicator,
                "📢 Canaux": audio_stream.get('channel_layout', str(audio_stream.get('channels', 'N/A'))),
                "🎼 Fréq. Échantillonnage": f"{audio_stream.get('sample_rate', 'N/A')} Hz" if audio_stream.get('sample_rate') else 'N/A',
                "🎛️ Bits par échantillon": f"{audio_stream.get('bits_per_sample', 'N/A')} bits" if audio_stream.get('bits_per_sample') else 'N/A',
                "📁 Taille Fichier": format_file_size(format_info.get('size', 'N/A')),
                "🏷️ Format Container": format_info.get('format_long_name', format_info.get('format_name', 'N/A')),
            }
            
            # Métadonnées si disponibles
            tags = format_info.get('tags', {})
            if tags:
                metadata_info = {}
                if tags.get('title'):
                    metadata_info["🎤 Titre"] = tags['title']
                if tags.get('artist'):
                    metadata_info["👤 Artiste"] = tags['artist']
                if tags.get('album'):
                    metadata_info["💿 Album"] = tags['album']
                if tags.get('date') or tags.get('year'):
                    metadata_info["📅 Année"] = tags.get('date', tags.get('year'))
                if tags.get('genre'):
                    metadata_info["🎭 Genre"] = tags['genre']
                    
                if metadata_info:
                    info.update(metadata_info)
                    
        elif mode == 'image':
            image_stream = next((s for s in streams if s.get('codec_type') == 'video'), {})  # Les images sont des streams vidéo
            
            # Calculer les mégapixels
            width = image_stream.get('width', 0)
            height = image_stream.get('height', 0)
            megapixels = "N/A"
            if width and height:
                try:
                    mp = (int(width) * int(height)) / 1000000
                    megapixels = f"{mp:.2f} MP"
                except:
                    pass
            
            # Calculer le ratio d'aspect
            aspect_ratio = "N/A"
            if width and height:
                try:
                    from math import gcd
                    w, h = int(width), int(height)
                    divisor = gcd(w, h)
                    aspect_ratio = f"{w//divisor}:{h//divisor}"
                except:
                    pass
            
            # Déterminer la qualité de résolution
            resolution_quality = "N/A"
            if width and height:
                try:
                    w, h = int(width), int(height)
                    if w >= 3840 and h >= 2160:
                        resolution_quality = "🟢 4K/Ultra HD"
                    elif w >= 1920 and h >= 1080:
                        resolution_quality = "🟡 Full HD"
                    elif w >= 1280 and h >= 720:
                        resolution_quality = "🟠 HD"
                    else:
                        resolution_quality = "🔴 SD"
                except:
                    pass
            
            # Informations sur la profondeur de couleur
            color_info = "N/A"
            pix_fmt = image_stream.get('pix_fmt', '')
            if 'rgba' in pix_fmt.lower():
                color_info = "RGBA (avec transparence)"
            elif 'rgb' in pix_fmt.lower():
                color_info = "RGB (couleur)"
            elif 'gray' in pix_fmt.lower():
                color_info = "Niveaux de gris"
            elif pix_fmt:
                color_info = pix_fmt.upper()
            
            info = {
                "📹 Résolution": f"{width}x{height}",
                "📊 Mégapixels": megapixels,
                "📐 Ratio d'aspect": aspect_ratio,
                "📈 Qualité": resolution_quality,
                "🎨 Codec": image_stream.get('codec_long_name', image_stream.get('codec_name', 'N/A')),
                "🎭 Format Pixel": image_stream.get('pix_fmt', 'N/A'),
                "🌈 Type Couleur": color_info,
                "🏷️ Espace Couleur": image_stream.get('color_space', 'N/A'),
                "📁 Taille Fichier": format_file_size(format_info.get('size', 'N/A')),
            }
            
            # Métadonnées EXIF si disponibles
            tags = format_info.get('tags', {})
            if tags:
                metadata_info = {}
                if tags.get('creation_time'):
                    metadata_info["📅 Date Création"] = tags['creation_time']
                if tags.get('make'):
                    metadata_info["📷 Fabricant"] = tags['make']
                if tags.get('model'):
                    metadata_info["📸 Modèle"] = tags['model']
                if tags.get('software'):
                    metadata_info["💻 Logiciel"] = tags['software']
                    
                if metadata_info:
                    info.update(metadata_info)
        
        return info

    def _update_inspector_ui(self, info: dict):
        self._clear_inspector()
        
        # Afficher un message si aucune information
        if not info:
            ttk.Label(self.inspector_info_frame, text="❌ Impossible de lire les informations du média.", 
                     font=("Helvetica", 11), foreground="red").pack(padx=10, pady=20)
            return

        # Créer un frame avec scrollbar pour les longues listes d'informations
        canvas = tk.Canvas(self.inspector_info_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.inspector_info_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Remplir avec les nouvelles informations
        for i, (label_text, value_text) in enumerate(info.items()):
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill=tk.X, pady=3, padx=10, anchor="n")
            
            # Utiliser une couleur alternée pour une meilleure lisibilité
            bg_color = "#f8f9fa" if i % 2 == 0 else "#ffffff"
            
            # Label avec icône emoji
            label = ttk.Label(frame, text=f"{label_text}:", width=25, anchor="w", 
                            font=("Helvetica", 10, "bold"))
            label.pack(side=tk.LEFT, padx=(0, 10))
            
            # Valeur avec wrapping pour les textes longs
            value_label = ttk.Label(frame, text=value_text or "N/A", anchor="w", 
                                  wraplength=350, font=("Helvetica", 10))
            value_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Pack canvas et scrollbar
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Bind mousewheel pour le scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        canvas.bind("<MouseWheel>", _on_mousewheel)  # Windows
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))  # Linux
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))   # Linux

    def _clear_inspector(self):
        for widget in self.inspector_info_frame.winfo_children():
            widget.destroy()

    def _update_inspector_file_list(self):
        """Met à jour la liste des fichiers dans l'inspecteur média"""
        # Sauvegarder la sélection actuelle
        selection = self.inspector_tree.selection()
        selected_id = selection[0] if selection else None

        # Effacer la liste actuelle
        for i in self.inspector_tree.get_children():
            self.inspector_tree.delete(i)
        
        # Remplir avec les jobs actuels
        for job in self.jobs:
            self.inspector_tree.insert("", "end", iid=str(id(job)), values=(job.src_path.name,))
        
        # Restaurer la sélection si possible
        if selected_id and self.inspector_tree.exists(selected_id):
            self.inspector_tree.selection_set(selected_id)
            self.inspector_tree.focus(selected_id)
            self.inspector_tree.see(selected_id)


class SettingsWindow:
    def __init__(self, master):
        self.top = Toplevel(master)
        self.top.title("Preferences")
        self._build()

    def _build(self):
        concurrency = Settings.data["concurrency"]
        video_conc = Settings.data["video_concurrency"]
        interval = Settings.data["progress_refresh_interval"]

        ttk.Label(self.top, text="Global concurrency (jobs)").grid(row=0, column=0, sticky="w")
        self.cores_var = ttk.Spinbox(self.top, from_=1, to=os.cpu_count(), width=5)
        self.cores_var.set(concurrency)
        self.cores_var.grid(row=0, column=1)

        ttk.Label(self.top, text="Video concurrency").grid(row=1, column=0, sticky="w")
        self.video_var = ttk.Spinbox(self.top, from_=1, to=os.cpu_count(), width=5)
        self.video_var.set(video_conc)
        self.video_var.grid(row=1, column=1)

        ttk.Label(self.top, text="Progress refresh (s)").grid(row=2, column=0, sticky="w")
        self.interval_var = ttk.Spinbox(self.top, from_=1, to=10, width=5)
        self.interval_var.set(interval)
        self.interval_var.grid(row=2, column=1)

        # Keep folder structure
        self.keep_var = BooleanVar(value=Settings.data.get("keep_folder_structure", True))
        ttk.Checkbutton(self.top, text="Keep folder structure", variable=self.keep_var).grid(row=3, column=0, columnspan=2, sticky="w")

        # Default encoders
        ttk.Label(self.top, text="Default video encoder").grid(row=4, column=0, sticky="w")
        self.def_vid_var = StringVar(value=Settings.data.get("default_video_encoder", ""))
        ttk.Entry(self.top, textvariable=self.def_vid_var, width=15).grid(row=4, column=1)

        ttk.Label(self.top, text="Default audio encoder").grid(row=5, column=0, sticky="w")
        self.def_aud_var = StringVar(value=Settings.data.get("default_audio_encoder", ""))
        ttk.Entry(self.top, textvariable=self.def_aud_var, width=15).grid(row=5, column=1)

        ttk.Label(self.top, text="Default image encoder").grid(row=6, column=0, sticky="w")
        self.def_img_var = StringVar(value=Settings.data.get("default_image_encoder", ""))
        ttk.Entry(self.top, textvariable=self.def_img_var, width=15).grid(row=6, column=1)

        # Custom flags
        ttk.Label(self.top, text="Custom ffmpeg flags").grid(row=7, column=0, sticky="w")
        self.flags_var = StringVar(value=Settings.data.get("custom_flags", ""))
        ttk.Entry(self.top, textvariable=self.flags_var, width=25).grid(row=7, column=1)

        save_btn = ttk.Button(self.top, text="Save", command=self._save)
        save_btn.grid(row=8, column=0, columnspan=2, pady=10)

    def _save(self):
        Settings.data["concurrency"] = int(self.cores_var.get())
        Settings.data["video_concurrency"] = int(self.video_var.get())
        Settings.data["progress_refresh_interval"] = int(self.interval_var.get())
        Settings.data["keep_folder_structure"] = self.keep_var.get()
        Settings.data["default_video_encoder"] = self.def_vid_var.get()
        Settings.data["default_audio_encoder"] = self.def_aud_var.get()
        Settings.data["default_image_encoder"] = self.def_img_var.get()
        Settings.data["custom_flags"] = self.flags_var.get()
        Settings.save()
        self.top.destroy()


class JobEditWindow:
    def __init__(self, master, job: EncodeJob):
        self.top = Toplevel(master)
        self.top.title(f"Edit job - {job.src_path.name}")
        self.job = job
        self._build()

    def _build(self):
        nb = ttk.Notebook(self.top)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        video_tab = ttk.Frame(nb)
        audio_tab = ttk.Frame(nb)
        image_tab = ttk.Frame(nb)
        nb.add(video_tab, text="Video")
        nb.add(audio_tab, text="Audio")
        nb.add(image_tab, text="Image")

        # Common vars
        self.encoder_var = StringVar(value=self.job.encoder or "")
        self.quality_var = StringVar(value=str(self.job.quality or ""))
        self.preset_var = StringVar(value=self.job.preset or "")
        self.custom_flags_var = StringVar(value=getattr(self.job, 'custom_flags', '') or "")

        # Video tab
        ttk.Label(video_tab, text="Encoder").grid(row=0, column=0, sticky="w")
        encoders = [e for e in FFmpegHelpers.available_encoders() if any(k in e for k in ["x264", "x265", "av1", "vp9", "h264", "hevc"])]
        ttk.Combobox(video_tab, textvariable=self.encoder_var, values=encoders).grid(row=0, column=1, sticky="ew")

        ttk.Label(video_tab, text="CRF / Quality").grid(row=1, column=0, sticky="w")
        ttk.Entry(video_tab, textvariable=self.quality_var, width=6).grid(row=1, column=1, sticky="w")

        ttk.Label(video_tab, text="Preset").grid(row=2, column=0, sticky="w")
        ttk.Combobox(video_tab, textvariable=self.preset_var, values=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"]).grid(row=2, column=1, sticky="w")

        ttk.Label(video_tab, text="Custom Flags").grid(row=3, column=0, sticky="w")
        ttk.Entry(video_tab, textvariable=self.custom_flags_var, width=40).grid(row=3, column=1, sticky="ew")

        # Audio tab
        ttk.Label(audio_tab, text="Encoder").grid(row=0, column=0, sticky="w")
        a_enc = [e for e in FFmpegHelpers.available_encoders() if any(k in e for k in ["aac", "flac", "opus", "vorbis", "mp3"])]
        ttk.Combobox(audio_tab, textvariable=self.encoder_var, values=a_enc).grid(row=0, column=1, sticky="ew")
        ttk.Label(audio_tab, text="Bitrate / Level").grid(row=1, column=0, sticky="w")
        ttk.Entry(audio_tab, textvariable=self.quality_var, width=6).grid(row=1, column=1, sticky="w")

        ttk.Label(audio_tab, text="Custom Flags").grid(row=2, column=0, sticky="w")
        ttk.Entry(audio_tab, textvariable=self.custom_flags_var, width=40).grid(row=2, column=1, sticky="ew")

        # Image tab
        ttk.Label(image_tab, text="Encoder").grid(row=0, column=0, sticky="w")
        i_enc = [e for e in FFmpegHelpers.available_encoders() if any(k in e for k in ["png", "jpeg", "webp", "avif", "jxl"])]
        ttk.Combobox(image_tab, textvariable=self.encoder_var, values=i_enc).grid(row=0, column=1, sticky="ew")
        ttk.Label(image_tab, text="Quality %").grid(row=1, column=0, sticky="w")
        ttk.Entry(image_tab, textvariable=self.quality_var, width=6).grid(row=1, column=1, sticky="w")

        ttk.Label(image_tab, text="Custom Flags").grid(row=2, column=0, sticky="w")
        ttk.Entry(image_tab, textvariable=self.custom_flags_var, width=40).grid(row=2, column=1, sticky="ew")

        # Save button
        ttk.Button(self.top, text="Save", command=self._save).pack(pady=10)

    def _save(self):
        self.job.encoder = self.encoder_var.get()
        self.job.quality = self.quality_var.get()
        self.job.preset = self.preset_var.get()
        self.job.custom_flags = self.custom_flags_var.get()
        # Update tree view, if exists
        # assume master is MainWindow, update row
        self.top.destroy()


class LogViewerWindow:
    """Fenêtre pour afficher les logs FFmpeg en temps réel"""
    
    def __init__(self, parent):
        self.window = Toplevel(parent)
        self.window.title("FFmpeg Logs")
        self.window.geometry("800x600")
        self.window.minsize(600, 400)
        
        # Créer l'interface
        self._build_interface()
        
        # Liste des logs par job
        self.job_logs = {}
        
    def _build_interface(self):
        """Construit l'interface du viewer de logs"""
        main_frame = ttk.Frame(self.window, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # Barre d'outils en haut
        toolbar = ttk.Frame(main_frame)
        toolbar.pack(fill="x", pady=(0, 10))
        
        # Sélecteur de job
        ttk.Label(toolbar, text="Job:").pack(side="left", padx=(0, 5))
        self.job_var = StringVar()
        self.job_combo = ttk.Combobox(toolbar, textvariable=self.job_var, width=40, state="readonly")
        self.job_combo.pack(side="left", padx=(0, 10))
        self.job_combo.bind("<<ComboboxSelected>>", self._on_job_selected)
        
        # Boutons de contrôle
        ttk.Button(toolbar, text="Clear", command=self._clear_logs).pack(side="left", padx=(0, 5))
        ttk.Button(toolbar, text="Save to File", command=self._save_logs).pack(side="left", padx=(0, 5))
        
        # Auto-scroll checkbox
        self.autoscroll_var = BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="Auto-scroll", variable=self.autoscroll_var).pack(side="right")
        
        # Zone de texte avec scrollbar
        text_frame = ttk.Frame(main_frame)
        text_frame.pack(fill="both", expand=True)
        
        self.text_area = Text(text_frame, wrap="none", font=("Consolas", 10))
        
        # Scrollbars
        v_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text_area.yview)
        h_scroll = ttk.Scrollbar(text_frame, orient="horizontal", command=self.text_area.xview)
        
        self.text_area.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        
        # Pack scrollbars et text area
        v_scroll.pack(side="right", fill="y")
        h_scroll.pack(side="bottom", fill="x")
        self.text_area.pack(side="left", fill="both", expand=True)
        
        # Tags pour coloration
        self.text_area.tag_configure("error", foreground="red")
        self.text_area.tag_configure("warning", foreground="orange")
        self.text_area.tag_configure("info", foreground="blue")
        self.text_area.tag_configure("progress", foreground="green")
    
    def add_job(self, job):
        """Ajoute un nouveau job à surveiller"""
        job_name = f"{job.src_path.name} -> {job.dst_path.name}"
        self.job_logs[str(id(job))] = {
            "name": job_name,
            "logs": [],
            "job": job
        }
        self._update_job_list()
    
    def _update_job_list(self):
        """Met à jour la liste des jobs dans le combobox"""
        job_names = [data["name"] for data in self.job_logs.values()]
        self.job_combo['values'] = job_names
        if job_names and not self.job_var.get():
            self.job_combo.set(job_names[0])
            self._on_job_selected()
    
    def _on_job_selected(self, event=None):
        """Affiche les logs du job sélectionné"""
        selected_name = self.job_var.get()
        if not selected_name:
            return
            
        # Trouver le job correspondant
        for job_data in self.job_logs.values():
            if job_data["name"] == selected_name:
                self._display_logs(job_data["logs"])
                break
    
    def _display_logs(self, logs):
        """Affiche les logs dans la zone de texte"""
        self.text_area.delete(1.0, "end")
        for log_entry in logs:
            self._append_log_line(log_entry["text"], log_entry["type"])
    
    def add_log(self, job, text, log_type="info"):
        """Ajoute une ligne de log pour un job"""
        job_id = str(id(job))
        if job_id not in self.job_logs:
            self.add_job(job)
        
        # Ajouter le log
        self.job_logs[job_id]["logs"].append({
            "text": text,
            "type": log_type,
            "timestamp": time.strftime("%H:%M:%S")
        })
        
        # Si ce job est actuellement affiché, mettre à jour l'affichage
        current_job_name = self.job_var.get()
        if current_job_name == self.job_logs[job_id]["name"]:
            self._append_log_line(text, log_type)
    
    def _append_log_line(self, text, log_type="info"):
        """Ajoute une ligne de texte à la zone de texte"""
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}\n"
        
        self.text_area.insert("end", line, log_type)
        
        # Auto-scroll si activé
        if self.autoscroll_var.get():
            self.text_area.see("end")
    
    def _clear_logs(self):
        """Efface les logs du job actuel"""
        selected_name = self.job_var.get()
        if not selected_name:
            return
            
        for job_id, job_data in self.job_logs.items():
            if job_data["name"] == selected_name:
                job_data["logs"].clear()
                break
        
        self.text_area.delete(1.0, "end")
    
    def _save_logs(self):
        """Sauvegarde les logs dans un fichier"""
        selected_name = self.job_var.get()
        if not selected_name:
            messagebox.showwarning("No Selection", "Please select a job first.")
            return
            
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Save logs to file"
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    content = self.text_area.get(1.0, "end")
                    f.write(content)
                messagebox.showinfo("Success", f"Logs saved to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save logs: {e}")


class BatchOperationsWindow:
    """Fenêtre pour configurer des opérations batch sur plusieurs fichiers sélectionnés"""
    
    def __init__(self, parent, selected_jobs):
        self.window = Toplevel(parent)
        self.window.title("Batch Operations")
        self.window.geometry("600x500")
        self.window.minsize(500, 400)
        
        self.selected_jobs = selected_jobs
        self.parent = parent
        
        # Variables pour les différentes configurations
        self.batch_configs = []
        
        self._build_interface()
        self._populate_initial_data()
        
    def _build_interface(self):
        """Construit l'interface de la fenêtre batch"""
        main_frame = ttk.Frame(self.window, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # En-tête avec information
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Label(header_frame, text=f"Batch Operations - {len(self.selected_jobs)} jobs selected", 
                 font=("Helvetica", 14, "bold")).pack()
        
        # Liste des jobs avec leurs configurations
        list_frame = ttk.LabelFrame(main_frame, text="Job Configurations", padding=10)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        # Créer le treeview pour les configurations batch
        columns = ("file", "encoder", "quality", "preset", "container")
        self.batch_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=10)
        
        # Configuration des en-têtes
        for col, label in zip(columns, ["File", "Encoder", "Quality", "Preset", "Container"]):
            self.batch_tree.heading(col, text=label)
            self.batch_tree.column(col, width=120)
        
        # Scrollbar pour le treeview
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.batch_tree.yview)
        self.batch_tree.configure(yscrollcommand=scrollbar.set)
        
        self.batch_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Bind double-click pour éditer
        self.batch_tree.bind("<Double-1>", self._edit_batch_item)
        
        # Contrôles pour modification batch
        controls_frame = ttk.LabelFrame(main_frame, text="Batch Controls", padding=10)
        controls_frame.pack(fill="x", pady=(0, 10))
        
        # Première ligne: Type et codec
        row1 = ttk.Frame(controls_frame)
        row1.pack(fill="x", pady=(0, 5))
        
        ttk.Label(row1, text="Type:").pack(side="left", padx=(0, 5))
        self.batch_type_var = StringVar(value="video")
        type_combo = ttk.Combobox(row1, textvariable=self.batch_type_var, values=["video", "audio", "image"], width=10, state="readonly")
        type_combo.pack(side="left", padx=(0, 10))
        type_combo.bind("<<ComboboxSelected>>", self._update_batch_codecs)
        
        ttk.Label(row1, text="Codec:").pack(side="left", padx=(0, 5))
        self.batch_codec_var = StringVar()
        self.batch_codec_combo = ttk.Combobox(row1, textvariable=self.batch_codec_var, width=15)
        self.batch_codec_combo.pack(side="left", padx=(0, 10))
        self.batch_codec_combo.bind("<<ComboboxSelected>>", self._update_batch_encoders)
        
        ttk.Label(row1, text="Encoder:").pack(side="left", padx=(0, 5))
        self.batch_encoder_var = StringVar()
        self.batch_encoder_combo = ttk.Combobox(row1, textvariable=self.batch_encoder_var, width=25)
        self.batch_encoder_combo.pack(side="left")
        
        # Deuxième ligne: Qualité et preset
        row2 = ttk.Frame(controls_frame)
        row2.pack(fill="x", pady=(0, 5))
        
        ttk.Label(row2, text="Quality:").pack(side="left", padx=(0, 5))
        self.batch_quality_var = StringVar()
        quality_entry = ttk.Entry(row2, textvariable=self.batch_quality_var, width=10)
        quality_entry.pack(side="left", padx=(0, 10))
        
        ttk.Label(row2, text="Preset:").pack(side="left", padx=(0, 5))
        self.batch_preset_var = StringVar()
        preset_combo = ttk.Combobox(row2, textvariable=self.batch_preset_var, width=12)
        preset_combo.pack(side="left", padx=(0, 10))
        
        ttk.Label(row2, text="Container:").pack(side="left", padx=(0, 5))
        self.batch_container_var = StringVar(value="mp4")
        container_combo = ttk.Combobox(row2, textvariable=self.batch_container_var, 
                                     values=["mp4", "mkv", "mov", "mxf", "webp", "png"], width=8, state="readonly")
        container_combo.pack(side="left")
        
        # Boutons d'action
        button_frame = ttk.Frame(controls_frame)
        button_frame.pack(fill="x", pady=(10, 0))
        
        ttk.Button(button_frame, text="Apply to Selected", command=self._apply_to_selected).pack(side="left", padx=(0, 5))
        ttk.Button(button_frame, text="Apply to All", command=self._apply_to_all).pack(side="left", padx=(0, 5))
        ttk.Button(button_frame, text="Reset All", command=self._reset_all).pack(side="left", padx=(5, 0))
        
        # Boutons de dialogue
        dialog_frame = ttk.Frame(main_frame)
        dialog_frame.pack(fill="x")
        
        ttk.Button(dialog_frame, text="Apply Changes", command=self._apply_changes).pack(side="right", padx=(5, 0))
        ttk.Button(dialog_frame, text="Cancel", command=self.window.destroy).pack(side="right")
        
        self._update_batch_codecs()
    
    def _populate_initial_data(self):
        """Remplit le treeview avec les données initiales des jobs"""
        for job in self.selected_jobs:
            values = (
                job.src_path.name,
                job.encoder or "libx264",
                job.quality or "23",
                job.preset or "medium",
                job.dst_path.suffix[1:] if job.dst_path.suffix else "mp4"
            )
            item_id = self.batch_tree.insert("", "end", values=values)
            # Stocker la référence au job
            self.batch_tree.set(item_id, "#0", str(id(job)))
    
    def _update_batch_codecs(self, event=None):
        """Met à jour la liste des codecs pour le type sélectionné"""
        media_type = self.batch_type_var.get()
        codecs = FFmpegHelpers.available_codecs()
        
        # Filtrer les codecs par type
        if media_type == "video":
            filtered_codecs = [c for c in codecs if c in ["h264", "hevc", "av1", "vp9", "vp8"]]
        elif media_type == "audio":
            filtered_codecs = [c for c in codecs if c in ["aac", "mp3", "opus", "vorbis", "flac"]]
        else:  # image
            filtered_codecs = [c for c in codecs if c in ["webp", "png", "jpeg"]]
        
        self.batch_codec_combo['values'] = filtered_codecs
        if filtered_codecs:
            self.batch_codec_var.set(filtered_codecs[0])
            self._update_batch_encoders()
    
    def _update_batch_encoders(self, event=None):
        """Met à jour la liste des encodeurs pour le codec sélectionné"""
        codec = self.batch_codec_var.get()
        if not codec:
            return
            
        all_encoders = FFmpegHelpers.available_encoders()
        compatible_encoders = []
        
        for encoder_name, description in all_encoders:
            if codec in encoder_name.lower():
                display_text = f"{encoder_name} - {description}"
                compatible_encoders.append(display_text)
        
        self.batch_encoder_combo['values'] = compatible_encoders
        if compatible_encoders:
            self.batch_encoder_combo.set(compatible_encoders[0])
    
    def _edit_batch_item(self, event):
        """Édite un item spécifique du batch"""
        selection = self.batch_tree.selection()
        if not selection:
            return
            
        item_id = selection[0]
        values = self.batch_tree.item(item_id, 'values')
        
        # Ouvrir une petite fenêtre d'édition
        edit_window = Toplevel(self.window)
        edit_window.title("Edit Job Settings")
        edit_window.geometry("400x200")
        edit_window.transient(self.window)
        edit_window.grab_set()
        
        # Variables pour l'édition
        edit_vars = {}
        labels = ["Encoder", "Quality", "Preset", "Container"]
        for i, label in enumerate(labels, 1):  # Skip filename
            ttk.Label(edit_window, text=f"{label}:").grid(row=i-1, column=0, padx=10, pady=5, sticky="w")
            var = StringVar(value=values[i])
            entry = ttk.Entry(edit_window, textvariable=var, width=30)
            entry.grid(row=i-1, column=1, padx=10, pady=5)
            edit_vars[label.lower()] = var
        
        def save_edit():
            new_values = (values[0],) + tuple(edit_vars[key].get() for key in ["encoder", "quality", "preset", "container"])
            self.batch_tree.item(item_id, values=new_values)
            edit_window.destroy()
        
        button_frame = ttk.Frame(edit_window)
        button_frame.grid(row=len(labels), column=0, columnspan=2, pady=10)
        ttk.Button(button_frame, text="Save", command=save_edit).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Cancel", command=edit_window.destroy).pack(side="left", padx=5)
    
    def _apply_to_selected(self):
        """Applique les paramètres actuels aux items sélectionnés"""
        selection = self.batch_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select items to modify.")
            return
        
        encoder_display = self.batch_encoder_var.get()
        encoder_name = encoder_display.split(" - ")[0] if " - " in encoder_display else encoder_display
        
        for item_id in selection:
            values = list(self.batch_tree.item(item_id, 'values'))
            values[1] = encoder_name
            values[2] = self.batch_quality_var.get()
            values[3] = self.batch_preset_var.get()
            values[4] = self.batch_container_var.get()
            self.batch_tree.item(item_id, values=values)
    
    def _apply_to_all(self):
        """Applique les paramètres actuels à tous les items"""
        encoder_display = self.batch_encoder_var.get()
        encoder_name = encoder_display.split(" - ")[0] if " - " in encoder_display else encoder_display
        
        for item_id in self.batch_tree.get_children():
            values = list(self.batch_tree.item(item_id, 'values'))
            values[1] = encoder_name
            values[2] = self.batch_quality_var.get()
            values[3] = self.batch_preset_var.get()
            values[4] = self.batch_container_var.get()
            self.batch_tree.item(item_id, values=values)
    
    def _reset_all(self):
        """Remet tous les items à leurs valeurs par défaut"""
        for job in self.selected_jobs:
            for item_id in self.batch_tree.get_children():
                if self.batch_tree.set(item_id, "#0") == str(id(job)):
                    values = (
                        job.src_path.name,
                        job.encoder or "libx264",
                        job.quality or "23",
                        job.preset or "medium",
                        job.dst_path.suffix[1:] if job.dst_path.suffix else "mp4"
                    )
                    self.batch_tree.item(item_id, values=values)
                    break
    
    def _apply_changes(self):
        """Applique tous les changements aux jobs réels"""
        try:
            for item_id in self.batch_tree.get_children():
                job_id = self.batch_tree.set(item_id, "#0")
                job = next((j for j in self.selected_jobs if str(id(j)) == job_id), None)
                if job:
                    values = self.batch_tree.item(item_id, 'values')
                    job.encoder = values[1]
                    job.quality = values[2]
                    job.preset = values[3]
                    # Mettre à jour l'extension si nécessaire
                    new_container = values[4]
                    if job.dst_path.suffix[1:] != new_container:
                        job.dst_path = job.dst_path.with_suffix(f".{new_container}")
            
            messagebox.showinfo("Success", f"Applied changes to {len(self.selected_jobs)} jobs.")
            self.window.destroy()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply changes: {e}")


class AudioTracksWindow:
    """Fenêtre pour configurer les pistes audio (sélection, réencodage, suppression)"""
    
    def __init__(self, parent, job: EncodeJob):
        self.window = Toplevel(parent)
        self.window.title(f"Audio Tracks - {job.src_path.name}")
        self.window.geometry("600x400")
        self.window.minsize(500, 300)
        
        self.job = job
        self.audio_tracks = []
        
        self._build_interface()
        self._load_audio_tracks()
        
    def _build_interface(self):
        """Construit l'interface de configuration audio"""
        main_frame = ttk.Frame(self.window, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # En-tête
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(header_frame, text="Audio Track Configuration", 
                 font=("Helvetica", 14, "bold")).pack()
        
        # Mode de traitement audio
        mode_frame = ttk.LabelFrame(main_frame, text="Audio Processing Mode", padding=10)
        mode_frame.pack(fill="x", pady=(0, 10))
        
        self.audio_mode_var = StringVar(value=self.job.audio_config["mode"])
        
        ttk.Radiobutton(mode_frame, text="Auto (Copy if compatible, encode if needed)", 
                       variable=self.audio_mode_var, value="auto").pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Copy all audio tracks (no re-encoding)", 
                       variable=self.audio_mode_var, value="copy").pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Re-encode all audio tracks", 
                       variable=self.audio_mode_var, value="encode").pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Remove all audio tracks", 
                       variable=self.audio_mode_var, value="remove").pack(anchor="w")
        
        # Liste des pistes audio
        tracks_frame = ttk.LabelFrame(main_frame, text="Audio Tracks", padding=10)
        tracks_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        # Treeview pour les pistes
        columns = ("track", "codec", "channels", "sample_rate", "bitrate", "language", "include")
        self.tracks_tree = ttk.Treeview(tracks_frame, columns=columns, show="headings", height=8)
        
        for col, label in zip(columns, ["Track", "Codec", "Channels", "Sample Rate", "Bitrate", "Language", "Include"]):
            self.tracks_tree.heading(col, text=label)
            if col == "include":
                self.tracks_tree.column(col, width=60)
            else:
                self.tracks_tree.column(col, width=80)
        
        scrollbar_tracks = ttk.Scrollbar(tracks_frame, orient="vertical", command=self.tracks_tree.yview)
        self.tracks_tree.configure(yscrollcommand=scrollbar_tracks.set)
        
        self.tracks_tree.pack(side="left", fill="both", expand=True)
        scrollbar_tracks.pack(side="right", fill="y")
        
        # Configuration d'encodage
        encode_frame = ttk.LabelFrame(main_frame, text="Re-encoding Settings", padding=10)
        encode_frame.pack(fill="x", pady=(0, 10))
        
        settings_row = ttk.Frame(encode_frame)
        settings_row.pack(fill="x")
        
        ttk.Label(settings_row, text="Audio Codec:").pack(side="left", padx=(0, 5))
        self.audio_codec_var = StringVar(value=self.job.audio_config["audio_codec"])
        codec_combo = ttk.Combobox(settings_row, textvariable=self.audio_codec_var, 
                                  values=["aac", "mp3", "opus", "vorbis", "flac"], width=10, state="readonly")
        codec_combo.pack(side="left", padx=(0, 15))
        
        ttk.Label(settings_row, text="Bitrate:").pack(side="left", padx=(0, 5))
        self.audio_bitrate_var = StringVar(value=self.job.audio_config["audio_bitrate"])
        bitrate_combo = ttk.Combobox(settings_row, textvariable=self.audio_bitrate_var,
                                   values=["96k", "128k", "192k", "256k", "320k"], width=8, state="readonly")
        bitrate_combo.pack(side="left")
        
        # Boutons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x")
        
        ttk.Button(button_frame, text="Detect Tracks", command=self._load_audio_tracks).pack(side="left")
        ttk.Button(button_frame, text="Select All", command=self._select_all_tracks).pack(side="left", padx=(10, 0))
        ttk.Button(button_frame, text="Select None", command=self._select_no_tracks).pack(side="left", padx=(5, 0))
        
        ttk.Button(button_frame, text="Cancel", command=self.window.destroy).pack(side="right")
        ttk.Button(button_frame, text="Apply", command=self._apply_settings).pack(side="right", padx=(0, 10))
        
        # Bind double-click pour toggle include
        self.tracks_tree.bind("<Double-1>", self._toggle_track_include)
    
    def _load_audio_tracks(self):
        """Charge les informations des pistes audio du fichier"""
        try:
            # Utiliser ffprobe pour obtenir les infos des pistes audio
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json", 
                "-show_streams", "-select_streams", "a", str(self.job.src_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                self.audio_tracks.clear()
                
                # Effacer l'arbre
                for item in self.tracks_tree.get_children():
                    self.tracks_tree.delete(item)
                
                # Ajouter chaque piste audio
                for i, stream in enumerate(data.get("streams", [])):
                    if stream.get("codec_type") == "audio":
                        track_info = {
                            "index": i,
                            "codec": stream.get("codec_name", "unknown"),
                            "channels": stream.get("channels", "unknown"),
                            "sample_rate": stream.get("sample_rate", "unknown"),
                            "bit_rate": stream.get("bit_rate", "unknown"),
                            "language": stream.get("tags", {}).get("language", "unknown"),
                            "included": i in self.job.audio_config.get("selected_tracks", []) or len(self.job.audio_config.get("selected_tracks", [])) == 0
                        }
                        self.audio_tracks.append(track_info)
                        
                        # Ajouter à l'arbre
                        bitrate = track_info["bit_rate"]
                        if bitrate != "unknown" and bitrate.isdigit():
                            bitrate = f"{int(bitrate)//1000}k"
                        
                        values = (
                            f"Track {i}",
                            track_info["codec"],
                            str(track_info["channels"]),
                            f"{track_info['sample_rate']} Hz" if track_info["sample_rate"] != "unknown" else "unknown",
                            bitrate,
                            track_info["language"],
                            "" if track_info["included"] else ""
                        )
                        
                        item_id = self.tracks_tree.insert("", "end", values=values)
                        # Stocker l'index de la piste
                        self.tracks_tree.set(item_id, "#0", str(i))
                
                if not self.audio_tracks:
                    # Pas de pistes audio trouvées
                    self.tracks_tree.insert("", "end", values=("No audio tracks found", "", "", "", "", "", ""))
                    
            else:
                messagebox.showerror("Error", f"Failed to analyze audio tracks: {result.stderr}")
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load audio tracks: {e}")
    
    def _toggle_track_include(self, event):
        """Toggle l'inclusion d'une piste audio"""
        item = self.tracks_tree.selection()[0] if self.tracks_tree.selection() else None
        if not item:
            return
            
        try:
            track_index = int(self.tracks_tree.set(item, "#0"))
            track_info = self.audio_tracks[track_index]
            track_info["included"] = not track_info["included"]
            
            # Mettre à jour l'affichage
            values = list(self.tracks_tree.item(item, "values"))
            values[6] = "" if track_info["included"] else ""
            self.tracks_tree.item(item, values=values)
            
        except (ValueError, IndexError):
            pass
    
    def _select_all_tracks(self):
        """Sélectionne toutes les pistes audio"""
        for track in self.audio_tracks:
            track["included"] = True
        self._refresh_tree_display()
    
    def _select_no_tracks(self):
        """Désélectionne toutes les pistes audio"""
        for track in self.audio_tracks:
            track["included"] = False
        self._refresh_tree_display()
    
    def _refresh_tree_display(self):
        """Met à jour l'affichage de l'arbre"""
        for item in self.tracks_tree.get_children():
            try:
                track_index = int(self.tracks_tree.set(item, "#0"))
                track_info = self.audio_tracks[track_index]
                values = list(self.tracks_tree.item(item, "values"))
                values[6] = "" if track_info["included"] else ""
                self.tracks_tree.item(item, values=values)
            except (ValueError, IndexError):
                pass
    
    def _apply_settings(self):
        """Applique les paramètres audio au job"""
        # Sauvegarder la configuration
        self.job.audio_config["mode"] = self.audio_mode_var.get()
        self.job.audio_config["audio_codec"] = self.audio_codec_var.get()
        self.job.audio_config["audio_bitrate"] = self.audio_bitrate_var.get()
        
        # Sauvegarder les pistes sélectionnées
        selected_tracks = [track["index"] for track in self.audio_tracks if track["included"]]
        self.job.audio_config["selected_tracks"] = selected_tracks
        
        messagebox.showinfo("Success", "Audio configuration applied successfully!")
        self.window.destroy()


class AdvancedFiltersWindow:
    """Fenêtre pour configurer les filtres avancés d'un job"""
    
    def __init__(self, parent, job: EncodeJob):
        self.parent = parent
        self.job = job
        self.window = Toplevel(parent)
        self.window.title(f"Filtres avancés - {job.src_path.name}")
        self.window.geometry("600x400")
        self.window.transient(parent)
        self.window.grab_set()
        
        # Variables pour les filtres
        self.filter_vars = {}
        self._create_filter_vars()
        
        # Variable pour la gestion du throttle
        self._preview_update_job = None
        
        self._build_interface()
        self._load_current_filters()
    
    def _create_filter_vars(self):
        """Crée les variables pour tous les filtres"""
        # Filtres de couleur et luminosité
        self.brightness_var = IntVar(value=0)
        self.contrast_var = IntVar(value=0)
        self.saturation_var = IntVar(value=0)
        self.gamma_var = DoubleVar(value=1.0)
        self.hue_var = IntVar(value=0)
        
        # Filtres de géométrie
        self.crop_x_var = IntVar(value=0)
        self.crop_y_var = IntVar(value=0)
        self.crop_w_var = IntVar(value=0)
        self.crop_h_var = IntVar(value=0)
        self.rotate_var = IntVar(value=0)
        self.flip_h_var = BooleanVar(value=False)
        self.flip_v_var = BooleanVar(value=False)
        
        # Filtres d'amélioration
        self.sharpness_var = IntVar(value=0)
        self.noise_reduction_var = IntVar(value=0)
    
    def _build_interface(self):
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Notebook pour les onglets
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        # Onglets
        color_frame = ttk.Frame(notebook)
        geometry_frame = ttk.Frame(notebook)
        enhancement_frame = ttk.Frame(notebook)
        notebook.add(color_frame, text="Couleur")
        notebook.add(geometry_frame, text="Géométrie")
        notebook.add(enhancement_frame, text="Amélioration")

        self._build_color_tab(color_frame)
        self._build_geometry_tab(geometry_frame)
        self._build_enhancement_tab(enhancement_frame)

        # Boutons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        ttk.Button(button_frame, text="Appliquer", command=self._apply_filters).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Annuler", command=self._cancel).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Réinitialiser tout", command=self._reset_all_filters).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Afficher l'aperçu", command=self._show_preview_frame).pack(side=tk.LEFT, padx=5)
    
    def _build_color_tab(self, parent):
        """Construit l'onglet des filtres de couleur et luminosité"""
        # Brightness
        self._create_slider_control(parent, "Brightness", self.brightness_var, -100, 100, 0)
        
        # Contrast
        self._create_slider_control(parent, "Contrast", self.contrast_var, -100, 100, 1)
        
        # Saturation
        self._create_slider_control(parent, "Saturation", self.saturation_var, -100, 100, 2)
        
        # Gamma
        self._create_slider_control(parent, "Gamma", self.gamma_var, 0.1, 3.0, 3, is_float=True, resolution=0.1)
        
        # Hue
        self._create_slider_control(parent, "Hue", self.hue_var, -180, 180, 4)
    
    def _build_geometry_tab(self, parent):
        """Construit l'onglet des filtres de géométrie"""
        # Crop controls
        crop_frame = ttk.LabelFrame(parent, text="Crop", padding=10)
        crop_frame.pack(fill="x", pady=(0, 15))
        
        ttk.Label(crop_frame, text="X:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        ttk.Entry(crop_frame, textvariable=self.crop_x_var, width=8).grid(row=0, column=1, padx=(0, 10))
        
        ttk.Label(crop_frame, text="Y:").grid(row=0, column=2, sticky="w", padx=(0, 5))
        ttk.Entry(crop_frame, textvariable=self.crop_y_var, width=8).grid(row=0, column=3, padx=(0, 10))
        
        ttk.Label(crop_frame, text="Width:").grid(row=1, column=0, sticky="w", padx=(0, 5), pady=(5, 0))
        ttk.Entry(crop_frame, textvariable=self.crop_w_var, width=8).grid(row=1, column=1, padx=(0, 10), pady=(5, 0))
        
        ttk.Label(crop_frame, text="Height:").grid(row=1, column=2, sticky="w", padx=(0, 5), pady=(5, 0))
        ttk.Entry(crop_frame, textvariable=self.crop_h_var, width=8).grid(row=1, column=3, padx=(0, 10), pady=(5, 0))
        
        # Rotation
        rotate_frame = ttk.LabelFrame(parent, text="Rotation", padding=10)
        rotate_frame.pack(fill="x", pady=(0, 15))
        
        ttk.Radiobutton(rotate_frame, text="0°", variable=self.rotate_var, value=0).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(rotate_frame, text="90°", variable=self.rotate_var, value=90).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(rotate_frame, text="180°", variable=self.rotate_var, value=180).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(rotate_frame, text="270°", variable=self.rotate_var, value=270).pack(side="left")
        
        # Flip
        flip_frame = ttk.LabelFrame(parent, text="Flip", padding=10)
        flip_frame.pack(fill="x")
        
        ttk.Checkbutton(flip_frame, text="Flip Horizontal", variable=self.flip_h_var).pack(side="left", padx=(0, 15))
        ttk.Checkbutton(flip_frame, text="Flip Vertical", variable=self.flip_v_var).pack(side="left")
    
    def _build_enhancement_tab(self, parent):
        """Construit l'onglet des filtres d'amélioration"""
        # Sharpness
        self._create_slider_control(parent, "Sharpness", self.sharpness_var, -10, 10, 0)
        
        # Noise Reduction
        self._create_slider_control(parent, "Noise Reduction", self.noise_reduction_var, 0, 100, 1)
    
    def _create_slider_control(self, parent, label, variable, min_val, max_val, row, is_float=False, resolution=1):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=2, pady=5, padx=5, sticky="ew")
        ttk.Label(frame, text=label).pack(side=tk.LEFT, padx=5)
        
        display_var = StringVar()
        ttk.Label(frame, textvariable=display_var, width=5).pack(side=tk.RIGHT, padx=5)
        
        def update_label(*args):
            value = variable.get()
            display_var.set(f"{value}")
        
        variable.trace_add("write", update_label)
        update_label()
        
        scale = ttk.Scale(frame, from_=min_val, to=max_val, variable=variable, orient=tk.HORIZONTAL, length=300)
        if is_float:
            scale.configure(resolution=resolution)
        scale.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        scale.config(command=lambda *args: self._schedule_preview_update())
        return frame
    
    def _load_current_filters(self):
        """Charge les filtres actuels du job dans l'interface"""
        filters = self.job.filters
        
        # Couleur et luminosité
        self.brightness_var.set(filters.get("brightness", 0))
        self.contrast_var.set(filters.get("contrast", 0))
        self.saturation_var.set(filters.get("saturation", 0))
        self.gamma_var.set(filters.get("gamma", 1.0))
        self.hue_var.set(filters.get("hue", 0))
        
        # Géométrie
        self.crop_x_var.set(filters.get("crop_x", 0))
        self.crop_y_var.set(filters.get("crop_y", 0))
        self.crop_w_var.set(filters.get("crop_w", 0))
        self.crop_h_var.set(filters.get("crop_h", 0))
        self.rotate_var.set(filters.get("rotate", 0))
        self.flip_h_var.set(filters.get("flip_h", False))
        self.flip_v_var.set(filters.get("flip_v", False))
        
        # Amélioration
        self.sharpness_var.set(filters.get("sharpness", 0))
        self.noise_reduction_var.set(filters.get("noise_reduction", 0))
    
    def _reset_all_filters(self):
        """Remet tous les filtres à leurs valeurs par défaut"""
        self.brightness_var.set(0)
        self.contrast_var.set(0)
        self.saturation_var.set(0)
        self.gamma_var.set(1.0)
        self.hue_var.set(0)
        
        self.crop_x_var.set(0)
        self.crop_y_var.set(0)
        self.crop_w_var.set(0)
        self.crop_h_var.set(0)
        self.rotate_var.set(0)
        self.flip_h_var.set(False)
        self.flip_v_var.set(False)
        
        self.sharpness_var.set(0)
        self.noise_reduction_var.set(0)
    
    def _show_preview_frame(self):
        # Vérifier si PIL est disponible
        if Image is None or ImageTk is None:
            messagebox.showerror("Dépendance manquante", "Le module 'Pillow' n'est pas installé. Veuillez l'installer avec 'pip install Pillow'.")
            return
            
        # Calculer le timestamp au milieu de la vidéo pour un cadre représentatif
        try:
            probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(self.job.src_path)
            ]
            duration = float(subprocess.check_output(probe_cmd).decode().strip())
            timestamp = duration / 2
        except:
            timestamp = 10  # Valeur par défaut si la durée ne peut pas être déterminée
        
        # Créer un job temporaire avec les filtres actuels
        temp_job = copy.deepcopy(self.job)
        self._apply_vars_to_job(temp_job)
        
        # Construire la commande FFmpeg pour extraire une image avec les filtres appliqués
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            output_path = temp_file.name
        
        filter_string = self._build_filter_string(temp_job.filters)
        
        command = [
            "ffmpeg",
            "-ss", str(timestamp),
            "-i", str(self.job.src_path),
            "-vf", filter_string,
            "-vframes", "1",
            "-y",  # Écraser le fichier temporaire si nécessaire
            output_path
        ]
        
        # Exécuter la commande
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Afficher l'image dans une nouvelle fenêtre
            self._display_preview_image(output_path)
            os.unlink(output_path)  # Supprimer le fichier temporaire
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Erreur", f"Erreur lors de la génération de l'aperçu: {e.stderr.decode()}")

    def _build_filter_string(self, filters: dict) -> str:
        # Construire la chaîne de filtres à partir du dictionnaire des filtres
        filter_parts = []
        
        # Couleur
        brightness = filters.get('brightness', 0)
        if brightness != 0:
            filter_parts.append(f"eq=brightness={brightness/100}")
        contrast = filters.get('contrast', 0)
        if contrast != 0:
            filter_parts.append(f"eq=contrast={1 + contrast/100}")
        saturation = filters.get('saturation', 0)
        if saturation != 0:
            filter_parts.append(f"eq=saturation={1 + saturation/100}")
        gamma = filters.get('gamma', 0)
        if gamma != 0:
            filter_parts.append(f"eq=gamma={1 + gamma/100}")
        
        # Géométrie
        rotate = filters.get('rotate', 0)
        if rotate != 0:
            filter_parts.append(f"rotate={rotate}*PI/180")
        flip_h = filters.get('flip_h', 0)
        if flip_h:
            filter_parts.append("hflip")
        flip_v = filters.get('flip_v', 0)
        if flip_v:
            filter_parts.append("vflip")
        crop = filters.get('crop_w', 0)
        if crop > 0:
            filter_parts.append(f"crop=in_w*{crop/100}:in_h*{crop/100}:in_w*(1-{crop/100})/2:in_h*(1-{crop/100})/2")
        
        # Amélioration
        sharpness = filters.get('sharpness', 0)
        if sharpness != 0:
            filter_parts.append(f"unsharp=5:5:{sharpness/10}")
        denoise = filters.get('denoise', 0)
        if denoise != 0:
            filter_parts.append(f"nlmeans={denoise/10}")
        deblock = filters.get('deblock', 0)
        if deblock != 0:
            filter_parts.append(f"deblock={deblock/10}")
        
        return ','.join(filter_parts) if filter_parts else 'null'

    def _display_preview_image(self, image_path):
        # Créer la fenêtre d'aperçu si elle n'existe pas ou a été fermée
        if not hasattr(self, 'preview_window') or not self.preview_window.winfo_exists():
            self.preview_window = tk.Toplevel(self.window)
            self.preview_window.title("Aperçu des filtres")
            self.preview_label = ttk.Label(self.preview_window)
            self.preview_label.pack(padx=10, pady=10)
        
        # Charger et afficher l'image
        image = Image.open(image_path)
        image.thumbnail((640, 480))  # Redimensionner si trop grand
        self.photo_image = ImageTk.PhotoImage(image)
        self.preview_label.configure(image=self.photo_image)

    def _schedule_preview_update(self):
        # Mettre à jour l'aperçu avec un délai pour éviter les exécutions trop fréquentes
        if hasattr(self, '_preview_update_job') and self._preview_update_job is not None:
            self.window.after_cancel(self._preview_update_job)
        self._preview_update_job = self.window.after(500, self._show_preview_frame)
    
    def _apply_vars_to_job(self, job):
        """Applique les variables actuelles aux filtres du job"""
        job.filters.update({
            "brightness": self.brightness_var.get(),
            "contrast": self.contrast_var.get(),
            "saturation": self.saturation_var.get(),
            "gamma": self.gamma_var.get(),
            "hue": self.hue_var.get(),
            "crop_x": self.crop_x_var.get(),
            "crop_y": self.crop_y_var.get(),
            "crop_w": self.crop_w_var.get(),
            "crop_h": self.crop_h_var.get(),
            "rotate": self.rotate_var.get(),
            "flip_h": self.flip_h_var.get(),
            "flip_v": self.flip_v_var.get(),
            "sharpness": self.sharpness_var.get(),
            "noise_reduction": self.noise_reduction_var.get()
        })
    
    def _apply_filters(self):
        """Applique les filtres au job et ferme la fenêtre"""
        self._apply_vars_to_job(self.job)
        messagebox.showinfo("Applied", "Filters have been applied to the selected job.")
        self.window.destroy()
    
    def _cancel(self):
        """Ferme la fenêtre sans appliquer les changements"""
        self.window.destroy()


class FolderWatcher(threading.Thread):
    def __init__(self, path, callback, stop_event):
        super().__init__(daemon=True)
        self.path = path
        self.callback = callback
        self.stop_event = stop_event
        self.observer = Observer()
        self.event_handler = WatcherEventHandler(callback)
    
    def run(self):
        self.observer.schedule(self.event_handler, str(self.path), recursive=True)
        self.observer.start()
        self.stop_event.wait()
        self.observer.stop()
        self.observer.join()

class WatcherEventHandler(FileSystemEventHandler):
    def __init__(self, callback):
        self.callback = callback
    
    def on_created(self, event):
        if not event.is_directory:
            self.callback(Path(event.src_path))


def main():
    Settings.load()
    
    # Utiliser TkinterDnD si disponible pour le drag & drop
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = Tk()
    
    root.geometry("1200x700")
    root.minsize(800, 500)
    
    app = MainWindow(root)
    
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        # Arrêter le pool de workers proprement
        app.pool.stop()


if __name__ == "__main__":
    main() 