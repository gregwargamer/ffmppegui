# Plan Détaillé Complet : Système d'Encodage Distribué FFmpeg Easy

## Vue d'Ensemble

Système d'encodage distribué avec deux applications séparées : une interface graphique (client) et des serveurs d'encodage (workers). Communication via WebSocket avec détection automatique des capacités et réassignation intelligente des tâches.

## Architecture Détaillée

```
┌─────────────────────────────┐    WebSocket/TCP    ┌─────────────────────────────┐
│        GUI CLIENT           │◄──────────────────►│       SERVER WORKER         │
│     ffmpeg-gui/main.py      │                     │     ffmpeg-server/main.py   │
│                             │                     │                             │
│  ┌─────────────────────┐   │                     │   ┌─────────────────────┐   │
│  │ Server Manager      │   │                     │   │ Job Processor       │   │
│  │ - Add/Remove IPs    │   │                     │   │ - FFmpeg Execution  │   │
│  │ - Capability Check  │   │                     │   │ - Progress Report   │   │
│  │ - Job Assignment    │   │                     │   │ - File Transfer     │   │
│  └─────────────────────┘   │                     │   └─────────────────────┘   │
│                             │                     │                             │
│  ┌─────────────────────┐   │                     │   ┌─────────────────────┐   │
│  │ Task Queue          │   │                     │   │ Capability Detector │   │
│  │ - Job Priority      │   │                     │   │ - NVENC/QuickSync   │   │
│  │ - Server Selection  │   │                     │   │ - VideoToolbox      │   │
│  │ - Reassignment      │   │                     │   │ - CPU/RAM/Disk      │   │
│  └─────────────────────┘   │                     │   └─────────────────────┘   │
└─────────────────────────────┘                     └─────────────────────────────┘
```

## Structure de Projet Séparée

```
ffmpeg-easy-distributed/
├── ffmpeg-gui/                           # APPLICATION CLIENT (GUI)
│   ├── main.py                           # Point d'entrée GUI
│   ├── project_map_gui.md                # Documentation GUI
│   ├── requirements.txt                  # Dépendances GUI
│   ├── gui/
│   │   ├── main_window.py                # Fenêtre principale
│   │   ├── server_manager_window.py      # NOUVEAU: Gestion serveurs
│   │   ├── job_queue_window.py           # NOUVEAU: File d'attente
│   │   ├── capability_viewer.py          # NOUVEAU: Capacités serveurs
│   │   ├── job_edit_window.py            # Édition jobs (modifié)
│   │   ├── settings_window.py
│   │   ├── log_viewer_window.py
│   │   ├── batch_operations_window.py
│   │   ├── advanced_filters_window.py
│   │   ├── audio_tracks_window.py
│   │   ├── merge_videos_window.py
│   │   └── subtitle_management_window.py
│   ├── core/
│   │   ├── distributed_client.py         # NOUVEAU: Client distribué
│   │   ├── server_discovery.py           # NOUVEAU: Découverte serveurs
│   │   ├── job_scheduler.py              # NOUVEAU: Planificateur
│   │   ├── capability_matcher.py         # NOUVEAU: Correspondance capacités
│   │   ├── encode_job.py                 # Jobs (partagé)
│   │   ├── settings.py                   # Paramètres GUI
│   │   └── ffmpeg_helpers.py             # Utilitaires FFmpeg
│   └── shared/
│       ├── protocol.py                   # NOUVEAU: Protocole WebSocket
│       ├── messages.py                   # NOUVEAU: Types de messages
│       └── utils.py                      # NOUVEAU: Utilitaires partagés
│
├── ffmpeg-server/                        # APPLICATION SERVEUR (WORKER)
│   ├── main.py                           # Point d'entrée serveur
│   ├── project_map_server.md             # Documentation serveur
│   ├── requirements.txt                  # Dépendances serveur
│   ├── server/
│   │   ├── encode_server.py              # NOUVEAU: Serveur WebSocket
│   │   ├── job_processor.py              # NOUVEAU: Traitement jobs
│   │   ├── file_manager.py               # NOUVEAU: Gestion fichiers
│   │   ├── capability_detector.py        # NOUVEAU: Détection capacités
│   │   ├── progress_reporter.py          # NOUVEAU: Rapport progression
│   │   └── config_manager.py             # NOUVEAU: Configuration
│   ├── core/
│   │   ├── ffmpeg_executor.py            # NOUVEAU: Exécuteur FFmpeg
│   │   ├── hardware_detector.py          # NOUVEAU: Détection matériel
│   │   ├── encode_job.py                 # Jobs (partagé)
│   │   └── worker_pool.py                # Pool local
│   ├── shared/                           # Lien symbolique vers ../ffmpeg-gui/shared/
│   │   ├── protocol.py                   
│   │   ├── messages.py                   
│   │   └── utils.py                      
│   ├── Dockerfile                        # Conteneurisation
│   ├── docker-compose.yml               # Multi-serveurs
│   └── scripts/
│       ├── install.sh                    # Installation Linux
│       ├── install.bat                   # Installation Windows
│       └── systemd-service.sh            # Service système
│
└── docs/
    ├── protocol_specification.md         # Spécification protocole
    ├── deployment_guide.md               # Guide déploiement
    ├── troubleshooting.md                # Dépannage
    └── api_reference.md                  # Référence API
```

## Plan d'Implémentation Détaillé : Étape par Étape

### PHASE 1 : Protocole de Communication et Messages

#### 1.1 Protocole WebSocket (shared/protocol.py)

**Objectif** : Créer un protocole de communication robuste entre client et serveur avec sérialisation JSON et validation des messages.

**Code complet à implémenter** :
```python
import json
import time
import uuid
import asyncio
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional, List, Union
from enum import Enum
import logging

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
        await websocket.send(message.to_json())
        logging.debug(f"Message envoyé: {message.type.value}")
    except Exception as e:
        logging.error(f"Erreur envoi message: {e}")
        raise ProtocolError(f"Failed to send message: {e}")

async def receive_message(websocket) -> Message:
    """Reçoit un message via WebSocket avec timeout"""
    try:
        raw_message = await asyncio.wait_for(websocket.recv(), timeout=30.0)
        message = Message.from_json(raw_message)
        logging.debug(f"Message reçu: {message.type.value}")
        return message
    except asyncio.TimeoutError:
        raise ProtocolError("Message reception timeout")
    except Exception as e:
        logging.error(f"Erreur réception message: {e}")
        raise ProtocolError(f"Failed to receive message: {e}")
```

#### 1.2 Types de Messages (shared/messages.py)

**Objectif** : Définir tous les types de données échangés entre client et serveur.

**Code complet à implémenter** :
```python
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Union
from enum import Enum

class ServerStatus(Enum):
    ONLINE = "online"
    BUSY = "busy"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"

class JobStatus(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class EncoderType(Enum):
    SOFTWARE = "software"
    HARDWARE_NVIDIA = "hardware_nvidia"
    HARDWARE_INTEL = "hardware_intel"
    HARDWARE_AMD = "hardware_amd"
    HARDWARE_APPLE = "hardware_apple"

@dataclass
class ServerCapabilities:
    """Capacités d'un serveur d'encodage"""
    # Système
    hostname: str
    os: str
    cpu_cores: int
    memory_gb: float
    disk_space_gb: float
    
    # Encodeurs disponibles
    software_encoders: List[str]
    hardware_encoders: Dict[str, List[str]]  # type -> [encoders]
    
    # Performance
    estimated_performance: float  # Score relatif
    current_load: float  # 0.0 à 1.0
    
    # Limitations
    max_resolution: str  # "4K", "8K", etc.
    supported_formats: List[str]
    max_file_size_gb: float

@dataclass
class ServerInfo:
    """Informations d'un serveur"""
    server_id: str
    name: str
    ip: str
    port: int
    status: ServerStatus
    capabilities: ServerCapabilities
    max_jobs: int
    current_jobs: int
    uptime: float
    last_seen: float

@dataclass
class JobConfiguration:
    """Configuration d'un job d'encodage"""
    job_id: str
    input_file: str
    output_file: str
    
    # Paramètres d'encodage
    encoder: str
    encoder_type: EncoderType
    preset: Optional[str]
    quality_mode: str  # "crf", "bitrate", "quality"
    quality_value: Union[int, str]
    
    # Filtres et options
    filters: List[str]
    ffmpeg_args: List[str]
    
    # Contraintes
    required_capabilities: List[str]
    priority: int  # 1-10
    estimated_duration: Optional[float]
    
    # Métadonnées
    file_size: int
    resolution: str
    codec: str
    container: str

@dataclass
class JobProgress:
    """Progression d'un job"""
    job_id: str
    progress: float  # 0.0 à 1.0
    current_frame: Optional[int]
    total_frames: Optional[int]
    fps: Optional[float]
    bitrate: Optional[str]
    speed: Optional[str]
    eta: Optional[int]  # secondes
    server_id: str

@dataclass
class JobResult:
    """Résultat d'un job terminé"""
    job_id: str
    status: JobStatus
    output_file: str
    file_size: int
    duration: float
    average_fps: float
    error_message: Optional[str]
    server_id: str
    completed_at: float

@dataclass
class CapabilityMatch:
    """Correspondance entre job et serveur"""
    server_id: str
    compatibility_score: float  # 0.0 à 1.0
    missing_capabilities: List[str]
    performance_estimate: float
    recommended: bool
```

