import tkinter as tk
from tkinter import Toplevel, ttk, StringVar, messagebox
import copy # For duplicating OutputConfig
import uuid # For new OutputConfig IDs

from core.encode_job import EncodeJob, OutputConfig
from core.ffmpeg_helpers import FFmpegHelpers


class JobEditWindow:
    def __init__(self, master, job: EncodeJob):
        self.master = master # Keep a reference to master for UI updates
        self.top = Toplevel(master)
        self.top.title(f"Edit Job Outputs - {job.src_path.name}")
        self.top.geometry("700x550") # Adjusted size
        self.job = job
        self.selected_output_config_id: Optional[str] = None

        # If job has no outputs, create a default one to start with
        if not self.job.outputs:
            default_output_name = "Default Output"
            # Try to make a somewhat intelligent default dst_path
            default_dst_path = self.job.src_path.parent / f"{self.job.src_path.stem}_{default_output_name.replace(' ', '_')}.mp4"
            new_output = OutputConfig(name=default_output_name, initial_dst_path=default_dst_path, mode=self.job.mode)
            # Populate with some sensible defaults or from global settings if accessible
            # For now, basic defaults:
            new_output.encoder = "libx264" if self.job.mode == "video" else ("aac" if self.job.mode == "audio" else "png")
            new_output.quality = "22" if self.job.mode == "video" else ("128k" if self.job.mode == "audio" else "90")
            new_output.preset = "medium" if self.job.mode == "video" else ""
            self.job.outputs.append(new_output)

        self._build()
        self._load_output_configs_to_tree()
        if self.job.outputs:
            first_output_id = self.job.outputs[0].id
            self.output_tree.selection_set(first_output_id)
            self.output_tree.focus(first_output_id)
            self._on_output_select(None) # Load data for the first output

    def _build(self):
        main_paned = ttk.PanedWindow(self.top, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left pane: OutputConfig list and controls
        left_frame = ttk.Frame(main_paned, width=200)
        main_paned.add(left_frame, weight=1)

        # Right pane: Settings for selected OutputConfig
        right_frame = ttk.Frame(main_paned)
        main_paned.add(right_frame, weight=2)

        # --- Left Pane: Output List ---
        ttk.Label(left_frame, text="Output Configurations:").pack(pady=(0,5), anchor="w")

        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        self.output_tree = ttk.Treeview(tree_frame, columns=("name", "encoder", "quality"), show="headings", selectmode="browse")
        self.output_tree.heading("name", text="Name")
        self.output_tree.heading("encoder", text="Encoder")
        self.output_tree.heading("quality", text="Quality")
        self.output_tree.column("name", width=100, stretch=tk.YES)
        self.output_tree.column("encoder", width=70)
        self.output_tree.column("quality", width=50)

        tree_scrollbar_v = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.output_tree.yview)
        self.output_tree.configure(yscrollcommand=tree_scrollbar_v.set)

        self.output_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scrollbar_v.pack(side=tk.RIGHT, fill=tk.Y)

        self.output_tree.bind("<<TreeviewSelect>>", self._on_output_select)

        output_controls_frame = ttk.Frame(left_frame)
        output_controls_frame.pack(fill=tk.X, pady=5)
        ttk.Button(output_controls_frame, text="Add", command=self._add_output_config).pack(side=tk.LEFT, padx=2)
        ttk.Button(output_controls_frame, text="Remove", command=self._remove_output_config).pack(side=tk.LEFT, padx=2)
        ttk.Button(output_controls_frame, text="Duplicate", command=self._duplicate_output_config).pack(side=tk.LEFT, padx=2)

        # --- Right Pane: Settings Notebook ---
        self.settings_notebook = ttk.Notebook(right_frame)
        self.settings_notebook.pack(fill="both", expand=True, padx=5, pady=5)

        self.video_tab = ttk.Frame(self.settings_notebook)
        self.audio_tab = ttk.Frame(self.settings_notebook)
        self.image_tab = ttk.Frame(self.settings_notebook)
        # Tabs will be added/removed dynamically based on selected output config's mode

        # Common vars for the selected OutputConfig's settings
        self.oc_name_var = StringVar() # For editing the name of the OutputConfig itself
        self.oc_mode_var = StringVar() # video, audio, image
        self.oc_encoder_var = StringVar()
        self.oc_quality_var = StringVar()
        self.oc_preset_var = StringVar()
        self.oc_custom_flags_var = StringVar()
        self.oc_container_var = StringVar() # For dst_path generation

        # OutputConfig Name and Mode (always visible above tabs)
        oc_details_frame = ttk.Frame(right_frame)
        oc_details_frame.pack(fill=tk.X, padx=5, pady=(0,5))
        ttk.Label(oc_details_frame, text="Output Name:").grid(row=0, column=0, sticky="w", padx=(0,5))
        ttk.Entry(oc_details_frame, textvariable=self.oc_name_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(oc_details_frame, text="Output Mode:").grid(row=1, column=0, sticky="w", padx=(0,5))
        self.mode_combo = ttk.Combobox(oc_details_frame, textvariable=self.oc_mode_var, values=["video", "audio", "image"], state="readonly")
        self.mode_combo.grid(row=1, column=1, sticky="ew")
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_output_mode_change)
        oc_details_frame.columnconfigure(1, weight=1)

        # --- Build Tab Contents (they will be populated on selection) ---
        self._build_video_tab(self.video_tab)
        self._build_audio_tab(self.audio_tab)
        self._build_image_tab(self.image_tab)

        # Save/Close button
        action_button_frame = ttk.Frame(self.top)
        action_button_frame.pack(fill=tk.X, padx=10, pady=(0,10))
        ttk.Button(action_button_frame, text="Apply & Close", command=self._apply_and_close).pack(side=tk.RIGHT, padx=5)
        # ttk.Button(action_button_frame, text="Apply", command=self._save_selected_output_config).pack(side=tk.RIGHT)


    def _build_video_tab(self, tab_frame):
        ttk.Label(tab_frame, text="Encoder:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        # Encoders will be filtered later based on availability
        self.video_encoder_combo = ttk.Combobox(tab_frame, textvariable=self.oc_encoder_var, postcommand=lambda: self._update_encoder_list_for_tab("video"))
        self.video_encoder_combo.grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        self.video_encoder_combo.bind("<<ComboboxSelected>>", self._on_encoder_selected)

        self.video_quality_label = ttk.Label(tab_frame, text="CRF / Quality:")
        self.video_quality_label.grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.video_quality_entry = ttk.Entry(tab_frame, textvariable=self.oc_quality_var, width=10)
        self.video_quality_entry.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(tab_frame, text="Preset:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.video_preset_combo = ttk.Combobox(tab_frame, textvariable=self.oc_preset_var, values=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"])
        self.video_preset_combo.grid(row=2, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(tab_frame, text="Container:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.video_container_combo = ttk.Combobox(tab_frame, textvariable=self.oc_container_var, values=["mp4", "mkv", "mov", "webm"])
        self.video_container_combo.grid(row=3, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(tab_frame, text="Custom Flags:").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(tab_frame, textvariable=self.oc_custom_flags_var, width=40).grid(row=4, column=1, sticky="ew", padx=5, pady=2)
        tab_frame.columnconfigure(1, weight=1)

    def _build_audio_tab(self, tab_frame):
        ttk.Label(tab_frame, text="Encoder:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.audio_encoder_combo = ttk.Combobox(tab_frame, textvariable=self.oc_encoder_var, postcommand=lambda: self._update_encoder_list_for_tab("audio"))
        self.audio_encoder_combo.grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        self.audio_encoder_combo.bind("<<ComboboxSelected>>", self._on_encoder_selected)

        self.audio_quality_label = ttk.Label(tab_frame, text="Bitrate / Level:")
        self.audio_quality_label.grid(row=1, column=0, sticky="w", padx=5, pady=2) # e.g., 192k for bitrate, 0-11 for FLAC
        self.audio_quality_entry = ttk.Entry(tab_frame, textvariable=self.oc_quality_var, width=10)
        self.audio_quality_entry.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(tab_frame, text="Container:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.audio_container_combo = ttk.Combobox(tab_frame, textvariable=self.oc_container_var, values=["m4a", "mp3", "opus", "flac", "ogg", "wav"])
        self.audio_container_combo.grid(row=2, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(tab_frame, text="Custom Flags:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(tab_frame, textvariable=self.oc_custom_flags_var, width=40).grid(row=3, column=1, sticky="ew", padx=5, pady=2)
        tab_frame.columnconfigure(1, weight=1)

    def _build_image_tab(self, tab_frame):
        ttk.Label(tab_frame, text="Encoder:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.image_encoder_combo = ttk.Combobox(tab_frame, textvariable=self.oc_encoder_var, postcommand=lambda: self._update_encoder_list_for_tab("image"))
        self.image_encoder_combo.grid(row=0, column=1, sticky="ew", padx=5, pady=2)
        self.image_encoder_combo.bind("<<ComboboxSelected>>", self._on_encoder_selected)

        self.image_quality_label = ttk.Label(tab_frame, text="Quality % (0-100):")
        self.image_quality_label.grid(row=1, column=0, sticky="w", padx=5, pady=2) # Or specific like PNG compression
        self.image_quality_entry = ttk.Entry(tab_frame, textvariable=self.oc_quality_var, width=10)
        self.image_quality_entry.grid(row=1, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(tab_frame, text="Container (ext):").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.image_container_combo = ttk.Combobox(tab_frame, textvariable=self.oc_container_var, values=["png", "jpg", "webp", "avif", "jxl", "heic", "tiff", "bmp"])
        self.image_container_combo.grid(row=2, column=1, sticky="w", padx=5, pady=2)

        ttk.Label(tab_frame, text="Custom Flags:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(tab_frame, textvariable=self.oc_custom_flags_var, width=40).grid(row=3, column=1, sticky="ew", padx=5, pady=2)
        tab_frame.columnconfigure(1, weight=1)

    def _update_encoder_list_for_tab(self, mode: str):
        """Dynamically updates the encoder list for the active tab."""
        all_encoders_tuples = FFmpegHelpers.available_encoders() # List of (name, description)
        all_encoders = [name for name, desc in all_encoders_tuples]

        if mode == "video":
            encoders = [e for e in all_encoders if any(k in e for k in ["x264", "x265", "av1", "vp9", "h264", "hevc", "prores", "dnxhd"])] # Simplified list
            self.video_encoder_combo['values'] = encoders
        elif mode == "audio":
            encoders = [e for e in all_encoders if any(k in e for k in ["aac", "flac", "opus", "vorbis", "mp3", "alac"])]
            self.audio_encoder_combo['values'] = encoders
        elif mode == "image":
            # Prioritize specific image encoders, then generic ones that might be used (like hevc for heic)
            # Explicitly list known good encoders for each format.
            img_encoders = set()
            # PNG
            if 'png' in all_encoders: img_encoders.add('png')
            # JPEG
            if 'mjpeg' in all_encoders: img_encoders.add('mjpeg') # common for jpg output
            if 'libjpeg' in all_encoders: img_encoders.add('libjpeg') # if available
            # WebP
            if 'libwebp_anim' in all_encoders: img_encoders.add('libwebp_anim')
            if 'libwebp' in all_encoders: img_encoders.add('libwebp')
            # AVIF
            if 'libaom-av1' in all_encoders: img_encoders.add('libaom-av1') # AV1 encoder for AVIF
            if 'libsvtav1' in all_encoders: img_encoders.add('libsvtav1')
             # JPEG XL
            if 'libjxl' in all_encoders: img_encoders.add('libjxl')
            # HEIC/HEIF
            if 'libheif' in all_encoders: img_encoders.add('libheif') # Ideal HEIF encoder
            # Fallback to HEVC encoders for HEIC if libheif is not present
            hevc_encoders = [e for e in all_encoders if "hevc" in e or "x265" in e]
            if 'libheif' not in img_encoders and hevc_encoders:
                img_encoders.update(hevc_encoders) # Add available HEVC encoders

            # Add any other encoders that were in the simple list and are valid
            other_simple_list_encoders = [e for e in all_encoders if any(k in e for k in ["png", "jpeg", "mjpeg", "webp", "libaom-av1", "libjxl", "hevc"])]
            img_encoders.update(other_simple_list_encoders)

            self.image_encoder_combo['values'] = sorted(list(img_encoders))


    def _load_output_configs_to_tree(self):
        self.output_tree.delete(*self.output_tree.get_children())
        for oc in self.job.outputs:
            quality_display = oc.quality or oc.cq_value or oc.bitrate or "-"
            self.output_tree.insert("", "end", iid=oc.id, values=(oc.name, oc.encoder, quality_display))

    def _get_selected_output_config(self) -> OutputConfig | None:
        if not self.selected_output_config_id:
            return None
        for oc in self.job.outputs:
            if oc.id == self.selected_output_config_id:
                return oc
        return None

    def _on_output_select(self, event):
        selected_items = self.output_tree.selection()
        if not selected_items:
            self.selected_output_config_id = None
            self._clear_settings_fields()
            return

        self.selected_output_config_id = selected_items[0]
        oc = self._get_selected_output_config()
        if oc:
            self.oc_name_var.set(oc.name)
            self.oc_mode_var.set(oc.mode)
            self._update_tabs_for_mode(oc.mode) # Show correct tab

            self.oc_encoder_var.set(oc.encoder or "")
            # Quality loading needs to be smarter based on encoder/mode
            self._load_quality_settings_for_oc(oc)
            self.oc_preset_var.set(oc.preset or "")
            self.oc_custom_flags_var.set(oc.custom_flags or "")
            self.oc_container_var.set(oc.container or self._default_container_for_mode(oc.mode))

            # Ensure encoder selection triggers UI update for quality controls
            self._on_encoder_selected(None)


    def _on_output_mode_change(self, event=None):
        new_mode = self.oc_mode_var.get()
        if self.selected_output_config_id:
            oc = self._get_selected_output_config()
            if oc and oc.mode != new_mode:
                # Mode changed, update the actual OutputConfig object
                oc.mode = new_mode
                # Reset encoder/quality/container for the new mode? Or try to keep?
                # For now, let's clear them to avoid incompatible settings.
                oc.encoder = ""
                oc.quality = "" # Generic quality field
                oc.cq_value = "" # Specific CRF/CQ
                oc.bitrate = ""  # Specific bitrate
                oc.preset = ""
                oc.container = self._default_container_for_mode(new_mode)
                # Reload UI for this OC
                self._on_output_select(None) # This will re-populate based on new mode and cleared fields

        self._update_tabs_for_mode(new_mode)
        self._on_encoder_selected(None) # Update quality UI for default encoder of new mode

    def _default_container_for_mode(self, mode:str) -> str:
        if mode == "video": return "mp4"
        if mode == "audio": return "m4a"
        if mode == "image": return "png"
        return ""

    def _update_tabs_for_mode(self, mode: str):
        # Remove all tabs first
        for tab_id in self.settings_notebook.tabs():
            self.settings_notebook.forget(tab_id)

        if mode == "video":
            self.settings_notebook.add(self.video_tab, text="Video Settings")
            self._update_encoder_list_for_tab("video")
        elif mode == "audio":
            self.settings_notebook.add(self.audio_tab, text="Audio Settings")
            self._update_encoder_list_for_tab("audio")
        elif mode == "image":
            self.settings_notebook.add(self.image_tab, text="Image Settings")
            self._update_encoder_list_for_tab("image")

        # Try to re-select the encoder if it's valid for the new mode/tab
        current_encoder = self.oc_encoder_var.get()
        active_combo = None
        if mode == "video": active_combo = self.video_encoder_combo
        elif mode == "audio": active_combo = self.audio_encoder_combo
        elif mode == "image": active_combo = self.image_encoder_combo

        if active_combo:
            if current_encoder in active_combo['values']:
                active_combo.set(current_encoder)
            elif active_combo['values']:
                active_combo.set(active_combo['values'][0]) # Default to first if current invalid
                self.oc_encoder_var.set(active_combo.get())
            else:
                self.oc_encoder_var.set("")


    def _clear_settings_fields(self):
        self.oc_name_var.set("")
        self.oc_mode_var.set("")
        self.oc_encoder_var.set("")
        self.oc_quality_var.set("")
        self.oc_preset_var.set("")
        self.oc_custom_flags_var.set("")
        self.oc_container_var.set("")
        # Remove all tabs from notebook
        for tab_id in self.settings_notebook.tabs():
            self.settings_notebook.forget(tab_id)


    def _add_output_config(self):
        new_id = str(uuid.uuid4())
        count = len(self.job.outputs) + 1
        # Determine initial_dst_path (this is tricky without full context of main window's output folder logic)
        # For now, a simple placeholder based on source and new output name
        new_name = f"Output {count}"
        dst_path = self.job.src_path.parent / f"{self.job.src_path.stem}_{new_name.replace(' ', '_')}.mp4"

        new_oc = OutputConfig(name=new_name, initial_dst_path=dst_path, mode=self.job.mode) # Default to job's primary mode
        new_oc.id = new_id # Ensure the new OC gets the new_id

        # Sensible defaults based on mode
        if new_oc.mode == "video":
            new_oc.encoder = "libx264"
            new_oc.quality = "23"
            new_oc.preset = "medium"
            new_oc.container = "mp4"
        elif new_oc.mode == "audio":
            new_oc.encoder = "aac"
            new_oc.quality = "128k" # Assuming quality var holds bitrate for audio
            new_oc.container = "m4a"
        elif new_oc.mode == "image":
            new_oc.encoder = "png" # Or a more common one like 'mjpeg' for jpg
            new_oc.quality = "90" # Assuming quality var holds percentage for image
            new_oc.container = "png"

        self.job.outputs.append(new_oc)
        self._load_output_configs_to_tree()
        self.output_tree.selection_set(new_oc.id)
        self.output_tree.focus(new_oc.id)

    def _remove_output_config(self):
        if not self.selected_output_config_id:
            messagebox.showwarning("No Selection", "Please select an output configuration to remove.")
            return
        if len(self.job.outputs) <= 1:
            messagebox.showerror("Cannot Remove", "At least one output configuration must remain.")
            return

        oc_to_remove = self._get_selected_output_config()
        if oc_to_remove:
            self.job.outputs.remove(oc_to_remove)
            self._load_output_configs_to_tree()
            # Select another item or clear selection
            if self.job.outputs:
                self.output_tree.selection_set(self.job.outputs[0].id)
                self.output_tree.focus(self.job.outputs[0].id)
            else:
                self._clear_settings_fields() # Should not happen due to len check

    def _duplicate_output_config(self):
        if not self.selected_output_config_id:
            messagebox.showwarning("No Selection", "Please select an output configuration to duplicate.")
            return

        original_oc = self._get_selected_output_config()
        if original_oc:
            new_oc = copy.deepcopy(original_oc)
            new_oc.id = str(uuid.uuid4())
            new_oc.name = f"{original_oc.name} (Copy)"
            # Adjust dst_path to avoid overwrite - this needs more robust logic
            new_oc.dst_path = new_oc.dst_path.parent / f"{new_oc.dst_path.stem}_copy{new_oc.dst_path.suffix}"

            self.job.outputs.append(new_oc)
            self._load_output_configs_to_tree()
            self.output_tree.selection_set(new_oc.id)
            self.output_tree.focus(new_oc.id)

    def _save_selected_output_config(self):
        oc = self._get_selected_output_config()
        if not oc:
            # messagebox.showwarning("No Output Selected", "Cannot save, no output is selected in the list.")
            return False # Indicate save failed or nothing to save

        oc.name = self.oc_name_var.get()
        oc.mode = self.oc_mode_var.get() # Mode is already updated in OC object by _on_output_mode_change
        oc.encoder = self.oc_encoder_var.get()
        oc.quality = self.oc_quality_var.get() # This might be CRF, bitrate, or quality %
        # TODO: Need to differentiate how quality is stored (e.g. oc.cq_value, oc.bitrate)
        # Intelligent saving of quality based on mode/encoder
        self._save_quality_settings_for_oc(oc)

        oc.preset = self.oc_preset_var.get()
        oc.custom_flags = self.oc_custom_flags_var.get()
        oc.container = self.oc_container_var.get()

        # Update tree display for the saved item
        quality_display = self._get_quality_display_for_oc(oc)
        self.output_tree.item(oc.id, values=(oc.name, oc.encoder, quality_display))
        return True

    def _load_quality_settings_for_oc(self, oc: OutputConfig):
        """Populates oc_quality_var based on OutputConfig's mode and encoder."""
        encoder = oc.encoder.lower() if oc.encoder else ""
        mode = oc.mode

        if mode == "video":
            if "x264" in encoder or "x265" in encoder or "libvpx" in encoder or "svtav1" in encoder or "aom" in encoder: # CRF-based
                self.oc_quality_var.set(oc.cq_value or "23")
            elif "qsv" in encoder or "nvenc" in encoder or "amf" in encoder or "videotoolbox" in encoder : # Can be quality or bitrate based
                 # Prefer cq_value if present (global_quality for QSV, some equivalent for others)
                if oc.cq_value:
                    self.oc_quality_var.set(oc.cq_value)
                elif oc.bitrate: # Fallback to bitrate if that's what was set
                    self.oc_quality_var.set(oc.bitrate.replace('k','')) # Store as number string
                else: # Default for these if nothing set
                    self.oc_quality_var.set("23") # A generic good quality/CRF like value
            else: # Other video encoders, might use bitrate or a generic quality
                self.oc_quality_var.set(oc.bitrate.replace('k','') if oc.bitrate else (oc.quality or "23"))
        elif mode == "audio":
            if "flac" in encoder:
                self.oc_quality_var.set(oc.quality or "5") # FLAC compression level 0-11 (or 0-8/0-9 in some ffmpeg versions)
            else: # AAC, MP3, Opus, Vorbis typically use bitrate
                self.oc_quality_var.set(oc.bitrate.replace('k','') if oc.bitrate else (oc.quality or "128"))
        elif mode == "image":
            # Most image encoders use a quality percentage (PNG uses compression level)
            if "png" in encoder:
                 self.oc_quality_var.set(oc.quality or "6") # PNG compression 0-9 or similar for ffmpeg
            else: # webp, jpeg, avif, jxl
                 self.oc_quality_var.set(oc.quality or "90")
        else:
            self.oc_quality_var.set(oc.quality or "")

    def _save_quality_settings_for_oc(self, oc: OutputConfig):
        """Saves oc_quality_var to the correct field in OutputConfig based on mode/encoder."""
        encoder = oc.encoder.lower() if oc.encoder else ""
        mode = oc.mode
        quality_val = self.oc_quality_var.get()

        # Clear all quality fields first to avoid stale values
        oc.quality = ""
        oc.cq_value = ""
        oc.bitrate = ""

        if mode == "video":
            if "x264" in encoder or "x265" in encoder or "libvpx" in encoder or "svtav1" in encoder or "aom" in encoder: # CRF-based
                oc.cq_value = quality_val
            elif "qsv" in encoder or "nvenc" in encoder or "amf" in encoder or "videotoolbox" in encoder:
                # These can be complex. For now, assume if it looks like bitrate (ends with k or M), it's bitrate.
                # Otherwise, assume it's a CRF/CQ-like value. This needs refinement.
                # A better approach might be a separate UI control for "quality mode" (CRF vs Bitrate) for these encoders.
                # For now, let's assume user input '23' is CRF/CQ, and '4000k' is bitrate
                if quality_val.isdigit(): # Simple check, assumes integer is CRF/CQ
                    oc.cq_value = quality_val
                else: # Try to parse as bitrate, or store as generic quality if not fitting
                    oc.bitrate = quality_val # Let ffmpeg parse "4000" or "4000k"
                    # If it was meant to be a non-integer CQ (some encoders might support float), this logic fails.
            else: # Other video encoders, assume bitrate if it has 'k', else generic quality
                if 'k' in quality_val.lower() or 'm' in quality_val.lower() or quality_val.isdigit():
                    oc.bitrate = quality_val if 'k' in quality_val.lower() or 'm' in quality_val.lower() else quality_val + "k"
                else:
                    oc.quality = quality_val # Generic quality
        elif mode == "audio":
            if "flac" in encoder:
                oc.quality = quality_val # FLAC compression level
            else: # AAC, MP3, Opus, Vorbis typically use bitrate
                oc.bitrate = quality_val + "k" if quality_val.isdigit() else quality_val
        elif mode == "image":
            oc.quality = quality_val # Quality percentage or PNG compression level
        else:
            oc.quality = quality_val

    def _get_quality_display_for_oc(self, oc: OutputConfig) -> str:
        """Gets a string representation of quality for the treeview."""
        if oc.cq_value: return oc.cq_value
        if oc.bitrate: return oc.bitrate
        if oc.quality: return oc.quality
        return "-"

    def _on_encoder_selected(self, event=None):
        """Updates quality label and default value based on selected encoder."""
        selected_encoder = self.oc_encoder_var.get().lower()
        current_mode = self.oc_mode_var.get()
        quality_label_widget = None
        new_label_text = "Quality:"
        current_quality_val = self.oc_quality_var.get()
        new_default_quality = ""

        if current_mode == "video":
            quality_label_widget = self.video_quality_label
            if "x264" in selected_encoder or "x265" in selected_encoder or "libvpx" in selected_encoder or "svtav1" in selected_encoder or "aom" in selected_encoder:
                new_label_text = "CRF Value:"
                new_default_quality = "23"
            elif "qsv" in selected_encoder or "nvenc" in selected_encoder or "amf" in selected_encoder or "videotoolbox" in selected_encoder:
                new_label_text = "CQ/CRF or Bitrate:" # User needs to know '23' vs '4000k'
                new_default_quality = "23" # Default to CQ/CRF like
            else: # Other video (e.g. mpeg4, prores)
                new_label_text = "Bitrate (e.g. 4000k):"
                new_default_quality = "4000k"
        elif current_mode == "audio":
            quality_label_widget = self.audio_quality_label
            if "flac" in selected_encoder:
                new_label_text = "FLAC Level (0-11):"
                new_default_quality = "5"
            elif selected_encoder in ["aac", "libfdk_aac", "mp3", "libmp3lame", "opus", "libopus", "vorbis", "libvorbis"]:
                new_label_text = "Bitrate (e.g. 128k):"
                new_default_quality = "128k"
            else: # Other audio
                new_label_text = "Quality/Bitrate:"
                new_default_quality = "128k"
        elif current_mode == "image":
            quality_label_widget = self.image_quality_label
            if "png" in selected_encoder:
                new_label_text = "PNG Comp. (0-9):"
                new_default_quality = "6"
            elif selected_encoder in ["jpeg", "mjpeg", "webp", "libwebp", "libaom-av1", "libjxl", "hevc"]: # hevc for heic
                new_label_text = "Quality % (0-100):"
                new_default_quality = "90"
            else: # Other image
                new_label_text = "Quality:"
                new_default_quality = "90"

        if quality_label_widget:
            quality_label_widget.config(text=new_label_text)

        # Set a default quality if current is empty or nonsensical for the new type
        # This is a simple check; more robust validation might be needed.
        if not current_quality_val: # Or add more checks, e.g. if current_quality_val is not suitable for new_label_text
            self.oc_quality_var.set(new_default_quality)
        # If user had a value, keep it for now. Save logic will try to interpret it.


    def _apply_and_close(self):
        # Save the currently selected/edited output config first
        if not self._save_selected_output_config() and self.selected_output_config_id:
            # If save failed (e.g. no OC was actually selected to populate vars, but an ID was stored)
            # it might mean the UI was in a weird state. For now, proceed to close.
            # A more robust check might be needed.
            pass

        # The self.job object in this window is a reference to the job in MainWindow.
        # Changes to job.outputs (adding, removing, modifying OutputConfig objects)
        # are directly reflected in the MainWindow's job list.

        # Update the main window's tree view for this job
        # This requires a way to tell MainWindow to refresh a specific job row.
        # Assuming MainWindow has a method like `_update_job_row(job_object)`
        if hasattr(self.master, '_update_job_row'):
            self.master._update_job_row(self.job)

        # Also update the job selector combobox in MainWindow if it exists and is tracking this job
        if hasattr(self.master, '_update_job_selector_combobox'):
            self.master._update_job_selector_combobox()


        self.top.destroy()
