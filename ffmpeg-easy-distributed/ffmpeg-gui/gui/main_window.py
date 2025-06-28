import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import (Checkbutton, DoubleVar, Frame, IntVar, Listbox, Menu,
                   Scrollbar, StringVar, Text, Toplevel, Tk, BooleanVar, Canvas,
                   filedialog, messagebox, simpledialog, ttk)
import tkinter as tk
from typing import Optional, List
import uuid
import logging

#j'ajoute dynamiquement le chemin racine du projet au PYTHONPATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.encode_job import EncodeJob, OutputConfig
from core.ffmpeg_helpers import FFmpegHelpers
from core.settings import Settings, load_settings
from core.distributed_client import DistributedClient
from core.server_discovery import ServerDiscovery
from core.job_scheduler import JobScheduler
from shared.messages import JobProgress, JobResult, ServerInfo

from gui.settings_window import SettingsWindow
from gui.job_edit_window import JobEditWindow
from gui.log_viewer_window import LogViewerWindow
from gui.batch_operations_window import BatchOperationsWindow
from gui.advanced_filters_window import AdvancedFiltersWindow
from gui.audio_tracks_window import AudioTracksWindow
from gui.folder_watcher import FolderWatcher
from gui.server_manager_window import ServerManagerWindow
from gui.job_queue_window import JobQueueWindow
from gui.capability_viewer import CapabilityViewerWindow
from gui.merge_videos_window import MergeVideosWindow
from gui.subtitle_management_window import SubtitleManagementWindow

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

try:
    from watchdog.observers import Observer # type: ignore
    from watchdog.events import FileSystemEventHandler
except ImportError:
    Observer = None
    class FileSystemEventHandler:
        pass