### PHASE 2 : Détection de Capacités Serveur

#### 2.1 Détecteur de Matériel (ffmpeg-server/core/hardware_detector.py)

**Objectif** : Détecter automatiquement toutes les capacités d'encodage disponibles sur le serveur (NVENC, QuickSync, VideoToolbox, etc.).

**Code complet à implémenter** :
```python
import subprocess
import platform
import psutil
import re
import json
from typing import Dict, List, Optional, Tuple
from dataclasses import asdict
from shared.messages import ServerCapabilities, EncoderType

class HardwareDetector:
    """Détecteur de capacités matérielles et logicielles"""
    
    def __init__(self):
        self.os_type = platform.system().lower()
        self.logger = logging.getLogger(__name__)
    
    def detect_all_capabilities(self) -> ServerCapabilities:
        """Détecte toutes les capacités du serveur"""
        self.logger.info("🔍 Détection des capacités du serveur...")
        
        # Informations système de base
        system_info = self._get_system_info()
        
        # Détection FFmpeg et encodeurs
        ffmpeg_info = self._detect_ffmpeg_capabilities()
        
        # Détection matériel spécialisé
        hardware_encoders = self._detect_hardware_encoders()
        
        # Estimation performance
        performance_score = self._estimate_performance()
        
        capabilities = ServerCapabilities(
            hostname=platform.node(),
            os=f"{platform.system()} {platform.release()}",
            cpu_cores=psutil.cpu_count(),
            memory_gb=round(psutil.virtual_memory().total / (1024**3), 1),
            disk_space_gb=round(psutil.disk_usage('/').free / (1024**3), 1),
            software_encoders=ffmpeg_info['software'],
            hardware_encoders=hardware_encoders,
            estimated_performance=performance_score,
            current_load=psutil.cpu_percent(interval=1) / 100.0,
            max_resolution=self._detect_max_resolution(),
            supported_formats=ffmpeg_info['formats'],
            max_file_size_gb=100.0  # Limite par défaut
        )
        
        self.logger.info(f"✅ Capacités détectées: {len(capabilities.software_encoders)} logiciels, "
                        f"{sum(len(encoders) for encoders in capabilities.hardware_encoders.values())} matériels")
        
        return capabilities
    
    def _get_system_info(self) -> Dict:
        """Informations système de base"""
        return {
            'platform': platform.platform(),
            'processor': platform.processor(),
            'architecture': platform.architecture(),
            'python_version': platform.python_version()
        }
    
    def _detect_ffmpeg_capabilities(self) -> Dict[str, List[str]]:
        """Détecte les capacités FFmpeg"""
        try:
            # Tester la disponibilité de FFmpeg
            result = subprocess.run(['ffmpeg', '-version'], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise RuntimeError("FFmpeg non disponible")
            
            # Détecter les encodeurs
            encoders_result = subprocess.run(['ffmpeg', '-encoders'], 
                                           capture_output=True, text=True, timeout=10)
            
            # Détecter les formats
            formats_result = subprocess.run(['ffmpeg', '-formats'], 
                                          capture_output=True, text=True, timeout=10)
            
            software_encoders = self._parse_software_encoders(encoders_result.stdout)
            supported_formats = self._parse_formats(formats_result.stdout)
            
            return {
                'software': software_encoders,
                'formats': supported_formats,
                'version': self._extract_ffmpeg_version(result.stdout)
            }
            
        except (subprocess.TimeoutExpired, FileNotFoundError, RuntimeError) as e:
            self.logger.error(f"❌ Erreur détection FFmpeg: {e}")
            return {'software': [], 'formats': [], 'version': 'unknown'}
    
    def _parse_software_encoders(self, encoders_output: str) -> List[str]:
        """Parse la sortie de ffmpeg -encoders pour les encodeurs logiciels"""
        software_encoders = []
        
        # Encodeurs vidéo courants
        video_patterns = {
            'libx264': r'libx264.*H\.264',
            'libx265': r'libx265.*H\.265',
            'libvpx': r'libvpx.*VP8',
            'libvpx-vp9': r'libvpx-vp9.*VP9',
            'libaom-av1': r'libaom-av1.*AV1',
            'libsvtav1': r'libsvtav1.*AV1'
        }
        
        # Encodeurs audio courants
        audio_patterns = {
            'aac': r'aac.*AAC',
            'libfdk_aac': r'libfdk_aac.*AAC',
            'libmp3lame': r'libmp3lame.*MP3',
            'libopus': r'libopus.*Opus',
            'libvorbis': r'libvorbis.*Vorbis',
            'flac': r'flac.*FLAC'
        }
        
        all_patterns = {**video_patterns, **audio_patterns}
        
        for encoder_name, pattern in all_patterns.items():
            if re.search(pattern, encoders_output, re.IGNORECASE):
                software_encoders.append(encoder_name)
        
        return software_encoders
    
    def _detect_hardware_encoders(self) -> Dict[str, List[str]]:
        """Détecte les encodeurs matériels disponibles"""
        hardware_encoders = {
            'nvidia': [],
            'intel': [],
            'amd': [],
            'apple': []
        }
        
        # Test NVIDIA NVENC
        nvidia_encoders = self._test_nvidia_encoders()
        if nvidia_encoders:
            hardware_encoders['nvidia'] = nvidia_encoders
        
        # Test Intel QuickSync
        intel_encoders = self._test_intel_encoders()
        if intel_encoders:
            hardware_encoders['intel'] = intel_encoders
        
        # Test AMD AMF
        amd_encoders = self._test_amd_encoders()
        if amd_encoders:
            hardware_encoders['amd'] = amd_encoders
        
        # Test Apple VideoToolbox (macOS)
        if self.os_type == 'darwin':
            apple_encoders = self._test_apple_encoders()
            if apple_encoders:
                hardware_encoders['apple'] = apple_encoders
        
        return hardware_encoders
    
    def _test_nvidia_encoders(self) -> List[str]:
        """Teste la disponibilité des encodeurs NVIDIA"""
        nvidia_encoders = []
        
        # Vérifier nvidia-smi
        try:
            result = subprocess.run(['nvidia-smi'], capture_output=True, timeout=5)
            if result.returncode != 0:
                return []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        
        # Tester les encodeurs NVENC
        nvenc_tests = {
            'h264_nvenc': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                          '-c:v', 'h264_nvenc', '-f', 'null', '-'],
            'hevc_nvenc': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                          '-c:v', 'hevc_nvenc', '-f', 'null', '-'],
            'av1_nvenc': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                         '-c:v', 'av1_nvenc', '-f', 'null', '-']
        }
        
        for encoder, test_args in nvenc_tests.items():
            if self._test_encoder_availability(encoder, test_args):
                nvidia_encoders.append(encoder)
        
        return nvidia_encoders
    
    def _test_intel_encoders(self) -> List[str]:
        """Teste la disponibilité de Intel QuickSync"""
        intel_encoders = []
        
        qsv_tests = {
            'h264_qsv': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                        '-c:v', 'h264_qsv', '-f', 'null', '-'],
            'hevc_qsv': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                        '-c:v', 'hevc_qsv', '-f', 'null', '-'],
            'av1_qsv': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                       '-c:v', 'av1_qsv', '-f', 'null', '-']
        }
        
        for encoder, test_args in qsv_tests.items():
            if self._test_encoder_availability(encoder, test_args):
                intel_encoders.append(encoder)
        
        return intel_encoders
    
    def _test_amd_encoders(self) -> List[str]:
        """Teste la disponibilité des encodeurs AMD AMF"""
        amd_encoders = []
        
        amf_tests = {
            'h264_amf': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                        '-c:v', 'h264_amf', '-f', 'null', '-'],
            'hevc_amf': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                        '-c:v', 'hevc_amf', '-f', 'null', '-']
        }
        
        for encoder, test_args in amf_tests.items():
            if self._test_encoder_availability(encoder, test_args):
                amd_encoders.append(encoder)
        
        return amd_encoders
    
    def _test_apple_encoders(self) -> List[str]:
        """Teste la disponibilité de VideoToolbox (macOS)"""
        apple_encoders = []
        
        vt_tests = {
            'h264_videotoolbox': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                                 '-c:v', 'h264_videotoolbox', '-f', 'null', '-'],
            'hevc_videotoolbox': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', 
                                 '-c:v', 'hevc_videotoolbox', '-f', 'null', '-']
        }
        
        for encoder, test_args in vt_tests.items():
            if self._test_encoder_availability(encoder, test_args):
                apple_encoders.append(encoder)
        
        return apple_encoders
    
    def _test_encoder_availability(self, encoder_name: str, test_args: List[str]) -> bool:
        """Teste si un encodeur spécifique est disponible"""
        try:
            result = subprocess.run(['ffmpeg'] + test_args, 
                                  capture_output=True, timeout=10)
            # Si l'encodeur n'est pas disponible, FFmpeg retourne une erreur
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def _parse_formats(self, formats_output: str) -> List[str]:
        """Parse les formats supportés"""
        formats = []
        lines = formats_output.split('\n')
        
        for line in lines:
            if ' E ' in line or ' DE' in line:  # Format d'encodage
                parts = line.split()
                if len(parts) >= 2:
                    format_name = parts[1]
                    formats.append(format_name)
        
        return formats
    
    def _detect_max_resolution(self) -> str:
        """Détermine la résolution maximale supportée"""
        # Test simple avec différentes résolutions
        resolutions = ['8K', '4K', '2K', '1080p']
        
        for res in resolutions:
            if self._test_resolution_support(res):
                return res
        
        return '1080p'  # Fallback
    
    def _test_resolution_support(self, resolution: str) -> bool:
        """Teste le support d'une résolution"""
        size_map = {
            '8K': '7680x4320',
            '4K': '3840x2160', 
            '2K': '2560x1440',
            '1080p': '1920x1080'
        }
        
        size = size_map.get(resolution, '1920x1080')
        
        try:
            result = subprocess.run([
                'ffmpeg', '-f', 'lavfi', '-i', f'testsrc=duration=1:size={size}:rate=1',
                '-c:v', 'libx264', '-f', 'null', '-'
            ], capture_output=True, timeout=15)
            return result.returncode == 0
        except:
            return False
    
    def _estimate_performance(self) -> float:
        """Estime un score de performance relatif"""
        # Score basé sur CPU, RAM et GPU
        cpu_score = psutil.cpu_count() * 10
        memory_score = psutil.virtual_memory().total / (1024**3) * 2
        
        # Bonus pour GPU
        gpu_bonus = 0
        try:
            # Tenter de détecter NVIDIA
            subprocess.run(['nvidia-smi'], capture_output=True, timeout=2)
            gpu_bonus = 50  # Bonus significatif pour GPU NVIDIA
        except:
            pass
        
        total_score = cpu_score + memory_score + gpu_bonus
        return min(total_score, 1000.0)  # Cap à 1000
    
    def _extract_ffmpeg_version(self, version_output: str) -> str:
        """Extrait la version de FFmpeg"""
        match = re.search(r'ffmpeg version (\S+)', version_output)
        return match.group(1) if match else 'unknown'

def detect_capabilities() -> ServerCapabilities:
    """Point d'entrée principal pour la détection"""
    detector = HardwareDetector()
    return detector.detect_all_capabilities()
```

