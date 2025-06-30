import asyncio
import websockets
import logging
from typing import Dict, List, Callable, Any, Optional
from collections import deque
import time
from pathlib import Path

from shared.protocol import Message, MessageType, send_message, receive_message, ProtocolError
from shared.messages import ServerInfo, ServerCapabilities, ServerStatus, JobConfiguration, JobProgress, JobResult, JobStatus
from core.settings import Settings
from core.app_state import AppState

class DistributedClient:
    """Client principal pour la communication avec les serveurs d'encodage distribués."""
    
    def __init__(self, settings: Settings, app_state: 'AppState'):
        self.settings = settings
        self.app_state = app_state
        self.servers: Dict[str, ServerInfo] = {}
        self.active_connections: Dict[str, websockets.WebSocketClientProtocol] = {}
        self.message_callbacks: Dict[str, Callable] = {}
        self.job_progress_callbacks: Dict[str, Callable] = {}
        self.job_completion_callbacks: Dict[str, Callable] = {}
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self.logger = logging.getLogger(__name__)
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.reconnect_tasks: Dict[str, asyncio.Task] = {}

    async def connect_to_server(self, ip: str, port: int) -> Optional[ServerInfo]:
        """Tente de se connecter à un serveur et récupère ses informations."""
        uri = f"ws://{ip}:{port}"
        self.logger.info(f"Tentative de connexion au serveur: {uri}")
        try:
            websocket = await websockets.connect(uri, open_timeout=self.settings.distributed.default_timeout)
            self.active_connections[uri] = websocket
            self.logger.info(f"Connecté au serveur: {uri}. Envoi du message HELLO.")

            # Envoyer un message HELLO pour initier la communication
            hello_msg = Message(MessageType.HELLO, {"client_name": "FFmpegEasyGUI"})
            self.logger.debug(f"Envoi du HELLO: {hello_msg.to_json()}")
            await send_message(websocket, hello_msg)

            # Attendre les informations du serveur
            self.logger.debug(f"Attente de la réponse SERVER_INFO de {uri}")
            response = await asyncio.wait_for(receive_message(websocket), timeout=self.settings.distributed.ping_timeout)
            self.logger.debug(f"Réponse reçue de {uri}: type={response.type}")

            if response.type == MessageType.SERVER_INFO:
                self.logger.debug(f"Données SERVER_INFO brutes: {response.data}")
                # Créer l'objet ServerCapabilities à partir du dictionnaire
                caps_data = response.data['capabilities']
                capabilities = ServerCapabilities(**caps_data)
                
                # Créer l'objet ServerInfo avec le bon status enum
                server_info = ServerInfo(
                    server_id=response.data['server_id'],
                    name=response.data['name'],
                    ip=ip,
                    port=port,
                    status=ServerStatus(response.data['status']),
                    capabilities=capabilities,
                    max_jobs=response.data['max_jobs'],
                    current_jobs=response.data['current_jobs'],
                    uptime=response.data['uptime'],
                    last_seen=response.data['last_seen']
                )
                self.servers[server_info.server_id] = server_info
                self.logger.info(f"Informations serveur reçues de {uri}: {server_info.name}")
                self.logger.debug(f"Détails du serveur: {server_info}")
                asyncio.create_task(self._listen_to_server(uri, websocket))
                return server_info
            else:
                self.logger.warning(f"Message inattendu de {uri}: {response.type}")
                await websocket.close()
                del self.active_connections[uri]
                return None
        except Exception as e:
            self.logger.error(f"Échec de connexion au serveur {uri}: {e}", exc_info=True)
            if uri in self.active_connections:
                del self.active_connections[uri]
            return None

    async def _listen_to_server(self, uri: str, websocket: websockets.WebSocketClientProtocol):
        """Écoute les messages entrants d'un serveur connecté."""
        self.logger.debug(f"Démarrage de l'écoute des messages pour {uri}")
        try:
            while True:
                message = await receive_message(websocket)
                self.logger.debug(f"Message reçu dans la boucle d'écoute de {uri}: {message.type}")
                await self._process_server_message(uri, message)
        except websockets.exceptions.ConnectionClosedOK:
            self.logger.info(f"Connexion fermée proprement avec {uri}")
        except websockets.exceptions.ConnectionClosedError as e:
            self.logger.warning(f"Connexion fermée avec erreur pour {uri}: code={e.code}, reason='{e.reason}'")
        except Exception as e:
            self.logger.error(f"Erreur d'écoute sur {uri}: {e}", exc_info=True)
        finally:
            self.logger.info(f"Déconnexion de {uri}. Nettoyage et tentative de reconnexion si nécessaire.")
            if uri in self.active_connections:
                del self.active_connections[uri]
            # Tenter de se reconnecter
            if uri not in self.reconnect_tasks:
                self.reconnect_tasks[uri] = asyncio.create_task(self._reconnect_server(uri))

    async def _reconnect_server(self, uri: str):
        """Tente de se reconnecter à un serveur après une déconnexion."""
        ip, port = uri.replace("ws://", "").split(":")
        port = int(port)
        #initialisation du délai de reconnexion et du compteur de tentatives
        retry_delay = getattr(self.settings.distributed, "reconnect_initial_delay", 5)
        attempts = 0
        max_attempts = getattr(self.settings.distributed, "max_reconnect_attempts", -1)

        #boucle de reconnexion avec limite de tentatives (∞ si max_attempts < 0)
        while uri not in self.active_connections and (max_attempts < 0 or attempts < max_attempts):
            self.logger.info(f"Tentative de reconnexion à {uri} dans {retry_delay}s... (essai {attempts + 1}/{max_attempts if max_attempts > -1 else '∞'})")
            await asyncio.sleep(retry_delay)
            attempts += 1
            try:
                websocket = await websockets.connect(uri, open_timeout=self.settings.distributed.default_timeout)
                self.active_connections[uri] = websocket
                self.logger.info(f"Reconnexion réussie à {uri}")
                # Ré-envoyer HELLO et récupérer SERVER_INFO
                hello_msg = Message(MessageType.HELLO, {"client_name": "FFmpegEasyGUI"})
                await send_message(websocket, hello_msg)
                response = await asyncio.wait_for(receive_message(websocket), timeout=self.settings.distributed.ping_timeout)
                if response.type == MessageType.SERVER_INFO:
                    # Créer l'objet ServerCapabilities à partir du dictionnaire
                    caps_data = response.data['capabilities']
                    capabilities = ServerCapabilities(**caps_data)
                    
                    # Créer l'objet ServerInfo avec le bon status enum
                    server_info = ServerInfo(
                        server_id=response.data['server_id'],
                        name=response.data['name'],
                        ip=ip,
                        port=port,
                        status=ServerStatus(response.data['status']),
                        capabilities=capabilities,
                        max_jobs=response.data['max_jobs'],
                        current_jobs=response.data['current_jobs'],
                        uptime=response.data['uptime'],
                        last_seen=response.data['last_seen']
                    )
                    self.servers[server_info.server_id] = server_info
                    self.logger.info(f"Informations serveur mises à jour pour {uri}: {server_info.name}")
                    asyncio.create_task(self._listen_to_server(uri, websocket))
                    if uri in self.reconnect_tasks:
                        del self.reconnect_tasks[uri]
                    break
                else:
                    self.logger.warning(f"Message inattendu après reconnexion de {uri}: {response.type}")
                    await websocket.close()
                    del self.active_connections[uri]
            except Exception as e:
                self.logger.warning(f"Échec de reconnexion à {uri}: {e}")
                #backoff exponentiel avec plafonnement configurable
                max_delay = getattr(self.settings.distributed, "reconnect_max_delay", 60)
                retry_delay = min(retry_delay * 2, max_delay)

        #si la reconnexion a échoué après le nombre maximal de tentatives
        if uri not in self.active_connections:
            self.logger.error(f"Abandon de la reconnexion à {uri} après {attempts} tentatives infructueuses.")

        #nettoyage de la tâche de reconnexion si elle existe toujours
        if uri in self.reconnect_tasks:
            del self.reconnect_tasks[uri]

    async def _process_server_message(self, uri: str, message: Message):
        """Traite un message reçu d'un serveur."""
        self.logger.debug(f"Message reçu de {uri}: {message.type.value}")

        if message.type == MessageType.SERVER_INFO:
            ip, port_str = uri.replace("ws://", "").split(":")
            port = int(port_str)
            # Créer l'objet ServerCapabilities à partir du dictionnaire
            caps_data = message.data['capabilities']
            capabilities = ServerCapabilities(**caps_data)
            
            # Créer l'objet ServerInfo avec le bon status enum
            server_info = ServerInfo(
                server_id=message.data['server_id'],
                name=message.data['name'],
                ip=ip,
                port=port,
                status=ServerStatus(message.data['status']),  # Convertir string en enum
                capabilities=capabilities,
                max_jobs=message.data['max_jobs'],
                current_jobs=message.data['current_jobs'],
                uptime=message.data['uptime'],
                last_seen=message.data['last_seen']
            )
            self.servers[server_info.server_id] = server_info
            self.logger.info(f"Mise à jour infos serveur: {server_info.name} ({server_info.status.value})")
        
        elif message.type == MessageType.JOB_PROGRESS:
            progress = JobProgress(**message.data)
            if progress.job_id in self.job_progress_callbacks:
                await self.job_progress_callbacks[progress.job_id](progress)
        
        elif message.type == MessageType.JOB_COMPLETED or message.type == MessageType.JOB_FAILED:
            # Créer l'objet JobResult avec le bon status enum
            result = JobResult(
                job_id=message.data['job_id'],
                status=JobStatus(message.data['status']),  # Convertir string en enum
                output_file=message.data['output_file'],
                file_size=message.data['file_size'],
                duration=message.data['duration'],
                average_fps=message.data['average_fps'],
                error_message=message.data['error_message'],
                server_id=message.data['server_id'],
                completed_at=message.data['completed_at']
            )
            if result.job_id in self.job_completion_callbacks:
                await self.job_completion_callbacks[result.job_id](result)
            # Nettoyer les callbacks après complétion
            self.job_progress_callbacks.pop(result.job_id, None)
            self.job_completion_callbacks.pop(result.job_id, None)

        elif message.type == MessageType.PONG:
            if message.reply_to in self.pending_requests:
                future = self.pending_requests[message.reply_to]
                if not future.done():
                    future.set_result(message)
        
        elif message.type == MessageType.ERROR or message.type == MessageType.VALIDATION_ERROR:
            self.logger.error(f"Erreur du serveur {uri}: {message.data.get('error', 'Erreur inconnue')}")
            if message.reply_to in self.pending_requests:
                future = self.pending_requests[message.reply_to]
                if not future.done():
                    future.set_exception(ProtocolError(message.data.get('error')))

        elif message.type == MessageType.FILE_DOWNLOAD_START:
            await self._handle_file_download_start(uri, message)
        
        elif message.type == MessageType.FILE_CHUNK:
            await self._handle_file_chunk(uri, message)

        # Gérer les réponses aux requêtes en attente
        if message.reply_to and message.reply_to in self.pending_requests:
            future = self.pending_requests[message.reply_to]
            if not future.done():
                future.set_result(message)

    async def _handle_file_download_start(self, uri: str, message: Message):
        """Gère le début d'un transfert de fichier descendant."""
        data = message.data
        job_id = data['job_id']
        file_name = data['file_name']
        file_size = data['file_size']

        # Assurez-vous que le dossier de sortie est défini et existe
        output_folder = self.app_state.output_folder
        if not output_folder:
            self.logger.error("Dossier de sortie non configuré. Impossible de recevoir le fichier.")
            return

        output_path = Path(output_folder) / file_name
        
        try:
            file_handle = open(output_path, "wb")
            self.active_downloads[job_id] = {
                "file_handle": file_handle,
                "file_path": output_path,
                "total_size": file_size,
                "received_size": 0
            }
            self.logger.info(f"Début de la réception du fichier {file_name} pour le job {job_id} vers {output_path}")
        except IOError as e:
            self.logger.error(f"Impossible d'ouvrir le fichier pour écriture {output_path}: {e}")

    async def _handle_file_chunk(self, uri: str, message: Message):
        """Gère la réception d'un chunk de fichier."""
        data = message.data
        job_id = data['job_id']
        chunk = data['chunk'] # Les chunks sont déjà en bytes

        if job_id in self.active_downloads:
            download_info = self.active_downloads[job_id]
            try:
                download_info["file_handle"].write(chunk)
                download_info["received_size"] += len(chunk)

                # Vérifier si le téléchargement est terminé
                if download_info["received_size"] >= download_info["total_size"]:
                    self.logger.info(f"Fichier {download_info['file_path'].name} pour le job {job_id} reçu avec succès.")
                    download_info["file_handle"].close()
                    
                    # Envoyer la confirmation de nettoyage au serveur
                    cleanup_msg = Message(MessageType.FILE_CLEANUP, {'job_id': job_id})
                    websocket = self.active_connections.get(uri)
                    if websocket:
                        await send_message(websocket, cleanup_msg)
                        self.logger.info(f"Message de nettoyage envoyé pour le job {job_id}.")
                    
                    # Nettoyer l'état du téléchargement
                    del self.active_downloads[job_id]

            except Exception as e:
                self.logger.error(f"Erreur lors de l'écriture du chunk pour le job {job_id}: {e}")
                # Nettoyer en cas d'erreur
                download_info["file_handle"].close()
                del self.active_downloads[job_id]

    async def send_job_to_server(self, server_id: str, job_config: JobConfiguration,
                                 progress_callback: Callable[[JobProgress], Any],
                                 completion_callback: Callable[[JobResult], Any]) -> bool:
        """Envoie un job à un serveur spécifique."""
        server_info = self.servers.get(server_id)
        if not server_info:
            self.logger.error(f"Serveur {server_id} non trouvé.")
            return False
        
        uri = f"ws://{server_info.ip}:{server_info.port}"
        websocket = self.active_connections.get(uri)
        
        # Vérifier si la connexion est active et ouverte
        if not websocket or getattr(websocket, 'closed', False):
            self.logger.error(f"Connexion au serveur {server_id} non active.")
            return False

        try:
            # Enregistrer les callbacks pour ce job
            self.job_progress_callbacks[job_config.job_id] = progress_callback
            self.job_completion_callbacks[job_config.job_id] = completion_callback

            # Convertir job_config en dictionnaire sérialisable
            job_dict = {
                'job_id': job_config.job_id,
                'input_file': job_config.input_file,
                'output_file': job_config.output_file,
                'encoder': job_config.encoder,
                'encoder_type': job_config.encoder_type.value if hasattr(job_config.encoder_type, 'value') else job_config.encoder_type,
                'preset': job_config.preset,
                'quality_mode': job_config.quality_mode,
                'quality_value': job_config.quality_value,
                'filters': job_config.filters,
                'ffmpeg_args': job_config.ffmpeg_args,
                'required_capabilities': job_config.required_capabilities,
                'priority': job_config.priority,
                'estimated_duration': job_config.estimated_duration,
                'file_size': job_config.file_size,
                'resolution': job_config.resolution,
                'codec': job_config.codec,
                'container': job_config.container
            }
            submit_msg = Message(MessageType.JOB_SUBMIT, job_dict)
            await send_message(websocket, submit_msg)
            self.logger.info(f"Job {job_config.job_id} soumis au serveur {server_id}")
            return True
        except Exception as e:
            self.logger.error(f"Échec de soumission du job {job_config.job_id} au serveur {server_id}: {e}")
            self.job_progress_callbacks.pop(job_config.job_id, None)
            self.job_completion_callbacks.pop(job_config.job_id, None)
            return False

    async def request_server_capabilities(self, server_id: str, encoders_needed: List[str]) -> Optional[Dict[str, Any]]:
        """Demande les capacités d'un serveur spécifique."""
        server_info = self.servers.get(server_id)
        if not server_info:
            self.logger.error(f"Serveur {server_id} non trouvé.")
            return None
        
        uri = f"ws://{server_info.ip}:{server_info.port}"
        websocket = self.active_connections.get(uri)
        
        # Vérifier si la connexion est active et ouverte
        if not websocket or getattr(websocket, 'closed', False):
            self.logger.error(f"Connexion au serveur {server_id} non active.")
            return None

        try:
            request_msg = Message(MessageType.CAPABILITY_REQUEST, {"encoders_needed": encoders_needed})
            future = asyncio.Future()
            self.pending_requests[request_msg.message_id] = future
            await send_message(websocket, request_msg)
            
            response = await asyncio.wait_for(future, timeout=self.settings.distributed.default_timeout)
            if response.type == MessageType.CAPABILITY_RESPONSE:
                return response.data
            else:
                self.logger.warning(f"Réponse inattendue pour CAPABILITY_REQUEST: {response.type}")
                return None
        except Exception as e:
            self.logger.error(f"Échec de la demande de capacités au serveur {server_id}: {e}")
            return None
        finally:
            self.pending_requests.pop(request_msg.message_id, None)

    async def ping_server(self, server_id: str) -> bool:
        """Envoie un ping à un serveur et attend un pong."""
        server_info = self.servers.get(server_id)
        if not server_info:
            self.logger.error(f"Serveur {server_id} non trouvé.")
            return False
        
        uri = f"ws://{server_info.ip}:{server_info.port}"
        websocket = self.active_connections.get(uri)
        
        # Vérifier si la connexion est active et ouverte
        if not websocket or getattr(websocket, 'closed', False):
            self.logger.warning(f"Connexion au serveur {server_id} non active pour ping.")
            return False

        try:
            ping_msg = Message(MessageType.PING, {"timestamp": time.time()})
            future = asyncio.Future()
            self.pending_requests[ping_msg.message_id] = future
            await send_message(websocket, ping_msg)
            
            response = await asyncio.wait_for(future, timeout=self.settings.distributed.ping_timeout)
            return response.type == MessageType.PONG
        except Exception as e:
            self.logger.warning(f"Échec du ping au serveur {server_id}: {e}")
            return False
        finally:
            self.pending_requests.pop(ping_msg.message_id, None)

    #this part do that
    #Méthode d'annulation d'un job déjà soumis à un serveur distant
    async def cancel_job_on_server(self, server_id: str, job_id: str) -> bool:
        """Envoie une requête d'annulation de job à un serveur. Retourne True si l'envoi a réussi."""
        server_info = self.servers.get(server_id)
        if not server_info:
            self.logger.error(f"Serveur {server_id} inconnu pour annulation job {job_id}.")
            return False

        uri = f"ws://{server_info.ip}:{server_info.port}"
        websocket = self.active_connections.get(uri)

        if not websocket or getattr(websocket, 'closed', False):
            self.logger.warning(f"Connexion non active vers {server_id} pour annuler le job {job_id}.")
            return False

        try:
            cancel_msg = Message(MessageType.JOB_CANCEL, {"job_id": job_id})
            await send_message(websocket, cancel_msg)
            self.logger.info(f"🛑 Requête d'annulation envoyée pour le job {job_id} au serveur {server_id}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'envoi de JOB_CANCEL au serveur {server_id}: {e}")
            return False

    #this other part do that
    async def pause_job_on_server(self, server_id: str, job_id: str) -> bool:
        """Envoie une requête de pause à un serveur."""
        server_info = self.servers.get(server_id)
        if not server_info:
            self.logger.error(f"Serveur {server_id} inconnu pour pause job {job_id}.")
            return False

        uri = f"ws://{server_info.ip}:{server_info.port}"
        websocket = self.active_connections.get(uri)

        if not websocket or getattr(websocket, 'closed', False):
            self.logger.warning(f"Connexion non active vers {server_id} pour pauser le job {job_id}.")
            return False

        try:
            pause_msg = Message(MessageType.JOB_PAUSE, {"job_id": job_id})
            await send_message(websocket, pause_msg)
            self.logger.info(f"⏸️  Requête de pause envoyée pour le job {job_id} au serveur {server_id}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'envoi de JOB_PAUSE au serveur {server_id}: {e}")
            return False

    async def resume_job_on_server(self, server_id: str, job_id: str) -> bool:
        """Envoie une requête de reprise à un serveur."""
        server_info = self.servers.get(server_id)
        if not server_info:
            self.logger.error(f"Serveur {server_id} inconnu pour reprise job {job_id}.")
            return False

        uri = f"ws://{server_info.ip}:{server_info.port}"
        websocket = self.active_connections.get(uri)

        if not websocket or getattr(websocket, 'closed', False):
            self.logger.warning(f"Connexion non active vers {server_id} pour reprendre le job {job_id}.")
            return False

        try:
            resume_msg = Message(MessageType.JOB_RESUME, {"job_id": job_id})
            await send_message(websocket, resume_msg)
            self.logger.info(f"▶️  Requête de reprise envoyée pour le job {job_id} au serveur {server_id}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'envoi de JOB_RESUME au serveur {server_id}: {e}")
            return False

    def get_connected_servers(self) -> List[ServerInfo]:
        """Retourne la liste des ServerInfo pour les serveurs actuellement connectés."""
        connected_uris = list(self.active_connections.keys())
        return [info for info in self.servers.values() if f"ws://{info.ip}:{info.port}" in connected_uris]

    async def disconnect_server(self, server_id: str):
        """Déconnecte un serveur spécifique."""
        server_info = self.servers.get(server_id)
        if not server_info:
            return
        uri = f"ws://{server_info.ip}:{server_info.port}"
        if uri in self.active_connections:
            websocket = self.active_connections[uri]
            await websocket.close()
            del self.active_connections[uri]
            self.logger.info(f"Serveur {server_id} déconnecté.")
        if uri in self.reconnect_tasks:
            self.reconnect_tasks[uri].cancel()
            del self.reconnect_tasks[uri]

    async def shutdown(self):
        """Ferme toutes les connexions actives."""
        for uri, websocket in list(self.active_connections.items()):
            try:
                await websocket.close()
            except Exception as e:
                self.logger.error(f"Erreur lors de la fermeture de la connexion {uri}: {e}")
            finally:
                del self.active_connections[uri]
        for task in self.reconnect_tasks.values():
            task.cancel()
        self.reconnect_tasks.clear()
        self.logger.info("Toutes les connexions client distribuées ont été fermées.")
