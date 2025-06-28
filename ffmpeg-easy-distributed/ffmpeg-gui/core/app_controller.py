from typing import List, Dict, Any, Optional
from pathlib import Path
import asyncio
import logging

from core.app_state import AppState
from core.job_scheduler import JobScheduler
from core.distributed_client import DistributedClient
from core.server_discovery import ServerDiscovery
from core.encode_job import EncodeJob, OutputConfig
from shared.messages import JobProgress, JobResult, ServerInfo

class AppController:
    """
    Contrôleur principal de l'application FFMpeg Easy Distributed.
    
    Centralise toute la logique business et orchestre les interactions
    entre les différents composants de l'application.
    """
    
    def __init__(self, app_state: AppState, job_scheduler: JobScheduler, 
                 distributed_client: DistributedClient, server_discovery: ServerDiscovery):
        self.state = app_state
        self.job_scheduler = job_scheduler
        self.distributed_client = distributed_client
        self.server_discovery = server_discovery
        self.logger = logging.getLogger(__name__)
        
        # Enregistrer les callbacks pour les événements
        self._setup_callbacks()

    def _setup_callbacks(self):
        """Configure les callbacks pour les événements des composants"""
        # Callbacks pour la découverte de serveurs
        self.server_discovery.register_server_update_callback(self._on_servers_updated)
        
        # Callbacks pour les jobs (à configurer via le job_scheduler)
        # Ces callbacks seront utilisés quand nous démarrerons l'encodage

    def _on_servers_updated(self, servers: List[ServerInfo]):
        """Appelé quand la liste des serveurs est mise à jour"""
        self.logger.debug(f"Mise à jour des serveurs: {len(servers)} serveurs")
        for server in servers:
            self.state.update_server(server)

    # === Gestion des fichiers et dossiers ===
    
    def add_files_to_queue(self, file_paths: List[Path]) -> List[EncodeJob]:
        """
        Ajoute des fichiers à la queue d'encodage.
        
        Returns:
            La liste des jobs créés
        """
        created_jobs = []
        
        for file_path in file_paths:
            if not file_path.exists():
                self.logger.warning(f"Fichier introuvable: {file_path}")
                continue
                
            if not self._is_media_file(file_path):
                self.logger.warning(f"Format de fichier non supporté: {file_path}")
                continue
            
            # Détecter le type de média
            media_type = self._detect_media_type(file_path)
            
            # Créer le job
            job = EncodeJob(src_path=file_path, mode=media_type)
            
            # Appliquer les paramètres globaux
            self.state.apply_global_settings_to_job(job)
            
            # Ajouter au state
            self.state.add_job(job)
            created_jobs.append(job)
            
            self.logger.info(f"Job ajouté: {file_path.name} ({media_type})")
        
        return created_jobs

    def add_folder_to_queue(self, folder_path: Path, recursive: bool = True) -> List[EncodeJob]:
        """
        Ajoute tous les fichiers média d'un dossier à la queue.
        
        Args:
            folder_path: Chemin du dossier
            recursive: Si True, inclut les sous-dossiers
            
        Returns:
            La liste des jobs créés
        """
        if not folder_path.exists() or not folder_path.is_dir():
            self.logger.error(f"Dossier introuvable: {folder_path}")
            return []
        
        file_paths = []
        pattern = "**/*" if recursive else "*"
        
        for file_path in folder_path.glob(pattern):
            if file_path.is_file() and self._is_media_file(file_path):
                file_paths.append(file_path)
        
        self.logger.info(f"Ajout de {len(file_paths)} fichiers depuis {folder_path}")
        return self.add_files_to_queue(file_paths)

    def _is_media_file(self, file_path: Path) -> bool:
        """Vérifie si un fichier est un fichier média supporté"""
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv", ".webm", ".flv", ".m4v", ".3gp"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3", ".wma", ".opus", ".ac3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp", ".gif", ".tga", ".dds"}
        
        return file_path.suffix.lower() in (video_exts | audio_exts | image_exts)

    def _detect_media_type(self, file_path: Path) -> str:
        """Détecte le type de média d'un fichier"""
        ext = file_path.suffix.lower()
        
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv", ".webm", ".flv", ".m4v", ".3gp"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3", ".wma", ".opus", ".ac3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp", ".gif", ".tga", ".dds"}
        
        if ext == ".gif":
            return "gif"
        elif ext in video_exts:
            return "video"
        elif ext in audio_exts:
            return "audio"
        elif ext in image_exts:
            return "image"
        else:
            return "unknown"

    # === Gestion des jobs ===
    
    def remove_jobs(self, job_ids: List[str]):
        """Supprime des jobs de la queue"""
        for job_id in job_ids:
            job = self.state.get_job_by_id(job_id)
            if job:
                if job.status in ["running", "pending"]:
                    self.cancel_job(job_id)
                self.state.remove_job(job_id)
                self.logger.info(f"Job supprimé: {job_id}")

    def clear_queue(self):
        """Vide complètement la queue d'encodage"""
        # Annuler tous les jobs en cours
        running_jobs = self.state.get_jobs_by_status("running")
        for job in running_jobs:
            self.cancel_job(job.job_id)
        
        self.state.clear_jobs()
        self.logger.info("Queue d'encodage vidée")

    def set_global_media_type(self, media_type: str):
        """Définit le type de média global dans l'état de l'application."""
        current_global_type = self.state.global_media_type
        self.state.set_global_media_type(media_type)

        if current_global_type != media_type:
            self.logger.info(f"Type de média global changé pour: {media_type}")

    def duplicate_job(self, job_id: str) -> Optional[EncodeJob]:
        """Duplique un job existant"""
        original_job = self.state.get_job_by_id(job_id)
        if not original_job:
            return None
        
        # Créer une copie du job
        new_job = EncodeJob(src_path=original_job.src_path, mode=original_job.mode)
        new_job.outputs = [output for output in original_job.outputs]  # Copie des outputs
        
        # Modifier le nom de destination pour éviter les conflits
        if new_job.outputs:
            output = new_job.outputs[0]
            stem = output.dst_path.stem
            new_name = f"{stem}_copy"
            output.dst_path = output.dst_path.with_name(f"{new_name}{output.dst_path.suffix}")
        
        self.state.add_job(new_job)
        self.logger.info(f"Job dupliqué: {job_id} -> {new_job.job_id}")
        return new_job

    # === Gestion de l'encodage ===
    
    async def start_encoding(self):
        """Démarre l'encodage de tous les jobs en attente"""
        pending_jobs = self.state.get_jobs_by_status("pending")
        if not pending_jobs:
            self.logger.warning("Aucun job en attente à encoder")
            return
        
        self.state.set_encoding_state(True)
        self.logger.info(f"Démarrage de l'encodage de {len(pending_jobs)} jobs")
        
        # Envoyer les jobs au scheduler
        for job in pending_jobs:
            await self._submit_job_to_scheduler(job)

    async def _submit_job_to_scheduler(self, job: EncodeJob):
        """Soumet un job au planificateur d'encodage"""
        try:
            # Configurer les callbacks pour ce job
            progress_callback = lambda progress: self._on_job_progress(job.job_id, progress)
            completion_callback = lambda result: self._on_job_completion(job.job_id, result)
            
            # Soumettre au scheduler
            success = await self.job_scheduler.submit_job(
                job, progress_callback, completion_callback
            )
            
            if success:
                job.status = "running"
                self.logger.info(f"Job soumis au scheduler: {job.job_id}")
            else:
                job.status = "failed"
                self.logger.error(f"Échec de soumission du job: {job.job_id}")
                
        except Exception as e:
            job.status = "failed"
            self.logger.error(f"Erreur lors de la soumission du job {job.job_id}: {e}")

    def cancel_job(self, job_id: str):
        """Annule un job en cours d'exécution"""
        job = self.state.get_job_by_id(job_id)
        if job:
            # Demander au scheduler d'annuler le job
            asyncio.create_task(self.job_scheduler.cancel_job(job_id))
            job.status = "cancelled"
            self.logger.info(f"Job annulé: {job_id}")

    def pause_job(self, job_id: str):
        """Met en pause un job (si supporté)"""
        job = self.state.get_job_by_id(job_id)
        if job and job.status == "running":
            # Pour l'instant, on change juste le statut
            # L'implémentation complète nécessiterait des changements au niveau du scheduler
            job.status = "paused"
            self.logger.info(f"Job mis en pause: {job_id}")

    def resume_job(self, job_id: str):
        """Reprend un job en pause"""
        job = self.state.get_job_by_id(job_id)
        if job and job.status == "paused":
            job.status = "running"
            self.logger.info(f"Job repris: {job_id}")

    def _on_job_progress(self, job_id: str, progress: JobProgress):
        """Callback appelé quand un job progresse"""
        job = self.state.get_job_by_id(job_id)
        if job:
            job.progress = progress.progress
            self.state.update_global_progress()
            self.state.notify_observers("job_progress")

    def _on_job_completion(self, job_id: str, result: JobResult):
        """Callback appelé quand un job est terminé"""
        job = self.state.get_job_by_id(job_id)
        if job:
            job.status = result.status.value
            job.progress = 100.0 if result.status.value == "completed" else job.progress
            
            if result.status.value == "completed":
                self.logger.info(f"Job terminé avec succès: {job_id}")
            else:
                self.logger.warning(f"Job échoué: {job_id} - {result.error_message}")
            
            self.state.update_global_progress()
            self.state.notify_observers("job_completion")
            
            # Vérifier si tous les jobs sont terminés
            if self._all_jobs_finished():
                self.state.set_encoding_state(False)
                self.logger.info("Tous les jobs terminés")

    def _all_jobs_finished(self) -> bool:
        """Vérifie si tous les jobs sont terminés"""
        active_statuses = {"running", "pending", "paused"}
        return not any(job.status in active_statuses for job in self.state.jobs)

    # === Gestion des presets ===
    
    def save_current_settings_as_preset(self, preset_name: str):
        """Sauvegarde les paramètres actuels comme preset"""
        preset_data = {
            "media_type": self.state.global_media_type,
            "codec": self.state.global_codec,
            "encoder": self.state.global_encoder,
            "container": self.state.global_container,
            "quality": self.state.global_quality,
            "preset": self.state.global_preset,
            "bitrate": self.state.global_bitrate,
            "multipass": self.state.global_multipass,
        }
        
        self.state.save_preset(preset_name, preset_data)
        self.logger.info(f"Preset sauvegardé: {preset_name}")

    def load_preset(self, preset_name: str) -> bool:
        """Charge un preset et applique ses paramètres"""
        preset_data = self.state.load_preset(preset_name)
        if not preset_data:
            self.logger.warning(f"Preset introuvable: {preset_name}")
            return False
        
        # Appliquer les paramètres du preset
        self.state.update_global_encoding_settings(**preset_data)
        self.state.current_preset = preset_name
        
        self.logger.info(f"Preset chargé: {preset_name}")
        return True

    def delete_preset(self, preset_name: str):
        """Supprime un preset"""
        self.state.delete_preset(preset_name)
        if self.state.current_preset == preset_name:
            self.state.current_preset = None
        self.logger.info(f"Preset supprimé: {preset_name}")

    # === Gestion des serveurs ===
    
    async def connect_to_server(self, ip: str, port: int) -> bool:
        """Tente de se connecter à un serveur"""
        try:
            server_info = await self.distributed_client.connect_to_server(ip, port)
            if server_info:
                self.state.update_server(server_info)
                self.logger.info(f"Connexion réussie au serveur: {ip}:{port}")
                return True
            else:
                self.logger.warning(f"Échec de connexion au serveur: {ip}:{port}")
                return False
        except Exception as e:
            self.logger.error(f"Erreur lors de la connexion au serveur {ip}:{port}: {e}")
            return False

    def disconnect_from_server(self, server_id: str):
        """Se déconnecte d'un serveur"""
        asyncio.create_task(self.distributed_client.disconnect_server(server_id))
        self.state.remove_server(server_id)
        self.logger.info(f"Déconnexion du serveur: {server_id}")

    # === Méthodes utilitaires ===
    
    def get_app_statistics(self) -> Dict[str, Any]:
        """Récupère des statistiques sur l'état de l'application"""
        jobs = self.state.jobs
        servers = self.state.servers.values()
        
        return {
            "jobs": {
                "total": len(jobs),
                "pending": len([j for j in jobs if j.status == "pending"]),
                "running": len([j for j in jobs if j.status == "running"]),
                "completed": len([j for j in jobs if j.status == "completed"]),
                "failed": len([j for j in jobs if j.status == "failed"]),
            },
            "servers": {
                "total": len(servers),
                "connected": len([s for s in servers if s.status.value in ["online", "busy"]]),
                "offline": len([s for s in servers if s.status.value == "offline"]),
            },
            "global_progress": self.state.global_progress,
            "is_encoding": self.state.is_encoding,
        } 