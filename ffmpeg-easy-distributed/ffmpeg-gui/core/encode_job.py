import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
import uuid # For unique IDs for OutputConfig
from enum import Enum
import json

# Using a simple class for OutputConfig for now. Could be a dataclass in Python 3.7+
class OutputConfig:
    def __init__(self, name: str, initial_dst_path: Path, mode: str):
        self.id: str = str(uuid.uuid4()) # Unique ID for this output configuration
        self.name: str = name # User-friendly name like "1080p H.264" or "AV1 4K"
        self.dst_path: Path = initial_dst_path # Will be updated by filename template
        self.mode: str = mode # video, audio, image, gif - usually same as parent EncodeJob but could differ

        self.encoder: str = ""
        self.container: str = "" # e.g. "mp4", "mkv" - crucial for filename template and ffmpeg
        self.quality: str = "" # CRF value for video, quality for image, bitrate for audio (e.g. "192k")
        self.cq_value: str = "" # Specific CQ value if different from quality (e.g. for QSV)
        self.preset: str = "" # Encoder preset (e.g. "medium", "slow")
        self.custom_flags: str = ""

        self.status: str = "pending"  # pending, running, paused, done, error, cancelled
        self.progress: float = 0.0
        self.process: Optional[subprocess.Popen] = None # FFmpeg process for this specific output
        self.is_paused: bool = False
        # is_cancelled is handled by the parent EncodeJob for all its outputs

        # Encoding parameters (mirrors what was in EncodeJob, now per output)
        self.video_mode: str = "quality"  # quality, bitrate
        self.bitrate: str = ""
        self.multipass: bool = False
        self.longest_side: str = ""
        self.megapixels: str = ""
        
        self.filters: Dict[str, Any] = { # Default filters, can be customized per output
            "brightness": 0, "contrast": 0, "saturation": 0, "gamma": 1.0, "hue": 0,
            "sharpness": 0, "noise_reduction": 0, "scale_width": 0, "scale_height": 0,
            "crop_x": 0, "crop_y": 0, "crop_w": 0, "crop_h": 0,
            "rotate": 0, "flip_h": False, "flip_v": False,
        }
        self.audio_config: Dict[str, Any] = { # Default audio config
            "mode": "auto", "selected_tracks": [], "audio_codec": "aac", "audio_bitrate": "128k"
        }
        self.subtitle_config: Dict[str, Any] = { # Default subtitle config
            "mode": "copy", "external_path": None, "burn_track": -1
        }
        self.trim_config: Dict[str, Any] = {"start": "", "end": ""} # Default trim
        self.gif_config: Dict[str, Any] = {"fps": 15, "use_palette": True} # Default GIF
        self.lut_path: Optional[str] = None # Path to the LUT file

        # Watermark settings
        self.watermark_path: Optional[str] = None
        self.watermark_position: str = "top_right" # e.g., top_left, top_right, bottom_left, bottom_right, center
        self.watermark_scale: float = 0.1 # Relative to video width (e.g., 0.1 = 10% of video width)
        self.watermark_opacity: float = 1.0 # 0.0 (transparent) to 1.0 (opaque)
        self.watermark_padding: int = 10 # Pixels

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "dst_path": str(self.dst_path),
            "mode": self.mode,
            "encoder": self.encoder,
            "container": self.container,
            "quality": self.quality,
            "cq_value": self.cq_value,
            "preset": self.preset,
            "custom_flags": self.custom_flags,
            "status": self.status,
            "progress": self.progress,
            "video_mode": self.video_mode,
            "bitrate": self.bitrate,
            "multipass": self.multipass,
            "longest_side": self.longest_side,
            "megapixels": self.megapixels,
            "filters": self.filters,
            "audio_config": self.audio_config,
            "subtitle_config": self.subtitle_config,
            "trim_config": self.trim_config,
            "gif_config": self.gif_config,
            "lut_path": self.lut_path,
            "watermark_path": self.watermark_path,
            "watermark_position": self.watermark_position,
            "watermark_scale": self.watermark_scale,
            "watermark_opacity": self.watermark_opacity,
            "watermark_padding": self.watermark_padding,
        }

    @classmethod
    def from_dict(cls, data):
        # Create a dummy path for init, it will be overwritten
        config = cls(name=data["name"], initial_dst_path=Path("dummy"), mode=data["mode"])
        for key, value in data.items():
            if key == "dst_path":
                setattr(config, key, Path(value))
            elif hasattr(config, key):
                setattr(config, key, value)
        return config


