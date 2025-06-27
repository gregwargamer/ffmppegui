import argparse
from pathlib import Path

class ServerConfig:
    """Gère la configuration du serveur d'encodage"""
    
    def __init__(self, host: str, port: int, max_jobs: int, max_file_size: str, name: str = None, temp_dir: str = None):
        self.host = host
        self.port = port
        self.max_jobs = max_jobs
        self.max_file_size = max_file_size
        self.name = name
        self.temp_dir = Path(temp_dir) if temp_dir else Path.home() / ".ffmpeg_easy_server_temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        self.max_file_size_bytes = self._parse_file_size(max_file_size)
        
    @classmethod
    def from_args(cls, args: argparse.Namespace) -> 'ServerConfig':
        """Crée une configuration à partir des arguments de ligne de commande"""
        return cls(
            host=args.host,
            port=args.port,
            max_jobs=args.max_jobs,
            max_file_size=args.max_file_size,
            name=args.name,
            temp_dir=args.temp_dir
        )
        
    def _parse_file_size(self, size_str: str) -> int:
        """Parse une chaîne de taille de fichier (ex: '10GB') en octets"""
        size_str = size_str.strip().upper()
        if size_str.endswith('KB'):
            return int(float(size_str[:-2]) * 1024)
        elif size_str.endswith('MB'):
            return int(float(size_str[:-2]) * 1024**2)
        elif size_str.endswith('GB'):
            return int(float(size_str[:-2]) * 1024**3)
        elif size_str.endswith('TB'):
            return int(float(size_str[:-2]) * 1024**4)
        else:
            return int(size_str)