### PHASE 3 : Serveur d'Encodage Complet

#### 3.1 Serveur Principal (ffmpeg-server/main.py)

**Objectif** : Point d'entrée du serveur avec gestion des arguments et configuration.

**Code complet à implémenter** :
```python
#!/usr/bin/env python3
"""
FFmpeg Easy - Serveur d'Encodage Distribué
Point d'entrée principal du serveur
"""

import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path

# Ajouter le répertoire shared au path
sys.path.append(str(Path(__file__).parent.parent))

from server.encode_server import EncodeServer
from server.config_manager import ServerConfig
from core.hardware_detector import detect_capabilities

def setup_logging(log_level: str, log_file: str = None):
    """Configure le système de logging"""
    level = getattr(logging, log_level.upper())
    
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )

def signal_handler(server: EncodeServer):
    """Gestionnaire de signaux pour arrêt propre"""
    def handler(signum, frame):
        logging.info(f"🛑 Signal {signum} reçu, arrêt du serveur...")
        asyncio.create_task(server.stop())
    return handler

async def main():
    """Point d'entrée principal"""
    parser = argparse.ArgumentParser(description="Serveur d'encodage FFmpeg Easy")
    
    # Configuration réseau
    parser.add_argument("--host", default="0.0.0.0", help="Adresse d'écoute")
    parser.add_argument("--port", type=int, default=8765, help="Port d'écoute")
    
    # Configuration jobs
    parser.add_argument("--max-jobs", type=int, default=2, help="Jobs simultanés maximum")
    parser.add_argument("--max-file-size", default="10GB", help="Taille fichier max")
    
    # Configuration système
    parser.add_argument("--name", help="Nom du serveur")
    parser.add_argument("--temp-dir", help="Répertoire temporaire")
    parser.add_argument("--config", help="Fichier de configuration")
    
    # Logging
    parser.add_argument("--log-level", default="INFO", 
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", help="Fichier de log")
    
    # Mode de test
    parser.add_argument("--test-capabilities", action="store_true",
                       help="Teste et affiche les capacités puis quitte")
    parser.add_argument("--validate-config", action="store_true",
                       help="Valide la configuration puis quitte")
    
    args = parser.parse_args()
    
    # Configuration du logging
    setup_logging(args.log_level, args.log_file)
    
    # Chargement de la configuration
    config = ServerConfig.from_args(args)
    
    if args.validate_config:
        logging.info("✅ Configuration valide")
        return 0
    
    # Test des capacités
    if args.test_capabilities:
        logging.info("🔍 Test des capacités du serveur...")
        capabilities = detect_capabilities()
        
        print("\n" + "="*60)
        print("CAPACITÉS DU SERVEUR")
        print("="*60)
        print(f"Hostname: {capabilities.hostname}")
        print(f"OS: {capabilities.os}")
        print(f"CPU: {capabilities.cpu_cores} cœurs")
        print(f"RAM: {capabilities.memory_gb} GB")
        print(f"Disque: {capabilities.disk_space_gb} GB libre")
        print(f"Performance: {capabilities.estimated_performance:.1f}")
        
        print(f"\nEncodeurs logiciels ({len(capabilities.software_encoders)}):")
        for encoder in capabilities.software_encoders:
            print(f"  ✓ {encoder}")
        
        print(f"\nEncodeurs matériels:")
        for vendor, encoders in capabilities.hardware_encoders.items():
            if encoders:
                print(f"  {vendor.upper()}: {', '.join(encoders)}")
        
        print(f"\nRésolution max: {capabilities.max_resolution}")
        print(f"Formats supportés: {len(capabilities.supported_formats)}")
        print("="*60)
        
        return 0
    
    # Création et démarrage du serveur
    try:
        server = EncodeServer(config)
        
        # Configuration des signaux pour arrêt propre
        for sig in [signal.SIGINT, signal.SIGTERM]:
            signal.signal(sig, signal_handler(server))
        
        logging.info("🚀 Démarrage du serveur FFmpeg Easy...")
        logging.info(f"📍 Écoute sur {args.host}:{args.port}")
        logging.info(f"⚙️  Jobs max: {args.max_jobs}")
        
        # Démarrage asynchrone
        await server.start()
        
    except KeyboardInterrupt:
        logging.info("🛑 Arrêt demandé par l'utilisateur")
        return 0
    except Exception as e:
        logging.error(f"❌ Erreur fatale: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

#### 3.2 Serveur WebSocket (ffmpeg-server/server/encode_server.py)

**Objectif** : Serveur WebSocket principal gérant les connexions clients et la distribution des jobs.

**Code complet à implémenter** :
```python
import asyncio
import websockets
import logging
import uuid
from typing import Dict, Set, Optional
from pathlib import Path
import time

