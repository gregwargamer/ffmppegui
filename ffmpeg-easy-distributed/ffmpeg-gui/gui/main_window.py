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
    def __init__(self, root, distributed_client: DistributedClient, server_discovery: ServerDiscovery, job_scheduler: JobScheduler):
        self.root = root
        self.root.title("FFmpeg Frontend")
        self.root.geometry("1200x800")
        
        self.distributed_client = distributed_client
        self.server_discovery = server_discovery
        self.job_scheduler = job_scheduler
        self.settings = load_settings()

        self.server_discovery.register_server_update_callback(self.update_server_status)

        self.jobs: list[EncodeJob] = []
        self.job_rows = {}
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
        self._update_preset_list()
        self._create_status_bar()
        
        try:
            self._update_codec_choices()
            self._update_media_type_ui(self.global_type_var.get())
        except Exception:
            pass
        
        self.job_scheduler.register_progress_callback(self._on_job_progress)
        self.job_scheduler.register_completion_callback(self._on_job_completion)
        self.job_scheduler.register_all_jobs_finished_callback(self._on_all_jobs_finished)

        # Initialiser les variables manquantes
        self.global_type_var = StringVar(value="unknown")
        self.global_encoder_var = StringVar()
        self.quality_var = StringVar()
        self.preset_var = StringVar()
        self.video_mode_var = StringVar(value="quality")
        self.bitrate_var = StringVar()
        self.multipass_var = BooleanVar()
        self.container_var = StringVar()
        self.selected_job_for_settings_var = StringVar()
        self.preset_name_var = StringVar()
        self.resolution_var_settings = StringVar()
        self.crop_top_var = StringVar(value="0")
        self.crop_bottom_var = StringVar(value="0") 
        self.crop_left_var = StringVar(value="0")
        self.crop_right_var = StringVar(value="0")
        self.global_codec_var = StringVar()
        self.subtitle_mode_var = StringVar(value="copy")
        self.subtitle_path_var = StringVar()
        self.lut_path_var = StringVar()
        self.watermark_path_var = StringVar()
        self.watermark_position_var = StringVar(value="top_right")
        self.watermark_scale_var = DoubleVar(value=0.1)
        self.watermark_opacity_var = DoubleVar(value=1.0)
        self.watermark_padding_var = IntVar(value=10)

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
        
        columns = ("Fichier", "Codec", "Qualité", "Progrès", "Statut")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        
        for col in columns:
            self.tree.heading(col, text=col)
            if col == "Fichier":
                self.tree.column(col, width=200)
            elif col == "Progrès":
                self.tree.column(col, width=80)
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
        
        self._on_video_mode_change()
        self._on_tonemap_change()
        
        initial_media_type = self.global_type_var.get() or "video"
        self._update_media_type_ui(initial_media_type)
        
        self.root.after(100, self._update_scroll_state)
        
    def _on_media_type_change(self, event=None):
        media_type = self.global_type_var.get()
        self._update_codec_choices()
        self._update_media_type_ui(media_type)
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
        servers_menu.add_command(label="Test Connexions", command=lambda: asyncio.create_task(self.test_all_servers()))

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
        connected_count = len(connected_servers)
        total_jobs = sum(s.current_jobs for s in connected_servers)
        if connected_count == 0:
            self.servers_var.set("🔴 Aucun serveur")
        else:
            self.servers_var.set(f"🟢 {connected_count} serveur(s) - {total_jobs} jobs en cours")

    def open_server_manager(self):
        ServerManagerWindow(self.root, self.server_discovery)

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
        keep_structure = Settings.data.get("keep_folder_structure", True)
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
            output_cfg.encoder = self._get_encoder_name_from_display(self.global_encoder_var.get())
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
            job_internal_id = str(id(job))

            display_encoder = output_cfg.encoder or "-"
            display_quality = output_cfg.quality or output_cfg.cq_value or output_cfg.bitrate or "-"

            self.tree.insert("", "end", iid=job_internal_id, values=(p.name, display_encoder, display_quality, "0%", "pending"))
            self.job_rows[job_internal_id] = {"job": job}
            current_batch_job_ids.append(job_internal_id)
        
        if current_batch_job_ids:
            self.last_import_job_ids = current_batch_job_ids

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
                asyncio.create_task(self.job_scheduler.add_job(job, self._on_job_progress, self._on_job_completion))

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
        messagebox.showinfo("Non implémenté", "Fonctionnalité en cours de développement")

    def _clear_queue(self):
        self.jobs.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.job_rows.clear()

    def _save_preset(self):
        messagebox.showinfo("Non implémenté", "Fonctionnalité en cours de développement")

    def _load_preset_by_name(self, name):
        messagebox.showinfo("Non implémenté", f"Chargement du préréglage {name} en cours de développement")

    def _show_log_viewer(self):
        messagebox.showinfo("Non implémenté", "Fonctionnalité en cours de développement")

    def _toggle_watch(self):
        messagebox.showinfo("Non implémenté", "Surveillance de dossier en cours de développement")

    def _update_control_buttons_state(self, state):
        pass

    def _update_preset_list(self):
        pass

    def _update_codec_choices(self):
        pass

    def _update_media_type_ui(self, media_type):
        pass

    def _update_job_selector_combobox(self):
        pass

    def _update_inspector_file_list(self):
        pass

    def _update_job_row(self, job):
        pass

    def _update_overall_progress(self):
        pass

    def _show_encoding_completion_notification(self):
        messagebox.showinfo("Terminé", "Tous les encodages sont terminés !")

    def _execute_post_encode_action(self, action):
        messagebox.showinfo("Non implémenté", f"Action post-encodage '{action}' en cours de développement")

    def _get_container_from_display(self, display):
        return "mp4"  # Valeur par défaut

    def _get_encoder_name_from_display(self, display):
        return "libx264"  # Valeur par défaut

    def _setup_drag_drop(self):
        pass

    # Méthodes manquantes de _build_encoding_section
    def _on_job_selected_for_settings_change(self, event=None):
        pass

    def _apply_ui_settings_to_selected_job_via_combobox(self):
        messagebox.showinfo("Non implémenté", "Application des paramètres en cours de développement")

    def _apply_ui_settings_to_last_import_batch(self):
        messagebox.showinfo("Non implémenté", "Application aux derniers fichiers en cours de développement")

    def _load_preset(self, event=None):
        messagebox.showinfo("Non implémenté", "Chargement des préréglages en cours de développement")

    def _on_resolution_change(self, event=None):
        pass

    def _on_video_mode_change(self):
        pass

    def _update_quality_controls_for_global(self):
        pass

    def _update_encoder_choices(self):
        pass

    def _update_container_choices(self):
        pass

    def _update_quality_preset_controls(self):
        pass

    def _on_subtitle_mode_change(self, event=None):
        pass

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
        pass

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
        messagebox.showinfo("Non implémenté", "Suppression du job en cours de développement")

    def _on_inspector_selection_change(self, event):
        """Appelée quand la sélection change dans l'inspecteur"""
        pass
