# Détecteur matériel partagé entre GUI et serveur

import subprocess
import platform
import psutil
import re
import logging
from typing import Dict, List

#this part do that
#Tentative d'import du module partagé quelle que soit la racine d'exécution (GUI ou serveur)
try:
    from shared.messages import ServerCapabilities  # type: ignore
except ImportError:  # fallback si 'shared' n'est pas dans sys.path
    import sys, os, importlib
    current_dir = os.path.dirname(__file__)
    for sub in ("ffmpeg-gui", "ffmpeg-server"):
        candidate = os.path.abspath(os.path.join(current_dir, os.pardir, sub))
        if candidate not in sys.path:
            sys.path.append(candidate)
    from shared.messages import ServerCapabilities  # type: ignore


class HardwareDetector:
    """Détecteur de capacités matérielles et logicielles (commun)"""

    def __init__(self):
        self.os_type = platform.system().lower()
        self.logger = logging.getLogger(__name__)

    # === Point d'entrée principal ===

    def detect_all_capabilities(self) -> ServerCapabilities:
        """Retourne un objet ServerCapabilities décrivant le système."""

        self.logger.info("🔍 Détection des capacités du serveur…")

        ffmpeg_info = self._detect_ffmpeg_capabilities()
        hardware_encoders = self._detect_hardware_encoders()
        performance_score = self._estimate_performance()

        capabilities = ServerCapabilities(
            hostname=platform.node(),
            os=f"{platform.system()} {platform.release()}",
            cpu_cores=psutil.cpu_count(),
            memory_gb=round(psutil.virtual_memory().total / (1024 ** 3), 1),
            disk_space_gb=round(psutil.disk_usage('/').free / (1024 ** 3), 1),
            software_encoders=ffmpeg_info['software'],
            hardware_encoders=hardware_encoders,
            estimated_performance=performance_score,
            current_load=psutil.cpu_percent(interval=1) / 100.0,
            max_resolution=self._detect_max_resolution(),
            supported_formats=ffmpeg_info['formats'],
            max_file_size_gb=100.0,
        )

        total_hw = sum(len(encoders) for encoders in capabilities.hardware_encoders.values())
        self.logger.info(
            f"✅ Capacités détectées: {len(capabilities.software_encoders)} logiciels, {total_hw} matériels")

        return capabilities

    # === FFmpeg ===

    def _detect_ffmpeg_capabilities(self) -> Dict[str, List[str]]:
        try:
            version_res = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=10)
            if version_res.returncode != 0:
                raise RuntimeError("FFmpeg non disponible")

            encoders_res = subprocess.run(['ffmpeg', '-encoders'], capture_output=True, text=True, timeout=10)
            formats_res = subprocess.run(['ffmpeg', '-formats'], capture_output=True, text=True, timeout=10)

            software_encoders = self._parse_software_encoders(encoders_res.stdout)
            supported_formats = self._parse_formats(formats_res.stdout)

            return {
                'software': software_encoders,
                'formats': supported_formats,
            }
        except (subprocess.TimeoutExpired, FileNotFoundError, RuntimeError) as e:
            self.logger.error(f"❌ Erreur détection FFmpeg: {e}")
            return {'software': [], 'formats': []}

    def _parse_software_encoders(self, enc_output: str) -> List[str]:
        patterns = {
            # vidéos
            'libx264': r'libx264.*H\.264',
            'libx265': r'libx265.*H\.265',
            'libvpx': r'libvpx.*VP8',
            'libvpx-vp9': r'libvpx-vp9.*VP9',
            'libaom-av1': r'libaom-av1.*AV1',
            'libsvtav1': r'libsvtav1.*AV1',
            # audio
            'aac': r'aac.*AAC',
            'libfdk_aac': r'libfdk_aac.*AAC',
            'libmp3lame': r'libmp3lame.*MP3',
            'libopus': r'libopus.*Opus',
            'libvorbis': r'libvorbis.*Vorbis',
            'flac': r'flac.*FLAC',
        }
        return [enc for enc, pat in patterns.items() if re.search(pat, enc_output, re.IGNORECASE)]

    def _parse_formats(self, fmts_output: str) -> List[str]:
        fmts = []
        for line in fmts_output.split('\n'):
            if ' E ' in line or ' DE' in line:
                parts = line.split()
                if len(parts) >= 2:
                    fmts.append(parts[1])
        return fmts

    # === Détection des encodeurs matériels ===

    def _detect_hardware_encoders(self) -> Dict[str, List[str]]:
        hw = {'nvidia': [], 'intel': [], 'amd': [], 'apple': []}

        nvidia = self._test_nvidia_encoders()
        if nvidia:
            hw['nvidia'] = nvidia

        intel = self._test_intel_encoders()
        if intel:
            hw['intel'] = intel

        amd = self._test_amd_encoders()
        if amd:
            hw['amd'] = amd

        if self.os_type == 'darwin':
            apple = self._test_apple_encoders()
            if apple:
                hw['apple'] = apple

        return hw

    def _test_encoder_availability(self, name: str, args: List[str]) -> bool:
        try:
            return subprocess.run(['ffmpeg'] + args, capture_output=True, timeout=10).returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _test_nvidia_encoders(self) -> List[str]:
        try:
            if subprocess.run(['nvidia-smi'], capture_output=True, timeout=5).returncode != 0:
                return []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []

        tests = {
            'h264_nvenc': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'h264_nvenc', '-f', 'null', '-'],
            'hevc_nvenc': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'hevc_nvenc', '-f', 'null', '-'],
            'av1_nvenc': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'av1_nvenc', '-f', 'null', '-'],
        }
        return [enc for enc, args in tests.items() if self._test_encoder_availability(enc, args)]

    def _test_intel_encoders(self) -> List[str]:
        tests = {
            'h264_qsv': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'h264_qsv', '-f', 'null', '-'],
            'hevc_qsv': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'hevc_qsv', '-f', 'null', '-'],
            'av1_qsv': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'av1_qsv', '-f', 'null', '-'],
        }
        return [enc for enc, args in tests.items() if self._test_encoder_availability(enc, args)]

    def _test_amd_encoders(self) -> List[str]:
        tests = {
            'h264_amf': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'h264_amf', '-f', 'null', '-'],
            'hevc_amf': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'hevc_amf', '-f', 'null', '-'],
        }
        return [enc for enc, args in tests.items() if self._test_encoder_availability(enc, args)]

    def _test_apple_encoders(self) -> List[str]:
        tests = {
            'h264_videotoolbox': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'h264_videotoolbox', '-f', 'null', '-'],
            'hevc_videotoolbox': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'hevc_videotoolbox', '-f', 'null', '-'],
        }
        return [enc for enc, args in tests.items() if self._test_encoder_availability(enc, args)]

    # === Divers ===

    def _detect_max_resolution(self) -> str:
        for res in ['8K', '4K', '2K', '1080p']:
            if self._test_resolution_support(res):
                return res
        return '1080p'

    def _test_resolution_support(self, res: str) -> bool:
        sizes = {'8K': '7680x4320', '4K': '3840x2160', '2K': '2560x1440', '1080p': '1920x1080'}
        size = sizes.get(res, '1920x1080')
        try:
            return subprocess.run(['ffmpeg', '-f', 'lavfi', '-i', f'testsrc=duration=1:size={size}:rate=1', '-c:v', 'libx264', '-f', 'null', '-'], capture_output=True, timeout=15).returncode == 0
        except Exception:
            return False

    def _estimate_performance(self) -> float:
        cpu_score = psutil.cpu_count() * 10
        mem_score = psutil.virtual_memory().total / (1024 ** 3) * 2
        gpu_bonus = 50 if self._is_nvidia_gpu_present() else 0
        return min(cpu_score + mem_score + gpu_bonus, 1000.0)

    def _is_nvidia_gpu_present(self) -> bool:
        try:
            return subprocess.run(['nvidia-smi'], capture_output=True, timeout=2).returncode == 0
        except Exception:
            return False


# Helper pour compatibilité API précédente

def detect_capabilities() -> ServerCapabilities:
    detector = HardwareDetector()
    return detector.detect_all_capabilities() 