from shared.protocol import Message, MessageType, send_message, receive_message, ProtocolError
from shared.messages import ServerInfo, ServerStatus, JobConfiguration, JobProgress
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
        
        # Gestion des connexions
        self.clients: Dict[str, websockets.WebSocketServerProtocol] = {}
        self.active_jobs: Dict[str, JobProcessor] = {}
        self.job_queue: List[JobConfiguration] = []
        
        # Gestionnaires
        self.file_manager = FileManager(config.temp_dir)
        
        # Métriques
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
        
        # Démarrer le serveur WebSocket
        async with websockets.serve(
            self.handle_client, 
            self.config.host, 
            self.config.port,
            ping_interval=20,
            ping_timeout=10
        ):
            # Démarrer les tâches de maintenance
            maintenance_task = asyncio.create_task(self.maintenance_loop())
            
            try:
                await asyncio.Future()  # Run forever
            finally:
                maintenance_task.cancel()
    
    async def stop(self):
        """Arrête proprement le serveur"""
        self.logger.info("🛑 Arrêt du serveur en cours...")
        self.status = ServerStatus.MAINTENANCE
        
        # Annuler tous les jobs en cours
        for job_id, processor in self.active_jobs.items():
            self.logger.info(f"⏹️  Annulation job {job_id}")
            await processor.cancel()
        
        # Fermer toutes les connexions
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
            # Envoyer les informations du serveur
            await self.send_server_info(websocket)
            
            # Boucle de traitement des messages
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
        """Traite un message reçu d'un client"""
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
        """Répond à un ping"""
        pong = Message(MessageType.PONG, message.data, reply_to=message.message_id)
        await send_message(websocket, pong)
    
    async def handle_capability_request(self, websocket, message: Message):
        """Traite une demande de capacités"""
        encoders_needed = message.data.get('encoders_needed', [])
        
        # Vérifier la compatibilité
        missing_encoders = []
        compatible_encoders = []
        
        all_available = (self.capabilities.software_encoders + 
                        [enc for encoders in self.capabilities.hardware_encoders.values() 
                         for enc in encoders])
        
        for encoder in encoders_needed:
            if encoder in all_available:
                compatible_encoders.append(encoder)
            else:
                missing_encoders.append(encoder)
        
        response_data = {
            'compatible_encoders': compatible_encoders,
            'missing_encoders': missing_encoders,
            'compatibility_score': len(compatible_encoders) / max(len(encoders_needed), 1),
            'server_load': len(self.active_jobs) / self.config.max_jobs,
            'estimated_performance': self.capabilities.estimated_performance
        }
        
        response = Message(MessageType.CAPABILITY_RESPONSE, response_data, 
                          reply_to=message.message_id)
        await send_message(websocket, response)
    
    async def handle_job_submission(self, client_id: str, websocket, message: Message):
        """Traite la soumission d'un job"""
        if len(self.active_jobs) >= self.config.max_jobs:
            # Serveur saturé
            reject_msg = Message(MessageType.JOB_REJECTED, {
                'job_id': message.data.get('job_id'),
                'reason': 'server_full',
                'retry_after': 30
            }, reply_to=message.message_id)
            await send_message(websocket, reject_msg)
            return
        
        try:
            job_config = JobConfiguration(**message.data)
            
            # Vérifier la compatibilité des encodeurs
            if not self._is_job_compatible(job_config):
                reject_msg = Message(MessageType.JOB_REJECTED, {
                    'job_id': job_config.job_id,
                    'reason': 'incompatible_encoder',
                    'missing_capabilities': job_config.required_capabilities
                }, reply_to=message.message_id)
                await send_message(websocket, reject_msg)
                return
            
            # Accepter le job
            accept_msg = Message(MessageType.JOB_ACCEPTED, {
                'job_id': job_config.job_id,
                'estimated_duration': job_config.estimated_duration
            }, reply_to=message.message_id)
            await send_message(websocket, accept_msg)
            
            # Créer et démarrer le processeur de job
            processor = JobProcessor(
                job_config=job_config,
                file_manager=self.file_manager,
                capabilities=self.capabilities,
                progress_callback=lambda progress: self._on_job_progress(client_id, progress),
                completion_callback=lambda result: self._on_job_completion(client_id, result)
            )
            
            self.active_jobs[job_config.job_id] = processor
            asyncio.create_task(processor.start())
            
            self.logger.info(f"✅ Job accepté: {job_config.job_id} (client: {client_id})")
            
        except Exception as e:
            self.logger.error(f"❌ Erreur soumission job: {e}")
            error_msg = Message(MessageType.ERROR, {
                'error': str(e),
                'job_id': message.data.get('job_id')
            }, reply_to=message.message_id)
            await send_message(websocket, error_msg)
    
    async def handle_job_cancellation(self, websocket, message: Message):
        """Traite l'annulation d'un job"""
        job_id = message.data.get('job_id')
        
        if job_id in self.active_jobs:
            processor = self.active_jobs[job_id]
            await processor.cancel()
            del self.active_jobs[job_id]
            
            self.logger.info(f"⏹️  Job annulé: {job_id}")
    
    def _is_job_compatible(self, job_config: JobConfiguration) -> bool:
        """Vérifie si le job est compatible avec ce serveur"""
        # Vérifier l'encodeur principal
        encoder = job_config.encoder
        
        all_available = (self.capabilities.software_encoders + 
                        [enc for encoders in self.capabilities.hardware_encoders.values() 
                         for enc in encoders])
        
        if encoder not in all_available:
            return False
        
        # Vérifier les capacités requises
        for capability in job_config.required_capabilities:
            if capability not in all_available:
                return False
        
        # Vérifier la taille du fichier
        if job_config.file_size > self.config.max_file_size_bytes:
            return False
        
        return True
    
    async def _on_job_progress(self, client_id: str, progress: JobProgress):
        """Callback appelé lors de la progression d'un job"""
        if client_id in self.clients:
            websocket = self.clients[client_id]
            progress_msg = Message(MessageType.JOB_PROGRESS, progress.__dict__)
            await send_message(websocket, progress_msg)
    
    async def _on_job_completion(self, client_id: str, result):
        """Callback appelé lors de la completion d'un job"""
        job_id = result.job_id
        
        if job_id in self.active_jobs:
            del self.active_jobs[job_id]
        
        if result.status == 'completed':
            self.jobs_completed += 1
        else:
            self.jobs_failed += 1
        
        if client_id in self.clients:
            websocket = self.clients[client_id]
            completion_msg = Message(MessageType.JOB_COMPLETED, result.__dict__)
            await send_message(websocket, completion_msg)
        
        self.logger.info(f"🎉 Job terminé: {job_id} ({result.status})")
    
    async def maintenance_loop(self):
        """Boucle de maintenance périodique"""
        while True:
            try:
                await asyncio.sleep(60)  # Toutes les minutes
                
                # Mettre à jour la charge système
                self.capabilities.current_load = psutil.cpu_percent(interval=1) / 100.0
                
                # Nettoyer les fichiers temporaires anciens
                await self.file_manager.cleanup_old_files()
                
                # Logs de statut
                self.logger.debug(f"📊 Statut: {len(self.active_jobs)}/{self.config.max_jobs} jobs, "
                                f"{len(self.clients)} clients, charge CPU: {self.capabilities.current_load:.1%}")
                
            except Exception as e:
                self.logger.error(f"❌ Erreur maintenance: {e}")
