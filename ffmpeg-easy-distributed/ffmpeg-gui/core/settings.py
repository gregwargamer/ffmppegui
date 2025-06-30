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
    #intervalle de rafraîchissement de la boucle Tkinter/asyncio (ms)
    tk_loop_interval_ms: int = 50

@dataclass
class DistributedSettings:
    auto_connect_servers: List[Dict[str, Any]] = field(default_factory=list, init=False)
    default_timeout: int = 30
    max_concurrent_jobs: int = 10
    preferred_encoders: List[str] = field(default_factory=lambda: ["h264_nvenc", "libx264"])
    max_reconnect_attempts: int = 10
    #délai initial (s) avant première tentative de reconnexion
    reconnect_initial_delay: int = 5
    #délai maximal (s) entre deux essais (cap du back-off)
    reconnect_max_delay: int = 60
    #délai d'attente (s) pour les réponses ping/pong
    ping_timeout: int = 5
    #poids utilisés par le CapabilityMatcher (0.0-1.0) – doivent totaliser 1.0
    matcher_weights: Dict[str, float] = field(default_factory=lambda: {
        "compatibility": 0.5,
        "performance": 0.3,
        "load": 0.2,
    })

@dataclass
class EncodingDefaults:
    default_video_encoder: str = "libx264"
    default_audio_encoder: str = "aac"
    default_image_encoder: str = "png"
    custom_flags: str = ""

@dataclass
class CodecInfo:
    video: List[Dict[str, Any]] = field(default_factory=list)
    audio: List[Dict[str, Any]] = field(default_factory=list)
    image: List[Dict[str, Any]] = field(default_factory=list)

@dataclass
class Settings:
    concurrency: Concurrency = field(default_factory=Concurrency)
    ui: UISettings = field(default_factory=UISettings)
    distributed: DistributedSettings = field(default_factory=DistributedSettings)
    filename_template: FilenameTemplate = field(default_factory=FilenameTemplate)
    encoding_defaults: EncodingDefaults = field(default_factory=EncodingDefaults)
    codec_info: CodecInfo = field(default_factory=CodecInfo, init=False)
    keep_folder_structure: bool = True
    presets: Dict[str, Any] = field(default_factory=dict, init=False)
    
    def __post_init__(self):
        self.codec_info = load_codec_info()
        self.presets = load_presets()
        self.distributed.auto_connect_servers = load_servers()

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

def load_codec_info(file_path: Path = Path("codecs.json")) -> CodecInfo:
    """Charge les informations sur les codecs depuis un fichier JSON"""
    if not file_path.exists():
        print(f"Fichier codecs.json non trouvé à {file_path}. Utilisation de valeurs vides.")
        return CodecInfo()
    try:
        with file_path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        return CodecInfo(**data)
    except (json.JSONDecodeError, TypeError) as e:
        print(f"Erreur lors du chargement de {file_path}: {e}. Utilisation de valeurs vides.")
        return CodecInfo()

def load_presets(file_path: Path = Path("presets.json")) -> Dict[str, Any]:
    """Charge les presets depuis un fichier JSON"""
    if not file_path.exists():
        print(f"Fichier presets.json non trouvé à {file_path}. Aucun preset chargé.")
        return {}
    try:
        with file_path.open('r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, TypeError) as e:
        print(f"Erreur lors du chargement de {file_path}: {e}. Aucun preset chargé.")
        return {}

def save_presets(presets: Dict[str, Any], file_path: Path = Path("presets.json")):
    """Sauvegarde les presets dans un fichier JSON"""
    try:
        with file_path.open('w', encoding='utf-8') as f:
            json.dump(presets, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Erreur lors de la sauvegarde des presets vers {file_path}: {e}")

def load_servers(file_path: Path = Path("servers.json")) -> List[Dict[str, Any]]:
    """Charge la liste des serveurs depuis un fichier JSON"""
    if not file_path.exists():
        print(f"Fichier servers.json non trouvé à {file_path}. Aucune serveur chargé.")
        return []
    try:
        with file_path.open('r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, TypeError) as e:
        print(f"Erreur lors du chargement de {file_path}: {e}. Aucun serveur chargé.")
        return []

def save_servers(servers: List[Dict[str, Any]], file_path: Path = Path("servers.json")):
    """Sauvegarde la liste des serveurs dans un fichier JSON"""
    try:
        with file_path.open('w', encoding='utf-8') as f:
            json.dump(servers, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Erreur lors de la sauvegarde des serveurs vers {file_path}: {e}")

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
            # On charge tout sauf la liste des serveurs qui est maintenant dans servers.json
            if "auto_connect_servers" in data["distributed"]:
                del data["distributed"]["auto_connect_servers"]
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
        
        return settings
        
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        print(f"Erreur lors du chargement des settings depuis {file_path}: {e}. Utilisation des valeurs par défaut.")
        return Settings()

def save_settings(settings: Settings, file_path: Path = Path("settings.json")):
    """Sauvegarde les paramètres dans un fichier JSON"""
    try:
        # Préparez les données à sauvegarder, sans les presets et serveurs
        settings_dict = asdict(settings)
        del settings_dict['presets']
        if 'distributed' in settings_dict and 'auto_connect_servers' in settings_dict['distributed']:
            del settings_dict['distributed']['auto_connect_servers']
        
        with file_path.open('w', encoding='utf-8') as f:
            json.dump(settings_dict, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Erreur lors de la sauvegarde des settings vers {file_path}: {e}")