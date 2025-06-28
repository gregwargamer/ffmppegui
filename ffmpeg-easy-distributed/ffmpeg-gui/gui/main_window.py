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
    def __init__(self, root, app_state, app_controller, loop, run_async_func, dnd_available=False):
        self.root = root
        self.root.title("FFmpeg Frontend")
        self.root.geometry("1200x800")
        
        # Nouvelle architecture : State et Controller
        self.state = app_state
        self.controller = app_controller
        self.loop = loop
        self.run_async_func = run_async_func
        self.logger = logging.getLogger(__name__)
        self.dnd_available = dnd_available
        
        # Références temporaires pour compatibilité (à supprimer progressivement)
        self.jobs = self.state.jobs  # Alias temporaire
        self.server_discovery = getattr(app_controller, 'server_discovery', None)
        self.job_scheduler = getattr(app_controller, 'job_scheduler', None)  
        self.distributed_client = getattr(app_controller, 'distributed_client', None)
        self.server_map = {}  # Dictionnaire pour mapper les IDs serveur aux noms
        
        # Variables d'interface Tkinter (liées aux widgets, pas à l'état business)
        self.input_folder = StringVar()
        self.output_folder = StringVar()
        self.job_rows: dict[str, dict] = {} # {tree_id: {"job": EncodeJob, "outputs": {output_id: tree_id}}}
        self.last_import_job_ids: list[str] = []
        
        # Variables d'interface Tkinter spécifiques à l'UI
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
        
        # Enregistrer comme observateur des changements d'état APRÈS la construction de l'UI
        self.state.register_observer(self._on_state_changed)
        
        # Initialiser après la construction de l'UI
        self.root.after(100, self._initial_ui_setup)

    def _on_state_changed(self, change_type: str = "general"):
        """
        Méthode centrale appelée quand l'état de l'application change.
        
        Cette méthode met à jour toute l'interface utilisateur pour refléter
        le nouvel état de l'application. C'est le cœur de la nouvelle architecture.
        """
        try:
            self._update_ui_from_state(change_type)
        except Exception as e:
            self.logger.error(f"Erreur lors de la mise à jour de l'interface: {e}", exc_info=True)

    def _update_ui_from_state(self, change_type: str):
        """Met à jour l'interface utilisateur basée sur l'état actuel"""
        
        # Mettre à jour la liste des jobs, mais seulement si ce n'est pas un changement de média global
        # pour éviter d'écraser la sélection de l'utilisateur.
        if change_type != "global_media_type_changed":
            self._update_jobs_display()
        
        # Mettre à jour les informations des serveurs
        self._update_servers_display()
        
        # Mettre à jour les paramètres d'encodage globaux
        self._update_encoding_settings_display()
        
        # Mettre à jour le progrès global
        self._update_progress_display()
        
        # Mettre à jour l'état des boutons
        self._update_buttons_state()
        
        # Mettre à jour la liste des presets
        self._update_presets_display()

    def _update_jobs_display(self):
        """Met à jour l'affichage de la liste des jobs"""
        # Vérifier que l'interface est initialisée
        if not hasattr(self, 'tree') or not hasattr(self, 'job_rows'):
            return
            
        # Synchroniser la treeview avec l'état des jobs
        current_items = set(self.tree.get_children())
        state_job_ids = {job.job_id for job in self.state.jobs}
        displayed_job_ids = {self.job_rows[item]["job"].job_id for item in current_items if item in self.job_rows}
        
        # Supprimer les jobs qui ne sont plus dans l'état
        for item in current_items:
            if item in self.job_rows:
                job = self.job_rows[item]["job"]
                if job.job_id not in state_job_ids:
                    self.tree.delete(item)
                    del self.job_rows[item]
        
        # Ajouter ou mettre à jour les jobs de l'état
        for job in self.state.jobs:
            self._update_or_add_job_row(job)

    def _update_or_add_job_row(self, job: EncodeJob):
        """Met à jour ou ajoute une ligne pour un job"""
        # Chercher si le job existe déjà dans l'affichage
        existing_item = None
        for item, data in self.job_rows.items():
            if data["job"].job_id == job.job_id:
                existing_item = item
                break
        
        if existing_item:
            # Mettre à jour le job existant
            self.job_rows[existing_item]["job"] = job
            self._update_job_row_display(existing_item, job)
        else:
            # Ajouter un nouveau job
            self._add_job_row(job)

    def _add_job_row(self, job: EncodeJob):
        """Ajoute une nouvelle ligne pour un job"""
        # Déterminer les valeurs d'affichage
        codec = job.outputs[0].encoder if job.outputs else "N/A"
        quality = job.outputs[0].quality if job.outputs else "N/A"
        progress = f"{getattr(job, 'progress', 0):.1f}%"
        status = getattr(job, 'status', 'pending')
        server = getattr(job, 'assigned_server', 'Local')
        
        item_id = self.tree.insert("", "end", values=(
            job.src_path.name, codec, quality, progress, status, server
        ))
        
        self.job_rows[item_id] = {"job": job, "outputs": {}}

    def _update_job_row_display(self, item_id: str, job: EncodeJob):
        """Met à jour l'affichage d'une ligne de job"""
        codec = job.outputs[0].encoder if job.outputs else "N/A"
        quality = job.outputs[0].quality if job.outputs else "N/A"
        progress = f"{getattr(job, 'progress', 0):.1f}%"
        status = getattr(job, 'status', 'pending')
        server = getattr(job, 'assigned_server', 'Local')
        
        self.tree.item(item_id, values=(
            job.src_path.name, codec, quality, progress, status, server
        ))

    def _update_servers_display(self):
        """Met à jour l'affichage des informations de serveurs"""
        connected_servers = self.state.get_connected_servers()
        # Utilise l'ancienne méthode pour la compatibilité
        self.update_server_status(connected_servers)

    def _update_encoding_settings_display(self):
        """Met à jour l'affichage des paramètres d'encodage globaux"""
        # Synchroniser les variables d'interface avec l'état
        if hasattr(self, 'global_type_var'):
            self.global_type_var.set(self.state.global_media_type)
        if hasattr(self, 'global_codec_var'):
            self.global_codec_var.set(self.state.global_codec)
        if hasattr(self, 'global_encoder_var'):
            self.global_encoder_var.set(self.state.global_encoder)
        if hasattr(self, 'container_var'):
            self.container_var.set(self.state.global_container)
        if hasattr(self, 'quality_var'):
            self.quality_var.set(self.state.global_quality)

    def _update_progress_display(self):
        """Met à jour l'affichage du progrès global"""
        if hasattr(self, 'progress_bar'):
            self.progress_bar['value'] = self.state.global_progress

    def _update_buttons_state(self):
        """Met à jour l'état des boutons selon l'état de l'application"""
        # Vérifier que l'interface est initialisée
        if not hasattr(self, 'start_btn'):
            return
            
        has_jobs = len(self.state.jobs) > 0
        is_encoding = self.state.is_encoding
        
        state = "disabled" if not has_jobs or is_encoding else "normal"
        self.start_btn.config(state=state)

    def _update_presets_display(self):
        """Met à jour l'affichage de la liste des presets"""
        # Utilise l'ancienne méthode pour la compatibilité
        self._update_preset_list()
        
    def _initial_ui_setup(self):
        """Fonction pour configurer l'état initial de l'UI après son chargement."""
        # Initialiser les listes de presets
        self._update_preset_list()
        
        # Vérifier que l'interface adaptative est bien configurée
        # (déjà fait dans _build_encoding_section mais on s'assure que tout est synchronisé)
        if hasattr(self, 'global_type_var'):
            current_type = self.global_type_var.get()
            self.logger.info(f"✅ Interface adaptative initialisée - Type de média: {current_type}")
            
            # Forcer une mise à jour complète pour s'assurer que tout est cohérent
            # self._update_media_type_ui(current_type) # Handled by orchestrator
            # self._update_codec_choices() # Handled by orchestrator
            # self._update_encoder_choices() # Handled by orchestrator
            # self._update_container_choices() # Handled by orchestrator
            # self._update_quality_controls_for_global() # Old method

            # Call the new orchestrator for initial setup based on global state
            self._update_ui_for_media_type_and_settings(
                media_type=self.state.global_media_type,
                output_config=None,
                is_global_context=True
            )
        
        self.logger.info("🎯 Interface utilisateur entièrement initialisée")

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
        # Variables d'interface liées au state
        self.selected_file_var = StringVar(value="No file selected")
        self.preset_name_var = StringVar()
        self.selected_job_for_settings_var = StringVar()
        self.resolution_var_settings = StringVar()
        self.crop_top_var = StringVar(value="0")
        self.crop_bottom_var = StringVar(value="0")
        self.crop_left_var = StringVar(value="0")
        self.crop_right_var = StringVar(value="0")
        
        # Variables liées au state global - avec callbacks pour synchronisation
        self.global_type_var = StringVar(value=self.state.global_media_type)
        self.global_codec_var = StringVar(value=self.state.global_codec)
        self.global_encoder_var = StringVar(value=self.state.global_encoder)
        self.container_var = StringVar(value=self.state.global_container)
        self.video_mode_var = StringVar(value="quality")
        self.quality_var = StringVar(value=self.state.global_quality)
        self.preset_var = StringVar(value=self.state.global_preset)
        self.bitrate_var = StringVar(value=self.state.global_bitrate)
        self.multipass_var = BooleanVar(value=self.state.global_multipass)
        self.custom_flags_var = StringVar()
        self.timestamp_var = StringVar(value="00:00:10")
        self.subtitle_mode_var = StringVar(value="copy")
        self.subtitle_path_var = StringVar()
        
        # Configurer les callbacks pour synchroniser avec l'état
        self.global_codec_var.trace_add("write", self._on_ui_setting_changed)
        self.global_encoder_var.trace_add("write", self._on_ui_setting_changed)
        self.container_var.trace_add("write", self._on_ui_setting_changed)
        self.quality_var.trace_add("write", self._on_ui_setting_changed)
        self.preset_var.trace_add("write", self._on_ui_setting_changed)
        self.bitrate_var.trace_add("write", self._on_ui_setting_changed)
        self.multipass_var.trace_add("write", self._on_ui_setting_changed)

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
        
        # Initialisation complète de l'interface adaptative
        initial_media_type = self.global_type_var.get() or "video"
        self.global_type_var.set(initial_media_type)  # S'assurer que la valeur est définie
        
        # Séquence d'initialisation dans le bon ordre
        self._update_media_type_ui(initial_media_type)
        self._update_codec_choices()           # Remplir les codecs selon le type de média (initial call)
        self._update_encoder_choices()         # Remplir les encodeurs selon le codec (initial call)
        self._update_container_choices()       # Remplir les conteneurs selon codec/encodeur (initial call)
        # self._update_quality_controls_for_global() # This is the old call, it's now handled by
                                                 # _initial_ui_setup via _update_ui_for_media_type_and_settings
                                                 # which calls _update_quality_controls_ui.
                                                 # The calls above ensure comboboxes have initial values before full orchestration.
        
        self.root.after(100, self._update_scroll_state)
        
    def _on_media_type_change(self, event=None):
        """Called when the user changes the global media type dropdown."""
        selected_global_media_type = self.global_type_var.get()
        self.logger.info(f"Global media type dropdown changed by user to: {selected_global_media_type}")

        # Update AppState. This will also reset related global settings in AppState (codec, encoder, etc.)
        # and notify observers. The logging part is handled by the controller now.
        self.controller.set_global_media_type(selected_global_media_type)

        # After controller updates AppState, AppState.global_media_type is selected_global_media_type.
        # AppState.global_codec, global_encoder, global_container are now blank.
        # The _update_ui_for_media_type_and_settings call will handle syncing UI vars from these
        # new blank AppState values and then populating choices correctly.
        self._update_ui_for_media_type_and_settings(selected_global_media_type, output_config=None, is_global_context=True)
    
    def _on_codec_change(self, event=None):
        """Called when the codec selection changes."""
        media_type = self.global_type_var.get()
        new_codec = self.global_codec_var.get()
        self.logger.debug(f"Codec changed to: {new_codec} for media type: {media_type}")

        self._update_encoder_choices(for_media_type=media_type, codec=new_codec)
        # Encoder change will trigger its own _on_encoder_change, which handles further updates.
        # However, if encoder doesn't change but codec did, we might need to force update quality controls.
        # Let's ensure _on_encoder_change is robust or call downstream updates here too.

        # For now, let _on_encoder_change handle the rest if it's triggered.
        # If global_encoder_var didn't change value, _on_encoder_change might not fire.
        # So, we explicitly update downstream elements that depend on codec or encoder.
        current_encoder = self.global_encoder_var.get() # This would be the newly set encoder if it changed
        self._update_container_choices(for_media_type=media_type)
        self._update_quality_preset_controls(encoder=current_encoder)
        self._update_quality_controls_ui(media_type=media_type, encoder=current_encoder, codec=new_codec,
                                         output_config=self._get_current_job_output_config_for_ui()) # Pass current job's config

        # Update AppState if this is a global context change
        if not self.selected_job_for_settings_var.get(): # If no specific job is selected
            self.state.update_global_encoding_settings(codec=new_codec, encoder=current_encoder)


    def _on_encoder_change(self, event=None):
        """Called when the encoder selection changes."""
        media_type = self.global_type_var.get()
        codec = self.global_codec_var.get() # Current codec
        new_encoder = self.global_encoder_var.get()
        self.logger.debug(f"Encoder changed to: {new_encoder} for media type: {media_type}, codec: {codec}")

        self._update_quality_preset_controls(encoder=new_encoder)
        self._update_quality_controls_ui(media_type=media_type, encoder=new_encoder, codec=codec,
                                         output_config=self._get_current_job_output_config_for_ui())

        # Update AppState if this is a global context change
        if not self.selected_job_for_settings_var.get(): # If no specific job is selected
             self.state.update_global_encoding_settings(encoder=new_encoder)

    def _get_current_job_output_config_for_ui(self) -> Optional[OutputConfig]:
        """Helper to get the OutputConfig for the currently selected job in the settings UI, or None."""
        selected_job_name = self.selected_job_for_settings_var.get()
        if not selected_job_name:
            return None
        # Ensure self.state.jobs is the correct list of jobs
        target_job = next((j for j in self.state.jobs if j.src_path.name == selected_job_name), None)
        if target_job and target_job.outputs:
            return target_job.outputs[0]
        self.logger.debug(f"No output config found for UI for job: {selected_job_name}")
        return None
    
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
        
        # self._update_codec_choices() # Orchestrator will handle this and more

        # After resetting vars, call the orchestrator to update the UI to reflect these defaults
        # for the "video" media type (which is the default reset type).
        self._update_ui_for_media_type_and_settings(
            media_type="video", # Resetting to video defaults
            output_config=None,
            is_global_context=True
        )
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
        for preset_name in self.state.get_preset_names():
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
        
        # Indicateur de statut drag & drop
        dnd_status = "🟢 Drag & Drop activé" if self.dnd_available else "🟡 Drag & Drop désactivé"
        self.dnd_var = StringVar(value=dnd_status)
        self.dnd_label = ttk.Label(self.status_frame, textvariable=self.dnd_var, font=("Helvetica", 9))
        self.dnd_label.pack(side=tk.LEFT, padx=(20, 5))
        
        self.servers_var = StringVar(value="🔴 Aucun serveur")
        self.servers_label = ttk.Label(self.status_frame, textvariable=self.servers_var)
        self.servers_label.pack(side=tk.RIGHT, padx=5)

    def update_server_status(self, connected_servers: List[ServerInfo]):
        """Met à jour le statut des serveurs dans la barre d'état."""
        # Vérifier que l'interface est initialisée
        if not hasattr(self, 'servers_var') or not self.server_discovery or not self.job_scheduler:
            return
            
        try:
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
        except Exception as e:
            self.logger.warning(f"Erreur lors de la mise à jour du statut serveur: {e}")

        self._update_server_map_and_status(connected_servers)

    def _update_server_map_and_status(self, connected_servers: List[ServerInfo]):
        """Met à jour le dictionnaire des serveurs et la barre de statut."""
        # Vérifier que les composants nécessaires sont initialisés
        if not self.server_discovery or not self.job_scheduler:
            return
            
        try:
            self.server_map.clear()
            local_server = self.job_scheduler.get_local_server_info()
            if local_server:
                self.server_map[local_server.server_id] = local_server.name
            
            all_servers = self.server_discovery.get_all_servers()
            for server_id, server_info in all_servers.items():
                self.server_map[server_id] = server_info.name
                
            # Éviter la récursion - ne pas rappeler update_server_status ici
            # car cette méthode est déjà appelée depuis update_server_status
            
            # Mettre à jour les jobs existants pour refléter les noms de serveur
            if hasattr(self, 'tree') and hasattr(self, 'job_rows'):
                for job_id in self.tree.get_children():
                    if job_id in self.job_rows:
                        self._update_job_row(self.job_rows[job_id]["job"])
        except Exception as e:
            self.logger.warning(f"Erreur lors de la mise à jour server map: {e}")

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
        
        info_text = "Optional: If no output folder is selected, files will be saved in the same folder as source with encoder suffix (e.g., filename_x265.mp4)"
        if not self.dnd_available:
            info_text += "\n⚠️ Note: Drag & Drop is disabled on this system. Use the buttons above to add files."
        
        info_label = ttk.Label(folder_grid, text=info_text, 
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
        preset_names = self.state.get_preset_names()
        if preset_names:
            self.watch_preset_combo['values'] = preset_names
            self.watch_preset_combo.set(preset_names[0])
        
        self.watch_status = ttk.Label(watch_frame, text="Statut: Inactif")
        self.watch_status.pack(side=tk.BOTTOM, fill=tk.X, pady=5)

    def _add_files(self):
        """Ajoute des fichiers via une boîte de dialogue"""
        paths = filedialog.askopenfilenames(title="Select input files")
        if not paths:
            return
        
        file_paths = [Path(p) for p in paths]
        added_jobs = self.controller.add_files_to_queue(file_paths)
        
        if added_jobs:
            self.logger.info(f"✅ {len(added_jobs)} fichiers ajoutés")
        else:
            self.logger.warning("⚠️ Aucun fichier valide sélectionné")

    def _add_folder(self):
        """Ajoute tous les fichiers d'un dossier"""
        folder_path = filedialog.askdirectory(title="Select a Folder")
        if folder_path:
            self.input_folder.set(folder_path)
            folder_path_obj = Path(folder_path)
            added_jobs = self.controller.add_folder_to_queue(folder_path_obj, recursive=True)
            
            if added_jobs:
                self.logger.info(f"✅ {len(added_jobs)} fichiers ajoutés depuis {folder_path}")
            else:
                self.logger.warning(f"⚠️ Aucun fichier média trouvé dans {folder_path}")

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
        """Méthode héritée qui utilise maintenant le contrôleur"""
        # Utiliser le contrôleur pour ajouter les fichiers
        added_jobs = self.controller.add_files_to_queue(paths)
        
        if added_jobs:
            self.logger.info(f"✅ {len(added_jobs)} fichiers ajoutés")
            # Les IDs des nouveaux jobs pour l'import batch
            self.last_import_job_ids = [job.job_id for job in added_jobs]
        else:
            self.logger.warning("⚠️ Aucun fichier valide trouvé")
            self.last_import_job_ids = []

    def _enqueue_paths_legacy(self, paths: list[Path]):
        """Ancienne méthode conservée temporairement pour référence"""
        out_root = Path(self.output_folder.get()) if self.output_folder.get() and not self.output_folder.get().startswith("(no") else None
        keep_structure = self.state.settings.ui.keep_folder_structure
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
        SettingsWindow(self.root, self.state.settings, self._update_codec_choices, self._update_preset_list)

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
        """Démarre l'encodage des jobs en attente"""
        if not self.state.jobs:
            messagebox.showinfo("No Jobs", "There are no jobs in the queue.")
            return

        if self.state.is_encoding:
            messagebox.showwarning("Already Running", "Encoding is already in progress!")
            return

        output_folder_set = self.output_folder.get() and not self.output_folder.get().startswith("(no")
        for job in self.state.jobs:
            for output_cfg in job.outputs:
                if not output_folder_set and not output_cfg.dst_path:
                    messagebox.showwarning("Output Folder Missing", f"Output folder is not selected for job: {job.src_path.name}.")
                    return

        # Utiliser le contrôleur pour démarrer l'encodage
        self.run_async_func(self.controller.start_encoding())

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
        SubtitleManagementWindow(self.root, selected_job, self.state.settings)

    def _clear_queue(self):
        """Vide la queue d'encodage"""
        self.controller.clear_queue()
        # L'interface sera mise à jour automatiquement via _on_state_changed

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
        connected_servers = self.state.get_connected_servers()
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

        self.state.save_preset(preset_name, preset_data)
        
        self._update_preset_list()
        messagebox.showinfo("Succès", f"Préréglage '{preset_name}' sauvegardé.")

    def _load_preset_by_name(self, name):
        preset_data = self.state.load_preset(name)
        if not preset_data:
            messagebox.showerror("Erreur", f"Préréglage '{name}' non trouvé.")
            return

        # Appliquer les valeurs du préréglage à l'UI
        self.global_type_var.set(preset_data.get("media_type", "video"))
        self._update_media_type_ui(self.global_type_var.get())
        
        self.container_var.set(preset_data.get("container", ""))
        self.global_codec_var.set(preset_data.get("codec", ""))
        
        # Mettre à jour les choix de codec AVANT de définir l'encodeur
        # Pass the media type from preset to ensure codec choices are for the correct type
        self._update_codec_choices(for_media_type=preset_data.get("media_type", "video"))

        def apply_remaining_preset():
            encoder_name = preset_data.get("encoder", "")
            encoder_display = ""
            # On cherche la description complète de l'encodeur pour l'afficher
            # Values in global_encoder_combo should be populated by _update_encoder_choices,
            # which is called by _update_codec_choices if codec changes, or by orchestrator.
            # Ensure encoder choices are for the *preset's codec* before finding display name.
            # This might require calling _update_encoder_choices here if not already done correctly.
            # For now, assume self.global_encoder_combo['values'] is correctly populated for the preset's codec.
            # This implies _update_codec_choices correctly set the preset's codec, and then called _update_encoder_choices.
            # Let's call _update_encoder_choices explicitly for the preset's codec to be safe.
            self._update_encoder_choices(for_media_type=self.global_type_var.get(), codec=self.global_codec_var.get())

            for item_val in self.global_encoder_combo['values']:
                if item_val.startswith(encoder_name):
                    encoder_display = item_val
                    break
            self.global_encoder_var.set(encoder_display or encoder_name)

            self.video_mode_var.set(preset_data.get("video_mode", "quality"))
            self.quality_var.set(str(preset_data.get("quality_or_cq", "22")))
            self.bitrate_var.set(str(preset_data.get("bitrate", "4000")))
            self.multipass_var.set(preset_data.get("multipass", False))
            self.preset_var.set(preset_data.get("preset", "medium")) # This is encoder preset

            self.resolution_var_settings.set(preset_data.get("resolution", "Keep Original"))
            crop_settings = preset_data.get("crop", {})
            self.crop_top_var.set(str(crop_settings.get("top", "0")))
            self.crop_bottom_var.set(str(crop_settings.get("bottom", "0")))
            self.crop_left_var.set(str(crop_settings.get("left", "0")))
            self.crop_right_var.set(str(crop_settings.get("right", "0")))
            
            self.preserve_hdr_var.set(preset_data.get("preserve_hdr", True))
            self.tonemap_var.set(preset_data.get("tonemap", False))
            self.tonemap_method_var.set(preset_data.get("tonemap_method", "hable"))

            self.subtitle_mode_var.set(preset_data.get("subtitle_mode", "copy"))
            self.subtitle_path_var.set(preset_data.get("subtitle_path", ""))
            self.lut_path_var.set(preset_data.get("lut_path", ""))
            watermark_cfg = preset_data.get("watermark", {})
            self.watermark_path_var.set(watermark_cfg.get('path', ""))
            self.watermark_position_var.set(watermark_cfg.get('position', "top_right"))
            self.watermark_scale_var.set(float(watermark_cfg.get('scale', 0.1)))
            self.watermark_opacity_var.set(float(watermark_cfg.get('opacity', 1.0)))
            self.watermark_padding_var.set(int(watermark_cfg.get('padding', 10)))
            self.custom_flags_var.set(preset_data.get("custom_flags", ""))


            # After setting all StringVars from preset_data, call the main UI orchestrator.
            self._update_ui_for_media_type_and_settings(
                media_type=self.global_type_var.get(), # This was set from preset
                output_config=None, # Preset is loaded into global view, not a specific job's output
                is_global_context=True
            )
            messagebox.showinfo("Préréglage chargé", f"Le préréglage '{name}' a été chargé.")

        self.root.after(100, apply_remaining_preset)
        
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
        
        presets = self.state.get_preset_names()
        self.preset_combo['values'] = presets
        self.watch_preset_combo['values'] = presets
        
        for preset_name in presets:
            preset_menu.add_command(label=preset_name, command=lambda name=preset_name: self._load_preset_by_name(name))

    def _update_codec_choices(self, for_media_type: Optional[str] = None):
        media_type_to_use = for_media_type if for_media_type is not None else self.global_type_var.get()
        self.logger.debug(f"Updating codec choices for media type: {media_type_to_use}")

        all_codecs = FFmpegHelpers.available_codecs()
        codecs_for_type = all_codecs.get(media_type_to_use, [])
        
        current_codec_val = self.global_codec_var.get() # Preserve current if valid in new list
        self.global_codec_combo['values'] = codecs_for_type
        
        if current_codec_val and current_codec_val in codecs_for_type:
            self.global_codec_var.set(current_codec_val)
        elif codecs_for_type:
            self.global_codec_var.set(codecs_for_type[0])
        else:
            self.global_codec_var.set("")
            
        # Important: Do NOT call _update_encoder_choices() here directly if this method
        # is part of a larger refresh sequence (e.g., called by _update_ui_for_media_type_and_settings).
        # The orchestrating method should handle the sequence.
        # If called standalone (e.g. directly from _on_media_type_change's first step),
        # then the caller is responsible for the next step.

    def _update_media_type_ui(self, media_type):
        is_video = media_type == "video"
        is_audio = media_type == "audio"
        is_image = media_type == "image"
        
        self.logger.info(f"🎯 Adaptation interface pour type: {media_type}")

        # Gérer l'affichage intelligent des sections selon le type de média
        if is_video:
            # Video: toutes les sections disponibles
            self.transform_frame.pack(fill=tk.X, pady=(0, 5))
            self.quality_frame.pack(fill=tk.X, pady=(0, 5))
            self.hdr_frame.pack(fill=tk.X, pady=(0, 5))
            self.subtitle_frame.pack(fill=tk.X, pady=(0, 5))
            self.lut_frame.pack(fill=tk.X, pady=(0, 5))
            self.codec_label.config(text="Codec Vidéo:")
            
        elif is_audio:
            # Audio: seulement qualité et codec visibles
            self.transform_frame.pack_forget()  # Résolution non pertinente
            self.quality_frame.pack(fill=tk.X, pady=(0, 5))  # Qualité importante
            self.hdr_frame.pack_forget()  # HDR non pertinent
            self.subtitle_frame.pack_forget()  # Sous-titres non pertinents
            self.lut_frame.pack_forget()  # LUT non pertinent pour audio
            self.codec_label.config(text="Codec Audio:")
            
        elif is_image:
            # Images: résolution, qualité et LUT utiles - pas HDR ni sous-titres
            self.transform_frame.pack(fill=tk.X, pady=(0, 5))  # Résolution utile pour images
            self.quality_frame.pack(fill=tk.X, pady=(0, 5))  # Qualité essentielle pour images
            self.hdr_frame.pack_forget()  # HDR pas pertinent
            self.subtitle_frame.pack_forget()  # Sous-titres non pertinents
            self.lut_frame.pack(fill=tk.X, pady=(0, 5))  # LUT utile pour images
            self.codec_label.config(text="Codec Image:")
        
        self.root.after(100, self._update_scroll_state)

    def _update_job_selector_combobox(self):
        job_names = [job.src_path.name for job in self.jobs]
        self.job_selector_combobox['values'] = job_names
        if job_names:
            self.job_selector_combobox.set(job_names[-1])
            # Setting the combobox value will trigger its <<ComboboxSelected>> event,
            # which is bound to _on_job_selected_for_settings_change.
            # _on_job_selected_for_settings_change now calls the new orchestrator.
            # So, no explicit call to load settings is needed here anymore.
        elif not job_names and hasattr(self, 'selected_job_for_settings_var'):
            # No jobs, clear the selection and revert UI to global defaults
            self.selected_job_for_settings_var.set("")
            # Trigger update to global defaults if no job is selected
            current_global_media_type = self.state.global_media_type
            self._update_ui_for_media_type_and_settings(current_global_media_type, output_config=None, is_global_context=True)


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
        if self.dnd_available:
            try:
                from tkinterdnd2 import DND_FILES
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind('<<Drop>>', self._on_drop)
                self.logger.info("✅ Drag and drop configuré avec succès")
            except Exception as e:
                self.logger.error(f"❌ Échec de la configuration du drag and drop: {e}")
                self.dnd_available = False
        else:
            self.logger.warning("⚠️ TkinterDnD2 non disponible - drag and drop désactivé")

    def _on_drop(self, event):
        """Gère le drag & drop de fichiers"""
        paths = self.root.tk.splitlist(event.data)
        file_paths = [Path(p) for p in paths]
        
        # Utiliser le contrôleur pour ajouter les fichiers
        added_jobs = self.controller.add_files_to_queue(file_paths)
        
        if added_jobs:
            self.logger.info(f"✅ {len(added_jobs)} fichiers ajoutés via drag & drop")
        else:
            self.logger.warning("⚠️ Aucun fichier valide ajouté")

    # Méthodes manquantes de _build_encoding_section
    def _on_job_selected_for_settings_change(self, event=None):
        selected_job_name = self.selected_job_for_settings_var.get()
        if not selected_job_name:
            # No job selected in combobox, revert to global settings view
            # The global_type_var should already reflect the user's choice in the main media type dropdown.
            # Or, if AppState's global_media_type is the source of truth, sync from that.
            # For now, assume global_type_var is the current global context.
            current_global_media_type = self.state.global_media_type # Get from AppState
            self.global_type_var.set(current_global_media_type) # Ensure UI var matches
            self._update_ui_for_media_type_and_settings(current_global_media_type, output_config=None, is_global_context=True)
            return

        target_job = next((j for j in self.state.jobs if j.src_path.name == selected_job_name), None)

        if not target_job:
            self.logger.warning(f"Job '{selected_job_name}' not found for settings configuration.")
            # Fallback to global settings view if job disappears or something unexpected.
            current_global_media_type = self.state.global_media_type
            self.global_type_var.set(current_global_media_type)
            self._update_ui_for_media_type_and_settings(current_global_media_type, output_config=None, is_global_context=True)
            return

        # We have a target_job. Update the encoding settings UI based on its type and settings.
        job_media_type = target_job.mode

        # Set the global_type_var to the job's media type for visual consistency in the media type dropdown.
        # This specific `set` should not trigger a global context change by `_on_media_type_change`.
        # The `is_global_context=False` in the call below handles this.
        if self.global_type_var.get() != job_media_type: # Only set if different to avoid unnecessary trace fires
            self.global_type_var.set(job_media_type)

        # Pass the first output's config. Jobs should ideally always have at least one.
        job_output_config = target_job.outputs[0] if target_job.outputs else None
        if not job_output_config:
             self.logger.warning(f"Job '{selected_job_name}' has no output configurations. UI might not fully update.")
             # Create a temporary default OutputConfig for UI purposes if needed, or handle gracefully.
             # For now, pass None, _update_ui_for_media_type_and_settings will use defaults for the job's media type.

        self._update_ui_for_media_type_and_settings(job_media_type, output_config=job_output_config, is_global_context=False)

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
        output_cfg.codec = self.global_codec_var.get() # Apply the selected codec
        
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

    # _load_settings_from_selected_job is now effectively replaced by calling
    # _update_ui_for_media_type_and_settings(job.mode, job.outputs[0], is_global_context=False)
    # from _on_job_selected_for_settings_change. So, this method can be removed.

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

    def _update_container_choices(self, for_media_type: Optional[str] = None):
        """Met à jour la liste des conteneurs compatibles avec le type de média sélectionné."""
        media_type_to_use = for_media_type if for_media_type is not None else self.global_type_var.get()
        self.logger.debug(f"Updating container choices for media type: {media_type_to_use}")

        containers: List[str] = []
        if media_type_to_use == "video":
            containers = ["mp4", "mkv", "mov", "webm", "avi"]
        elif media_type_to_use == "audio":
            containers = ["m4a", "mp3", "opus", "flac", "ogg", "wav", "aac"]
        elif media_type_to_use == "image":
            # For images, container often matches codec, but some codecs can go in generic containers too.
            # FFmpeg often uses the codec name as format for single images e.g. -f image2 or -f webp
            # This list represents common output formats for image sequences or single images.
            containers = ["png", "jpg", "webp", "avif", "jxl", "heic", "tiff", "bmp", "gif"]

        current_container_val = self.container_var.get() # Preserve
        self.container_combo['values'] = containers

        if current_container_val and current_container_val in containers:
            self.container_var.set(current_container_val)
        elif containers:
            # Smart default based on current codec if possible
            codec = self.global_codec_var.get()
            smart_default = ""
            if media_type_to_use == "video":
                if codec == "h264" or codec == "hevc": smart_default = "mp4"
                elif codec == "av1" or codec == "vp9": smart_default = "webm"
                elif codec == "prores": smart_default = "mov"
            elif media_type_to_use == "audio":
                if codec == "aac": smart_default = "m4a"
                elif codec == "opus": smart_default = "opus" # or ogg
                elif codec == "flac": smart_default = "flac"
                elif codec == "mp3": smart_default = "mp3"
            elif media_type_to_use == "image": # Often codec name is the format
                if codec in containers: smart_default = codec

            if smart_default and smart_default in containers:
                self.container_var.set(smart_default)
            else:
                self.container_var.set(containers[0])
        else:
            self.container_var.set("")

    def _update_quality_preset_controls(self, encoder: Optional[str] = None):
        """Met à jour la liste des presets d'encodeur disponibles en fonction de l'encodeur choisi."""
        encoder_to_use = encoder if encoder is not None else self.global_encoder_var.get()
        encoder_name_short = self._get_encoder_name_from_display(encoder_to_use) # Gets the actual encoder name e.g. libx264
        self.logger.debug(f"Updating quality preset controls for encoder: {encoder_name_short}")

        presets: List[str] = []
        # Default presets for libx264, libx265, libvpx, etc.
        if any(e in encoder_name_short for e in ["libx264", "libx265", "libvpx", "libaom", "rav1e", "svt"]):
            presets = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
        elif "qsv" in encoder_name_short or "nvenc" in encoder_name_short or "amf" in encoder_name_short or "videotoolbox" in encoder_name_short:
            # Hardware encoders often use different preset names
             presets = ["default", "slow", "medium", "fast", "hp", "hq", "ll", "llhq", "llhp", "lossless", "p1", "p2", "p3", "p4", "p5", "p6", "p7"] # Generic list, can be refined
             if "qsv" in encoder_name_short: # Intel QSV specific
                 presets = ["veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"] # QSV specific, maps to quality levels
             elif "nvenc" in encoder_name_short: # NVIDIA NVENC specific
                 presets = ["default", "slow", "medium", "fast", "hp", "hq", "bd", "ll", "llhq", "llhp", "lossless", "losslesshp", "p1", "p2", "p3", "p4", "p5", "p6", "p7"]
             elif "amf" in encoder_name_short: # AMD AMF specific
                 presets = ["balanced", "speed", "quality"]
             elif "videotoolbox" in encoder_name_short: # Apple VideoToolbox
                 presets = [] # VideoToolbox doesn't typically expose named presets like this, quality is via bitrate/crf like params
        # Audio/Image encoders usually don't have "speed" presets like video encoders.
        # For those, this list will be empty, and the combobox might be hidden or disabled by _update_quality_controls_for_global

        current_preset_val = self.preset_var.get() # Preserve
        self.quality_entry['values'] = presets

        if current_preset_val and current_preset_val in presets:
            self.preset_var.set(current_preset_val)
        elif presets:
            if "medium" in presets: # Default to medium if available
                 self.preset_var.set("medium")
            else:
                 self.preset_var.set(presets[0])
        else:
            self.preset_var.set("")


    def _update_encoder_choices(self, for_media_type: Optional[str] = None, codec: Optional[str] = None):
        """Met à jour la liste des encodeurs compatibles avec le codec sélectionné et ajoute les encodeurs matériels distants."""
        # media_type_to_use = for_media_type if for_media_type is not None else self.global_type_var.get() # Not directly used for encoder logic here but good for logging
        codec_to_use = codec if codec is not None else self.global_codec_var.get()

    def _update_ui_for_media_type_and_settings(self, media_type: str, output_config: Optional[OutputConfig] = None, is_global_context: bool = False):
        """
        Orchestrates the update of the encoding settings UI based on the given media type
        and specific output configuration OR global defaults.

        Args:
            media_type: The media type ("video", "audio", "image") to adapt the UI for.
            output_config: The specific OutputConfig of a job to load settings from.
                           If None, UI reflects global defaults for the media_type.
            is_global_context: True if this update is for global settings (e.g. user changed main media type dropdown),
                               False if for a specific job selected in the job combobox.
        """
        self.logger.info(f"Updating UI. Media Type: {media_type}, Job Config: {'Provided' if output_config else 'None'}, Global Context: {is_global_context}")

        # 0. If this is a global context change, ensure AppState reflects the new global media type
        #    and its associated blank codec/encoder values.
        if is_global_context:
            self.global_type_var.set(self.state.global_media_type) # Should match 'media_type' arg
            self.global_codec_var.set(self.state.global_codec)     # Will be blank from AppState
            self.global_encoder_var.set(self.state.global_encoder) # Will be blank from AppState
            self.container_var.set(self.state.global_container)   # Will be blank from AppState

            # Reset other UI vars to reflect a fresh global context for the *new* media_type
            # Quality/Bitrate should use defaults for the new media_type and potentially blank encoder
            temp_encoder_for_defaults = self.state.global_encoder # which is blank
            self.quality_var.set(self._get_optimal_quality_for_codec(temp_encoder_for_defaults, media_type))
            self.bitrate_var.set(self._get_optimal_quality_for_codec(temp_encoder_for_defaults, media_type, mode="bitrate"))
            self.preset_var.set("medium")
            self.video_mode_var.set("quality")
            self.custom_flags_var.set("")

            self.resolution_var_settings.set(self.state.settings.ui.default_resolution or "Keep Original")
            self.crop_top_var.set("0"); self.crop_bottom_var.set("0"); self.crop_left_var.set("0"); self.crop_right_var.set("0")
            self.preserve_hdr_var.set(True); self.tonemap_var.set(False); self.tonemap_method_var.set("hable")
            self.subtitle_mode_var.set("copy"); self.subtitle_path_var.set("")
            self.lut_path_var.set(""); self.watermark_path_var.set("")
            self.watermark_position_var.set("top_right"); self.watermark_scale_var.set(0.1)
            self.watermark_opacity_var.set(1.0); self.watermark_padding_var.set(10)

        # 1. Update visibility of UI sections based on media_type
        self._update_media_type_ui(media_type)

        # 2. Update codec choices based on media_type. This will also set self.global_codec_var.
        self._update_codec_choices(for_media_type=media_type)

        # 3. If a specific job's output_config is provided, load its settings into UI variables
        if output_config and not is_global_context: # only load if not in global context
            self.logger.debug(f"Loading settings from provided output_config for job: {getattr(output_config, 'name', 'N/A')}")

            job_codec = getattr(output_config, 'codec', '')
            if not job_codec and output_config.encoder:
                # Attempt to derive codec from encoder
                # This is a simplified approach and might need a more robust mapping utility
                # For example, 'libx264' -> 'h264', 'hevc_nvenc' -> 'hevc'
                encoder_name_lower = output_config.encoder.lower()
                if "264" in encoder_name_lower or "avc" in encoder_name_lower: job_codec = "h264"
                elif "265" in encoder_name_lower or "hevc" in encoder_name_lower: job_codec = "hevc"
                elif "vp9" in encoder_name_lower: job_codec = "vp9"
                elif "av1" in encoder_name_lower: job_codec = "av1"
                elif "aac" in encoder_name_lower: job_codec = "aac"
                elif "mp3" in encoder_name_lower: job_codec = "mp3"
                elif "flac" in encoder_name_lower: job_codec = "flac"
                elif "opus" in encoder_name_lower: job_codec = "opus"
                elif "webp" in encoder_name_lower: job_codec = "webp"
                elif "png" in encoder_name_lower: job_codec = "png"
                elif "jp" in encoder_name_lower: job_codec = "jpeg" # for jpeg, mjpeg, jpegxl
                # Fallback: check if any part of the encoder name is a known codec for this media type
                if not job_codec:
                    available_codecs_for_type = FFmpegHelpers.available_codecs().get(media_type, [])
                    for ac_known in available_codecs_for_type:
                        if ac_known in encoder_name_lower:
                            job_codec = ac_known
                            break
            self.global_codec_var.set(job_codec if job_codec else "") # Set codec, then update encoders

            # Update encoder choices now that codec is (potentially) set for the job
            self._update_encoder_choices(for_media_type=media_type, codec=self.global_codec_var.get())

            encoder_name = getattr(output_config, 'encoder', '')
            encoder_display = ""
            if encoder_name: # Find full display name for the encoder
                for item_value in self.global_encoder_combo['values']: # Check against current combo values
                    if item_value.startswith(encoder_name):
                        encoder_display = item_value; break
            self.global_encoder_var.set(encoder_display or encoder_name)

            self.container_var.set(getattr(output_config, 'container', ''))
            self.video_mode_var.set(getattr(output_config, 'video_mode', 'quality'))

            quality_val = getattr(output_config, 'quality', '')
            if not quality_val and hasattr(output_config, 'cq_value'): quality_val = getattr(output_config, 'cq_value', '')
            self.quality_var.set(str(quality_val))

            self.bitrate_var.set(str(getattr(output_config, 'bitrate', '')))
            self.multipass_var.set(getattr(output_config, 'multipass', False))
            self.preset_var.set(getattr(output_config, 'preset', 'medium'))
            self.custom_flags_var.set(getattr(output_config, 'custom_flags', ''))

            self.resolution_var_settings.set(getattr(output_config, 'resolution', "Keep Original"))
            crop_settings = getattr(output_config, 'crop', {})
            self.crop_top_var.set(str(crop_settings.get("top", "0"))); self.crop_bottom_var.set(str(crop_settings.get("bottom", "0")))
            self.crop_left_var.set(str(crop_settings.get("left", "0"))); self.crop_right_var.set(str(crop_settings.get("right", "0")))

            self.preserve_hdr_var.set(getattr(output_config, 'preserve_hdr', True))
            self.tonemap_var.set(getattr(output_config, 'tonemap', False))
            self.tonemap_method_var.set(getattr(output_config, 'tonemap_method', "hable"))

            self.subtitle_mode_var.set(getattr(output_config, 'subtitle_mode', "copy"))
            self.subtitle_path_var.set(getattr(output_config, 'subtitle_path', ""))

            self.lut_path_var.set(getattr(output_config, 'lut_path', ""))
            watermark_cfg = getattr(output_config, 'watermark', {})
            self.watermark_path_var.set(watermark_cfg.get('path', "")); self.watermark_position_var.set(watermark_cfg.get('position', "top_right"))
            self.watermark_scale_var.set(float(watermark_cfg.get('scale', 0.1))); self.watermark_opacity_var.set(float(watermark_cfg.get('opacity', 1.0)))
            self.watermark_padding_var.set(int(watermark_cfg.get('padding', 10)))
        else:
            # No specific output_config OR is_global_context: ensure encoder choices are updated for current global codec
            # (which might have been set by _update_codec_choices or by the global_context block above)
            self._update_encoder_choices(for_media_type=media_type, codec=self.global_codec_var.get())

        # 4. Update container choices (depends on media_type, potentially codec)
        self._update_container_choices(for_media_type=media_type)

        # 5. Update quality presets (depends on encoder)
        self._update_quality_preset_controls(encoder=self.global_encoder_var.get())

        # 6. Update the actual quality control widgets (CQ entry, bitrate entry, labels)
        # Pass the job's output_config if available AND not in a global context, else it uses UI vars reflecting global defaults.
        effective_output_config_for_quality = output_config if output_config and not is_global_context else None
        self._update_quality_controls_ui(media_type=media_type,
                                         encoder=self.global_encoder_var.get(),
                                         codec=self.global_codec_var.get(),
                                         output_config=effective_output_config_for_quality)

        # 7. Refresh UI states for controls that depend on others using their current var values
        self._on_video_mode_change()
        self._on_tonemap_change()
        self._on_subtitle_mode_change()
        self._on_resolution_change()

        self.logger.debug(f"UI Update complete. UI Vars: Type={self.global_type_var.get()}, Codec={self.global_codec_var.get()}, Enc={self.global_encoder_var.get()}, Cont={self.container_var.get()}")

        self.logger.debug(f"Updating encoder choices for codec: {codec_to_use}")

        if not codec_to_use:
            self.global_encoder_combo['values'] = []
            self.global_encoder_var.set("")
            return

        # 1. Encodeurs locaux compatibles
        local_encoders_info = FFmpegHelpers.available_encoders() # Expect list of dicts
        compatible_local = [
            f"{enc['name']} - {enc['description']}"
            for enc in local_encoders_info
            if enc.get('codec') == codec_to_use # Use .get for safety
        ]
        
        # Ajouter des encodeurs par défaut si aucun n'est trouvé pour certains codecs
        if not compatible_local:
            fallback_encoders = {
                'webp': 'libwebp - WebP encoder',
                'jpegxl': 'libjxl - JPEG XL encoder', 
                'heic': 'libx265 - HEIC encoder', # Typically uses HEVC encoders
                'avif': 'libaom-av1 - AVIF encoder', # Typically uses AV1 encoders
                'png': 'png - PNG encoder',
                'jpeg': 'mjpeg - Motion JPEG encoder', # or libjpeg
                'h264': 'libx264 - H.264 encoder',
                'hevc': 'libx265 - H.265/HEVC encoder',
                'aac': 'aac - AAC encoder',
                'flac': 'flac - FLAC encoder',
                'mp3': 'libmp3lame - MP3 encoder',
                'opus': 'libopus - Opus encoder',
                # Add other common fallbacks as needed
            }
            if codec_to_use in fallback_encoders:
                compatible_local = [fallback_encoders[codec_to_use]]

        # 2. Encodeurs matériels distants
        remote_encoders_list: list[str] = [] # Renamed to avoid conflict
        if self.distributed_client: # Ensure client exists
            connected_servers = self.distributed_client.get_connected_servers()
            for server in connected_servers:
                # Ensure server status and capabilities are accessible
                server_status_val = getattr(server.status, 'value', None) if hasattr(server, 'status') else None
                if server_status_val == 'online' and hasattr(server, 'capabilities') and server.capabilities:

                    all_hw_encoders_on_server: List[str] = []
                    if hasattr(server.capabilities, 'hardware_encoders') and server.capabilities.hardware_encoders:
                        if isinstance(server.capabilities.hardware_encoders, dict):
                            # e.g. {"nvidia": ["h264_nvenc"], "intel": ["h264_qsv"]}
                            for enc_list_per_type in server.capabilities.hardware_encoders.values():
                                if isinstance(enc_list_per_type, list):
                                    all_hw_encoders_on_server.extend(enc_list_per_type)
                        elif isinstance(server.capabilities.hardware_encoders, list):
                            # e.g. ["h264_nvenc", "hevc_nvenc"]
                            all_hw_encoders_on_server.extend(server.capabilities.hardware_encoders)

                    for hw_enc_name in all_hw_encoders_on_server:
                        # Basic compatibility check - this might need to be more robust,
                        # potentially checking against FFmpeg's known codec <-> encoder mappings.
                        # For now, simple string matching.
                        if codec_to_use in hw_enc_name or \
                           (codec_to_use == 'hevc' and ('h265' in hw_enc_name or 'hevc' in hw_enc_name)) or \
                           (codec_to_use == 'av1' and 'av1' in hw_enc_name) or \
                           (codec_to_use == 'h264' and ('h264' in hw_enc_name or 'avc' in hw_enc_name)):
                            remote_encoders_list.append(f"{server.name}: {hw_enc_name}")

        all_encoders = compatible_local + list(set(remote_encoders_list)) # list(set()) to remove duplicates

        current_encoder_val = self.global_encoder_var.get() # Preserve current if valid
        self.global_encoder_combo['values'] = all_encoders

        if current_encoder_val and current_encoder_val in all_encoders:
            self.global_encoder_var.set(current_encoder_val)
        elif all_encoders:
            self.global_encoder_var.set(all_encoders[0])
        else:
            self.global_encoder_var.set("")

        # Orchestrating method will call subsequent updates like _update_container_choices and _update_quality_preset_controls.

    def _update_ui_for_media_type_and_settings(self, media_type: str, output_config: Optional[OutputConfig] = None, is_global_context: bool = False):
        """
        Orchestrates the update of the encoding settings UI based on the given media type
        and specific output configuration OR global defaults.

        Args:
            media_type: The media type ("video", "audio", "image") to adapt the UI for.
            output_config: The specific OutputConfig of a job to load settings from.
                           If None, UI reflects global defaults for the media_type.
            is_global_context: True if this update is for global settings (e.g. user changed main media type dropdown),
                               False if for a specific job selected in the job combobox.
        """
        self.logger.info(f"Updating UI. Media Type: {media_type}, Job Config: {'Provided' if output_config else 'None'}, Global Context: {is_global_context}")

        # 0. If this is a global context change, ensure AppState reflects the new global media type
        #    and its associated blank codec/encoder values.
        if is_global_context:
            self.global_type_var.set(self.state.global_media_type) # Should match 'media_type' arg
            self.global_codec_var.set(self.state.global_codec)     # Will be blank from AppState
            self.global_encoder_var.set(self.state.global_encoder) # Will be blank from AppState
            self.container_var.set(self.state.global_container)   # Will be blank from AppState

            temp_encoder_for_defaults = self.state.global_encoder
            self.quality_var.set(self._get_optimal_quality_for_codec(temp_encoder_for_defaults, media_type))
            self.bitrate_var.set(self._get_optimal_quality_for_codec(temp_encoder_for_defaults, media_type, mode="bitrate"))
            self.preset_var.set("medium")
            self.video_mode_var.set("quality")
            self.custom_flags_var.set("")

            self.resolution_var_settings.set(self.state.settings.ui.default_resolution or "Keep Original")
            self.crop_top_var.set("0"); self.crop_bottom_var.set("0"); self.crop_left_var.set("0"); self.crop_right_var.set("0")
            self.preserve_hdr_var.set(True); self.tonemap_var.set(False); self.tonemap_method_var.set("hable")
            self.subtitle_mode_var.set("copy"); self.subtitle_path_var.set("")
            self.lut_path_var.set(""); self.watermark_path_var.set("")
            self.watermark_position_var.set("top_right"); self.watermark_scale_var.set(0.1)
            self.watermark_opacity_var.set(1.0); self.watermark_padding_var.set(10)

        # 1. Update visibility of UI sections based on media_type
        self._update_media_type_ui(media_type)

        # 2. Update codec choices based on media_type. This will also set self.global_codec_var.
        self._update_codec_choices(for_media_type=media_type)

        # 3. If a specific job's output_config is provided, load its settings into UI variables
        if output_config and not is_global_context:
            self.logger.debug(f"Loading settings from provided output_config for job: {getattr(output_config, 'name', 'N/A')}")

            job_codec = getattr(output_config, 'codec', '')
            if not job_codec and output_config.encoder:
                encoder_name_lower = output_config.encoder.lower()
                if "264" in encoder_name_lower or "avc" in encoder_name_lower: job_codec = "h264"
                elif "265" in encoder_name_lower or "hevc" in encoder_name_lower: job_codec = "hevc"
                elif "vp9" in encoder_name_lower: job_codec = "vp9"
                elif "av1" in encoder_name_lower: job_codec = "av1"
                elif "aac" in encoder_name_lower: job_codec = "aac"
                elif "mp3" in encoder_name_lower: job_codec = "mp3"
                elif "flac" in encoder_name_lower: job_codec = "flac"
                elif "opus" in encoder_name_lower: job_codec = "opus"
                elif "webp" in encoder_name_lower: job_codec = "webp"
                elif "png" in encoder_name_lower: job_codec = "png"
                elif "jp" in encoder_name_lower: job_codec = "jpeg"
                if not job_codec:
                    available_codecs_for_type = FFmpegHelpers.available_codecs().get(media_type, [])
                    for ac_known in available_codecs_for_type:
                        if ac_known in encoder_name_lower: job_codec = ac_known; break
            self.global_codec_var.set(job_codec if job_codec else "")

            self._update_encoder_choices(for_media_type=media_type, codec=self.global_codec_var.get())

            encoder_name = getattr(output_config, 'encoder', '')
            encoder_display = ""
            if encoder_name:
                for item_value in self.global_encoder_combo['values']:
                    if item_value.startswith(encoder_name): encoder_display = item_value; break
            self.global_encoder_var.set(encoder_display or encoder_name)

            self.container_var.set(getattr(output_config, 'container', ''))
            self.video_mode_var.set(getattr(output_config, 'video_mode', 'quality'))

            quality_val = getattr(output_config, 'quality', '')
            if not quality_val and hasattr(output_config, 'cq_value'): quality_val = getattr(output_config, 'cq_value', '')
            self.quality_var.set(str(quality_val))

            self.bitrate_var.set(str(getattr(output_config, 'bitrate', '')))
            self.multipass_var.set(getattr(output_config, 'multipass', False))
            self.preset_var.set(getattr(output_config, 'preset', 'medium'))
            self.custom_flags_var.set(getattr(output_config, 'custom_flags', ''))

            self.resolution_var_settings.set(getattr(output_config, 'resolution', "Keep Original"))
            crop_settings = getattr(output_config, 'crop', {})
            self.crop_top_var.set(str(crop_settings.get("top", "0"))); self.crop_bottom_var.set(str(crop_settings.get("bottom", "0")))
            self.crop_left_var.set(str(crop_settings.get("left", "0"))); self.crop_right_var.set(str(crop_settings.get("right", "0")))

            self.preserve_hdr_var.set(getattr(output_config, 'preserve_hdr', True))
            self.tonemap_var.set(getattr(output_config, 'tonemap', False))
            self.tonemap_method_var.set(getattr(output_config, 'tonemap_method', "hable"))

            self.subtitle_mode_var.set(getattr(output_config, 'subtitle_mode', "copy"))
            self.subtitle_path_var.set(getattr(output_config, 'subtitle_path', ""))

            self.lut_path_var.set(getattr(output_config, 'lut_path', ""))
            watermark_cfg = getattr(output_config, 'watermark', {})
            self.watermark_path_var.set(watermark_cfg.get('path', "")); self.watermark_position_var.set(watermark_cfg.get('position', "top_right"))
            self.watermark_scale_var.set(float(watermark_cfg.get('scale', 0.1))); self.watermark_opacity_var.set(float(watermark_cfg.get('opacity', 1.0)))
            self.watermark_padding_var.set(int(watermark_cfg.get('padding', 10)))
        else:
            self._update_encoder_choices(for_media_type=media_type, codec=self.global_codec_var.get())

        self._update_container_choices(for_media_type=media_type)
        self._update_quality_preset_controls(encoder=self.global_encoder_var.get())

        effective_output_config_for_quality = output_config if output_config and not is_global_context else None
        self._update_quality_controls_ui(media_type=media_type,
                                         encoder=self.global_encoder_var.get(),
                                         codec=self.global_codec_var.get(),
                                         output_config=effective_output_config_for_quality)

        self._on_video_mode_change()
        self._on_tonemap_change()
        self._on_subtitle_mode_change()
        self._on_resolution_change()

        self.logger.debug(f"UI Update complete. UI Vars: Type={self.global_type_var.get()}, Codec={self.global_codec_var.get()}, Enc={self.global_encoder_var.get()}, Cont={self.container_var.get()}")

    def _update_quality_controls_ui(self, media_type: Optional[str] = None, encoder: Optional[str] = None, codec: Optional[str] = None, output_config: Optional[OutputConfig] = None):
        """Met à jour les contrôles de qualité en fonction du type de média, de l'encodeur sélectionné et de la configuration existante."""
        media_type_to_use = media_type if media_type is not None else self.global_type_var.get()
        encoder_to_use = self._get_encoder_name_from_display(encoder if encoder is not None else self.global_encoder_var.get())
        codec_to_use = codec if codec is not None else self.global_codec_var.get()
        self.logger.debug(f"Updating quality controls UI for media: {media_type_to_use}, encoder: {encoder_to_use}, codec: {codec_to_use}")

        # Default states for all controls in the quality frame
        self.video_mode_radio_quality.config(state="disabled", text="Qualité (N/A)")
        self.cq_entry.config(state="disabled")
        self.video_mode_radio_bitrate.config(state="disabled", text="Bitrate (N/A)")
        self.bitrate_entry.config(state="disabled")
        self.multipass_check.config(state="disabled")
        self.preset_label.grid_remove() # Hide by default
        self.quality_entry.grid_remove() # Hide by default (this is the encoder preset combobox)

        # Determine current settings from output_config or use optimal defaults
        # output_config takes precedence if available for a specific job's settings
        current_video_mode = getattr(output_config, 'video_mode', 'quality') if output_config else 'quality'

        current_quality_val = None
        if output_config:
            current_quality_val = getattr(output_config, 'quality', None)
            if current_quality_val is None and hasattr(output_config, 'cq_value'): # Legacy
                 current_quality_val = getattr(output_config, 'cq_value', None)
        if current_quality_val is None: # Fallback to optimal if not in output_config
            current_quality_val = self._get_optimal_quality_for_codec(encoder_to_use, media_type_to_use, mode="quality")

        current_bitrate_val = getattr(output_config, 'bitrate', None) if output_config else None
        if current_bitrate_val is None: # Fallback to optimal
            current_bitrate_val = self._get_optimal_quality_for_codec(encoder_to_use, media_type_to_use, mode="bitrate")

        self.video_mode_var.set(current_video_mode)
        self.quality_var.set(str(current_quality_val))
        self.bitrate_var.set(str(current_bitrate_val))
        if output_config:
             self.multipass_var.set(getattr(output_config, 'multipass', False))
             self.preset_var.set(getattr(output_config, 'preset', 'medium')) # Encoder preset
        else: # Defaults for global context
             self.multipass_var.set(False)
             self.preset_var.set(self._get_optimal_quality_for_codec(encoder_to_use, media_type_to_use, mode="preset"))


        if media_type_to_use == "video":
            self.video_mode_radio_quality.config(state="normal")
            self.video_mode_radio_bitrate.config(state="normal", text="Bitrate (kbps)")
            self.multipass_check.config(state="normal")
            self.preset_label.grid()
            self.quality_entry.grid() # Show encoder preset combobox

            # Set appropriate label for quality/CQ/CRF
            if any(e in encoder_to_use for e in ['nvenc', 'qsv', 'amf', 'videotoolbox']):
                self.video_mode_radio_quality.config(text="Qualité Cible (CQ/ICQ)")
            else:
                self.video_mode_radio_quality.config(text="Qualité Cible (CRF)")

        elif media_type_to_use == "audio":
            self.video_mode_radio_bitrate.config(state="normal", text="Bitrate (kbps)") # Always allow bitrate for audio

            if 'flac' in encoder_to_use:
                self.video_mode_radio_quality.config(text="Compression (0-12)", state="normal")
                self.video_mode_radio_bitrate.config(state="disabled") # FLAC is lossless
                self.bitrate_entry.config(state="disabled")
                if not output_config: self.video_mode_var.set("quality") # Default to compression for FLAC
            elif any(c in encoder_to_use for c in ['aac', 'libopus', 'libvorbis', 'libmp3lame']):
                self.video_mode_radio_quality.config(text="Qualité VBR (varies)", state="normal")
                # VBR quality scale depends on codec, e.g. LAME -V (0-9), Vorbis -q (0-10), fdk_aac -vbr (1-5)
            else: # e.g., PCM like 'wav'
                self.video_mode_radio_quality.config(text="Qualité (N/A)", state="disabled")
                # Bitrate might still be relevant for PCM (e.g. sample_rate * bit_depth * channels) but not user-settable here usually
                self.video_mode_radio_bitrate.config(state="disabled")
                self.bitrate_entry.config(state="disabled")


        elif media_type_to_use == "image":
            self.video_mode_radio_quality.config(state="normal") # Quality mode is primary
            if not output_config: self.video_mode_var.set("quality")

            q_text = "Qualité (0-100)" # Generic default
            if 'webp' in encoder_to_use: q_text = "WebP Qualité (0-100)"
            elif 'avif' in encoder_to_use: q_text = "AVIF Qualité (CQ 0-63)"
            elif 'jpeg' in encoder_to_use or 'mjpeg' in encoder_to_use : q_text = "JPEG Qualité (1-100)"
            elif 'jpegxl' in encoder_to_use or 'jxl' in encoder_to_use: q_text = "JXL Distance (0-15)"
            elif 'heic' in encoder_to_use: q_text = "HEIC Qualité (CRF 0-51)"
            elif 'png' in encoder_to_use: q_text = "PNG Compression (0-100)"
            self.video_mode_radio_quality.config(text=q_text)
        
        # This call will enable/disable the actual entry fields based on the radio button selection
        self._on_video_mode_change()

    def _get_optimal_quality_for_codec(self, encoder: str, media_type: str, mode: str = "quality") -> str:
        """Retourne une valeur de qualité/paramètre optimal par défaut pour un encodeur et un type de média."""
        encoder_lower = encoder.lower() if encoder else "" # Handle empty encoder string

        if media_type == "video":
            if mode == "bitrate": return "4000"
            if any(e in encoder_lower for e in ['x264', 'libx264', 'x265', 'libx265']): return "22"
            if any(e in encoder_lower for e in ['nvenc_h264', 'nvenc_hevc']): return "20" # NVIDIA CQ
            if any(e in encoder_lower for e in ['h264_qsv', 'hevc_qsv']): return "23" # Intel QSV CQP/ICQ
            if any(e in encoder_lower for e in ['h264_amf', 'hevc_amf']): return "23" # AMD AMF
            if any(e in encoder_lower for e in ['h264_videotoolbox', 'hevc_videotoolbox']): return "75" # VT Quality 0-100
            if 'libaom-av1' in encoder_lower or 'rav1e' in encoder_lower or 'svt_av1' in encoder_lower: return "30" # AV1 CRF (higher can be good)
            if 'libvpx-vp9' in encoder_lower: return "32" # VP9 CRF
            return "22" # General video fallback

        elif media_type == "audio":
            if 'flac' in encoder_lower: return "8" # FLAC compression level 0-12
            if mode == "bitrate":
                if 'aac' in encoder_lower: return "160"
                if 'libmp3lame' in encoder_lower: return "192"
                if 'libopus' in encoder_lower: return "128"
                if 'libvorbis' in encoder_lower: return "160"
                return "128" # Default bitrate for other audio
            else: # VBR quality mode
                if 'libmp3lame' in encoder_lower: return "2"  # LAME VBR quality -V 2
                if 'libvorbis' in encoder_lower: return "6"   # Vorbis VBR quality -q:a 6
                # For AAC (libfdk_aac), VBR is 1-5. For FFmpeg's internal aac, -q:a (e.g., 2 is good).
                # Opus is best controlled by bitrate or specific flags; simple VBR quality number is less common.
                # For simplicity, if not FLAC and not bitrate mode, might return empty or a general VBR indicator.
                if 'aac' in encoder_lower : return "4" # Assuming a general VBR scale, e.g. for libfdk_aac
                return "" # No simple VBR quality number for other codecs by default

        elif media_type == "image":
            # Quality mode is primary for images
            if 'libwebp' in encoder_lower: return "90"  # WebP quality 0-100 (100 can be lossless-ish)
            if 'libavif' in encoder_lower or 'avif' in encoder_lower : return "25"  # AVIF CQ for libaom (0-63, lower is better)
            if 'jpeg' in encoder_lower or 'mjpeg' in encoder_lower: return "85" # JPEG quality (often 1-100 or qscale 2-31)
            if 'libjpegxl' in encoder_lower or 'jxl' in encoder_lower: return "1.0" # JXL distance (0-15, lower is better, 0=lossless)
            if 'heic' in encoder_lower or 'libheif' in encoder_lower : return "28" # HEIC CRF (like HEVC)
            if 'png' in encoder_lower: return "90" # FFmpeg's -compression_level 0-100.
            return "85" # General image quality fallback

        return "22" # Overall fallback, should ideally not be reached if types are handled.

    def _on_ui_setting_changed(self, *args):
        """
        Callback appelé quand les paramètres d'interface (StringVar, BooleanVar, etc. for encoding settings) changent.
        Si in global context (no specific job selected for settings), it synchronises these UI values
        with the AppState's global encoding settings.
        """
        # Determine if a specific job is selected for configuration
        is_job_specific_context = bool(self.selected_job_for_settings_var.get())

        if is_job_specific_context:
            # Changes are for the selected job, not global defaults.
            # These changes are staged in the UI vars. Applying them to the actual job object
            # typically happens via an "Apply" button or when the job is processed.
            self.logger.debug("UI setting changed in job-specific context. No AppState.global_settings update.")
            return

        # If here, no specific job is selected, so changes affect global AppState defaults.
        self.logger.debug("UI setting changed in global context. Updating AppState global encoding settings.")
        try:
            updates = {}
            # Collect all relevant global settings from their UI variables
            # Note: global_type_var change is handled by _on_media_type_change directly updating controller/appstate.
            # We only need to sync settings that are *downstream* of media type.

            # if hasattr(self, 'global_type_var'): # This should reflect AppState.global_media_type
            #     updates['media_type'] = self.global_type_var.get() # Usually not needed here due to _on_media_type_change
            
            if hasattr(self, 'global_codec_var') and self.global_codec_var.get() != self.state.global_codec:
                updates['codec'] = self.global_codec_var.get()
            if hasattr(self, 'global_encoder_var') and self.global_encoder_var.get() != self.state.global_encoder:
                updates['encoder'] = self._get_encoder_name_from_display(self.global_encoder_var.get()) # Store short name
            if hasattr(self, 'container_var') and self.container_var.get() != self.state.global_container:
                updates['container'] = self.container_var.get()

            # Quality related settings - these depend on video_mode_var
            current_video_mode = self.video_mode_var.get() # quality or bitrate
            if current_video_mode == "quality":
                if hasattr(self, 'quality_var') and self.quality_var.get() != self.state.global_quality:
                     updates['quality'] = self.quality_var.get()
                # When in quality mode, bitrate in AppState might be nulled or kept, depends on desired behavior
                # updates['bitrate'] = "" # Or some indicator that it's not active
            elif current_video_mode == "bitrate":
                if hasattr(self, 'bitrate_var') and self.bitrate_var.get() != self.state.global_bitrate:
                    updates['bitrate'] = self.bitrate_var.get()
                # updates['quality'] = "" # Or some indicator

            if hasattr(self, 'preset_var') and self.preset_var.get() != self.state.global_preset: # This is encoder preset
                updates['preset'] = self.preset_var.get()
            if hasattr(self, 'multipass_var') and self.multipass_var.get() != self.state.global_multipass:
                updates['multipass'] = self.multipass_var.get()

            if updates: # Only update if there are actual changes to global settings
                self.logger.info(f"Pushing UI changes to AppState global settings: {updates}")
                self.state.update_global_encoding_settings(**updates)
            else:
                self.logger.debug("No actual changes in UI to push to AppState global settings.")
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la synchronisation des paramètres globaux via _on_ui_setting_changed: {e}", exc_info=True)