```

**server.py** - Serveur standalone prêt à l'emploi :
```python
#!/usr/bin/env python3
import asyncio
import websockets
import subprocess
import platform
import psutil
import tempfile
import base64
import argparse
from pathlib import Path
import logging

# Import local
from core.distributed import Message, MessageTypes, ServerInfo, JobData

class SimpleEncodeServer:
    def __init__(self, port=8765, max_jobs=2, name=""):
        self.port = port
        self.max_jobs = max_jobs
        self.name = name or f"{platform.node()}"
        self.current_jobs = 0
        self.temp_dir = Path(tempfile.gettempdir()) / "ffmpeg_server"
        self.temp_dir.mkdir(exist_ok=True)
        
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
    
    def get_server_info(self) -> ServerInfo:
        """Retourne les infos du serveur"""
        return ServerInfo(
            name=self.name,
            cpu_count=psutil.cpu_count(),
            memory_gb=round(psutil.virtual_memory().total / (1024**3), 1),
            max_jobs=self.max_jobs,
            current_jobs=self.current_jobs,
            encoders=self.detect_encoders()
        )
    
    def detect_encoders(self) -> list:
        """Détecte les encodeurs FFmpeg disponibles"""
        try:
            result = subprocess.run(
                ["ffmpeg", "-encoders"], 
                capture_output=True, text=True, timeout=5
            )
            encoders = []
            for line in result.stdout.split('\n'):
                if line.strip().startswith('V') and 'x264' in line:
                    encoders.append('libx264')
                elif line.strip().startswith('V') and 'x265' in line:
                    encoders.append('libx265')
            return encoders
        except:
            return ['libx264']  # Fallback
    
    async def handle_client(self, websocket, path):
        """Gère une connexion client"""
        client_addr = websocket.remote_address
        self.logger.info(f"Client connecté: {client_addr}")
        
        try:
            # Envoyer les infos du serveur
            server_info = self.get_server_info()
            msg = Message(MessageTypes.SERVER_INFO, server_info)
            await websocket.send(msg.to_json())
            
            # Écouter les messages
            async for message in websocket:
                await self.process_message(websocket, message)
                
        except websockets.exceptions.ConnectionClosed:
            self.logger.info(f"Client déconnecté: {client_addr}")
        except Exception as e:
            self.logger.error(f"Erreur client {client_addr}: {e}")
    
    async def process_message(self, websocket, message_str: str):
        """Traite un message reçu"""
        try:
            msg = Message.from_json(message_str)
            
            if msg.type == MessageTypes.JOB_SUBMIT:
                await self.handle_job_submission(websocket, msg.data)
            elif msg.type == MessageTypes.PING:
                pong = Message(MessageTypes.PONG, {"timestamp": msg.data.get("timestamp")})
                await websocket.send(pong.to_json())
                
        except Exception as e:
            error_msg = Message(MessageTypes.JOB_ERROR, {"error": str(e)})
            await websocket.send(error_msg.to_json())
    
    async def handle_job_submission(self, websocket, job_data: dict):
        """Traite une soumission de job"""
        if self.current_jobs >= self.max_jobs:
            error_msg = Message(MessageTypes.JOB_ERROR, {"error": "Serveur saturé"})
            await websocket.send(error_msg.to_json())
            return
        
        self.current_jobs += 1
        job = JobData(**job_data)
        
        try:
            # Recevoir le fichier (base64)
            input_path = await self.receive_file(websocket, job)
            
            # Encoder
            output_path = await self.encode_file(websocket, job, input_path)
            
            # Retourner le résultat
            await self.send_result(websocket, job, output_path)
            
        except Exception as e:
            error_msg = Message(MessageTypes.JOB_ERROR, {"job_id": job.job_id, "error": str(e)})
            await websocket.send(error_msg.to_json())
        finally:
            self.current_jobs -= 1
    
    async def receive_file(self, websocket, job: JobData) -> Path:
        """Reçoit un fichier en base64 par chunks"""
        input_path = self.temp_dir / f"{job.job_id}_input"
        
        # Attendre les chunks de fichier
        # (Implémentation simplifiée - en vrai il faudrait gérer les chunks)
        
        return input_path
    
    async def encode_file(self, websocket, job: JobData, input_path: Path) -> Path:
        """Encode un fichier avec FFmpeg"""
        output_path = self.temp_dir / f"{job.job_id}_output"
        
        # Construire la commande FFmpeg
        cmd = ["ffmpeg", "-i", str(input_path)] + job.ffmpeg_args + [str(output_path)]
        
        # Lancer l'encodage avec suivi
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        
        # Simuler le suivi de progression (simplifié)
        while process.poll() is None:
            progress_msg = Message(MessageTypes.JOB_PROGRESS, {
                "job_id": job.job_id,
                "progress": 0.5  # Simplifié
            })
            await websocket.send(progress_msg.to_json())
            await asyncio.sleep(1)
        
        if process.returncode != 0:
            stderr = process.stderr.read()
            raise RuntimeError(f"FFmpeg error: {stderr}")
        
        return output_path
    
    async def send_result(self, websocket, job: JobData, output_path: Path):
        """Envoie le fichier résultat"""
        # Lire et encoder en base64 (simplifié)
        with open(output_path, 'rb') as f:
            file_data = base64.b64encode(f.read()).decode()
        
        complete_msg = Message(MessageTypes.JOB_COMPLETE, {
            "job_id": job.job_id,
            "file_data": file_data,
            "file_size": output_path.stat().st_size
        })
        await websocket.send(complete_msg.to_json())
        
        # Nettoyer
        output_path.unlink()
    
    async def start(self):
        """Démarre le serveur"""
        self.logger.info(f"🚀 Serveur FFmpeg démarré sur port {self.port}")
        self.logger.info(f"📊 Capacités: {self.get_server_info()}")
        
        async with websockets.serve(self.handle_client, "0.0.0.0", self.port):
            await asyncio.Future()  # Run forever

def main():
    parser = argparse.ArgumentParser(description="Serveur d'encodage FFmpeg")
    parser.add_argument("--port", type=int, default=8765, help="Port d'écoute")
    parser.add_argument("--max-jobs", type=int, default=2, help="Jobs simultanés max")
    parser.add_argument("--name", help="Nom du serveur")
    
    args = parser.parse_args()
    
    server = SimpleEncodeServer(args.port, args.max_jobs, args.name)
    
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\n🛑 Serveur arrêté")

if __name__ == "__main__":
    main()
```

### Étape 3 : Interface de Gestion des Serveurs

**gui/distributed_window.py** - Interface simple pour gérer les serveurs :
```python
import tkinter as tk
from tkinter import ttk, messagebox
import asyncio
import websockets
from core.distributed import Message, MessageTypes

