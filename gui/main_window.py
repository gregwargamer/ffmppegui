import json
import os
import subprocess
import threading
from pathlib import Path
from tkinter import (Tk, filedialog, ttk, Menu, messagebox, StringVar, BooleanVar, Text, Toplevel, IntVar, DoubleVar)
import tkinter as tk

from core.encode_job import EncodeJob
from core.ffmpeg_helpers import FFmpegHelpers
from core.settings import Settings
from core.worker_pool import WorkerPool
from gui.settings_window import SettingsWindow
from gui.job_edit_window import JobEditWindow
from gui.log_viewer_window import LogViewerWindow
from gui.batch_operations_window import BatchOperationsWindow
from gui.advanced_filters_window import AdvancedFiltersWindow
from gui.audio_tracks_window import AudioTracksWindow


try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

try:
    import psutil  # type: ignore
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    psutil = None
    DND_AVAILABLE = False

# Ajout des imports pour watchdog (surveillance de dossier)
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
except ImportError:
    Observer = None
    class FileSystemEventHandler:
        pass

class MainWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("FFmpeg Frontend")
        self.root.geometry("1200x800")
        
        # Liste des jobs
        self.jobs: list[EncodeJob] = []
        self.job_rows = {}
        
        # Variables d'état
        self.is_running = False
        self.input_folder = StringVar()
        self.output_folder = StringVar()
        
        # Variable pour la surveillance de dossier
        self.watch_var = BooleanVar(value=False)
        self.log_viewer = None
        
        # Variables pour l'inspecteur média
        self.resolution_var = StringVar(value="N/A")
        self.duration_var = StringVar(value="N/A")
        self.vcodec_var = StringVar(value="N/A")
        self.vbitrate_var = StringVar(value="N/A")
        self.acodec_var = StringVar(value="N/A")
        self.abitrate_var = StringVar(value="N/A")
        self.achannels_var = StringVar(value="N/A")
        
        # Pool de workers
        self.pool = WorkerPool(
            max_workers=Settings.data.get("concurrency", 4),
            progress_callback=self._on_job_progress,
            log_callback=self._on_job_log
        )
        
        self._build_menu()
        self._build_layout()
        self._setup_drag_drop()
        self._update_preset_list()
        
        # Démarrer le pool
        self.pool.start()

    # === GUI construction ===
    def _build_menu(self):
        menubar = Menu(self.root)
        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(label="Add Files…", command=self._add_files)
        file_menu.add_command(label="Add Folder…", command=self._add_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Add Files or Folder…", command=self._add_files_or_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)
        
        # Menu Edit
        edit_menu = Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Batch Operations", command=self._batch_operations)
        edit_menu.add_command(label="Advanced Filters", command=self._advanced_filters)
        edit_menu.add_command(label="Audio Tracks", command=self._configure_audio_tracks)
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear Queue", command=self._clear_queue)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        # Menu Presets
        preset_menu = Menu(menubar, tearoff=0)
        preset_menu.add_command(label="Save Current as Preset…", command=self._save_preset)
        preset_menu.add_separator()
        # Ajouter les presets existants au menu
        for preset_name in Settings.data["presets"].keys():
            preset_menu.add_command(
                label=preset_name, 
                command=lambda name=preset_name: self._load_preset_by_name(name)
            )
        menubar.add_cascade(label="Presets", menu=preset_menu)
        
        # Menu View
        view_menu = Menu(menubar, tearoff=0)
        view_menu.add_command(label="Show Log Viewer", command=self._show_log_viewer)
        menubar.add_cascade(label="View", menu=view_menu)

        settings_menu = Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Preferences…", command=self._open_settings)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menubar)

    def _build_layout(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Onglets pour différents types de contenu
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        file_section = ttk.LabelFrame(notebook, text="File Selection", padding=15)
        file_section.pack(fill="x", pady=(0, 15))

        settings_section = ttk.LabelFrame(notebook, text="Encoding Settings", padding=15)
        settings_section.pack(fill="x", pady=(0, 15))

        queue_section = ttk.LabelFrame(notebook, text="Encoding Queue", padding=10)
        queue_section.pack(fill="both", expand=True, pady=(0, 15))

        inspector_frame = ttk.LabelFrame(notebook, text="Inspecteur média", padding=10)
        inspector_frame.pack(fill="x", pady=(0, 15))

        notebook.add(file_section, text="Sélection de fichiers")
        notebook.add(settings_section, text="Paramètres d'encodage")
        notebook.add(inspector_frame, text="Inspecteur média")
        notebook.add(queue_section, text="File d'attente")

        # === MEDIA INSPECTOR SECTION ===
        paned_window = ttk.PanedWindow(inspector_frame, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True)

        # Left pane: file list
        inspector_list_frame = ttk.Frame(paned_window, width=300)
        paned_window.add(inspector_list_frame, weight=1)

        ttk.Label(inspector_list_frame, text="Fichiers en file d'attente", font=("Helvetica", 10, "bold")).pack(pady=(0, 5))
        
        self.inspector_tree = ttk.Treeview(inspector_list_frame, columns=("file",), show="headings", height=10)
        self.inspector_tree.heading("file", text="Fichier")
        self.inspector_tree.pack(fill=tk.BOTH, expand=True)
        self.inspector_tree.bind("<<TreeviewSelect>>", self._on_inspector_selection_change)

        # Right pane: media info (will be populated dynamically)
        self.inspector_info_frame = ttk.Frame(paned_window)
        paned_window.add(self.inspector_info_frame, weight=2)
        
        # === FILE SELECTION SECTION ===
        self.input_folder = StringVar(value="No input folder selected")
        self.output_folder = StringVar(value="No output folder selected")

        # Clean folder selection grid
        folder_grid = ttk.Frame(file_section)
        folder_grid.pack(fill="x")

        # Input folder
        ttk.Label(folder_grid, text="Input:", font=("Helvetica", 11, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.input_folder_entry = ttk.Entry(folder_grid, textvariable=self.input_folder, width=60, font=("Helvetica", 10))
        self.input_folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Button(folder_grid, text="Browse", command=self._select_input_folder, width=8).grid(row=0, column=2)

        # Output folder
        ttk.Label(folder_grid, text="Output:", font=("Helvetica", 11, "bold")).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        self.output_folder_entry = ttk.Entry(folder_grid, textvariable=self.output_folder, width=60, font=("Helvetica", 10))
        self.output_folder_entry.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(8, 0))
        ttk.Button(folder_grid, text="Browse", command=self._select_output_folder, width=8).grid(row=1, column=2, pady=(8, 0))
        
        # Info label for output behavior
        info_label = ttk.Label(folder_grid, text="Optional: If no output folder is selected, files will be saved in the same folder as source with encoder suffix (e.g., filename_x265.mp4)", 
                              font=("Helvetica", 9), foreground="gray")
        info_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(5, 0))

        folder_grid.columnconfigure(1, weight=1)

        # Add buttons row
        buttons_row = ttk.Frame(file_section)
        buttons_row.pack(fill="x", pady=(15, 0))
        
        ttk.Button(buttons_row, text="Add Files", command=self._add_files).pack(side="left", padx=(0, 10))
        ttk.Button(buttons_row, text="Add Folder", command=self._add_folder).pack(side="left", padx=(0, 10))
        ttk.Button(buttons_row, text="Find Files in Input Folder", command=self._find_and_add_files).pack(side="left")

        # Ajout du cadre pour la surveillance de dossier
        watch_frame = ttk.LabelFrame(file_section, text="Surveillance de dossier", padding="5")
        watch_frame.pack(fill=tk.X, pady=(15, 0))
        watch_toggle = ttk.Checkbutton(watch_frame, text="Surveiller le dossier d'entrée", variable=self.watch_var, command=self._toggle_watch)
        watch_toggle.pack(side=tk.TOP, fill=tk.X)
        
        preset_frame = ttk.Frame(watch_frame)
        preset_frame.pack(fill=tk.X, pady=5)
        ttk.Label(preset_frame, text="Préréglage pour les nouveaux fichiers:").pack(side=tk.LEFT)
        self.watch_preset_combo = ttk.Combobox(preset_frame, state="readonly")
        self.watch_preset_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        preset_names = list(Settings.data.get("presets", {}).keys())
        if preset_names:
            self.watch_preset_combo['values'] = preset_names
            self.watch_preset_combo.set(preset_names[0])
        
        self.watch_status = ttk.Label(watch_frame, text="Statut: Inactif")
        self.watch_status.pack(side=tk.BOTTOM, fill=tk.X, pady=5)

        # === ENCODING SETTINGS SECTION ===
        # File selection for preview
        file_select_row = ttk.Frame(settings_section)
        file_select_row.pack(fill="x", pady=(0, 10))
        
        ttk.Label(file_select_row, text="Selected File:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.selected_file_var = StringVar(value="No file selected")
        self.selected_file_combo = ttk.Combobox(file_select_row, textvariable=self.selected_file_var, width=50, state="readonly")
        self.selected_file_combo.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.selected_file_combo.bind("<<ComboboxSelected>>", self._on_file_selection_change)
        
        # Top row - Media Type and Quick Presets
        top_row = ttk.Frame(settings_section)
        top_row.pack(fill="x", pady=(0, 15))

        ttk.Label(top_row, text="Media Type:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.global_type_var = StringVar(value="video")
        self.global_codec_var = StringVar(value="")
        self.global_encoder_var = StringVar(value="")
        type_combo = ttk.Combobox(top_row, textvariable=self.global_type_var, 
                                 values=["video", "audio", "image"], width=10, state="readonly")
        type_combo.pack(side="left", padx=(0, 20))
        type_combo.bind("<<ComboboxSelected>>", lambda e: self._update_codec_choices())

        ttk.Label(top_row, text="Quick Presets:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.preset_name_var = StringVar(value="")
        self.preset_combo = ttk.Combobox(top_row, textvariable=self.preset_name_var, width=18, state="readonly")
        self.preset_combo.pack(side="left", padx=(0, 10))
        self.preset_combo.bind("<<ComboboxSelected>>", self._load_preset)

        ttk.Button(top_row, text="Save", command=self._save_preset, width=4).pack(side="left", padx=(2, 2))
        ttk.Button(top_row, text="Delete", command=self._delete_preset, width=5).pack(side="left")

        # Codec and Encoder rows
        codec_encoder_frame = ttk.Frame(settings_section)
        codec_encoder_frame.pack(fill="x", pady=(0, 15))

        # Codec selection row
        codec_row = ttk.Frame(codec_encoder_frame)
        codec_row.pack(fill="x", pady=(0, 8))

        ttk.Label(codec_row, text="1. Codec:", font=("Helvetica", 10, "bold"), width=10).pack(side="left", padx=(0, 5))
        self.global_codec_combo = ttk.Combobox(codec_row, textvariable=self.global_codec_var, width=25, state="readonly")
        self.global_codec_combo.pack(side="left", padx=(0, 10))
        self.global_codec_combo.bind("<<ComboboxSelected>>", lambda e: self._update_encoder_choices())

        # Help text for codec button
        ttk.Label(codec_row, text="Smart apply", font=("Helvetica", 8), foreground="gray").pack(side="right", padx=(5, 5))
        ttk.Button(codec_row, text="Apply Codec", command=self._apply_codec_smart).pack(side="right")

        # Encoder selection row  
        encoder_row = ttk.Frame(codec_encoder_frame)
        encoder_row.pack(fill="x")
        self.encoder_row = encoder_row  # Stocker la référence pour modification dynamique

        ttk.Label(encoder_row, text="2. Encoder:", font=("Helvetica", 10, "bold"), width=10).pack(side="left", padx=(0, 5))
        self.global_encoder_combo = ttk.Combobox(encoder_row, textvariable=self.global_encoder_var, width=50, state="readonly")
        self.global_encoder_combo.pack(side="left", fill="x", expand=True)
        self.global_encoder_combo.bind("<<ComboboxSelected>>", lambda e: self._update_quality_preset_controls())
        
        # Add help text
        help_label = ttk.Label(encoder_row, text="(Only compatible encoders)", font=("Helvetica", 9), foreground="gray")
        help_label.pack(side="right", padx=(10, 0))

        # Quality Controls
        quality_frame = ttk.Frame(settings_section)
        quality_frame.pack(fill="x", pady=(0, 15))

        # Quality settings row
        quality_row = ttk.Frame(quality_frame)
        quality_row.pack(fill="x", pady=(0, 8))

        # Video encoding mode selector
        self.video_mode_var = StringVar(value="quality")  # quality, bitrate
        self.video_mode_frame = ttk.Frame(quality_row)
        
        ttk.Label(quality_row, text="Quality/CRF:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.quality_var = StringVar(value="")
        self.quality_entry = ttk.Entry(quality_row, textvariable=self.quality_var, width=8)
        self.quality_entry.pack(side="left", padx=(0, 10))

        ttk.Label(quality_row, text="CQ:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.cq_var = StringVar(value="")
        self.cq_entry = ttk.Entry(quality_row, textvariable=self.cq_var, width=8)
        self.cq_entry.pack(side="left", padx=(0, 15))

        # Bitrate controls (initially hidden)
        self.bitrate_label = ttk.Label(quality_row, text="Bitrate:", font=("Helvetica", 10, "bold"))
        self.bitrate_var = StringVar(value="")
        self.bitrate_entry = ttk.Entry(quality_row, textvariable=self.bitrate_var, width=8)
        
        # Multi-pass controls (initially hidden)
        self.multipass_var = BooleanVar(value=False)
        self.multipass_check = ttk.Checkbutton(quality_row, text="Multi-pass", variable=self.multipass_var)

        ttk.Label(quality_row, text="Preset:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.preset_var = StringVar(value="")
        self.preset_combo_quality = ttk.Combobox(quality_row, textvariable=self.preset_var, width=10, state="readonly")
        self.preset_combo_quality.pack(side="left", padx=(0, 15))

        ttk.Label(quality_row, text="Container:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.container_var = StringVar(value="MP4")
        self.container_combo = ttk.Combobox(quality_row, textvariable=self.container_var, 
                                           width=12, state="readonly")
        self.container_combo.pack(side="left")
        self._update_container_choices()

        # Mode selector row (for video)
        mode_row = ttk.Frame(quality_frame)
        self.mode_row = mode_row  # Store reference for showing/hiding
        
        ttk.Label(mode_row, text="Encoding Mode:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        quality_radio = ttk.Radiobutton(mode_row, text="Quality (CRF)", variable=self.video_mode_var, value="quality", command=self._on_video_mode_change)
        quality_radio.pack(side="left", padx=(0, 10))
        bitrate_radio = ttk.Radiobutton(mode_row, text="Bitrate (CBR/VBR)", variable=self.video_mode_var, value="bitrate", command=self._on_video_mode_change)
        bitrate_radio.pack(side="left")

        # Resolution row
        resolution_row = ttk.Frame(quality_frame)
        resolution_row.pack(fill="x", pady=(8, 0))

        ttk.Label(resolution_row, text="Resolution:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.resolution_var_settings = StringVar(value="Original")
        self.resolution_combo = ttk.Combobox(resolution_row, textvariable=self.resolution_var_settings, 
                                           values=["Original", "3840x2160 (4K)", "1920x1080 (1080p)", "1280x720 (720p)", 
                                                  "854x480 (480p)", "640x360 (360p)", 
                                                  "2160x3840 (4K Portrait)", "1080x1920 (1080p Portrait)", 
                                                  "720x1280 (720p Portrait)", "480x854 (480p Portrait)", "Custom"], 
                                           width=25, state="readonly")
        self.resolution_combo.pack(side="left", padx=(0, 10))
        self.resolution_combo.bind("<<ComboboxSelected>>", self._on_resolution_change)
        
        # Image-specific resolution controls (hidden by default)
        self.image_res_frame = ttk.Frame(resolution_row)
        ttk.Label(self.image_res_frame, text="Longest side:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(10, 5))
        self.longest_side_var = StringVar(value="")
        self.longest_side_combo = ttk.Combobox(self.image_res_frame, textvariable=self.longest_side_var,
                                             values=["Original", "5200", "4096", "3840", "2560", "1920", "1280", "Custom"],
                                             width=10, state="readonly")
        self.longest_side_combo.pack(side="left", padx=(0, 10))
        self.longest_side_combo.bind("<<ComboboxSelected>>", self._on_longest_side_change)
        
        ttk.Label(self.image_res_frame, text="Megapixels:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(10, 5))
        self.megapixels_var = StringVar(value="")
        self.megapixels_combo = ttk.Combobox(self.image_res_frame, textvariable=self.megapixels_var,
                                           values=["Original", "50", "25", "16", "12", "8", "4", "2", "Custom"],
                                           width=8, state="readonly")
        self.megapixels_combo.pack(side="left")
        self.megapixels_combo.bind("<<ComboboxSelected>>", self._on_megapixels_change)
        
        # Custom resolution fields (hidden by default)
        self.custom_width_var = StringVar(value="")
        self.custom_height_var = StringVar(value="")
        self.width_entry = ttk.Entry(resolution_row, textvariable=self.custom_width_var, width=8)
        self.height_entry = ttk.Entry(resolution_row, textvariable=self.custom_height_var, width=8)
        self.x_label = ttk.Label(resolution_row, text="x")
        
        # Custom longest side entry (hidden by default)
        self.custom_longest_var = StringVar(value="")
        self.custom_longest_entry = ttk.Entry(self.image_res_frame, textvariable=self.custom_longest_var, width=8)
        
        # Custom megapixels entry (hidden by default)
        self.custom_mp_var = StringVar(value="")
        self.custom_mp_entry = ttk.Entry(self.image_res_frame, textvariable=self.custom_mp_var, width=8)

        # Ajout des contrôles de recadrage pour la vidéo
        self.crop_frame = ttk.Frame(resolution_row)
        ttk.Label(self.crop_frame, text="Crop (px):", font=("Helvetica", 10, "bold")).pack(side="left", padx=(10, 5))
        
        ttk.Label(self.crop_frame, text="L:").pack(side="left", padx=(0, 2))
        self.crop_left_var = StringVar(value="0")
        self.crop_left_entry = ttk.Entry(self.crop_frame, textvariable=self.crop_left_var, width=5)
        self.crop_left_entry.pack(side="left", padx=(0, 5))
        
        ttk.Label(self.crop_frame, text="R:").pack(side="left", padx=(0, 2))
        self.crop_right_var = StringVar(value="0")
        self.crop_right_entry = ttk.Entry(self.crop_frame, textvariable=self.crop_right_var, width=5)
        self.crop_right_entry.pack(side="left", padx=(0, 5))
        
        ttk.Label(self.crop_frame, text="T:").pack(side="left", padx=(0, 2))
        self.crop_top_var = StringVar(value="0")
        self.crop_top_entry = ttk.Entry(self.crop_frame, textvariable=self.crop_top_var, width=5)
        self.crop_top_entry.pack(side="left", padx=(0, 5))
        
        ttk.Label(self.crop_frame, text="B:").pack(side="left", padx=(0, 2))
        self.crop_bottom_var = StringVar(value="0")
        self.crop_bottom_entry = ttk.Entry(self.crop_frame, textvariable=self.crop_bottom_var, width=5)
        self.crop_bottom_entry.pack(side="left", padx=(0, 5))
        
        self.crop_frame.pack(side="left", padx=(10, 0))
        
        # Ajout des contrôles de timestamp et de prévisualisation de frame (pour vidéo uniquement)
        self.preview_row = ttk.Frame(quality_frame)
        self.preview_row.pack(fill="x", pady=(8, 0))
        
        ttk.Label(self.preview_row, text="Preview Timestamp:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.timestamp_var = StringVar(value="00:00:00")
        self.timestamp_entry = ttk.Entry(self.preview_row, textvariable=self.timestamp_var, width=10)
        self.timestamp_entry.pack(side="left", padx=(0, 10))
        
        ttk.Button(self.preview_row, text="Previous Frame", command=self._preview_previous_frame).pack(side="left", padx=(0, 5))
        ttk.Button(self.preview_row, text="Render Frame", command=self._render_preview_frame).pack(side="left", padx=(0, 5))
        ttk.Button(self.preview_row, text="Next Frame", command=self._preview_next_frame).pack(side="left", padx=(0, 5))
        
        # Cadre séparé pour afficher l'image de prévisualisation
        preview_image_row = ttk.Frame(quality_frame)
        preview_image_row.pack(fill="x", pady=(8, 0))
        
        self.preview_image_label = ttk.Label(preview_image_row, text="No preview available", relief="sunken", width=40)
        self.preview_image_label.pack(side="left", padx=(0, 10))

        # Custom flags row
        custom_row = ttk.Frame(quality_frame)
        custom_row.pack(fill="x", pady=(8, 0))

        ttk.Label(custom_row, text="Custom Flags:", font=("Helvetica", 10, "bold")).pack(side="left", padx=(0, 5))
        self.custom_flags_var = StringVar(value="")
        self.custom_flags_entry = ttk.Entry(custom_row, textvariable=self.custom_flags_var, width=50)
        self.custom_flags_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        # Help label for custom flags
        help_label = ttk.Label(custom_row, text="(Advanced: additional FFmpeg parameters)", 
                              font=("Helvetica", 9), foreground="gray")
        help_label.pack(side="right")

        # Action buttons row
        action_row = ttk.Frame(quality_frame)
        action_row.pack(fill="x", pady=(8, 0))

        apply_btn = ttk.Button(action_row, text="Apply Settings", command=self._apply_settings_smart)
        apply_btn.pack(side="left", padx=(0, 5))
        
        ttk.Button(action_row, text="Duplicate Selected", command=self._duplicate_selected).pack(side="left", padx=(0, 10))
        
        # Help text for Apply behavior
        help_text = ttk.Label(action_row, text="Applies to selected jobs or all jobs of current type if none selected", 
                             font=("Helvetica", 8), foreground="gray")
        help_text.pack(side="left", padx=(10, 0))

        self._update_preset_list()
        self._update_codec_choices()
        self._update_quality_preset_controls()
        
        # Initialiser l'interface pour le type par défaut
        self._update_media_type_ui(self.global_type_var.get())

        # === ENCODING QUEUE SECTION ===
        # Queue treeview with scrollbar
        queue_frame = ttk.Frame(queue_section)
        queue_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(queue_frame, columns=("file", "encoder", "quality", "progress", "status"), show="headings", height=12)
        for col, label in zip(self.tree["columns"], ["File", "Encoder", "Quality", "Progress", "Status"]):
            self.tree.heading(col, text=label)
            if col == "progress":
                self.tree.column(col, width=80)
            elif col == "status":
                self.tree.column(col, width=80)
            else:
                self.tree.column(col, width=150)

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_queue_selection_change)
        self.tree.bind("<Button-2>", self._on_right_click)  # macOS right-click
        self.tree.bind("<Button-3>", self._on_right_click)  # Windows/Linux right-click

        # Scrollbar for queue
        queue_scrollbar = ttk.Scrollbar(queue_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=queue_scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        queue_scrollbar.pack(side="right", fill="y")

        # Context menu for jobs
        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Edit Job", command=self._edit_selected_job)
        self.context_menu.add_command(label="Advanced Filters", command=self._advanced_filters)
        self.context_menu.add_command(label="Audio Tracks", command=self._configure_audio_tracks)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Pause", command=self._pause_selected_job)
        self.context_menu.add_command(label="Resume", command=self._resume_selected_job)
        self.context_menu.add_command(label="Cancel", command=self._cancel_selected_job)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Batch Operations", command=self._batch_operations)
        self.context_menu.add_command(label="Remove", command=self._remove_selected_job)

        # === CONTROL PANEL ===
        control_panel = ttk.Frame(main_frame)
        control_panel.pack(fill="x")

        # Progress bar
        self.progress_var = StringVar(value="0%")
        self.progress_bar = ttk.Progressbar(control_panel, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=(0, 10))

        # Control buttons
        button_panel = ttk.Frame(control_panel)
        button_panel.pack(fill="x")

        # Main action button (larger and prominent)
        self.start_btn = ttk.Button(button_panel, text="Start Encoding", command=self._start_encoding)
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # Control buttons (compact icons)
        control_buttons = ttk.Frame(button_panel)
        control_buttons.pack(side="right")

        self.pause_btn = ttk.Button(control_buttons, text="Pause", command=self._pause_all, state="disabled", width=5)
        self.pause_btn.pack(side="left", padx=(0, 2))

        self.resume_btn = ttk.Button(control_buttons, text="Resume", command=self._resume_all, state="disabled", width=6)
        self.resume_btn.pack(side="left", padx=(0, 2))

        self.cancel_btn = ttk.Button(control_buttons, text="Cancel", command=self._cancel_all, state="disabled", width=6)
        self.cancel_btn.pack(side="left", padx=(0, 10))

        ttk.Button(control_buttons, text="Clear", command=self._clear_queue, width=5).pack(side="left")

        # Initialize drag & drop
        if DND_AVAILABLE:
            self._setup_drag_drop()

    # === Callbacks ===
    def _add_files(self):
        paths = filedialog.askopenfilenames(title="Select input files")
        if not paths:
            return
        self._enqueue_paths([Path(p) for p in paths])

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select input folder")
        if not folder:
            return
        root_path = Path(folder)
        all_files = [p for p in root_path.rglob("*") if p.is_file()]
        self._enqueue_paths(all_files)

    def _add_files_or_folder(self):
        """Offre un choix entre ajouter des fichiers ou un dossier"""
        from tkinter import messagebox
        
        choice = messagebox.askyesnocancel(
            "Add Files or Folder",
            "What would you like to add?\n\n"
            "• Yes = Select multiple files\n"
            "• No = Select a folder\n"
            "• Cancel = Nothing",
            icon='question'
        )
        
        if choice is True:  # Yes - Files
            self._add_files()
        elif choice is False:  # No - Folder
            self._add_folder()
        # choice is None = Cancel, do nothing

    def _enqueue_paths(self, paths: list[Path]):
        out_root = Path(self.output_folder.get()) if self.output_folder.get() and not self.output_folder.get().startswith("(no") else None
        keep_structure = Settings.data.get("keep_folder_structure", True)
        input_folder = self.input_folder.get()
        
        for p in paths:
            mode = self._detect_mode(p)
            if self.global_type_var.get() == "unknown":
                self.global_type_var.set(mode)
            
            # Calculer le chemin relatif de manière sécurisée
            if out_root and keep_structure and input_folder and not input_folder.startswith("(no"):
                try:
                    # Essayer de calculer le chemin relatif
                    input_path = Path(input_folder)
                    relative = p.relative_to(input_path)
                except (ValueError, OSError):
                    # Le fichier n'est pas dans le dossier d'entrée ou erreur de calcul
                    # Utiliser juste le nom du fichier
                    relative = p.name
            else:
                relative = p.name
            
            # Génération intelligente du chemin de sortie
            container = self._get_container_from_display(self.container_var.get())
            
            if out_root:
                # Dossier de sortie spécifié
                dst_basename = relative if isinstance(relative, Path) else Path(relative)
                dst_path = out_root / dst_basename
                dst_path = dst_path.with_suffix("." + container)
                dst_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                # Pas de dossier de sortie - utiliser le même dossier que la source avec suffixe
                # Déterminer le suffixe basé sur l'encodeur/codec sélectionné
                encoder_display = self.global_encoder_var.get()
                encoder_name = self._get_encoder_name_from_display(encoder_display) if encoder_display else ""
                
                # Générer un suffixe approprié
                if "x265" in encoder_name or "hevc" in encoder_name:
                    suffix = "_x265"
                elif "x264" in encoder_name or "h264" in encoder_name:
                    suffix = "_x264"
                elif "av1" in encoder_name:
                    suffix = "_av1"
                elif "vp9" in encoder_name:
                    suffix = "_vp9"
                elif "nvenc" in encoder_name:
                    suffix = "_nvenc"
                elif "qsv" in encoder_name:
                    suffix = "_qsv" 
                elif "amf" in encoder_name:
                    suffix = "_amf"
                elif "videotoolbox" in encoder_name:
                    suffix = "_vt"
                elif mode == "audio":
                    if "aac" in encoder_name:
                        suffix = "_aac"
                    elif "mp3" in encoder_name:
                        suffix = "_mp3"
                    elif "opus" in encoder_name:
                        suffix = "_opus"
                    elif "flac" in encoder_name:
                        suffix = "_flac"
                    else:
                        suffix = "_audio"
                elif mode == "image":
                    if "webp" in encoder_name:
                        suffix = "_webp"
                    elif "avif" in encoder_name:
                        suffix = "_avif"
                    else:
                        suffix = "_img"
                else:
                    suffix = "_encoded"
                
                # Créer le nouveau nom avec le suffixe
                stem = p.stem
                dst_path = p.parent / f"{stem}{suffix}.{container}"
            
            job = EncodeJob(src_path=p, dst_path=dst_path, mode=mode)
            # Apply default encoder based on mode
            if mode == "video":
                job.encoder = Settings.data.get("default_video_encoder")
                # Copier toutes les pistes audio par défaut
                job.copy_audio = True
            elif mode == "audio":
                job.encoder = Settings.data.get("default_audio_encoder")
            else:
                job.encoder = Settings.data.get("default_image_encoder")
            self.jobs.append(job)
            self.tree.insert("", "end", iid=str(id(job)), values=(p.name, "-", "-", "0%", "pending"))
            self.job_rows[str(id(job))] = {"job": job}
            # do not submit yet; submission happens when user presses Start Encoding
        
        self._update_inspector_file_list()
        # Mettre à jour l'état des boutons après avoir ajouté des jobs
        if not any(j.status in ["running", "paused"] for j in self.jobs):
            self._update_control_buttons_state("idle")

    def _detect_mode(self, path: Path) -> str:
        ext = path.suffix.lower()
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}
        if ext in video_exts:
            return "video"
        if ext in audio_exts:
            return "audio"
        if ext in image_exts:
            return "image"
        return "unknown"

    def _open_settings(self):
        SettingsWindow(self.root)

    def _on_double_click(self, event):
        item_id = self.tree.identify("item", event.x, event.y)
        if item_id:
            job = next((j for j in self.jobs if str(id(j)) == item_id), None)
            if job:
                JobEditWindow(self.root, job)

    def _select_input_folder(self):
        folder = filedialog.askdirectory(title="Select input folder")
        if folder:
            self.input_folder.set(folder)
            # Optionally, auto-enqueue files from this folder

    def _select_output_folder(self):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.output_folder.set(folder)

    def _find_and_add_files(self):
        folder = self.input_folder.get()
        if not folder or folder.startswith("(no input"):
            messagebox.showwarning("No Input Folder", "Please select an input folder first.")
            return
        root_path = Path(folder)
        if not root_path.exists() or not root_path.is_dir():
            messagebox.showerror("Invalid Folder", "The selected input folder does not exist or is not a directory.")
            return
        # Only add media files (video, audio, image)
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}
        all_files = [p for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() in (video_exts | audio_exts | image_exts)]
        if not all_files:
            messagebox.showinfo("No Media Files Found", "No media files found in the selected input folder.")
            return
        self._enqueue_paths(all_files)

    def _update_codec_choices(self):
        """Met à jour les choix de codecs basés sur le type de média sélectionné"""
        media_type = self.global_type_var.get()
        
        # Définir des codecs communs avec des noms conviviaux
        if media_type == "video":
            codec_choices = [
                ("H.264/AVC", "h264"),
                ("H.265/HEVC", "hevc"), 
                ("AV1", "av1"),
                ("VP9", "vp9"),
                ("VP8", "vp8"),
                ("MPEG-4", "mpeg4"),
                ("MPEG-2", "mpeg2video"),
                ("ProRes", "prores"),
                ("DNxHD", "dnxhd"),
                ("Remux (no re-encode)", "remux")
            ]
        elif media_type == "audio":
            codec_choices = [
                ("AAC", "aac"),
                ("MP3", "mp3"),
                ("Opus", "opus"),
                ("Vorbis", "vorbis"),
                ("FLAC", "flac"),
                ("ALAC", "alac"),
                ("AC3", "ac3"),
                ("PCM", "pcm_s16le"),
                ("WAV", "wav")
            ]
        else:  # image
            codec_choices = [
                ("WebP", "webp"),
                ("PNG", "png"),
                ("JPEG", "mjpeg"),
                ("BMP", "bmp"),
                ("TIFF", "tiff"),
                ("AVIF", "libaom-av1"),
                ("JPEG XL", "jpegxl")
            ]
        
        # Ajouter l'option personnalisée
        codec_choices.append(("Custom", "custom"))
        
        # Obtenir les codecs disponibles depuis FFmpeg
        available_codecs = FFmpegHelpers.available_codecs()
        codec_list = available_codecs.get(media_type, [])
        
        # Filtrer par codecs disponibles ou par codecs communs que nous savons fonctionner
        filtered_choices = []
        for display, codec in codec_choices:
            # Vérifier si le codec exact est disponible OU si on a des encodeurs pour ce codec
            if codec == "custom" or (codec in codec_list or 
                codec.lower() in [c.lower() for c in codec_list] or
                self._has_encoders_for_codec(codec)):
                filtered_choices.append((display, codec))
        
        # Si aucun codec trouvé, utiliser une liste par défaut
        if not filtered_choices:
            if media_type == "video":
                filtered_choices = [("H.264/AVC", "h264"), ("MPEG-4", "mpeg4")]
            elif media_type == "audio":
                filtered_choices = [("AAC", "aac"), ("MP3", "mp3")]
            else:  # image
                filtered_choices = [("JPEG", "mjpeg"), ("PNG", "png")]
        
        # Mettre à jour le combobox avec les noms affichables
        display_values = [display for display, _ in filtered_choices]
        self.global_codec_combo['values'] = display_values
        self._current_codec_choices = filtered_choices  # Stocker les paires display/codec
        
        if display_values:
            self.global_codec_var.set(display_values[0])
        else:
            self.global_codec_var.set("")
        
        self._update_encoder_choices()
        self._update_container_choices()
        self._update_quality_preset_controls()
    
    def _has_encoders_for_codec(self, codec: str) -> bool:
        """Vérifie si nous avons des encodeurs disponibles pour un codec donné"""
        codec_encoder_map = {
            'h264': ['libx264', 'h264_nvenc', 'h264_qsv', 'h264_amf', 'h264_videotoolbox'],
            'hevc': ['libx265', 'hevc_nvenc', 'hevc_qsv', 'hevc_amf', 'hevc_videotoolbox'],
            'av1': ['libsvtav1', 'libaom-av1', 'av1_nvenc', 'av1_qsv'],
            'vp9': ['libvpx-vp9'],
            'vp8': ['libvpx'],
            'mpeg4': ['libxvid', 'mpeg4'],
            'mpeg2video': ['mpeg2video'],
            'prores': ['prores_ks'],
            'dnxhd': ['dnxhd'],
            'aac': ['aac', 'libfdk_aac'],
            'mp3': ['libmp3lame'],
            'opus': ['libopus'],
            'vorbis': ['libvorbis'],
            'flac': ['flac'],
            'alac': ['alac'],
            'ac3': ['ac3'],
            'pcm_s16le': ['pcm_s16le'],
            'wav': ['pcm_s16le'],
            'webp': ['libwebp'],
            'png': ['png'],
            'mjpeg': ['mjpeg'],
            'bmp': ['bmp'],
            'tiff': ['tiff'],
            'libaom-av1': ['libaom-av1'],
            'jpegxl': ['libjxl']
        }
        
        expected_encoders = codec_encoder_map.get(codec.lower(), [])
        if not expected_encoders:
            return False
            
        # Vérifier si au moins un encodeur est disponible
        all_encoders = FFmpegHelpers.available_encoders()
        available_encoder_names = [name for name, _ in all_encoders]
        
        return any(encoder in available_encoder_names for encoder in expected_encoders)

    def _update_container_choices(self):
        """Met à jour les choix de containers basés sur le type de média"""
        media_type = self.global_type_var.get()
        
        if media_type == "video":
            container_choices = [
                ("MP4", "mp4"),
                ("MKV (Matroska)", "mkv"), 
                ("MOV (QuickTime)", "mov"),
                ("AVI", "avi"),
                ("MXF", "mxf"),
                ("WebM", "webm")
            ]
        elif media_type == "audio":
            container_choices = [
                ("M4A (AAC)", "m4a"),
                ("MP3", "mp3"),
                ("FLAC", "flac"),
                ("OGG", "ogg"),
                ("WAV", "wav"),
                ("AC3", "ac3")
            ]
        else:  # image
            container_choices = [
                ("WebP", "webp"),
                ("PNG", "png"),
                ("JPEG", "jpg"),
                ("BMP", "bmp"),
                ("TIFF", "tiff"),
                ("AVIF", "avif")
            ]
        
        # Mettre à jour le combobox
        display_values = [display for display, _ in container_choices]
        self.container_combo['values'] = display_values
        self._current_container_choices = container_choices
        
        # Sélectionner le premier par défaut
        if display_values:
            self.container_var.set(display_values[0])

    def _get_container_from_display(self, display_text: str) -> str:
        """Extrait la vraie extension de container à partir du texte affiché"""
        if hasattr(self, '_current_container_choices'):
            for display, container in self._current_container_choices:
                if display == display_text:
                    return container
        return display_text.lower()

    def _on_resolution_change(self, event=None):
        """Gère le changement de résolution dans le dropdown"""
        resolution = self.resolution_var_settings.get()
        if resolution == "Custom":
            # Afficher les champs de saisie personnalisés
            self.width_entry.pack(side="left", padx=(5, 2))
            self.x_label.pack(side="left")
            self.height_entry.pack(side="left", padx=(2, 5))
        else:
            # Cacher les champs personnalisés
            self.width_entry.pack_forget()
            self.x_label.pack_forget()
            self.height_entry.pack_forget()

    def _on_longest_side_change(self, event=None):
        """Gère le changement de la plus longue dimension pour les images"""
        longest_side = self.longest_side_var.get()
        if longest_side == "Custom":
            self.custom_longest_entry.pack(side="left", padx=(5, 0))
        else:
            self.custom_longest_entry.pack_forget()

    def _on_megapixels_change(self, event=None):
        """Gère le changement de mégapixels pour les images"""
        megapixels = self.megapixels_var.get()
        if megapixels == "Custom":
            self.custom_mp_entry.pack(side="left", padx=(5, 0))
        else:
            self.custom_mp_entry.pack_forget()

    def _on_video_mode_change(self):
        """Gère le changement de mode d'encodage vidéo (qualité vs bitrate)"""
        mode = self.video_mode_var.get()
        if mode == "quality":
            # Afficher les contrôles de qualité, cacher les contrôles de bitrate
            self.quality_entry.pack(side="left", padx=(0, 10))
            self.cq_entry.pack(side="left", padx=(0, 15))
            self.bitrate_label.pack_forget()
            self.bitrate_entry.pack_forget()
            self.multipass_check.pack_forget()
        else:  # bitrate
            # Cacher les contrôles de qualité, afficher les contrôles de bitrate
            self.quality_entry.pack_forget()
            self.cq_entry.pack_forget()
            self.bitrate_label.pack(side="left", padx=(0, 5))
            self.bitrate_entry.pack(side="left", padx=(0, 10))
            self.multipass_check.pack(side="left", padx=(0, 15))

    def _get_resolution_values(self):
        """Retourne les valeurs de résolution (width, height) selon la sélection"""
        resolution = self.resolution_var_settings.get()
        if resolution == "Original":
            return 0, 0
        elif resolution == "Custom":
            try:
                width = int(self.custom_width_var.get()) if self.custom_width_var.get() else 0
                height = int(self.custom_height_var.get()) if self.custom_height_var.get() else 0
                return width, height
            except ValueError:
                return 0, 0
        else:
            # Parser les résolutions prédéfinies
            resolution_map = {
                "3840x2160 (4K)": (3840, 2160),
                "1920x1080 (1080p)": (1920, 1080),
                "1280x720 (720p)": (1280, 720),
                "854x480 (480p)": (854, 480),
                "640x360 (360p)": (640, 360),
                "2160x3840 (4K Portrait)": (2160, 3840),
                "1080x1920 (1080p Portrait)": (1080, 1920),
                "720x1280 (720p Portrait)": (720, 1280),
                "480x854 (480p Portrait)": (480, 854)
            }
            return resolution_map.get(resolution, (0, 0))

    def _update_encoder_choices(self):
        """Met à jour la liste des encodeurs basée sur le codec sélectionné"""
        codec_display = self.global_codec_var.get()
        if not codec_display:
            self.global_encoder_combo['values'] = []
            self.global_encoder_var.set("")
            return
        
        # Obtenir le vrai nom du codec à partir du display
        codec = self._get_codec_from_display(codec_display).lower()
        
        # Vérifier si le codec est 'custom'
        if codec == "custom":
            # Cacher le combobox et afficher un champ de saisie texte
            self.global_encoder_combo.pack_forget()
            if not hasattr(self, 'global_encoder_entry'):
                self.global_encoder_entry = ttk.Entry(self.encoder_row, textvariable=self.global_encoder_var, width=30)
            self.global_encoder_entry.pack(side="left", padx=(5, 0))
            self.global_encoder_var.set("")
            return
        
        # Si on avait un champ de saisie texte, le cacher et réafficher le combobox
        if hasattr(self, 'global_encoder_entry'):
            self.global_encoder_entry.pack_forget()
            self.global_encoder_combo.pack(side="left", padx=(5, 0))
        
        # Obtenir tous les encodeurs avec descriptions
        all_encoders = FFmpegHelpers.available_encoders()
        
        # Filtrer les encodeurs compatibles avec le codec
        compatible_encoders = []
        
        # Pour certains codecs professionnels, ne montrer que l'encodeur principal
        primary_encoders = {
            'prores': 'prores_ks',
            'dnxhd': 'dnxhd'
        }
        
        if codec.lower() in primary_encoders:
            # Ne montrer que l'encodeur principal pour ces codecs
            primary_encoder = primary_encoders[codec.lower()]
            for encoder_name, description in all_encoders:
                if encoder_name == primary_encoder:
                    display_text = f"{encoder_name} - {description}"
                    compatible_encoders.append((encoder_name, display_text))
                    break
        else:
            # Logique normale pour les autres codecs
            for encoder_name, description in all_encoders:
                if codec in encoder_name.lower() or self._encoder_supports_codec(encoder_name, codec):
                    # Marquer les encodeurs hardware
                    if FFmpegHelpers.is_hardware_encoder(encoder_name):
                        display_text = f"{encoder_name} - {description} (Hardware)"
                    else:
                        display_text = f"{encoder_name} - {description}"
                    compatible_encoders.append((encoder_name, display_text))
        
        # Séparer les encodeurs hardware et software
        hw_encoders = [(name, desc) for name, desc in compatible_encoders 
                      if FFmpegHelpers.is_hardware_encoder(name)]
        sw_encoders = [(name, desc) for name, desc in compatible_encoders 
                      if not FFmpegHelpers.is_hardware_encoder(name)]
        
        # Organiser la liste avec hardware en premier, puis software
        display_values = []
        encoder_mapping = {}  # Pour mapper display vers encoder name
        
        if hw_encoders:
            for name, desc in hw_encoders:
                display_values.append(desc)
                encoder_mapping[desc] = name
                
        if sw_encoders:
            for name, desc in sw_encoders:
                display_values.append(desc)
                encoder_mapping[desc] = name
        
        # Mettre à jour le combobox
        self.global_encoder_combo['values'] = display_values
        self._current_encoder_mapping = encoder_mapping
        
        # Sélectionner le premier encodeur par défaut
        if display_values:
            self.global_encoder_var.set(display_values[0])
        else:
            self.global_encoder_var.set("")
            
        # Mettre à jour les contrôles qualité/preset
        self._update_quality_preset_controls()

    def _encoder_supports_codec(self, encoder_name: str, codec: str) -> bool:
        """Détermine si un encodeur supporte un codec donné"""
        codec_encoder_map = {
            'h264': ['libx264', 'h264_nvenc', 'h264_qsv', 'h264_amf', 'h264_videotoolbox'],
            'hevc': ['libx265', 'hevc_nvenc', 'hevc_qsv', 'hevc_amf', 'hevc_videotoolbox'],
            'av1': ['libsvtav1', 'libaom-av1', 'av1_nvenc', 'av1_qsv'],
            'vp9': ['libvpx-vp9'],
            'vp8': ['libvpx'],
            'aac': ['aac', 'libfdk_aac'],
            'mp3': ['libmp3lame'],
            'opus': ['libopus'],
            'vorbis': ['libvorbis'],
            'webp': ['libwebp']
        }
        return encoder_name in codec_encoder_map.get(codec, [encoder_name])

    def _update_quality_preset_controls(self):
        """Met à jour les contrôles qualité/preset basés sur le codec/encodeur sélectionné"""
        codec_display = self.global_codec_var.get()
        encoder_display = self.global_encoder_var.get()
        media_type = self.global_type_var.get()
        
        # Extraire les vrais noms depuis les displays
        codec = self._get_codec_from_display(codec_display).lower() if codec_display else ""
        encoder = self._get_encoder_name_from_display(encoder_display).lower() if encoder_display else ""
        
        # Gérer l'affichage des contrôles spécifiques au type de média
        self._update_media_type_ui(media_type)
        
        # Réinitialiser les états
        self.quality_entry.config(state="normal")
        self.cq_entry.config(state="normal")
        self.preset_combo_quality.config(state="readonly")

    def _update_media_type_ui(self, media_type):
        """Met à jour l'interface selon le type de média sélectionné"""
        if media_type == "video":
            # Afficher les contrôles vidéo
            self.mode_row.pack(fill="x", pady=(0, 8), before=self.mode_row.master.children[list(self.mode_row.master.children.keys())[0]])
            self.resolution_combo.pack(side="left", padx=(0, 10))
            self.image_res_frame.pack_forget()
            # Appliquer le mode vidéo actuel
            self._on_video_mode_change()
            # Afficher les contrôles de recadrage et de prévisualisation
            self.crop_frame.pack(side="left", padx=(10, 0))
            self.preview_row.pack(fill="x", pady=(8, 0))
            
        elif media_type == "image":
            # Masquer les contrôles vidéo, afficher les contrôles image
            self.mode_row.pack_forget()
            self.resolution_combo.pack_forget()
            self.image_res_frame.pack(side="left", padx=(10, 0))
            # Forcer le mode qualité pour les images
            self.quality_entry.pack(side="left", padx=(0, 10))
            self.cq_entry.pack_forget()
            self.bitrate_label.pack_forget()
            self.bitrate_entry.pack_forget()
            self.multipass_check.pack_forget()
            # Masquer les contrôles de recadrage et de prévisualisation
            self.crop_frame.pack_forget()
            self.preview_row.pack_forget()
            
        elif media_type == "audio":
            # Masquer tous les contrôles de résolution et mode vidéo
            self.mode_row.pack_forget()
            self.resolution_combo.pack_forget()
            self.image_res_frame.pack_forget()
            # Configuration audio basique
            self.quality_entry.pack(side="left", padx=(0, 10))
            self.cq_entry.pack_forget()
            self.bitrate_label.pack_forget()
            self.bitrate_entry.pack_forget()
            self.multipass_check.pack_forget()
            # Masquer les contrôles de recadrage et de prévisualisation
            self.crop_frame.pack_forget()
            self.preview_row.pack_forget()
        
        # Obtenir l'encodeur actuel à partir de la variable globale avec une valeur par défaut
        encoder_display = self.global_encoder_var.get()
        encoder = ""
        if encoder_display:
            try:
                encoder = self._get_encoder_name_from_display(encoder_display).lower()
            except Exception:
                encoder = ""
        
        if media_type == "video":
            # Déterminer le type de qualité basé sur l'encodeur
            if any(hw in encoder for hw in ["nvenc", "qsv", "amf", "videotoolbox"]):
                # Encodeurs hardware - utiliser CQ/qualité appropriée
                if "nvenc" in encoder:
                    self.quality_entry.config(state="disabled")
                    self.quality_var.set("")
                    self.cq_entry.config(state="normal")
                    self.cq_var.set(self.cq_var.get() or "23")
                    self.preset_combo_quality.config(state="readonly")
                    self.preset_combo_quality['values'] = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
                    self.preset_var.set(self.preset_var.get() or "p4")
                elif "qsv" in encoder:
                    self.quality_entry.config(state="disabled")
                    self.quality_var.set("")
                    self.cq_entry.config(state="normal")
                    self.cq_var.set(self.cq_var.get() or "23")
                    self.preset_combo_quality.config(state="readonly")
                    self.preset_combo_quality['values'] = ["veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
                    self.preset_var.set(self.preset_var.get() or "medium")
                elif "amf" in encoder:
                    self.quality_entry.config(state="disabled")
                    self.quality_var.set("")
                    self.cq_entry.config(state="normal")
                    self.cq_var.set(self.cq_var.get() or "23")
                    self.preset_combo_quality.config(state="readonly")
                    self.preset_combo_quality['values'] = ["speed", "balanced", "quality"]
                    self.preset_var.set(self.preset_var.get() or "balanced")
                elif "videotoolbox" in encoder:
                    self.quality_entry.config(state="disabled")
                    self.quality_var.set("")
                    self.cq_entry.config(state="normal")
                    self.cq_var.set(self.cq_var.get() or "23")
                    self.preset_combo_quality.config(state="disabled")
                    self.preset_var.set("")
            elif any(sw in encoder for sw in ["x264", "x265", "libx264", "libx265"]):
                # Encodeurs software x264/x265 - CRF + presets
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "23")
                self.cq_entry.config(state="disabled")
                self.cq_var.set("")
                self.preset_combo_quality.config(state="readonly")
                self.preset_combo_quality['values'] = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "placebo"]
                self.preset_var.set(self.preset_var.get() or "medium")
            elif any(av1 in encoder for av1 in ["av1", "svt-av1", "aom"]):
                # Encodeurs AV1
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "28")
                self.cq_entry.config(state="disabled")
                self.cq_var.set("")
                self.preset_combo_quality.config(state="readonly")
                self.preset_combo_quality['values'] = ["0", "1", "2", "3", "4", "5", "6", "7", "8"]
                self.preset_var.set(self.preset_var.get() or "4")
            elif "vp9" in encoder:
                # VP9
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "28")
                self.cq_entry.config(state="disabled")
                self.cq_var.set("")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
            else:
                # Autres encodeurs vidéo
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "23")
                self.cq_entry.config(state="disabled")
                self.cq_var.set("")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
                
        elif media_type == "audio":
            # Encodeurs audio - CQ non applicable
            self.cq_entry.config(state="disabled")
            self.cq_var.set("")
            if "flac" in encoder:
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "5")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
            elif any(lossy in encoder for lossy in ["aac", "mp3", "opus", "vorbis"]):
                # Pour les codecs avec perte, utiliser un sélecteur de bitrate
                self.quality_entry.config(state="disabled")
                self.quality_var.set("")
                self.preset_combo_quality.config(state="readonly")
                # Configurer les bitrates communs selon le codec
                if "aac" in encoder:
                    bitrates = ["96k", "128k", "192k", "256k", "320k"]
                elif "mp3" in encoder:
                    bitrates = ["96k", "128k", "160k", "192k", "256k", "320k"]
                elif "opus" in encoder:
                    bitrates = ["64k", "96k", "128k", "160k", "192k", "256k"]
                elif "vorbis" in encoder:
                    bitrates = ["96k", "128k", "160k", "192k", "256k", "320k"]
                else:
                    bitrates = ["128k", "192k", "256k", "320k"]
                
                self.preset_combo_quality['values'] = bitrates
                self.preset_var.set(self.preset_var.get() or "128k")
            else:
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "128")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
                
        elif media_type == "image":
            # Encodeurs image - CQ non applicable
            self.cq_entry.config(state="disabled")
            self.cq_var.set("")
            if any(img in encoder for img in ["jpeg", "webp", "avif", "jpegxl", "jxl"]):
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "90")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
            elif "png" in encoder:
                self.quality_entry.config(state="disabled")
                self.quality_var.set("")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
            else:
                self.quality_entry.config(state="normal")
                self.quality_var.set(self.quality_var.get() or "90")
                self.preset_combo_quality.config(state="disabled")
                self.preset_var.set("")
        else:
            # Mode non défini
            self.quality_entry.config(state="disabled")
            self.quality_var.set("")
            self.cq_entry.config(state="disabled")
            self.cq_var.set("")
            self.preset_combo_quality.config(state="disabled")
            self.preset_var.set("")

    def _apply_quality_all_type(self):
        media_type = self.global_type_var.get()
        quality = self.quality_var.get()
        cq_value = self.cq_var.get()
        preset = self.preset_var.get()
        custom_flags = self.custom_flags_var.get()
        width, height = self._get_resolution_values()
        
        for job in self.jobs:
            if job.mode == media_type:
                job.quality = quality
                job.cq_value = cq_value
                job.custom_flags = custom_flags
                job.preset = preset
                
                # Paramètres spécifiques au type de média
                if media_type == "video":
                    if hasattr(self, 'video_mode_var'):
                        job.video_mode = self.video_mode_var.get()
                        job.bitrate = self.bitrate_var.get() if self.video_mode_var.get() == "bitrate" else ""
                        job.multipass = self.multipass_var.get() if self.video_mode_var.get() == "bitrate" else False
                    # Appliquer la résolution aux filtres
                    job.filters["scale_width"] = width
                    job.filters["scale_height"] = height
                elif media_type == "image":
                    # Appliquer les paramètres spécifiques aux images
                    if hasattr(self, 'longest_side_var'):
                        longest_side = self.longest_side_var.get()
                        if longest_side == "Custom":
                            job.longest_side = self.custom_longest_var.get()
                        else:
                            job.longest_side = longest_side
                    
                    if hasattr(self, 'megapixels_var'):
                        megapixels = self.megapixels_var.get()
                        if megapixels == "Custom":
                            job.megapixels = self.custom_mp_var.get()
                        else:
                            job.megapixels = megapixels
        # Update the queue display
        for iid in self.tree.get_children():
            job = next((j for j in self.jobs if str(id(j)) == iid), None)
            if job and job.mode == media_type:
                values = list(self.tree.item(iid, 'values'))
                values[2] = job.quality
                self.tree.item(iid, values=values)

    def _apply_quality_selected(self):
        quality = self.quality_var.get()
        cq_value = self.cq_var.get()
        preset = self.preset_var.get()
        custom_flags = self.custom_flags_var.get()
        width, height = self._get_resolution_values()
        media_type = self.global_type_var.get()
        
        selected = self.tree.selection()
        for iid in selected:
            job = next((j for j in self.jobs if str(id(j)) == iid), None)
            if job:
                job.quality = quality
                job.cq_value = cq_value
                job.custom_flags = custom_flags
                job.preset = preset
                
                # Paramètres spécifiques au type de média
                if job.mode == "video":
                    if hasattr(self, 'video_mode_var'):
                        job.video_mode = self.video_mode_var.get()
                        job.bitrate = self.bitrate_var.get() if self.video_mode_var.get() == "bitrate" else ""
                        job.multipass = self.multipass_var.get() if self.video_mode_var.get() == "bitrate" else False
                    # Appliquer la résolution aux filtres
                    job.filters["scale_width"] = width
                    job.filters["scale_height"] = height
                elif job.mode == "image":
                    # Appliquer les paramètres spécifiques aux images
                    if hasattr(self, 'longest_side_var'):
                        longest_side = self.longest_side_var.get()
                        if longest_side == "Custom":
                            job.longest_side = self.custom_longest_var.get()
                        else:
                            job.longest_side = longest_side
                    
                    if hasattr(self, 'megapixels_var'):
                        megapixels = self.megapixels_var.get()
                        if megapixels == "Custom":
                            job.megapixels = self.custom_mp_var.get()
                        else:
                            job.megapixels = megapixels
                
                values = list(self.tree.item(iid, 'values'))
                values[2] = job.quality or job.bitrate or job.preset
                self.tree.item(iid, values=values)

    def _duplicate_selected(self):
        selected = self.tree.selection()
        for iid in selected:
            job = next((j for j in self.jobs if str(id(j)) == iid), None)
            if job:
                new_job = EncodeJob(src_path=job.src_path, dst_path=job.dst_path, mode=job.mode)
                new_job.encoder = job.encoder
                new_job.quality = job.quality
                new_job.cq_value = job.cq_value
                new_job.preset = job.preset
                new_job.custom_flags = job.custom_flags
                self.jobs.append(new_job)
                self.tree.insert("", "end", iid=str(id(new_job)), values=(new_job.src_path.name, new_job.encoder or "-", new_job.quality or "-", "0%", "pending"))
                self.job_rows[str(id(new_job))] = {"job": new_job}

    def _set_codec_for_all(self):
        """Applique tous les paramètres d'encodage globaux à tous les jobs du type sélectionné"""
        target_type = self.global_type_var.get()
        encoder_display = self.global_encoder_var.get()
        encoder_name = self._get_encoder_name_from_display(encoder_display)
        quality = self.quality_var.get()
        cq_value = self.cq_var.get()
        preset = self.preset_var.get()
        container = self._get_container_from_display(self.container_var.get())
        custom_flags = self.custom_flags_var.get()
        width, height = self._get_resolution_values()
        
        count = 0
        for job in self.jobs:
            if job.mode == target_type:
                job.encoder = encoder_name
                job.quality = quality
                job.cq_value = cq_value
                job.preset = preset
                job.custom_flags = custom_flags
                # Appliquer la résolution aux filtres
                job.filters["scale_width"] = width
                job.filters["scale_height"] = height
                # Mettre à jour le chemin de destination avec le nouveau container
                if container:
                    job.dst_path = job.dst_path.with_suffix("." + container)
                count += 1
        
        # Mettre à jour l'affichage
        for item_id in self.tree.get_children():
            job = next((j for j in self.jobs if str(id(j)) == item_id), None)
            if job and job.mode == target_type:
                self._update_job_row(job)
        
        messagebox.showinfo("Applied", f"All encoding settings applied to {count} {target_type} job(s).")

    def _apply_settings_smart(self):
        """Applique les paramètres intelligemment selon la sélection"""
        selected = self.tree.selection()
        
        if selected:
            # Il y a des éléments sélectionnés - appliquer uniquement à ceux-ci
            self._apply_quality_selected()
            messagebox.showinfo("Applied", f"Settings applied to {len(selected)} selected job(s).")
        else:
            # Aucune sélection - appliquer à tous les jobs du type actuel
            self._apply_quality_all_type()

    def _apply_codec_smart(self):
        """Applique le codec intelligemment selon la sélection"""
        selected = self.tree.selection()
        
        # Vérifier qu'un encodeur est sélectionné
        encoder_display = self.global_encoder_var.get()
        if not encoder_display:
            messagebox.showwarning("No Encoder", "Please select an encoder first.")
            return
        
        encoder_name = self._get_encoder_name_from_display(encoder_display)
        if not encoder_name:
            messagebox.showwarning("Invalid Encoder", "Could not determine encoder name.")
            return
        
        if selected:
            # Il y a des éléments sélectionnés - appliquer codec/encodeur à ceux-ci
            target_type = self.global_type_var.get()
            container = self._get_container_from_display(self.container_var.get())
            
            count = 0
            for item_id in selected:
                job = next((j for j in self.jobs if str(id(j)) == item_id), None)
                if job and job.mode == target_type:
                    job.encoder = encoder_name
                    # Mettre à jour le chemin de destination avec le nouveau container
                    if container:
                        job.dst_path = job.dst_path.with_suffix("." + container)
                    count += 1
            
            # Mettre à jour l'affichage
            for item_id in selected:
                job = next((j for j in self.jobs if str(id(j)) == item_id), None)
                if job and job.mode == target_type:
                    self._update_job_row(job)
            
            messagebox.showinfo("Applied", f"Encoder '{encoder_name}' applied to {count} selected job(s).")
        else:
            # Aucune sélection - appliquer à tous les jobs du type actuel
            self._set_codec_for_all()

    def _get_encoder_name_from_display(self, display_text: str) -> str:
        """Extrait le vrai nom de l'encodeur à partir du texte affiché"""
        if not display_text:
            return ""
        if hasattr(self, '_current_encoder_mapping') and display_text in self._current_encoder_mapping:
            return self._current_encoder_mapping[display_text]
        # Si on utilise un champ de saisie texte (pour custom), retourner le texte directement
        if hasattr(self, 'global_encoder_entry') and self.global_encoder_entry.winfo_ismapped():
            return display_text
        return display_text.split(' - ')[0] if ' - ' in display_text else display_text

    def _get_codec_from_display(self, display_text: str) -> str:
        """Extrait le vrai nom du codec à partir du texte affiché"""
        if hasattr(self, '_current_codec_choices'):
            for display, codec in self._current_codec_choices:
                if display == display_text:
                    return codec
        return display_text.lower()

    def _start_encoding(self):
        """Commence l'encodage de tous les jobs en attente"""
        # Vérifier qu'un dossier de sortie est sélectionné
        if not self.output_folder.get() or self.output_folder.get().startswith("No output"):
            messagebox.showwarning("No Output Folder", "Please select an output folder before starting encoding.")
            return
        
        pending_jobs = [job for job in self.jobs if job.status == "pending"]
        if not pending_jobs:
            messagebox.showinfo("No Jobs", "No pending jobs to encode.")
            return

        # Mettre à jour l'état des boutons de contrôle
        self._update_control_buttons_state("encoding")
        
        # Démarrer les pools de workers
        self.pool.start()
        
        # Soumettre les jobs aux pools appropriés
        for job in pending_jobs:
            self.pool.submit(job)

    def _update_control_buttons_state(self, mode: str):
        """Met à jour l'état des boutons de contrôle selon le mode"""
        if mode == "idle":
            # Aucun encodage en cours - vérifier s'il y a des jobs pending
            pending_jobs = [job for job in self.jobs if job.status == "pending"]
            
            # Le bouton Start est activé s'il y a des jobs en attente
            self.start_btn.config(state="normal" if pending_jobs else "disabled")
            self.pause_btn.config(state="disabled")
            self.resume_btn.config(state="disabled")
            self.cancel_btn.config(state="disabled")
        elif mode == "encoding":
            # Encodage en cours
            self.start_btn.config(state="disabled")
            self.pause_btn.config(state="normal")
            self.resume_btn.config(state="disabled")
            self.cancel_btn.config(state="normal")
        elif mode == "paused":
            # Encodage en pause
            self.start_btn.config(state="disabled")
            self.pause_btn.config(state="disabled")
            self.resume_btn.config(state="normal")
            self.cancel_btn.config(state="normal")

    def _on_job_progress(self, job: EncodeJob):
        """Met à jour l'affichage quand un job progresse"""
        self._update_job_row(job)
        self._update_overall_progress()
        
        # Vérifier si tous les jobs sont terminés
        active_jobs = [j for j in self.jobs if j.status in ["pending", "running", "paused"]]
        if not active_jobs:
            # Tous les jobs sont terminés, revenir à l'état idle
            self._update_control_buttons_state("idle")
            self._show_encoding_completion_notification()

    def _show_encoding_completion_notification(self):
        """Affiche une notification quand tous les encodages sont terminés"""
        completed_jobs = [j for j in self.jobs if j.status == "done"]
        failed_jobs = [j for j in self.jobs if j.status == "error"]
        cancelled_jobs = [j for j in self.jobs if j.status == "cancelled"]
        
        if completed_jobs or failed_jobs or cancelled_jobs:
            # Créer un message de résumé
            message_parts = []
            if completed_jobs:
                message_parts.append(f"✅ {len(completed_jobs)} job(s) terminé(s) avec succès")
            if failed_jobs:
                message_parts.append(f"❌ {len(failed_jobs)} job(s) échoué(s)")
            if cancelled_jobs:
                message_parts.append(f"🚫 {len(cancelled_jobs)} job(s) annulé(s)")
            
            message = "Encodage terminé!\n\n" + "\n".join(message_parts)
            
            # Afficher la notification
            messagebox.showinfo("Encodage terminé", message)
            
            # Optionnel: jouer un son système si disponible
            try:
                import winsound
                winsound.SystemSound("SystemAsterisk")
            except ImportError:
                try:
                    import os
                    os.system("afplay /System/Library/Sounds/Glass.aiff")  # macOS
                except:
                    pass  # Pas de son disponible

    def _update_job_row(self, job):
        iid = str(id(job))
        if self.tree.exists(iid):
            values = list(self.tree.item(iid, 'values'))
            # Mettre à jour l'encodeur si défini
            if job.encoder:
                values[1] = job.encoder
            # Mettre à jour la qualité si définie
            if job.quality:
                values[2] = job.quality
            # Mettre à jour la progression
            values[3] = f"{int(job.progress*100)}%"
            # Mettre à jour le statut
            status = job.status
            if len(values) < 5:
                values.append(status)
            else:
                values[4] = status
            self.tree.item(iid, values=values)
        self._update_overall_progress()

    def _update_overall_progress(self):
        if not self.jobs:
            self.progress_bar['value'] = 0
            return
        avg = sum(j.progress for j in self.jobs) / len(self.jobs)
        self.progress_bar['value'] = avg * 100

    def _pause_all(self):
        """Met en pause tous les jobs en cours d'exécution"""
        paused_count = 0
        for job in self.jobs:
            if job.status == "running":
                job.pause()
                paused_count += 1
        
        if paused_count > 0:
            self._update_control_buttons_state("paused")

    def _resume_all(self):
        """Reprend tous les jobs en pause"""
        resumed_count = 0
        for job in self.jobs:
            if job.status == "paused":
                job.resume()
                resumed_count += 1
        
        if resumed_count > 0:
            self._update_control_buttons_state("encoding")

    def _cancel_all(self):
        """Annule tous les jobs en cours"""
        cancelled_count = 0
        for job in self.jobs:
            if job.status in ["running", "paused", "pending"]:
                job.cancel()
                cancelled_count += 1
        
        if cancelled_count > 0:
            # Arrêter les pools de workers
            self.pool.stop()
            self._update_control_buttons_state("idle")

    def _clear_queue(self):
        """Vide complètement la queue d'encodage"""
        # Annuler tous les jobs en cours
        for job in self.jobs:
            if job.status in ["running", "paused", "pending"]:
                job.cancel()
        
        # Arrêter les pools de workers
        self.pool.stop()
        
        # Vider la liste et l'interface
        self.jobs.clear()
        self.tree.delete(*self.tree.get_children())
        self.progress_bar['value'] = 0
        self.job_rows.clear()
        self._update_inspector_file_list()
        
        # Remettre les boutons à l'état idle
        self._update_control_buttons_state("idle")

    def _on_right_click(self, event):
        item_id = self.tree.identify("item", event.x, event.y)
        if item_id:
            self.tree.selection_set(item_id)
            self.context_menu.post(event.x_root, event.y_root)

    def _edit_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                JobEditWindow(self.root, job)

    def _pause_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                job.pause()

    def _resume_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                job.resume()

    def _cancel_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                job.cancel()

    def _remove_selected_job(self):
        """Supprime le job sélectionné de la queue"""
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                # Annuler le job s'il est en cours
                if job.status in ["running", "paused"]:
                    job.cancel()
                    
                self.jobs.remove(job)
                self.tree.delete(selected[0])
                self.job_rows.pop(selected[0], None)
                self._update_inspector_file_list()
                
                # Mettre à jour l'état des boutons si aucun job n'est actif
                if not any(j.status in ["running", "paused"] for j in self.jobs):
                    self._update_control_buttons_state("idle")

    def _setup_drag_drop(self):
        """Configure les zones de drop pour le drag & drop"""
        if not DND_AVAILABLE:
            return
            
        # Drop sur le champ input folder
        self.input_folder_entry.drop_target_register(DND_FILES)
        self.input_folder_entry.dnd_bind('<<Drop>>', self._on_drop_input_folder)
        
        # Drop sur la queue (treeview)
        self.tree.drop_target_register(DND_FILES)
        self.tree.dnd_bind('<<Drop>>', self._on_drop_queue)

    def _on_drop_input_folder(self, event):
        """Gère le drop de fichiers/dossiers sur le champ input folder"""
        files = self.root.tk.splitlist(event.data)
        if files:
            first_path = Path(files[0])
            if first_path.is_dir():
                self.input_folder.set(str(first_path))
            elif first_path.is_file():
                # Si c'est un fichier, utiliser son dossier parent
                self.input_folder.set(str(first_path.parent))

    def _on_drop_queue(self, event):
        """Gère le drop de fichiers/dossiers directement dans la queue"""
        files = self.root.tk.splitlist(event.data)
        paths = []
        for file_path in files:
            path = Path(file_path)
            if path.is_file():
                paths.append(path)
            elif path.is_dir():
                # Ajouter tous les fichiers du dossier récursivement
                paths.extend([p for p in path.rglob("*") if p.is_file()])
        
        if paths:
            self._enqueue_paths(paths)

    def _update_preset_list(self):
        """Met à jour la liste des presets disponibles"""
        preset_names = list(Settings.data["presets"].keys())
        self.preset_combo['values'] = preset_names
        if not self.preset_name_var.get() and preset_names:
            self.preset_name_var.set(preset_names[0])

    def _save_preset(self):
        """Sauvegarde le preset actuel ou crée un nouveau preset"""
        current_preset = self.preset_name_var.get()
        
        # Demander le nom du preset
        if not current_preset or current_preset in ["H264 High Quality", "H264 Fast", "WebP Images"]:
            # Créer un nouveau preset
            preset_name = self._ask_preset_name()
        else:
            # Demander si on veut écraser le preset existant
            result = messagebox.askyesno(
                "Save Preset", 
                f"Update existing preset '{current_preset}'?",
                icon='question'
            )
            if result:  # Yes = update existing
                preset_name = current_preset
            else:  # No = create new
                preset_name = self._ask_preset_name()
        
        if not preset_name:
            return
            
        # Créer le preset
        preset_data = {
            "mode": self.global_type_var.get(),
            "codec": self.global_codec_var.get(),
            "encoder": self._get_encoder_name_from_display(self.global_encoder_var.get()),
            "quality": self.quality_var.get(),
            "cq_value": self.cq_var.get(),
            "preset": self.preset_var.get(),
            "container": self.container_var.get(),
            "custom_flags": self.custom_flags_var.get()
        }
        
        Settings.data["presets"][preset_name] = preset_data
        Settings.save()
        self._update_preset_list()
        self.preset_name_var.set(preset_name)
        messagebox.showinfo("Success", f"Preset '{preset_name}' saved successfully!")

    def _ask_preset_name(self) -> str:
        """Demande le nom d'un nouveau preset"""
        from tkinter.simpledialog import askstring
        name = askstring("New Preset", "Enter preset name:")
        if name and name.strip():
            return name.strip()
        return ""

    def _load_preset(self, event=None):
        """Charge un preset sélectionné"""
        selected = self.preset_name_var.get()
        if selected and selected in Settings.data["presets"]:
            preset = Settings.data["presets"][selected]
            
            # Charger les valeurs du preset
            self.global_type_var.set(preset["mode"])
            self.global_codec_var.set(preset["codec"])
            self.quality_var.set(preset.get("quality", ""))
            self.cq_var.set(preset.get("cq_value", ""))
            self.preset_var.set(preset.get("preset", ""))
            self.container_var.set(preset.get("container", "mp4"))
            self.custom_flags_var.set(preset.get("custom_flags", ""))
            
            # Mettre à jour les listes de codecs/encodeurs
            self._update_codec_choices()
            self._update_encoder_choices()
            
            # Définir l'encodeur (avec gestion du format display)
            encoder = preset.get("encoder", "")
            if encoder:
                # Chercher l'encodeur dans la liste des encodeurs disponibles
                for encoder_name, description in FFmpegHelpers.available_encoders():
                    if encoder_name == encoder:
                        display_text = f"{encoder_name} - {description}"
                        self.global_encoder_var.set(display_text)
                        break
                else:
                    # Fallback si l'encodeur n'est pas trouvé
                    self.global_encoder_var.set(encoder)

    def _delete_preset(self):
        """Supprime le preset sélectionné"""
        selected = self.preset_name_var.get()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a preset to delete.")
            return
            
        if selected in ["H264 High Quality", "H264 Fast", "WebP Images"]:
            messagebox.showwarning("Cannot Delete", "Cannot delete default presets.")
            return
            
        result = messagebox.askyesno("Confirm Delete", f"Delete preset '{selected}'?")
        if result:
            del Settings.data["presets"][selected]
            Settings.save()
            self._update_preset_list()
            self.preset_name_var.set("")
            messagebox.showinfo("Deleted", f"Preset '{selected}' deleted successfully!")

    def _load_preset_by_name(self, preset_name: str):
        """Charge un preset par son nom (utilisé par le menu)"""
        self.preset_name_var.set(preset_name)
        self._load_preset()

    def _show_log_viewer(self):
        self.log_viewer = LogViewerWindow(self.root)

    def _on_job_log(self, job: EncodeJob, message: str, log_type: str = "info"):
        """Callback pour recevoir les logs des jobs et les transmettre au log viewer"""
        if self.log_viewer:
            # Utiliser after_idle pour s'assurer que les mises à jour GUI se font sur le thread principal
            self.root.after_idle(lambda: self.log_viewer.add_log(job, message, log_type))

    def _batch_operations(self):
        """Ouvre la fenêtre de batch operations pour les jobs sélectionnés"""
        selected_item_ids = self.tree.selection()
        if not selected_item_ids:
            messagebox.showwarning("No Selection", "Please select one or more jobs for batch operations.")
            return
            
        # Récupérer les jobs correspondants aux IDs sélectionnés
        selected_jobs = []
        for item_id in selected_item_ids:
            job = next((j for j in self.jobs if str(id(j)) == item_id), None)
            if job:
                selected_jobs.append(job)
        
        if selected_jobs:
            BatchOperationsWindow(self.root, selected_jobs)
        else:
            messagebox.showwarning("No Jobs Found", "Could not find jobs for selected items.")

    def _advanced_filters(self):
        """Ouvre la fenêtre de filtres avancés pour le job sélectionné"""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a job to configure filters.")
            return
            
        job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
        if job:
            AdvancedFiltersWindow(self.root, job)
        else:
            messagebox.showwarning("Job Not Found", "Could not find the selected job.")

    def _configure_audio_tracks(self):
        """Ouvre la fenêtre de configuration des pistes audio"""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a job to configure audio tracks.")
            return
            
        job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
        if job:
            if job.mode == "image":
                messagebox.showinfo("Not Applicable", "Audio track configuration is not applicable to image files.")
                return
            AudioTracksWindow(self.root, job)
        else:
            messagebox.showwarning("Job Not Found", "Could not find the selected job.")

    def _toggle_watch(self):
        if self.watch_var.get():
            if Observer is None:
                messagebox.showerror("Dépendance manquante", "Le module 'watchdog' n'est pas installé. Veuillez l'installer avec 'pip install watchdog'.")
                self.watch_var.set(False)
                return
            if not self.input_folder.get():
                messagebox.showerror("Erreur", "Veuillez d'abord sélectionner un dossier d'entrée")
                self.watch_var.set(False)
                return
            
            self.stop_event = threading.Event()
            self.watcher = FolderWatcher(
                Path(self.input_folder.get()),
                self._handle_new_watched_file,
                self.stop_event
            )
            self.watcher.start()
            self.watch_status.config(text="Statut: Surveillance active...")
        else:
            if hasattr(self, 'watcher'):
                self.stop_event.set()
                self.watcher.join()
                self.watch_status.config(text="Statut: Inactif")

    def _handle_new_watched_file(self, file_path):
        self.root.after_idle(lambda: self._enqueue_watched_file(file_path))

    def _enqueue_watched_file(self, file_path):
        preset_name = self.watch_preset_combo.get()
        if preset_name in Settings.data.get("presets", {}):
            self._enqueue_paths([file_path])
            new_job = self.jobs[-1] if self.jobs else None
            if new_job:
                preset = Settings.data["presets"][preset_name]
                new_job.encoder = preset.get("encoder", "")
                new_job.quality = preset.get("quality", "")
                new_job.cq_value = preset.get("cq_value", "")
                new_job.preset = preset.get("preset", "")
                new_job.custom_flags = preset.get("custom_flags", "")
                self._update_job_row(new_job)
                print(f"Fichier surveillé ajouté: {file_path}")
                # Démarrer automatiquement l'encodage si configuré
                if Settings.data.get("auto_start_watched_files", False):
                    self._start_encoding()

    def _on_queue_selection_change(self, event=None):
        selection = self.tree.selection()
        if not selection:
            self._clear_inspector()
            return
        
        job_id = selection[0]
        
        # Select the same item in the inspector tree
        if self.inspector_tree.exists(job_id):
            self.inspector_tree.selection_set(job_id)
            self.inspector_tree.focus(job_id)
    
    def _on_inspector_selection_change(self, event=None):
        selection = self.inspector_tree.selection()
        if not selection:
            self._clear_inspector()
            return

        job_id = selection[0]
        # The job id is stored in the item's iid
        job = next((j for j in self.jobs if str(id(j)) == job_id), None)

        if job:
            self._clear_inspector() # Show loading message
            ttk.Label(self.inspector_info_frame, text="Inspection en cours...").pack(padx=10, pady=10)
            threading.Thread(
                target=self._run_probe_and_update_inspector, 
                args=(job,),
                daemon=True
            ).start()

    def _run_probe_and_update_inspector(self, job: EncodeJob):
        try:
            cmd = [
                "ffprobe", "-v", "quiet", 
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(job.src_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            
            # Extraire les informations pertinentes
            info = self._parse_ffprobe_data(data, job.mode)
            self.root.after_idle(lambda: self._update_inspector_ui(info))
        except Exception as e:
            print(f"Erreur d'inspection: {e}")
            self.root.after_idle(self._clear_inspector)

    def _parse_ffprobe_data(self, data, mode):
        def format_duration(seconds):
            if not seconds or seconds == 'N/A':
                return "N/A"
            try:
                seconds = float(seconds)
                hours, remainder = divmod(int(seconds), 3600)
                minutes, seconds = divmod(remainder, 60)
                return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            except:
                return "N/A"

        def format_bitrate(bps):
            if not bps or bps == 'N/A':
                return "N/A"
            try:
                bps = int(bps)
                if bps >= 1000000:
                    return f"{bps/1000000:.2f} Mbps"
                elif bps >= 1000:
                    return f"{bps/1000:.2f} kbps"
                else:
                    return f"{bps} bps"
            except:
                return "N/A"

        def format_file_size(bytes_size):
            if not bytes_size or bytes_size == 'N/A':
                return "N/A"
            try:
                bytes_size = int(bytes_size)
                if bytes_size >= 1024**3:
                    return f"{bytes_size/(1024**3):.2f} GB"
                elif bytes_size >= 1024**2:
                    return f"{bytes_size/(1024**2):.2f} MB"
                elif bytes_size >= 1024:
                    return f"{bytes_size/1024:.2f} KB"
                else:
                    return f"{bytes_size} bytes"
            except:
                return "N/A"

        format_info = data.get('format', {})
        streams = data.get('streams', [])
        info = {}

        if mode == 'video':
            video_stream = next((s for s in streams if s.get('codec_type') == 'video'), {})
            audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
            
            # Informations vidéo principales
            width = video_stream.get('width', 'N/A')
            height = video_stream.get('height', 'N/A')
            fps = video_stream.get('r_frame_rate', 'N/A')
            if fps != 'N/A' and '/' in str(fps):
                try:
                    num, den = map(int, fps.split('/'))
                    fps = f"{num/den:.2f}" if den != 0 else 'N/A'
                except:
                    pass
            
            # Calculer le ratio d'aspect
            aspect_ratio = "N/A"
            if width != 'N/A' and height != 'N/A':
                try:
                    from math import gcd
                    w, h = int(width), int(height)
                    divisor = gcd(w, h)
                    aspect_ratio = f"{w//divisor}:{h//divisor}"
                except:
                    pass
            
            info = {
                "Résolution": f"{width}x{height}",
                "Ratio d'aspect": aspect_ratio,
                "Durée": format_duration(format_info.get('duration', 'N/A')),
                "Images/sec": f"{fps} fps" if fps != 'N/A' else 'N/A',
                "Codec Vidéo": video_stream.get('codec_long_name', video_stream.get('codec_name', 'N/A')),
                "Débit Vidéo": format_bitrate(video_stream.get('bit_rate', 'N/A')),
                "Format Pixel": video_stream.get('pix_fmt', 'N/A'),
                "Taille Fichier": format_file_size(format_info.get('size', 'N/A')),
            }
            
            # Informations audio si présentes
            if audio_streams:
                main_audio = audio_streams[0]
                info.update({
                                    "Codec Audio": main_audio.get('codec_long_name', main_audio.get('codec_name', 'N/A')),
                "Débit Audio": format_bitrate(main_audio.get('bit_rate', 'N/A')),
                                         "Canaux Audio": main_audio.get('channel_layout', str(main_audio.get('channels', 'N/A'))),
                     "Fréq. Échantillonnage": f"{main_audio.get('sample_rate', 'N/A')} Hz" if main_audio.get('sample_rate') else 'N/A',
                })
                
                # Si plusieurs pistes audio
                if len(audio_streams) > 1:
                    info["Pistes Audio"] = f"{len(audio_streams)} pistes"
            else:
                info["Audio"] = "Aucune piste audio"
            
            # Suggérer automatiquement une résolution basée sur le ratio d'aspect
            self._suggest_resolution_from_aspect_ratio(width, height)
                
        elif mode == 'audio':
            audio_stream = next((s for s in streams if s.get('codec_type') == 'audio'), {})
            
            # Calculer le débit moyen si pas disponible directement
            duration = format_info.get('duration')
            file_size = format_info.get('size')
            calculated_bitrate = "N/A"
            if duration and file_size:
                try:
                    duration_sec = float(duration)
                    size_bytes = int(file_size)
                    bitrate_bps = (size_bytes * 8) / duration_sec
                    calculated_bitrate = format_bitrate(bitrate_bps)
                except:
                    pass
            
            # Déterminer la qualité audio
            quality_indicator = "N/A"
            bitrate = audio_stream.get('bit_rate')
            if bitrate:
                try:
                    br = int(bitrate)
                    if br >= 320000:
                        quality_indicator = "Très haute (≥320kbps)"
                    elif br >= 192000:
                        quality_indicator = "Haute (≥192kbps)"
                    elif br >= 128000:
                        quality_indicator = "Moyenne (≥128kbps)"
                    else:
                        quality_indicator = "Basse (<128kbps)"
                except:
                    pass
            
            info = {
                "Durée": format_duration(format_info.get('duration', 'N/A')),
                "Codec": audio_stream.get('codec_long_name', audio_stream.get('codec_name', 'N/A')),
                "Débit": format_bitrate(audio_stream.get('bit_rate', calculated_bitrate)),
                "Qualité": quality_indicator,
                "Canaux": audio_stream.get('channel_layout', str(audio_stream.get('channels', 'N/A'))),
                "Fréq. Échantillonnage": f"{audio_stream.get('sample_rate', 'N/A')} Hz" if audio_stream.get('sample_rate') else 'N/A',
                "Bits par échantillon": f"{audio_stream.get('bits_per_sample', 'N/A')} bits" if audio_stream.get('bits_per_sample') else 'N/A',
                "Taille Fichier": format_file_size(format_info.get('size', 'N/A')),
                "Format Container": format_info.get('format_long_name', format_info.get('format_name', 'N/A')),
            }
            
            # Métadonnées si disponibles
            tags = format_info.get('tags', {})
            if tags:
                metadata_info = {}
                if tags.get('title'):
                    metadata_info["Titre"] = tags['title']
                if tags.get('artist'):
                    metadata_info["Artiste"] = tags['artist']
                if tags.get('album'):
                    metadata_info["Album"] = tags['album']
                if tags.get('date') or tags.get('year'):
                    metadata_info["Année"] = tags.get('date', tags.get('year'))
                if tags.get('genre'):
                    metadata_info["Genre"] = tags['genre']
                    
                if metadata_info:
                    info.update(metadata_info)
                    
        elif mode == 'image':
            image_stream = next((s for s in streams if s.get('codec_type') == 'video'), {})  # Les images sont des streams vidéo
            
            # Calculer les mégapixels
            width = image_stream.get('width', 0)
            height = image_stream.get('height', 0)
            megapixels = "N/A"
            if width and height:
                try:
                    mp = (int(width) * int(height)) / 1000000
                    megapixels = f"{mp:.2f} MP"
                except:
                    pass
            
            # Calculer le ratio d'aspect
            aspect_ratio = "N/A"
            if width and height:
                try:
                    from math import gcd
                    w, h = int(width), int(height)
                    divisor = gcd(w, h)
                    aspect_ratio = f"{w//divisor}:{h//divisor}"
                except:
                    pass
            
            # Déterminer la qualité de résolution
            resolution_quality = "N/A"
            if width and height:
                try:
                    w, h = int(width), int(height)
                    if w >= 3840 and h >= 2160:
                        resolution_quality = "4K/Ultra HD"
                    elif w >= 1920 and h >= 1080:
                        resolution_quality = "Full HD"
                    elif w >= 1280 and h >= 720:
                        resolution_quality = "HD"
                    else:
                        resolution_quality = "SD"
                except:
                    pass
            
            # Informations sur la profondeur de couleur
            color_info = "N/A"
            pix_fmt = image_stream.get('pix_fmt', '')
            if 'rgba' in pix_fmt.lower():
                color_info = "RGBA (avec transparence)"
            elif 'rgb' in pix_fmt.lower():
                color_info = "RGB (couleur)"
            elif 'gray' in pix_fmt.lower():
                color_info = "Niveaux de gris"
            elif pix_fmt:
                color_info = pix_fmt.upper()
            
            info = {
                "Résolution": f"{width}x{height}",
                "Mégapixels": megapixels,
                "Ratio d'aspect": aspect_ratio,
                "Qualité": resolution_quality,
                "Codec": image_stream.get('codec_long_name', image_stream.get('codec_name', 'N/A')),
                "Format Pixel": image_stream.get('pix_fmt', 'N/A'),
                "Type Couleur": color_info,
                "Espace Couleur": image_stream.get('color_space', 'N/A'),
                "Taille Fichier": format_file_size(format_info.get('size', 'N/A')),
            }
            
            # Métadonnées EXIF si disponibles
            tags = format_info.get('tags', {})
            if tags:
                metadata_info = {}
                if tags.get('creation_time'):
                    metadata_info["Date Création"] = tags['creation_time']
                if tags.get('make'):
                    metadata_info["Fabricant"] = tags['make']
                if tags.get('model'):
                    metadata_info["Modèle"] = tags['model']
                if tags.get('software'):
                    metadata_info["Logiciel"] = tags['software']
                    
                if metadata_info:
                    info.update(metadata_info)
        
        return info

    def _update_inspector_ui(self, info: dict):
        self._clear_inspector()
        
        # Afficher un message si aucune information
        if not info:
            ttk.Label(self.inspector_info_frame, text="Impossible de lire les informations du média.", 
                     font=("Helvetica", 11), foreground="red").pack(padx=10, pady=20)
            return

        # Créer un frame avec scrollbar pour les longues listes d'informations
        canvas = tk.Canvas(self.inspector_info_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.inspector_info_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Remplir avec les nouvelles informations
        for i, (label_text, value_text) in enumerate(info.items()):
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill=tk.X, pady=3, padx=10, anchor="n")
            
            # Utiliser une couleur alternée pour une meilleure lisibilité
            bg_color = "#f8f9fa" if i % 2 == 0 else "#ffffff"
            
            # Label avec icône emoji
            label = ttk.Label(frame, text=f"{label_text}:", width=25, anchor="w", 
                            font=("Helvetica", 10, "bold"))
            label.pack(side=tk.LEFT, padx=(0, 10))
            
            # Valeur avec wrapping pour les textes longs
            value_label = ttk.Label(frame, text=value_text or "N/A", anchor="w", 
                                  wraplength=350, font=("Helvetica", 10))
            value_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Pack canvas et scrollbar
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Bind mousewheel pour le scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        canvas.bind("<MouseWheel>", _on_mousewheel)  # Windows
        canvas.bind("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))  # Linux
        canvas.bind("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))   # Linux

    def _clear_inspector(self):
        for widget in self.inspector_info_frame.winfo_children():
            widget.destroy()

    def _update_inspector_file_list(self):
        """Met à jour la liste des fichiers dans l'inspecteur et dans le sélecteur de fichier des paramètres d'encodage"""
        # Nettoyer la liste
        for item in self.inspector_tree.get_children():
            self.inspector_tree.delete(item)
        
        # Ajouter les jobs de la queue
        file_list = []
        for job_id, job_data in self.job_rows.items():
            job = job_data["job"]
            filename = job.src_path.name
            self.inspector_tree.insert("", "end", iid=job_id, values=(filename,))
            file_list.append(filename)
        
        # Mettre à jour le sélecteur de fichier dans les paramètres d'encodage
        if hasattr(self, 'selected_file_combo'):
            self.selected_file_combo['values'] = file_list
            if file_list and self.selected_file_var.get() == "No file selected":
                self.selected_file_var.set(file_list[0])
        self._render_preview_frame()

    def _on_file_selection_change(self, event=None):
        """Gère le changement de sélection de fichier dans les paramètres d'encodage"""
        selected_filename = self.selected_file_var.get()
        if selected_filename == "No file selected":
            return
        
        # Trouver le job correspondant au fichier sélectionné
        for job_id, job_data in self.job_rows.items():
            job = job_data["job"]
            if job.src_path.name == selected_filename:
                # Sélectionner ce fichier dans l'inspecteur
                self.inspector_tree.selection_set(job_id)
                self.inspector_tree.see(job_id)
                # Déclencher l'inspection du fichier
                self._on_inspector_selection_change()
                # Afficher immédiatement un aperçu de frame
                self._render_preview_frame()
                break

    def _suggest_resolution_from_aspect_ratio(self, width: int, height: int):
        """Suggère une résolution basée sur le ratio d'aspect du fichier source"""
        if width == 0 or height == 0:
            return
        
        aspect_ratio = width / height
        
        # Déterminer si c'est du portrait ou du paysage
        if aspect_ratio < 1:  # Portrait (9:16, etc.)
            if aspect_ratio <= 0.5625:  # 9:16 ratio
                self.resolution_var_settings.set("1080x1920 (1080p Portrait)")
            elif aspect_ratio <= 0.75:  # 3:4 ratio
                self.resolution_var_settings.set("720x1280 (720p Portrait)")
        else:  # Paysage (16:9, etc.)
            if aspect_ratio >= 1.77:  # 16:9 ratio
                self.resolution_var_settings.set("1920x1080 (1080p)")
            elif aspect_ratio >= 1.33:  # 4:3 ratio
                self.resolution_var_settings.set("1280x720 (720p)")

    def _render_preview_frame(self):
        """Rend une frame de prévisualisation basée sur le timestamp saisi"""
        # Obtenir le fichier sélectionné dans les paramètres d'encodage
        selected_filename = self.selected_file_var.get()
        if selected_filename == "No file selected":
            self.preview_image_label.config(text="No file selected for preview")
            return
        
        # Trouver le job correspondant
        selected_job = None
        for job_id, job_data in self.job_rows.items():
            job = job_data["job"]
            if job.src_path.name == selected_filename:
                selected_job = job
                break
        
        if not selected_job:
            self.preview_image_label.config(text="Selected file not found in queue")
            return
        
        # Aperçu uniquement disponible pour les vidéos
        if selected_job.mode != "video":
            self.preview_image_label.config(text="Preview available for video files only")
            return

        timestamp = self.timestamp_var.get()
        
        # Vérifier si PIL est disponible pour afficher l'image
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self.preview_image_label.config(text="PIL not installed, preview unavailable")
            return
        
        # Créer un fichier temporaire pour la frame
        import tempfile
        import subprocess
        import os
        
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_path = temp_file.name
        
        # Construire la commande FFmpeg pour extraire une frame avec les paramètres de recadrage
        crop_left = self.crop_left_var.get() or "0"
        crop_right = self.crop_right_var.get() or "0"
        crop_top = self.crop_top_var.get() or "0"
        crop_bottom = self.crop_bottom_var.get() or "0"
        
        ffmpeg_cmd = [
            "ffmpeg",
            "-i", str(selected_job.src_path),
            "-ss", timestamp,
            "-vframes", "1",
            "-c:v", "png"
        ]
        
        # Ajouter le filtre de recadrage si nécessaire
        if any(val != "0" for val in [crop_left, crop_right, crop_top, crop_bottom]):
            crop_filter = f"crop=iw-{crop_left}-{crop_right}:ih-{crop_top}-{crop_bottom}:{crop_left}:{crop_top}"
            ffmpeg_cmd.extend(["-vf", crop_filter])
        
        ffmpeg_cmd.extend(["-y", temp_path])
        
        try:
            subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Charger et afficher l'image
            img = Image.open(temp_path)
            # Redimensionner l'image pour l'aperçu (par exemple, 300x200 max)
            img.thumbnail((300, 200))
            photo = ImageTk.PhotoImage(img)
            self.preview_image_label.config(image=photo, text="")
            self.preview_image_label.image = photo  # Garder une référence
        except Exception as e:
            self.preview_image_label.config(text=f"Error rendering frame: {str(e)}")
        finally:
            # Nettoyer le fichier temporaire
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def _preview_previous_frame(self):
        """Navigue à la frame précédente basée sur le timestamp actuel"""
        try:
            current_time = self._parse_timestamp(self.timestamp_var.get())
            if current_time > 1:
                current_time -= 1
                self.timestamp_var.set(self._format_timestamp(current_time))
                self._render_preview_frame()
        except ValueError:
            self.preview_image_label.config(text="Invalid timestamp format")

    def _preview_next_frame(self):
        """Navigue à la frame suivante basée sur le timestamp actuel"""
        try:
            current_time = self._parse_timestamp(self.timestamp_var.get())
            current_time += 1
            self.timestamp_var.set(self._format_timestamp(current_time))
            self._render_preview_frame()
        except ValueError:
            self.preview_image_label.config(text="Invalid timestamp format")

    def _parse_timestamp(self, timestamp: str) -> int:
        """Convertit un timestamp HH:MM:SS en secondes"""
        parts = timestamp.split(':')
        if len(parts) != 3:
            raise ValueError("Invalid timestamp format")
        hours, minutes, seconds = map(int, parts)
        return hours * 3600 + minutes * 60 + seconds

    def _format_timestamp(self, seconds: int) -> str:
        """Convertit des secondes en timestamp HH:MM:SS"""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"