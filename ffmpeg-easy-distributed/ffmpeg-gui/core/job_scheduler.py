import asyncio
import logging
from typing import List, Dict, Optional, Any, Callable
import uuid

from shared.messages import JobConfiguration, JobProgress, JobResult, ServerInfo, JobStatus
from core.distributed_client import DistributedClient
from core.capability_matcher import CapabilityMatcher

class JobScheduler:
    """Planifie et distribue les jobs d'encodage aux serveurs disponibles."""
    
    def __init__(self, distributed_client: DistributedClient, capability_matcher: CapabilityMatcher):
        self.distributed_client = distributed_client
        self.capability_matcher = capability_matcher
        self.job_queue: asyncio.Queue[JobConfiguration] = asyncio.Queue()
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

    async def add_job(self, job_config: JobConfiguration,
                      progress_callback: Callable[[JobProgress], Any],
                      completion_callback: Callable[[JobResult], Any]):
        """Ajoute un job à la file d'attente pour traitement."""
        job_config.job_id = str(uuid.uuid4()) # Assigner un ID unique si non déjà fait
        await self.job_queue.put(job_config)
        self.active_jobs[job_config.job_id] = job_config
        self.job_status_callbacks[job_config.job_id] = progress_callback
        self.job_completion_callbacks[job_config.job_id] = completion_callback
        self.logger.info(f"Job {job_config.job_id} ajouté à la file d'attente.")

    async def _schedule_jobs_loop(self):
        """Boucle principale de planification des jobs."""
        while True:
            try:
                job = await self.job_queue.get()
                self.logger.info(f"Tentative de planification du job: {job.job_id}")
                
                # Trouver le meilleur serveur
                best_servers = self.capability_matcher.find_best_servers(
                    job, self.distributed_client.get_connected_servers()
                )

                if not best_servers:
                    self.logger.warning(f"Aucun serveur compatible trouvé pour le job {job.job_id}. Remise en file d'attente.")
                    await self.job_queue.put(job) # Remettre en file d'attente
                    await asyncio.sleep(5) # Attendre avant de réessayer
                    continue

                # Pour l'instant, prendre le premier serveur recommandé
                selected_server_match = best_servers[0]
                selected_server_id = selected_server_match.server_id

                self.logger.info(f"Job {job.job_id} assigné au serveur {selected_server_id}")
                
                # Envoyer le job au serveur
                success = await self.distributed_client.send_job_to_server(
                    selected_server_id, job,
                    self._get_job_progress_callback(job.job_id),
                    self._get_job_completion_callback(job.job_id)
                )

                if not success:
                    self.logger.error(f"Échec de l'envoi du job {job.job_id} au serveur {selected_server_id}. Remise en file d'attente.")
                    await self.job_queue.put(job) # Remettre en file d'attente
                    await asyncio.sleep(5) # Attendre avant de réessayer

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"Erreur dans la boucle de planification: {e}")
                await asyncio.sleep(5) # Attendre avant de réessayer

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
        """Annule un job en cours ou en attente."""
        # TODO: Implémenter l'annulation côté serveur via distributed_client
        if job_id in self.active_jobs:
            self.logger.info(f"Annulation du job {job_id} demandée.")
            # Si le job est en cours, envoyer un message d'annulation au serveur
            # Sinon, le retirer de la file d'attente
            # Pour l'instant, juste le retirer des listes locales
            self.active_jobs.pop(job_id, None)
            self.job_status_callbacks.pop(job_id, None)
            self.job_completion_callbacks.pop(job_id, None)
            # Si le job est dans la queue, il faut le retirer. C'est plus complexe avec asyncio.Queue.
            # Une solution serait de vider la queue et de remettre les jobs non annulés.
            self.logger.warning(f"Annulation de job {job_id} non implémentée côté serveur/queue.")

    def register_progress_callback(self, callback: Callable[[JobProgress], Any]):
        """Enregistre un callback global pour le progrès des jobs."""
        self.global_progress_callback = callback

    def register_completion_callback(self, callback: Callable[[JobResult], Any]):
        """Enregistre un callback global pour la complétion des jobs."""
        self.global_completion_callback = callback

    def register_all_jobs_finished_callback(self, callback: Callable[[], Any]):
        """Enregistre un callback appelé quand tous les jobs sont terminés."""
        self.all_jobs_finished_callback = callback

