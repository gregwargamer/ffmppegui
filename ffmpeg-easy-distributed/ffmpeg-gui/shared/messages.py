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
