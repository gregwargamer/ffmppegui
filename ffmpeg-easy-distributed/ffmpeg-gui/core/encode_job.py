import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
import uuid # For unique IDs for OutputConfig

# Using a simple class for OutputConfig for now. Could be a dataclass in Python 3.7+
class OutputConfig:
    def __init__(self, name: str, initial_dst_path: Path, mode: str):
        self.id: str = str(uuid.uuid4()) # Unique ID for this output configuration
        self.name: str = name # User-friendly name like "1080p H.264" or "AV1 4K"
        self.dst_path: Path = initial_dst_path # Will be updated by filename template
        self.mode: str = mode # video, audio, image, gif - usually same as parent EncodeJob but could differ

        self.encoder: str = ""
        self.container: str = "" # e.g. "mp4", "mkv" - crucial for filename template and ffmpeg
        self.quality: str = "" # CRF value for video, quality for image, bitrate for audio (e.g. "192k")
        self.cq_value: str = "" # Specific CQ value if different from quality (e.g. for QSV)
        self.preset: str = "" # Encoder preset (e.g. "medium", "slow")
        self.custom_flags: str = ""

        self.status: str = "pending"  # pending, running, paused, done, error, cancelled
        self.progress: float = 0.0
        self.process: Optional[subprocess.Popen] = None # FFmpeg process for this specific output
        self.is_paused: bool = False
        # is_cancelled is handled by the parent EncodeJob for all its outputs

        # Encoding parameters (mirrors what was in EncodeJob, now per output)
        self.video_mode: str = "quality"  # quality, bitrate
        self.bitrate: str = ""
        self.multipass: bool = False
        self.longest_side: str = ""
        self.megapixels: str = ""
        
        self.filters: Dict[str, Any] = { # Default filters, can be customized per output
            "brightness": 0, "contrast": 0, "saturation": 0, "gamma": 1.0, "hue": 0,
            "sharpness": 0, "noise_reduction": 0, "scale_width": 0, "scale_height": 0,
            "crop_x": 0, "crop_y": 0, "crop_w": 0, "crop_h": 0,
            "rotate": 0, "flip_h": False, "flip_v": False,
        }
        self.audio_config: Dict[str, Any] = { # Default audio config
            "mode": "auto", "selected_tracks": [], "audio_codec": "aac", "audio_bitrate": "128k"
        }
        self.subtitle_config: Dict[str, Any] = { # Default subtitle config
            "mode": "copy", "external_path": None, "burn_track": -1
        }
        self.trim_config: Dict[str, Any] = {"start": "", "end": ""} # Default trim
        self.gif_config: Dict[str, Any] = {"fps": 15, "use_palette": True} # Default GIF
        self.lut_path: Optional[str] = None # Path to the LUT file

        # Watermark settings
        self.watermark_path: Optional[str] = None
        self.watermark_position: str = "top_right" # e.g., top_left, top_right, bottom_left, bottom_right, center
        self.watermark_scale: float = 0.1 # Relative to video width (e.g., 0.1 = 10% of video width)
        self.watermark_opacity: float = 1.0 # 0.0 (transparent) to 1.0 (opaque)
        self.watermark_padding: int = 10 # Pixels


class EncodeJob:
    def __init__(self, src_path: Path, mode: str, initial_output_config: OutputConfig = None):
        self.src_path: Path = src_path
        self.mode: str = mode # Primary mode of the source (video, audio, image)
        self.relative_src_path: Optional[Path] = None # For preserving structure

        self.outputs: List[OutputConfig] = []
        if initial_output_config:
            self.outputs.append(initial_output_config)

        # Overall status and progress for the EncodeJob (aggregated from outputs)
        # self.status: str = "pending" # This will be derived
        # self.progress: float = 0.0 # This will be derived
        self.duration: Optional[float] = None # Duration of the source, fetched once

        self.is_cancelled: bool = False # Cancellation applies to all outputs of this job

    def get_overall_status(self) -> str:
        if not self.outputs:
            return "pending" # Or "empty" / "misconfigured"
        if self.is_cancelled:
            return "cancelled"

        statuses = [out.status for out in self.outputs]
        if any(s == "running" for s in statuses): return "running"
        if any(s == "paused" for s in statuses): return "paused"
        # If all are done, it's done. If all are pending, it's pending.
        # If all are error/cancelled/done, and at least one error, it's error.
        if all(s == "done" for s in statuses): return "done"
        if all(s == "pending" for s in statuses): return "pending"
        if all(s in ["done", "error", "cancelled"] for s in statuses):
            if any(s == "error" for s in statuses): return "error"
            # If here, all are done or cancelled (no errors, no pending, no running)
            if any(s == "done" for s in statuses): return "done" # At least one done, others cancelled
            return "cancelled" # All must be cancelled

        return "mixed" # Some pending, some done, etc. but not running/paused

    def get_overall_progress(self) -> float:
        if not self.outputs:
            return 0.0
        total_progress = sum(out.progress for out in self.outputs)
        return total_progress / len(self.outputs) if self.outputs else 0.0

    def cancel_all_outputs(self):
        """Cancels all individual output processes for this job."""
        self.is_cancelled = True # Mark the main job as cancelled
        for output_cfg in self.outputs:
            output_cfg.status = "cancelled" # Mark individual output
            if output_cfg.process and output_cfg.process.poll() is None:
                try:
                    output_cfg.process.terminate()
                    output_cfg.process.wait(timeout=2) # Short wait
                except subprocess.TimeoutExpired:
                    output_cfg.process.kill()
                except Exception:
                    pass # Ignore other errors during cancellation
            output_cfg.process = None

    def pause_all_outputs(self):
        """Pauses all running individual output processes."""
        if self.is_cancelled: return
        paused_any = False
        for output_cfg in self.outputs:
            if output_cfg.status == "running" and output_cfg.process and output_cfg.process.poll() is None and not output_cfg.is_paused:
                try:
                    import signal
                    output_cfg.process.send_signal(signal.SIGSTOP)
                    output_cfg.is_paused = True
                    output_cfg.status = "paused"
                    paused_any = True
                except Exception:
                    pass # log this?
        return paused_any

    def resume_all_outputs(self):
        """Resumes all paused individual output processes."""
        if self.is_cancelled: return
        resumed_any = False
        for output_cfg in self.outputs:
            if output_cfg.status == "paused" and output_cfg.process and output_cfg.process.poll() is None and output_cfg.is_paused:
                try:
                    import signal
                    output_cfg.process.send_signal(signal.SIGCONT)
                    output_cfg.is_paused = False
                    output_cfg.status = "running" # Set back to running
                    resumed_any = True
                except Exception:
                    pass # log this?
        return resumed_any

    def __str__(self):
        num_outputs = len(self.outputs)
        if num_outputs == 1:
            # For single output, display can be similar to before
            # return f"EncodeJob({self.src_path.name} -> {self.outputs[0].dst_path.name}, {self.outputs[0].status})"
            # For now, always indicate potential for multiple outputs
            return f"EncodeJob({self.src_path.name}, {num_outputs} output(s), {self.get_overall_status()})"

        return f"EncodeJob({self.src_path.name}, {num_outputs} outputs, {self.get_overall_status()})"