class DistributedWindow:
    def __init__(self, parent):
        self.parent = parent
        self.servers = []  # Liste des serveurs
        
        self.window = tk.Toplevel(parent)
        self.window.title("Serveurs d'Encodage")
        self.window.geometry("700x500")
        
        self.build_ui()
    
    def build_ui(self):
        # Frame principal
        main_frame = ttk.Frame(self.window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Ajout de serveur
        add_frame = ttk.LabelFrame(main_frame, text="Ajouter un Serveur")
        add_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(add_frame, text="IP:").grid(row=0, column=0, padx=5, pady=5)
        self.ip_var = tk.StringVar(value="localhost")
        ttk.Entry(add_frame, textvariable=self.ip_var, width=15).grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(add_frame, text="Port:").grid(row=0, column=2, padx=5, pady=5)
        self.port_var = tk.StringVar(value="8765")
        ttk.Entry(add_frame, textvariable=self.port_var, width=8).grid(row=0, column=3, padx=5, pady=5)
        
        ttk.Button(add_frame, text="Ajouter", command=self.add_server).grid(row=0, column=4, padx=5, pady=5)
        
        # Liste des serveurs
        list_frame = ttk.LabelFrame(main_frame, text="Serveurs Connectés")
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        # Treeview
        columns = ("ip", "status", "jobs", "cpu", "memory")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        
        self.tree.heading("ip", text="Adresse")
        self.tree.heading("status", text="Statut")
        self.tree.heading("jobs", text="Jobs")
        self.tree.heading("cpu", text="CPU")
        self.tree.heading("memory", text="RAM (GB)")
        
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Boutons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(button_frame, text="Actualiser", command=self.refresh_servers).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(button_frame, text="Supprimer", command=self.remove_server).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(button_frame, text="Test Ping", command=self.ping_servers).pack(side=tk.LEFT)
    
    def add_server(self):
        """Ajoute un serveur à la liste"""
        ip = self.ip_var.get().strip()
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror("Erreur", "Port invalide")
            return
        
        if not ip:
            messagebox.showerror("Erreur", "IP requise")
            return
        
        # Tester la connexion
        asyncio.create_task(self.test_server_connection(ip, port))
    
    async def test_server_connection(self, ip: str, port: int):
        """Teste la connexion à un serveur"""
        try:
            uri = f"ws://{ip}:{port}"
            async with websockets.connect(uri, timeout=5) as websocket:
                # Attendre les infos du serveur
                message = await websocket.recv()
                msg = Message.from_json(message)
                
                if msg.type == MessageTypes.SERVER_INFO:
                    server_info = msg.data
                    self.servers.append({
                        "ip": ip,
                        "port": port,
                        "info": server_info,
                        "status": "Connecté",
                        "websocket": None
                    })
                    self.refresh_display()
                    messagebox.showinfo("Succès", f"Serveur {ip}:{port} ajouté")
                
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de se connecter à {ip}:{port}\n{e}")
    
    def refresh_servers(self):
        """Actualise l'état des serveurs"""
        for server in self.servers:
            asyncio.create_task(self.ping_server(server))
    
    async def ping_server(self, server):
        """Ping un serveur pour vérifier son état"""
        try:
            uri = f"ws://{server['ip']}:{server['port']}"
            async with websockets.connect(uri, timeout=2) as websocket:
                ping_msg = Message(MessageTypes.PING, {"timestamp": time.time()})
                await websocket.send(ping_msg.to_json())
                
                response = await asyncio.wait_for(websocket.recv(), timeout=2)
                msg = Message.from_json(response)
                
                if msg.type == MessageTypes.PONG:
                    server["status"] = "Connecté"
                else:
                    server["status"] = "Erreur"
                    
        except Exception:
            server["status"] = "Déconnecté"
        
        self.refresh_display()
    
    def refresh_display(self):
        """Met à jour l'affichage de la liste"""
        # Vider la liste
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Ajouter les serveurs
        for server in self.servers:
            info = server.get("info", {})
            self.tree.insert("", "end", values=(
                f"{server['ip']}:{server['port']}",
                server["status"],
                f"{info.get('current_jobs', 0)}/{info.get('max_jobs', 0)}",
                info.get("cpu_count", "N/A"),
                info.get("memory_gb", "N/A")
            ))
    
    def remove_server(self):
        """Supprime le serveur sélectionné"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Attention", "Sélectionnez un serveur")
            return
        
        # Trouver l'index et supprimer
        index = self.tree.index(selection[0])
        del self.servers[index]
        self.refresh_display()
    
    def ping_servers(self):
        """Ping tous les serveurs"""
        for server in self.servers:
            asyncio.create_task(self.ping_server(server))
```

### Étape 4 : Intégration dans main.py

**Modifications dans gui/main_window.py** :
```python
# Ajouter au menu ou aux boutons
def open_distributed_window(self):
    """Ouvre la fenêtre de gestion des serveurs"""
    from gui.distributed_window import DistributedWindow
    DistributedWindow(self.root)

# Dans le menu "Outils"
tools_menu.add_command(label="Serveurs d'Encodage", command=self.open_distributed_window)
```

### Étape 5 : Docker pour Déploiement Facile

**Dockerfile** :
```dockerfile
FROM python:3.11-slim

# Installer FFmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copier le code
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY server.py .
COPY core/ ./core/

# Port d'écoute
EXPOSE 8765

# Lancer le serveur
CMD ["python", "server.py", "--port", "8765", "--max-jobs", "2"]
```

**docker-compose.yml** pour plusieurs serveurs :
```yaml
version: '3.8'

services:
  server1:
    build: .
    ports:
      - "8765:8765"
    environment:
      - SERVER_NAME=Server-1
    
  server2:
    build: .
    ports:
      - "8766:8765"
    environment:
      - SERVER_NAME=Server-2
    
  server3:
    build: .
    ports:
      - "8767:8765"
    environment:
      - SERVER_NAME=Server-3
```

### Étape 6 : Requirements Minimal

**requirements.txt** :
```
websockets>=10.0
psutil>=5.8.0
```

## Usage Simplifié

### Pour lancer le serveur :
```bash
# Local
python server.py --port 8765 &
python main.py  # Ajouter localhost:8765
```

### Pour utiliser le client :
```bash
# Lancer l'interface graphique normale
python main.py

# Dans l'interface : Menu → Outils → Serveurs d'Encodage
# Ajouter les IPs des serveurs (localhost:8765, 192.168.1.100:8765, etc.)
```

## Avantages de cette Approche

1. **🚀 Simple** : 2 fichiers principaux (main.py + server.py)
2. **📦 Portable** : Pas d'installation, juste `pip install -r requirements.txt`
3. **🐳 Docker Ready** : Containerisation facile
4. **🔧 Configurable** : Arguments en ligne de commande
5. **💻 Cross-platform** : Fonctionne partout où Python/FFmpeg tournent
6. **🔌 Plug & Play** : Ajouter/retirer des serveurs à la volée

## Tests Rapides

1. **Test local** :
   ```bash
   python server.py --port 8765 &
   python main.py  # Ajouter localhost:8765
   ```

2. **Test réseau** :
   ```bash
   # Sur machine A
   python server.py --port 8765 --name "Machine-A"
   
   # Sur machine B (avec IP de A)
   python main.py  # Ajouter 192.168.1.100:8765
   ```

3. **Test Docker** :
   ```bash
   docker-compose up -d
   python main.py  # Ajouter localhost:8765, :8766, :8767
   ```

### PHASE 4 : Interface Client GUI

#### 4.1 Gestionnaire de Serveurs (ffmpeg-gui/gui/server_manager_window.py)

**Objectif** : Interface pour ajouter des serveurs par IP, visualiser leurs capacités et gérer les connexions.

**Fonctionnalités clés** :
- **Ajout serveur par IP:port** : Interface simple de saisie
- **Test automatique capacités** : NVENC, QuickSync, VideoToolbox
- **Statut temps réel** : Charge, jobs actifs, disponibilité
- **Sélection serveur préféré** : Pour types de jobs spécifiques

#### 4.2 File d'Attente Jobs (ffmpeg-gui/gui/job_queue_window.py)

**Objectif** : Visualisation et gestion des jobs distribués avec possibilité de réassignation.

**Fonctionnalités clés** :
- **Liste jobs actifs** : En attente, en cours, terminés
- **Réassignation jobs** : Drag & drop vers autre serveur
- **Vérification compatibilité** : Alerte si encodeur non supporté
- **Historique performances** : Temps moyens par serveur

#### 4.3 Correspondance Capacités (ffmpeg-gui/core/capability_matcher.py)

**Objectif** : Algorithme intelligent de sélection du serveur optimal selon le job.

**Code détaillé d'implémentation** :
```python
from typing import List, Dict, Optional
from dataclasses import dataclass
from shared.messages import ServerInfo, JobConfiguration, CapabilityMatch, EncoderType

@dataclass
class ServerScore:
    """Score d'évaluation d'un serveur pour un job"""
    server_id: str
    compatibility_score: float  # 0.0 - 1.0
    performance_score: float    # 0.0 - 1.0  
    load_score: float          # 0.0 - 1.0 (1.0 = pas chargé)
    total_score: float         # Score combiné
    missing_capabilities: List[str]
    warnings: List[str]

class CapabilityMatcher:
    """Moteur de correspondance capacités serveur/job"""
    
    def __init__(self):
        # Poids pour calcul score final
        self.weights = {
            'compatibility': 0.5,  # Encodeurs supportés
            'performance': 0.3,    # Performance brute
            'load': 0.2           # Charge actuelle
        }
        
        # Préférences encodeurs par performance
        self.encoder_preferences = {
            # Hardware encoders (plus rapides)
            'h264_nvenc': 1.0,
            'hevc_nvenc': 1.0,
            'h264_qsv': 0.9,
            'hevc_qsv': 0.9,
            'h264_videotoolbox': 0.95,
            'hevc_videotoolbox': 0.95,
            'h264_amf': 0.85,
            'hevc_amf': 0.85,
            
            # Software encoders (plus lents mais universels)
            'libx264': 0.7,
            'libx265': 0.6,
            'libvpx': 0.5,
            'libvpx-vp9': 0.45,
        }
    
    def find_best_servers(self, job: JobConfiguration, 
                         available_servers: List[ServerInfo],
                         max_results: int = 3) -> List[CapabilityMatch]:
        """Trouve les meilleurs serveurs pour un job donné"""
        
        scores = []
        
        for server in available_servers:
            if server.status != 'online':
                continue
                
            score = self._evaluate_server(job, server)
            scores.append(score)
        
        # Trier par score décroissant
        scores.sort(key=lambda x: x.total_score, reverse=True)
        
        # Convertir en CapabilityMatch
        matches = []
        for score in scores[:max_results]:
            match = CapabilityMatch(
                server_id=score.server_id,
                compatibility_score=score.compatibility_score,
                missing_capabilities=score.missing_capabilities,
                performance_estimate=score.performance_score,
                recommended=score.total_score > 0.7
            )
            matches.append(match)
        
        return matches
    
    def _evaluate_server(self, job: JobConfiguration, server: ServerInfo) -> ServerScore:
        """Évalue un serveur pour un job spécifique"""
        
        # 1. Score de compatibilité
        compatibility_score, missing_caps = self._calculate_compatibility(job, server)
        
        # 2. Score de performance
        performance_score = self._calculate_performance(job, server)
        
        # 3. Score de charge
        load_score = self._calculate_load_score(server)
        
        # 4. Score total pondéré
        total_score = (
            compatibility_score * self.weights['compatibility'] +
            performance_score * self.weights['performance'] +
            load_score * self.weights['load']
        )
        
        # 5. Génération warnings
        warnings = self._generate_warnings(job, server, compatibility_score)
        
        return ServerScore(
            server_id=server.server_id,
            compatibility_score=compatibility_score,
            performance_score=performance_score,
            load_score=load_score,
            total_score=total_score,
            missing_capabilities=missing_caps,
            warnings=warnings
        )
    
    def _calculate_compatibility(self, job: JobConfiguration, 
                               server: ServerInfo) -> tuple[float, List[str]]:
        """Calcule le score de compatibilité et liste les capacités manquantes"""
        
        required_encoders = [job.encoder] + job.required_capabilities
        available_encoders = (
            server.capabilities.software_encoders +
            [enc for encoders in server.capabilities.hardware_encoders.values() 
             for enc in encoders]
        )
        
        missing = []
        supported = []
        
        for encoder in required_encoders:
            if encoder in available_encoders:
                supported.append(encoder)
            else:
                missing.append(encoder)
        
        # Score basé sur ratio supporté/requis
        if not required_encoders:
            compatibility_score = 1.0
        else:
            compatibility_score = len(supported) / len(required_encoders)
        
        return compatibility_score, missing
    
    def _calculate_performance(self, job: JobConfiguration, server: ServerInfo) -> float:
        """Calcule le score de performance basé sur l'encodeur et le matériel"""
        
        # Score de base du serveur
        base_score = min(server.capabilities.estimated_performance / 1000.0, 1.0)
        
        # Bonus selon type d'encodeur
        encoder_bonus = self.encoder_preferences.get(job.encoder, 0.5)
        
        # Bonus selon type d'encodeur disponible
        has_hardware = any(
            job.encoder in encoders 
            for encoders in server.capabilities.hardware_encoders.values()
        )
        hardware_bonus = 1.3 if has_hardware else 1.0
        
        # Score final
        performance_score = min(base_score * encoder_bonus * hardware_bonus, 1.0)
        
        return performance_score
    
    def _calculate_load_score(self, server: ServerInfo) -> float:
        """Calcule le score de charge (1.0 = pas chargé, 0.0 = saturé)"""
        
        if server.max_jobs == 0:
            return 0.0
        
        job_load = server.current_jobs / server.max_jobs
        cpu_load = getattr(server.capabilities, 'current_load', 0.5)
        
        # Moyenne pondérée
        combined_load = (job_load * 0.7) + (cpu_load * 0.3)
        
        return max(0.0, 1.0 - combined_load)
    
    def _generate_warnings(self, job: JobConfiguration, server: ServerInfo, 
                          compatibility_score: float) -> List[str]:
        """Génère des avertissements pour l'utilisateur"""
        
        warnings = []
        
        if compatibility_score < 1.0:
            warnings.append(f"Encodeur {job.encoder} non supporté sur ce serveur")
        
        if server.current_jobs >= server.max_jobs:
            warnings.append("Serveur actuellement saturé")
        
        if hasattr(server.capabilities, 'current_load') and server.capabilities.current_load > 0.9:
            warnings.append("Charge CPU élevée sur ce serveur")
        
        # Vérifier taille fichier
        if job.file_size > server.capabilities.max_file_size_gb * 1024**3:
            warnings.append("Fichier trop volumineux pour ce serveur")
        
        # Vérifier résolution
        resolution_limits = {
            '1080p': 1920 * 1080,
            '2K': 2560 * 1440, 
            '4K': 3840 * 2160,
            '8K': 7680 * 4320
        }
        
        job_pixels = self._parse_resolution(job.resolution)
        max_pixels = resolution_limits.get(server.capabilities.max_resolution, 1920*1080)
        
        if job_pixels > max_pixels:
            warnings.append(f"Résolution {job.resolution} peut être trop élevée")
        
        return warnings
    
    def _parse_resolution(self, resolution: str) -> int:
        """Parse une résolution type '1920x1080' en nombre de pixels"""
        try:
            if 'x' in resolution:
                w, h = resolution.split('x')
                return int(w) * int(h)
        except:
            pass
        return 1920 * 1080  # Fallback
    
    def suggest_alternatives(self, job: JobConfiguration, 
                           available_servers: List[ServerInfo]) -> List[str]:
        """Suggère des alternatives si aucun serveur compatible"""
        
        suggestions = []
        
        # Rechercher encodeurs similaires disponibles
        similar_encoders = {
            'h264_nvenc': ['libx264', 'h264_qsv', 'h264_videotoolbox'],
            'hevc_nvenc': ['libx265', 'hevc_qsv', 'hevc_videotoolbox'],
            'h264_videotoolbox': ['libx264', 'h264_nvenc', 'h264_qsv'],
            'libx264': ['h264_nvenc', 'h264_qsv', 'h264_videotoolbox']
        }
        
        alternatives = similar_encoders.get(job.encoder, [])
        
        for server in available_servers:
            available = (
                server.capabilities.software_encoders +
                [enc for encoders in server.capabilities.hardware_encoders.values() 
                 for enc in encoders]
            )
            
            for alt in alternatives:
                if alt in available:
                    suggestions.append(
                        f"Serveur {server.name} supporte {alt} (alternative à {job.encoder})"
                    )
                    break
        
        return suggestions[:3]  # Top 3 suggestions
```

### PHASE 5 : Interface de Gestion Complète

#### 5.1 Menu Principal Étendu (ffmpeg-gui/gui/main_window.py)

**Modifications à apporter** :
```python
def _create_menu(self):
    # Menu existant...
    
    # NOUVEAU: Menu Serveurs
    servers_menu = tk.Menu(self.menubar, tearoff=0)
    self.menubar.add_cascade(label="Serveurs", menu=servers_menu)
    
    servers_menu.add_command(label="Gestion Serveurs", 
                           command=self.open_server_manager)
    servers_menu.add_command(label="File d'Attente", 
                           command=self.open_job_queue)
    servers_menu.add_command(label="Capacités Serveurs", 
                           command=self.open_capability_viewer)
    servers_menu.add_separator()
    servers_menu.add_command(label="Test Connexions", 
                           command=self.test_all_servers)

def _create_status_bar(self):
    # Status bar étendu
    self.status_frame = ttk.Frame(self.root)
    self.status_frame.pack(side=tk.BOTTOM, fill=tk.X)
    
    # Status existant
    self.status_var = tk.StringVar(value="Prêt")
    self.status_label = ttk.Label(self.status_frame, textvariable=self.status_var)
    self.status_label.pack(side=tk.LEFT, padx=5)
    
    # NOUVEAU: Indicateur serveurs
    self.servers_var = tk.StringVar(value="Aucun serveur")
    self.servers_label = ttk.Label(self.status_frame, textvariable=self.servers_var)
    self.servers_label.pack(side=tk.RIGHT, padx=5)

def update_server_status(self, connected_count: int, total_jobs: int):
    """Met à jour l'affichage du statut des serveurs"""
    if connected_count == 0:
        self.servers_var.set("🔴 Aucun serveur")
    else:
        self.servers_var.set(f"🟢 {connected_count} serveur(s) - {total_jobs} jobs")
```

#### 5.2 Sélection Serveur par Job (modification job_edit_window.py)

**Ajouts à l'interface d'édition** :
```python
def _build_server_selection_frame(self):
    """Frame de sélection du serveur cible"""
    server_frame = ttk.LabelFrame(self.window, text="Serveur d'Encodage")
    server_frame.pack(fill=tk.X, padx=10, pady=5)
    
    # Auto-sélection (par défaut)
    self.server_mode_var = tk.StringVar(value="auto")
    
    auto_radio = ttk.Radiobutton(server_frame, text="Sélection automatique", 
                                variable=self.server_mode_var, value="auto",
                                command=self._on_server_mode_change)
    auto_radio.grid(row=0, column=0, sticky="w", padx=5, pady=2)
    
    manual_radio = ttk.Radiobutton(server_frame, text="Serveur spécifique:", 
                                  variable=self.server_mode_var, value="manual",
                                  command=self._on_server_mode_change)
    manual_radio.grid(row=1, column=0, sticky="w", padx=5, pady=2)
    
    # Dropdown serveurs disponibles
    self.server_var = tk.StringVar()
    self.server_combo = ttk.Combobox(server_frame, textvariable=self.server_var,
                                    state="readonly")
    self.server_combo.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
    
    # Indicateur compatibilité
    self.compatibility_var = tk.StringVar(value="")
    self.compatibility_label = ttk.Label(server_frame, 
                                        textvariable=self.compatibility_var,
                                        foreground="blue")
    self.compatibility_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=5)
    
    # Bouton test capacités
    test_btn = ttk.Button(server_frame, text="Tester Compatibilité",
                         command=self._test_compatibility)
    test_btn.grid(row=3, column=0, sticky="w", padx=5, pady=5)

