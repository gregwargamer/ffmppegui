import queue
import subprocess
import threading
import ffmpeg

from core.encode_job import EncodeJob, OutputConfig


def build_ffmpeg_stream(job: EncodeJob):
    """Construit le stream FFmpeg pour un job donné avec filtres"""
    # Si remux, on ne fait que copier les flux
    if getattr(job, 'encoder', None) == 'remux':
        input_stream = ffmpeg.input(str(job.src_path))
        output_stream = ffmpeg.output(input_stream, str(job.dst_path), vcodec='copy', acodec='copy')
        return output_stream
    
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
        import threading as _th
        self._stop_event = _th.Event()
        self.progress_callback = progress_callback
        self.log_callback = log_callback

    def start(self):
        """Démarre les threads workers"""
        if not self.running:
            self.running = True
            self._stop_event.clear()
            for i in range(self.max_workers):
                thread = threading.Thread(target=self._worker, daemon=True)
                thread.start()
                self.threads.append(thread)

    def stop(self):
        """Arrête les workers proprement"""
        self.running = False
        self._stop_event.set()
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
                item = self.job_queue.get(timeout=1.0) # item is now ((parent_job, output_cfg), command_builder)
                if item is None:  # Sentinelle pour arrêter
                    break

                (parent_job, output_cfg_for_task), command_builder = item
                self._run_job(parent_job, output_cfg_for_task, command_builder)
                self.job_queue.task_done()
            except queue.Empty:
                continue

    def _run_job(self, parent_job: EncodeJob, output_cfg: OutputConfig, command_builder=None):
        try:
            # Vérifier si le job parent a été annulé avant de commencer
            if parent_job.is_cancelled: # Check parent job's cancellation flag
                output_cfg.status = "cancelled" # Mark this specific output as cancelled too
                if self.progress_callback: self.progress_callback(parent_job, output_cfg)
                return

            # Log du début du job (specific to this output_cfg)
            log_context_job = parent_job # For overall job context in logs
            if self.log_callback:
                self.log_callback(log_context_job, f"Starting output '{output_cfg.name}': {parent_job.src_path.name} -> {output_cfg.dst_path.name}", "info", output_cfg.id)

            # Fetch duration from parent_job if not already fetched
            if parent_job.duration is None:
                try:
                    rr = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(parent_job.src_path)],
                                       capture_output=True, text=True, timeout=30)
                    if rr.returncode == 0 and rr.stdout.strip():
                        duration_str = rr.stdout.strip()
                        if duration_str != "N/A":
                            parent_job.duration = float(duration_str)
                            if self.log_callback:
                                self.log_callback(log_context_job, f"Source duration detected: {parent_job.duration:.2f}s", "info", output_cfg.id)
                except (subprocess.TimeoutExpired, ValueError) as e:
                    if self.log_callback:
                        self.log_callback(log_context_job, f"Could not detect source duration: {e}", "warning", output_cfg.id)

            # Construire la commande FFmpeg using the command_builder, passing parent_job and output_cfg
            if command_builder:
                args = command_builder(parent_job, output_cfg) + ["-progress", "-", "-nostats"]
            else:
                # Fallback or error if no command_builder, as build_ffmpeg_stream is not adapted for OutputConfig directly
                # For now, assume command_builder is always provided from MainWindow
                if self.log_callback:
                    self.log_callback(log_context_job, "Error: No command_builder provided for job.", "error", output_cfg.id)
                output_cfg.status = "error"
                if self.progress_callback: self.progress_callback(parent_job, output_cfg)
                return
            
            if self.log_callback:
                self.log_callback(log_context_job, f"FFmpeg command for '{output_cfg.name}': {' '.join(args)}", "info", output_cfg.id)
            
            output_cfg.status = "running"
            if self.progress_callback:
                self.progress_callback(parent_job, output_cfg) # Notify with parent_job and specific output_cfg
            
            # Lancer le processus FFmpeg, store process on output_cfg
            output_cfg.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
            
            # Créer un thread pour lire stderr
            stderr_thread = threading.Thread(target=self._read_stderr, args=(parent_job, output_cfg,), daemon=True)
            stderr_thread.start()
            
            # Lire les informations de progrès
            while True:
                if parent_job.is_cancelled: # Check parent job's cancellation flag
                    if output_cfg.process and output_cfg.process.poll() is None:
                        try: output_cfg.process.terminate()
                        except: pass
                    output_cfg.status = "cancelled"
                    break
                    
                line_bytes = output_cfg.process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode('utf-8').strip()
                if line.startswith("out_time_ms="):
                    try:
                        time_str = line.split("=")[1].strip()
                        if time_str != "N/A":
                            ms = int(time_str)
                            if parent_job.duration and parent_job.duration > 0: # Use parent_job.duration
                                output_cfg.progress = min(ms / 1e6 / parent_job.duration, 1.0)
                                if self.progress_callback:
                                    self.progress_callback(parent_job, output_cfg)
                    except (ValueError, IndexError):
                        pass
                elif line.startswith("progress=") and line.endswith("end"):
                    output_cfg.progress = 1.0
                    if self.progress_callback:
                        self.progress_callback(parent_job, output_cfg)
            
            output_cfg.process.wait() # Wait for this specific output's process
            
            if parent_job.is_cancelled: # Re-check, process might have finished due to cancellation
                 output_cfg.status = "cancelled"
            elif output_cfg.process.returncode == 0:
                output_cfg.status = "done"
                output_cfg.progress = 1.0
                if self.log_callback:
                    self.log_callback(log_context_job, f"Output '{output_cfg.name}' completed successfully.", "info", output_cfg.id)
            else:
                output_cfg.status = "error"
                if self.log_callback:
                    self.log_callback(log_context_job, f"Output '{output_cfg.name}' failed with return code {output_cfg.process.returncode}.", "error", output_cfg.id)
                
        except Exception as e:
            if not parent_job.is_cancelled: # Only set to error if not part of a general cancel
                output_cfg.status = "error"
            error_msg = f"Encoding error for output '{output_cfg.name}': {e}"
            print(error_msg) # Keep console print for critical errors
            if self.log_callback:
                self.log_callback(parent_job, error_msg, "error", output_cfg.id) # Log with parent_job context
        finally:
            output_cfg.process = None # Clear process handle
            if self.progress_callback:
                self.progress_callback(parent_job, output_cfg) # Final progress update
    
    def _read_stderr(self, parent_job: EncodeJob, output_cfg: OutputConfig):
        """Lit la sortie stderr de FFmpeg dans un thread séparé for a specific output_cfg."""
        if not output_cfg.process or not output_cfg.process.stderr:
            return
            
        try:
            while True:
                line_bytes = output_cfg.process.stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode('utf-8', errors='ignore').strip()
                if line and self.log_callback:
                    log_type = "info"
                    if "error" in line.lower() or "failed" in line.lower(): log_type = "error"
                    elif "warning" in line.lower(): log_type = "warning"
                    elif "frame=" in line or "fps=" in line: log_type = "progress" # Could be too noisy
                    
                    self.log_callback(parent_job, f"[{output_cfg.name}]: {line}", log_type, output_cfg.id)
        except Exception as e:
            if self.log_callback:
                self.log_callback(parent_job, f"Error reading stderr for '{output_cfg.name}': {e}", "error", output_cfg.id)

    def submit(self, job_item: tuple, command_builder=None): # job_item is now (parent_job, output_cfg)
        self.job_queue.put((job_item, command_builder))