# The old EncodeJob methods like cancel(), pause(), resume() that acted on a single self.process
# are now replaced by cancel_all_outputs(), pause_all_outputs(), resume_all_outputs()
# which iterate over output_cfg.process.
# The individual output_cfg.process, output_cfg.status, output_cfg.progress, output_cfg.is_paused
# will be managed by the worker pool for each specific output task.

    # === Backward-compatibility helpers ===
    # Many parts of the GUI still expect single-output attributes on EncodeJob.
    # To avoid crashing while the refactor is underway, expose shim properties
    # that transparently map to the first OutputConfig in self.outputs.
    # Les commentaires sont en français, conformément aux consignes.

    #this part expose un accès rapide au premier OutputConfig
    @property
    def _first_output(self) -> Optional[OutputConfig]:
        return self.outputs[0] if self.outputs else None

    #this part gère l'attribut encoder historique
    @property
    def encoder(self) -> str:
        return self._first_output.encoder if self._first_output else ""

    @encoder.setter
    def encoder(self, value: str):
        if self._first_output:
            self._first_output.encoder = value

    #this part gère la qualité générique
    @property
    def quality(self) -> str:
        return self._first_output.quality if self._first_output else ""

    @quality.setter
    def quality(self, value: str):
        if self._first_output:
            self._first_output.quality = value

    #this part gère la valeur CQ/CRF spécifique
    @property
    def cq_value(self) -> str:
        return self._first_output.cq_value if self._first_output else ""

    @cq_value.setter
    def cq_value(self, value: str):
        if self._first_output:
            self._first_output.cq_value = value

    #this part gère le bitrate
    @property
    def bitrate(self) -> str:
        return self._first_output.bitrate if self._first_output else ""

    @bitrate.setter
    def bitrate(self, value: str):
        if self._first_output:
            self._first_output.bitrate = value

    #this part gère le preset
    @property
    def preset(self) -> str:
        return self._first_output.preset if self._first_output else ""

    @preset.setter
    def preset(self, value: str):
        if self._first_output:
            self._first_output.preset = value

    #this part gère les flags personnalisés
    @property
    def custom_flags(self) -> str:
        return self._first_output.custom_flags if self._first_output else ""

    @custom_flags.setter
    def custom_flags(self, value: str):
        if self._first_output:
            self._first_output.custom_flags = value

    #this part gère le mode vidéo/bitrate
    @property
    def video_mode(self) -> str:
        return self._first_output.video_mode if self._first_output else "quality"

    @video_mode.setter
    def video_mode(self, value: str):
        if self._first_output:
            self._first_output.video_mode = value

    #this part expose le dict des filtres
    @property
    def filters(self):
        return self._first_output.filters if self._first_output else {}

    # Les setters pour les dicts modifient directement la référence existante

    #this part gère les sous-titres, trim, audio config
    @property
    def subtitle_config(self):
        return self._first_output.subtitle_config if self._first_output else {}

    @property
    def trim_config(self):
        return self._first_output.trim_config if self._first_output else {}

    @property
    def audio_config(self):
        return self._first_output.audio_config if self._first_output else {}

    #this part gère le mode multipass (bitrate seulement)
    @property
    def multipass(self) -> bool:
        return self._first_output.multipass if self._first_output else False

    @multipass.setter
    def multipass(self, value: bool):
        if self._first_output:
            self._first_output.multipass = value
