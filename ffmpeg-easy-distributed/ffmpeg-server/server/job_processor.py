import asyncio
import logging
import re
import time
from pathlib import Path

from shared.messages import JobConfiguration, JobProgress, JobResult, JobStatus, ServerCapabilities
from server.file_manager import FileManager

class JobProcessor:
    """Traite un job d'encodage FFmpeg"""
    def __init__(self, job_config: JobConfiguration, file_manager: FileManager, capabilities: ServerCapabilities, progress_callback, completion_callback):
        self.job_config = job_config
        self.file_manager = file_manager
        self.capabilities = capabilities
        self.progress_callback = progress_callback
        self.completion_callback = completion_callback
        self.process = None
        self.cancelled = False
        self.logger = logging.getLogger(__name__)

    async def start(self):
        """Démarre le traitement du job"""
        self.logger.info(f"Début traitement job: {self.job_config.job_id}")
        input_path = self.file_manager.temp_dir / f"{self.job_config.job_id}_input"
        output_path = self.file_manager.temp_dir / self.job_config.output_file

        try:
            # In a real implementation, the file would be transferred here.
            # For now, we assume the file is already in the temp directory.
            # You would await self.file_manager.receive_file(job_config.job_id) here.

            ffmpeg_cmd = [
                "ffmpeg", "-i", str(input_path),
            ] + self.job_config.ffmpeg_args + [
                str(output_path)
            ]

            self.logger.info(f"Exécution FFmpeg: {' '.join(ffmpeg_cmd)}")
            self.process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            await self._monitor_progress(self.process.stderr)
            stdout, stderr = await self.process.communicate()

            if self.cancelled:
                status = JobStatus.CANCELLED
                error_message = "Job annulé par l'utilisateur"
            elif self.process.returncode != 0:
                status = JobStatus.FAILED
                error_message = f"FFmpeg a échoué avec le code {self.process.returncode}: {stderr.decode()}"
                self.logger.error(error_message)
            else:
                status = JobStatus.COMPLETED
                error_message = None
                self.logger.info(f"Job {self.job_config.job_id} terminé avec succès.")

            result = JobResult(
                job_id=self.job_config.job_id,
                status=status,
                output_file=str(output_path) if output_path.exists() else "",
                file_size=output_path.stat().st_size if output_path.exists() else 0,
                duration=0.0, # TODO: Parse from ffmpeg output
                average_fps=0.0, # TODO: Parse from ffmpeg output
                error_message=error_message,
                server_id=self.capabilities.hostname,
                completed_at=time.time()
            )
            await self.completion_callback(result)

        except asyncio.CancelledError:
            self.logger.info(f"Job {self.job_config.job_id} a été annulé.")
            result = JobResult(
                job_id=self.job_config.job_id, status=JobStatus.CANCELLED,
                output_file="", file_size=0, duration=0, average_fps=0,
                error_message="Job annulé", server_id=self.capabilities.hostname,
                completed_at=time.time()
            )
            await self.completion_callback(result)
        except Exception as e:
            self.logger.error(f"Erreur inattendue lors du traitement du job {self.job_config.job_id}: {e}")
            result = JobResult(
                job_id=self.job_config.job_id, status=JobStatus.FAILED,
                output_file="", file_size=0, duration=0, average_fps=0,
                error_message=str(e), server_id=self.capabilities.hostname,
                completed_at=time.time()
            )
            await self.completion_callback(result)
        finally:
            if input_path.exists():
                input_path.unlink(missing_ok=True)

    async def cancel(self):
        """Annule le job en cours"""
        self.cancelled = True
        if self.process and self.process.returncode is None:
            self.logger.info(f"Tentative d'arrêt du processus FFmpeg pour job {self.job_config.job_id}")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.logger.warning(f"FFmpeg pour job {self.job_config.job_id} ne s'est pas arrêté, le tuer.")
                self.process.kill()

    async def _monitor_progress(self, stderr_stream):
        """Monitore la sortie d'erreur de FFmpeg pour la progression"""
        total_frames = None
        async for line in stderr_stream:
            line = line.decode().strip()
            if "frame=" in line:
                try:
                    frame_match = re.search(r"frame=\s*(\d+)", line)
                    fps_match = re.search(r"fps=\s*([\d.]+)", line)
                    time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})", line)
                    bitrate_match = re.search(r"bitrate=\s*([\d.]+kbits/s)", line)
                    speed_match = re.search(r"speed=\s*([\d.]+x)", line)

                    current_frame = int(frame_match.group(1)) if frame_match else None
                    fps = float(fps_match.group(1)) if fps_match else None
                    bitrate = bitrate_match.group(1) if bitrate_match else None
                    speed = speed_match.group(1) if speed_match else None
                    
                    progress_percent = 0.0
                    if total_frames and current_frame is not None:
                        progress_percent = (current_frame / total_frames) * 100
                    
                    eta = None
                    if fps and fps > 0 and total_frames and current_frame is not None:
                        remaining_frames = total_frames - current_frame
                        eta = int(remaining_frames / fps)
                    
                    progress_data = JobProgress(
                        job_id=self.job_config.job_id,
                        progress=progress_percent,
                        current_frame=current_frame,
                        total_frames=total_frames,
                        fps=fps,
                        bitrate=bitrate,
                        speed=speed,
                        eta=eta,
                        server_id=self.capabilities.hostname
                    )
                    await self.progress_callback(progress_data)
                except Exception as e:
                    self.logger.warning(f"Erreur parsing progression FFmpeg: {e} dans ligne: {line}")
            if self.cancelled:
                break
