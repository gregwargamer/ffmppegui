import asyncio
import websockets
import logging
import uuid
from typing import Dict, Set, Optional, List
from pathlib import Path
import psutil
import time

from shared.protocol import Message, MessageType, send_message, receive_message, ProtocolError
from shared.messages import ServerInfo, ServerStatus, JobConfiguration, JobProgress, JobResult, JobStatus
from server.job_processor import JobProcessor
from server.file_manager import FileManager
from server.config_manager import ServerConfig
from core.hardware_detector import detect_capabilities

class EncodeServer:
    """Serveur d'encodage WebSocket principal"""
    
    def __init__(self, config: ServerConfig):
        self.config = config
        self.server_id = str(uuid.uuid4())
        self.capabilities = detect_capabilities()
        self.status = ServerStatus.ONLINE
        
        self.clients: Dict[str, websockets.WebSocketServerProtocol] = {}
        self.active_jobs: Dict[str, JobProcessor] = {}
        self.job_queue: List[JobConfiguration] = []
        
        self.file_manager = FileManager(config.temp_dir)
        
        self.start_time = time.time()
        self.jobs_completed = 0
        self.jobs_failed = 0
        
        self.logger = logging.getLogger(__name__)

        self._stopping = False
        self.websocket_server = None
        self.maintenance_task = None
    
    async def start(self):
        """Démarre le serveur WebSocket sans bloquer."""
        self.logger.info(f"🚀 Serveur démarré - ID: {self.server_id}")
        self.logger.info(f"📊 Capacités détectées:")
        self.logger.info(f"   - CPU: {self.capabilities.cpu_cores} cœurs")
        self.logger.info(f"   - RAM: {self.capabilities.memory_gb} GB")
        self.logger.info(f"   - Encodeurs SW: {len(self.capabilities.software_encoders)}")
        self.logger.info(f"   - Encodeurs HW: {sum(len(v) for v in self.capabilities.hardware_encoders.values())}")
        
        self.websocket_server = await websockets.serve(
            self.handle_client, 
            self.config.host, 
            self.config.port,
            ping_interval=20,
            ping_timeout=10
        )
        self.maintenance_task = asyncio.create_task(self.maintenance_loop())
    
    async def stop(self):
        """Arrête proprement le serveur."""
        if self._stopping:
            return
        self._stopping = True

        self.logger.info("🛑 Arrêt du serveur en cours...")
        self.status = ServerStatus.MAINTENANCE
        
        # Annuler la tâche de maintenance
        if self.maintenance_task:
            self.maintenance_task.cancel()

        # Fermer les connexions client existantes
        if self.clients:
            self.logger.info(f"Fermeture de {len(self.clients)} connexions client...")
            client_close_tasks = []
            for client in self.clients.values():
                try:
                    # Vérifier que la connexion n'est pas déjà fermée avant de tenter de la fermer
                    if not getattr(client, 'closed', False):
                        client_close_tasks.append(client.close(code=1001, reason="Server shutting down"))
                except Exception as e:
                    self.logger.warning(f"Erreur lors de la fermeture d'un client: {e}")
            
            if client_close_tasks:
                await asyncio.wait(client_close_tasks, timeout=5)

        # Annuler les jobs actifs
        if self.active_jobs:
            self.logger.info(f"Annulation de {len(self.active_jobs)} jobs actifs...")
            job_cancel_tasks = [
                processor.cancel() for processor in self.active_jobs.values()
            ]
            await asyncio.wait(job_cancel_tasks, timeout=10)
        
        # Arrêter le serveur websocket
        if self.websocket_server:
            self.websocket_server.close()
            await self.websocket_server.wait_closed()
            self.logger.info("Serveur WebSocket arrêté.")

        self.logger.info("✅ Serveur arrêté proprement")
    
    async def handle_client(self, websocket, path=None):
        """Gère une connexion client"""
        client_id = str(uuid.uuid4())
        client_addr = websocket.remote_address
        self.clients[client_id] = websocket
        
        self.logger.info(f"👋 Client connecté: {client_addr} (ID: {client_id})")
        
        try:
            # Le client attend un HELLO, mais notre protocole est que le serveur envoie directement ses infos.
            # On attend le HELLO du client avant d'envoyer nos infos.
            self.logger.debug(f"Attente du message HELLO du client {client_id}")
            
            # Le client envoie HELLO juste après la connexion.
            # On doit donc lire ce premier message ici.
            try:
                raw_message = await asyncio.wait_for(websocket.recv(), timeout=10)
                message = Message.from_json(raw_message)
                if message.type == MessageType.HELLO:
                    client_name = message.data.get("client_name", "Inconnu")
                    self.logger.info(f"Message HELLO reçu du client {client_id} ({client_name})")
                else:
                    self.logger.warning(f"Premier message n'était pas HELLO (reçu: {message.type}). On continue quand même.")
            except asyncio.TimeoutError:
                self.logger.error(f"Timeout en attente du HELLO du client {client_id}. Fermeture connexion.")
                return
            except Exception as e:
                self.logger.error(f"Erreur en lisant le message HELLO du client {client_id}: {e}", exc_info=True)
                return


            self.logger.debug(f"Envoi des informations du serveur au client {client_id}")
            await self.send_server_info(websocket)
            self.logger.info(f"Informations du serveur envoyées avec succès au client {client_id}")
            
            async for raw_message in websocket:
                try:
                    self.logger.debug(f"Message brut reçu de {client_id}: {raw_message}")
                    message = Message.from_json(raw_message)
                    await self.process_message(client_id, websocket, message)
                except ProtocolError as e:
                    self.logger.warning(f"⚠️  Erreur protocole client {client_id}: {e}")
                    error_msg = Message(MessageType.VALIDATION_ERROR, {"error": str(e)})
                    await send_message(websocket, error_msg)
                
        except websockets.exceptions.ConnectionClosed as e:
            self.logger.info(f"👋 Client déconnecté: {client_addr} (code: {e.code}, raison: {e.reason})")
        except Exception as e:
            self.logger.error(f"❌ Erreur critique dans handle_client pour {client_id}: {e}", exc_info=True)
        finally:
            self.logger.debug(f"Nettoyage de la connexion pour le client {client_id}")
            if client_id in self.clients:
                del self.clients[client_id]
    
    async def send_server_info(self, websocket):
        """Envoie les informations du serveur au client"""
        try:
            self.logger.debug("Construction du message SERVER_INFO...")
            # Convertir les capacités en dictionnaire sérialisable
            capabilities_dict = {
                'hostname': self.capabilities.hostname,
                'os': self.capabilities.os,
                'cpu_cores': self.capabilities.cpu_cores,
                'memory_gb': self.capabilities.memory_gb,
                'disk_space_gb': self.capabilities.disk_space_gb,
                'software_encoders': self.capabilities.software_encoders,
                'hardware_encoders': self.capabilities.hardware_encoders,
                'estimated_performance': self.capabilities.estimated_performance,
                'current_load': self.capabilities.current_load,
                'max_resolution': self.capabilities.max_resolution,
                'supported_formats': self.capabilities.supported_formats,
                'max_file_size_gb': self.capabilities.max_file_size_gb
            }
            
            server_info_dict = {
                'server_id': self.server_id,
                'name': self.config.name or self.capabilities.hostname,
                'ip': self.config.host,
                'port': self.config.port,
                'status': self.status.value,  # Convertir l'enum en string
                'capabilities': capabilities_dict,
                'max_jobs': self.config.max_jobs,
                'current_jobs': len(self.active_jobs),
                'uptime': time.time() - self.start_time,
                'last_seen': time.time()
            }
            self.logger.debug(f"Données SERVER_INFO prêtes: {server_info_dict}")
            
            message = Message(MessageType.SERVER_INFO, server_info_dict)
            self.logger.debug("Envoi du message SERVER_INFO...")
            await send_message(websocket, message)
            self.logger.debug("Message SERVER_INFO envoyé.")
        except Exception as e:
            self.logger.error(f"Erreur fatale lors de l'envoi de SERVER_INFO: {e}", exc_info=True)
            # Cette erreur peut causer la fermeture de la connexion par le serveur.
            await websocket.close(code=1011, reason="Internal server error during info exchange")
            raise  # Relauncer l'exception pour que handle_client la capture.
    
    async def process_message(self, client_id: str, websocket, message: Message):
        self.logger.debug(f"📨 Message reçu de {client_id}: {message.type} - Contenu: {message.data}")
        
        if message.type == MessageType.HELLO:
            # Déjà traité dans handle_client, mais on peut logger ici si besoin.
            self.logger.info(f"Message HELLO (re)reçu de {client_id}. Normalement ignoré ici.")
            return # Ne rien faire, la poignée de main est déjà faite.
        if message.type == MessageType.PING:
            await self.handle_ping(websocket, message)
        elif message.type == MessageType.CAPABILITY_REQUEST:
            await self.handle_capability_request(websocket, message)
        elif message.type == MessageType.JOB_SUBMIT:
            await self.handle_job_submission(client_id, websocket, message)
        elif message.type == MessageType.JOB_CANCEL:
            await self.handle_job_cancellation(websocket, message)
        elif message.type == MessageType.FILE_UPLOAD_START:
            await self.handle_file_upload_start(websocket, message)
        elif message.type == MessageType.FILE_CHUNK:
            await self.handle_file_chunk(websocket, message)
        else:
            self.logger.warning(f"⚠️  Type de message non géré: {message.type}")
    
    async def handle_ping(self, websocket, message: Message):
        pong = Message(MessageType.PONG, message.data, reply_to=message.message_id)
        await send_message(websocket, pong)
    
    async def handle_capability_request(self, websocket, message: Message):
        encoders_needed = message.data.get('encoders_needed', [])
        all_available = (self.capabilities.software_encoders + 
                        [enc for encoders in self.capabilities.hardware_encoders.values() for enc in encoders])
        
        compatible_encoders = [enc for enc in encoders_needed if enc in all_available]
        missing_encoders = [enc for enc in encoders_needed if enc not in all_available]
        
        response_data = {
            'compatible_encoders': compatible_encoders,
            'missing_encoders': missing_encoders,
            'compatibility_score': len(compatible_encoders) / max(len(encoders_needed), 1),
            'server_load': len(self.active_jobs) / self.config.max_jobs,
            'estimated_performance': self.capabilities.estimated_performance
        }
        response = Message(MessageType.CAPABILITY_RESPONSE, response_data, reply_to=message.message_id)
        await send_message(websocket, response)
    
    async def handle_job_submission(self, client_id: str, websocket, message: Message):
        if len(self.active_jobs) >= self.config.max_jobs:
            reject_msg = Message(MessageType.JOB_REJECTED, {'job_id': message.data.get('job_id'), 'reason': 'server_full', 'retry_after': 30}, reply_to=message.message_id)
            await send_message(websocket, reject_msg)
            return
        
        try:
            job_config = JobConfiguration(**message.data)
            if not self._is_job_compatible(job_config):
                reject_msg = Message(MessageType.JOB_REJECTED, {'job_id': job_config.job_id, 'reason': 'incompatible_encoder', 'missing_capabilities': job_config.required_capabilities}, reply_to=message.message_id)
                await send_message(websocket, reject_msg)
                return
            
            accept_msg = Message(MessageType.JOB_ACCEPTED, {'job_id': job_config.job_id, 'estimated_duration': job_config.estimated_duration}, reply_to=message.message_id)
            await send_message(websocket, accept_msg)
            
            processor = JobProcessor(job_config=job_config, file_manager=self.file_manager, capabilities=self.capabilities,
                                     progress_callback=lambda p: self._on_job_progress(client_id, p),
                                     completion_callback=lambda r: self._on_job_completion(client_id, r))
            
            self.active_jobs[job_config.job_id] = processor
            asyncio.create_task(processor.start())
            self.logger.info(f"✅ Job accepté: {job_config.job_id} (client: {client_id})")
        except Exception as e:
            self.logger.error(f"❌ Erreur soumission job: {e}")
            error_msg = Message(MessageType.ERROR, {'error': str(e), 'job_id': message.data.get('job_id')}, reply_to=message.message_id)
            await send_message(websocket, error_msg)
    
    async def handle_job_cancellation(self, websocket, message: Message):
        job_id = message.data.get('job_id')
        if job_id in self.active_jobs:
            await self.active_jobs[job_id].cancel()
            del self.active_jobs[job_id]
            self.logger.info(f"⏹️  Job annulé: {job_id}")
    
    def _is_job_compatible(self, job_config: JobConfiguration) -> bool:
        all_available = (self.capabilities.software_encoders + 
                        [enc for encoders in self.capabilities.hardware_encoders.values() for enc in encoders])
        
        if job_config.encoder not in all_available:
            return False
        if any(cap not in all_available for cap in job_config.required_capabilities):
            return False
        if job_config.file_size > self.config.max_file_size_bytes:
            return False
        return True
    
    async def _on_job_progress(self, client_id: str, progress: JobProgress):
        if client_id in self.clients:
            # Vérifier que la connexion WebSocket est toujours active
            websocket = self.clients[client_id]
            try:
                # Vérifier l'état de la connexion WebSocket (compatible avec websockets 15.x)
                if getattr(websocket, 'closed', False):
                    self.logger.warning(f"Connexion WebSocket fermée pour client {client_id}, suppression du cache")
                    del self.clients[client_id]
                    return
                
                # Convertir l'objet progress en dictionnaire sérialisable
                progress_dict = {
                    'job_id': progress.job_id,
                    'progress': progress.progress,
                    'current_frame': progress.current_frame,
                    'total_frames': progress.total_frames,
                    'fps': progress.fps,
                    'bitrate': progress.bitrate,
                    'speed': progress.speed,
                    'eta': progress.eta,
                    'server_id': progress.server_id
                }
                progress_msg = Message(MessageType.JOB_PROGRESS, progress_dict)
                await send_message(websocket, progress_msg)
            except Exception as e:
                self.logger.warning(f"Erreur envoi progression au client {client_id}: {e}")
                # Nettoyer le client défaillant
                if client_id in self.clients:
                    del self.clients[client_id]
    
    async def _on_job_completion(self, client_id: str, result: JobResult):
        job_id = result.job_id
        if job_id in self.active_jobs:
            del self.active_jobs[job_id]
        
        if result.status == JobStatus.COMPLETED:
            self.jobs_completed += 1
        else:
            self.jobs_failed += 1
        
        if client_id in self.clients:
            # Vérifier que la connexion WebSocket est toujours active
            websocket = self.clients[client_id]
            try:
                # Vérifier l'état de la connexion WebSocket (compatible avec websockets 15.x)
                if getattr(websocket, 'closed', False):
                    self.logger.warning(f"Connexion WebSocket fermée pour client {client_id}, suppression du cache")
                    del self.clients[client_id]
                    return
                
                # Convertir l'objet result en dictionnaire sérialisable
                result_dict = {
                    'job_id': result.job_id,
                    'status': result.status.value,  # Convertir l'enum en string
                    'output_file': result.output_file,
                    'file_size': result.file_size,
                    'duration': result.duration,
                    'average_fps': result.average_fps,
                    'error_message': result.error_message,
                    'server_id': result.server_id,
                    'completed_at': result.completed_at
                }
                completion_msg = Message(MessageType.JOB_COMPLETED, result_dict)
                await send_message(websocket, completion_msg)
            except Exception as e:
                self.logger.warning(f"Erreur envoi résultat au client {client_id}: {e}")
                # Nettoyer le client défaillant
                if client_id in self.clients:
                    del self.clients[client_id]
        
        self.logger.info(f"🎉 Job terminé: {job_id} ({result.status.value})")
    
    async def maintenance_loop(self):
        """Boucle de maintenance pour les tâches périodiques."""
        while True:
            await asyncio.sleep(30) # Exécuter toutes les 30 secondes
            try:
                # Mettre à jour la charge CPU
                self.capabilities.current_load = psutil.cpu_percent(interval=1) / 100.0
                
                # Nettoyer les anciens fichiers temporaires
                await self.file_manager.cleanup_old_files()
                
                self.logger.debug(
                    f"Tâches de maintenance exécutées. "
                    f"Charge CPU: {self.capabilities.current_load:.1%}, "
                    f"Clients: {len(self.clients)}, Jobs: {len(self.active_jobs)}"
                )
            except asyncio.CancelledError:
                self.logger.info("Boucle de maintenance annulée, arrêt en cours.")
                break
            except Exception as e:
                self.logger.error(f"❌ Erreur inattendue dans la boucle de maintenance: {e}", exc_info=True)
                # On continue la boucle même en cas d'erreur sur une itération.

    async def handle_file_upload_start(self, websocket, message: Message):
        job_id = message.data.get('job_id')
        file_size = message.data.get('file_size')
        if not job_id or not file_size:
            raise ProtocolError("FILE_UPLOAD_START message missing job_id or file_size")
        self.logger.info(f"Début du transfert de fichier pour le job {job_id} (taille: {file_size} octets)")
        ack_msg = Message(MessageType.JOB_ACCEPTED, {'job_id': job_id, 'status': 'file_transfer_started'})
        await send_message(websocket, ack_msg)

    async def handle_file_chunk(self, websocket, message: Message):
        job_id = message.data.get('job_id')
        chunk = message.data.get('chunk')
        if job_id and chunk:
            await self.file_manager.receive_file_chunk(job_id, chunk)
        else:
            self.logger.warning(f"Message FILE_CHUNK invalide: {message.data}")
