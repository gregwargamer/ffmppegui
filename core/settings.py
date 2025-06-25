import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".ffmpeg_frontend"
CONFIG_DIR.mkdir(exist_ok=True)
SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULT_SETTINGS = {
    "concurrency": 4,
    "video_concurrency": 1,
    "progress_refresh_interval": 2,
    "default_video_encoder": "libx264",
    "default_audio_encoder": "aac",
    "custom_flags": "",
    "keep_folder_structure": True,
    "default_image_encoder": "libwebp",
    "presets": {
        "H264 High Quality": {
            "mode": "video",
            "codec": "h264",
            "encoder": "libx264",
            "quality": "18",
            "cq_value": "",
            "preset": "slow",
            "container": "mp4",
            "custom_flags": ""
        },
        "H264 Fast": {
            "mode": "video", 
            "codec": "h264",
            "encoder": "libx264",
            "quality": "23",
            "cq_value": "",
            "preset": "fast", 
            "container": "mp4",
            "custom_flags": ""
        },
        "WebP Images": {
            "mode": "image",
            "codec": "webp",
            "encoder": "libwebp",
            "quality": "80",
            "cq_value": "",
            "preset": "",
            "container": "webp",
            "custom_flags": ""
        }
    }
}


class Settings:
    data: dict = DEFAULT_SETTINGS.copy()

    @classmethod
    def load(cls):
        if SETTINGS_FILE.exists():
            try:
                cls.data.update(json.loads(SETTINGS_FILE.read_text()))
            except Exception:
                print("Failed to parse settings.json, using defaults")
        else:
            cls.save()

    @classmethod
    def save(cls):
        SETTINGS_FILE.write_text(json.dumps(cls.data, indent=2))
