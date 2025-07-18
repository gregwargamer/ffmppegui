import subprocess
import sys
from tkinter import messagebox
import re

class FFmpegHelpers:
    """Utility functions to query ffmpeg."""

    _encoders_cache = None
    _codecs_cache = None
    _hw_encoders_cache = None

    @classmethod
    def available_encoders(cls):
        if cls._encoders_cache is None:
            try:
                result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=True)
                encoders = []
                codec_pattern = re.compile(r'\(codec (\w+)\)')
                for line in result.stdout.splitlines():
                    # On ne s'intéresse qu'aux lignes qui décrivent un encodeur
                    if not line.strip() or line.startswith("="):
                        continue
                    
                    # V..... = Video, A..... = Audio, S..... = Subtitle
                    if line[1] == 'V' or line[1] == 'A' or line[1] == 'S':
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            encoder_name = parts[1]
                            description = ' '.join(parts[2:])
                            
                            match = codec_pattern.search(description)
                            implemented_codec = match.group(1) if match else None
                            
                            # Si le codec n'est pas explicitement listé, on essaie de le deviner
                            if not implemented_codec:
                                if "h264" in encoder_name: implemented_codec = "h264"
                                elif "h265" in encoder_name or "hevc" in encoder_name: implemented_codec = "hevc"
                                elif "av1" in encoder_name: implemented_codec = "av1"
                                elif "vp9" in encoder_name: implemented_codec = "vp9"
                                elif "webp" in encoder_name: implemented_codec = "webp"
                                elif "jpegxl" in encoder_name or "jxl" in encoder_name: implemented_codec = "jpegxl"
                                elif "heic" in encoder_name or "hevc_image" in encoder_name: implemented_codec = "heic"
                                elif "aac" in encoder_name: implemented_codec = "aac"
                                elif "mp3" in encoder_name: implemented_codec = "mp3lame" # ou mp3

                            encoders.append({
                                "name": encoder_name,
                                "description": description,
                                "codec": implemented_codec
                            })

                cls._encoders_cache = encoders
            except (FileNotFoundError, subprocess.CalledProcessError):
                cls._encoders_cache = []
        return cls._encoders_cache

    @classmethod
    def is_hardware_encoder(cls, encoder_name: str) -> bool:
        """Détermine si un encodeur utilise l'accélération hardware"""
        hw_patterns = [
            # NVIDIA
            'nvenc', 'h264_nvenc', 'hevc_nvenc', 'av1_nvenc',
            # AMD
            'amf', 'h264_amf', 'hevc_amf',
            # Intel QuickSync
            'qsv', 'h264_qsv', 'hevc_qsv', 'av1_qsv',
            # Apple VideoToolbox
            'videotoolbox', 'h264_videotoolbox', 'hevc_videotoolbox',
            # ARM Mali
            'v4l2m2m',
            # Other hardware
            'vaapi', 'vdpau', 'mediacodec'
        ]
        return any(pattern in encoder_name.lower() for pattern in hw_patterns)

    @classmethod
    def get_hardware_encoders(cls):
        """Retourne uniquement les encodeurs hardware disponibles"""
        if cls._hw_encoders_cache is None:
            all_encoders = cls.available_encoders()
            # La structure a changé en dictionnaire
            cls._hw_encoders_cache = [encoder for encoder in all_encoders 
                                     if cls.is_hardware_encoder(encoder["name"])]
        return cls._hw_encoders_cache

    @classmethod
    def available_codecs(cls):
        if cls._codecs_cache is None:
            try:
                result = subprocess.run(["ffmpeg", "-hide_banner", "-codecs"], capture_output=True, text=True, check=True)
                video, audio, image = set(), set(), set()
                
                # Regex pour extraire le nom du codec
                codec_line_re = re.compile(r"^\s(?:D|E|\.)(V|A|S|\.)(?:F|\.)(?:S|\.)(?:D|\.)(?:T|\.)(?:I|\.)\s+(\w+)")

                for line in result.stdout.splitlines():
                    match = codec_line_re.match(line)
                    if match:
                        type_flag, name = match.groups()
                        if type_flag == 'V':
                            video.add(name)
                        elif type_flag == 'A':
                            audio.add(name)
                        elif type_flag == 'S':
                             # Pour l'instant on considère les sous-titres comme des 'images' pour le UI
                            image.add(name)
                
                # Ajout manuel des formats d'image courants qui peuvent ne pas être listés comme des codecs traditionnels
                image.update(["png", "mjpeg", "jpg", "webp", "tiff", "bmp", "gif", "avif", "jpegxl", "heic"])

                cls._codecs_cache = {
                    "video": sorted(list(video)) or ["h264", "hevc", "vp9", "av1", "mpeg4"],
                    "audio": sorted(list(audio)) or ["aac", "mp3", "opus", "flac"],
                    "image": sorted(list(image)) or ["webp", "png", "jpeg", "bmp", "jpegxl", "heic"]
                }
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                messagebox.showerror("Erreur FFmpeg", f"Impossible de lister les codecs: {e}")
                cls._codecs_cache = {"video": [], "audio": [], "image": []}
        return cls._codecs_cache
