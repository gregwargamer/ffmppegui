from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any
import json
from pathlib import Path

@dataclass
class DistributedSettings:
    auto_connect_servers: List[Dict[str, Any]] = field(default_factory=list)
    default_timeout: int = 30
    max_concurrent_jobs: int = 10
    preferred_encoders: List[str] = field(default_factory=lambda: ["h264_nvenc", "libx264"])

@dataclass
class UISettings:
    refresh_interval: int = 5
    show_server_details: bool = True
    auto_select_best_server: bool = True

@dataclass
class Settings:
    distributed: DistributedSettings = field(default_factory=DistributedSettings)
    ui: UISettings = field(default_factory=UISettings)
    
    def __post_init__(self):
        # Compatibilité avec l'ancien système de settings
        self.data = {
            "presets": {},
            "keep_folder_structure": True
        }
    
    def save(self):
        """Méthode pour sauvegarder les paramètres"""
        save_settings(self)

def load_settings(file_path: Path = Path("settings.json")) -> Settings:
    settings = Settings()
    if file_path.exists():
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
                if "distributed" in data:
                    settings.distributed = DistributedSettings(**data["distributed"])
                if "ui" in data:
                    settings.ui = UISettings(**data["ui"])
        except Exception as e:
            print(f"Error loading settings from {file_path}: {e}")
    return settings

def save_settings(settings: Settings, file_path: Path = Path("settings.json")):
    try:
        with open(file_path, 'w') as f:
            json.dump(asdict(settings), f, indent=4)
    except Exception as e:
        print(f"Error saving settings to {file_path}: {e}")