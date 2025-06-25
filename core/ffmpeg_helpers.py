import subprocess
import sys
from tkinter import messagebox

class FFmpegHelpers:
    """Utility functions to query ffmpeg."""

    _encoders_cache = None
    _codecs_cache = None
    _hw_encoders_cache = None

    @classmethod
    def available_encoders(cls):
        if cls._encoders_cache is None:
            try:
                result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
                encoders = []
                for line in result.stdout.split('\n'):
                    if line.startswith(' V') or line.startswith(' A'):
                        parts = line.split()
                        if len(parts) >= 3:
                            encoder_name = parts[1]
                            description = ' '.join(parts[2:])
                            # Taguer les encodeurs hardware
                            if cls.is_hardware_encoder(encoder_name):
                                description += " (Hardware)"
                            encoders.append((encoder_name, description))
                cls._encoders_cache = encoders
            except FileNotFoundError:
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
            cls._hw_encoders_cache = [(name, desc) for name, desc in all_encoders 
                                     if cls.is_hardware_encoder(name)]
        return cls._hw_encoders_cache

    @classmethod
    def available_codecs(cls):
        if cls._codecs_cache is None:
            try:
                result = subprocess.run(["ffmpeg", "-hide_banner", "-codecs"], capture_output=True, text=True)
                video, audio, image = set(), set(), set()
                for line in result.stdout.splitlines():
                    if line.startswith(" ") and "(" not in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            flags, name = parts[0], parts[1]
                            if "V" in flags:
                                video.add(name)
                            elif "A" in flags:
                                audio.add(name)
                            # Accept more for image: known image codecs/extensions
                            if name.lower() in {"jpg", "jpeg", "webp", "png", "avif", "tiff", "bmp", "jxl", "jpegxl"}:
                                image.add(name)
                            elif "S" in flags or "I" in flags:
                                image.add(name)
                # Add common image codecs if missing
                for extra in ["jpg", "jpeg", "webp", "png", "avif", "tiff", "bmp", "jxl", "jpegxl"]:
                    image.add(extra)
                cls._codecs_cache = {
                    "video": sorted(video),
                    "audio": sorted(audio),
                    "image": sorted(image)
                }
            except FileNotFoundError:
                messagebox.showerror("FFmpeg not found", "ffmpeg executable not found in PATH.")
                sys.exit(1)
        return cls._codecs_cache
