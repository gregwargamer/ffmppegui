import os
from tkinter import Toplevel, ttk, BooleanVar, StringVar

from core.settings import Settings


class SettingsWindow:
    def __init__(self, master):
        self.top = Toplevel(master)
        self.top.title("Preferences")
        self._build()

    def _build(self):
        concurrency = Settings.data["concurrency"]
        video_conc = Settings.data["video_concurrency"]
        interval = Settings.data["progress_refresh_interval"]

        ttk.Label(self.top, text="Global concurrency (jobs)").grid(row=0, column=0, sticky="w")
        self.cores_var = ttk.Spinbox(self.top, from_=1, to=os.cpu_count(), width=5)
        self.cores_var.set(concurrency)
        self.cores_var.grid(row=0, column=1)

        ttk.Label(self.top, text="Video concurrency").grid(row=1, column=0, sticky="w")
        self.video_var = ttk.Spinbox(self.top, from_=1, to=os.cpu_count(), width=5)
        self.video_var.set(video_conc)
        self.video_var.grid(row=1, column=1)

        ttk.Label(self.top, text="Progress refresh (s)").grid(row=2, column=0, sticky="w")
        self.interval_var = ttk.Spinbox(self.top, from_=1, to=10, width=5)
        self.interval_var.set(interval)
        self.interval_var.grid(row=2, column=1)

        # Keep folder structure
        self.keep_var = BooleanVar(value=Settings.data.get("keep_folder_structure", True))
        ttk.Checkbutton(self.top, text="Keep folder structure", variable=self.keep_var).grid(row=3, column=0, columnspan=2, sticky="w")

        # Default encoders
        ttk.Label(self.top, text="Default video encoder").grid(row=4, column=0, sticky="w")
        self.def_vid_var = StringVar(value=Settings.data.get("default_video_encoder", ""))
        ttk.Entry(self.top, textvariable=self.def_vid_var, width=15).grid(row=4, column=1)

        ttk.Label(self.top, text="Default audio encoder").grid(row=5, column=0, sticky="w")
        self.def_aud_var = StringVar(value=Settings.data.get("default_audio_encoder", ""))
        ttk.Entry(self.top, textvariable=self.def_aud_var, width=15).grid(row=5, column=1)

        ttk.Label(self.top, text="Default image encoder").grid(row=6, column=0, sticky="w")
        self.def_img_var = StringVar(value=Settings.data.get("default_image_encoder", ""))
        ttk.Entry(self.top, textvariable=self.def_img_var, width=15).grid(row=6, column=1)

        # Custom flags
        ttk.Label(self.top, text="Custom ffmpeg flags").grid(row=7, column=0, sticky="w")
        self.flags_var = StringVar(value=Settings.data.get("custom_flags", ""))
        ttk.Entry(self.top, textvariable=self.flags_var, width=25).grid(row=7, column=1)

        # Filename Template
        ttk.Label(self.top, text="Output Filename Template").grid(row=8, column=0, sticky="w")
        self.filename_template_var = StringVar(value=Settings.data.get("filename_template", "{nom_source}-{resolution}.{container_ext}"))
        ttk.Entry(self.top, textvariable=self.filename_template_var, width=40).grid(row=8, column=1, sticky="ew")

        # Info label for filename template
        template_info_label = ttk.Label(self.top,
                                        text="Variables: {nom_source}, {resolution}, {codec}, {date}, {container_ext}",
                                        font=("Helvetica", 9), foreground="gray")
        template_info_label.grid(row=9, column=0, columnspan=2, sticky="w", padx=5, pady=(0,5))


        save_btn = ttk.Button(self.top, text="Save", command=self._save)
        save_btn.grid(row=10, column=0, columnspan=2, pady=10)

    def _save(self):
        Settings.data["concurrency"] = int(self.cores_var.get())
        Settings.data["video_concurrency"] = int(self.video_var.get())
        Settings.data["progress_refresh_interval"] = int(self.interval_var.get())
        Settings.data["keep_folder_structure"] = self.keep_var.get()
        Settings.data["default_video_encoder"] = self.def_vid_var.get()
        Settings.data["default_audio_encoder"] = self.def_aud_var.get()
        Settings.data["default_image_encoder"] = self.def_img_var.get()
        Settings.data["custom_flags"] = self.flags_var.get()
        Settings.data["filename_template"] = self.filename_template_var.get()
        Settings.save()
        self.top.destroy()
