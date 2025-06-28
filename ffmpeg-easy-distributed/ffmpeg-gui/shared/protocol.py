import json
import time
import uuid
import asyncio
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional, List, Union
from enum import Enum
import logging
import websockets

class MessageType(Enum):
    """Types de messages du protocole"""
    # Connexion et découverte
    HELLO = "hello"
    SERVER_INFO = "server_info"
    PING = "ping"
    PONG = "pong"
    DISCONNECT = "disconnect"
    
    # Gestion des capacités
    CAPABILITY_REQUEST = "capability_request"
    CAPABILITY_RESPONSE = "capability_response"
    CAPABILITY_UPDATE = "capability_update"
    
    # Jobs et tâches
    JOB_SUBMIT = "job_submit"
    JOB_ACCEPTED = "job_accepted"
    JOB_REJECTED = "job_rejected"
    JOB_PROGRESS = "job_progress"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_CANCEL = "job_cancel"
    JOB_REASSIGN = "job_reassign"
    
    # Transfert de fichiers
    FILE_UPLOAD_START = "file_upload_start"
    FILE_CHUNK = "file_chunk"
    FILE_UPLOAD_COMPLETE = "file_upload_complete"
    FILE_DOWNLOAD_REQUEST = "file_download_request"
    FILE_DOWNLOAD_START = "file_download_start"
    
    # Erreurs
    ERROR = "error"
    VALIDATION_ERROR = "validation_error"

@dataclass
class Message:
    """Message base du protocole"""
    type: MessageType
    data: Any
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    reply_to: Optional[str] = None
    
    def to_json(self) -> str:
        """Sérialise le message en JSON"""
        return json.dumps({
            'type': self.type.value,
            'data': self.data,
            'message_id': self.message_id,
            'timestamp': self.timestamp,
            'reply_to': self.reply_to
        })
    
    @classmethod
    def from_json(cls, json_str: str) -> 'Message':
        """Désérialise un message JSON"""
        try:
            data = json.loads(json_str)
            return cls(
                type=MessageType(data['type']),
                data=data['data'],
                message_id=data.get('message_id', str(uuid.uuid4())),
                timestamp=data.get('timestamp', time.time()),
                reply_to=data.get('reply_to')
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ProtocolError(f"Invalid message format: {e}")

class ProtocolError(Exception):
    """Erreur de protocole"""
    pass

class MessageValidator:
    """Validateur de messages"""
    
    @staticmethod
    def validate_server_info(data: Dict[str, Any]) -> bool:
        """Valide un message server_info"""
        required_fields = ['name', 'capabilities', 'status', 'max_jobs', 'current_jobs']
        return all(field in data for field in required_fields)
    
    @staticmethod
    def validate_job_submission(data: Dict[str, Any]) -> bool:
        """Valide une soumission de job"""
        required_fields = ['job_id', 'input_file', 'output_config', 'ffmpeg_args']
        return all(field in data for field in required_fields)
    
    @staticmethod
    def validate_capability_request(data: Dict[str, Any]) -> bool:
        """Valide une demande de capacités"""
        return 'encoders_needed' in data

async def send_message(websocket, message: Message) -> None:
    """Envoie un message via WebSocket avec gestion d'erreur"""
    try:
        json_message = message.to_json()
        logging.debug(f"Message envoyé: {message.type.value}, Contenu: {json_message}")
        await websocket.send(json_message)
    except Exception as e:
        logging.error(f"Erreur envoi message: {e}")
        raise ProtocolError(f"Failed to send message: {e}")

async def receive_message(websocket) -> Message:
    """Reçoit un message via WebSocket avec timeout"""
    try:
        raw_message = await websocket.recv()
        logging.debug(f"Message brut reçu: {raw_message}")
        message = Message.from_json(raw_message)
        logging.debug(f"Message reçu déserialisé: {message.type.value}")
        return message
    except websockets.exceptions.ConnectionClosed:
        raise
    except Exception as e:
        logging.error(f"Erreur réception message: {e}", exc_info=True)
        raise ProtocolError(f"Failed to receive message: {e}")
