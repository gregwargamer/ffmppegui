import subprocess
from pathlib import Path

class EncodeJob:
    def __init__(self, src_path: Path, dst_path: Path, mode: str):
        self.src_path = src_path
        self.dst_path = dst_path
        self.mode = mode
        self.encoder: str = ""
        self.quality: str = ""
        self.cq_value: str = ""
        self.preset: str = ""
        self.custom_flags: str = ""
        self.status: str = "pending"  # pending, running, paused, done, error, cancelled
        self.progress: float = 0.0
        self.duration: float = None
        self.process: subprocess.Popen = None
        self.is_paused: bool = False
        self.is_cancelled: bool = False
        
        # Nouveaux paramètres d'encodage
        self.video_mode: str = "quality"  # quality, bitrate
        self.bitrate: str = ""  # Pour l'encodage basé sur le bitrate
        self.multipass: bool = False  # Multi-pass encoding
        self.longest_side: str = ""  # Pour les images
        self.megapixels: str = ""  # Pour les images
        
        # Filtres avancés
        self.filters = {
            "brightness": 0,    # -100 à 100
            "contrast": 0,      # -100 à 100
            "saturation": 0,    # -100 à 100
            "gamma": 1.0,       # 0.1 à 3.0
            "hue": 0,          # -180 à 180
            "sharpness": 0,     # -10 à 10
            "noise_reduction": 0, # 0 à 100
            "scale_width": 0,   # 0 = pas de resize
            "scale_height": 0,  # 0 = pas de resize
            "crop_x": 0,
            "crop_y": 0,
            "crop_w": 0,
            "crop_h": 0,
            "rotate": 0,        # 0, 90, 180, 270
            "flip_h": False,    # flip horizontal
            "flip_v": False,    # flip vertical
        }
        
        # Configuration des pistes audio
        self.audio_config = {
            "mode": "auto",  # auto, copy, encode, remove
            "selected_tracks": [],  # Liste des indices de pistes à garder
            "audio_codec": "aac",
            "audio_bitrate": "128k"
        }

    def cancel(self):
        """Annule le job et termine le processus FFmpeg"""
        self.is_cancelled = True
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.status = "cancelled"

    def pause(self):
        """Met en pause le job (suspend le processus)"""
        if self.process and self.process.poll() is None and not self.is_paused:
            try:
                import signal
                self.process.send_signal(signal.SIGSTOP)
                self.is_paused = True
                self.status = "paused"
            except:
                pass

    def resume(self):
        """Reprend le job en pause"""
        if self.process and self.process.poll() is None and self.is_paused:
            try:
                import signal
                self.process.send_signal(signal.SIGCONT)
                self.is_paused = False
                self.status = "running"
            except:
                pass

    def __str__(self):
        return f"EncodeJob({self.src_path.name} -> {self.dst_path.name}, {self.status})"
