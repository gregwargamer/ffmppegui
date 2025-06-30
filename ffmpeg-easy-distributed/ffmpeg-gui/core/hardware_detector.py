# Import réexporté depuis le module commun
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from common.hardware_detector import HardwareDetector, detect_capabilities

import subprocess
import platform
import psutil
import re
import json
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import asdict
from shared.messages import ServerCapabilities, EncoderType

class HardwareDetector:
    """Détecteur de capacités matérielles et logicielles"""
    
    def __init__(self):
        self.os_type = platform.system().lower()
        self.logger = logging.getLogger(__name__)
    
    def detect_all_capabilities(self) -> ServerCapabilities:
        """Détecte toutes les capacités du serveur"""
        self.logger.info("🔍 Détection des capacités du serveur...")
        
        system_info = self._get_system_info()
        ffmpeg_info = self._detect_ffmpeg_capabilities()
        hardware_encoders = self._detect_hardware_encoders()
        performance_score = self._estimate_performance()
        
        capabilities = ServerCapabilities(
            hostname=platform.node(),
            os=f"{platform.system()} {platform.release()}",
            cpu_cores=psutil.cpu_count(),
            memory_gb=round(psutil.virtual_memory().total / (1024**3), 1),
            disk_space_gb=round(psutil.disk_usage('/').free / (1024**3), 1),
            software_encoders=ffmpeg_info['software'],
            hardware_encoders=hardware_encoders,
            estimated_performance=performance_score,
            current_load=psutil.cpu_percent(interval=1) / 100.0,
            max_resolution=self._detect_max_resolution(),
            supported_formats=ffmpeg_info['formats'],
            max_file_size_gb=100.0
        )
        
        self.logger.info(f"✅ Capacités détectées: {len(capabilities.software_encoders)} logiciels, "
                        f"{sum(len(encoders) for encoders in capabilities.hardware_encoders.values())} matériels")
        
        return capabilities
    
    def _get_system_info(self) -> Dict:
        return {
            'platform': platform.platform(),
            'processor': platform.processor(),
            'architecture': platform.architecture(),
            'python_version': platform.python_version()
        }
    
    def _detect_ffmpeg_capabilities(self) -> Dict[str, List[str]]:
        try:
            result = subprocess.run(['ffmpeg', '-version'], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise RuntimeError("FFmpeg non disponible")
            
            encoders_result = subprocess.run(['ffmpeg', '-encoders'], 
                                           capture_output=True, text=True, timeout=10)
            formats_result = subprocess.run(['ffmpeg', '-formats'], 
                                          capture_output=True, text=True, timeout=10)
            
            software_encoders = self._parse_software_encoders(encoders_result.stdout)
            supported_formats = self._parse_formats(formats_result.stdout)
            
            return {
                'software': software_encoders,
                'formats': supported_formats,
                'version': self._extract_ffmpeg_version(result.stdout)
            }
            
        except (subprocess.TimeoutExpired, FileNotFoundError, RuntimeError) as e:
            self.logger.error(f"❌ Erreur détection FFmpeg: {e}")
            return {'software': [], 'formats': [], 'version': 'unknown'}
    
    def _parse_software_encoders(self, encoders_output: str) -> List[str]:
        software_encoders = []
        video_patterns = {
            'libx264': r'libx264.*H\.264',
            'libx265': r'libx265.*H\.265',
            'libvpx': r'libvpx.*VP8',
            'libvpx-vp9': r'libvpx-vp9.*VP9',
            'libaom-av1': r'libaom-av1.*AV1',
            'libsvtav1': r'libsvtav1.*AV1'
        }
        audio_patterns = {
            'aac': r'aac.*AAC',
            'libfdk_aac': r'libfdk_aac.*AAC',
            'libmp3lame': r'libmp3lame.*MP3',
            'libopus': r'libopus.*Opus',
            'libvorbis': r'libvorbis.*Vorbis',
            'flac': r'flac.*FLAC'
        }
        all_patterns = {**video_patterns, **audio_patterns}
        for encoder_name, pattern in all_patterns.items():
            if re.search(pattern, encoders_output, re.IGNORECASE):
                software_encoders.append(encoder_name)
        return software_encoders
    
    def _detect_hardware_encoders(self) -> Dict[str, List[str]]:
        hardware_encoders = {'nvidia': [], 'intel': [], 'amd': [], 'apple': []}
        
        nvidia_encoders = self._test_nvidia_encoders()
        if nvidia_encoders:
            hardware_encoders['nvidia'] = nvidia_encoders
        
        intel_encoders = self._test_intel_encoders()
        if intel_encoders:
            hardware_encoders['intel'] = intel_encoders
        
        amd_encoders = self._test_amd_encoders()
        if amd_encoders:
            hardware_encoders['amd'] = amd_encoders
        
        if self.os_type == 'darwin':
            apple_encoders = self._test_apple_encoders()
            if apple_encoders:
                hardware_encoders['apple'] = apple_encoders
        
        return hardware_encoders
    
    def _test_nvidia_encoders(self) -> List[str]:
        nvidia_encoders = []
        try:
            if subprocess.run(['nvidia-smi'], capture_output=True, timeout=5).returncode != 0:
                return []
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return []
        
        nvenc_tests = {
            'h264_nvenc': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'h264_nvenc', '-f', 'null', '-'],
            'hevc_nvenc': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'hevc_nvenc', '-f', 'null', '-'],
            'av1_nvenc': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'av1_nvenc', '-f', 'null', '-']
        }
        for encoder, test_args in nvenc_tests.items():
            if self._test_encoder_availability(encoder, test_args):
                nvidia_encoders.append(encoder)
        return nvidia_encoders
    
    def _test_intel_encoders(self) -> List[str]:
        intel_encoders = []
        qsv_tests = {
            'h264_qsv': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'h264_qsv', '-f', 'null', '-'],
            'hevc_qsv': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'hevc_qsv', '-f', 'null', '-'],
            'av1_qsv': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'av1_qsv', '-f', 'null', '-']
        }
        for encoder, test_args in qsv_tests.items():
            if self._test_encoder_availability(encoder, test_args):
                intel_encoders.append(encoder)
        return intel_encoders
    
    def _test_amd_encoders(self) -> List[str]:
        amd_encoders = []
        amf_tests = {
            'h264_amf': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'h264_amf', '-f', 'null', '-'],
            'hevc_amf': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'hevc_amf', '-f', 'null', '-']
        }
        for encoder, test_args in amf_tests.items():
            if self._test_encoder_availability(encoder, test_args):
                amd_encoders.append(encoder)
        return amd_encoders
    
    def _test_apple_encoders(self) -> List[str]:
        apple_encoders = []
        vt_tests = {
            'h264_videotoolbox': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'h264_videotoolbox', '-f', 'null', '-'],
            'hevc_videotoolbox': ['-f', 'lavfi', '-i', 'testsrc=duration=1:size=320x240:rate=1', '-c:v', 'hevc_videotoolbox', '-f', 'null', '-']
        }
        for encoder, test_args in vt_tests.items():
            if self._test_encoder_availability(encoder, test_args):
                apple_encoders.append(encoder)
        return apple_encoders
    
    def _test_encoder_availability(self, encoder_name: str, test_args: List[str]) -> bool:
        try:
            return subprocess.run(['ffmpeg'] + test_args, capture_output=True, timeout=10).returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def _parse_formats(self, formats_output: str) -> List[str]:
        formats = []
        for line in formats_output.split('\n'):
            if ' E ' in line or ' DE' in line:
                parts = line.split()
                if len(parts) >= 2:
                    formats.append(parts[1])
        return formats
    
    def _detect_max_resolution(self) -> str:
        for res in ['8K', '4K', '2K', '1080p']:
            if self._test_resolution_support(res):
                return res
        return '1080p'
    
    def _test_resolution_support(self, resolution: str) -> bool:
        size_map = {'8K': '7680x4320', '4K': '3840x2160', '2K': '2560x1440', '1080p': '1920x1080'}
        size = size_map.get(resolution, '1920x1080')
        try:
            return subprocess.run(['ffmpeg', '-f', 'lavfi', '-i', f'testsrc=duration=1:size={size}:rate=1', '-c:v', 'libx264', '-f', 'null', '-'], capture_output=True, timeout=15).returncode == 0
        except:
            return False
    
    def _estimate_performance(self) -> float:
        cpu_score = psutil.cpu_count() * 10
        memory_score = psutil.virtual_memory().total / (1024**3) * 2
        gpu_bonus = 50 if self._is_nvidia_gpu_present() else 0
        return min(cpu_score + memory_score + gpu_bonus, 1000.0)
    
    def _is_nvidia_gpu_present(self) -> bool:
        try:
            return subprocess.run(['nvidia-smi'], capture_output=True, timeout=2).returncode == 0
        except:
            return False
    
    def _extract_ffmpeg_version(self, version_output: str) -> str:
        match = re.search(r'ffmpeg version (\S+)', version_output)
        return match.group(1) if match else 'unknown'

def detect_capabilities() -> ServerCapabilities:
    detector = HardwareDetector()
    return detector.detect_all_capabilities()
