from tkinter import Toplevel, ttk, StringVar

from core.encode_job import EncodeJob
from core.ffmpeg_helpers import FFmpegHelpers


class JobEditWindow:
    def __init__(self, master, job: EncodeJob):
        self.top = Toplevel(master)
        self.top.title(f"Edit job - {job.src_path.name}")
        self.job = job
        self._build()

    def _build(self):
        nb = ttk.Notebook(self.top)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        video_tab = ttk.Frame(nb)
        audio_tab = ttk.Frame(nb)
        image_tab = ttk.Frame(nb)
        nb.add(video_tab, text="Video")
        nb.add(audio_tab, text="Audio")
        nb.add(image_tab, text="Image")

        # Common vars
        self.encoder_var = StringVar(value=self.job.encoder or "")
        self.quality_var = StringVar(value=str(self.job.quality or ""))
        self.preset_var = StringVar(value=self.job.preset or "")
        self.custom_flags_var = StringVar(value=getattr(self.job, 'custom_flags', '') or "")

        # Video tab
        ttk.Label(video_tab, text="Encoder").grid(row=0, column=0, sticky="w")
        encoders = [e for e in FFmpegHelpers.available_encoders() if any(k in e for k in ["x264", "x265", "av1", "vp9", "h264", "hevc"])]
        ttk.Combobox(video_tab, textvariable=self.encoder_var, values=encoders).grid(row=0, column=1, sticky="ew")

        ttk.Label(video_tab, text="CRF / Quality").grid(row=1, column=0, sticky="w")
        ttk.Entry(video_tab, textvariable=self.quality_var, width=6).grid(row=1, column=1, sticky="w")

        ttk.Label(video_tab, text="Preset").grid(row=2, column=0, sticky="w")
        ttk.Combobox(video_tab, textvariable=self.preset_var, values=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"]).grid(row=2, column=1, sticky="w")

        ttk.Label(video_tab, text="Custom Flags").grid(row=3, column=0, sticky="w")
        ttk.Entry(video_tab, textvariable=self.custom_flags_var, width=40).grid(row=3, column=1, sticky="ew")

        # Audio tab
        ttk.Label(audio_tab, text="Encoder").grid(row=0, column=0, sticky="w")
        a_enc = [e for e in FFmpegHelpers.available_encoders() if any(k in e for k in ["aac", "flac", "opus", "vorbis", "mp3"])]
        ttk.Combobox(audio_tab, textvariable=self.encoder_var, values=a_enc).grid(row=0, column=1, sticky="ew")
        ttk.Label(audio_tab, text="Bitrate / Level").grid(row=1, column=0, sticky="w")
        ttk.Entry(audio_tab, textvariable=self.quality_var, width=6).grid(row=1, column=1, sticky="w")

        ttk.Label(audio_tab, text="Custom Flags").grid(row=2, column=0, sticky="w")
        ttk.Entry(audio_tab, textvariable=self.custom_flags_var, width=40).grid(row=2, column=1, sticky="ew")

        # Image tab
        ttk.Label(image_tab, text="Encoder").grid(row=0, column=0, sticky="w")
        i_enc = [e for e in FFmpegHelpers.available_encoders() if any(k in e for k in ["png", "jpeg", "webp", "avif", "jxl"])]
        ttk.Combobox(image_tab, textvariable=self.encoder_var, values=i_enc).grid(row=0, column=1, sticky="ew")
        ttk.Label(image_tab, text="Quality %").grid(row=1, column=0, sticky="w")
        ttk.Entry(image_tab, textvariable=self.quality_var, width=6).grid(row=1, column=1, sticky="w")

        ttk.Label(image_tab, text="Custom Flags").grid(row=2, column=0, sticky="w")
        ttk.Entry(image_tab, textvariable=self.custom_flags_var, width=40).grid(row=2, column=1, sticky="ew")

        # Save button
        ttk.Button(self.top, text="Save", command=self._save).pack(pady=10)

    def _save(self):
        self.job.encoder = self.encoder_var.get()
        self.job.quality = self.quality_var.get()
        self.job.preset = self.preset_var.get()
        self.job.custom_flags = self.custom_flags_var.get()
        # Update tree view, if exists
        # assume master is MainWindow, update row
        self.top.destroy()
