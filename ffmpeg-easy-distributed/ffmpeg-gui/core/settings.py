from dataclasses import dataclass, field, asdict
import json
from pathlib import Path
from typing import Dict, Any, List, Optional

@dataclass
class FilenameTemplate:
    template: str = "{nom_source}-{resolution}.{container_ext}"

@dataclass
class Concurrency:
    global_jobs: int = 4
    video_jobs: int = 2

@dataclass
class UISettings:
    refresh_interval: int = 5
    show_server_details: bool = True
    auto_select_best_server: bool = True
    progress_refresh_interval: int = 2
    default_resolution: Optional[str] = None

@dataclass
class DistributedSettings:
    auto_connect_servers: List[Dict[str, Any]] = field(default_factory=list)
    default_timeout: int = 30
    max_concurrent_jobs: int = 10
    preferred_encoders: List[str] = field(default_factory=lambda: ["h264_nvenc", "libx264"])

@dataclass
class EncodingDefaults:
    default_video_encoder: str = "libx264"
    default_audio_encoder: str = "aac"
    default_image_encoder: str = "png"
    custom_flags: str = ""

@dataclass
class Settings:
    concurrency: Concurrency = field(default_factory=Concurrency)
    ui: UISettings = field(default_factory=UISettings)
    distributed: DistributedSettings = field(default_factory=DistributedSettings)
    filename_template: FilenameTemplate = field(default_factory=FilenameTemplate)
    encoding_defaults: EncodingDefaults = field(default_factory=EncodingDefaults)
    keep_folder_structure: bool = True
    presets: Dict[str, Any] = field(default_factory=dict)
    auto_connect_servers: List[Dict[str, Any]] = field(default_factory=list)
    
    def save(self, file_path: Path = Path("settings.json")):
        """Sauvegarde les paramètres dans un fichier JSON"""
        save_settings(self, file_path)
    
    # Propriétés de compatibilité pour l'ancien système
    @property
    def data(self) -> Dict[str, Any]:
        """Compatibilité avec l'ancien système settings.data"""
        return {
            "presets": self.presets,
            "keep_folder_structure": self.keep_folder_structure,
            "concurrency": self.concurrency.global_jobs,
            "video_concurrency": self.concurrency.video_jobs,
            "progress_refresh_interval": self.ui.progress_refresh_interval,
            "default_video_encoder": self.encoding_defaults.default_video_encoder,
            "default_audio_encoder": self.encoding_defaults.default_audio_encoder,
            "default_image_encoder": self.encoding_defaults.default_image_encoder,
            "custom_flags": self.encoding_defaults.custom_flags,
            "filename_template": self.filename_template.template,
        }

def load_settings(file_path: Path = Path("settings.json")) -> Settings:
    """Charge les paramètres depuis un fichier JSON"""
    if not file_path.exists():
        return Settings()
    
    try:
        with file_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Migration des anciens paramètres
        settings = Settings()
        
        # Charger les nouveaux paramètres structurés s'ils existent
        if "concurrency" in data and isinstance(data["concurrency"], dict):
            settings.concurrency = Concurrency(**data["concurrency"])
        elif "concurrency" in data:
            # Ancien format : nombre simple
            settings.concurrency.global_jobs = data["concurrency"]
            settings.concurrency.video_jobs = data.get("video_concurrency", 2)
        
        if "ui" in data:
            settings.ui = UISettings(**data["ui"])
        
        if "distributed" in data:
            settings.distributed = DistributedSettings(**data["distributed"])
        
        if "filename_template" in data and isinstance(data["filename_template"], dict):
            settings.filename_template = FilenameTemplate(**data["filename_template"])
        elif "filename_template" in data:
            settings.filename_template.template = data["filename_template"]
        
        if "encoding_defaults" in data:
            settings.encoding_defaults = EncodingDefaults(**data["encoding_defaults"])
        else:
            # Migrer les anciens paramètres d'encodage
            settings.encoding_defaults.default_video_encoder = data.get("default_video_encoder", "libx264")
            settings.encoding_defaults.default_audio_encoder = data.get("default_audio_encoder", "aac")
            settings.encoding_defaults.default_image_encoder = data.get("default_image_encoder", "png")
            settings.encoding_defaults.custom_flags = data.get("custom_flags", "")
        
        # Autres paramètres
        settings.keep_folder_structure = data.get("keep_folder_structure", True)
        settings.presets = data.get("presets", {})
        settings.auto_connect_servers = data.get("auto_connect_servers", [])
        
        return settings
        
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        print(f"Erreur lors du chargement des settings depuis {file_path}: {e}. Utilisation des valeurs par défaut.")
        return Settings()

def save_settings(settings: Settings, file_path: Path = Path("settings.json")):
    """Sauvegarde les paramètres dans un fichier JSON"""
    try:
        with file_path.open('w', encoding='utf-8') as f:
            json.dump(asdict(settings), f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Erreur lors de la sauvegarde des settings vers {file_path}: {e}")