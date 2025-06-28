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
                encoder_line_re = re.compile(r"^\s([VAS])([.FSXBD]{5})\s+(\w+)\s+(.*)$")
                for line in result.stdout.splitlines():
                    match = encoder_line_re.match(line)
                    if match:
                        type_flag, flags, encoder_name, description = match.groups()
                        
                        match = codec_pattern.search(description)
                        implemented_codec = match.group(1) if match else None
                        
                        if not implemented_codec:
                            if "h264" in encoder_name: implemented_codec = "h264"
                            elif "h265" in encoder_name or "hevc" in encoder_name: implemented_codec = "hevc"
                            elif "av1" in encoder_name: implemented_codec = "av1"
                            elif "vp9" in encoder_name: implemented_codec = "vp9"
                            elif "webp" in encoder_name: implemented_codec = "webp"
                            elif "jpegxl" in encoder_name or "jxl" in encoder_name: implemented_codec = "jpegxl"
                            elif "heic" in encoder_name or "hevc_image" in encoder_name: implemented_codec = "heic"
                            elif "aac" in encoder_name: implemented_codec = "aac"
                            elif "mp3" in encoder_name: implemented_codec = "mp3" # Use "mp3" to match available_codecs
                            elif "flac" == encoder_name: implemented_codec = "flac"
                            elif "alac" == encoder_name: implemented_codec = "alac"
                            elif "pcm_s16le" == encoder_name: implemented_codec = "pcm_s16le"
                            elif "opus" in encoder_name: implemented_codec = "opus" # e.g. libopus
                            elif "vorbis" in encoder_name: implemented_codec = "vorbis" # e.g. libvorbis

                        encoders.append({
                            "name": encoder_name,
                            "description": description,
                            "codec": implemented_codec
                        })
                cls._encoders_cache = encoders
            except Exception as e:
                print(f"Échec de la récupération des encodeurs depuis ffmpeg : {e}")
                print("Utilisation d'une liste d'encodeurs par défaut.")
                cls._encoders_cache = [
                    # Vidéo
                    {"name": "libx264", "description": "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10", "codec": "h264"},
                    {"name": "libx265", "description": "H.265 / HEVC", "codec": "hevc"},
                    {"name": "libvpx-vp9", "description": "VP9", "codec": "vp9"},
                    {"name": "libaom-av1", "description": "AV1", "codec": "av1"},
                    # Audio
                    {"name": "aac", "description": "AAC (Advanced Audio Coding)", "codec": "aac"},
                    {"name": "libmp3lame", "description": "MP3 (MPEG audio layer 3)", "codec": "mp3"},
                    {"name": "flac", "description": "FLAC (Free Lossless Audio Codec)", "codec": "flac"},
                    {"name": "opus", "description": "Opus", "codec": "opus"},
                    {"name": "libvorbis", "description": "Vorbis", "codec": "vorbis"},
                    {"name": "pcm_s16le", "description": "PCM signed 16-bit little-endian", "codec": "pcm_s16le"},
                    {"name": "pcm_alaw", "description": "PCM A-law", "codec": "pcm_alaw"},
                    {"name": "pcm_mulaw", "description": "PCM mu-law", "codec": "pcm_mulaw"},
                    # Image
                    {"name": "libwebp", "description": "WebP", "codec": "webp"},
                    {"name": "png", "description": "PNG (Portable Network Graphics) image", "codec": "png"},
                    {"name": "mjpeg", "description": "MJPEG (Motion JPEG)", "codec": "mjpeg"},
                ]
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
                    "video": ["h264", "hevc", "vp9", "av1"] + sorted([x for x in list(video) if x not in ["h264", "hevc", "vp9", "av1"]]) or ["h264", "hevc", "vp9", "av1", "mpeg4"],
                    "audio": ["aac", "mp3", "opus", "flac"] + sorted([x for x in list(audio) if x not in ["aac", "mp3", "opus", "flac"]]) or ["aac", "mp3", "opus", "flac"],
                    "image": ["webp", "png", "jpeg", "avif", "jpegxl", "heic"] + sorted([x for x in list(image) if x not in ["webp", "png", "jpeg", "avif", "jpegxl", "heic"]]) or ["webp", "png", "jpeg", "bmp", "jpegxl", "heic"]
                }
            except (FileNotFoundError, subprocess.CalledProcessError) as e:
                messagebox.showerror("Erreur FFmpeg", f"Impossible de lister les codecs: {e}")
                cls._codecs_cache = {"video": [], "audio": [], "image": []}
        return cls._codecs_cache 