def _on_server_mode_change(self):
    """Active/désactive la sélection manuelle"""
    if self.server_mode_var.get() == "auto":
        self.server_combo.config(state="disabled")
    else:
        self.server_combo.config(state="readonly")
        self._update_server_list()

def _test_compatibility(self):
    """Teste la compatibilité avec le serveur sélectionné"""
    if self.server_mode_var.get() == "auto":
        self.compatibility_var.set("✅ Sélection automatique activée")
        return
    
    server_name = self.server_var.get()
    if not server_name:
        self.compatibility_var.set("⚠️ Aucun serveur sélectionné")
        return
    
    # Test asynchrone de compatibilité
    encoder = self.global_encoder_var.get()
    asyncio.create_task(self._async_test_compatibility(server_name, encoder))

async def _async_test_compatibility(self, server_name: str, encoder: str):
    """Test asynchrone de compatibilité"""
    try:
        # Récupérer info serveur
        server_info = self.distributed_client.get_server_by_name(server_name)
        if not server_info:
            self.compatibility_var.set("❌ Serveur non trouvé")
            return
        
        # Tester compatibilité
        all_encoders = (
            server_info.capabilities.software_encoders +
            [enc for encs in server_info.capabilities.hardware_encoders.values() 
             for enc in encs]
        )
        
        if encoder in all_encoders:
            # Déterminer type d'encodeur
            hw_type = None
            for hw_vendor, hw_encoders in server_info.capabilities.hardware_encoders.items():
                if encoder in hw_encoders:
                    hw_type = hw_vendor.upper()
                    break
            
            if hw_type:
                self.compatibility_var.set(f"✅ Compatible ({hw_type})")
            else:
                self.compatibility_var.set("✅ Compatible (logiciel)")
        else:
            # Suggérer alternatives
            alternatives = self._find_encoder_alternatives(encoder, all_encoders)
            if alternatives:
                alt_text = ", ".join(alternatives[:2])
                self.compatibility_var.set(f"❌ Non compatible. Alternatives: {alt_text}")
            else:
                self.compatibility_var.set("❌ Aucun encodeur compatible")
                
    except Exception as e:
        self.compatibility_var.set(f"⚠️ Erreur test: {e}")

