import asyncio
import logging
from typing import List, Dict, Callable, Optional, Any

from shared.messages import ServerInfo, ServerStatus
from core.distributed_client import DistributedClient
from core.settings import Settings

class ServerDiscovery:
    """Gère la découverte, la connexion et le monitoring des serveurs d'encodage."""
    
    def __init__(self, distributed_client: DistributedClient, settings: Settings):
        self.distributed_client = distributed_client
        self.settings = settings
        self.logger = logging.getLogger(__name__)
        self._monitoring_task = None
        self.server_update_callback: Optional[Callable[[List[ServerInfo]], Any]] = None

    async def start_discovery(self):
        """Démarre le processus de découverte et de monitoring des serveurs."""
        self.logger.info("Démarrage de la découverte des serveurs...")
        # Connecter aux serveurs configurés au démarrage
        for server_config in self.settings.distributed.auto_connect_servers:
            ip = server_config.get("ip")
            port = server_config.get("port")
            if ip and port:
                await self.distributed_client.connect_to_server(ip, port)
        
        # Démarrer la tâche de monitoring périodique
        if not self._monitoring_task or self._monitoring_task.done():
            self._monitoring_task = asyncio.create_task(self._monitor_servers_periodically())

    async def stop_discovery(self):
        """Arrête le processus de découverte et de monitoring."""
        if self._monitoring_task:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                self.logger.info("Tâche de monitoring des serveurs annulée.")
        await self.distributed_client.shutdown()

    async def _monitor_servers_periodically(self):
        """Tâche de monitoring périodique des serveurs connectés."""
        while True:
            self.logger.debug("Monitoring des serveurs...")
            connected_servers = self.distributed_client.get_connected_servers()
            
            # Mettre à jour le statut des serveurs existants
            for server_id, server_info in list(self.distributed_client.servers.items()):
                if server_info not in connected_servers:
                    # Serveur déconnecté, tenter de le reconnecter si ce n'est pas déjà fait
                    if f"ws://{server_info.ip}:{server_info.port}" not in self.distributed_client.reconnect_tasks:
                        self.logger.warning(f"Serveur {server_info.name} ({server_info.ip}:{server_info.port}) déconnecté. Tentative de reconnexion...")
                        asyncio.create_task(self.distributed_client.connect_to_server(server_info.ip, server_info.port))
                else:
                    # Serveur toujours connecté, envoyer un ping pour vérifier l'état
                    await self.distributed_client.ping_server(server_id)
            
            # Appeler le callback de mise à jour si défini
            if self.server_update_callback:
                self.server_update_callback(self.distributed_client.get_connected_servers())

            await asyncio.sleep(self.settings.ui.refresh_interval)

    def register_server_update_callback(self, callback: Callable[[List[ServerInfo]], Any]):
        """Enregistre un callback pour être appelé lors de la mise à jour des serveurs."""
        self.server_update_callback = callback

    async def add_server(self, ip: str, port: int) -> Optional[ServerInfo]:
        """Ajoute un nouveau serveur à la liste et tente de s'y connecter."""
        server_info = await self.distributed_client.connect_to_server(ip, port)
        if server_info:
            # Ajouter à la configuration pour auto-connexion future
            if {"ip": ip, "port": port} not in self.settings.distributed.auto_connect_servers:
                self.settings.distributed.auto_connect_servers.append({"ip": ip, "port": port})
                self.settings.save()
            if self.server_update_callback:
                self.server_update_callback(self.distributed_client.get_connected_servers())
        return server_info

    async def remove_server(self, server_id: str):
        """Supprime un serveur de la liste et le déconnecte."""
        server_info = self.distributed_client.servers.get(server_id)
        if server_info:
            await self.distributed_client.disconnect_server(server_id)
            # Supprimer de la configuration
            self.settings.distributed.auto_connect_servers = [
                s for s in self.settings.distributed.auto_connect_servers 
                if not (s.get("ip") == server_info.ip and s.get("port") == server_info.port)
            ]
            self.settings.save()
            del self.distributed_client.servers[server_id]
            if self.server_update_callback:
                self.server_update_callback(self.distributed_client.get_connected_servers())

    def get_all_servers(self) -> Dict[str, ServerInfo]:
        """Retourne toutes les informations de serveur connues (connectées ou non)."""
        return self.distributed_client.servers
