from typing import List, Dict, Optional, Callable, Any
from core.encode_job import EncodeJob
from shared.messages import ServerInfo
from core.settings import Settings
import logging
import json
from pathlib import Path

class AppState:
    """
    Gestionnaire d'état centralisé pour l'application FFMpeg Easy Distributed.
    
    Toutes les données partagées de l'application passent par cette classe,
    qui implémente le pattern Observer pour notifier les changements.
    """
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jobs: List[EncodeJob] = []
        self.servers: Dict[str, ServerInfo] = {}
        self.selected_job_ids: List[str] = []
        self.current_preset: Optional[str] = None
        self.is_encoding: bool = False
        self.global_progress: float = 0.0
        
        # Variables d'interface
        self.input_folder: str = ""
        self.output_folder: str = ""
        self.watch_folder_enabled: bool = False
        
        # Variables d'encodage globales
        self.global_media_type: str = "video"
        self.global_codec: str = ""
        self.global_encoder: str = ""
        self.global_container: str = ""
        self.global_quality: str = "22"
        self.global_preset: str = "medium"
        self.global_bitrate: str = "4000"
        self.global_multipass: bool = False
        
        # Observateurs pour les changements d'état
        self._observers: List[Callable] = []
        self.logger = logging.getLogger(__name__)

        self.load_queue()

    def load_queue(self, file_path: Path = Path("queue.json")):
        """Charge la file d'attente depuis un fichier JSON"""
        if not file_path.exists():
            self.jobs = []
            return

        try:
            with file_path.open('r', encoding='utf-8') as f:
                jobs_data = json.load(f)
            
            self.jobs = [EncodeJob.from_dict(data) for data in jobs_data]
            
            # Vérifier si tous les jobs sont terminés
            all_done = all(job.get_overall_status() in ["done", "cancelled", "error"] for job in self.jobs)
            if all_done and self.jobs:
                self.logger.info("Tous les jobs dans la file d'attente précédente sont terminés. Nettoyage de la file.")
                self.clear_jobs() # Ceci va sauvegarder une file vide
            else:
                self.logger.info(f"{len(self.jobs)} jobs chargés depuis la file d'attente.")
                self.notify_observers("jobs_changed")

            # Réinitialiser les jobs non terminés pour éviter les jobs orphelins (état running/paused assigné à un serveur disparu)
            for job in self.jobs:
                if job.get_overall_status() not in ["done", "cancelled", "error", "pending"]:
                    for out in job.outputs:
                        if out.status not in ["done", "cancelled", "error"]:
                            out.status = "pending"
                    job.is_cancelled = False
            # Sauvegarder immédiatement la file mise à jour si des changements ont été faits
            self.save_queue()

        except (json.JSONDecodeError, TypeError, KeyError) as e:
            self.logger.error(f"Erreur lors du chargement de la file d'attente: {e}. La file sera vide.")
            self.jobs = []

    def save_queue(self, file_path: Path = Path("queue.json")):
        """Sauvegarde la file d'attente dans un fichier JSON"""
        jobs_to_save = [
            job for job in self.jobs
            if job.get_overall_status() not in ["done", "cancelled", "error"]
        ]
        try:
            with file_path.open('w', encoding='utf-8') as f:
                json.dump([job.to_dict() for job in jobs_to_save], f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.logger.error(f"Erreur lors de la sauvegarde de la file d'attente: {e}")

    def register_observer(self, observer: Callable):
        """Enregistre un observateur pour les changements d'état"""
        if observer not in self._observers:
            self._observers.append(observer)
            self.logger.debug(f"Observateur enregistré: {observer.__name__ if hasattr(observer, '__name__') else str(observer)}")

    def unregister_observer(self, observer: Callable):
        """Désenregistre un observateur"""
        if observer in self._observers:
            self._observers.remove(observer)
            self.logger.debug(f"Observateur désenregistré: {observer.__name__ if hasattr(observer, '__name__') else str(observer)}")

    def notify_observers(self, change_type: str = "general"):
        """Notifie tous les observateurs d'un changement d'état"""
        self.logger.info(f"📢 APPSTATE: Notification des observateurs: {change_type} ({len(self._observers)} observateurs)")
        for i, observer in enumerate(self._observers[:]):  # Copie pour éviter les modifications pendant l'itération
            try:
                self.logger.info(f"📞 APPSTATE: Appel observateur {i+1}/{len(self._observers)}: {observer}")
                if hasattr(observer, '__call__'):
                    observer(change_type=change_type)
                    self.logger.info(f"✅ APPSTATE: Observateur {i+1} appelé avec succès")
                else:
                    self.logger.warning(f"⚠️ APPSTATE: Observateur non callable: {observer}")
            except Exception as e:
                self.logger.error(f"💥 APPSTATE: Erreur lors de la notification de l'observateur {observer}: {e}", exc_info=True)

    # Méthodes pour la gestion des jobs
    def add_job(self, job: EncodeJob):
        """Ajoute un job à la liste"""
        self.logger.info(f"📝 APPSTATE: Ajout job {job.job_id} - {job.src_path.name}")
        self.jobs.append(job)
        self.logger.info(f"📊 APPSTATE: Total jobs maintenant: {len(self.jobs)}")
        
        try:
            self.save_queue()
            self.logger.info(f"💾 APPSTATE: Queue sauvegardée")
        except Exception as e:
            self.logger.error(f"💥 APPSTATE: Erreur sauvegarde queue: {e}", exc_info=True)
        
        try:
            self.notify_observers("jobs_changed")
            self.logger.info(f"📢 APPSTATE: Observateurs notifiés (jobs_changed)")
        except Exception as e:
            self.logger.error(f"💥 APPSTATE: Erreur notification observateurs: {e}", exc_info=True)

    def remove_job(self, job_id: str):
        """Supprime un job par son ID"""
        self.jobs = [job for job in self.jobs if job.job_id != job_id]
        if job_id in self.selected_job_ids:
            self.selected_job_ids.remove(job_id)
        self.save_queue()
        self.notify_observers("jobs_changed")

    def get_job_by_id(self, job_id: str) -> Optional[EncodeJob]:
        """Récupère un job par son ID"""
        return next((job for job in self.jobs if job.job_id == job_id), None)

    def clear_jobs(self):
        """Vide la liste des jobs"""
        self.jobs.clear()
        self.selected_job_ids.clear()
        self.save_queue()
        self.notify_observers("jobs_changed")

    def get_jobs_by_status(self, status: str) -> List[EncodeJob]:
        """Récupère tous les jobs ayant un statut donné"""
        return [job for job in self.jobs if job.status == status]

    # Méthodes pour la gestion des serveurs
    def update_server(self, server: ServerInfo):
        """Met à jour les informations d'un serveur"""
        self.servers[server.server_id] = server
        self.notify_observers("servers_changed")

    def remove_server(self, server_id: str):
        """Supprime un serveur"""
        if server_id in self.servers:
            del self.servers[server_id]
            self.notify_observers("servers_changed")

    def get_connected_servers(self) -> List[ServerInfo]:
        """Récupère la liste des serveurs connectés"""
        return [server for server in self.servers.values() if server.status.value in ["online", "busy"]]

    def get_server_by_id(self, server_id: str) -> Optional[ServerInfo]:
        """Récupère un serveur par son ID"""
        return self.servers.get(server_id)

    # Méthodes pour la sélection
    def set_selected_jobs(self, job_ids: List[str]):
        """Définit la sélection de jobs"""
        self.selected_job_ids = job_ids[:]
        self.notify_observers("selection_changed")

    def get_selected_jobs(self) -> List[EncodeJob]:
        """Récupère les jobs sélectionnés"""
        return [job for job in self.jobs if job.job_id in self.selected_job_ids]

    # Méthodes pour les paramètres globaux
    def update_global_encoding_settings(self, **kwargs):
        """Met à jour les paramètres d'encodage globaux"""
        for key, value in kwargs.items():
            if hasattr(self, f"global_{key}"):
                setattr(self, f"global_{key}", value)
        self.notify_observers("encoding_settings_changed")

    def apply_global_settings_to_job(self, job: EncodeJob):
        """Applique les paramètres globaux à un job"""
        if not job.outputs:
            # Créer un OutputConfig par défaut si nécessaire
            from core.encode_job import OutputConfig
            output_name = f"{job.src_path.stem} - Default"
            dst_path = job.src_path.with_suffix(f".{self.global_container}")
            output_cfg = OutputConfig(output_name, dst_path, job.mode)
            job.outputs.append(output_cfg)

        # Appliquer les paramètres au premier output
        output_cfg = job.outputs[0]
        output_cfg.encoder = self.global_encoder
        output_cfg.codec = self.global_codec
        output_cfg.container = self.global_container
        output_cfg.quality = self.global_quality
        output_cfg.preset = self.global_preset
        output_cfg.bitrate = self.global_bitrate
        output_cfg.multipass = self.global_multipass
        
        if self.global_container:
            output_cfg.dst_path = job.src_path.with_suffix(f".{self.global_container}")

    # Méthodes pour les presets
    def save_preset(self, name: str, preset_data: Dict[str, Any]):
        """Sauvegarde un preset"""
        self.settings.presets[name] = preset_data
        self.settings.save()
        self.notify_observers("presets_changed")

    def load_preset(self, name: str) -> Optional[Dict[str, Any]]:
        """Charge un preset par nom"""
        return self.settings.presets.get(name)

    def delete_preset(self, name: str):
        """Supprime un preset"""
        if name in self.settings.presets:
            del self.settings.presets[name]
            self.settings.save()
            self.notify_observers("presets_changed")

    def get_preset_names(self) -> List[str]:
        """Récupère la liste des noms de presets"""
        return list(self.settings.presets.keys())

    # Méthodes pour le progrès global
    def update_global_progress(self):
        """Met à jour le progrès global basé sur l'état des jobs"""
        if not self.jobs:
            self.global_progress = 0.0
        else:
            total_progress = sum(getattr(job, 'progress', 0) for job in self.jobs)
            self.global_progress = total_progress / len(self.jobs)
        self.notify_observers("progress_changed")

    # Méthodes pour l'état de l'encodage
    def set_encoding_state(self, is_encoding: bool):
        """Met à jour l'état de l'encodage."""
        self.is_encoding = is_encoding
        self.notify_observers("encoding_state_changed")

    def set_global_media_type(self, media_type: str):
        """Met à jour le type de média global."""
        if self.global_media_type != media_type:
            self.global_media_type = media_type
            # Potentiellement réinitialiser d'autres paramètres globaux
            self.global_codec = ""
            self.global_encoder = ""
            self.global_container = ""
            self.notify_observers("global_media_type_changed")

    # Méthodes utilitaires
    def get_state_summary(self) -> Dict[str, Any]:
        """Récupère un résumé de l'état pour le debugging"""
        return {
            "jobs_count": len(self.jobs),
            "servers_count": len(self.servers),
            "connected_servers": len(self.get_connected_servers()),
            "selected_jobs": len(self.selected_job_ids),
            "is_encoding": self.is_encoding,
            "global_progress": self.global_progress,
            "presets_count": len(self.settings.presets),
        } 