class EncodeJob:
    """Représente un travail d'encodage unique."""
    def __init__(self, src_path: Path, mode: str, initial_output_config: Optional[OutputConfig] = None):
        self.job_id: str = str(uuid.uuid4())
        self.src_path = src_path
        self.relative_src_path: Optional[Path] = None
        self.mode = mode  # 'video', 'audio', 'image', 'gif'
        self.outputs: List[OutputConfig] = []
        if initial_output_config:
            self.outputs.append(initial_output_config)

        # Overall status and progress for the EncodeJob (aggregated from outputs)
        # self.status: str = "pending" # This will be derived
        # self.progress: float = 0.0 # This will be derived
        self.duration: Optional[float] = None # Duration of the source, fetched once

        self.is_cancelled: bool = False # Cancellation applies to all outputs of this job

        self.assigned_server: Optional[str] = None
        
        self._media_info_cache: Optional[Dict[str, Any]] = None
        self._ffprobe_json_cache: Optional[Dict[str, Any]] = None

    def to_dict(self):
        return {
            "job_id": self.job_id,
            "src_path": str(self.src_path),
            "relative_src_path": str(self.relative_src_path) if self.relative_src_path else None,
            "mode": self.mode,
            "outputs": [o.to_dict() for o in self.outputs],
            "duration": self.duration,
            "is_cancelled": self.is_cancelled,
        }

    @classmethod
    def from_dict(cls, data):
        job = cls(src_path=Path(data["src_path"]), mode=data["mode"])
        job.job_id = data["job_id"]
        job.relative_src_path = Path(data["relative_src_path"]) if data.get("relative_src_path") else None
        job.duration = data.get("duration")
        job.is_cancelled = data.get("is_cancelled", False)
        
        job.outputs = [OutputConfig.from_dict(o_data) for o_data in data["outputs"]]
        
        # Reset runtime state for jobs loaded from file
        for output in job.outputs:
            if output.status not in ["done", "error", "cancelled"]:
                output.status = "pending"
                output.progress = 0
            output.process = None
            output.is_paused = False

        return job

    @property
    def status(self) -> str:
        """Property to get the overall status, for backward compatibility."""
        return self.get_overall_status()

    @status.setter
    def status(self, value: str):
        """Property to set the overall status, propagating to all outputs."""
        if value == "cancelled":
            self.cancel_all_outputs()
        else:
            for out in self.outputs:
                out.status = value

    def get_overall_status(self) -> str:
        if not self.outputs:
            return "pending" # Or "empty" / "misconfigured"
        if self.is_cancelled:
            return "cancelled"

        statuses = [out.status for out in self.outputs]
        if any(s == "running" for s in statuses): return "running"
        if any(s == "paused" for s in statuses): return "paused"
        # If all are done, it's done. If all are pending, it's pending.
        # If all are error/cancelled/done, and at least one error, it's error.
        if all(s == "done" for s in statuses): return "done"
        if all(s == "pending" for s in statuses): return "pending"
        if all(s in ["done", "error", "cancelled"] for s in statuses):
            if any(s == "error" for s in statuses): return "error"
            # If here, all are done or cancelled (no errors, no pending, no running)
            if any(s == "done" for s in statuses): return "done" # At least one done, others cancelled
            return "cancelled" # All must be cancelled

        return "mixed" # Some pending, some done, etc. but not running/paused

    def get_overall_progress(self) -> float:
        if not self.outputs:
            return 0.0
        total_progress = sum(out.progress for out in self.outputs)
        return total_progress / len(self.outputs) if self.outputs else 0.0

    def cancel_all_outputs(self):
        """Cancels all individual output processes for this job."""
        self.is_cancelled = True # Mark the main job as cancelled
        for output_cfg in self.outputs:
            output_cfg.status = "cancelled" # Mark individual output
            if output_cfg.process and output_cfg.process.poll() is None:
                try:
                    output_cfg.process.terminate()
                    output_cfg.process.wait(timeout=2) # Short wait
                except subprocess.TimeoutExpired:
                    output_cfg.process.kill()
                except Exception:
                    pass # Ignore other errors during cancellation
            output_cfg.process = None

    def pause_all_outputs(self):
        """Pauses all running individual output processes."""
        if self.is_cancelled: return
        paused_any = False
        for output_cfg in self.outputs:
            if output_cfg.status == "running" and output_cfg.process and output_cfg.process.poll() is None and not output_cfg.is_paused:
                try:
                    import signal
                    output_cfg.process.send_signal(signal.SIGSTOP)
                    output_cfg.is_paused = True
                    output_cfg.status = "paused"
                    paused_any = True
                except Exception:
                    pass # log this?
        return paused_any

    def resume_all_outputs(self):
        """Resumes all paused individual output processes."""
        if self.is_cancelled: return
        resumed_any = False
        for output_cfg in self.outputs:
            if output_cfg.status == "paused" and output_cfg.process and output_cfg.process.poll() is None and output_cfg.is_paused:
                try:
                    import signal
                    output_cfg.process.send_signal(signal.SIGCONT)
                    output_cfg.is_paused = False
                    output_cfg.status = "running" # Set back to running
                    resumed_any = True
                except Exception:
                    pass # log this?
        return resumed_any

    def __str__(self):
        num_outputs = len(self.outputs)
        if num_outputs == 1:
            # For single output, display can be similar to before
            # return f"EncodeJob({self.src_path.name} -> {self.outputs[0].dst_path.name}, {self.outputs[0].status})"
            # For now, always indicate potential for multiple outputs
            return f"EncodeJob({self.src_path.name}, {num_outputs} output(s), {self.get_overall_status()})"

        return f"EncodeJob({self.src_path.name}, {num_outputs} outputs, {self.get_overall_status()})"

    def get_raw_ffprobe_info(self) -> Optional[Dict[str, Any]]:
        """
        Retourne les informations brutes de ffprobe en JSON, en utilisant un cache.
        """
        if self._ffprobe_json_cache is not None:
            return self._ffprobe_json_cache

        if not self.src_path.exists():
            return None

        try:
            result = subprocess.run([
                'ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams',
                str(self.src_path)
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                self._ffprobe_json_cache = {"error": f"ffprobe failed with code {result.returncode}", "stderr": result.stderr}
                return self._ffprobe_json_cache
            
            self._ffprobe_json_cache = json.loads(result.stdout)
            return self._ffprobe_json_cache

        except (subprocess.TimeoutExpired, subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError) as e:
            self._ffprobe_json_cache = {"error": str(e)}
            return self._ffprobe_json_cache
    
    def get_media_info(self) -> Optional[Dict[str, Any]]:
        """Récupère les informations formatées du fichier média source."""
        if self._media_info_cache is not None:
            return self._media_info_cache

        ffprobe_data = self.get_raw_ffprobe_info()
        
        if not ffprobe_data or "error" in ffprobe_data:
            # Fallback vers les informations basiques si ffprobe échoue
            if self.src_path.exists():
                return {
                    'filename': self.src_path.name,
                    'size': self._format_file_size(self.src_path.stat().st_size),
                    'error': ffprobe_data.get("error", "ffprobe not available or failed") if ffprobe_data else "ffprobe failed"
                }
            return None

        media_info = {}
        # Informations générales du fichier
        if 'format' in ffprobe_data:
            fmt = ffprobe_data['format']
            media_info['format'] = fmt.get('format_name', 'Inconnu')
            media_info['duration'] = self._format_duration(fmt.get('duration'))
            media_info['size'] = self._format_file_size(fmt.get('size'))
            media_info['bitrate'] = self._format_bitrate(fmt.get('bit_rate'))
        
        # Informations des streams
        if 'streams' in ffprobe_data:
            streams = ffprobe_data['streams']
            video_streams = [s for s in streams if s.get('codec_type') == 'video']
            audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
            
            if video_streams:
                v = video_streams[0]  # Premier stream vidéo
                media_info['video_codec'] = v.get('codec_name', 'Inconnu')
                media_info['resolution'] = f"{v.get('width', '?')}x{v.get('height', '?')}"
                media_info['fps'] = self._format_fps(v.get('r_frame_rate'))
                media_info['pixel_format'] = v.get('pix_fmt', 'Inconnu')
            
            if audio_streams:
                a = audio_streams[0]  # Premier stream audio
                media_info['audio_codec'] = a.get('codec_name', 'Inconnu')
                media_info['audio_channels'] = a.get('channels', 'Inconnu')
                media_info['sample_rate'] = self._format_sample_rate(a.get('sample_rate'))
            
            media_info['nb_streams'] = len(streams)
            media_info['video_streams'] = len(video_streams)
            media_info['audio_streams'] = len(audio_streams)
        
        self._media_info_cache = media_info
        return media_info
    
    def _format_duration(self, duration_str: Optional[str]) -> str:
        """Formate la durée en format lisible."""
        if not duration_str:
            return "Inconnue"
        try:
            seconds = float(duration_str)
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            if hours > 0:
                return f"{hours:02d}:{minutes:02d}:{secs:02d}"
            else:
                return f"{minutes:02d}:{secs:02d}"
        except ValueError:
            return "Inconnue"
    
    def _format_file_size(self, size_bytes) -> str:
        """Formate la taille du fichier en format lisible."""
        if not size_bytes:
            return "Inconnue"
        try:
            size = int(size_bytes)
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if size < 1024:
                    return f"{size:.1f} {unit}"
                size /= 1024
            return f"{size:.1f} PB"
        except (ValueError, TypeError):
            return "Inconnue"
    
    def _format_bitrate(self, bitrate_str: Optional[str]) -> str:
        """Formate le bitrate en format lisible."""
        if not bitrate_str:
            return "Inconnu"
        try:
            bitrate = int(bitrate_str)
            if bitrate >= 1000000:
                return f"{bitrate / 1000000:.1f} Mbps"
            elif bitrate >= 1000:
                return f"{bitrate / 1000:.0f} kbps"
            else:
                return f"{bitrate} bps"
        except ValueError:
            return "Inconnu"
    
    def _format_fps(self, fps_str: Optional[str]) -> str:
        """Formate les FPS en format lisible."""
        if not fps_str:
            return "Inconnu"
        try:
            if '/' in fps_str:
                num, den = fps_str.split('/')
                fps = float(num) / float(den)
            else:
                fps = float(fps_str)
            return f"{fps:.2f} fps"
        except (ValueError, ZeroDivisionError):
            return "Inconnu"
    
    def _format_sample_rate(self, sample_rate: Optional[str]) -> str:
        """Formate le sample rate en format lisible."""
        if not sample_rate:
            return "Inconnu"
        try:
            sr = int(sample_rate)
            if sr >= 1000:
                return f"{sr / 1000:.1f} kHz"
            else:
                return f"{sr} Hz"
        except ValueError:
            return "Inconnu"

    # === Backward-compatibility helpers ===
    # Many parts of the GUI still expect single-output attributes on EncodeJob.
    # To avoid crashing while the refactor is underway, expose shim properties
    # that transparently map to the first OutputConfig in self.outputs.
    # Les commentaires sont en français, conformément aux consignes.

    #this part expose un accès rapide au premier OutputConfig
    @property
    def _first_output(self) -> Optional[OutputConfig]:
        return self.outputs[0] if self.outputs else None

    #this part gère l'attribut encoder historique
    @property
    def encoder(self) -> str:
        return self._first_output.encoder if self._first_output else ""

    @encoder.setter
    def encoder(self, value: str):
        if self._first_output:
            self._first_output.encoder = value

    #this part gère la qualité générique
    @property
    def quality(self) -> str:
        return self._first_output.quality if self._first_output else ""

    @quality.setter
    def quality(self, value: str):
        if self._first_output:
            self._first_output.quality = value

    #this part gère la valeur CQ/CRF spécifique
    @property
    def cq_value(self) -> str:
        return self._first_output.cq_value if self._first_output else ""

    @cq_value.setter
    def cq_value(self, value: str):
        if self._first_output:
            self._first_output.cq_value = value

    #this part gère le bitrate
    @property
    def bitrate(self) -> str:
        return self._first_output.bitrate if self._first_output else ""

    @bitrate.setter
    def bitrate(self, value: str):
        if self._first_output:
            self._first_output.bitrate = value

    #this part gère le preset
    @property
    def preset(self) -> str:
        return self._first_output.preset if self._first_output else ""

    @preset.setter
    def preset(self, value: str):
        if self._first_output:
            self._first_output.preset = value

    #this part gère les flags personnalisés
    @property
    def custom_flags(self) -> str:
        return self._first_output.custom_flags if self._first_output else ""

    @custom_flags.setter
    def custom_flags(self, value: str):
        if self._first_output:
            self._first_output.custom_flags = value

    #this part gère le mode vidéo/bitrate
    @property
    def video_mode(self) -> str:
        return self._first_output.video_mode if self._first_output else "quality"

    @video_mode.setter
    def video_mode(self, value: str):
        if self._first_output:
            self._first_output.video_mode = value

    #this part expose le dict des filtres
    @property
    def filters(self):
        return self._first_output.filters if self._first_output else {}

    # Les setters pour les dicts modifient directement la référence existante

    #this part gère les sous-titres, trim, audio config
    @property
    def subtitle_config(self):
        return self._first_output.subtitle_config if self._first_output else {}

    @property
    def trim_config(self):
        return self._first_output.trim_config if self._first_output else {}

    @property
    def audio_config(self):
        return self._first_output.audio_config if self._first_output else {}

    #this part gère le mode multipass (bitrate seulement)
    @property
    def multipass(self) -> bool:
        return self._first_output.multipass if self._first_output else False

    @multipass.setter
    def multipass(self, value: bool):
        if self._first_output:
            self._first_output.multipass = value


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