class TransientInfoDialog(Toplevel):
    def __init__(self, parent, title, message, auto_dismiss_ms=None):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)

        main_frame = ttk.Frame(self, padding="10", style="InfoDialog.TFrame")
        main_frame.pack(expand=True, fill=tk.BOTH)

        ttk.Label(main_frame, text=message, wraplength=350, anchor="center").pack(padx=20, pady=10)

        button_frame = ttk.Frame(main_frame, style="InfoDialog.TFrame")
        button_frame.pack(pady=(0, 10))
        ok_button = ttk.Button(button_frame, text="OK", command=self._dismiss_dialog, style="InfoDialog.TButton")
        ok_button.pack()

        s = ttk.Style()
        s.configure("InfoDialog.TFrame", background="#f0f0f0")
        s.configure("InfoDialog.TButton", padding=5)

        self.update_idletasks()
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        dialog_width = self.winfo_width()
        dialog_height = self.winfo_height()
        x = parent_x + (parent_width // 2) - (dialog_width // 2)
        y = parent_y + (parent_height // 2) - (dialog_height // 2)
        self.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")

        self.bind("<Escape>", lambda e: self._dismiss_dialog())
        self._root_click_handler_id = parent.winfo_toplevel().bind("<Button-1>", self._handle_root_click, add="+")

        if auto_dismiss_ms:
            self._auto_dismiss_timer = self.after(auto_dismiss_ms, self._dismiss_dialog)

        ok_button.focus_set()

    def _handle_root_click(self, event):
        if event.widget.winfo_toplevel() != self:
            self._dismiss_dialog()

    def _dismiss_dialog(self):
        if hasattr(self, '_auto_dismiss_timer'):
            self.after_cancel(self._auto_dismiss_timer)
        if hasattr(self, '_root_click_handler_id'):
            self.master.winfo_toplevel().unbind("<Button-1>", self._root_click_handler_id)
            delattr(self, '_root_click_handler_id')
        if self.winfo_exists():
            self.destroy()

class MainWindow:
    def __init__(self, root, distributed_client: DistributedClient, server_discovery: ServerDiscovery, job_scheduler: JobScheduler, loop, run_async_func):
        self.root = root
        self.root.title("FFmpeg Frontend")
        self.root.geometry("1200x800")
        
        self.distributed_client = distributed_client
        self.server_discovery = server_discovery
        self.job_scheduler = job_scheduler
        self.settings = load_settings()
        self.loop = loop
        self.run_async_func = run_async_func
        self.logger = logging.getLogger(__name__)
        self.server_map = {}

        # Observer les changements de serveur pour mettre à jour la map et le statut
        self.server_discovery.register_server_update_callback(self._update_server_map_and_status)

        self.jobs: list[EncodeJob] = []
        self.job_rows: dict[str, dict] = {} # {tree_id: {"job": EncodeJob, "outputs": {output_id: tree_id}}}
        self.last_import_job_ids: list[str] = []
        
        self.is_running = False
        self.input_folder = StringVar()
        self.output_folder = StringVar()
        
        self.watch_var = BooleanVar(value=False)
        self.log_viewer = None
        
        self.cq_var = StringVar(value="22")
        self.trim_start_var = StringVar(value="00:00:00")
        self.trim_end_var = StringVar(value="00:00:00")
        
        self.hdr_detected_var = BooleanVar(value=False)
        self.tonemap_var = BooleanVar(value=False)
        self.tonemap_method_var = StringVar(value="hable")
        self.preserve_hdr_var = BooleanVar(value=True)
        
        self.resolution_var = StringVar(value="N/A")
        self.duration_var = StringVar(value="N/A")
        self.vcodec_var = StringVar(value="N/A")
        self.vbitrate_var = StringVar(value="N/A")
        self.acodec_var = StringVar(value="N/A")
        self.abitrate_var = StringVar(value="N/A")
        self.achannels_var = StringVar(value="N/A")
        
        self.post_encode_action_var = StringVar(value="rien")
        
        self._build_menu()
        self._build_layout()
        self._setup_drag_drop()
        
        # Initialiser après la construction de l'UI
        self.root.after(100, self._initial_ui_setup)
        
    def _initial_ui_setup(self):
        """Fonction pour configurer l'état initial de l'UI après son chargement."""
        self._update_preset_list()
        self._update_media_type_ui(self.global_type_var.get())
        self._update_codec_choices()

    def _build_layout(self):
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=1)
        
        right_paned = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(right_paned, weight=2)
        
        self._build_file_section(left_frame)
        
        encoding_frame = ttk.LabelFrame(left_frame, text="Paramètres d'encodage", padding="10")
        encoding_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self._build_encoding_section(encoding_frame)
        
        queue_frame = ttk.LabelFrame(right_paned, text="Queue d'encodage", padding="5")
        right_paned.add(queue_frame, weight=3)
        
        tree_frame = ttk.Frame(queue_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = ("Fichier", "Codec", "Qualité", "Progrès", "Statut", "Serveur")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        
        for col in columns:
            self.tree.heading(col, text=col)
            if col == "Fichier":
                self.tree.column(col, width=250)
            elif col == "Codec":
                self.tree.column(col, width=80, anchor=tk.CENTER)
            elif col == "Qualité":
                self.tree.column(col, width=80, anchor=tk.CENTER)
            elif col == "Progrès":
                self.tree.column(col, width=80, anchor=tk.CENTER)
            else:
                self.tree.column(col, width=100)
        
        tree_scrollbar_v = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scrollbar_h = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scrollbar_v.set, xscrollcommand=tree_scrollbar_h.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scrollbar_v.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scrollbar_h.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_queue_selection_change)
        
        control_frame = ttk.Frame(queue_frame)
        control_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.start_btn = ttk.Button(control_frame, text="Démarrer", command=self._start_encoding, state="disabled")
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.pause_btn = ttk.Button(control_frame, text="Pause All", command=self._pause_all, state="disabled")
        self.pause_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.resume_btn = ttk.Button(control_frame, text="Resume All", command=self._resume_all, state="disabled")
        self.resume_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.cancel_btn = ttk.Button(control_frame, text="Cancel All", command=self._cancel_all, state="disabled")
        self.cancel_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        ttk.Button(control_frame, text="Clear Queue", command=self._clear_queue).pack(side=tk.LEFT)
        ttk.Button(control_frame, text="Réassigner", command=self._reassign_selected_jobs).pack(side=tk.LEFT, padx=(5, 0))
        
        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Modifier", command=self._edit_selected_job)
        self.context_menu.add_command(label="Dupliquer", command=self._duplicate_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Pause", command=self._pause_selected_job)
        self.context_menu.add_command(label="Resume", command=self._resume_selected_job)
        self.context_menu.add_command(label="Cancel", command=self._cancel_selected_job)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Supprimer", command=self._remove_selected_job)
        
        progress_frame = ttk.Frame(queue_frame)
        progress_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(progress_frame, text="Progrès global:").pack(side=tk.LEFT)
        self.progress_bar = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        
        inspector_frame = ttk.LabelFrame(right_paned, text="Inspecteur de média", padding="5")
        right_paned.add(inspector_frame, weight=2)
        
        inspector_tree_frame = ttk.Frame(inspector_frame)
        inspector_tree_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(inspector_tree_frame, text="Fichier:").pack(side=tk.LEFT)
        self.inspector_tree = ttk.Treeview(inspector_tree_frame, columns=("name",), show="headings", height=3)
        self.inspector_tree.heading("name", text="Nom du fichier")
        self.inspector_tree.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        
        self.inspector_info_frame = ttk.Frame(inspector_frame)
        self.inspector_info_frame.pack(fill=tk.BOTH, expand=True)

        self.inspector_tree.bind("<<TreeviewSelect>>", self._on_inspector_selection_change)

    def _build_encoding_section(self, parent):
        self.selected_file_var = StringVar(value="No file selected")
        self.preset_name_var = StringVar()
        self.selected_job_for_settings_var = StringVar()
        self.resolution_var_settings = StringVar()
        self.crop_top_var = StringVar(value="0")
        self.crop_bottom_var = StringVar(value="0")
        self.crop_left_var = StringVar(value="0")
        self.crop_right_var = StringVar(value="0")
        self.global_type_var = StringVar(value="video")
        self.global_codec_var = StringVar()
        self.global_encoder_var = StringVar()
        self.container_var = StringVar()
        self.video_mode_var = StringVar(value="quality")
        self.quality_var = StringVar(value="22")
        self.preset_var = StringVar(value="medium")
        self.bitrate_var = StringVar(value="4000")
        self.multipass_var = BooleanVar(value=False)
        self.custom_flags_var = StringVar()
        self.timestamp_var = StringVar(value="00:00:10")
        self.subtitle_mode_var = StringVar(value="copy")
        self.subtitle_path_var = StringVar()

        canvas_frame = ttk.Frame(parent)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        self.settings_canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.settings_canvas.yview)
        main_frame = ttk.Frame(self.settings_canvas)
        
        main_frame.bind('<Configure>', self._on_frame_configure)
        self.settings_canvas.bind('<Configure>', self._on_canvas_configure)
        
        canvas_window = self.settings_canvas.create_window((0, 0), window=main_frame, anchor="nw")
        self.settings_canvas.configure(yscrollcommand=scrollbar.set)
        
        self.settings_canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        self._canvas_window = canvas_window
        self._scrollbar = scrollbar
        
        def _on_mousewheel(event):
            if sys.platform == "darwin":
                self.settings_canvas.yview_scroll(int(-1 * event.delta), "units")
            elif sys.platform == "win32":
                self.settings_canvas.yview_scroll(-1 * (event.delta // 120), "units")
            elif hasattr(event, 'num'):
                if event.num == 4:
                    self.settings_canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    self.settings_canvas.yview_scroll(1, "units")
            elif hasattr(event, 'delta') and event.delta != 0:
                 self.settings_canvas.yview_scroll(int(-1 * event.delta), "units")

        def bind_to_mousewheel(widget):
            widget.bind("<MouseWheel>", _on_mousewheel)
            widget.bind("<Button-4>", _on_mousewheel)
            widget.bind("<Button-5>", _on_mousewheel)

        bind_to_mousewheel(self.settings_canvas)
        bind_to_mousewheel(main_frame)

        self.settings_canvas.bind("<Enter>", lambda e: self.settings_canvas.focus_set())
        
        def bind_recursive(widget):
            bind_to_mousewheel(widget)
            for child in widget.winfo_children():
                bind_recursive(child)
        
        main_frame.after(100, lambda: bind_recursive(main_frame))
        
        file_apply_frame = ttk.Frame(main_frame, padding="5")
        file_apply_frame.pack(fill=tk.X, pady=(5, 5))

        ttk.Label(file_apply_frame, text="Fichier à configurer:").pack(side=tk.LEFT, padx=(0, 5))
        self.job_selector_combobox = ttk.Combobox(file_apply_frame, textvariable=self.selected_job_for_settings_var, state="readonly", width=40)
        self.job_selector_combobox.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0,10))
        self.job_selector_combobox.bind("<<ComboboxSelected>>", self._on_job_selected_for_settings_change)

        self.apply_settings_btn = ttk.Button(file_apply_frame, text="Appliquer", command=self._apply_ui_settings_to_selected_job_via_combobox)
        self.apply_settings_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.apply_to_last_batch_btn = ttk.Button(file_apply_frame, text="Appliquer au dernier import", command=self._apply_ui_settings_to_last_import_batch)
        self.apply_to_last_batch_btn.pack(side=tk.LEFT)

        preset_frame = ttk.LabelFrame(main_frame, text="Préréglage", padding="5")
        preset_frame.pack(fill=tk.X, pady=(0, 5))
        self.preset_combo = ttk.Combobox(preset_frame, textvariable=self.preset_name_var, state="readonly")
        self.preset_combo.pack(fill=tk.X, expand=True)
        self.preset_combo.bind("<<ComboboxSelected>>", self._load_preset)

        media_type_frame = ttk.LabelFrame(main_frame, text="Type de Média", padding="5")
        media_type_frame.pack(fill=tk.X, pady=(0, 5))
        self.media_type_combo = ttk.Combobox(media_type_frame, textvariable=self.global_type_var, values=["video", "audio", "image"], state="readonly")
        self.media_type_combo.pack(fill=tk.X, expand=True)
        self.media_type_combo.bind("<<ComboboxSelected>>", self._on_media_type_change)

        self.transform_frame = ttk.LabelFrame(main_frame, text="Taille et Rognage", padding="5")
        self.transform_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(self.transform_frame, text="Résolution:").grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.resolution_combo = ttk.Combobox(self.transform_frame, textvariable=self.resolution_var_settings, state="readonly")
        self.resolution_combo.grid(row=0, column=1, columnspan=3, sticky="ew", pady=(0, 5))
        self.resolution_combo.bind("<<ComboboxSelected>>", self._on_resolution_change)
        
        self.custom_resolution_frame = ttk.Frame(self.transform_frame)
        self.width_var = StringVar()
        self.height_var = StringVar()
        self.width_entry = ttk.Entry(self.custom_resolution_frame, textvariable=self.width_var, width=6)
        ttk.Label(self.custom_resolution_frame, text="x").pack(side='left')
        self.height_entry = ttk.Entry(self.custom_resolution_frame, textvariable=self.height_var, width=6)
        
        self._update_resolution_choices()
        
        ttk.Label(self.transform_frame, text="Rognage (px):").grid(row=1, column=0, sticky="w")
        ttk.Label(self.transform_frame, text="Haut:").grid(row=2, column=0, sticky="e", padx=5)
        ttk.Entry(self.transform_frame, textvariable=self.crop_top_var, width=5).grid(row=2, column=1)
        ttk.Label(self.transform_frame, text="Bas:").grid(row=2, column=2, sticky="e", padx=5)
        ttk.Entry(self.transform_frame, textvariable=self.crop_bottom_var, width=5).grid(row=2, column=3)
        ttk.Label(self.transform_frame, text="Gauche:").grid(row=3, column=0, sticky="e", padx=5)
        ttk.Entry(self.transform_frame, textvariable=self.crop_left_var, width=5).grid(row=3, column=1)
        ttk.Label(self.transform_frame, text="Droite:").grid(row=3, column=2, sticky="e", padx=5)
        ttk.Entry(self.transform_frame, textvariable=self.crop_right_var, width=5).grid(row=3, column=3)
        
        format_frame = ttk.LabelFrame(main_frame, text="Format et Codec", padding="5")
        format_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(format_frame, text="Conteneur:").grid(row=0, column=0, sticky="w", pady=2)
        self.container_combo = ttk.Combobox(format_frame, textvariable=self.container_var, state="readonly")
        self.container_combo.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Label(format_frame, text="Codec Vidéo:").grid(row=1, column=0, sticky="w", pady=2)
        self.global_codec_combo = ttk.Combobox(format_frame, textvariable=self.global_codec_var, state="readonly")
        self.global_codec_combo.grid(row=1, column=1, sticky="ew", pady=2)
        self.global_codec_combo.bind("<<ComboboxSelected>>", self._on_codec_change)
        ttk.Label(format_frame, text="Encodeur:").grid(row=2, column=0, sticky="w", pady=2)
        self.global_encoder_combo = ttk.Combobox(format_frame, textvariable=self.global_encoder_var, state="readonly", width=40)
        self.global_encoder_combo.grid(row=2, column=1, sticky="ew", pady=2)
        self.global_encoder_combo.bind("<<ComboboxSelected>>", self._on_encoder_change)
        
        self.quality_frame = ttk.LabelFrame(main_frame, text="Qualité", padding="5")
        self.quality_frame.pack(fill=tk.X, pady=(0, 5))
        
        self.video_mode_radio_quality = ttk.Radiobutton(self.quality_frame, text="Qualité Constante (CQ)", variable=self.video_mode_var, value="quality", command=self._on_video_mode_change)
        self.video_mode_radio_quality.grid(row=0, column=0, sticky="w")
        self.cq_entry = ttk.Entry(self.quality_frame, textvariable=self.quality_var, width=5)
        self.cq_entry.grid(row=0, column=1, sticky="w")
        
        self.video_mode_radio_bitrate = ttk.Radiobutton(self.quality_frame, text="Bitrate (kbps)", variable=self.video_mode_var, value="bitrate", command=self._on_video_mode_change)
        self.video_mode_radio_bitrate.grid(row=1, column=0, sticky="w")
        self.bitrate_entry = ttk.Entry(self.quality_frame, textvariable=self.bitrate_var, width=8)
        self.bitrate_entry.grid(row=1, column=1, sticky="w")
        self.multipass_check = ttk.Checkbutton(self.quality_frame, text="2-Passes", variable=self.multipass_var)
        self.multipass_check.grid(row=1, column=2, sticky="w")
        
        self.preset_label = ttk.Label(self.quality_frame, text="Preset Encodeur:")
        self.preset_label.grid(row=2, column=0, sticky="w", pady=(5,0))
        self.quality_entry = ttk.Combobox(self.quality_frame, textvariable=self.preset_var, state="readonly")
        self.quality_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(5,0))

        self.hdr_frame = ttk.LabelFrame(main_frame, text="HDR et Couleur", padding="5")
        self.hdr_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(self.hdr_frame, text="HDR Détecté:").grid(row=0, column=0, sticky="w", pady=2)
        self.hdr_status_label = ttk.Label(self.hdr_frame, text="Non détecté", foreground="gray")
        self.hdr_status_label.grid(row=0, column=1, sticky="w", pady=2)
        
        self.preserve_hdr_check = ttk.Checkbutton(self.hdr_frame, text="Préserver HDR (si possible)", 
                                                 variable=self.preserve_hdr_var)
        self.preserve_hdr_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        
        self.tonemap_check = ttk.Checkbutton(self.hdr_frame, text="Tone mapping vers SDR", 
                                           variable=self.tonemap_var, command=self._on_tonemap_change)
        self.tonemap_check.grid(row=2, column=0, sticky="w", pady=2)
        
        ttk.Label(self.hdr_frame, text="Méthode:").grid(row=2, column=1, sticky="w", padx=(10,0), pady=2)
        self.tonemap_method_combo = ttk.Combobox(self.hdr_frame, textvariable=self.tonemap_method_var,
                                               values=["hable", "mobius", "reinhard", "bt2390"], 
                                               state="readonly", width=10)
        self.tonemap_method_combo.grid(row=2, column=2, sticky="w", pady=2)
        
        self.subtitle_frame = ttk.LabelFrame(main_frame, text="Sous-titres", padding="5")
        self.subtitle_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(self.subtitle_frame, text="Mode:").grid(row=0, column=0, sticky="w", pady=2)
        self.subtitle_mode_combo = ttk.Combobox(self.subtitle_frame, textvariable=self.subtitle_mode_var,
                                               values=["copy", "burn", "remove", "embed"], 
                                               state="readonly", width=10)
        self.subtitle_mode_combo.grid(row=0, column=1, sticky="w", pady=2)
        self.subtitle_mode_combo.bind("<<ComboboxSelected>>", self._on_subtitle_mode_change)
        
        self.external_subtitle_frame = ttk.Frame(self.subtitle_frame)
        self.external_subtitle_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=2)
        
        ttk.Label(self.external_subtitle_frame, text="Fichier externe:").pack(side='left', padx=(0, 5))
        self.subtitle_path_entry = ttk.Entry(self.external_subtitle_frame, textvariable=self.subtitle_path_var, width=30)
        self.subtitle_path_entry.pack(side='left', expand=True, fill='x', padx=(0, 5))
        self.subtitle_browse_button = ttk.Button(self.external_subtitle_frame, text="...", command=self._browse_subtitle_file, width=3)
        self.subtitle_browse_button.pack(side='left')

        self.lut_frame = ttk.LabelFrame(main_frame, text="Effets (LUT)", padding="5")
        self.lut_frame.pack(fill=tk.X, pady=(0,5))

        self.lut_path_var = StringVar()
        ttk.Label(self.lut_frame, text="Fichier LUT (.cube, .look, .3dl):").grid(row=0, column=0, sticky="w", pady=2)
        self.lut_path_entry = ttk.Entry(self.lut_frame, textvariable=self.lut_path_var, width=40)
        self.lut_path_entry.grid(row=1, column=0, sticky="ew", pady=2, padx=(0,5))
        self.lut_browse_button = ttk.Button(self.lut_frame, text="...", command=self._browse_lut_file, width=3)
        self.lut_browse_button.grid(row=1, column=1, sticky="w", pady=2)

        ttk.Separator(self.lut_frame, orient=tk.HORIZONTAL).grid(row=2, column=0, columnspan=2, sticky="ew", pady=5)

        ttk.Label(self.lut_frame, text="Watermark PNG:").grid(row=3, column=0, sticky="w", pady=(5,2))
        self.watermark_path_var = StringVar()
        self.watermark_path_entry = ttk.Entry(self.lut_frame, textvariable=self.watermark_path_var, width=40)
        self.watermark_path_entry.grid(row=4, column=0, sticky="ew", pady=2, padx=(0,5))
        self.watermark_browse_button = ttk.Button(self.lut_frame, text="...", command=self._browse_watermark_file, width=3)
        self.watermark_browse_button.grid(row=4, column=1, sticky="w", pady=2)

        ttk.Label(self.lut_frame, text="Position:").grid(row=5, column=0, sticky="w", pady=2)
        self.watermark_position_var = StringVar(value="top_right")
        self.watermark_pos_combo = ttk.Combobox(self.lut_frame, textvariable=self.watermark_position_var,
                                                values=["top_left", "top_right", "bottom_left", "bottom_right", "center"],
                                                state="readonly", width=15)
        self.watermark_pos_combo.grid(row=5, column=1, sticky="w", pady=2)

        ttk.Label(self.lut_frame, text="Scale (relative to video width):").grid(row=6, column=0, sticky="w", pady=2)
        self.watermark_scale_var = DoubleVar(value=0.1)
        ttk.Spinbox(self.lut_frame, from_=0.01, to=1.0, increment=0.01, textvariable=self.watermark_scale_var, width=6).grid(row=6, column=1, sticky="w", pady=2)

        ttk.Label(self.lut_frame, text="Opacity (0.0-1.0):").grid(row=7, column=0, sticky="w", pady=2)
        self.watermark_opacity_var = DoubleVar(value=1.0)
        ttk.Spinbox(self.lut_frame, from_=0.0, to=1.0, increment=0.1, textvariable=self.watermark_opacity_var, width=6).grid(row=7, column=1, sticky="w", pady=2)

        ttk.Label(self.lut_frame, text="Padding (px):").grid(row=8, column=0, sticky="w", pady=2)
        self.watermark_padding_var = IntVar(value=10)
        ttk.Spinbox(self.lut_frame, from_=0, to=100, increment=1, textvariable=self.watermark_padding_var, width=6).grid(row=8, column=1, sticky="w", pady=2)

        self.lut_frame.columnconfigure(0, weight=1)
        
        self.transform_frame.columnconfigure(1, weight=1)
        self.transform_frame.columnconfigure(3, weight=1)
        format_frame.columnconfigure(1, weight=1)
        self.quality_frame.columnconfigure(2, weight=1)
        self.subtitle_frame.columnconfigure(2, weight=1)
        
        # Le label du codec sera mis à jour dynamiquement
        self.codec_label = ttk.Label(format_frame, text="Codec:")
        self.codec_label.grid(row=1, column=0, sticky="w", pady=2)
        self.global_codec_combo = ttk.Combobox(format_frame, textvariable=self.global_codec_var, state="readonly")
        self.global_codec_combo.grid(row=1, column=1, sticky="ew", pady=2)
        self.global_codec_combo.bind("<<ComboboxSelected>>", self._on_codec_change)
        
        ttk.Label(format_frame, text="Encodeur:").grid(row=2, column=0, sticky="w", pady=2)
        self.global_encoder_combo = ttk.Combobox(format_frame, textvariable=self.global_encoder_var, state="readonly", width=40)
        self.global_encoder_combo.grid(row=2, column=1, sticky="ew", pady=2)
        self.global_encoder_combo.bind("<<ComboboxSelected>>", self._on_encoder_change)

        self._on_video_mode_change()
        self._on_tonemap_change()
        
        initial_media_type = self.global_type_var.get() or "video"
        self._update_media_type_ui(initial_media_type)
        
        self.root.after(100, self._update_scroll_state)
        
    def _on_media_type_change(self, event=None):
        media_type = self.global_type_var.get()
        self._update_media_type_ui(media_type)
        
        # Réinitialiser les choix pour ce nouveau type de média
        self.global_codec_var.set("")
        self.global_encoder_var.set("")
        self.container_var.set("")
        
        # Mettre à jour dans le bon ordre
        self._update_codec_choices()
        self._update_container_choices()
        self._update_quality_controls_for_global()
    
    def _on_codec_change(self, event=None):
        self._update_encoder_choices()
        self._update_container_choices()
        self._update_quality_controls_for_global()
    
    def _on_encoder_change(self, event=None):
        self._update_quality_preset_controls()
        self._update_quality_controls_for_global()
    
    def _on_tonemap_change(self):
        if self.tonemap_var.get():
            self.tonemap_method_combo.config(state="readonly")
            self.preserve_hdr_var.set(False)
        else:
            self.tonemap_method_combo.config(state="disabled")
    
    def _on_frame_configure(self, event):
        self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))
        self._update_scroll_state()
    
    def _on_canvas_configure(self, event):
        canvas_width = event.width
        self.settings_canvas.itemconfig(self._canvas_window, width=canvas_width)
        self._update_scroll_state()
    
    def _update_scroll_state(self):
        bbox = self.settings_canvas.bbox("all")
        if bbox:
            content_height = bbox[3] - bbox[1]
            canvas_height = self.settings_canvas.winfo_height()
            
            if content_height > canvas_height:
                self._scrollbar.pack(side="right", fill="y")
            else:
                self._scrollbar.pack_forget()
    
    def _update_resolution_choices(self):
        resolution_choices = [
            "Keep Original", "3840x2160 (4K)", "2560x1440 (1440p)", "1920x1080 (1080p)", "1280x720 (720p)",
            "854x480 (480p)", "640x360 (360p)", "1080x1920 (1080p Portrait)", "720x1280 (720p Portrait)",
            "480x854 (480p Portrait)", "540x960 (TikTok/Stories)", "1125x2000 (Instagram Stories)",
            "3440x1440 (Ultrawide 1440p)", "2560x1080 (Ultrawide 1080p)", "1920x1200 (WUXGA)",
            "1680x1050 (WSXGA+)", "1440x900 (WXGA+)", "1280x800 (WXGA)", "Custom"
        ]
        self.resolution_combo['values'] = resolution_choices
        if not self.resolution_var_settings.get():
            self.resolution_var_settings.set("Keep Original")

    def _apply_settings_to_selected_file(self):
        selected_filename = self.selected_file_var.get()
        if selected_filename == "No file selected" or not selected_filename:
            messagebox.showwarning("No File Selected", "Please select a file to apply settings to.")
            return
        
        target_job = None
        for job_id, job_data in self.job_rows.items():
            job = job_data["job"]
            if job.src_path.name == selected_filename:
                target_job = job
                break
        
        if not target_job:
            messagebox.showerror("Job Not Found", "Could not find the job for the selected file.")
            return
        
        self._apply_ui_settings_to_job(target_job)
        self._update_job_row(target_job)
        messagebox.showinfo("Settings Applied", f"Settings have been applied to '{selected_filename}'")

    def _apply_settings_to_all_files(self):
        if not self.jobs:
            messagebox.showwarning("No Files", "No files in the queue to apply settings to.")
            return
        
        result = messagebox.askyesno("Apply to All", f"Apply current settings to all {len(self.jobs)} files in the queue?", icon='question')
        if not result:
            return
        
        for job in self.jobs:
            self._apply_ui_settings_to_job(job)
            self._update_job_row(job)
        
        messagebox.showinfo("Settings Applied", f"Settings have been applied to all {len(self.jobs)} files")

    def _reset_settings_ui(self):
        result = messagebox.askyesno("Reset Settings", "Reset all encoding settings to default values?", icon='question')
        if not result:
            return
        
        self.global_type_var.set("video")
        self.global_codec_var.set("")
        self.global_encoder_var.set("")
        self.container_var.set("")
        self.quality_var.set("")
        self.preset_var.set("")
        self.cq_var.set("")
        self.custom_flags_var.set("")
        self.video_mode_var.set("cq")
        self.bitrate_var.set("")
        self.multipass_var.set(False)
        self.resolution_var_settings.set("")
        self.subtitle_mode_var.set("copy")
        self.subtitle_path_var.set("")
        self.trim_start_var.set("")
        self.trim_end_var.set("")
        
        self._update_codec_choices()
        messagebox.showinfo("Settings Reset", "All encoding settings have been reset to default values")

    def _build_menu(self):
        self.menubar = Menu(self.root)
        file_menu = Menu(self.menubar, tearoff=0)
        file_menu.add_command(label="Add Files…", command=self._add_files)
        file_menu.add_command(label="Add Folder…", command=self._add_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Add Files or Folder…", command=self._add_files_or_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        self.menubar.add_cascade(label="File", menu=file_menu)
        
        edit_menu = Menu(self.menubar, tearoff=0)
        edit_menu.add_command(label="Batch Operations", command=self._batch_operations)
        edit_menu.add_command(label="Advanced Filters", command=self._advanced_filters)
        edit_menu.add_command(label="Audio Tracks", command=self._configure_audio_tracks)
        edit_menu.add_command(label="Subtitles...", command=self._manage_subtitles)
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear Queue", command=self._clear_queue)
        edit_menu.add_command(label="Réassigner Jobs...", command=self._reassign_selected_jobs)
        edit_menu.add_separator()
        edit_menu.add_command(label="Merge Videos", command=self._merge_videos)
        self.menubar.add_cascade(label="Edit", menu=edit_menu)

        preset_menu = Menu(self.menubar, tearoff=0)
        preset_menu.add_command(label="Save Current as Preset…", command=self._save_preset)
        preset_menu.add_separator()
        for preset_name in self.settings.data["presets"].keys():
            preset_menu.add_command(label=preset_name, command=lambda name=preset_name: self._load_preset_by_name(name))
        self.menubar.add_cascade(label="Presets", menu=preset_menu)
        
        view_menu = Menu(self.menubar, tearoff=0)
        view_menu.add_command(label="Show Log Viewer", command=self._show_log_viewer)
        self.menubar.add_cascade(label="View", menu=view_menu)

        servers_menu = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Serveurs", menu=servers_menu)
        
        servers_menu.add_command(label="Gestion Serveurs", command=self.open_server_manager)
        servers_menu.add_command(label="File d'Attente Distribuée", command=self.open_job_queue_window)
        servers_menu.add_command(label="Capacités Serveurs", command=self.open_capability_viewer_window)
        servers_menu.add_separator()
        servers_menu.add_command(label="Test Connexions", command=lambda: self.run_async_func(self.test_all_servers(), self.loop))

        settings_menu = Menu(self.menubar, tearoff=0)
        settings_menu.add_command(label="Preferences…", command=self._open_settings)
        self.menubar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=self.menubar)

    def _create_status_bar(self):
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(side=tk.BOTTOM, fill=tk.X)
        
        self.status_var = StringVar(value="Prêt")
        self.status_label = ttk.Label(self.status_frame, textvariable=self.status_var)
        self.status_label.pack(side=tk.LEFT, padx=5)
        
        self.servers_var = StringVar(value="🔴 Aucun serveur")
        self.servers_label = ttk.Label(self.status_frame, textvariable=self.servers_var)
        self.servers_label.pack(side=tk.RIGHT, padx=5)

    def update_server_status(self, connected_servers: List[ServerInfo]):
        """Met à jour le statut des serveurs dans la barre d'état."""
        all_known_servers = list(self.server_discovery.get_all_servers().values())
        local_server_info = self.job_scheduler.get_local_server_info()

        # Le serveur local est toujours implicitement présent, on s'intéresse aux serveurs distants configurés.
        # get_all_servers() peut contenir le serveur local si on l'ajoute manuellement, donc on le filtre.
        remote_servers = [s for s in all_known_servers if s.server_id != local_server_info.server_id]
        
        # Les serveurs connectés sont ceux qui sont activement en ligne.
        connected_remote_servers = [s for s in connected_servers if s.server_id != local_server_info.server_id]
        
        # Les jobs totaux incluent le local et les distants connectés
        all_available_servers = connected_remote_servers + [local_server_info]
        total_jobs = sum(s.current_jobs for s in all_available_servers)

        if not remote_servers:
            # // S'il n'y a aucun serveur distant configuré, on est en mode local.
            self.servers_var.set("🟡 Serveur local uniquement")
        else:
            # // S'il y a des serveurs distants configurés.
            connected_count = len(connected_remote_servers)
            if connected_count > 0:
                # // Au moins un serveur distant est connecté.
                self.servers_var.set(f"🟢 {connected_count} serveur(s) distant(s) connecté(s) - {total_jobs} jobs")
            else:
                # // Des serveurs distants sont configurés mais aucun n'est connecté.
                self.servers_var.set(f"🔴 Serveurs distants déconnectés - {total_jobs} jobs (local)")

        self._update_server_map_and_status(connected_servers)

    def _update_server_map_and_status(self, connected_servers: List[ServerInfo]):
        """Met à jour le dictionnaire des serveurs et la barre de statut."""
        self.server_map.clear()
        local_server = self.job_scheduler.get_local_server_info()
        if local_server:
            self.server_map[local_server.server_id] = local_server.name
        
        all_servers = self.server_discovery.get_all_servers()
        for server_id, server_info in all_servers.items():
            self.server_map[server_id] = server_info.name
            
        self.update_server_status(connected_servers)
        
        # Mettre à jour les jobs existants pour refléter les noms de serveur
        for job_id in self.tree.get_children():
            if job_id in self.job_rows:
                self._update_job_row(self.job_rows[job_id]["job"])

    def open_server_manager(self):
        ServerManagerWindow(self.root, self.server_discovery, self.loop, self.run_async_func)

    def open_job_queue_window(self):
        JobQueueWindow(self.root, self.job_scheduler, self.server_discovery)

    def open_capability_viewer_window(self):
        CapabilityViewerWindow(self.root, self.server_discovery, self.job_scheduler.capability_matcher)

    async def test_all_servers(self):
        self.status_var.set("Test des connexions serveurs...")
        servers = self.server_discovery.get_all_servers().values()
        for server_info in servers:
            is_reachable = await self.distributed_client.ping_server(server_info.server_id)
            if is_reachable:
                logging.info(f"Serveur {server_info.name} ({server_info.ip}:{server_info.port}) est joignable.")
            else:
                logging.warning(f"Serveur {server_info.name} ({server_info.ip}:{server_info.port}) n'est PAS joignable.")
        self.status_var.set("Test des connexions terminé.")

    def _build_file_section(self, parent_frame):
        self.input_folder = StringVar(value="No input folder selected")
        self.output_folder = StringVar(value="No output folder selected")

        folder_grid = ttk.Frame(parent_frame)
        folder_grid.pack(fill="x")

        ttk.Label(folder_grid, text="Input:", font=("Helvetica", 11, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.input_folder_entry = ttk.Entry(folder_grid, textvariable=self.input_folder, width=60, font=("Helvetica", 10))
        self.input_folder_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Button(folder_grid, text="Browse", command=self._select_input_folder, width=8).grid(row=0, column=2)

        ttk.Label(folder_grid, text="Output:", font=("Helvetica", 11, "bold")).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
        self.output_folder_entry = ttk.Entry(folder_grid, textvariable=self.output_folder, width=60, font=("Helvetica", 10))
        self.output_folder_entry.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(8, 0))
        
        self.output_folder.trace_add("write", lambda *args: self._update_control_buttons_state('idle'))
        ttk.Button(folder_grid, text="Browse", command=self._select_output_folder, width=8).grid(row=1, column=2, pady=(8, 0))
        
        info_label = ttk.Label(folder_grid, text="Optional: If no output folder is selected, files will be saved in the same folder as source with encoder suffix (e.g., filename_x265.mp4)", 
                              font=("Helvetica", 9), foreground="gray")
        info_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(5, 0))

        folder_grid.columnconfigure(1, weight=1)

        buttons_row = ttk.Frame(parent_frame)
        buttons_row.pack(fill="x", pady=(15, 0))
        
        ttk.Button(buttons_row, text="Add Files", command=self._add_files).pack(side="left", padx=(0, 10))
        ttk.Button(buttons_row, text="Add Folder", command=self._add_folder).pack(side="left", padx=(0, 10))
        ttk.Button(buttons_row, text="Add from URL", command=self._add_from_url).pack(side="left", padx=(0, 10))

        watch_frame = ttk.LabelFrame(parent_frame, text="Surveillance de dossier", padding="5")
        watch_frame.pack(fill=tk.X, pady=(15, 0))
        watch_toggle = ttk.Checkbutton(watch_frame, text="Surveiller le dossier d'entrée", variable=self.watch_var, command=self._toggle_watch)
        watch_toggle.pack(side=tk.TOP, fill=tk.X)
        
        preset_frame = ttk.Frame(watch_frame)
        preset_frame.pack(fill=tk.X, pady=5)
        ttk.Label(preset_frame, text="Préréglage pour les nouveaux fichiers:").pack(side=tk.LEFT)
        self.watch_preset_combo = ttk.Combobox(preset_frame, state="readonly")
        self.watch_preset_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        preset_names = list(self.settings.data.get("presets", {}).keys())
        if preset_names:
            self.watch_preset_combo['values'] = preset_names
            self.watch_preset_combo.set(preset_names[0])
        
        self.watch_status = ttk.Label(watch_frame, text="Statut: Inactif")
        self.watch_status.pack(side=tk.BOTTOM, fill=tk.X, pady=5)

    def _add_files(self):
        paths = filedialog.askopenfilenames(title="Select input files")
        if not paths:
            return
        self._enqueue_paths([Path(p) for p in paths])

    def _add_folder(self):
        folder_path = filedialog.askdirectory(title="Select a Folder")
        if folder_path:
            self.input_folder.set(folder_path)
            paths = list(Path(folder_path).rglob("*.*"))
            self._enqueue_paths(paths)

    def _merge_videos(self):
        MergeVideosWindow(self.root, self)

    def _add_files_or_folder(self):
        choice = messagebox.askyesnocancel("Add Files or Folder", "Yes = Files, No = Folder")
        if choice is True:
            self._add_files()
        elif choice is False:
            self._add_folder()

    def _add_from_url(self):
        url = simpledialog.askstring("Add from URL", "Enter a video URL:")
        if not url:
            return
        threading.Thread(target=self._download_and_enqueue, args=(url,), daemon=True).start()

    def _download_and_enqueue(self, url):
        try:
            import tempfile
            temp_dir = tempfile.mkdtemp()
            command = ["yt-dlp", "-o", os.path.join(temp_dir, "%(title)s.%(ext)s"), url]
            subprocess.run(command, check=True)
            downloaded_files = list(Path(temp_dir).rglob("*.*"))
            if downloaded_files:
                self.root.after_idle(self._enqueue_paths, downloaded_files)
            else:
                messagebox.showerror("Download Error", "Could not find the downloaded file.")
        except Exception as e:
            messagebox.showerror("Download Error", f"Could not download video: {e}")

    def _enqueue_paths(self, paths: list[Path]):
        out_root = Path(self.output_folder.get()) if self.output_folder.get() and not self.output_folder.get().startswith("(no") else None
        keep_structure = self.settings.data.get("keep_folder_structure", True) # Changed Settings.data to self.settings.data
        input_folder = self.input_folder.get()
        
        current_batch_job_ids = []

        for p in paths:
            mode = self._detect_mode(p)
            if self.global_type_var.get() == "unknown":
                self.global_type_var.set(mode)
            
            if out_root and keep_structure and input_folder and not input_folder.startswith("(no"):
                try:
                    input_path = Path(input_folder)
                    relative = p.relative_to(input_path)
                except (ValueError, OSError):
                    relative = p.name
            else:
                relative = p.name
            
            container = self._get_container_from_display(self.container_var.get())
            
            if not container:
                if mode == "video": container = "mp4"
                elif mode == "audio": container = "m4a"
                elif mode == "image": container = "png"
                else: container = "mp4"
            
            initial_dst_path = None
            if out_root:
                dst_basename = relative if isinstance(relative, Path) else Path(relative)
                initial_dst_path = out_root / dst_basename
                initial_dst_path = initial_dst_path.with_suffix("." + container)
                initial_dst_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                encoder_display = self.global_encoder_var.get()
                encoder_name = self._get_encoder_name_from_display(encoder_display) if encoder_display else ""
                
                if "x265" in encoder_name or "hevc" in encoder_name: suffix = "_x265"
                elif "x264" in encoder_name or "h264" in encoder_name: suffix = "_x264"
                elif "av1" in encoder_name: suffix = "_av1"
                elif "vp9" in encoder_name: suffix = "_vp9"
                elif "nvenc" in encoder_name: suffix = "_nvenc"
                elif "qsv" in encoder_name: suffix = "_qsv" 
                elif "amf" in encoder_name: suffix = "_amf"
                elif "videotoolbox" in encoder_name: suffix = "_vt"
                elif mode == "audio":
                    if "aac" in encoder_name: suffix = "_aac"
                    elif "mp3" in encoder_name: suffix = "_mp3"
                    elif "opus" in encoder_name: suffix = "_opus"
                    elif "flac" in encoder_name: suffix = "_flac"
                    else: suffix = "_audio"
                elif mode == "image":
                    if "webp" in encoder_name: suffix = "_webp"
                    elif "avif" in encoder_name: suffix = "_avif"
                    else: suffix = "_img"
                else: suffix = "_encoded"
                
                stem = p.stem
                initial_dst_path = p.parent / f"{stem}{suffix}.{container}"

            output_cfg = OutputConfig(name="Default", initial_dst_path=initial_dst_path, mode=mode)
            
            # Appliquer les paramètres actuels de l'UI
            encoder_display = self.global_encoder_var.get()
            encoder_name = self._get_encoder_name_from_display(encoder_display) if encoder_display else ""
            
            # Si pas d'encodeur spécifié, utiliser un encodeur par défaut basé sur le codec
            if not encoder_name:
                codec = self.global_codec_var.get()
                if codec == "webp":
                    encoder_name = "libwebp"
                elif codec == "h264":
                    encoder_name = "libx264"
                elif codec == "hevc":
                    encoder_name = "libx265"
                elif codec == "av1":
                    encoder_name = "libaom-av1"
                elif codec == "vp9":
                    encoder_name = "libvpx-vp9"
                elif codec == "jpegxl":
                    encoder_name = "libjxl"
                elif codec == "heic":
                    encoder_name = "libx265"
                elif codec == "avif":
                    encoder_name = "libaom-av1"
                elif codec == "flac":
                    encoder_name = "flac"
                elif codec == "aac":
                    encoder_name = "aac"
                elif codec == "mp3":
                    encoder_name = "libmp3lame"
                else:
                    encoder_name = "libx264"  # Défaut général
            
            output_cfg.encoder = encoder_name
            output_cfg.container = container
            output_cfg.quality = self.quality_var.get()
            output_cfg.cq_value = self.cq_var.get()
            output_cfg.preset = self.preset_var.get()
            output_cfg.video_mode = self.video_mode_var.get()
            output_cfg.bitrate = self.bitrate_var.get()
            output_cfg.multipass = self.multipass_var.get()

            job = EncodeJob(src_path=p, mode=mode, initial_output_config=output_cfg)

            if keep_structure and input_folder and not input_folder.startswith("(no") and out_root:
                try:
                    input_path_for_rel = Path(input_folder)
                    if hasattr(p, 'is_relative_to') and p.is_relative_to(input_path_for_rel):
                        job.relative_src_path = p.relative_to(input_path_for_rel)
                    else:
                        job.relative_src_path = Path(os.path.relpath(p, input_path_for_rel))
                except (ValueError, AttributeError):
                     job.relative_src_path = Path(p.name)
            elif out_root:
                job.relative_src_path = Path(p.name)
            elif not out_root:
                 job.relative_src_path = Path(p.name)

            self.jobs.append(job)
            job_id = job.job_id # Utiliser l'UUID du job comme identifiant unique

            display_encoder = output_cfg.encoder or "-"
            display_quality = output_cfg.quality or output_cfg.cq_value or output_cfg.bitrate or "-"

            self.tree.insert("", "end", iid=job_id, values=(p.name, display_encoder, display_quality, "0%", "pending", "-"))
            self.job_rows[job_id] = {"job": job}
            current_batch_job_ids.append(job_id)
        
        if current_batch_job_ids:
            self.last_import_job_ids = current_batch_job_ids
            
            # Appliquer les paramètres UI actuels aux nouveaux jobs
            for job_id in current_batch_job_ids:
                job_data = self.job_rows.get(job_id)
                if job_data:
                    job = job_data["job"]
                    self._apply_ui_settings_to_job(job)
                    self._update_job_row(job)

        self._update_job_selector_combobox()
        self._update_control_buttons_state('idle')
        self._update_inspector_file_list()
        if not any(j.status in ["running", "paused"] for j in self.jobs):
            self._update_control_buttons_state("idle")

    def _detect_mode(self, path: Path) -> str:
        ext = path.suffix.lower()
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}
        if ext == ".gif": return "gif"
        if ext in video_exts: return "video"
        if ext in audio_exts: return "audio"
        if ext in image_exts: return "image"
        return "unknown"

    def _open_settings(self):
        SettingsWindow(self.root, self.settings, self._update_codec_choices, self._update_preset_list)

    def _on_double_click(self, event):
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        
        job = next((j for j in self.jobs if j.src_path.name == item_id), None)
        if job:
            JobEditWindow(self.root, job, self.distributed_client)

    def _select_input_folder(self):
        folder = filedialog.askdirectory(title="Sélectionner un dossier d'entrée")
        if folder:
            self.input_folder.set(folder)
            self._auto_import_from_folder(folder)

    def _auto_import_from_folder(self, folder):
        root_path = Path(folder)
        if not root_path.is_dir():
            messagebox.showerror("Invalid Folder", "The selected input folder does not exist.")
            return
            
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv", ".webm", ".flv", ".m4v", ".3gp"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3", ".wma", ".opus", ".ac3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp", ".gif", ".tga", ".dds"}
        
        all_files = [p for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() in (video_exts | audio_exts | image_exts)]
        
        if not all_files:
            messagebox.showinfo("No Media Files Found", f"No media files found in: {folder}")
            return
            
        self._enqueue_paths(all_files)
        dialog_message = f"Imported {len(all_files)} files from:\n{folder}"
        TransientInfoDialog(self.root, "Files Imported", dialog_message, auto_dismiss_ms=7000)

    def _select_output_folder(self):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.output_folder.set(folder)
            if hasattr(self, 'output_folder_entry'):
                self.output_folder_entry.config(foreground="black")
            self._update_control_buttons_state('init')

    def _start_encoding(self):
        if not self.jobs:
            messagebox.showinfo("No Jobs", "There are no jobs in the queue.")
            return

        output_folder_set = self.output_folder.get() and not self.output_folder.get().startswith("(no")
        for job in self.jobs:
            for output_cfg in job.outputs:
                if not output_folder_set and not output_cfg.dst_path:
                    messagebox.showwarning("Output Folder Missing", f"Output folder is not selected for job: {job.src_path.name}.")
                    return

        self._update_control_buttons_state("running")
        self.status_var.set("Encoding in progress...")

        for job in self.jobs:
            if job.get_overall_status() == "pending":
                # Use run_async_func to schedule the job addition
                self.run_async_func(
                    self.job_scheduler.add_job(job, self._on_job_progress, self._on_job_completion),
                    self.loop
                )

    def _on_job_progress(self, progress: JobProgress):
        job = next((j for j in self.jobs if j.job_id == progress.job_id), None)
        if job:
            self.root.after_idle(self._update_job_row, job)
            self.root.after_idle(self._update_overall_progress)

    def _on_job_completion(self, result: JobResult):
        job = next((j for j in self.jobs if j.job_id == result.job_id), None)
        if job:
            job.status = result.status.value
            self.root.after_idle(self._update_job_row, job)
            self.root.after_idle(self._update_overall_progress)

    def _on_all_jobs_finished(self):
        self.is_running = False
        self._update_control_buttons_state("idle")
        self._show_encoding_completion_notification()
        action = self.post_encode_action_var.get()
        if action != "rien":
            if messagebox.askyesno("Post-Encode Action", f"All encodes finished. Execute '{action}'?"):
                self._execute_post_encode_action(action)

    # Méthodes stub pour les fonctionnalités manquantes
    def _batch_operations(self):
        messagebox.showinfo("Non implémenté", "Fonctionnalité en cours de développement")

    def _advanced_filters(self):
        messagebox.showinfo("Non implémenté", "Fonctionnalité en cours de développement")

    def _configure_audio_tracks(self):
        messagebox.showinfo("Non implémenté", "Fonctionnalité en cours de développement")

    def _manage_subtitles(self):
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("No Job Selected", "Please select a job from the queue to manage its subtitles.", parent=self.root)
            return

        item_id = selected_items[0] # Assuming single selection
        job_data = self.job_rows.get(item_id)
        if not job_data or "job" not in job_data:
            messagebox.showerror("Error", "Could not find job data for the selected item.", parent=self.root)
            return

        selected_job = job_data["job"]
        SubtitleManagementWindow(self.root, selected_job, self.settings) # Pass self.settings

    def _clear_queue(self):
        self.jobs.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.job_rows.clear()

    def _reassign_selected_jobs(self):
        """Réassigne les jobs sélectionnés à un serveur spécifique."""
        selected_items = self.tree.selection()
        if not selected_items:
            messagebox.showwarning("Aucune sélection", "Veuillez sélectionner un ou plusieurs jobs dans la file d'attente.")
            return
        
        # Récupérer les jobs correspondants
        selected_jobs = []
        for item_id in selected_items:
            job_data = self.job_rows.get(item_id)
            if job_data and "job" in job_data:
                selected_jobs.append(job_data["job"])
        
        if not selected_jobs:
            messagebox.showerror("Erreur", "Impossible de trouver les jobs sélectionnés.")
            return
        
        # Obtenir la liste des serveurs connectés
        connected_servers = self.distributed_client.get_connected_servers()
        if not connected_servers:
            messagebox.showwarning("Aucun serveur", "Aucun serveur distribué connecté pour la réassignation.")
            return
        
        # Ouvrir la fenêtre de sélection de serveur
        self._open_server_selection_dialog(selected_jobs, connected_servers)
    
    def _open_server_selection_dialog(self, selected_jobs: list, connected_servers: list):
        """Ouvre une fenêtre de dialogue pour sélectionner le serveur de destination."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Réassigner Jobs")
        dialog.geometry("600x500")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Information sur les jobs sélectionnés
        info_frame = ttk.LabelFrame(main_frame, text="Jobs Sélectionnés", padding="5")
        info_frame.pack(fill=tk.X, pady=(0, 10))
        
        job_list = ttk.Label(info_frame, text=f"{len(selected_jobs)} job(s) sélectionné(s):")
        job_list.pack(anchor="w")
        
        for i, job in enumerate(selected_jobs[:3]):  # Afficher max 3 noms
            job_label = ttk.Label(info_frame, text=f"• {job.src_path.name}")
            job_label.pack(anchor="w", padx=(10, 0))
        
        if len(selected_jobs) > 3:
            more_label = ttk.Label(info_frame, text=f"• ... et {len(selected_jobs) - 3} autre(s)")
            more_label.pack(anchor="w", padx=(10, 0))
        
        # Sélection du serveur
        server_frame = ttk.LabelFrame(main_frame, text="Serveur de Destination", padding="5")
        server_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(server_frame, text="Sélectionnez le serveur :").pack(anchor="w", pady=(0, 5))
        
        server_var = tk.StringVar()
        server_info_dict = {}
        
        for server in connected_servers:
            server_name = f"{server.name} ({getattr(server, 'host', getattr(server, 'ip', 'unknown'))}:{server.port})"
            server_info_dict[server_name] = server
        
        server_names = list(server_info_dict.keys())
        server_combo = ttk.Combobox(server_frame, textvariable=server_var, 
                                   values=server_names, state="readonly", width=50)
        server_combo.pack(fill=tk.X, pady=(0, 10))
        if server_names:
            server_var.set(server_names[0])
        
        # Informations du serveur sélectionné
        info_text = tk.Text(server_frame, height=8, wrap=tk.WORD, state=tk.DISABLED)
        info_text.pack(fill=tk.BOTH, expand=True)
        
        def update_server_info(*args):
            """Met à jour les informations du serveur sélectionné."""
            try:
                selected_name = server_var.get()
                if selected_name and selected_name in server_info_dict:
                    server = server_info_dict[selected_name]
                    
                    info_text.config(state=tk.NORMAL)
                    info_text.delete(1.0, tk.END)
                    
                    info_lines = [
                        f"Statut: {getattr(server.status, 'value', 'inconnu')}",
                        f"Jobs actifs: {getattr(server, 'current_jobs', 0)}/{getattr(server, 'max_jobs', '?')}",
                        f"CPU: {getattr(server.capabilities, 'cpu_cores', '?')} cœurs" + 
                              (f" ({server.capabilities.current_load:.1%} charge)" if hasattr(server.capabilities, 'current_load') else ""),
                        f"RAM: {getattr(server.capabilities, 'memory_gb', '?')} GB" if hasattr(server, 'capabilities') else "RAM: ?",
                        "",
                        "Encodeurs logiciels:",
                        f"  {', '.join(server.capabilities.software_encoders) if hasattr(server, 'capabilities') and server.capabilities.software_encoders else 'Aucun'}",
                        "",
                        "Encodeurs matériels:"
                    ]
                    
                    if hasattr(server, 'capabilities') and server.capabilities and server.capabilities.hardware_encoders:
                        for hw_type, encoders in server.capabilities.hardware_encoders.items():
                            if encoders:
                                info_lines.append(f"  {hw_type.upper()}: {', '.join(encoders)}")
                    else:
                        info_lines.append("  Aucun")
                    
                    info_text.insert(1.0, "\n".join(info_lines))
                    info_text.config(state=tk.DISABLED)
                else:
                    info_text.config(state=tk.NORMAL)
                    info_text.delete(1.0, tk.END)
                    info_text.insert(1.0, "Aucun serveur sélectionné")
                    info_text.config(state=tk.DISABLED)
            except Exception as e:
                # Protection contre les erreurs lors de l'accès aux propriétés du serveur
                try:
                    info_text.config(state=tk.NORMAL)
                    info_text.delete(1.0, tk.END)
                    info_text.insert(1.0, f"Erreur lors de l'affichage: {e}")
                    info_text.config(state=tk.DISABLED)
                except:
                    pass  # Si même ça échoue, on ignore
        
        server_combo.bind('<<ComboboxSelected>>', lambda e: update_server_info())
        update_server_info()  # Mise à jour initiale
        
        # Boutons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        def confirm_reassignment():
            selected_server_name = server_var.get()
            if not selected_server_name:
                messagebox.showwarning("Erreur", "Veuillez sélectionner un serveur.")
                return
            
            target_server = server_info_dict[selected_server_name]
            
            # Vérifier la compatibilité avant réassignation
            incompatible_jobs = []
            for job in selected_jobs:
                if not self._check_job_server_compatibility(job, target_server):
                    incompatible_jobs.append(job.src_path.name)
            
            if incompatible_jobs:
                warning_msg = f"Attention : {len(incompatible_jobs)} job(s) peuvent être incompatibles :\n"
                warning_msg += "\n".join(f"• {name}" for name in incompatible_jobs[:5])
                if len(incompatible_jobs) > 5:
                    warning_msg += f"\n• ... et {len(incompatible_jobs) - 5} autre(s)"
                warning_msg += "\n\nContinuer quand même ?"
                
                if not messagebox.askyesno("Compatibilité", warning_msg):
                    return
            
            # Effectuer la réassignation
            self.run_async_func(self._perform_jobs_reassignment(selected_jobs, target_server), self.loop)
            dialog.destroy()
        
        ttk.Button(button_frame, text="Réassigner", command=confirm_reassignment).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(button_frame, text="Annuler", command=dialog.destroy).pack(side=tk.RIGHT)
        
        # Donner le focus à la combobox
        dialog.after(100, lambda: server_combo.focus_set())
    
    def _check_job_server_compatibility(self, job, server) -> bool:
        """Vérifie si un job est compatible avec un serveur."""
        # Récupérer l'encodeur du premier output du job
        if not job.outputs:
            return True  # Pas d'output configuré, on assume compatible
        
        encoder = job.outputs[0].encoder
        if not encoder:
            return True  # Pas d'encodeur spécifié
        
        # Vérifier si l'encodeur est disponible sur le serveur
        all_server_encoders = (
            server.capabilities.software_encoders +
            [enc for encoders in server.capabilities.hardware_encoders.values() for enc in encoders]
        )
        
        return encoder in all_server_encoders
    
    async def _perform_jobs_reassignment(self, jobs: list, target_server):
        """Effectue la réassignation asynchrone des jobs vers le serveur cible."""
        success_count = 0
        error_count = 0
        
        for job in jobs:
            try:
                # Convertir le job en JobConfiguration pour le système distribué
                job_config = self._convert_job_to_distributed_config(job, target_server)
                
                # Soumettre le job au serveur spécifié
                success = await self.distributed_client.send_job_to_server(
                    target_server.server_id, 
                    job_config,
                    lambda progress: self._on_job_progress(progress),
                    lambda result: self._on_job_completion(result)
                )
                
                if success:
                    success_count += 1
                    # Mettre à jour l'affichage du job
                    self._update_job_status_display(job, f"Assigné à {target_server.name}")
                else:
                    error_count += 1
                    
            except Exception as e:
                self.logger.error(f"Erreur lors de la réassignation du job {job.src_path.name}: {e}")
                error_count += 1
        
        # Afficher le résultat
        if success_count > 0:
            message = f"✅ {success_count} job(s) réassigné(s) avec succès à {target_server.name}"
            if error_count > 0:
                message += f"\n❌ {error_count} job(s) en erreur"
            messagebox.showinfo("Réassignation Terminée", message)
        else:
            messagebox.showerror("Erreur", f"Impossible de réassigner les jobs à {target_server.name}")
    
    def _convert_job_to_distributed_config(self, job, target_server):
        """Convertit un EncodeJob local en JobConfiguration pour le système distribué."""
        from shared.messages import JobConfiguration, EncoderType
        
        # Utiliser le premier output comme référence
        output_cfg = job.outputs[0] if job.outputs else None
        
        return JobConfiguration(
            job_id=str(uuid.uuid4()),
            input_file=str(job.src_path),
            output_file=str(output_cfg.dst_path) if output_cfg else str(job.src_path.with_suffix('.out')),
            encoder=output_cfg.encoder if output_cfg else 'libx264',
            encoder_type=EncoderType.SOFTWARE,  # Sera déterminé côté serveur
            preset=output_cfg.preset if output_cfg else None,
            quality_mode=output_cfg.video_mode if output_cfg else 'quality',
            quality_value=output_cfg.quality if output_cfg else '22',
            filters=[],
            ffmpeg_args=[],
            required_capabilities=[],
            priority=5,
            estimated_duration=None,
            file_size=job.src_path.stat().st_size if job.src_path.exists() else 0,
            resolution="1920x1080",  # Valeur par défaut
            codec=output_cfg.encoder.split('_')[0] if output_cfg and output_cfg.encoder else 'h264',
            container=output_cfg.container if output_cfg else 'mp4'
        )
    
    def _update_job_status_display(self, job, status_text: str):
        """Met à jour l'affichage du statut d'un job dans la TreeView."""
        job_id = job.job_id
        if job_id in self.tree.get_children():
            current_values = list(self.tree.item(job_id)['values'])
            if len(current_values) >= 5:
                current_values[4] = status_text  # Colonne statut
                self.tree.item(job_id, values=current_values)

    def _save_preset(self):
        preset_name = simpledialog.askstring("Sauvegarder le Préréglage", "Nom du préréglage:", parent=self.root)
        if not preset_name or not preset_name.strip():
            return

        preset_data = {
            "media_type": self.global_type_var.get(),
            "container": self.container_var.get(),
            "codec": self.global_codec_var.get(),
            "encoder": self._get_encoder_name_from_display(self.global_encoder_var.get()),
            "video_mode": self.video_mode_var.get(),
            "quality_or_cq": self.quality_var.get(),
            "bitrate": self.bitrate_var.get(),
            "multipass": self.multipass_var.get(),
            "preset": self.preset_var.get(),
            "resolution": self.resolution_var_settings.get(),
            "crop": {
                "top": self.crop_top_var.get(),
                "bottom": self.crop_bottom_var.get(),
                "left": self.crop_left_var.get(),
                "right": self.crop_right_var.get()
            },
            "preserve_hdr": self.preserve_hdr_var.get(),
            "tonemap": self.tonemap_var.get(),
            "tonemap_method": self.tonemap_method_var.get(),
            "subtitle_mode": self.subtitle_mode_var.get(),
            "lut_path": self.lut_path_var.get(),
            "watermark_path": self.watermark_path_var.get(),
            "watermark_position": self.watermark_position_var.get(),
            "watermark_scale": self.watermark_scale_var.get(),
            "watermark_opacity": self.watermark_opacity_var.get(),
            "watermark_padding": self.watermark_padding_var.get(),
        }

        if "presets" not in self.settings.data:
            self.settings.data["presets"] = {}
        
        self.settings.data["presets"][preset_name] = preset_data
        self.settings.save()
        
        self._update_preset_list()
        messagebox.showinfo("Succès", f"Préréglage '{preset_name}' sauvegardé.")

    def _load_preset_by_name(self, name):
        preset_data = self.settings.data.get("presets", {}).get(name)
        if not preset_data:
            messagebox.showerror("Erreur", f"Préréglage '{name}' non trouvé.")
            return

        # Appliquer les valeurs du préréglage à l'UI
        self.global_type_var.set(preset_data.get("media_type", "video"))
        self._update_media_type_ui(self.global_type_var.get())
        
        self.container_var.set(preset_data.get("container", ""))
        self.global_codec_var.set(preset_data.get("codec", ""))
        
        # Mettre à jour les choix de codec AVANT de définir l'encodeur
        self._update_codec_choices()

        def apply_remaining_preset():
            encoder_name = preset_data.get("encoder", "")
            encoder_display = ""
            # On cherche la description complète de l'encodeur pour l'afficher
            for item in self.global_encoder_combo['values']:
                if item.startswith(encoder_name):
                    encoder_display = item
                    break
            self.global_encoder_var.set(encoder_display or encoder_name)

            self.video_mode_var.set(preset_data.get("video_mode", "quality"))
            self.quality_var.set(str(preset_data.get("quality_or_cq", "22")))
            self.bitrate_var.set(str(preset_data.get("bitrate", "4000")))
            self.multipass_var.set(preset_data.get("multipass", False))
            self.preset_var.set(preset_data.get("preset", "medium"))

            self.resolution_var_settings.set(preset_data.get("resolution", "Keep Original"))
            crop_settings = preset_data.get("crop", {})
            self.crop_top_var.set(crop_settings.get("top", "0"))
            self.crop_bottom_var.set(crop_settings.get("bottom", "0"))
            self.crop_left_var.set(crop_settings.get("left", "0"))
            self.crop_right_var.set(crop_settings.get("right", "0"))
            
            self.preserve_hdr_var.set(preset_data.get("preserve_hdr", True))
            self.tonemap_var.set(preset_data.get("tonemap", False))
            self.tonemap_method_var.set(preset_data.get("tonemap_method", "hable"))

            self._on_video_mode_change()
            self._on_tonemap_change()
            self._update_quality_preset_controls()

        # On attend un court instant pour s'assurer que l'UI a mis à jour les listes
        self.root.after(100, apply_remaining_preset)
        
        messagebox.showinfo("Préréglage chargé", f"Le préréglage '{name}' a été chargé.")

    def _show_log_viewer(self):
        messagebox.showinfo("Non implémenté", "Fonctionnalité en cours de développement")

    def _toggle_watch(self):
        messagebox.showinfo("Non implémenté", "Surveillance de dossier en cours de développement")

    def _update_control_buttons_state(self, state):
        has_jobs = bool(self.jobs)
        if state == "running":
            self.start_btn.config(state="disabled")
            self.pause_btn.config(state="normal")
            self.resume_btn.config(state="disabled")
            self.cancel_btn.config(state="normal")
        elif state == "paused":
            self.start_btn.config(state="disabled")
            self.pause_btn.config(state="disabled")
            self.resume_btn.config(state="normal")
            self.cancel_btn.config(state="normal")
        else: # idle, init, or finished
            self.start_btn.config(state="normal" if has_jobs else "disabled")
            self.pause_btn.config(state="disabled")
            self.resume_btn.config(state="disabled")
            self.cancel_btn.config(state="disabled")

    def _update_preset_list(self):
        #je dois aussi mettre à jour le menu des préréglages
        preset_menu = self.menubar.winfo_children()[2] # Attention, c'est fragile
        preset_menu.delete(2, tk.END) # Supprimer les anciens préréglages
        
        presets = list(self.settings.data.get("presets", {}).keys())
        self.preset_combo['values'] = presets
        self.watch_preset_combo['values'] = presets
        
        for preset_name in presets:
            preset_menu.add_command(label=preset_name, command=lambda name=preset_name: self._load_preset_by_name(name))

    def _update_codec_choices(self):
        media_type = self.global_type_var.get()
        all_codecs = FFmpegHelpers.available_codecs()
        
        codecs_for_type = all_codecs.get(media_type, [])
        self.global_codec_combo['values'] = codecs_for_type
        
        if codecs_for_type:
            # Si aucun codec n'est sélectionné ou si le codec actuel n'est pas compatible
            current_codec = self.global_codec_var.get()
            if not current_codec or current_codec not in codecs_for_type:
                self.global_codec_var.set(codecs_for_type[0])
        else:
            self.global_codec_var.set("")
            
        # Mettre à jour les encodeurs après avoir défini le codec
        self._update_encoder_choices()

    def _update_media_type_ui(self, media_type):
        is_video = media_type == "video"
        is_audio = media_type == "audio"
        is_image = media_type == "image"

        # Cacher ou montrer les sections en fonction du type
        if is_video:
            self.transform_frame.pack(fill=tk.X, pady=(0, 5))
            self.quality_frame.pack(fill=tk.X, pady=(0, 5))
            self.hdr_frame.pack(fill=tk.X, pady=(0, 5))
            self.subtitle_frame.pack(fill=tk.X, pady=(0, 5))
            self.lut_frame.pack(fill=tk.X, pady=(0, 5))
            self.codec_label.config(text="Codec Vidéo:")
        elif is_audio:
            self.transform_frame.pack_forget()
            self.quality_frame.pack_forget()
            self.hdr_frame.pack_forget()
            self.subtitle_frame.pack_forget()
            self.lut_frame.pack_forget()
            self.codec_label.config(text="Codec Audio:")
        elif is_image:
            self.transform_frame.pack_forget()
            self.quality_frame.pack_forget()
            self.hdr_frame.pack_forget()
            self.subtitle_frame.pack_forget()
            self.lut_frame.pack_forget()
            self.codec_label.config(text="Codec Image:")
        
        self.root.after(100, self._update_scroll_state)

    def _update_job_selector_combobox(self):
        job_names = [job.src_path.name for job in self.jobs]
        self.job_selector_combobox['values'] = job_names
        if job_names:
            self.job_selector_combobox.set(job_names[-1])
            self._load_settings_from_selected_job()

    def _update_inspector_file_list(self):
        self.inspector_tree.delete(*self.inspector_tree.get_children())
        for job in self.jobs:
            self.inspector_tree.insert("", "end", iid=job.job_id, values=(job.src_path.name,))

    def _update_job_row(self, job: EncodeJob):
        job_id = job.job_id
        if job_id in self.tree.get_children():
            status = job.get_overall_status()
            progress = job.get_overall_progress()
            
            # Utiliser le premier output pour l'affichage (simplification)
            output_cfg = job.outputs[0]
            encoder = output_cfg.encoder or "N/A"
            quality_val = ""
            if output_cfg.video_mode == "quality":
                quality_val = f"CQ {output_cfg.quality}"
            elif output_cfg.video_mode == "bitrate":
                quality_val = f"{output_cfg.bitrate} kbps"

            self.tree.item(job_id, values=(
                job.src_path.name,
                encoder,
                quality_val,
                f"{progress:.1f}%",
                status,
                self.server_map.get(job.assigned_to, "En attente...") if job.assigned_to else "En attente..."
            ))

    def _update_overall_progress(self):
        total_progress = 0
        active_jobs = [j for j in self.jobs if j.get_overall_status() not in ("pending", "finished", "failed")]
        if not active_jobs:
            self.progress_bar['value'] = 0
            return
            
        for job in active_jobs:
            total_progress += job.get_overall_progress()
            
        overall_percentage = total_progress / len(active_jobs)
        self.progress_bar['value'] = overall_percentage

    def _show_encoding_completion_notification(self):
        messagebox.showinfo("Terminé", "Tous les encodages sont terminés !")

    def _execute_post_encode_action(self, action):
        if action == "shutdown":
            if messagebox.askyesno("Éteindre", "Voulez-vous vraiment éteindre l'ordinateur ?"):
                if sys.platform == "win32":
                    os.system("shutdown /s /t 1")
                elif sys.platform == "darwin":
                    os.system("sudo shutdown -h now")
                else: # linux
                    os.system("sudo shutdown -h now")
        elif action == "sleep":
             if sys.platform == "win32":
                os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
             elif sys.platform == "darwin":
                os.system("pmset sleepnow")
             else: # linux
                os.system("systemctl suspend")

    def _get_container_from_display(self, display):
        return display.split(" ")[0].lower() if display else ""

    def _get_encoder_name_from_display(self, display):
        return display.split(" ")[0] if display else ""

    def _setup_drag_drop(self):
        if DND_AVAILABLE:
            try:
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind('<<Drop>>', self._on_drop)
            except Exception as e:
                logging.error(f"Failed to set up drag and drop: {e}")

    def _on_drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        file_paths = [Path(p) for p in paths]
        self._enqueue_paths(file_paths)

    # Méthodes manquantes de _build_encoding_section
    def _on_job_selected_for_settings_change(self, event=None):
        self._load_settings_from_selected_job()

    def _apply_ui_settings_to_selected_job_via_combobox(self):
        selected_job_name = self.selected_job_for_settings_var.get()
        if not selected_job_name:
            messagebox.showwarning("Aucun Job", "Veuillez sélectionner un job dans la liste déroulante.")
            return

        target_job = next((j for j in self.jobs if j.src_path.name == selected_job_name), None)
        if not target_job:
            messagebox.showerror("Erreur", "Job non trouvé.")
            return
        
        self._apply_ui_settings_to_job(target_job)
        self._update_job_row(target_job)
        messagebox.showinfo("Appliqué", f"Les paramètres ont été appliqués à {target_job.src_path.name}")

    def _apply_ui_settings_to_last_import_batch(self):
        if not self.last_import_job_ids:
            messagebox.showwarning("Aucun import récent", "Aucun import récent à modifier.")
            return

        if not messagebox.askyesno("Confirmer", f"Appliquer les paramètres actuels aux {len(self.last_import_job_ids)} derniers fichiers importés ?"):
            return

        for job_id in self.last_import_job_ids:
            job_data = self.job_rows.get(job_id)
            if job_data:
                job = job_data["job"]
                self._apply_ui_settings_to_job(job)
                self._update_job_row(job)
        
        messagebox.showinfo("Succès", f"Paramètres appliqués à {len(self.last_import_job_ids)} jobs.")

    def _on_subtitle_mode_change(self, event=None):
        mode = self.subtitle_mode_var.get()
        show_external_frame = mode in ["burn", "embed"]
        
        if show_external_frame:
            self.external_subtitle_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=2)
        else:
            self.external_subtitle_frame.grid_forget()

    def _browse_subtitle_file(self):
        filename = filedialog.askopenfilename(
            title="Sélectionner un fichier de sous-titres",
            filetypes=[("Sous-titres", "*.srt *.ass *.ssa *.vtt *.sub"), ("Tous les fichiers", "*.*")]
        )
        if filename:
            self.subtitle_path_var.set(filename)

    def _browse_lut_file(self):
        filename = filedialog.askopenfilename(
            title="Sélectionner un fichier LUT",
            filetypes=[("Fichiers LUT", "*.cube *.look *.3dl"), ("Tous les fichiers", "*.*")]
        )
        if filename:
            self.lut_path_var.set(filename)

    def _browse_watermark_file(self):
        filename = filedialog.askopenfilename(
            title="Sélectionner un watermark",
            filetypes=[("Images PNG", "*.png"), ("Tous les fichiers", "*.*")]
        )
        if filename:
            self.watermark_path_var.set(filename)

    def _on_right_click(self, event):
        """Menu contextuel pour le click droit sur la liste des jobs"""
        pass

    def _on_queue_selection_change(self, event):
        """Appelée quand la sélection change dans la liste des jobs"""
        pass

    def _pause_all(self):
        """Pause tous les jobs en cours"""
        messagebox.showinfo("Non implémenté", "Pause des jobs en cours de développement")

    def _resume_all(self):
        """Reprend tous les jobs en pause"""
        messagebox.showinfo("Non implémenté", "Reprise des jobs en cours de développement")

    def _stop_all(self):
        """Arrête tous les jobs en cours"""
        messagebox.showinfo("Non implémenté", "Arrêt des jobs en cours de développement")

    def _apply_ui_settings_to_job(self, job):
        """Applique les paramètres de l'UI à un job"""
        if not job.outputs:
            # Créer un OutputConfig par défaut si aucun n'existe
            from core.encode_job import OutputConfig
            output_name = f"{job.src_path.stem} - Default"
            dst_path = job.src_path.with_suffix(f".{self.container_var.get()}")
            output_cfg = OutputConfig(output_name, dst_path, job.mode)
            job.outputs.append(output_cfg)
        
        # Appliquer les paramètres UI au premier output
        output_cfg = job.outputs[0]
        
        # Mettre à jour le mode de job si nécessaire
        job.mode = self.global_type_var.get()
        output_cfg.mode = job.mode
        
        # Encodeur et codec
        encoder_display = self.global_encoder_var.get()
        output_cfg.encoder = self._get_encoder_name_from_display(encoder_display)
        
        # Container et chemin de destination
        container = self.container_var.get()
        output_cfg.container = container
        if container:
            output_cfg.dst_path = job.src_path.with_suffix(f".{container}")
        
        # Paramètres de qualité
        output_cfg.video_mode = self.video_mode_var.get()
        output_cfg.quality = self.quality_var.get()
        output_cfg.bitrate = self.bitrate_var.get()
        output_cfg.multipass = self.multipass_var.get()
        output_cfg.preset = self.preset_var.get()
        
        # Paramètres personnalisés
        output_cfg.custom_flags = self.custom_flags_var.get()

    def _cancel_all(self):
        """Annule tous les jobs en cours"""
        messagebox.showinfo("Non implémenté", "Annulation des jobs en cours de développement")

    def _edit_selected_job(self):
        """Modifier le job sélectionné"""
        messagebox.showinfo("Non implémenté", "Modification des jobs en cours de développement")

    def _duplicate_selected(self):
        """Dupliquer le job sélectionné"""
        messagebox.showinfo("Non implémenté", "Duplication des jobs en cours de développement")

    def _pause_selected_job(self):
        """Pause le job sélectionné"""
        messagebox.showinfo("Non implémenté", "Pause du job en cours de développement")

    def _resume_selected_job(self):
        """Reprend le job sélectionné"""
        messagebox.showinfo("Non implémenté", "Reprise du job en cours de développement")

    def _cancel_selected_job(self):
        """Annule le job sélectionné"""
        messagebox.showinfo("Non implémenté", "Annulation du job en cours de développement")

    def _remove_selected_job(self):
        """Supprime le job sélectionné"""
        selected_items = self.tree.selection()
        if not selected_items:
            return
        
        if messagebox.askyesno("Confirmer", "Supprimer les jobs sélectionnés de la file d'attente ?"):
            for item_id in selected_items:
                self.tree.delete(item_id)
                job_to_remove = self.job_rows.pop(item_id, {}).get("job")
                if job_to_remove:
                    self.jobs.remove(job_to_remove)
            self._update_job_selector_combobox()
            self._update_inspector_file_list()

    def _on_inspector_selection_change(self, event):
        """Appelée quand la sélection change dans l'inspecteur"""
        selected_items = self.inspector_tree.selection()
        if not selected_items:
            return
        
        job_id = selected_items[0]
        job = next((j for j in self.jobs if j.job_id == job_id), None)
        
        if job:
            self._display_job_info_in_inspector(job)

    def _display_job_info_in_inspector(self, job: EncodeJob):
        # Nettoyer l'ancien contenu
        for widget in self.inspector_info_frame.winfo_children():
            widget.destroy()
            
        # Afficher les nouvelles informations
        info = job.get_media_info()
        
        row = 0
        if info:
            for key, value in info.items():
                ttk.Label(self.inspector_info_frame, text=f"{key.replace('_', ' ').title()}:", font=("Helvetica", 10, "bold")).grid(row=row, column=0, sticky="w", padx=5, pady=2)
                ttk.Label(self.inspector_info_frame, text=str(value), wraplength=400).grid(row=row, column=1, sticky="w", padx=5, pady=2)
                row += 1
        else:
            ttk.Label(self.inspector_info_frame, text="Informations média non disponibles.").grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=2)

    def _load_settings_from_selected_job(self):
        """Charge les paramètres d'un job sélectionné dans l'UI."""
        job_name = self.selected_job_for_settings_var.get()
        if not job_name:
            return

        job = next((j for j in self.jobs if j.src_path.name == job_name), None)
        if not job:
            return
            
        output_cfg = job.outputs[0] # On prend la première config de sortie
        
        self.global_type_var.set(job.mode)
        self._update_media_type_ui(job.mode)

        # Utiliser l'attribut correct pour le codec - vérifier s'il existe
        codec_value = getattr(output_cfg, 'codec', '') or output_cfg.encoder.split('_')[0] if hasattr(output_cfg, 'encoder') and output_cfg.encoder else ''
        self.global_codec_var.set(codec_value)
        self._update_codec_choices()

        # Trouver le display name de l'encodeur
        encoder_display = ""
        encoder_name = getattr(output_cfg, 'encoder', '')
        if encoder_name:
            for item in self.global_encoder_combo['values']:
                if item.startswith(encoder_name):
                    encoder_display = item
                    break
            self.global_encoder_var.set(encoder_display or encoder_name)
        self._update_encoder_choices()
        
        self.container_var.set(getattr(output_cfg, 'container', ''))
        self.video_mode_var.set(getattr(output_cfg, 'video_mode', 'quality'))
        self.quality_var.set(getattr(output_cfg, 'quality', '') or getattr(output_cfg, 'cq_value', ''))
        self.bitrate_var.set(getattr(output_cfg, 'bitrate', ''))
        self.multipass_var.set(getattr(output_cfg, 'multipass', False))
        self.preset_var.set(getattr(output_cfg, 'preset', ''))
        
        extra_params = getattr(output_cfg, 'extra_params', [])
        self.custom_flags_var.set(" ".join(extra_params) if extra_params else "")

        # Mettre à jour les contrôles
        self._on_video_mode_change()

    def _load_preset(self, event=None):
        preset_name = self.preset_name_var.get()
        if not preset_name:
            return
        self._load_preset_by_name(preset_name)

    def _on_resolution_change(self, event=None):
        """Affiche ou cache les champs de résolution personnalisée selon le choix."""
        if self.resolution_var_settings.get() == "Custom":
            self.custom_resolution_frame.grid(row=0, column=4, padx=(10, 0))
            self.width_entry.pack(side='left')
            self.height_entry.pack(side='left', padx=(5,0))
        else:
            self.custom_resolution_frame.grid_forget()

    def _on_video_mode_change(self, event=None):
        """Active ou désactive les champs selon que le mode est CQ (qualité) ou bitrate."""
        is_quality_mode = self.video_mode_var.get() == "quality"
        self.cq_entry.config(state="normal" if is_quality_mode else "disabled")
        self.bitrate_entry.config(state="disabled" if is_quality_mode else "normal")
        self.multipass_check.config(state="disabled" if is_quality_mode else "normal")
        # Mettre à jour les étiquettes/valeurs liées si nécessaire

    def _update_container_choices(self):
        """Met à jour la liste des conteneurs compatibles avec le type de média sélectionné."""
        media_type = self.global_type_var.get()
        if media_type == "video":
            containers = ["mp4", "mkv", "mov", "webm"]
        elif media_type == "audio":
            containers = ["m4a", "mp3", "opus", "flac", "ogg"]
        elif media_type == "image":
            containers = ["png", "jpg", "webp", "avif", "jxl", "heic"]
        else:
            containers = []
        
        self.container_combo['values'] = containers
        # Toujours définir le premier conteneur de la liste pour ce type de média
        if containers:
            current_container = self.container_var.get()
            if not current_container or current_container not in containers:
                self.container_var.set(containers[0])

    def _update_quality_preset_controls(self):
        """Met à jour la liste des presets d'encodeur disponibles en fonction de l'encodeur choisi."""
        encoder_name = self._get_encoder_name_from_display(self.global_encoder_var.get())
        presets = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
        
        # Certains encodeurs disposent de préréglages différents
        if "qsv" in encoder_name or "nvenc" in encoder_name:
            presets = ["default", "slow", "medium", "fast", "hp", "hq", "ll", "llhq", "llhp", "lossless"]
        elif "amf" in encoder_name:
            presets = ["balanced", "speed", "quality"]
        
        self.quality_entry['values'] = presets
        if self.preset_var.get() not in presets:
            self.preset_var.set(presets[0] if presets else "")

    def _update_encoder_choices(self):
        """Met à jour la liste des encodeurs compatibles avec le codec sélectionné et ajoute les encodeurs matériels distants."""
        selected_codec = self.global_codec_var.get()
        if not selected_codec:
            self.global_encoder_combo['values'] = []
            self.global_encoder_var.set("")
            return

        # 1. Encodeurs locaux compatibles
        local_encoders = FFmpegHelpers.available_encoders()
        compatible_local = [
            f"{enc['name']} - {enc['description']}" for enc in local_encoders if enc['codec'] == selected_codec
        ]
        
        # Ajouter des encodeurs par défaut si aucun n'est trouvé pour certains codecs
        if not compatible_local:
            fallback_encoders = {
                'webp': 'libwebp - WebP encoder',
                'jpegxl': 'libjxl - JPEG XL encoder', 
                'heic': 'libx265 - HEIC encoder',
                'avif': 'libaom-av1 - AVIF encoder',
                'png': 'png - PNG encoder',
                'jpeg': 'mjpeg - JPEG encoder',
                'h264': 'libx264 - H.264 encoder',
                'hevc': 'libx265 - H.265/HEVC encoder',
                'aac': 'aac - AAC encoder',
                'flac': 'flac - FLAC encoder',
                'mp3': 'libmp3lame - MP3 encoder'
            }
            if selected_codec in fallback_encoders:
                compatible_local = [fallback_encoders[selected_codec]]

        # 2. Encodeurs matériels distants
        remote_encoders: list[str] = []
        connected_servers = self.distributed_client.get_connected_servers()
        for server in connected_servers:
            if getattr(server, 'status', None) == 'online':
                hw_list = server.capabilities.hardware_encoders.get('all', []) if server.capabilities else []
                for hw in hw_list:
                    if selected_codec in hw or (selected_codec == 'hevc' and '265' in hw):
                        remote_encoders.append(f"{server.name}: {hw}")

        all_encoders = compatible_local + remote_encoders
        self.global_encoder_combo['values'] = all_encoders

        if all_encoders:
            current = self.global_encoder_var.get()
            if current not in all_encoders:
                self.global_encoder_var.set(all_encoders[0])
        else:
            self.global_encoder_var.set("")

        # Mettre à jour les dépendances
        self._update_container_choices()
        self._update_quality_preset_controls()

    def _update_quality_controls_for_global(self):
        """Met à jour les contrôles de qualité en fonction du type de média et de l'encodeur sélectionné."""
        media_type = self.global_type_var.get()
        encoder = self._get_encoder_name_from_display(self.global_encoder_var.get())
        codec = self.global_codec_var.get()
        
        # Mise à jour des labels et plages selon le codec/encodeur
        optimal_quality = self._get_optimal_quality_for_codec(encoder, media_type)
        
        if media_type == "image":
            if 'webp' in encoder.lower():
                # WebP: qualité 0-100 (100 = lossless)
                self.video_mode_radio_quality.config(text="Qualité WebP (0-100, 100=lossless)")
                self.quality_var.set(optimal_quality)
                self.cq_entry.config(state="normal")
                self.video_mode_radio_bitrate.config(state="disabled")
                self.bitrate_entry.config(state="disabled")
            elif 'avif' in encoder.lower():
                # AVIF: CRF 0-63
                self.video_mode_radio_quality.config(text="Qualité AVIF (CRF 0-63)")
                self.quality_var.set(optimal_quality)
                self.cq_entry.config(state="normal")
            elif 'jpeg' in encoder.lower():
                # JPEG: qualité 1-100
                self.video_mode_radio_quality.config(text="Qualité JPEG (1-100)")
                self.quality_var.set(optimal_quality)
                self.cq_entry.config(state="normal")
            elif 'jpegxl' in encoder.lower() or 'jxl' in encoder.lower():
                # JPEGXL: distance 0.0-15.0 (0.0=lossless, 1.0=très bonne qualité)
                self.video_mode_radio_quality.config(text="Distance JPEGXL (0.0-15.0, 0.0=lossless)")
                self.quality_var.set(optimal_quality)
                self.cq_entry.config(state="normal")
                self.video_mode_radio_bitrate.config(state="disabled")
                self.bitrate_entry.config(state="disabled")
            elif 'heic' in encoder.lower():
                # HEIC: CRF 0-51 (comme HEVC)
                self.video_mode_radio_quality.config(text="Qualité HEIC (CRF 0-51)")
                self.quality_var.set(optimal_quality)
                self.cq_entry.config(state="normal")
                self.video_mode_radio_bitrate.config(state="disabled")
                self.bitrate_entry.config(state="disabled")
            else:
                # PNG et autres : pas de contrôle qualité
                self.video_mode_radio_quality.config(text="Qualité (N/A pour PNG)")
                self.cq_entry.config(state="disabled")
                self.video_mode_radio_bitrate.config(state="disabled")
                
        elif media_type == "audio":
            if 'flac' in encoder.lower():
                # FLAC: niveau compression 0-12
                self.video_mode_radio_quality.config(text="Niveau Compression FLAC (0-12)")
                self.quality_var.set(optimal_quality)
                self.cq_entry.config(state="normal")
                self.video_mode_radio_bitrate.config(state="disabled")
                self.bitrate_entry.config(state="disabled")
            elif 'alac' in encoder.lower() or 'wav' in encoder.lower():
                # Codecs lossless : pas de contrôle qualité
                self.video_mode_radio_quality.config(text="Lossless (pas de paramètres)")
                self.cq_entry.config(state="disabled")
                self.video_mode_radio_bitrate.config(state="disabled")
            else:
                # AAC, MP3, Opus : bitrate
                self.video_mode_radio_quality.config(text="Qualité Variable (VBR)")
                self.video_mode_radio_bitrate.config(text="Bitrate Constant (CBR)")
                self.video_mode_radio_bitrate.config(state="normal")
                self.bitrate_var.set(optimal_quality)
                
        elif media_type == "video":
            # Conserver le comportement existant pour la vidéo
            if 'nvenc' in encoder or 'qsv' in encoder or 'amf' in encoder or 'videotoolbox' in encoder:
                self.video_mode_radio_quality.config(text="Qualité Constante (CQ)")
            else:
                self.video_mode_radio_quality.config(text="Qualité Constante (CRF)")
            self.quality_var.set(optimal_quality)
                
        # Déclencher la mise à jour de l'état des contrôles
        self._on_video_mode_change()

    def _get_optimal_quality_for_codec(self, encoder: str, media_type: str) -> str:
        """Retourne une valeur de qualité optimale par défaut pour un encodeur donné."""
        encoder_lower = encoder.lower()
        
        if media_type == "video":
            if 'x264' in encoder_lower or 'x265' in encoder_lower:
                return "22"  # CRF optimal pour x264/x265
            elif 'nvenc' in encoder_lower:
                return "20"  # CQ optimal pour NVENC
            elif 'qsv' in encoder_lower:
                return "23"  # CQ optimal pour QuickSync
            elif 'av1' in encoder_lower:
                return "25"  # CRF optimal pour AV1
            elif 'vp9' in encoder_lower:
                return "28"  # CRF optimal pour VP9
            return "22"  # Défaut général
            
        elif media_type == "audio":
            if 'flac' in encoder_lower:
                return "8"   # Niveau compression FLAC optimal (équilibre vitesse/taille)
            elif 'aac' in encoder_lower:
                return "128"  # Bitrate AAC optimal
            elif 'mp3' in encoder_lower:
                return "192"  # Bitrate MP3 optimal
            elif 'opus' in encoder_lower:
                return "128"  # Bitrate Opus optimal
            return "128"  # Défaut général
            
        elif media_type == "image":
            if 'webp' in encoder_lower:
                return "90"   # Qualité WebP très bonne sans être lossless
            elif 'avif' in encoder_lower:
                return "23"   # CRF AVIF optimal
            elif 'jpeg' in encoder_lower:
                return "85"   # Qualité JPEG très bonne
            elif 'jpegxl' in encoder_lower or 'jxl' in encoder_lower:
                return "1.0"  # Distance JPEGXL très bonne qualité
            elif 'heic' in encoder_lower:
                return "23"   # CRF HEIC optimal (comme HEVC)
            return "90"   # Défaut général
            
        return "22"  # Défaut global
