import asyncio
import websockets
import logging
import uuid
from typing import Dict, Set, Optional, List
from pathlib import Path
import time

from shared.protocol import Message, MessageType, send_message, receive_message, ProtocolError
from shared.messages import ServerInfo, ServerStatus, JobConfiguration, JobProgress, JobResult
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
    
    async def start(self):
        """Démarre le serveur WebSocket"""
        self.logger.info(f"🚀 Serveur démarré - ID: {self.server_id}")
        self.logger.info(f"📊 Capacités détectées:")
        self.logger.info(f"   - CPU: {self.capabilities.cpu_cores} cœurs")
        self.logger.info(f"   - RAM: {self.capabilities.memory_gb} GB")
        self.logger.info(f"   - Encodeurs SW: {len(self.capabilities.software_encoders)}")
        self.logger.info(f"   - Encodeurs HW: {sum(len(v) for v in self.capabilities.hardware_encoders.values())}")
        
        async with websockets.serve(
            self.handle_client, 
            self.config.host, 
            self.config.port,
            ping_interval=20,
            ping_timeout=10
        ):
            maintenance_task = asyncio.create_task(self.maintenance_loop())
            try:
                await asyncio.Future()
            finally:
                maintenance_task.cancel()
    
    async def stop(self):
        """Arrête proprement le serveur"""
        self.logger.info("🛑 Arrêt du serveur en cours...")
        self.status = ServerStatus.MAINTENANCE
        
        for job_id, processor in self.active_jobs.items():
            self.logger.info(f"⏹️  Annulation job {job_id}")
            await processor.cancel()
        
        for client_id, websocket in self.clients.items():
            await websocket.close()
        
        self.logger.info("✅ Serveur arrêté proprement")
    
    async def handle_client(self, websocket, path):
        """Gère une connexion client"""
        client_id = str(uuid.uuid4())
        client_addr = websocket.remote_address
        self.clients[client_id] = websocket
        
        self.logger.info(f"👋 Client connecté: {client_addr} (ID: {client_id})")
        
        try:
            await self.send_server_info(websocket)
            
            async for raw_message in websocket:
                try:
                    message = Message.from_json(raw_message)
                    await self.process_message(client_id, websocket, message)
                except ProtocolError as e:
                    self.logger.warning(f"⚠️  Erreur protocole client {client_id}: {e}")
                    error_msg = Message(MessageType.VALIDATION_ERROR, {"error": str(e)})
                    await send_message(websocket, error_msg)
                
        except websockets.exceptions.ConnectionClosed:
            self.logger.info(f"👋 Client déconnecté: {client_addr}")
        except Exception as e:
            self.logger.error(f"❌ Erreur client {client_id}: {e}")
        finally:
            if client_id in self.clients:
                del self.clients[client_id]
    
    async def send_server_info(self, websocket):
        """Envoie les informations du serveur au client"""
        server_info = ServerInfo(
            server_id=self.server_id,
            name=self.config.name or self.capabilities.hostname,
            ip=self.config.host,
            port=self.config.port,
            status=self.status,
            capabilities=self.capabilities,
            max_jobs=self.config.max_jobs,
            current_jobs=len(self.active_jobs),
            uptime=time.time() - self.start_time,
            last_seen=time.time()
        )
        message = Message(MessageType.SERVER_INFO, server_info.__dict__)
        await send_message(websocket, message)
    
    async def process_message(self, client_id: str, websocket, message: Message):
        self.logger.debug(f"📨 Message reçu de {client_id}: {message.type}")
        
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
            progress_msg = Message(MessageType.JOB_PROGRESS, progress.__dict__)
            await send_message(self.clients[client_id], progress_msg)
    
    async def _on_job_completion(self, client_id: str, result: JobResult):
        job_id = result.job_id
        if job_id in self.active_jobs:
            del self.active_jobs[job_id]
        
        if result.status == JobStatus.COMPLETED:
            self.jobs_completed += 1
        else:
            self.jobs_failed += 1
        
        if client_id in self.clients:
            completion_msg = Message(MessageType.JOB_COMPLETED, result.__dict__)
            await send_message(self.clients[client_id], completion_msg)
        
        self.logger.info(f"🎉 Job terminé: {job_id} ({result.status.value})")
    
    async def maintenance_loop(self):
        while True:
            try:
                await asyncio.sleep(60)
                self.capabilities.current_load = psutil.cpu_percent(interval=1) / 100.0
                await self.file_manager.cleanup_old_files()
                self.logger.debug(f"📊 Statut: {len(self.active_jobs)}/{self.config.max_jobs} jobs, {len(self.clients)} clients, charge CPU: {self.capabilities.current_load:.1%}")
            except Exception as e:
                self.logger.error(f"❌ Erreur maintenance: {e}")

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
