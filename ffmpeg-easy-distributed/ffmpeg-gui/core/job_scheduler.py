import asyncio
import logging
import time
from typing import List, Dict, Optional, Any, Callable, Tuple
import uuid

from shared.messages import JobConfiguration, JobProgress, JobResult, ServerInfo, JobStatus
from core.distributed_client import DistributedClient
from core.capability_matcher import CapabilityMatcher
from core.local_server import LocalServer

class JobScheduler:
    """Planifie et distribue les jobs d'encodage aux serveurs disponibles."""
    
    def __init__(self, distributed_client: DistributedClient, capability_matcher: CapabilityMatcher):
        self.distributed_client = distributed_client
        self.capability_matcher = capability_matcher
        self.local_server = LocalServer()  # Serveur local intégré
        #file prioritaire (priority, timestamp, JobConfiguration)
        self.job_queue: asyncio.PriorityQueue[tuple[int, float, JobConfiguration]] = asyncio.PriorityQueue()
        self.active_jobs: Dict[str, JobConfiguration] = {}
        self.job_status_callbacks: Dict[str, Callable[[JobProgress], Any]] = {}
        self.job_completion_callbacks: Dict[str, Callable[[JobResult], Any]] = {}
        self.logger = logging.getLogger(__name__)
        self._scheduler_task = None
        
        # Callbacks globaux pour l'interface
        self.global_progress_callback: Optional[Callable[[JobProgress], Any]] = None
        self.global_completion_callback: Optional[Callable[[JobResult], Any]] = None
        self.all_jobs_finished_callback: Optional[Callable[[], Any]] = None

    async def start_scheduler(self):
        """Démarre le planificateur de jobs."""
        self.logger.info("Démarrage du planificateur de jobs...")
        if not self._scheduler_task or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._schedule_jobs_loop())

    async def stop_scheduler(self):
        """Arrête le planificateur de jobs."""
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                self.logger.info("Planificateur de jobs arrêté.")
        
        # Nettoyer le serveur local
        self.local_server.cleanup()

    async def add_job(self, job_config: JobConfiguration,
                      progress_callback: Callable[[JobProgress], Any],
                      completion_callback: Callable[[JobResult], Any]):
        """Ajoute un job à la file d'attente pour traitement."""
        job_config.job_id = str(uuid.uuid4()) # Assigner un ID unique si non déjà fait
        #utiliser l'heure pour conserver l'ordre FIFO parmi même priorité
        await self.job_queue.put((job_config.priority, time.time(), job_config))
        self.active_jobs[job_config.job_id] = job_config
        self.job_status_callbacks[job_config.job_id] = progress_callback
        self.job_completion_callbacks[job_config.job_id] = completion_callback
        self.logger.info(f"Job {job_config.job_id} ajouté à la file d'attente.")

    async def _schedule_jobs_loop(self):
        """Boucle principale de planification des jobs."""
        while True:
            try:
                _priority, _ts, job = await self.job_queue.get()
                self.logger.info(f"Tentative de planification du job: {job.job_id}")
                
                # Trouver le meilleur serveur distant
                connected_servers = self.distributed_client.get_connected_servers()
                best_servers = self.capability_matcher.find_best_servers(job, connected_servers) if connected_servers else []

                # Assigner le job et notifier l'UI
                assigned_server_id = None
                if best_servers and connected_servers:
                    assigned_server_id = best_servers[0].server_id
                else:
                    assigned_server_id = self.local_server.get_server_info().server_id
                
                job.assigned_to = assigned_server_id
                job.status = JobStatus.ASSIGNED
                progress_callback = self._get_job_progress_callback(job.job_id)
                await progress_callback(JobProgress(job_id=job.job_id, progress=0, speed="N/A", current_frame=0, total_frames=None, fps=0.0, bitrate=None, eta=None, server_id="local"))

                if best_servers and connected_servers:
                    # Utiliser un serveur distant
                    selected_server_id = best_servers[0].server_id
                    self.logger.info(f"Job {job.job_id} assigné au serveur distant {selected_server_id}")
                    
                    success = await self.distributed_client.send_job_to_server(
                        selected_server_id, job,
                        self._get_job_progress_callback(job.job_id),
                        self._get_job_completion_callback(job.job_id)
                    )

                    if not success:
                        self.logger.warning(f"Échec de l'envoi au serveur distant {selected_server_id}. Fallback vers serveur local.")
                        # Fallback vers le serveur local
                        await self._process_job_locally(job)
                else:
                    # Aucun serveur distant disponible, utiliser le serveur local
                    self.logger.info(f"Aucun serveur distant disponible. Job {job.job_id} traité localement.")
                    await self._process_job_locally(job)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"Erreur dans la boucle de planification: {e}")
                await asyncio.sleep(5) # Attendre avant de réessayer

    async def _process_job_locally(self, job: JobConfiguration):
        """Traite un job localement avec le serveur intégré"""
        try:
            if not self.local_server.is_available():
                self.logger.warning(f"Serveur local occupé. Job {job.job_id} remis en file d'attente.")
                await self.job_queue.put((job.priority, time.time(), job))
                await asyncio.sleep(2)
                return

            success = await self.local_server.process_job(
                job,
                self._get_job_progress_callback(job.job_id),
                self._get_job_completion_callback(job.job_id)
            )
            
            if not success:
                self.logger.error(f"Échec du traitement local du job {job.job_id}")
        except Exception as e:
            self.logger.error(f"Erreur traitement local job {job.job_id}: {e}")
            # Créer un résultat d'erreur
            error_result = JobResult(
                job_id=job.job_id,
                status=JobStatus.FAILED,
                output_file="",
                file_size=0,
                duration=0.0,
                average_fps=0.0,
                error_message=str(e),
                server_id=self.local_server.server_id,
                completed_at=time.time()
            )
            completion_callback = self._get_job_completion_callback(job.job_id)
            await completion_callback(error_result)

    def _get_job_progress_callback(self, job_id: str) -> Callable[[JobProgress], Any]:
        """Retourne un callback de progression spécifique au job."""
        async def callback(progress: JobProgress):
            if job_id in self.job_status_callbacks:
                await self.job_status_callbacks[job_id](progress)
            if self.global_progress_callback:
                self.global_progress_callback(progress)
        return callback

    def _get_job_completion_callback(self, job_id: str) -> Callable[[JobResult], Any]:
        """Retourne un callback de complétion spécifique au job."""
        async def callback(result: JobResult):
            if job_id in self.job_completion_callbacks:
                await self.job_completion_callbacks[job_id](result)
            if self.global_completion_callback:
                self.global_completion_callback(result)
            self.active_jobs.pop(job_id, None)
            self.job_status_callbacks.pop(job_id, None)
            self.job_completion_callbacks.pop(job_id, None)
            
            # Vérifier si tous les jobs sont terminés
            if not self.active_jobs and self.all_jobs_finished_callback:
                self.all_jobs_finished_callback()
        return callback

    def get_active_jobs(self) -> Dict[str, JobConfiguration]:
        """Retourne les jobs actuellement actifs (en file d'attente ou en cours)."""
        return self.active_jobs

    async def cancel_job(self, job_id: str):
        """Annule un job. Gère les cas distant et local."""
        self.logger.info(f"Demande d'annulation pour le job {job_id}")

        # Vérifier si le job est actif (en cours ou déjà assigné)
        job_config = self.active_jobs.get(job_id)

        if job_config:
            assigned_server = getattr(job_config, 'assigned_to', None)

            if assigned_server == self.local_server.server_id:
                # Annulation locale
                success = await self.local_server.cancel_job(job_id)
                self.logger.info(f"Annulation locale du job {job_id} -> {success}")
            elif assigned_server:
                # Annulation sur serveur distant
                success = await self.distributed_client.cancel_job_on_server(assigned_server, job_id)
                self.logger.info(f"Annulation distante du job {job_id} -> {success}")
            else:
                success = False
                self.logger.warning(f"Job {job_id} n'a pas de serveur assigné")

            # Nettoyer les structures internes quel que soit le résultat
            self.active_jobs.pop(job_id, None)
            self.job_status_callbacks.pop(job_id, None)
            self.job_completion_callbacks.pop(job_id, None)

        else:
            # Job peut être encore dans la queue -> besoin de le retirer
            new_queue = asyncio.PriorityQueue()
            while not self.job_queue.empty():
                pri, ts, queued_job = await self.job_queue.get()
                if queued_job.job_id != job_id:
                    await new_queue.put((pri, ts, queued_job))
            self.job_queue = new_queue
            self.logger.info(f"Job {job_id} retiré de la file d'attente si présent.")

    async def pause_job(self, job_id: str):
        """Met en pause un job actif (local ou distant)"""
        self.logger.info(f"Demande de pause pour le job {job_id}")
        job_config = self.active_jobs.get(job_id)
        if not job_config:
            self.logger.warning(f"Job {job_id} introuvable ou déjà terminé")
            return False
        assigned_server = getattr(job_config, 'assigned_to', None)
        success = False
        if assigned_server == self.local_server.server_id:
            success = await self.local_server.pause_job(job_id)
        elif assigned_server:
            success = await self.distributed_client.pause_job_on_server(assigned_server, job_id)
        if success:
            job_config.status = JobStatus.PAUSED
        return success

    async def resume_job(self, job_id: str):
        """Reprend un job précédemment mis en pause (local ou distant)"""
        self.logger.info(f"Demande de reprise pour le job {job_id}")
        job_config = self.active_jobs.get(job_id)
        if not job_config:
            self.logger.warning(f"Job {job_id} introuvable ou déjà terminé")
            return False
        assigned_server = getattr(job_config, 'assigned_to', None)
        success = False
        if assigned_server == self.local_server.server_id:
            success = await self.local_server.resume_job(job_id)
        elif assigned_server:
            success = await self.distributed_client.resume_job_on_server(assigned_server, job_id)
        if success:
            job_config.status = JobStatus.RUNNING
        return success

    def register_progress_callback(self, callback: Callable[[JobProgress], Any]):
        """Enregistre un callback global pour le progrès des jobs."""
        self.global_progress_callback = callback

    def register_completion_callback(self, callback: Callable[[JobResult], Any]):
        """Enregistre un callback global pour la complétion des jobs."""
        self.global_completion_callback = callback

    def register_all_jobs_finished_callback(self, callback: Callable[[], Any]):
        """Enregistre un callback appelé quand tous les jobs sont terminés."""
        self.all_jobs_finished_callback = callback

    def get_local_server_info(self) -> ServerInfo:
        """Retourne les informations du serveur local pour l'interface"""
        return self.local_server.get_server_info()

