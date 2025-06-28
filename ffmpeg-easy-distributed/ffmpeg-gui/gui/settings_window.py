import os
from tkinter import Toplevel, ttk, BooleanVar, StringVar

from core.settings import Settings # Settings class for type hinting
# No direct usage of Settings.data or Settings.save() at module level

class SettingsWindow:
    def __init__(self, master, settings_instance: Settings, loop, run_async_func): # Added settings_instance, loop, run_async_func
        self.master = master
        self.settings = settings_instance # Store the instance
        self.loop = loop # Store, though not used in this window currently
        self.run_async_func = run_async_func # Store, though not used

        self.top = Toplevel(master)
        self.top.title("Preferences")
        self.top.transient(master)
        self.top.grab_set()
        self._build()

    def _build(self):
        # Access data from the settings instance
        concurrency = self.settings.data.get("concurrency", os.cpu_count()) # Use .get for safety
        video_conc = self.settings.data.get("video_concurrency", max(1, os.cpu_count() // 2))
        interval = self.settings.data.get("progress_refresh_interval", 2)

        ttk.Label(self.top, text="Global concurrency (jobs)").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.cores_var = ttk.Spinbox(self.top, from_=1, to=os.cpu_count() or 1, width=5) # Ensure 'to' is at least 1
        self.cores_var.set(concurrency)
        self.cores_var.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(self.top, text="Video concurrency").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.video_var = ttk.Spinbox(self.top, from_=1, to=os.cpu_count() or 1, width=5)
        self.video_var.set(video_conc)
        self.video_var.grid(row=1, column=1, padx=5, pady=2)

        ttk.Label(self.top, text="Progress refresh (s)").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.interval_var = ttk.Spinbox(self.top, from_=1, to=10, width=5)
        self.interval_var.set(interval)
        self.interval_var.grid(row=2, column=1, padx=5, pady=2)

        # Keep folder structure
        self.keep_var = BooleanVar(value=self.settings.data.get("keep_folder_structure", True))
        ttk.Checkbutton(self.top, text="Keep folder structure", variable=self.keep_var).grid(row=3, column=0, columnspan=2, sticky="w", padx=5, pady=2)

        # Default encoders
        ttk.Label(self.top, text="Default video encoder").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        self.def_vid_var = StringVar(value=self.settings.data.get("default_video_encoder", "libx264"))
        ttk.Entry(self.top, textvariable=self.def_vid_var, width=20).grid(row=4, column=1, padx=5, pady=2)

        ttk.Label(self.top, text="Default audio encoder").grid(row=5, column=0, sticky="w", padx=5, pady=2)
        self.def_aud_var = StringVar(value=self.settings.data.get("default_audio_encoder", "aac"))
        ttk.Entry(self.top, textvariable=self.def_aud_var, width=20).grid(row=5, column=1, padx=5, pady=2)

        ttk.Label(self.top, text="Default image encoder").grid(row=6, column=0, sticky="w", padx=5, pady=2)
        self.def_img_var = StringVar(value=self.settings.data.get("default_image_encoder", "png"))
        ttk.Entry(self.top, textvariable=self.def_img_var, width=20).grid(row=6, column=1, padx=5, pady=2)

        # Custom flags
        ttk.Label(self.top, text="Custom ffmpeg flags").grid(row=7, column=0, sticky="w", padx=5, pady=2)
        self.flags_var = StringVar(value=self.settings.data.get("custom_flags", ""))
        ttk.Entry(self.top, textvariable=self.flags_var, width=30).grid(row=7, column=1, padx=5, pady=2, sticky="ew")
        self.top.columnconfigure(1, weight=1) # Allow flags entry to expand

        # Filename Template
        ttk.Label(self.top, text="Output Filename Template").grid(row=8, column=0, sticky="w", padx=5, pady=2)
        self.filename_template_var = StringVar(value=self.settings.data.get("filename_template", "{nom_source}-{resolution}.{container_ext}"))
        ttk.Entry(self.top, textvariable=self.filename_template_var, width=40).grid(row=8, column=1, padx=5, pady=2, sticky="ew")

        # Info label for filename template
        template_info_label = ttk.Label(self.top,
                                        text="Variables: {nom_source}, {resolution}, {codec}, {date}, {container_ext}",
                                        font=("Helvetica", 9), foreground="gray")
        template_info_label.grid(row=9, column=0, columnspan=2, sticky="w", padx=5, pady=(0,5))


        button_frame = ttk.Frame(self.top)
        button_frame.grid(row=10, column=0, columnspan=2, pady=10)

        save_btn = ttk.Button(button_frame, text="Save", command=self._save)
        save_btn.pack(side=tk.LEFT, padx=5)

        cancel_btn = ttk.Button(button_frame, text="Cancel", command=self.top.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=5)


    def _save(self):
        # Save to the settings instance's data attribute
        self.settings.data["concurrency"] = int(self.cores_var.get())
        self.settings.data["video_concurrency"] = int(self.video_var.get())
        self.settings.data["progress_refresh_interval"] = int(self.interval_var.get())
        self.settings.data["keep_folder_structure"] = self.keep_var.get()
        self.settings.data["default_video_encoder"] = self.def_vid_var.get()
        self.settings.data["default_audio_encoder"] = self.def_aud_var.get()
        self.settings.data["default_image_encoder"] = self.def_img_var.get()
        self.settings.data["custom_flags"] = self.flags_var.get()
        self.settings.data["filename_template"] = self.filename_template_var.get()

        # Call the instance's save method
        self.settings.save()
        self.top.destroy()