def _find_encoder_alternatives(self, target_encoder: str, available: List[str]) -> List[str]:
    """Trouve des alternatives à un encodeur"""
    alternatives_map = {
        'h264_nvenc': ['libx264', 'h264_qsv', 'h264_videotoolbox'],
        'hevc_nvenc': ['libx265', 'hevc_qsv', 'hevc_videotoolbox'], 
        'h264_videotoolbox': ['libx264', 'h264_nvenc'],
        'libx264': ['h264_nvenc', 'h264_qsv'],
        'libx265': ['hevc_nvenc', 'hevc_qsv']
    }
    
    possible_alts = alternatives_map.get(target_encoder, [])
    return [alt for alt in possible_alts if alt in available]
```

## Instructions de Mise en Œuvre pour un Agent de Codage

### Ordre d'Implémentation Recommandé

1. **COMMENCER PAR** : Créer la structure de dossiers séparée
2. **ENSUITE** : Implémenter shared/protocol.py et shared/messages.py
3. **PUIS** : Créer ffmpeg-server/core/hardware_detector.py
4. **APRÈS** : Implémenter ffmpeg-server/main.py et encode_server.py
5. **ENFIN** : Créer ffmpeg-gui avec gestionnaire distribué

### Étapes de Test Progressives

1. **Test capacités serveur** : `python ffmpeg-server/main.py --test-capabilities`
2. **Test serveur standalone** : `python ffmpeg-server/main.py --port 8765`
3. **Test connexion client** : Ajouter localhost:8765 dans l'interface
4. **Test job simple** : Encoder un petit fichier de test
5. **Test réassignation** : Changer serveur cible d'un job en attente

### Points d'Attention Critiques

#### Pour l'Agent de Codage :
- **TOUJOURS** valider la compatibilité encodeur avant soumission job
- **JAMAIS** supposer qu'un serveur supporte un encodeur sans test
- **IMPLÉMENTER** la gestion d'erreur robuste pour connexions réseau
- **TESTER** chaque composant individuellement avant intégration
- **DOCUMENTER** chaque méthode avec exemples d'usage

#### Gestion d'Erreurs Obligatoire :
```python
# Exemple pattern à suivre partout
try:
    result = await operation_risquee()
    if not result.success:
        self.logger.warning(f"Opération échouée: {result.error}")
        return ErrorResponse(result.error)
except ConnectionError as e:
    self.logger.error(f"Erreur connexion: {e}")
    return ErrorResponse("Serveur inaccessible")
except TimeoutError as e:
    self.logger.error(f"Timeout: {e}")  
    return ErrorResponse("Timeout serveur")
except Exception as e:
    self.logger.exception(f"Erreur inattendue: {e}")
    return ErrorResponse("Erreur interne")
```

## Résultats Attendus

### Côté Utilisateur
- Interface simple pour ajouter serveurs par IP
- Sélection automatique serveur optimal pour chaque job
- Alerte si encodeur demandé non disponible
- Réassignation facile des jobs entre serveurs
- Visualisation temps réel statut tous serveurs

### Côté Performance  
- Distribution intelligente selon capacités matérielles
- Utilisation optimale encodeurs hardware (NVENC, etc.)
- Load balancing automatique entre serveurs
- Fallback local si aucun serveur disponible

### Côté Fiabilité
- Détection automatique déconnexions serveur
- Reconnexion automatique avec retry
- Validation compatibilité avant soumission
- Gestion propre annulation/réassignation jobs

Cette architecture complète permet un déploiement simple (juste lancer les fichiers Python) tout en offrant toute la robustesse nécessaire pour un usage professionnel. 