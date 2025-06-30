import asyncio
import logging
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional

from shared.messages import ServerInfo, ServerCapabilities, JobConfiguration, JobProgress, JobResult, JobStatus, ServerStatus
from core.hardware_detector import detect_capabilities

class LocalServer:
    """Serveur local intégré pour l'encodage quand aucun serveur distant n'est disponible."""
    
    def __init__(self):
        self.server_id = "local-server"
        self.logger = logging.getLogger(__name__)
        self.temp_dir = Path(tempfile.gettempdir()) / "ffmpeg_easy_local"
        self.temp_dir.mkdir(exist_ok=True)
        self.active_jobs: Dict[str, subprocess.Popen] = {}
        self.capabilities: Optional[ServerCapabilities] = None
        self._initialize_capabilities()

    def _initialize_capabilities(self):
        """Initialise les capacités du serveur local"""
        try:
            detected_capabilities = detect_capabilities()
            
            self.capabilities = ServerCapabilities(
                hostname="localhost",
                os="macOS",  # Peut être détecté dynamiquement
                cpu_cores=detected_capabilities.cpu_cores,
                memory_gb=detected_capabilities.memory_gb,
                disk_space_gb=detected_capabilities.disk_space_gb,
                software_encoders=detected_capabilities.software_encoders,
                hardware_encoders=detected_capabilities.hardware_encoders,
                estimated_performance=1.0,
                current_load=0.0,
                max_resolution=detected_capabilities.max_resolution,
                supported_formats=detected_capabilities.supported_formats,
                max_file_size_gb=100.0  # Limite par défaut
            )
            
            self.logger.info(f"🔧 Serveur local initialisé avec {len(self.capabilities.software_encoders)} encodeurs")
        except Exception as e:
            self.logger.error(f"❌ Erreur initialisation serveur local: {e}")
            # Fallback avec capacités minimales
            self.capabilities = ServerCapabilities(
                hostname="localhost",
                os="Unknown",
                cpu_cores=2,
                memory_gb=4.0,
                disk_space_gb=100.0,
                software_encoders=["libx264", "libx265", "aac"],
                hardware_encoders={},
                estimated_performance=0.5,
                current_load=0.0,
                max_resolution="1080p",
                supported_formats=["mp4", "mkv", "avi"],
                max_file_size_gb=50.0
            )

    def get_server_info(self) -> ServerInfo:
        """Retourne les informations du serveur local"""
        return ServerInfo(
            server_id=self.server_id,
            name="Serveur Local",
            ip="127.0.0.1",
            port=0,  # Pas de port réseau pour le serveur local
            status=ServerStatus.ONLINE,
            capabilities=self.capabilities,
            max_jobs=2,  # Limite par défaut
            current_jobs=len(self.active_jobs),
            uptime=0.0,  # Pas de tracking uptime pour le serveur local
            last_seen=time.time()
        )

    async def process_job(self, job_config: JobConfiguration, 
                         progress_callback, completion_callback) -> bool:
        """Traite un job d'encodage localement"""
        job_id = job_config.job_id
        self.logger.info(f"🔄 Début traitement local du job: {job_id}")
        
        try:
            # Construire la commande FFmpeg
            ffmpeg_cmd = self._build_ffmpeg_command(job_config)
            if not ffmpeg_cmd:
                raise ValueError("Impossible de construire la commande FFmpeg")
            
            self.logger.info(f"🚀 Commande FFmpeg: {' '.join(ffmpeg_cmd)}")
            
            # Lancer le processus FFmpeg
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            self.active_jobs[job_id] = process
            
            # Surveiller le progrès
            await self._monitor_job_progress(job_id, process, job_config, progress_callback)
            
            # Attendre la fin du processus
            await asyncio.create_task(self._wait_for_process(process))
            
            # Nettoyer
            if job_id in self.active_jobs:
                del self.active_jobs[job_id]
            
            # Créer le résultat
            if process.returncode == 0:
                result = JobResult(
                    job_id=job_id,
                    status=JobStatus.COMPLETED,
                    output_file=job_config.output_file,
                    file_size=0,  # TODO: Calculer la taille réelle
                    duration=0.0,
                    average_fps=0.0,
                    error_message=None,
                    server_id=self.server_id,
                    completed_at=time.time()
                )
                self.logger.info(f"✅ Job local {job_id} terminé avec succès")
            else:
                stderr_content = process.stderr.read() if process.stderr else "Erreur inconnue"
                result = JobResult(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    output_file="",
                    file_size=0,
                    duration=0.0,
                    average_fps=0.0,
                    error_message=f"FFmpeg a échoué avec le code {process.returncode}: {stderr_content}",
                    server_id=self.server_id,
                    completed_at=time.time()
                )
                self.logger.error(f"❌ Échec du job local {job_id}: {result.error_message}")
            
            await completion_callback(result)
            return process.returncode == 0
            
        except Exception as e:
            self.logger.error(f"❌ Erreur traitement job local {job_id}: {e}")
            
            # Nettoyer en cas d'erreur
            if job_id in self.active_jobs:
                try:
                    self.active_jobs[job_id].terminate()
                except:
                    pass
                del self.active_jobs[job_id]
            
            result = JobResult(
                job_id=job_id,
                status=JobStatus.FAILED,
                output_file="",
                file_size=0,
                duration=0.0,
                average_fps=0.0,
                error_message=str(e),
                server_id=self.server_id,
                completed_at=time.time()
            )
            
            await completion_callback(result)
            return False

    def _build_ffmpeg_command(self, job_config: JobConfiguration) -> Optional[list[str]]:
        """Construit la commande FFmpeg à partir de la configuration du job"""
        try:
            cmd = ["ffmpeg", "-i", job_config.input_file]
            cmd.extend(job_config.ffmpeg_args)
            cmd.append(job_config.output_file)
            return cmd
        except Exception as e:
            self.logger.error(f"❌ Erreur construction commande FFmpeg: {e}")
            return None

    async def _monitor_job_progress(self, job_id: str, process: subprocess.Popen, 
                                  job_config: JobConfiguration, progress_callback):
        """Surveille le progrès d'un job d'encodage"""
        try:
            while process.poll() is None:
                # Simuler le progrès pour l'instant (à améliorer avec parsing stderr)
                await asyncio.sleep(1)
                
                progress = JobProgress(
                    job_id=job_id,
                    progress=50.0,  # Valeur fixe pour l'instant
                    current_frame=None,
                    total_frames=None,
                    fps=None,
                    bitrate=None,
                    speed=None,
                    eta=None,
                    server_id=self.server_id
                )
                
                await progress_callback(progress)
                
        except Exception as e:
            self.logger.error(f"❌ Erreur surveillance progrès job {job_id}: {e}")

    async def _wait_for_process(self, process: subprocess.Popen):
        """Attend qu'un processus se termine de manière asynchrone"""
        while process.poll() is None:
            await asyncio.sleep(0.1)

    async def cancel_job(self, job_id: str) -> bool:
        """Annule un job en cours"""
        if job_id in self.active_jobs:
            try:
                process = self.active_jobs[job_id]
                process.terminate()
                await asyncio.sleep(1)
                if process.poll() is None:
                    process.kill()
                del self.active_jobs[job_id]
                self.logger.info(f"🛑 Job local {job_id} annulé")
                return True
            except Exception as e:
                self.logger.error(f"❌ Erreur annulation job {job_id}: {e}")
        return False

    async def pause_job(self, job_id: str) -> bool:
        """Met en pause un job local en envoyant SIGSTOP"""
        if job_id in self.active_jobs:
            process = self.active_jobs[job_id]
            if process.poll() is None:
                try:
                    import signal
                    if hasattr(signal, 'SIGSTOP'):
                        process.send_signal(signal.SIGSTOP)
                    else:
                        import psutil
                        try:
                            psutil.Process(process.pid).suspend()
                        except ImportError:
                            psutil = None
                        if psutil:
                            psutil.Process(process.pid).suspend()
                    self.logger.info(f"⏸️  Job local {job_id} mis en pause")
                    return True
                except Exception as e:
                    self.logger.error(f"Erreur lors de la mise en pause du job {job_id}: {e}")
        return False

    async def resume_job(self, job_id: str) -> bool:
        """Reprend un job local en envoyant SIGCONT"""
        if job_id in self.active_jobs:
            process = self.active_jobs[job_id]
            if process.poll() is None:
                try:
                    import signal
                    if hasattr(signal, 'SIGCONT'):
                        process.send_signal(signal.SIGCONT)
                    else:
                        import psutil
                        try:
                            psutil.Process(process.pid).resume()
                        except ImportError:
                            psutil = None
                        if psutil:
                            psutil.Process(process.pid).resume()
                    self.logger.info(f"▶️  Job local {job_id} repris")
                    return True
                except Exception as e:
                    self.logger.error(f"Erreur lors de la reprise du job {job_id}: {e}")
        return False

    def is_available(self) -> bool:
        """Vérifie si le serveur local est disponible pour de nouveaux jobs"""
        return len(self.active_jobs) < 2  # Limite à 2 jobs simultanés

    def cleanup(self):
        """Nettoie les ressources du serveur local"""
        for job_id, process in list(self.active_jobs.items()):
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
            except:
                try:
                    process.kill()
                except:
                    pass
        self.active_jobs.clear()
        
        # Nettoyer le répertoire temporaire
        try:
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except:
            pass 