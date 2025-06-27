import asyncio
import logging
import time
from pathlib import Path
from typing import IO

from shared.protocol import Message, MessageType, send_message

class FileManager:
    """Gère la réception et l'envoi de fichiers par chunks."""
    
    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir
        self.active_uploads: dict[str, IO] = {}
        self.logger = logging.getLogger(__name__)

    async def receive_file_chunk(self, job_id: str, chunk: bytes):
        """Reçoit un chunk de fichier et l'écrit sur le disque."""
        if job_id not in self.active_uploads:
            # First chunk, open the file for writing
            file_path = self.temp_dir / f"{job_id}_input"
            self.active_uploads[job_id] = file_path.open("wb")
            self.logger.info(f"Début réception fichier pour job {job_id}")
        
        self.active_uploads[job_id].write(chunk)

    def finish_upload(self, job_id: str) -> Path:
        """Finalise la réception d'un fichier."""
        if job_id in self.active_uploads:
            self.active_uploads[job_id].close()
            del self.active_uploads[job_id]
            self.logger.info(f"Fichier pour job {job_id} reçu.")
            return self.temp_dir / f"{job_id}_input"
        raise FileNotFoundError(f"No active upload found for job {job_id}")

    async def send_file(self, websocket, file_path: Path, job_id: str):
        """Envoie un fichier par chunks via WebSocket."""
        if not file_path.exists():
            raise FileNotFoundError(f"Fichier à envoyer non trouvé: {file_path}")

        file_size = file_path.stat().st_size
        self.logger.info(f"Début envoi fichier résultat pour job {job_id} ({file_size} octets)")

        start_msg = Message(MessageType.FILE_DOWNLOAD_START, {
            'job_id': job_id,
            'file_name': file_path.name,
            'file_size': file_size
        })
        await send_message(websocket, start_msg)

        chunk_size = 1024 * 1024  # 1MB
        try:
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    chunk_msg = Message(MessageType.FILE_CHUNK, {'job_id': job_id, 'chunk': chunk})
                    await send_message(websocket, chunk_msg)
            self.logger.info(f"Fichier résultat pour job {job_id} envoyé.")
        except Exception as e:
            self.logger.error(f"Erreur envoi fichier pour job {job_id}: {e}")
            raise

    async def cleanup_old_files(self, age_hours: int = 24):
        """Supprime les fichiers temporaires plus anciens que age_hours."""
        now = time.time()
        for f in self.temp_dir.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > (age_hours * 3600):
                try:
                    f.unlink()
                    self.logger.info(f"Fichier temporaire supprimé: {f.name}")
                except Exception as e:
                    self.logger.warning(f"Impossible de supprimer {f.name}: {e}")
