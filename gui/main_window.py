import json
import os
import subprocess
import threading
import sys
from pathlib import Path
from tkinter import (Tk, filedialog, ttk, Menu, messagebox, StringVar, BooleanVar, Text, Toplevel, IntVar, DoubleVar, simpledialog, Listbox, Scrollbar, Frame, Canvas, Checkbutton)
import tkinter as tk

#j'ajoute dynamiquement le chemin racine du projet au PYTHONPATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.encode_job import EncodeJob, OutputConfig
from core.ffmpeg_helpers import FFmpegHelpers
from core.settings import Settings
from core.worker_pool import WorkerPool
from gui.settings_window import SettingsWindow
from gui.job_edit_window import JobEditWindow
from gui.log_viewer_window import LogViewerWindow
from gui.batch_operations_window import BatchOperationsWindow
from gui.advanced_filters_window import AdvancedFiltersWindow
from gui.audio_tracks_window import AudioTracksWindow
from gui.folder_watcher import FolderWatcher


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
        self.transient(parent) # Display on top of the parent window
        # self.grab_set() # Make modal: this would prevent clicking outside. We want non-modal.

        # Remove window decorations (title bar, close button) for a more "popup" feel
        # self.overrideredirect(True) # This makes it hard to move/close if something goes wrong

        main_frame = ttk.Frame(self, padding="10", style="InfoDialog.TFrame")
        main_frame.pack(expand=True, fill=tk.BOTH)

        ttk.Label(main_frame, text=message, wraplength=350, anchor="center").pack(padx=20, pady=10)

        button_frame = ttk.Frame(main_frame, style="InfoDialog.TFrame")
        button_frame.pack(pady=(0, 10))
        ok_button = ttk.Button(button_frame, text="OK", command=self._dismiss_dialog, style="InfoDialog.TButton")
        ok_button.pack()

        # Style for the dialog (optional, but can make it look nicer)
        s = ttk.Style()
        s.configure("InfoDialog.TFrame", background="#f0f0f0") # Light gray background
        s.configure("InfoDialog.TButton", padding=5)


        # Center the dialog on the parent
        self.update_idletasks() # Ensure dimensions are calculated
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

        # This is the core of "click outside to dismiss"
        # We bind to the entire application's root window for any click.
        self._root_click_handler_id = parent.winfo_toplevel().bind("<Button-1>", self._handle_root_click, add="+")

        if auto_dismiss_ms:
            self._auto_dismiss_timer = self.after(auto_dismiss_ms, self._dismiss_dialog)

        ok_button.focus_set()
        # self.protocol("WM_DELETE_WINDOW", self._dismiss_dialog) # Handle window close button if not overrideredirect

    def _handle_root_click(self, event):
        # Check if the click was outside this dialog
        # event.widget is the widget that was clicked.
        # Check if the top-level window of the clicked widget is this dialog.
        clicked_toplevel = event.widget.winfo_toplevel()
        if clicked_toplevel != self:
            self._dismiss_dialog()

    def _dismiss_dialog(self):
        if hasattr(self, '_auto_dismiss_timer'):
            self.after_cancel(self._auto_dismiss_timer)

        # Unbind the global click handler
        if hasattr(self, '_root_click_handler_id'):
            parent_toplevel = self.master.winfo_toplevel()
            parent_toplevel.unbind("<Button-1>", self._root_click_handler_id)
            delattr(self, '_root_click_handler_id') # Important to prevent errors if dismissed multiple ways

        if self.winfo_exists(): # Check if window still exists before destroying
            self.destroy()

class MainWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("FFmpeg Frontend")
        self.root.geometry("1200x800")
        
        # Liste des jobs
        self.jobs: list[EncodeJob] = []
        self.job_rows = {}
        self.last_import_job_ids: list[str] = [] # Store IDs of jobs from the last import operation
        
        # Variables d'état
        self.is_running = False
        self.input_folder = StringVar()
        self.output_folder = StringVar()
        
        # Variable pour la surveillance de dossier
        self.watch_var = BooleanVar(value=False)
        self.log_viewer = None
        
        # Variables manquantes qui causent des erreurs
        self.cq_var = StringVar(value="22")
        self.trim_start_var = StringVar(value="00:00:00")
        self.trim_end_var = StringVar(value="00:00:00")
        
        # Variables HDR
        self.hdr_detected_var = BooleanVar(value=False)
        self.tonemap_var = BooleanVar(value=False)
        self.tonemap_method_var = StringVar(value="hable")
        self.preserve_hdr_var = BooleanVar(value=True)
        
        # Variables pour l'inspecteur média
        self.resolution_var = StringVar(value="N/A")
        self.duration_var = StringVar(value="N/A")
        self.vcodec_var = StringVar(value="N/A")
        self.vbitrate_var = StringVar(value="N/A")
        self.acodec_var = StringVar(value="N/A")
        self.abitrate_var = StringVar(value="N/A")
        self.achannels_var = StringVar(value="N/A")
        
        # Action post-encodage
        self.post_encode_action_var = StringVar(value="rien")
        
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
        
        # Initialiser les valeurs par défaut après que tous les éléments UI soient créés
        try:
            self._update_codec_choices()
        except Exception:
            # Ignorer les erreurs d'initialisation pour l'instant
            pass
        
        # Démarrer le pool
        self.pool.start()

    # === GUI construction ===
    def _build_layout(self):
        """Construit l'interface utilisateur principale"""
        # Création du layout principal avec des panneaux
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Panneau gauche pour les paramètres
        left_frame = ttk.Frame(main_paned)
        main_paned.add(left_frame, weight=1)
        
        # Panneau droit pour la queue et l'inspecteur
        right_paned = ttk.PanedWindow(main_paned, orient=tk.VERTICAL)
        main_paned.add(right_paned, weight=2)
        
        # === Panneau gauche: Paramètres ===
        # Section de sélection des fichiers
        self._build_file_section(left_frame)
        
        # Section des paramètres d'encodage
        encoding_frame = ttk.LabelFrame(left_frame, text="Paramètres d'encodage", padding="10")
        # Allow encoding_frame to expand vertically as well
        encoding_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self._build_encoding_section(encoding_frame)
        
        # === Panneau droit supérieur: Queue des jobs ===
        queue_frame = ttk.LabelFrame(right_paned, text="Queue d'encodage", padding="5")
        right_paned.add(queue_frame, weight=3)
        
        # Treeview pour la queue
        tree_frame = ttk.Frame(queue_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        # Colonnes du treeview
        columns = ("Fichier", "Codec", "Qualité", "Progrès", "Statut")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10)
        
        # Configuration des colonnes
        for col in columns:
            self.tree.heading(col, text=col)
            if col == "Fichier":
                self.tree.column(col, width=200)
            elif col == "Progrès":
                self.tree.column(col, width=80)
            else:
                self.tree.column(col, width=100)
        
        # Scrollbars pour le treeview
        tree_scrollbar_v = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scrollbar_h = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scrollbar_v.set, xscrollcommand=tree_scrollbar_h.set)
        
        # Pack du treeview et des scrollbars
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scrollbar_v.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scrollbar_h.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Liaisons d'événements pour le treeview
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)  # Clic droit
        self.tree.bind("<<TreeviewSelect>>", self._on_queue_selection_change)
        
        # Boutons de contrôle
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
        
        # Menu contextuel pour la queue
        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Modifier", command=self._edit_selected_job)
        self.context_menu.add_command(label="Dupliquer", command=self._duplicate_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Pause", command=self._pause_selected_job)
        self.context_menu.add_command(label="Resume", command=self._resume_selected_job)
        self.context_menu.add_command(label="Cancel", command=self._cancel_selected_job)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Supprimer", command=self._remove_selected_job)
        
        # Barre de progression globale
        progress_frame = ttk.Frame(queue_frame)
        progress_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(progress_frame, text="Progrès global:").pack(side=tk.LEFT)
        self.progress_bar = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        
        # === Panneau droit inférieur: Inspecteur ===
        inspector_frame = ttk.LabelFrame(right_paned, text="Inspecteur de média", padding="5")
        right_paned.add(inspector_frame, weight=2)
        
        # Treeview pour la sélection de fichier dans l'inspecteur
        inspector_tree_frame = ttk.Frame(inspector_frame)
        inspector_tree_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(inspector_tree_frame, text="Fichier:").pack(side=tk.LEFT)
        self.inspector_tree = ttk.Treeview(inspector_tree_frame, columns=("name",), show="headings", height=3)
        self.inspector_tree.heading("name", text="Nom du fichier")
        self.inspector_tree.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        
        # Frame pour les informations du média
        self.inspector_info_frame = ttk.Frame(inspector_frame)
        self.inspector_info_frame.pack(fill=tk.BOTH, expand=True)
        
        # Menu contextuel pour la queue
        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Edit Job", command=self._edit_selected_job)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Pause", command=self._pause_selected_job)
        self.context_menu.add_command(label="Resume", command=self._resume_selected_job)
        self.context_menu.add_command(label="Cancel", command=self._cancel_selected_job)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Remove", command=self._remove_selected_job)
        
        # Binding des événements
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_queue_selection_change)
        self.inspector_tree.bind("<<TreeviewSelect>>", self._on_inspector_selection_change)

    def _build_encoding_section(self, parent):
        """Construit la section des paramètres d'encodage de manière plus logique."""
        # --- Variables d'état pour les paramètres ---
        self.selected_file_var = StringVar(value="No file selected")
        self.preset_name_var = StringVar()
        self.selected_job_for_settings_var = StringVar() # For the new combobox
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

        # --- Cadre principal pour les réglages (avec scroll intelligent) ---
        # Créer un cadre avec scroll seulement si le contenu dépasse la hauteur
        canvas_frame = ttk.Frame(parent)
        # Make canvas_frame expand vertically within its parent (encoding_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        # Canvas et scrollbar pour scroll uniquement si nécessaire
        # Remove fixed height, let it expand with canvas_frame
        self.settings_canvas = tk.Canvas(canvas_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.settings_canvas.yview)
        # main_frame is the content frame INSIDE the canvas
        main_frame = ttk.Frame(self.settings_canvas)
        
        # Configuration du scroll
        # When main_frame's size changes, update canvas scrollregion
        main_frame.bind('<Configure>', self._on_frame_configure)
        # When settings_canvas's size changes, update main_frame's width and scroll state
        self.settings_canvas.bind('<Configure>', self._on_canvas_configure)
        
        # Créer la fenêtre dans le canvas
        # This embeds main_frame into the canvas
        canvas_window = self.settings_canvas.create_window((0, 0), window=main_frame, anchor="nw")
        self.settings_canvas.configure(yscrollcommand=scrollbar.set)
        
        # Pack canvas et scrollbar
        # Canvas should fill and expand
        self.settings_canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        # Scrollbar will be managed by _update_scroll_state
        # scrollbar.pack(side="right", fill="y") # Initially hidden, shown by _update_scroll_state
        
        # Variables pour gérer le scroll intelligent
        self._canvas_window = canvas_window
        self._scrollbar = scrollbar
        
        # Configurer le scrolling avec la molette
        def _on_mousewheel(event):
            # La direction et l'amplitude du défilement varient selon la plateforme
            # Sur Windows, event.delta est un multiple de 120.
            # Sur macOS, event.delta est le nombre de "lignes" à faire défiler.
            # Sur Linux, on utilise Button-4 et Button-5.
            
            if sys.platform == "darwin": # Explicitly for macOS
                # event.delta for macOS trackpad can be small, continuous values.
                # Negative delta for scrolling up, positive for scrolling down.
                # yview_scroll positive for down, negative for up.
                # Add a print statement to debug delta values on macOS
                # print(f"macOS scroll delta: {event.delta}")
                self.settings_canvas.yview_scroll(int(-1 * event.delta), "units")
            elif sys.platform == "win32": # Windows
                self.settings_canvas.yview_scroll(-1 * (event.delta // 120), "units")
            # For Linux (event.num)
            elif hasattr(event, 'num'): # Check for event.num for Linux
                if event.num == 4:
                    self.settings_canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    self.settings_canvas.yview_scroll(1, "units")
            # Fallback for other <MouseWheel> events that might have .delta
            elif hasattr(event, 'delta') and event.delta != 0:
                 self.settings_canvas.yview_scroll(int(-1 * event.delta), "units")

        def bind_to_mousewheel(widget):
            widget.bind("<MouseWheel>", _on_mousewheel)
            widget.bind("<Button-4>", _on_mousewheel) # For Linux scroll up
            widget.bind("<Button-5>", _on_mousewheel) # For Linux scroll down

        # Lier l'événement de la molette au canvas et au frame principal
        bind_to_mousewheel(self.settings_canvas)
        bind_to_mousewheel(main_frame)

        # Quand le curseur entre dans le canvas, il prend le focus pour le scroll
        self.settings_canvas.bind("<Enter>", lambda e: self.settings_canvas.focus_set())
        
        # Lier récursivement la molette à tous les widgets enfants
        def bind_recursive(widget):
            bind_to_mousewheel(widget)
            for child in widget.winfo_children():
                bind_recursive(child)
        
        main_frame.after(100, lambda: bind_recursive(main_frame))
        

        # --- Structure de l'UI ---

        # 0. File Selector and Apply Buttons
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


        # 1. Préréglages (Presets)
        preset_frame = ttk.LabelFrame(main_frame, text="Préréglage", padding="5")
        preset_frame.pack(fill=tk.X, pady=(0, 5))
        self.preset_combo = ttk.Combobox(preset_frame, textvariable=self.preset_name_var, state="readonly")
        self.preset_combo.pack(fill=tk.X, expand=True)
        self.preset_combo.bind("<<ComboboxSelected>>", self._load_preset)

        # 2. Type de Média
        media_type_frame = ttk.LabelFrame(main_frame, text="Type de Média", padding="5")
        media_type_frame.pack(fill=tk.X, pady=(0, 5))
        self.media_type_combo = ttk.Combobox(media_type_frame, textvariable=self.global_type_var, values=["video", "audio", "image"], state="readonly")
        self.media_type_combo.pack(fill=tk.X, expand=True)
        self.media_type_combo.bind("<<ComboboxSelected>>", self._on_media_type_change)

        # 3. Résolution et Rognage
        self.transform_frame = ttk.LabelFrame(main_frame, text="Taille et Rognage", padding="5")
        self.transform_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(self.transform_frame, text="Résolution:").grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.resolution_combo = ttk.Combobox(self.transform_frame, textvariable=self.resolution_var_settings, state="readonly")
        self.resolution_combo.grid(row=0, column=1, columnspan=3, sticky="ew", pady=(0, 5))
        self.resolution_combo.bind("<<ComboboxSelected>>", self._on_resolution_change)
        
        # Frame pour la résolution personnalisée (largeur x hauteur)
        self.custom_resolution_frame = ttk.Frame(self.transform_frame)
        self.width_var = StringVar()
        self.height_var = StringVar()
        self.width_entry = ttk.Entry(self.custom_resolution_frame, textvariable=self.width_var, width=6)
        ttk.Label(self.custom_resolution_frame, text="x").pack(side='left')
        self.height_entry = ttk.Entry(self.custom_resolution_frame, textvariable=self.height_var, width=6)
        
        # Initialiser les valeurs de résolution communes
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
        
        # 4. Format et Codec
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
        
        # 5. Qualité
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

        # 6. HDR et Color Management
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
        
        # 7. Sous-titres
        self.subtitle_frame = ttk.LabelFrame(main_frame, text="Sous-titres", padding="5")
        self.subtitle_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(self.subtitle_frame, text="Mode:").grid(row=0, column=0, sticky="w", pady=2)
        self.subtitle_mode_combo = ttk.Combobox(self.subtitle_frame, textvariable=self.subtitle_mode_var,
                                               values=["copy", "burn", "remove", "embed"], 
                                               state="readonly", width=10)
        self.subtitle_mode_combo.grid(row=0, column=1, sticky="w", pady=2)
        self.subtitle_mode_combo.bind("<<ComboboxSelected>>", self._on_subtitle_mode_change)
        
        # Frame pour fichier externe de sous-titres
        self.external_subtitle_frame = ttk.Frame(self.subtitle_frame)
        self.external_subtitle_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=2)
        
        ttk.Label(self.external_subtitle_frame, text="Fichier externe:").pack(side='left', padx=(0, 5))
        self.subtitle_path_entry = ttk.Entry(self.external_subtitle_frame, textvariable=self.subtitle_path_var, width=30)
        self.subtitle_path_entry.pack(side='left', expand=True, fill='x', padx=(0, 5))
        self.subtitle_browse_button = ttk.Button(self.external_subtitle_frame, text="...", command=self._browse_subtitle_file, width=3)
        self.subtitle_browse_button.pack(side='left')

        # 8. LUT Application
        self.lut_frame = ttk.LabelFrame(main_frame, text="Effets (LUT)", padding="5")
        self.lut_frame.pack(fill=tk.X, pady=(0,5))

        self.lut_path_var = StringVar()
        ttk.Label(self.lut_frame, text="Fichier LUT (.cube, .look, .3dl):").grid(row=0, column=0, sticky="w", pady=2)
        self.lut_path_entry = ttk.Entry(self.lut_frame, textvariable=self.lut_path_var, width=40)
        self.lut_path_entry.grid(row=1, column=0, sticky="ew", pady=2, padx=(0,5))
        self.lut_browse_button = ttk.Button(self.lut_frame, text="...", command=self._browse_lut_file, width=3)
        self.lut_browse_button.grid(row=1, column=1, sticky="w", pady=2)

        ttk.Separator(self.lut_frame, orient=tk.HORIZONTAL).grid(row=2, column=0, columnspan=2, sticky="ew", pady=5)

        # Watermark UI Elements
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

        self.lut_frame.columnconfigure(0, weight=1) # Ensure first column expands

        # Ajuster la configuration des colonnes pour s'adapter au contenu
        self.transform_frame.columnconfigure(1, weight=1)
        self.transform_frame.columnconfigure(3, weight=1)
        format_frame.columnconfigure(1, weight=1)
        self.quality_frame.columnconfigure(2, weight=1)
        self.subtitle_frame.columnconfigure(2, weight=1)
        
        # Initialisation des valeurs
        self._on_video_mode_change() # Pour cacher/afficher les bons contrôles
        self._on_tonemap_change() # Pour désactiver la méthode au début
        
        # Appliquer l'UI selon le type de média par défaut
        initial_media_type = self.global_type_var.get() or "video"
        self._update_media_type_ui(initial_media_type)
        
        # Mise à jour initiale du scroll
        self.root.after(100, self._update_scroll_state)
        
    def _on_media_type_change(self, event=None):
        """Met à jour les choix de l'UI quand le type de média change."""
        media_type = self.global_type_var.get()
        self._update_codec_choices()
        self._update_media_type_ui(media_type)
    
    def _on_codec_change(self, event=None):
        """Appelé quand le codec change pour mettre à jour les encodeurs disponibles."""
        self._update_encoder_choices()
        self._update_container_choices()
    
    def _on_encoder_change(self, event=None):
        """Appelé quand l'encodeur change pour mettre à jour les presets."""
        self._update_quality_preset_controls()
    
    def _on_tonemap_change(self):
        """Active ou désactive la méthode de tone mapping."""
        if self.tonemap_var.get():
            self.tonemap_method_combo.config(state="readonly")
            self.preserve_hdr_var.set(False)  # Désactiver préservation HDR si tone mapping actif
        else:
            self.tonemap_method_combo.config(state="disabled")
    
    def _on_frame_configure(self, event):
        """Met à jour la zone de défilement quand le frame change de taille."""
        self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))
        self._update_scroll_state()
    
    def _on_canvas_configure(self, event):
        """Met à jour la largeur du frame intérieur pour s'adapter au canvas."""
        canvas_width = event.width
        self.settings_canvas.itemconfig(self._canvas_window, width=canvas_width)
        self._update_scroll_state()
    
    def _update_scroll_state(self):
        """Affiche ou cache la scrollbar selon si le contenu dépasse la hauteur disponible."""
        # Mettre à jour la région de défilement
        self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))
        
        # Vérifier si le contenu dépasse la hauteur visible
        bbox = self.settings_canvas.bbox("all")
        if bbox:
            content_height = bbox[3] - bbox[1]
            canvas_height = self.settings_canvas.winfo_height()
            
            if content_height > canvas_height:
                # Afficher la scrollbar
                self._scrollbar.pack(side="right", fill="y")
            else:
                # Cacher la scrollbar
                self._scrollbar.pack_forget()
    
    def _update_resolution_choices(self):
        """Met à jour les choix de résolution avec des valeurs communes incluant les formats verticaux."""
        resolution_choices = [
            "Keep Original",
            # Formats horizontaux standards
            "3840x2160 (4K)",
            "2560x1440 (1440p)",
            "1920x1080 (1080p)",
            "1280x720 (720p)",
            "854x480 (480p)",
            "640x360 (360p)",
            # Formats verticaux (pour mobile/TikTok/Instagram Stories)
            "1080x1920 (1080p Portrait)",
            "720x1280 (720p Portrait)",
            "480x854 (480p Portrait)",
            "540x960 (TikTok/Stories)",
            "1125x2000 (Instagram Stories)",
            # Formats ultra-larges
            "3440x1440 (Ultrawide 1440p)",
            "2560x1080 (Ultrawide 1080p)",
            # Formats classiques
            "1920x1200 (WUXGA)",
            "1680x1050 (WSXGA+)",
            "1440x900 (WXGA+)",
            "1280x800 (WXGA)",
            "Custom"
        ]
        
        self.resolution_combo['values'] = resolution_choices
        if not self.resolution_var_settings.get():
            self.resolution_var_settings.set("Keep Original")

    def _apply_settings_to_selected_file(self):
        """Applique les réglages actuels de l'UI au fichier sélectionné"""
        selected_filename = self.selected_file_var.get()
        if selected_filename == "No file selected" or not selected_filename:
            messagebox.showwarning("No File Selected", "Please select a file to apply settings to.")
            return
        
        # Trouver le job correspondant au fichier sélectionné
        target_job = None
        for job_id, job_data in self.job_rows.items():
            job = job_data["job"]
            if job.src_path.name == selected_filename:
                target_job = job
                break
        
        if not target_job:
            messagebox.showerror("Job Not Found", "Could not find the job for the selected file.")
            return
        
        # Appliquer les réglages de l'UI au job
        self._apply_ui_settings_to_job(target_job)
        
        # Mettre à jour l'affichage dans le treeview
        self._update_job_row(target_job)
        
        # Confirmation pour l'utilisateur
        messagebox.showinfo("Settings Applied", f"Settings have been applied to '{selected_filename}'")

    def _apply_settings_to_all_files(self):
        """Applique les réglages actuels à tous les fichiers de la queue"""
        if not self.jobs:
            messagebox.showwarning("No Files", "No files in the queue to apply settings to.")
            return
        
        # Demander confirmation
        result = messagebox.askyesno(
            "Apply to All", 
            f"Apply current settings to all {len(self.jobs)} files in the queue?",
            icon='question'
        )
        
        if not result:
            return
        
        # Appliquer les réglages à tous les jobs
        for job in self.jobs:
            self._apply_ui_settings_to_job(job)
            self._update_job_row(job)
        
        messagebox.showinfo("Settings Applied", f"Settings have been applied to all {len(self.jobs)} files")

    def _reset_settings_ui(self):
        """Remet à zéro tous les réglages de l'interface"""
        # Demander confirmation
        result = messagebox.askyesno(
            "Reset Settings", 
            "Reset all encoding settings to default values?",
            icon='question'
        )
        
        if not result:
            return
        
        # Réinitialiser toutes les variables
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
        
        # Mettre à jour les choix disponibles
        self._update_codec_choices()
        
        messagebox.showinfo("Settings Reset", "All encoding settings have been reset to default values")

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
        edit_menu.add_command(label="Subtitles...", command=self._manage_subtitles) # New line
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear Queue", command=self._clear_queue)
        edit_menu.add_separator()
        edit_menu.add_command(label="Merge Videos", command=self._merge_videos)
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

    def _build_file_section(self, parent_frame):
        # --- FILE SELECTION SECTION ---
        self.input_folder = StringVar(value="No input folder selected")
        self.output_folder = StringVar(value="No output folder selected")

        # Clean folder selection grid
        folder_grid = ttk.Frame(parent_frame)
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
        
        # Liaison pour mettre à jour les boutons lorsque le dossier de sortie change
        self.output_folder.trace_add("write", lambda *args: self._update_control_buttons_state('idle'))
        ttk.Button(folder_grid, text="Browse", command=self._select_output_folder, width=8).grid(row=1, column=2, pady=(8, 0))
        
        # Info label for output behavior
        info_label = ttk.Label(folder_grid, text="Optional: If no output folder is selected, files will be saved in the same folder as source with encoder suffix (e.g., filename_x265.mp4)", 
                              font=("Helvetica", 9), foreground="gray")
        info_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(5, 0))

        folder_grid.columnconfigure(1, weight=1)

        # Add buttons row
        buttons_row = ttk.Frame(parent_frame)
        buttons_row.pack(fill="x", pady=(15, 0))
        
        ttk.Button(buttons_row, text="Add Files", command=self._add_files).pack(side="left", padx=(0, 10))
        ttk.Button(buttons_row, text="Add Folder", command=self._add_folder).pack(side="left", padx=(0, 10))
        ttk.Button(buttons_row, text="Add from URL", command=self._add_from_url).pack(side="left", padx=(0, 10))

        # Ajout du cadre pour la surveillance de dossier
        watch_frame = ttk.LabelFrame(parent_frame, text="Surveillance de dossier", padding="5")
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

    # === Callbacks ===
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
        from gui.merge_videos_window import MergeVideosWindow
        MergeVideosWindow(self.root, self)

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

    def _add_from_url(self):
        url = simpledialog.askstring("Add from URL", "Enter a video URL:")
        if not url:
            return

        # Use a separate thread to download the video without blocking the UI
        threading.Thread(target=self._download_and_enqueue, args=(url,), daemon=True).start()

    def _download_and_enqueue(self, url):
        try:
            # Create a temporary directory to download the video
            import tempfile
            temp_dir = tempfile.mkdtemp()

            # Use yt-dlp to download the video
            command = [
                "yt-dlp",
                "-o", os.path.join(temp_dir, "%(title)s.%(ext)s"),
                url
            ]
            subprocess.run(command, check=True)

            # Find the downloaded file and enqueue it
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
            
            # Définir un container par défaut si vide
            if not container:
                if mode == "video":
                    container = "mp4"
                elif mode == "audio":
                    container = "m4a"
                elif mode == "image":
                    container = "png"
                else:
                    container = "mp4"  # Fallback
            
            initial_dst_path = None # Placeholder for OutputConfig
            if out_root:
                # Dossier de sortie spécifié
                dst_basename = relative if isinstance(relative, Path) else Path(relative)
                initial_dst_path = out_root / dst_basename
                initial_dst_path = initial_dst_path.with_suffix("." + container)
                initial_dst_path.parent.mkdir(parents=True, exist_ok=True)
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
                initial_dst_path = p.parent / f"{stem}{suffix}.{container}" # Renamed to initial_dst_path

            # Create the initial OutputConfig based on current global UI settings
            # This OutputConfig will get its properties refined by _apply_ui_settings_to_output_config later if needed
            output_cfg = OutputConfig(name="Default", initial_dst_path=initial_dst_path, mode=mode)
            output_cfg.encoder = self._get_encoder_name_from_display(self.global_encoder_var.get())
            output_cfg.container = container # container determined above for dst_path
            output_cfg.quality = self.quality_var.get()
            output_cfg.cq_value = self.cq_var.get()
            output_cfg.preset = self.preset_var.get()
            output_cfg.video_mode = self.video_mode_var.get()
            output_cfg.bitrate = self.bitrate_var.get()
            output_cfg.multipass = self.multipass_var.get()
            # Copy other relevant settings from global UI to this initial output_cfg
            # Filters, audio_config, subtitle_config, etc. will be copied by _apply_ui_settings_to_output_config
            # or should be deepcopied here if they are complex dicts.
            # For now, _apply_ui_settings will handle them.

            job = EncodeJob(src_path=p, mode=mode, initial_output_config=output_cfg)

            # Store relative path if keep_structure is on and input_folder is valid for relative path calculations
            # This relative_src_path is for the main EncodeJob, used to structure its outputs if output_folder is set.
            if keep_structure and input_folder and not input_folder.startswith("(no") and out_root:
                try:
                    input_path_for_rel = Path(input_folder)
                    # Try Python 3.9+ method first
                    if hasattr(p, 'is_relative_to') and p.is_relative_to(input_path_for_rel):
                        job.relative_src_path = p.relative_to(input_path_for_rel)
                    else:
                        # Fallback to os.path.relpath
                        import os
                        job.relative_src_path = Path(os.path.relpath(p, input_path_for_rel))
                except (ValueError, AttributeError): # AttributeError for is_relative_to, ValueError for os.path.relpath
                     job.relative_src_path = Path(p.name) # Default to just filename if it cannot be made relative
            elif out_root: # Output root exists, but not keeping structure or input folder invalid for rel path
                job.relative_src_path = Path(p.name)
            elif not out_root: # No output root, relative_src_path isn't strictly needed by _apply_ui_settings_to_job for path construction
                 job.relative_src_path = Path(p.name) # Store filename as a fallback

            # Default settings for the initial OutputConfig are now taken from global UI vars above.
            # Old job.encoder and job.copy_audio lines are removed.
            # Specific default encoders from Settings (like "default_video_encoder") could be
            # used if global_encoder_var is empty, but current UI flow should prevent that.

            self.jobs.append(job)
            job_internal_id = str(id(job)) # Using EncodeJob's ID for the main tree item

            # Display for the tree: For now, show info from the first output.
            # Later, this might show "Multiple outputs" or allow expansion.
            display_encoder = output_cfg.encoder or "-"
            display_quality = output_cfg.quality or output_cfg.cq_value or output_cfg.bitrate or "-"

            self.tree.insert("", "end", iid=job_internal_id, values=(p.name, display_encoder, display_quality, "0%", "pending"))
            self.job_rows[job_internal_id] = {"job": job}
            current_batch_job_ids.append(job_internal_id)
            # do not submit yet; submission happens when user presses Start Encoding
        
        if current_batch_job_ids:
            self.last_import_job_ids = current_batch_job_ids

        self._update_job_selector_combobox() # Update the new combobox
        self._update_control_buttons_state('idle') # Update button states
        self._update_inspector_file_list()
        # Mettre à jour l'état des boutons après avoir ajouté des jobs
        if not any(j.status in ["running", "paused"] for j in self.jobs):
            self._update_control_buttons_state("idle")

    def _detect_mode(self, path: Path) -> str:
        ext = path.suffix.lower()
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}
        if ext == ".gif":
            return "gif"
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
                JobEditWindow(self.root, job) # This window will need significant updates

    def _select_input_folder(self):
        folder = filedialog.askdirectory(title="Select input folder")
        if folder:
            self.input_folder.set(folder)
            # Auto-importer tous les fichiers média du dossier
            self._auto_import_from_folder(folder)

    def _auto_import_from_folder(self, folder):
        """Auto-importe tous les fichiers média d'un dossier"""
        root_path = Path(folder)
        if not root_path.exists() or not root_path.is_dir():
            messagebox.showerror("Invalid Folder", "The selected input folder does not exist or is not a directory.")
            return
            
        # Définir les extensions de fichiers média supportées
        video_exts = {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".wmv", ".webm", ".flv", ".m4v", ".3gp"}
        audio_exts = {".flac", ".m4a", ".aac", ".wav", ".ogg", ".mp3", ".wma", ".opus", ".ac3"}
        image_exts = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp", ".gif", ".tga", ".dds"}
        
        # Rechercher tous les fichiers média dans le dossier et ses sous-dossiers
        all_files = [p for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() in (video_exts | audio_exts | image_exts)]
        
        if not all_files:
            messagebox.showinfo("No Media Files Found", f"No media files found in the folder:\n{folder}")
            return
            
        # Importer tous les fichiers trouvés
        self._enqueue_paths(all_files)
        # Use the new TransientInfoDialog
        dialog_message = f"Successfully imported {len(all_files)} media files from:\n{folder}"
        TransientInfoDialog(self.root, "Files Imported", dialog_message, auto_dismiss_ms=7000) # Auto-dismiss after 7s

    def _select_output_folder(self):
        """Ouvre une boîte de dialogue pour sélectionner le dossier de sortie."""
        folder = filedialog.askdirectory(title="Sélectionner le dossier de sortie")
        if folder:
            self.output_folder.set(folder)
            # Réinitialiser la couleur du champ
            if hasattr(self, 'output_folder_entry'):
                self.output_folder_entry.config(foreground="black")
            # Mettre à jour l'état des boutons
            self._update_control_buttons_state('init')



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
                ("Remux (copy stream)", "remux")
            ]
        elif media_type == "audio":
            codec_choices = [
                ("AAC", "aac"),
                ("MP3", "mp3"),
                ("Opus", "opus"),
                ("Vorbis", "vorbis"),
                ("FLAC", "flac"),
                ("ALAC (Apple Lossless)", "alac"),
                ("AC3 (Dolby Digital)", "ac3"),
                ("PCM 16-bit", "pcm_s16le"),
                ("WAV", "wav"),
                ("Copy (no re-encode)", "copy")
            ]
        else:  # image
            codec_choices = [
                ("WebP", "webp"),
                ("PNG", "png"),
                ("JPEG", "mjpeg"),
                ("AVIF (AV1)", "libaom-av1"),
                ("BMP", "bmp"),
                ("TIFF", "tiff"),
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
        # Utiliser la logique cohérente avec la nouvelle méthode
        expected_encoders = self._get_expected_encoders_for_codec(codec.lower())
        if not expected_encoders:
            return False
            
        # Vérifier si au moins un encodeur est disponible
        all_encoders = FFmpegHelpers.available_encoders()
        available_encoder_names = [name.lower() for name, _ in all_encoders]
        
        return any(encoder.lower() in available_encoder_names for encoder in expected_encoders)

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
        
        # Filtrer les encodeurs compatibles avec le codec de manière stricte
        compatible_encoders = []
        
        # Obtenir la liste exacte des encodeurs supportés pour ce codec
        expected_encoders = self._get_expected_encoders_for_codec(codec)
        
        for encoder_name, description in all_encoders:
            # Vérification stricte : l'encodeur doit être dans la liste exacte
            if encoder_name.lower() in [enc.lower() for enc in expected_encoders]:
                # Marquer les encodeurs hardware
                if FFmpegHelpers.is_hardware_encoder(encoder_name):
                    display_text = f"{encoder_name} - {description} (Hardware)"
                else:
                    display_text = f"{encoder_name} - {description}"
                compatible_encoders.append((encoder_name, display_text))
        
        # Trier par priorité : software recommendé, puis hardware, puis autres
        def encoder_priority(encoder_tuple):
            encoder_name = encoder_tuple[0].lower()
            
            # Encodeurs recommandés en premier
            priority_encoders = {
                'h264': ['libx264', 'h264_videotoolbox', 'h264_nvenc'],
                'hevc': ['libx265', 'hevc_videotoolbox', 'hevc_nvenc'], 
                'av1': ['libsvtav1', 'libaom-av1', 'av1_nvenc'],
                'vp9': ['libvpx-vp9'],
                'aac': ['aac', 'libfdk_aac'],
                'mp3': ['libmp3lame']
            }
            
            recommended = priority_encoders.get(codec, [])
            
            if encoder_name in [r.lower() for r in recommended]:
                return recommended.index(encoder_name.lower() if encoder_name.lower() in [r.lower() for r in recommended] else encoder_name)
            else:
                return 100  # Autres encodeurs à la fin
        
        # Trier les encodeurs par priorité
        compatible_encoders.sort(key=encoder_priority)
        
        # Organiser la liste finale
        display_values = []
        encoder_mapping = {}  # Pour mapper display vers encoder name
        
        for name, desc in compatible_encoders:
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
    
    def _get_expected_encoders_for_codec(self, codec: str) -> list[str]:
        """Retourne la liste exacte des encodeurs supportés pour un codec donné"""
        codec_encoder_map = {
            # Codecs vidéo
            'h264': ['libx264', 'h264_nvenc', 'h264_qsv', 'h264_amf', 'h264_videotoolbox'],
            'hevc': ['libx265', 'hevc_nvenc', 'hevc_qsv', 'hevc_amf', 'hevc_videotoolbox'],
            'av1': ['libsvtav1', 'libaom-av1', 'av1_nvenc', 'av1_qsv'],  # Seulement encodeurs vidéo
            'vp9': ['libvpx-vp9'],
            'vp8': ['libvpx'],
            'mpeg4': ['libxvid', 'mpeg4'],
            'mpeg2video': ['mpeg2video'],
            'prores': ['prores_ks', 'prores'],
            'dnxhd': ['dnxhd'],
            'remux': ['copy'],  # Pour remux
            
            # Codecs audio
            'aac': ['aac', 'libfdk_aac'],
            'mp3': ['libmp3lame'],
            'opus': ['libopus'],
            'vorbis': ['libvorbis'],
            'flac': ['flac'],
            'alac': ['alac'],
            'ac3': ['ac3'],
            'pcm_s16le': ['pcm_s16le'],
            'wav': ['pcm_s16le', 'pcm_s24le', 'pcm_s32le'],
            'copy': ['copy'],  # Pour copy audio
            
            # Codecs image
            'webp': ['libwebp'],
            'mjpeg': ['mjpeg'],
            'png': ['png'],
            'bmp': ['bmp'],
            'tiff': ['tiff'],
            'libaom-av1': ['libaom-av1'],  # Pour AVIF
            'jpegxl': ['libjxl'],
        }
        
        return codec_encoder_map.get(codec.lower(), [])

    def _encoder_supports_codec(self, encoder_name: str, codec: str) -> bool:
        """Détermine si un encodeur supporte un codec donné - version stricte"""
        encoder_clean = encoder_name.lower().strip()
        codec_clean = codec.lower().strip()
        
        # Utiliser la même logique que _get_expected_encoders_for_codec
        expected_encoders = self._get_expected_encoders_for_codec(codec_clean)
        
        # Vérification stricte : l'encodeur doit être exactement dans la liste
        return encoder_clean in [enc.lower() for enc in expected_encoders]

    def _update_quality_preset_controls(self):
        """Met à jour les contrôles qualité/preset basés sur le codec/encodeur sélectionné"""
        codec_display = self.global_codec_var.get()
        encoder_display = self.global_encoder_var.get()
        media_type = self.global_type_var.get()
        
        # Extraire les vrais noms depuis les displays
        codec = self._get_codec_from_display(codec_display).lower() if codec_display else ""
        encoder = self._get_encoder_name_from_display(encoder_display).lower() if encoder_display else ""
        
        # Réinitialiser les états des contrôles qui existent
        if hasattr(self, 'quality_entry'):
            self.quality_entry.config(state="normal")
        if hasattr(self, 'cq_entry'):
            self.cq_entry.config(state="normal")
        
        # Définir les presets disponibles selon l'encodeur pour vidéo
        # Pour audio/image, les presets sont gérés dans leurs méthodes spécifiques
        if media_type == "video":
            preset_values = []
            if "x264" in encoder or "x265" in encoder:
                preset_values = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
            elif "nvenc" in encoder:
                preset_values = ["default", "slow", "medium", "fast", "hp", "hq", "bd", "ll", "llhq", "llhp", "lossless", "losslesshp"]
            elif "svt" in encoder or "libaom" in encoder:
                preset_values = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]
            elif "vpx" in encoder:
                preset_values = ["best", "good", "rt"]
            else:
                preset_values = ["ultrafast", "fast", "medium", "slow", "veryslow"]
                
            # Mettre à jour le combobox des presets s'il existe
            if hasattr(self, 'quality_entry') and hasattr(self.quality_entry, 'config'):
                self.quality_entry['values'] = preset_values
                if preset_values and not self.preset_var.get():
                    self.preset_var.set("medium" if "medium" in preset_values else preset_values[len(preset_values)//2])

    def _update_media_type_ui(self, media_type):
        """Met à jour l'interface utilisateur en fonction du type de média."""
        # Cacher/afficher les sections selon le type de média
        if media_type == "video":
            # Afficher toutes les sections pour vidéo
            self._show_frame(self.transform_frame)
            self._show_frame(self.quality_frame)
            self._show_frame(self.hdr_frame)
            
            # Configurer les presets vidéo
            self._update_quality_presets_for_video()
            
        elif media_type == "audio":
            # Cacher résolution/rognage et HDR pour audio
            self._hide_frame(self.transform_frame)
            self._hide_frame(self.hdr_frame)
            self._show_frame(self.quality_frame)
            
            # Configurer les presets audio
            self._update_quality_presets_for_audio()
            
        elif media_type == "image":
            # Afficher résolution mais pas HDR pour image
            self._show_frame(self.transform_frame)
            self._show_frame(self.quality_frame)
            self._hide_frame(self.hdr_frame)
            
            # Configurer les presets image
            self._update_quality_presets_for_image()
    
    def _show_frame(self, frame):
        """Affiche un frame s'il existe."""
        if frame and hasattr(frame, 'pack'):
            frame.pack(fill=tk.X, pady=(0, 5))
    
    def _hide_frame(self, frame):
        """Cache un frame s'il existe."""
        if frame and hasattr(frame, 'pack_forget'):
            frame.pack_forget()
    
    def _update_quality_presets_for_video(self):
        """Met à jour les options de qualité pour vidéo."""
        # Réafficher tous les éléments pour la vidéo
        self.video_mode_radio_quality.config(text="Qualité Constante (CQ)")
        self.video_mode_radio_quality.grid(row=0, column=0, sticky="w")
        self.cq_entry.grid(row=0, column=1, sticky="w")
        
        self.video_mode_radio_bitrate.config(text="Bitrate (kbps)")
        self.video_mode_radio_bitrate.grid(row=1, column=0, sticky="w")
        self.bitrate_entry.grid(row=1, column=1, sticky="w")
        self.multipass_check.grid(row=1, column=2, sticky="w")
        
        # Label approprié pour preset vidéo
        self.preset_label.config(text="Preset Encodeur:")
        
        # Remettre les presets par défaut sans appeler _update_quality_preset_controls
        # pour éviter la récursion
    
    def _update_quality_presets_for_audio(self):
        """Met à jour les options de qualité pour audio."""
        # Cacher CQ et afficher seulement bitrate
        self.video_mode_radio_quality.grid_remove()
        self.cq_entry.grid_remove()
        
        # Garder seulement bitrate mais sans multipass
        self.video_mode_radio_bitrate.config(text="Bitrate Audio (kbps)")
        self.video_mode_radio_bitrate.grid(row=0, column=0, sticky="w")
        self.bitrate_entry.grid(row=0, column=1, sticky="w")
        self.multipass_check.grid_remove()
        
        # Label approprié pour preset audio
        self.preset_label.config(text="Qualité Audio:")
        
        # Mettre des presets audio dans quality_entry
        audio_presets = ["320", "256", "192", "128", "96", "64", "Custom"]
        self.quality_entry['values'] = audio_presets
        if not self.preset_var.get() or self.preset_var.get() not in audio_presets:
            self.preset_var.set("192")
        
        # Forcer le mode bitrate pour audio
        self.video_mode_var.set("bitrate")
        if not self.bitrate_var.get() or self.bitrate_var.get() == "0":
            self.bitrate_var.set("192")
    
    def _update_quality_presets_for_image(self):
        """Met à jour les options de qualité pour image."""
        # Pour les images, afficher seulement les options pertinentes
        self.video_mode_radio_quality.config(text="Qualité Image (%)")
        
        # Cacher les options non pertinentes pour les images
        self.video_mode_radio_bitrate.grid_remove()
        self.bitrate_entry.grid_remove()
        self.multipass_check.grid_remove()
        
        # Cacher aussi le dropdown des presets encodeur car il n'est pas pertinent pour les images
        self.preset_label.grid_remove()
        self.quality_entry.grid_remove()
        
        # Afficher seulement l'option qualité
        self.video_mode_radio_quality.grid(row=0, column=0, sticky="w")
        self.cq_entry.grid(row=0, column=1, sticky="w")
        
        # Forcer le mode qualité pour image
        self.video_mode_var.set("quality")
        if not self.quality_var.get() or self.quality_var.get() == "0":
            self.quality_var.set("90")

    def _build_quality_section(self, parent_frame):
        pass

    def _apply_quality_all_type(self):
        media_type = self.global_type_var.get()
        quality = self.quality_var.get()
        cq_value = self.cq_var.get()
        preset = self.preset_var.get()
        custom_flags = self.custom_flags_var.get()
        width, height = self._get_resolution_values()
        
        for job in self.jobs:
            if job.mode == media_type: # This check might need to be on job.outputs[0].mode or similar
                for output_cfg in job.outputs: # Apply to all outputs of matching jobs
                    output_cfg.quality = quality
                    output_cfg.cq_value = cq_value
                    output_cfg.custom_flags = custom_flags
                    output_cfg.preset = preset
                    
                    if output_cfg.mode == "video": # Check mode of the output config
                        output_cfg.video_mode = self.video_mode_var.get()
                        output_cfg.bitrate = self.bitrate_var.get() if self.video_mode_var.get() == "bitrate" else ""
                        output_cfg.multipass = self.multipass_var.get() if self.video_mode_var.get() == "bitrate" else False
                        output_cfg.filters["scale_width"] = width
                        output_cfg.filters["scale_height"] = height
                    elif output_cfg.mode == "image":
                        # Apply image-specific settings from global UI to output_cfg
                        # This part depends on how longest_side_var etc. are defined and if they exist
                        # output_cfg.longest_side = ...
                        # output_cfg.megapixels = ...
                        pass

        for iid in self.tree.get_children():
            job = next((j for j in self.jobs if str(id(j)) == iid), None)
            if job and job.outputs and job.outputs[0].mode == media_type: # Check first output's mode
                self._update_job_row(job)


    def _apply_quality_selected(self):
        quality = self.quality_var.get()
        cq_value = self.cq_var.get()
        preset = self.preset_var.get()
        custom_flags = self.custom_flags_var.get()
        width, height = self._get_resolution_values()
        
        selected = self.tree.selection()
        for iid in selected:
            job = next((j for j in self.jobs if str(id(j)) == iid), None)
            if job:
                for output_cfg in job.outputs: # Apply to all outputs of selected jobs
                    output_cfg.quality = quality
                    output_cfg.cq_value = cq_value
                    output_cfg.custom_flags = custom_flags
                    output_cfg.preset = preset
                    
                    if output_cfg.mode == "video":
                        output_cfg.video_mode = self.video_mode_var.get()
                        output_cfg.bitrate = self.bitrate_var.get() if self.video_mode_var.get() == "bitrate" else ""
                        output_cfg.multipass = self.multipass_var.get() if self.video_mode_var.get() == "bitrate" else False
                        output_cfg.filters["scale_width"] = width
                        output_cfg.filters["scale_height"] = height
                    elif output_cfg.mode == "image":
                        # Apply image-specific settings
                        pass
                self._update_job_row(job)


    def _duplicate_selected(self):
        selected_ids = self.tree.selection()
        for iid in selected_ids:
            original_job = next((j for j in self.jobs if str(id(j)) == iid), None)
            if original_job and original_job.outputs:
                # Create a new EncodeJob
                # For the new job's initial OutputConfig, deepcopy the first OutputConfig of the original job
                import copy
                new_initial_output_cfg = copy.deepcopy(original_job.outputs[0])

                # Modify dst_path for the new output_cfg to avoid overwrite, e.g., add "_copy"
                # This needs the filename templating logic. For now, simple suffix.
                old_dst = new_initial_output_cfg.dst_path
                new_initial_output_cfg.dst_path = old_dst.parent / f"{old_dst.stem}_copy{old_dst.suffix}"
                new_initial_output_cfg.name = f"{new_initial_output_cfg.name} Copy" # Update name

                new_job = EncodeJob(src_path=original_job.src_path, mode=original_job.mode, initial_output_config=new_initial_output_cfg)
                new_job.relative_src_path = original_job.relative_src_path # Copy relative path too

                # If the original job had more outputs, deepcopy them as well
                if len(original_job.outputs) > 1:
                    for out_cfg_orig in original_job.outputs[1:]:
                        new_out_cfg = copy.deepcopy(out_cfg_orig)
                        old_dst_other = new_out_cfg.dst_path
                        new_out_cfg.dst_path = old_dst_other.parent / f"{old_dst_other.stem}_copy{old_dst_other.suffix}"
                        new_out_cfg.name = f"{new_out_cfg.name} Copy"
                        new_job.outputs.append(new_out_cfg)

                self.jobs.append(new_job)
                new_job_id = str(id(new_job))
                # Display for tree from the first output of the new job
                disp_enc = new_job.outputs[0].encoder or "-"
                disp_qual = new_job.outputs[0].quality or new_job.outputs[0].cq_value or new_job.outputs[0].bitrate or "-"
                self.tree.insert("", "end", iid=new_job_id, values=(new_job.src_path.name, disp_enc, disp_qual, "0%", "pending"))
                self.job_rows[new_job_id] = {"job": new_job}
                self._update_job_selector_combobox()


    def _set_codec_for_all(self):
        """Applique tous les paramètres d'encodage globaux à tous les jobs du type sélectionné"""
        # This function needs significant rework for multi-output.
        # It should iterate through jobs, then their outputs, and apply settings.
        # The concept of "target_type" for the job might be okay, but then settings apply to all outputs of that job.
        messagebox.showinfo("Not Implemented", "This function needs update for multi-output jobs.")


    def _apply_settings_smart(self):
        """Applique les paramètres intelligemment selon la sélection"""
        # This function also needs rework for multi-output.
        # If items are selected, it should apply to their outputs.
        # If no selection, how should "apply to all of type" work with multi-output?
        messagebox.showinfo("Not Implemented", "This function needs update for multi-output jobs.")

    def _apply_codec_smart(self):
        """Applique le codec intelligemment selon la sélection"""
        # Needs rework for multi-output.
        messagebox.showinfo("Not Implemented", "This function needs update for multi-output jobs.")


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
        """Obtient le vrai nom du codec à partir du texte affiché dans le combobox."""
        if not display_text:
            return ""
        # _current_codec_choices is populated in _update_codec_choices with (display_name, actual_codec_name) tuples
        if hasattr(self, '_current_codec_choices') and self._current_codec_choices:
            for display, codec_val in self._current_codec_choices:
                if display == display_text:
                    return codec_val

        # Fallback: This part is reached if display_text is not found in _current_codec_choices.
        # This could happen if a preset is loaded with a display name not currently in the generated list,
        # or if the initial state of global_codec_var is not one of the display names.
        # The original simple split is kept as a last resort.
        # Consider logging a warning here if this fallback is used frequently.
        # print(f"Warning: Codec display text '{display_text}' not found in _current_codec_choices. Using fallback split.")
        return display_text.split(" (")[0]

    def _build_ffmpeg_command_for_output_config(self, parent_job: EncodeJob, output_cfg: OutputConfig) -> list[str]:
        """Construit la commande FFmpeg pour un OutputConfig donné."""
        cmd_prefix = ["ffmpeg", "-hide_banner"]

        trim_config = output_cfg.trim_config
        trim_start = trim_config.get("start")
        if trim_start:
            cmd_prefix.extend(["-ss", trim_start])

        cmd = cmd_prefix + ["-i", str(parent_job.src_path)]
        
        trim_end = trim_config.get("end")
        if trim_end:
            cmd.extend(["-to", trim_end])

        # Video Codec and Options
        if output_cfg.mode == "video" or output_cfg.mode == "gif":
            if output_cfg.encoder:
                 cmd.extend(["-c:v", output_cfg.encoder])

            if output_cfg.mode == "video" and self.hdr_detected_var.get():
                if self.tonemap_var.get():
                    cmd.extend(["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"])
                elif self.preserve_hdr_var.get():
                    cmd.extend(["-color_primaries", "copy", "-color_trc", "copy", "-colorspace", "copy"])
                    if "x265" in output_cfg.encoder or "hevc" in output_cfg.encoder:
                        cmd.extend(["-x265-params", "hdr-opt=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc"])
                    elif "av1" in output_cfg.encoder:
                        cmd.extend(["-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"])
            
            if output_cfg.video_mode == "quality":
                if "qsv" in output_cfg.encoder or "hevc_videotoolbox" in output_cfg.encoder:
                    cmd.extend(["-global_quality", output_cfg.cq_value])
                else:
                    cmd.extend(["-crf" if "x26" in output_cfg.encoder else "-cq:v", output_cfg.cq_value])
            elif output_cfg.video_mode == "bitrate":
                cmd.extend(["-b:v", output_cfg.bitrate])
                if output_cfg.multipass: pass

            if output_cfg.preset: cmd.extend(["-preset", output_cfg.preset])

        elif output_cfg.mode == "audio": cmd.append("-vn")
        elif output_cfg.mode == "image": cmd.append("-vn")

        # --- Additional Inputs & Filter Complex Construction ---
        additional_input_paths = []
        input_map_idx_counter = 0

        watermark_input_label_for_filter = None
        if output_cfg.watermark_path and Path(output_cfg.watermark_path).exists() and Path(output_cfg.watermark_path).suffix.lower() == ".png":
            additional_input_paths.append(str(output_cfg.watermark_path))
            input_map_idx_counter +=1
            watermark_input_label_for_filter = f"[{input_map_idx_counter}:v]"

        subtitle_config = output_cfg.subtitle_config
        embed_subtitle_input_label_for_filter = None
        embed_subtitle_codec_for_cmd = None
        if subtitle_config.get("mode") == "embed" and subtitle_config.get("external_path"):
            sub_file_path = Path(subtitle_config["external_path"])
            if sub_file_path.exists():
                additional_input_paths.append(str(sub_file_path))
                input_map_idx_counter += 1
                embed_subtitle_input_label_for_filter = f"[{input_map_idx_counter}:s?]"
                embed_subtitle_codec_for_cmd = "srt" if Path(output_cfg.dst_path).suffix.lower() == ".mkv" else "mov_text"

        for path_str in additional_input_paths:
            cmd.extend(["-i", path_str])

        main_video_filters_for_vf = []       # For simple -vf case if no filter_complex needed
        filter_complex_segments = []         # For -filter_complex string
        current_video_label_for_filters = "[0:v]" # Start with main video input

        # Populate main_video_filters_for_vf with general filters, LUT, burn-in subs
        s_w = output_cfg.filters.get("scale_width",0); s_h = output_cfg.filters.get("scale_height",0)
        if s_w > 0 or s_h > 0: main_video_filters_for_vf.append(f"scale={s_w if s_w>0 else -1}:{s_h if s_h>0 else -1}")

        # Add other general filters from output_cfg.filters to main_video_filters_for_vf
        # Example: if output_cfg.filters.get("brightness", 0) != 0: main_video_filters_for_vf.append(f"eq=brightness={output_cfg.filters['brightness']/200.0}")
        # This needs to be fully implemented based on all available filters in output_cfg.filters
        filter_keys = ["brightness", "contrast", "saturation", "gamma", "hue", "sharpness", "noise_reduction"] # etc.
        # (Actual filter string construction logic for these general filters would go here)


        if output_cfg.mode == "video" and self.hdr_detected_var.get() and self.tonemap_var.get(): # tonemap filter
            tonemap_method = self.tonemap_method_var.get()
            main_video_filters_for_vf.append(f"zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap={tonemap_method}:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


        if output_cfg.lut_path and Path(output_cfg.lut_path).exists():
            lut_fp = Path(output_cfg.lut_path)
            escaped_lut = str(lut_fp).replace("\\", "/").replace(":", "\\:") if sys.platform == "win32" else str(lut_fp).replace("\\","/")
            main_video_filters_for_vf.append(f"lut3d=file='{escaped_lut}'")

        if subtitle_config.get("mode") == "burn" and subtitle_config.get("external_path"):
            sub_fp = Path(subtitle_config["external_path"])
            if sub_fp.exists():
                escaped_sub = str(sub_fp).replace("\\", "/").replace(":", "\\:") if sys.platform == "win32" else str(sub_fp).replace("\\","/")
                main_video_filters_for_vf.append(f"subtitles=filename='{escaped_sub}'")

        use_filter_complex = bool(watermark_input_label_for_filter) or (output_cfg.mode == "gif")

        if output_cfg.mode == "gif":
            use_filter_complex = True # Ensure complex filter for GIF palette
            gif_fps = output_cfg.gif_config.get("fps", 15)
            # Use scale from filters if available, otherwise GIF specific resolution or keep original
            gif_scale_str = ""
            if s_w > 0 or s_h > 0: # From general filters
                 gif_scale_str = f",scale={s_w if s_w > 0 else -1}:{s_h if s_h > 0 else -1}:flags=lanczos"
            elif output_cfg.gif_config.get("resolution"): # from gif_config (e.g. "540x-1")
                 gif_scale_str = f",scale={output_cfg.gif_config['resolution']}:flags=lanczos"

            gif_filters = ",".join(main_video_filters_for_vf) if main_video_filters_for_vf else ""
            if gif_filters: gif_filters += "," # Add comma if other filters exist

            filter_complex_segments.append(f"[0:v]{gif_filters}fps={gif_fps}{gif_scale_str},split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse")
            current_video_label_for_filters = None # Final video output from paletteuse

        elif use_filter_complex: # Watermark case (and not GIF)
            if main_video_filters_for_vf:
                filter_complex_segments.append(f"{current_video_label_for_filters}{','.join(main_video_filters_for_vf)}[v_filtered_base]")
                current_video_label_for_filters = "[v_filtered_base]"

            if watermark_input_label_for_filter:
                wm_proc_filters = [f"scale=main_w*{output_cfg.watermark_scale}:-1"]
                if output_cfg.watermark_opacity < 1.0:
                    wm_proc_filters.append(f"format=rgba,colorchannelmixer=aa={output_cfg.watermark_opacity}")
                filter_complex_segments.append(f"{watermark_input_label_for_filter}{','.join(wm_proc_filters)}[wm_processed]")
                
                pad = output_cfg.watermark_padding; pos = output_cfg.watermark_position
                xy_pos = f"x={pad}:y={pad}"
                if pos == "top_right": xy_pos = f"x=main_w-overlay_w-{pad}:y={pad}"
                elif pos == "bottom_left": xy_pos = f"x={pad}:y=main_h-overlay_h-{pad}"
                elif pos == "bottom_right": xy_pos = f"x=main_w-overlay_w-{pad}:y=main_h-overlay_h-{pad}"
                elif pos == "center": xy_pos = f"x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2"
                filter_complex_segments.append(f"{current_video_label_for_filters}[wm_processed]overlay={xy_pos}")

        elif main_video_filters_for_vf: # No watermark, simple filters, use -vf
             cmd.extend(["-vf", ",".join(main_video_filters_for_vf)])

        if filter_complex_segments:
            cmd.extend(["-filter_complex", ";".join(filter_complex_segments)])
            if current_video_label_for_filters and current_video_label_for_filters != "[0:v]" and not watermark_input_label_for_filter and output_cfg.mode != "gif":
                 # If main filters created a label, and no overlay happened to consume it for final output
                 cmd.extend(["-map", current_video_label_for_filters])


        # --- Audio Handling ---
        audio_cfg = output_cfg.audio_config
        if output_cfg.mode != "image" and output_cfg.mode != "gif": # GIFs usually no audio
            # Map video stream if not already implicitly mapped by filter_complex's last segment
            # This explicit mapping is safer if audio mapping is also explicit.
            # If filter_complex was used, and its final segment isn't labelled for video output,
            # ffmpeg might pick one, but with audio mapping, it's better to be clear.
            # However, if current_video_label_for_filters is the output of -filter_complex,
            # we might need -map current_video_label_for_filters.
            # This interaction is complex. For now, let's assume FFmpeg handles video mapping correctly from filter_complex.

            if audio_cfg.get("mode") == "remove": cmd.append("-an")
            elif audio_cfg.get("selected_tracks") and isinstance(audio_cfg.get("selected_tracks"), list) and len(audio_cfg.get("selected_tracks")) > 0 :
                output_audio_idx = 0
                for track_src_idx in audio_cfg.get("selected_tracks"):
                    cmd.extend(["-map", f"0:a:{track_src_idx}?"]) # Optional mapping
                    action_for_track = audio_cfg.get("mode") # Simplified: global mode applies
                    # TODO: Extend for per-track action if audio_cfg structure changes
                    if action_for_track == "copy":
                        cmd.extend([f"-c:a:{output_audio_idx}", "copy"])
                    elif action_for_track == "encode":
                        cmd.extend([f"-c:a:{output_audio_idx}", audio_cfg.get("audio_codec","aac"),
                                    f"-b:a:{output_audio_idx}", audio_cfg.get("audio_bitrate","192k")])
                    output_audio_idx += 1
                if output_audio_idx == 0 : cmd.append("-an") # No tracks actually selected/mapped
            elif audio_cfg.get("mode") == "copy":
                cmd.extend(["-map", "0:a?", "-c:a", "copy"])
            elif audio_cfg.get("mode") == "encode":
                cmd.extend(["-map", "0:a?",
                            "-c:a", audio_cfg.get("audio_codec","aac"),
                            "-b:a", audio_cfg.get("audio_bitrate","192k")])
            else: # auto mode or unspecified
                cmd.extend(["-map", "0:a?"])
                if audio_cfg.get("audio_codec") and audio_cfg.get("mode") == "auto":
                     cmd.extend(["-c:a", audio_cfg.get("audio_codec")])
                     if audio_cfg.get("audio_bitrate"): cmd.extend(["-b:a", audio_cfg.get("audio_bitrate")])
        elif output_cfg.mode == "gif": # Explicitly no audio for GIFs
            cmd.append("-an")


        # --- Subtitle Handling (Embed/Copy - Burn is a video filter) ---
        if embed_subtitle_input_label_for_filter:
            cmd.extend(["-map", embed_subtitle_input_label_for_filter])
            cmd.extend(["-c:s", embed_subtitle_codec_for_cmd])
        elif subtitle_config.get("mode") == "copy":
            cmd.extend(["-map", "0:s?", "-c:s", "copy"])
        elif subtitle_config.get("mode") == "remove" and not (subtitle_config.get("mode") == "burn"):
            cmd.append("-sn")

        # --- Final commands ---
        # Ensure video stream is mapped if not image/audio only
        if output_cfg.mode not in ["audio", "image"] and not use_filter_complex and not main_video_filters_for_vf:
             # If no video filters at all (-vf or -filter_complex), ensure 0:v is mapped.
             # This is usually default unless other -map options make it ambiguous.
             # This case should be rare if scaling/LUTs are common.
             # If filter_complex was used, its output is usually mapped automatically.
             # If -vf was used, 0:v is implicitly the input and output.
             # This explicit map might be redundant or conflict if audio maps are also there.
             # Let's rely on FFmpeg's default stream selection for video if no explicit video filter mapping.
             pass


        cmd.extend(["-map_metadata", "0"])
        if output_cfg.custom_flags: cmd.extend(output_cfg.custom_flags.split())
        cmd.extend(["-y", str(output_cfg.dst_path)])
        
        return cmd

    def _update_job_selector_combobox(self):
        """Updates the job selector combobox with current job filenames."""
        job_choices = [f"{id(j)}: {j.src_path.name}" for j in self.jobs] # Use job object ID and filename
        current_selection = self.selected_job_for_settings_var.get()

        self.job_selector_combobox['values'] = job_choices
        if job_choices:
            if current_selection in job_choices:
                self.selected_job_for_settings_var.set(current_selection) # Keep current selection if still valid
            else:
                self.selected_job_for_settings_var.set(job_choices[0]) # Default to first
            self._load_settings_for_selected_job_from_combobox()
        else:
            self.selected_job_for_settings_var.set("")
            self._clear_encoding_settings_ui(reset_to_defaults=True)


    def _clear_encoding_settings_ui(self, reset_to_defaults=False):
        """Clears or resets the encoding settings UI."""
        if reset_to_defaults:
            # Attempt to load a default preset or clear to app defaults
            # This is a simplified reset. A full reset might involve calling _load_preset with a default preset name.
            self.preset_name_var.set("") # Clear preset selection
            self.global_type_var.set("video")
            self.resolution_var_settings.set("Keep Original")
            self.crop_top_var.set("0")
            self.crop_bottom_var.set("0")
            self.crop_left_var.set("0")
            self.crop_right_var.set("0")
            self.container_var.set("") # Will be set by codec change
            self.global_codec_var.set("") # Will be set by _update_codec_choices
            self.global_encoder_var.set("") # Will be set by _on_codec_change
            self.video_mode_var.set("quality")
            self.quality_var.set(Settings.data.get("default_cq", "22"))
            self.cq_var.set(Settings.data.get("default_cq", "22"))
            self.preset_var.set(Settings.data.get("default_preset", "medium"))
            self.bitrate_var.set(Settings.data.get("default_bitrate", "4000"))
            self.multipass_var.set(False)
            self.custom_flags_var.set("")
            self.subtitle_mode_var.set("copy")
            self.subtitle_path_var.set("")
            self.trim_start_var.set("00:00:00")
            self.trim_end_var.set("00:00:00")
            self.tonemap_var.set(False)
            self.preserve_hdr_var.set(True)
            self._update_codec_choices() # This will trigger chain updates for encoder, container, quality presets
            self._on_video_mode_change()
            self._on_tonemap_change()
            self._on_subtitle_mode_change()
        else:
            # Just clear the fields if not resetting to full defaults (e.g., if no jobs left)
            self.selected_job_for_settings_var.set("")
            # Optionally clear other fields or leave them as is
            # For now, if no jobs, we reset to defaults via the call in _update_job_selector_combobox

    def _on_job_selected_for_settings_change(self, event=None):
        """Called when a job is selected in the settings combobox."""
        self._load_settings_for_selected_job_from_combobox()

    def _load_settings_for_selected_job_from_combobox(self):
        """Charge les paramètres du job sélectionné dans le combobox de sélection."""
        selected_job_display = self.selected_job_for_settings_var.get()
        if not selected_job_display:
            # Soit aucun job n'est sélectionné, soit c'est le placeholder.
            # On peut décider de vider les champs ou de ne rien faire.
            # Pour l'instant, on ne fait rien pour éviter de perdre des réglages en cours.
            return
        
        # Trouver le job correspondant à l'affichage
        # Le format est "ID: NOM_FICHIER", donc on extrait l'ID
        job_id_str = selected_job_display.split(":")[0]
        
        target_job = next((j for j in self.jobs if str(id(j)) == job_id_str), None)
        
        if target_job:
            self._load_settings_for_job(target_job)

    def _load_settings_for_job(self, job: EncodeJob):
        """Loads a job's settings into the encoding UI."""
        # Ensure media type is set first as it drives other UI updates
        self.global_type_var.set(job.mode or "video")
        self._update_media_type_ui(job.mode or "video") # Update visibility of sections
        self._update_codec_choices() # This populates codec choices and triggers encoder/container updates

        # Set Codec
        codec_display_name = ""
        # Find the display name that corresponds to job.encoder value
        for display, codec_val in getattr(self, '_current_codec_choices', []):
            if codec_val == job.encoder:
                codec_display_name = display
                break
        self.global_codec_var.set(codec_display_name) # This will trigger _on_codec_change -> _update_encoder_choices

        # Set Encoder - wait for global_codec_var.set to populate encoder choices
        def set_encoder_and_rest():
            encoder_display_name = job.encoder
            # Check current mapping (populated by _update_encoder_choices)
            current_encoder_map = getattr(self, '_current_encoder_mapping', {})
            found_encoder = False
            for display, encoder_val_map in current_encoder_map.items():
                if encoder_val_map == job.encoder:
                    encoder_display_name = display
                    found_encoder = True
                    break

            if not found_encoder: # Fallback: if not in map, try to find by name in combobox values
                for enc_combo_val in self.global_encoder_combo['values']:
                    if enc_combo_val.startswith(job.encoder): # e.g., 'libx264' matches 'libx264 - H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10'
                        encoder_display_name = enc_combo_val
                        break
            self.global_encoder_var.set(encoder_display_name) # This will trigger _on_encoder_change -> _update_quality_preset_controls

            # Set Container - after codec/encoder might have influenced it
            container_display_name = job.container
            for display, container_val in getattr(self, '_current_container_choices', []):
                if container_val == job.container: # Assuming job.container stores the extension like 'mp4'
                    container_display_name = display
                    break
            self.container_var.set(container_display_name)


            # Video Quality Settings
            self.video_mode_var.set(job.video_mode or "quality")
            self.quality_var.set(job.quality or Settings.data.get("default_cq", "22") )
            self.cq_var.set(job.cq_value or Settings.data.get("default_cq", "22"))
            self.bitrate_var.set(job.bitrate.replace('k', '') if job.bitrate else Settings.data.get("default_bitrate", "4000"))
            self.multipass_var.set(job.multipass or False)
            self.preset_var.set(job.preset or Settings.data.get("default_preset", "medium")) # This should be updated by _update_quality_preset_controls too

            # Resolution and Crop
            res_w = job.filters.get("scale_width", 0)
            res_h = job.filters.get("scale_height", 0)
            if res_w and res_h:
                res_str_exact = f"{res_w}x{res_h}"
                found_res_display = False
                for r_choice in self.resolution_combo['values']: # Assuming self.resolution_combo is the correct combobox
                    if res_str_exact in r_choice.split(' ')[0]: # Check "1920x1080" part of "1920x1080 (1080p)"
                        self.resolution_var_settings.set(r_choice)
                        found_res_display = True
                        break
                if not found_res_display:
                    # If not found, set to custom and potentially update custom fields if they exist
                    self.resolution_var_settings.set("Custom")
                    # If you add custom width/height StringVars:
                    # self.custom_width_var.set(str(res_w))
                    # self.custom_height_var.set(str(res_h))
            else:
                self.resolution_var_settings.set("Keep Original")

            self.crop_top_var.set(str(job.filters.get("crop_top", 0)))
            self.crop_bottom_var.set(str(job.filters.get("crop_bottom", 0)))
            self.crop_left_var.set(str(job.filters.get("crop_left", 0)))
            self.crop_right_var.set(str(job.filters.get("crop_right", 0)))

            self.custom_flags_var.set(job.custom_flags or "")

            # Subtitles
            self.subtitle_mode_var.set(job.subtitle_config.get("mode", "copy"))
            self.subtitle_path_var.set(job.subtitle_config.get("external_path", ""))
            self._on_subtitle_mode_change()

            # Trimming
            self.trim_start_var.set(job.trim_config.get("start", "00:00:00"))
            self.trim_end_var.set(job.trim_config.get("end", "00:00:00"))

            # HDR - These are typically derived from probing the source.
            # If job object stores specific HDR intent for output, load it here.
            # For now, assume they reflect the current state of the global UI vars,
            # which should be updated by probing when a file is selected in inspector.
            # self.tonemap_var.set(job.tonemap_settings.get("enabled", False))
            # self.tonemap_method_var.set(job.tonemap_settings.get("method", "hable"))
            # self.preserve_hdr_var.set(job.hdr_settings.get("preserve", True))

            self._on_video_mode_change() # Ensure CQ/Bitrate fields visibility
            self._on_tonemap_change()    # Ensure tonemap method field state

            # Load preset name if these settings match a known preset
            # This is complex; for now, we don't try to guess the preset name.
            self.preset_name_var.set("") # Clear preset selection, as we've loaded specific job settings

        # Schedule the encoder and container setting after a short delay
        # to allow Tkinter to process the codec combobox update and its dependent callbacks.
        self.root.after(50, set_encoder_and_rest)


    def _apply_ui_settings_to_selected_job_via_combobox(self):
        """Applies current UI settings to the job selected in the combobox."""
        selected_job_display = self.selected_job_for_settings_var.get()
        if not selected_job_display:
            messagebox.showwarning("Aucune sélection", "Veuillez sélectionner un fichier dans la liste déroulante pour appliquer les paramètres.")
            return

        # Extraire l'ID du job depuis l'affichage "ID: FILENAME"
        job_id_str = selected_job_display.split(":")[0]
        target_job = next((j for j in self.jobs if str(id(j)) == job_id_str), None)
        
        if target_job:
            self._apply_ui_settings_to_job(target_job)
            self._update_job_row(target_job)
            messagebox.showinfo("Paramètres appliqués", f"Les paramètres d'encodage actuels ont été appliqués à :\n{target_job.src_path.name}")
        else:
            messagebox.showerror("Erreur", "Impossible de trouver le job sélectionné pour appliquer les paramètres.")

    def _apply_ui_settings_to_last_import_batch(self):
        """Applies current UI settings to all jobs from the last import operation."""
        if not self.last_import_job_ids:
            messagebox.showinfo("Aucun import récent", "Aucun fichier dans le dernier lot d'import.")
            return

        confirmed = messagebox.askyesno("Appliquer au dernier import",
                                        f"Voulez-vous appliquer les paramètres d'encodage actuels aux {len(self.last_import_job_ids)} fichier(s) du dernier import ?",
                                        icon='question')
        if not confirmed:
            return

        num_applied = 0
        for job_id_str in self.last_import_job_ids:
            if job_id_str in self.job_rows:
                job_to_update = self.job_rows[job_id_str]["job"]
                self._apply_ui_settings_to_job(job_to_update)
                self._update_job_row(job_to_update)
                num_applied += 1

        if num_applied > 0:
            messagebox.showinfo("Paramètres appliqués", f"Les paramètres d'encodage ont été appliqués à {num_applied} fichier(s) du dernier import.")
        else:
            messagebox.showwarning("Aucun fichier affecté", "Aucun fichier du dernier import n'a pu être mis à jour. Le lot était peut-être vide ou les fichiers ont été retirés.")


    def _start_encoding(self):
        if self.is_running:
            messagebox.showwarning("Already Running", "Encoding is already in progress.")
            return

        if not self.jobs:
            messagebox.showinfo("Empty Queue", "The encoding queue is empty.")
            return

        self.is_running = True
        self._update_control_buttons_state("running")

        # Ensure the pool is started if it was previously stopped
        if self.pool._stop_event.is_set(): # Accessing internal but necessary
            self.pool.start()
        
        for job in self.jobs:
            # The 'status' of EncodeJob is now an aggregation. We check its overall status.
            # Or, more simply, iterate outputs and submit pending ones.
            # Let's assume _apply_ui_settings_to_output_config is called for each output before submitting.

            # Apply UI settings to each output configuration of the job
            # This might overwrite settings made in JobEditWindow if not careful.
            # For now, this is how it behaves: global UI settings are applied to all outputs.
            for output_cfg in job.outputs:
                if output_cfg.status == "pending":
                    self._apply_ui_settings_to_output_config(output_cfg, job)

            # Submit each pending output configuration as a separate task
            for output_cfg in job.outputs:
                if output_cfg.status == "pending":
                    self.pool.submit((job, output_cfg), command_builder=self._build_ffmpeg_command_for_output_config)


    def _apply_ui_settings_to_output_config(self, output_cfg: OutputConfig, parent_job: EncodeJob):
        """Applique les paramètres actuels de l'interface utilisateur principale à un objet OutputConfig."""

        output_cfg.encoder = self._get_encoder_name_from_display(self.global_encoder_var.get())
        output_cfg.container = self._get_container_from_display(self.container_var.get())

        output_cfg.quality = self.quality_var.get()
        output_cfg.cq_value = self.cq_var.get()
        output_cfg.preset = self.preset_var.get()
        output_cfg.video_mode = self.video_mode_var.get()
        output_cfg.bitrate = self.bitrate_var.get() + "k" if self.bitrate_var.get() else "4000k"
        output_cfg.multipass = self.multipass_var.get()
        output_cfg.custom_flags = self.custom_flags_var.get()

        import copy
        # Ensure filters are copied, not referenced, if they come from a shared source initially
        if not output_cfg.filters or output_cfg.filters is (parent_job.outputs[0].filters if parent_job.outputs else None):
             output_cfg.filters = copy.deepcopy(output_cfg.filters or {})

        resolution_val = self.resolution_var_settings.get()
        scale_w, scale_h = 0,0
        if resolution_val != "Keep Original":
            if resolution_val == "Custom":
                try:
                    scale_w = int(self.width_var.get() or 0) # width_var is from custom resolution UI
                    scale_h = int(self.height_var.get() or 0) # height_var is from custom resolution UI
                except ValueError: pass
            else:
                try:
                    res_part = resolution_val.split(' ')[0]
                    if 'x' in res_part:
                        w_str, h_str = res_part.split('x')
                        scale_w = int(w_str); scale_h = int(h_str)
                except ValueError: pass
        output_cfg.filters["scale_width"] = scale_w
        output_cfg.filters["scale_height"] = scale_h
        output_cfg.filters["crop_top"] = int(self.crop_top_var.get() or 0)
        output_cfg.filters["crop_bottom"] = int(self.crop_bottom_var.get() or 0)
        output_cfg.filters["crop_left"] = int(self.crop_left_var.get() or 0)
        output_cfg.filters["crop_right"] = int(self.crop_right_var.get() or 0)

        # Apply other main UI settings to this output_cfg
        # For complex types like dicts, ensure deep copies if they might be shared.
        output_cfg.audio_config = copy.deepcopy(self.audio_config_from_ui()) # Placeholder for actual UI read
        output_cfg.subtitle_config = copy.deepcopy(self.subtitle_config_from_ui()) # Placeholder
        output_cfg.trim_config = copy.deepcopy(self.trim_config_from_ui()) # Placeholder
        output_cfg.gif_config = copy.deepcopy(self.gif_config_from_ui()) # Placeholder


        output_folder_setting = self.output_folder.get()
        current_out_root = Path(output_folder_setting) if output_folder_setting and not output_folder_setting.startswith("No output") else None
        src_path = parent_job.src_path

        generated_full_filename = self._generate_filename_from_template(src_path, output_cfg)

        new_dst_path = None
        if current_out_root:
            if Settings.data.get("keep_folder_structure", True) and hasattr(parent_job, 'relative_src_path') and parent_job.relative_src_path:
                new_dst_path = current_out_root / parent_job.relative_src_path.parent / generated_full_filename
            else:
                new_dst_path = current_out_root / generated_full_filename
            if new_dst_path:
              new_dst_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            new_dst_path = src_path.parent / generated_full_filename

        if new_dst_path:
            output_cfg.dst_path = new_dst_path

        output_cfg.lut_path = self.lut_path_var.get() if self.lut_path_var.get() else None
        output_cfg.watermark_path = self.watermark_path_var.get() if self.watermark_path_var.get() else None
        if output_cfg.watermark_path:
            output_cfg.watermark_position = self.watermark_position_var.get()
            output_cfg.watermark_scale = self.watermark_scale_var.get()
            output_cfg.watermark_opacity = self.watermark_opacity_var.get()
            output_cfg.watermark_padding = self.watermark_padding_var.get()
        else:
            output_cfg.watermark_position = "top_right"
            output_cfg.watermark_scale = 0.1
            output_cfg.watermark_opacity = 1.0
            output_cfg.watermark_padding = 10

    # Placeholder methods for fetching complex configs from UI for _apply_ui_settings_to_output_config
    # These would read from respective UI elements if they were more complex than simple StringVars/IntVars etc.
    # For now, OutputConfig initializes these, and JobEditWindow would be the place to change them per output.
    # The main UI panel's subtitle/trim/gif settings are directly on self.
    def audio_config_from_ui(self):
        # This should read from actual UI elements for audio track configuration if they exist for global settings
        # For now, let's assume it copies the default or first output's config if not directly editable globally
        return {"mode": "auto", "selected_tracks": [], "audio_codec": "aac", "audio_bitrate": "128k"}

    def subtitle_config_from_ui(self):
        return {"mode": self.subtitle_mode_var.get(), "external_path": self.subtitle_path_var.get() or None, "burn_track": -1}

    def trim_config_from_ui(self):
        return {"start": self.trim_start_var.get() or "", "end": self.trim_end_var.get() or ""}

    def gif_config_from_ui(self):
        # Assuming these vars exist if GIF settings are on main panel
        # fps = getattr(self, 'gif_fps_var', IntVar(value=15)).get()
        # use_palette = getattr(self, 'gif_palette_var', BooleanVar(value=True)).get()
        return {"fps": 15, "use_palette": True} # Default


    def _update_control_buttons_state(self, mode: str):
        """Met à jour l'état des boutons de contrôle (Start, Pause, etc.)"""
        if mode == "idle":
            pending_exists = any(out.status == "pending" for job in self.jobs for out in job.outputs)
            can_start = pending_exists and self.output_folder.get() and not self.output_folder.get().startswith("No output")
            
            self.start_btn.config(state="normal" if can_start else "disabled")
            
            if pending_exists and (not self.output_folder.get() or self.output_folder.get().startswith("No output")) and hasattr(self, 'output_folder_entry'):
                self.output_folder_entry.config(foreground="red")
            elif hasattr(self, 'output_folder_entry'):
                current_fg = self.output_folder_entry.cget("foreground")
                if str(current_fg) == "red": # Ensure comparison is against string form of color
                    self.output_folder_entry.config(foreground="black") # Default text color

            any_running_paused = any(out.status in ["running", "paused"] for job in self.jobs for out in job.outputs)
            self.pause_btn.config(state="normal" if any(out.status == "running" for job in self.jobs for out in job.outputs) else "disabled")
            self.resume_btn.config(state="normal" if any(out.status == "paused" for job in self.jobs for out in job.outputs) else "disabled")
            self.cancel_btn.config(state="normal" if any_running_paused or pending_exists else "disabled")

        elif mode == "running": # Renamed from "encoding" for clarity
            self.start_btn.config(state="disabled")
            self.pause_btn.config(state="normal")
            self.resume_btn.config(state="disabled")
            self.cancel_btn.config(state="normal")
        elif mode == "paused":
            self.start_btn.config(state="disabled")
            self.pause_btn.config(state="disabled")
            self.resume_btn.config(state="normal")
            self.cancel_btn.config(state="normal")

    def _on_job_progress(self, parent_job: EncodeJob, output_cfg: OutputConfig): # Signature changed
        """Met à jour l'affichage quand un output d'un job progresse"""
        self.root.after_idle(self._update_job_row, parent_job) # Update the main job row based on overall status/progress
        self.root.after_idle(self._update_overall_progress) # This calculates based on all EncodeJob's overall progress

        # Check if all outputs of all jobs are finished
        all_finished = True
        active_job_exists = False
        for job_iter in self.jobs:
            if job_iter.is_cancelled: continue
            for out_iter in job_iter.outputs:
                if out_iter.status in ["pending", "running", "paused"]:
                    all_finished = False
                    active_job_exists = True # Mark that there's at least one active/pending job
                    break
            if not all_finished:
                break
        
        if all_finished and self.is_running: # Only trigger completion if encoding was actually started
            self.is_running = False
            self._update_control_buttons_state("idle")
            self._show_encoding_completion_notification()
        elif active_job_exists and self.is_running: # Still jobs running/paused
             # Determine if overall state is paused or running for buttons
            is_any_running = any(out.status == "running" for job in self.jobs for out in job.outputs if not job.is_cancelled)
            is_any_paused = any(out.status == "paused" for job in self.jobs for out in job.outputs if not job.is_cancelled)
            if is_any_running:
                self._update_control_buttons_state("running")
            elif is_any_paused: # No job is running, but some are paused
                self._update_control_buttons_state("paused")
            # else, they are all pending or done/error/cancelled, handled by all_finished or initial idle state


    def _show_encoding_completion_notification(self):
        """Affiche une notification quand tous les encodages sont terminés"""
        # This needs to check overall status of EncodeJob objects
        completed_jobs_count = sum(1 for job in self.jobs if job.get_overall_status() == "done")
        failed_jobs_count = sum(1 for job in self.jobs if job.get_overall_status() == "error")
        cancelled_jobs_count = sum(1 for job in self.jobs if job.is_cancelled or job.get_overall_status() == "cancelled")
        
        if completed_jobs_count or failed_jobs_count or cancelled_jobs_count:
            # Créer un message de résumé
            message_parts = []
            if completed_jobs_count:
                message_parts.append(f"✅ {completed_jobs_count} job(s) terminé(s) avec succès")
            if failed_jobs_count:
                message_parts.append(f"❌ {failed_jobs_count} job(s) échoué(s)")
            if cancelled_jobs_count:
                message_parts.append(f"🚫 {cancelled_jobs_count} job(s) annulé(s)")
            
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
        iid = str(id(job)) # job is parent_job (EncodeJob)
        if self.tree.exists(iid):
            values = list(self.tree.item(iid, 'values'))

            # Display for Codec and Quality columns
            if job.outputs:
                first_output = job.outputs[0]
                num_outputs = len(job.outputs)
                values[1] = f"{first_output.encoder}{' (+'+str(num_outputs-1)+')' if num_outputs > 1 else ''}"
                qual_val = first_output.quality or first_output.cq_value or first_output.bitrate
                values[2] = f"{qual_val}{'...' if num_outputs > 1 else ''}"
            else: # Should not happen if enqueued properly
                values[1] = "-"
                values[2] = "-"

            values[3] = f"{int(job.get_overall_progress()*100)}%"
            values[4] = job.get_overall_status()

            self.tree.item(iid, values=values)

    def _update_overall_progress(self):
        if not self.jobs:
            self.progress_bar['value'] = 0
            return
        # Calculate average progress based on EncodeJob's overall progress
        total_progress_sum = sum(j.get_overall_progress() for j in self.jobs)
        avg = total_progress_sum / len(self.jobs) if self.jobs else 0.0
        self.progress_bar['value'] = avg * 100

    def _pause_all(self):
        """Met en pause tous les jobs en cours d'exécution"""
        paused_any_job = False
        for job in self.jobs:
            if job.pause_all_outputs(): # pause_all_outputs returns True if any output was paused
                paused_any_job = True
        
        if paused_any_job: # Corrected variable name
            self._update_control_buttons_state("paused")

    def _resume_all(self):
        """Reprend tous les jobs en pause"""
        resumed_any_job = False
        for job in self.jobs:
            if job.resume_all_outputs(): # resume_all_outputs returns True if any output was resumed
                resumed_any_job = True
        
        if resumed_any_job:
            self._update_control_buttons_state("running") # Change to running state after resume

    def _cancel_all(self):
        """Annule tous les jobs en cours"""
        cancelled_any_job = False
        for job in self.jobs:
            if not job.is_cancelled: # Check main job cancellation flag
                 job.cancel_all_outputs() # This sets job.is_cancelled = True and handles individual outputs
                 cancelled_any_job = True

        if cancelled_any_job:
            # self.pool.stop() # Stopping pool here might be too aggressive if user wants to add new jobs.
            # Cancellation of individual processes is handled in job.cancel_all_outputs()
            # The pool workers will finish their current (now cancelled) task and pick up new ones if any.
            # If the queue is also cleared, then stopping the pool is fine.
            # For now, just update button state. If queue is cleared, then pool is stopped.
            self.is_running = False # No longer actively running encodes from this batch
            self._update_control_buttons_state("idle")
            # Refresh queue display
            for job_to_update in self.jobs:
                 self._update_job_row(job_to_update)


    def _clear_queue(self):
        """Vide complètement la queue d'encodage"""
        for job in self.jobs:
            job.cancel_all_outputs() # Ensure all FFmpeg processes are stopped for each job
        
        # Arrêter les pools de workers
        if self.pool and hasattr(self.pool, 'running') and self.pool.running: # Check if pool is running
            self.pool.stop()
            # Pool needs to be restartable if user adds new jobs
            self.pool.threads.clear() # Clear old threads
            self.pool.job_queue = خواندن # Re-initialize queue
            # self.pool.start() # Or start it when new jobs are added / encoding starts again

        
        # Vider la liste et l'interface
        self.jobs.clear()
        self.tree.delete(*self.tree.get_children())
        self.progress_bar['value'] = 0
        self.job_rows.clear()
        self.last_import_job_ids.clear()
        self._update_inspector_file_list()
        self._update_job_selector_combobox() # Update the settings combobox
        
        # Remettre les boutons à l'état idle
        self.is_running = False
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
                job.pause_all_outputs()
                self._update_job_row(job) # Update UI to reflect new status
                self._on_job_progress(job, None) # Trigger button state update via progress logic

    def _resume_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                job.resume_all_outputs()
                self._update_job_row(job) # Update UI
                self._on_job_progress(job, None) # Trigger button state update

    def _cancel_selected_job(self):
        selected = self.tree.selection()
        if selected:
            job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
            if job:
                job.cancel_all_outputs()
                self._update_job_row(job) # Update UI
                self._on_job_progress(job, None) # Trigger button state update


    def _remove_selected_job(self):
        """Supprime le job sélectionné de la queue"""
        selected = self.tree.selection()
        if selected:
            job_id_to_remove = selected[0]
            job = next((j for j in self.jobs if str(id(j)) == job_id_to_remove), None)
            if job:
                job.cancel_all_outputs() # Ensure all associated processes are stopped
                    
                self.jobs.remove(job)
                self.tree.delete(job_id_to_remove)
                self.job_rows.pop(job_id_to_remove, None) # Use job_id_to_remove
                self.last_import_job_ids = [jid for jid in self.last_import_job_ids if jid != job_id_to_remove]

                self._update_inspector_file_list()
                self._update_job_selector_combobox()
                
                # Mettre à jour l'état des boutons si aucun job n'est actif ou pending
                if not any(out.status in ["running", "paused", "pending"] for j_iter in self.jobs for out in j_iter.outputs):
                    self.is_running = False
                    self._update_control_buttons_state("idle")
                self._update_overall_progress()


    def _on_all_jobs_finished(self):
        """Appelée quand tous les jobs de la file sont terminés."""
        self.is_running = False # Should have been set by _on_job_progress already
        self._update_control_buttons_state("idle")
        self._show_encoding_completion_notification()

        action = self.post_encode_action_var.get()
        if action == "rien":
            return

        if messagebox.askyesno("Action Post-Encodage", f"Tous les encodages sont terminés.\nVoulez-vous vraiment '{action}' l'ordinateur ?"):
            self._execute_post_encode_action(action)

    def _execute_post_encode_action(self, action: str):
        """Exécute la commande système pour l'action post-encodage."""
        import platform, subprocess
        
        system = platform.system().lower()
        command = ""

        if action == "eteindre":
            if system == "windows":
                command = "shutdown /s /t 1"
            elif system == "darwin": # macOS
                command = "osascript -e 'tell app \"System Events\" to shut down'"
            else: # Linux
                command = "shutdown -h now"
        elif action == "veille":
            if system == "windows":
                command = "rundll32.exe powrprof.dll,SetSuspendState 0,1,1"
            elif system == "darwin":
                command = "pmset sleepnow"
            else: # Linux
                command = "systemctl suspend"
        
        if command:
            try:
                messagebox.showinfo("Action Post-Encodage", f"L'action '{action}' serait exécutée avec la commande :\n{command}")
                # subprocess.run(command.split(), check=True) # Intentionally commented for safety
            except Exception as e:
                messagebox.showerror("Erreur d'action", f"Impossible d'exécuter l'action '{action}':\n{e}")
        else:
            messagebox.showwarning("Action non supportée", f"L'action '{action}' n'est pas supportée sur {system}.")

    def _setup_drag_drop(self):
        """Configure les zones de drop pour le drag & drop"""
        if not DND_AVAILABLE:
            return
            
        self.input_folder_entry.drop_target_register(DND_FILES)
        self.input_folder_entry.dnd_bind('<<Drop>>', self._on_drop_input_folder)
        
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
        
        if not current_preset or current_preset in ["H264 High Quality", "H264 Fast", "WebP Images"]:
            preset_name = self._ask_preset_name()
        else:
            result = messagebox.askyesno("Save Preset", f"Update existing preset '{current_preset}'?", icon='question')
            if result: preset_name = current_preset
            else: preset_name = self._ask_preset_name()
        
        if not preset_name: return
            
        preset_data = {
            "mode": self.global_type_var.get(),
            "codec": self.global_codec_var.get(), # This stores display name, should store actual codec
            "encoder": self._get_encoder_name_from_display(self.global_encoder_var.get()),
            "quality": self.quality_var.get(),
            "cq_value": self.cq_var.get(),
            "preset": self.preset_var.get(),
            "container": self._get_container_from_display(self.container_var.get()), # Store actual container
            "custom_flags": self.custom_flags_var.get(),
            # Save other relevant UI settings too
            "video_mode": self.video_mode_var.get(),
            "bitrate": self.bitrate_var.get(),
            "multipass": self.multipass_var.get(),
            "resolution_setting": self.resolution_var_settings.get(), # Store the display string
            "crop_top": self.crop_top_var.get(), "crop_bottom": self.crop_bottom_var.get(),
            "crop_left": self.crop_left_var.get(), "crop_right": self.crop_right_var.get(),
            "lut_path": self.lut_path_var.get(),
            "watermark_path": self.watermark_path_var.get(),
            "watermark_position": self.watermark_position_var.get(),
            "watermark_scale": self.watermark_scale_var.get(),
            "watermark_opacity": self.watermark_opacity_var.get(),
            "watermark_padding": self.watermark_padding_var.get(),
            "subtitle_mode": self.subtitle_mode_var.get(),
            "subtitle_path": self.subtitle_path_var.get(),
            "trim_start": self.trim_start_var.get(),
            "trim_end": self.trim_end_var.get(),
            # HDR settings might also be part of a preset
            "tonemap_active": self.tonemap_var.get(),
            "tonemap_method": self.tonemap_method_var.get(),
            "preserve_hdr": self.preserve_hdr_var.get()
        }
        
        Settings.data["presets"][preset_name] = preset_data
        Settings.save()
        self._update_preset_list() # Refresh menu
        self.preset_name_var.set(preset_name)
        messagebox.showinfo("Success", f"Preset '{preset_name}' saved successfully!")

    def _ask_preset_name(self) -> str:
        from tkinter.simpledialog import askstring
        name = askstring("New Preset", "Enter preset name:")
        return name.strip() if name and name.strip() else ""

    def _load_preset(self, event=None):
        selected_preset_name = self.preset_name_var.get()
        if selected_preset_name and selected_preset_name in Settings.data["presets"]:
            preset = Settings.data["presets"][selected_preset_name]

            self.global_type_var.set(preset.get("mode", "video"))
            self._update_codec_choices() # Update available codecs for the mode

            # Load codec (find display name for stored codec value)
            stored_codec_val = preset.get("codec", "")
            codec_display_to_set = ""
            if hasattr(self, '_current_codec_choices'):
                for disp, val in self._current_codec_choices:
                    if val == stored_codec_val:
                        codec_display_to_set = disp
                        break
            self.global_codec_var.set(codec_display_to_set or stored_codec_val) # Fallback to stored val if display not found
            
            self._update_encoder_choices() # Update encoders for the chosen codec

            # Load encoder (find display name for stored encoder value)
            stored_encoder_val = preset.get("encoder", "")
            encoder_display_to_set = ""
            if hasattr(self, '_current_encoder_mapping'):
                 for disp, val in self._current_encoder_mapping.items():
                    if val == stored_encoder_val:
                        encoder_display_to_set = disp
                        break
            self.global_encoder_var.set(encoder_display_to_set or stored_encoder_val)

            self._update_container_choices() # Update containers
            # Load container (find display name for stored container value)
            stored_container_val = preset.get("container", "")
            container_display_to_set = ""
            if hasattr(self, '_current_container_choices'):
                for disp, val in self._current_container_choices:
                    if val == stored_container_val:
                        container_display_to_set = disp
                        break
            self.container_var.set(container_display_to_set or stored_container_val)

            self.quality_var.set(preset.get("quality", "22"))
            self.cq_var.set(preset.get("cq_value", "22")) # Ensure this is also loaded
            self.preset_var.set(preset.get("preset", "medium"))
            self.custom_flags_var.set(preset.get("custom_flags", ""))

            self.video_mode_var.set(preset.get("video_mode", "quality"))
            self.bitrate_var.set(preset.get("bitrate", "4000"))
            self.multipass_var.set(preset.get("multipass", False))

            self.resolution_var_settings.set(preset.get("resolution_setting", "Keep Original"))
            self.crop_top_var.set(preset.get("crop_top", "0")); self.crop_bottom_var.set(preset.get("crop_bottom", "0"))
            self.crop_left_var.set(preset.get("crop_left", "0")); self.crop_right_var.set(preset.get("crop_right", "0"))

            self.lut_path_var.set(preset.get("lut_path", ""))
            self.watermark_path_var.set(preset.get("watermark_path", ""))
            self.watermark_position_var.set(preset.get("watermark_position", "top_right"))
            self.watermark_scale_var.set(preset.get("watermark_scale", 0.1))
            self.watermark_opacity_var.set(preset.get("watermark_opacity", 1.0))
            self.watermark_padding_var.set(preset.get("watermark_padding", 10))

            self.subtitle_mode_var.set(preset.get("subtitle_mode", "copy"))
            self.subtitle_path_var.set(preset.get("subtitle_path", ""))
            self.trim_start_var.set(preset.get("trim_start", "00:00:00"))
            self.trim_end_var.set(preset.get("trim_end", "00:00:00"))

            self.tonemap_var.set(preset.get("tonemap_active", False))
            self.tonemap_method_var.set(preset.get("tonemap_method", "hable"))
            self.preserve_hdr_var.set(preset.get("preserve_hdr", True))

            # Trigger UI updates based on loaded values
            self._on_media_type_change() # This will call _update_codec_choices, _update_media_type_ui
            self._on_codec_change()      # This will call _update_encoder_choices, _update_container_choices
            self._on_encoder_change()    # This will call _update_quality_preset_controls
            self._on_video_mode_change()
            self._on_tonemap_change()
            self._on_subtitle_mode_change()
            self._on_resolution_change()


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

    def _on_job_log(self, parent_job: EncodeJob, message: str, log_type: str = "info", output_id: Optional[str] = None):
        """Callback pour recevoir les logs des jobs et les transmettre au log viewer"""
        # The LogViewerWindow.add_log might need an update to handle output_id for per-output logs
        if self.log_viewer and hasattr(self.log_viewer, 'add_log_entry'): # Check for a more specific method if LogViewer is updated
             self.root.after_idle(lambda: self.log_viewer.add_log_entry(parent_job.src_path.name, message, log_type, output_id))
        elif self.log_viewer and hasattr(self.log_viewer, 'add_log'): # Fallback to old method
             self.root.after_idle(lambda: self.log_viewer.add_log(parent_job, message, log_type))


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

    def _manage_subtitles(self):
        """Ouvre la fenêtre de gestion des sous-titres pour le job sélectionné."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a job to manage its subtitles.")
            return

        job = next((j for j in self.jobs if str(id(j)) == selected[0]), None)
        if job:
            from gui.subtitle_management_window import SubtitleManagementWindow # Import here
            SubtitleManagementWindow(self.root, job)
        else:
            messagebox.showwarning("Job Not Found", "Could not find the selected job for subtitle management.")


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
            # Optionally clear settings UI or select "nothing" in the job_selector_combobox
            # self.selected_job_for_settings_var.set("")
            # self._clear_encoding_settings_ui(reset_to_defaults=True)
            return
        
        job_id = selection[0] # This is the internal ID string from str(id(job))
        
        # Update the inspector tree selection (if it exists)
        if self.inspector_tree.exists(job_id):
            self.inspector_tree.selection_set(job_id)
            self.inspector_tree.focus(job_id)
            # _on_inspector_selection_change will be called by this selection,
            # which then calls _run_probe_and_update_inspector

        # Update the new job selector combobox and load settings into UI
        if job_id in self.job_rows:
            selected_job_object = self.job_rows[job_id]["job"]
            selected_filename = selected_job_object.src_path.name

            # Set the combobox value without triggering its own callback if possible,
            # or ensure the callback handles re-entrancy or no-change gracefully.
            if self.selected_job_for_settings_var.get() != selected_filename:
                self.selected_job_for_settings_var.set(selected_filename)

            self._load_settings_for_job(selected_job_object)
        else:
            # Job ID from tree not in job_rows, something is out of sync
            # self.selected_job_for_settings_var.set("")
            # self._clear_encoding_settings_ui(reset_to_defaults=True)
            pass
    
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
            
            # Détection HDR
            hdr_detected = False
            hdr_info = "SDR"
            color_space = video_stream.get('color_space', '')
            color_transfer = video_stream.get('color_transfer', '')
            color_primaries = video_stream.get('color_primaries', '')
            
            # Vérifier les indicateurs HDR
            hdr_indicators = [
                'bt2020',  # Rec. 2020 color space
                'smpte2084',  # PQ (HDR10)
                'arib-std-b67',  # HLG (Hybrid Log-Gamma)
                'bt2100'  # ITU-R BT.2100
            ]
            
            if any(indicator in str(color_space).lower() for indicator in hdr_indicators):
                hdr_detected = True
                hdr_info = "HDR10"
            elif any(indicator in str(color_transfer).lower() for indicator in hdr_indicators):
                hdr_detected = True
                if 'arib-std-b67' in str(color_transfer).lower():
                    hdr_info = "HLG"
                else:
                    hdr_info = "HDR10"
            elif any(indicator in str(color_primaries).lower() for indicator in hdr_indicators):
                hdr_detected = True
                hdr_info = "HDR"
            
            # Mettre à jour l'interface HDR
            self.hdr_detected_var.set(hdr_detected)
            if hdr_detected:
                self.root.after_idle(lambda: self.hdr_status_label.config(text=hdr_info, foreground="orange"))
                self.root.after_idle(lambda: self.preserve_hdr_check.config(state="normal"))
                self.root.after_idle(lambda: self.tonemap_check.config(state="normal"))
            else:
                self.root.after_idle(lambda: self.hdr_status_label.config(text="SDR", foreground="gray"))
                self.root.after_idle(lambda: self.preserve_hdr_check.config(state="disabled"))
                self.root.after_idle(lambda: self.tonemap_check.config(state="disabled"))
            
            info = {
                "Résolution": f"{width}x{height}",
                "Ratio d'aspect": aspect_ratio,
                "Durée": format_duration(format_info.get('duration', 'N/A')),
                "Images/sec": f"{fps} fps" if fps != 'N/A' else 'N/A',
                "Codec Vidéo": video_stream.get('codec_long_name', video_stream.get('codec_name', 'N/A')),
                "Débit Vidéo": format_bitrate(video_stream.get('bit_rate', 'N/A')),
                "Format Pixel": video_stream.get('pix_fmt', 'N/A'),
                "HDR/Couleur": hdr_info,
                "Espace Couleur": color_space if color_space else 'N/A',
                "Transfert": color_transfer if color_transfer else 'N/A',
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
            # La direction et l'amplitude du défilement varient selon la plateforme
            if sys.platform == "darwin": # Explicitly for macOS
                # Add a print statement to debug delta values on macOS
                # print(f"macOS inspector scroll delta: {event.delta}")
                canvas.yview_scroll(int(-1 * event.delta), "units")
            elif sys.platform == "win32": # Windows
                canvas.yview_scroll(-1 * (event.delta // 120), "units")
            elif hasattr(event, 'num'): # Linux
                if event.num == 4:
                    canvas.yview_scroll(-1, "units")
                elif event.num == 5:
                    canvas.yview_scroll(1, "units")
            elif hasattr(event, 'delta') and event.delta != 0: # Fallback
                 canvas.yview_scroll(int(-1 * event.delta), "units")

        canvas.bind("<MouseWheel>", _on_mousewheel)
        canvas.bind("<Button-4>", _on_mousewheel)
        canvas.bind("<Button-5>", _on_mousewheel)

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
        """Génère et affiche une image de prévisualisation avec les filtres actuels."""
        selected_item = self.tree.selection()
        if not selected_item:
            # Clear preview or show placeholder text directly in the preview label
            if hasattr(self, 'preview_image_label'):
                # Assuming preview_image_label can display text
                self.preview_image_label.config(image=None, text="Sélectionnez un job pour l'aperçu.")
                if hasattr(self.preview_image_label, 'image'):
                    self.preview_image_label.image = None # Clear previous image reference
            return

        parent_job = next((j for j in self.jobs if str(id(j)) == selected_item[0]), None)
        if not parent_job or not parent_job.outputs:
            # Clear preview or show placeholder
            if hasattr(self, 'preview_image_label'):
                self.preview_image_label.config(image=None, text="Job or output configuration not found.")
                if hasattr(self.preview_image_label, 'image'): self.preview_image_label.image = None
            return

        # For preview, we'll use the settings of the first output configuration
        # and apply current global UI settings to it for an up-to-date preview.
        output_to_preview = parent_job.outputs[0]

        # Apply current main UI panel settings to this output config for preview purposes
        # This ensures filters, LUT path, etc., from the UI are reflected.
        self._apply_ui_settings_to_output_config(output_to_preview, parent_job)

        preview_filters = []
        # Basic scale filter from output_cfg (if any) - more filters could be added
        scale_w = output_to_preview.filters.get("scale_width", 0)
        scale_h = output_to_preview.filters.get("scale_height", 0)
        if scale_w > 0 or scale_h > 0:
            actual_w = scale_w if scale_w > 0 else -1
            actual_h = scale_h if scale_h > 0 else -1
            preview_filters.append(f"scale={actual_w}:{actual_h}")

        # Apply LUT for preview if set on the output_to_preview
        if output_to_preview.lut_path:
            lut_file_path = Path(output_to_preview.lut_path)
            if lut_file_path.exists():
                lut_filter_type = "lut3d" # Defaulting to lut3d for preview
                escaped_lut_path = str(lut_file_path).replace("\\", "/")
                if sys.platform == "win32":
                    escaped_lut_path = escaped_lut_path.replace(":", "\\:")
                preview_filters.append(f"{lut_filter_type}=file='{escaped_lut_path}'")

        sub_cfg = output_to_preview.subtitle_config
        if sub_cfg.get("mode") == "burn" and sub_cfg.get("external_path"):
            ext_sub_path = Path(sub_cfg["external_path"])
            if ext_sub_path.exists():
                escaped_sub_path = str(ext_sub_path).replace("\\", "/")
                if sys.platform == "win32": escaped_sub_path = escaped_sub_path.replace(":", "\\:")
                preview_filters.append(f"subtitles=filename='{escaped_sub_path}'")

        timestamp = self.timestamp_var.get()
        preview_cmd = [ "ffmpeg", "-ss", timestamp, "-i", str(parent_job.src_path) ]

        # Watermark for Preview
        filter_complex_preview_segments = []
        current_preview_video_label = "[0:v]"

        if output_to_preview.watermark_path and Path(output_to_preview.watermark_path).exists():
            wm_path = Path(output_to_preview.watermark_path)
            preview_cmd.extend(["-i", str(wm_path)]) # Add watermark as input [1:v]

            wm_preview_filters = [f"scale=main_w*{output_to_preview.watermark_scale}:-1"]
            if output_to_preview.watermark_opacity < 1.0:
                wm_preview_filters.append(f"format=rgba,colorchannelmixer=aa={output_to_preview.watermark_opacity}")
            filter_complex_segments.append(f"[1:v]{','.join(wm_preview_filters)}[wm_prev]")

            if preview_filters: # Main video filters
                filter_complex_segments.append(f"[0:v]{','.join(preview_filters)}[v_prev_filtered]")
                current_preview_video_label = "[v_prev_filtered]"
            # else current_preview_video_label remains "[0:v]"
            
            pad = output_to_preview.watermark_padding; pos = output_to_preview.watermark_position
            xy_pos = f"x={pad}:y={pad}"
            if pos == "top_right": xy_pos = f"x=main_w-overlay_w-{pad}:y={pad}"
            elif pos == "bottom_left": xy_pos = f"x={pad}:y=main_h-overlay_h-{pad}"
            elif pos == "bottom_right": xy_pos = f"x=main_w-overlay_w-{pad}:y=main_h-overlay_h-{pad}"
            elif pos == "center": xy_pos = f"x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2"
            filter_complex_segments.append(f"{current_preview_video_label}[wm_prev]overlay={xy_pos}")
            
            preview_cmd.extend(["-filter_complex", ";".join(filter_complex_segments)])

        elif preview_filters: # No watermark, but other filters for preview
            preview_cmd.extend(["-vf", ",".join(preview_filters)])

        preview_cmd.extend(["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"])

        try:
            process = subprocess.Popen(preview_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # (le reste de la logique d'exécution de la commande)
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg error: {stderr.decode('utf-8')}")
            
            from PIL import Image, ImageTk
            import io
            
            image = Image.open(io.BytesIO(stdout))
            image.thumbnail((300, 200)) # Garder une taille raisonnable
            photo = ImageTk.PhotoImage(image)
            
            self.preview_image_label.config(image=photo, text="")
            self.preview_image_label.image = photo

        except Exception as e:
            messagebox.showerror("Preview Error", f"Failed to render preview frame: {e}")

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

    def _on_subtitle_mode_change(self):
        """Active ou désactive le champ du fichier externe selon le mode."""
        mode = self.subtitle_mode_var.get()
        state = "normal" if mode in ["embed", "burn"] else "disabled"
        
        for child in self.external_subtitle_frame.winfo_children():
            child.configure(state=state)

    def _browse_subtitle_file(self):
        """Ouvre une boîte de dialogue pour choisir un fichier de sous-titres."""
        filetypes = [
            ("Subtitle Files", "*.srt *.ass *.ssa *.vtt"),
            ("All files", "*.*")
        ]
        filepath = filedialog.askopenfilename(title="Select a Subtitle File", filetypes=filetypes)
        if filepath:
            self.subtitle_path_var.set(filepath)

    def _browse_lut_file(self):
        """Ouvre une boîte de dialogue pour choisir un fichier LUT."""
        filetypes = [
            ("LUT Files", "*.cube *.look *.3dl *.dat *.m3d"), # Common LUT extensions
            ("All files", "*.*")
        ]
        filepath = filedialog.askopenfilename(title="Select a LUT File", filetypes=filetypes)
        if filepath:
            self.lut_path_var.set(filepath)
            # Optionally, trigger preview update if a job is selected
            # self._render_preview_frame()

    def _browse_watermark_file(self):
        """Ouvre une boîte de dialogue pour choisir un fichier image PNG pour le watermark."""
        filetypes = [
            ("PNG Images", "*.png"),
            ("All files", "*.*")
        ]
        filepath = filedialog.askopenfilename(title="Select Watermark PNG File", filetypes=filetypes)
        if filepath:
            self.watermark_path_var.set(filepath)
            # Optionally, trigger preview update
            # self._render_preview_frame()

    def _add_from_url(self):
        """Ouvre une boîte de dialogue pour entrer une URL et la télécharger."""
        url = tk.simpledialog.askstring("Add from URL", "Enter a video URL:", parent=self.root)
        if not url:
            return

        # Le téléchargement doit se faire en arrière-plan pour ne pas geler l'UI
        download_thread = threading.Thread(target=self._download_and_enqueue_url, args=(url,), daemon=True)
        download_thread.start()

    def _download_and_enqueue_url(self, url: str):
        """Télécharge une vidéo depuis une URL en utilisant yt-dlp."""
        output_dir = self.output_folder.get()
        if not output_dir or output_dir.startswith("No output"):
            # Si aucun dossier de sortie n'est défini, on ne peut pas télécharger
            messagebox.showerror("Output Folder Required", "Please select an output folder before downloading from a URL.")
            return
        
        download_path = Path(output_dir) / "downloads"
        download_path.mkdir(exist_ok=True)
        
        # Mettre à jour un statut dans l'UI (thread-safe)
        self.root.after(0, lambda: self.watch_status.config(text=f"Status: Downloading {url[:50]}..."))
        
        try:
            # Utiliser yt-dlp pour télécharger et récupérer le nom du fichier
            cmd = [
                "yt-dlp",
                "--print", "filename",
                "-o", str(download_path / "%(title)s.%(ext)s"),
                url
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            output_filename = result.stdout.strip()
            if not output_filename:
                raise ValueError("yt-dlp did not return a filename.")
                
            # Ajouter le fichier téléchargé à la file d'attente (thread-safe)
            self.root.after(0, self._enqueue_paths, [Path(output_filename)])
            self.root.after(0, lambda: self.watch_status.config(text="Status: Download complete."))

        except subprocess.CalledProcessError as e:
            error_message = f"Failed to download from URL.\nError: {e.stderr}"
            self.root.after(0, lambda: self.watch_status.config(text="Status: Download failed."))
            messagebox.showerror("Download Error", error_message)
        except Exception as e:
            error_message = f"An unexpected error occurred during download: {e}"
            self.root.after(0, lambda: self.watch_status.config(text="Status: Download failed."))
            messagebox.showerror("Download Error", error_message)

    def _generate_filename_from_template(self, src_path: Path, output_cfg: OutputConfig) -> str:
        """Generates a filename based on the user's template and OutputConfig properties."""
        template_str = Settings.data.get("filename_template", "{nom_source}.{container_ext}")

        # Prepare variables
        nom_source = src_path.stem

        resolution_str = "sourceRes"
        # Use scale_width/height from the specific output_cfg's filters
        if output_cfg.filters.get("scale_width", 0) > 0 and output_cfg.filters.get("scale_height", 0) > 0:
            resolution_str = f"{output_cfg.filters['scale_width']}x{output_cfg.filters['scale_height']}"

        codec_str = output_cfg.encoder.lower()
        # Simplify common encoder names to codec names for template
        if "libx264" in codec_str or "h264_" in codec_str: codec_str = "h264"
        elif "libx265" in codec_str or "hevc_" in codec_str: codec_str = "hevc"
        elif "libsvtav1" in codec_str or "av1_" in codec_str or "libaom-av1" in codec_str : codec_str = "av1"
        elif "libvpx-vp9" in codec_str or "vp9_" in codec_str: codec_str = "vp9"
        elif "aac" in codec_str: codec_str = "aac"
        elif "mp3" in codec_str or "lame" in codec_str: codec_str = "mp3"
        elif "opus" in codec_str: codec_str = "opus"
        elif "webp" in codec_str: codec_str = "webp"
        # Add more simplifications as needed

        from datetime import datetime
        date_str = datetime.now().strftime("%Y%m%d")

        container_ext_str = output_cfg.container

        # Perform replacements
        filename = template_str.replace("{nom_source}", nom_source)
        filename = filename.replace("{resolution}", resolution_str)
        filename = filename.replace("{codec}", codec_str)
        filename = filename.replace("{date}", date_str)
        filename = filename.replace("{container_ext}", container_ext_str)

        # Sanitize filename (basic sanitization)
        # Remove characters that are problematic in filenames on most OS
        # \ / : * ? " < > |
        # Also replace spaces with underscores for better compatibility in some scripts/systems
        illegal_chars = r'[\s\\/:*?"<>|]+' # Added space to this
        import re
        filename_stem_part = Path(filename).stem # Get the part before the final extension
        extension_part = Path(filename).suffix   # Get the final extension (e.g. .mp4)

        sanitized_stem = re.sub(illegal_chars, "_", filename_stem_part)

        # Ensure it's not empty after sanitization
        if not sanitized_stem:
            sanitized_stem = "untitled_video" # Fallback

        return sanitized_stem + extension_part


    def _on_megapixels_change(self, event=None):
        if self.megapixels_var.get() == "Custom":
            self.custom_mp_entry.config(state="normal")
        else:
            self.custom_mp_entry.config(state="disabled")

    def _on_media_type_change(self, event=None):
        """Met à jour les choix de l'UI quand le type de média change."""
        self._update_codec_choices()

    def _on_video_mode_change(self):
        """Gère le changement entre modes qualité et bitrate - utilise grid au lieu de pack"""
        mode = self.video_mode_var.get()
        if mode == "quality":
            # Afficher les contrôles de qualité
            if hasattr(self, 'cq_entry'):
                self.cq_entry.config(state="normal")
            if hasattr(self, 'bitrate_entry'):
                self.bitrate_entry.config(state="disabled")
            if hasattr(self, 'multipass_check'):
                self.multipass_check.config(state="disabled")
        else:  # bitrate
            # Afficher les contrôles de bitrate
            if hasattr(self, 'cq_entry'):
                self.cq_entry.config(state="disabled")
            if hasattr(self, 'bitrate_entry'):
                self.bitrate_entry.config(state="normal")
            if hasattr(self, 'multipass_check'):
                self.multipass_check.config(state="normal")