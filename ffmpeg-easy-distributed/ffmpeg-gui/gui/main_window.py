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
from dataclasses import asdict

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
        # Charger les informations des codecs depuis codecs.json
        try:
            codecs_path = Path(__file__).parent.parent / "codecs.json"
            with open(codecs_path, 'r', encoding='utf-8') as f:
                codec_info = json.load(f)
            self.ffmpeg_helpers = FFmpegHelpers(codec_info)
            self.logger.info(f"✅ Codecs chargés depuis {codecs_path}")
        except Exception as e:
            self.logger.error(f"❌ Erreur lors du chargement de codecs.json: {e}")
            # Fallback avec données vides
            self.ffmpeg_helpers = FFmpegHelpers({})
        self.codec_name_map = {}
        
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
        self._last_media_type = None  # Pour éviter les boucles infinies
        
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
        
        # Plus besoin de fichier JSON - logique simplifiée directement dans le code

        self._build_menu()
        self._build_layout()
        self._setup_drag_drop()
        
        # Enregistrer comme observateur des changements d'état APRÈS la construction de l'UI
        self.state.register_observer(self._on_state_changed)
        
        # Initialiser après la construction de l'UI
        self.root.after(100, self._initial_ui_setup)

    def _build_menu(self):
        self.menu_bar = Menu(self.root)
        self.root.config(menu=self.menu_bar)

        # Menu Fichier
        file_menu = Menu(self.menu_bar, tearoff=0)
        file_menu.add_command(label="Ajouter des fichiers", command=self._on_menu_add_files)
        file_menu.add_command(label="Ajouter un dossier", command=self._on_menu_add_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Quitter", command=self.root.quit)
        self.menu_bar.add_cascade(label="Fichier", menu=file_menu)

        # Menu Édition
        edit_menu = Menu(self.menu_bar, tearoff=0)
        edit_menu.add_command(label="Modifier le Job", command=self._edit_selected_job)
        edit_menu.add_command(label="Dupliquer le Job", command=self._duplicate_selected)
        edit_menu.add_separator()
        edit_menu.add_command(label="Supprimer le Job", command=self._remove_selected_job)
        edit_menu.add_command(label="Vider la file", command=self._clear_queue)
        self.menu_bar.add_cascade(label="Édition", menu=edit_menu)

        # Menu Outils
        tools_menu = Menu(self.menu_bar, tearoff=0)
        tools_menu.add_command(label="Gestionnaire de serveurs", command=self.open_server_manager)
        tools_menu.add_command(label="Voir les logs", command=self.open_log_viewer)
        tools_menu.add_command(label="Paramètres", command=self.open_settings)
        self.menu_bar.add_cascade(label="Outils", menu=tools_menu)

        # Menu Aide
        help_menu = Menu(self.menu_bar, tearoff=0)
        help_menu.add_command(label="À propos", command=self.show_about)
        self.menu_bar.add_cascade(label="Aide", menu=help_menu)

    def _on_menu_add_files(self):
        """Méthode appelée par le menu Ajouter des fichiers"""
        self.logger.info("📋 Menu: Ajouter des fichiers cliqué")
        self._add_files()
    
    def _on_menu_add_folder(self):
        """Méthode appelée par le menu Ajouter un dossier"""
        self.logger.info("📋 Menu: Ajouter un dossier cliqué")
        self._add_folder()

    def _on_add_files_button(self):
        """Méthode appelée par le bouton Ajouter des fichiers"""
        self.logger.info("🔘 Bouton: Ajouter des fichiers cliqué")
        self._add_files()
    
    def _on_add_folder_button(self):
        """Méthode appelée par le bouton Ajouter un dossier"""
        self.logger.info("🔘 Bouton: Ajouter un dossier cliqué")
        self._add_folder()

    def _add_files(self):
        self.logger.info("🔍 Ouverture du dialogue de sélection de fichiers...")
        try:
            files = filedialog.askopenfilenames(title="Sélectionner des fichiers")
            self.logger.info(f"📁 Fichiers sélectionnés: {len(files) if files else 0}")
            if files:
                self.logger.info(f"📄 Liste des fichiers: {files}")
                file_paths = [Path(f) for f in files]
                self.logger.info(f"🔗 Chemins convertis: {file_paths}")
                
                self.logger.info("🚀 Appel du contrôleur pour ajouter les fichiers...")
                result = self.controller.add_files_to_queue(file_paths)
                self.logger.info(f"✅ Résultat de l'ajout: {result}")
                
                # Enregistrer les IDs pour le bouton "Appliquer au dernier import"
                if result:
                    self.last_import_job_ids = [job.job_id for job in result]
                    self.logger.info(f"💾 IDs des derniers jobs (fichiers): {self.last_import_job_ids}")
            else:
                self.logger.info("❌ Aucun fichier sélectionné")
        except Exception as e:
            self.logger.error(f"💥 Erreur lors de l'ajout de fichiers: {e}", exc_info=True)

    def _add_folder(self):
        self.logger.info("📂 Ouverture du dialogue de sélection de dossier...")
        try:
            folder = filedialog.askdirectory(title="Sélectionner un dossier")
            self.logger.info(f"📁 Dossier sélectionné: {folder}")
            if folder:
                folder_path = Path(folder)
                self.logger.info(f"🔗 Chemin converti: {folder_path}")
                
                self.logger.info("🚀 Appel du contrôleur pour ajouter le dossier...")
                result = self.controller.add_folder_to_queue(folder_path)
                self.logger.info(f"✅ Résultat de l'ajout: {result}")
                
                # Enregistrer les IDs pour le bouton "Appliquer au dernier import"
                if result:
                    self.last_import_job_ids = [job.job_id for job in result]
                    self.logger.info(f"💾 IDs des derniers jobs (dossier): {self.last_import_job_ids}")
            else:
                self.logger.info("❌ Aucun dossier sélectionné")
        except Exception as e:
            self.logger.error(f"💥 Erreur lors de l'ajout de dossier: {e}", exc_info=True)

    def _clear_queue(self):
        if messagebox.askyesno("Vider la file", "Êtes-vous sûr de vouloir supprimer tous les jobs ?"):
            self.controller.clear_queue()

    def open_server_manager(self):
        if self.server_discovery is None:
            messagebox.showerror("Erreur", "La découverte de serveurs n'est pas disponible.")
            return
        ServerManagerWindow(self.root, self.server_discovery, self.loop, self.run_async_func)

    def open_log_viewer(self):
        if self.log_viewer is None or not self.log_viewer.winfo_exists():
            self.log_viewer = LogViewerWindow(self.root)
        self.log_viewer.deiconify()

    def open_settings(self):
        SettingsWindow(self.root, self.state, self.controller)

    def show_about(self):
        messagebox.showinfo("À propos", "FFmpeg Easy GUI\n\nUne interface pour simplifier l'encodage.")

    def _on_state_changed(self, change_type: str = "general"):
        """
        Méthode centrale appelée quand l'état de l'application change.
        
        Cette méthode met à jour toute l'interface utilisateur pour refléter
        le nouvel état de l'application. C'est le cœur de la nouvelle architecture.
        """
        self.logger.info(f"🔄 MAINWINDOW: _on_state_changed appelé avec type: {change_type}")
        try:
            self._update_ui_from_state(change_type)
            self.logger.info(f"✅ MAINWINDOW: Mise à jour UI terminée pour: {change_type}")
        except Exception as e:
            self.logger.error(f"💥 MAINWINDOW: Erreur lors de la mise à jour de l'interface: {e}", exc_info=True)

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

        # Si le changement concerne le type de média, mettre à jour l'interface adaptative
        if change_type == "global_media_type_changed":
            self._update_media_type_ui(self.state.global_media_type)

    def _update_jobs_display(self):
        """Met à jour l'affichage de la liste des jobs"""
        self.logger.info(f"🔄 MAINWINDOW: _update_jobs_display appelé")
        
        # Vérifier que l'interface est initialisée
        if not hasattr(self, 'tree') or not hasattr(self, 'job_rows'):
            self.logger.warning(f"⚠️ MAINWINDOW: Interface pas encore initialisée (tree: {hasattr(self, 'tree')}, job_rows: {hasattr(self, 'job_rows')})")
            return
            
        self.logger.info(f"📊 MAINWINDOW: Nombre de jobs dans l'état: {len(self.state.jobs)}")
        
        # Synchroniser la treeview avec l'état des jobs
        current_items = set(self.tree.get_children())
        state_job_ids = {job.job_id for job in self.state.jobs}
        displayed_job_ids = {self.job_rows[item]["job"].job_id for item in current_items if item in self.job_rows}
        
        self.logger.info(f"📋 MAINWINDOW: Items actuels dans tree: {len(current_items)}")
        self.logger.info(f"📋 MAINWINDOW: Jobs dans l'état: {len(state_job_ids)}")
        self.logger.info(f"📋 MAINWINDOW: Jobs affichés: {len(displayed_job_ids)}")
        
        # Supprimer les jobs qui ne sont plus dans l'état
        removed_count = 0
        for item in current_items:
            if item in self.job_rows:
                job = self.job_rows[item]["job"]
                if job.job_id not in state_job_ids:
                    self.tree.delete(item)
                    del self.job_rows[item]
                    removed_count += 1
        
        if removed_count > 0:
            self.logger.info(f"🗑️ MAINWINDOW: {removed_count} jobs supprimés de l'affichage")
        
        # Ajouter ou mettre à jour les jobs de l'état
        added_count = 0
        updated_count = 0
        for job in self.state.jobs:
            if job.job_id not in displayed_job_ids:
                self._update_or_add_job_row(job)
                added_count += 1
            else:
                self._update_or_add_job_row(job)
                updated_count += 1
        
        self.logger.info(f"✅ MAINWINDOW: {added_count} jobs ajoutés, {updated_count} jobs mis à jour dans l'affichage")
        
        # Mettre à jour l'inspecteur et le sélecteur de jobs
        self._update_inspector_file_list()
        self._update_job_selector_combobox()

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
            
            # Mettre à jour l'apparence des boutons selon l'état initial
            if hasattr(self, 'media_type_buttons'):
                self._update_media_type_buttons_appearance()
            
            # Call the new orchestrator for initial setup based on global state
            self._update_ui_for_media_type_and_settings(
                media_type=self.state.global_media_type,
                output_config=None,
                is_global_context=True
            )
            
            # Forcer une synchronisation complète des paramètres UI vers l'AppState
            # Cela garantit que l'AppState a les bonnes valeurs par défaut
            self.logger.info("🔄 Synchronisation forcée des paramètres UI vers AppState...")
            self._on_ui_setting_changed()
            
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
        self.context_menu.add_command(label="Éditer", command=self._edit_selected_job)
        self.context_menu.add_command(label="Supprimer", command=self._remove_selected_jobs)
        
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

    def _build_file_section(self, parent):
        """Construit la section de gestion des fichiers."""
        file_frame = ttk.LabelFrame(parent, text="Fichiers", padding="10")
        file_frame.pack(fill=tk.X, pady=(0, 5))
        
        # Boutons pour ajouter des fichiers
        buttons_frame = ttk.Frame(file_frame)
        buttons_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Button(buttons_frame, text="Ajouter des fichiers", command=self._on_add_files_button).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(buttons_frame, text="Ajouter un dossier", command=self._on_add_folder_button).pack(side=tk.LEFT, padx=(0, 5))
        
        # Dossier d'entrée
        input_frame = ttk.Frame(file_frame)
        input_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(input_frame, text="Dossier d'entrée:").pack(side=tk.LEFT)
        ttk.Entry(input_frame, textvariable=self.input_folder, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        ttk.Button(input_frame, text="Parcourir", command=self._browse_input_folder).pack(side=tk.LEFT)
        
        # Dossier de sortie
        output_frame = ttk.Frame(file_frame)
        output_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(output_frame, text="Dossier de sortie:").pack(side=tk.LEFT)
        ttk.Entry(output_frame, textvariable=self.output_folder, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        ttk.Button(output_frame, text="Parcourir", command=self._browse_output_folder).pack(side=tk.LEFT)
        
        # Options de surveillance
        watch_frame = ttk.Frame(file_frame)
        watch_frame.pack(fill=tk.X)
        
        ttk.Checkbutton(watch_frame, text="Surveiller le dossier d'entrée", variable=self.watch_var).pack(side=tk.LEFT)

    def _browse_input_folder(self):
        """Ouvre un dialogue pour sélectionner le dossier d'entrée."""
        folder = filedialog.askdirectory(title="Sélectionner le dossier d'entrée")
        if folder:
            self.input_folder.set(folder)

    def _browse_output_folder(self):
        """Ouvre un dialogue pour sélectionner le dossier de sortie."""
        folder = filedialog.askdirectory(title="Sélectionner le dossier de sortie")
        if folder:
            self.output_folder.set(folder)

    def _browse_subtitle_file(self):
        """Ouvre un dialogue pour sélectionner un fichier de sous-titres externe."""
        filetypes = [
            ("Fichiers de sous-titres", "*.srt *.ass *.ssa *.sub *.vtt"),
            ("Tous les fichiers", "*.*")
        ]
        filename = filedialog.askopenfilename(
            title="Sélectionner un fichier de sous-titres",
            filetypes=filetypes
        )
        if filename:
            self.subtitle_path_var.set(filename)

    def _browse_lut_file(self):
        """Ouvre un dialogue pour sélectionner un fichier LUT."""
        filetypes = [
            ("Fichiers LUT", "*.cube *.look *.3dl"),
            ("Tous les fichiers", "*.*")
        ]
        filename = filedialog.askopenfilename(
            title="Sélectionner un fichier LUT",
            filetypes=filetypes
        )
        if filename:
            self.lut_path_var.set(filename)

    def _browse_watermark_file(self):
        """Ouvre un dialogue pour sélectionner un fichier de filigrane PNG."""
        filetypes = [
            ("Images PNG", "*.png"),
            ("Tous les fichiers", "*.*")
        ]
        filename = filedialog.askopenfilename(
            title="Sélectionner un fichier PNG pour le filigrane",
            filetypes=filetypes
        )
        if filename:
            self.watermark_path_var.set(filename)

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

        #this part do that
        # --- Barre d'application des paramètres (placée tout en haut de la zone d'encodage) ---
        file_apply_frame = ttk.Frame(parent, padding="5")
        file_apply_frame.pack(fill=tk.X, pady=(5, 5))

        ttk.Label(file_apply_frame, text="Fichier à configurer:").pack(side=tk.LEFT, padx=(0, 5))
        self.job_selector_combobox = ttk.Combobox(file_apply_frame, textvariable=self.selected_job_for_settings_var, state="readonly", width=40)
        self.job_selector_combobox.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0,10))
        self.job_selector_combobox.bind("<<ComboboxSelected>>", self._on_job_selected_for_settings_change)

        self.apply_settings_btn = ttk.Button(file_apply_frame, text="Appliquer", command=self._apply_ui_settings_to_selected_job_via_combobox)
        self.apply_settings_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.apply_to_last_batch_btn = ttk.Button(file_apply_frame, text="Appliquer au dernier import", command=self._apply_ui_settings_to_last_import_batch)
        self.apply_to_last_batch_btn.pack(side=tk.LEFT)

        # --- Zone scrollable contenant tous les contrôles ---
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
        
        preset_frame = ttk.LabelFrame(main_frame, text="Préréglage", padding="5")
        preset_frame.pack(fill=tk.X, pady=(0, 5))
        self.preset_combo = ttk.Combobox(preset_frame, textvariable=self.preset_name_var, state="readonly")
        self.preset_combo.pack(fill=tk.X, expand=True)
        self.preset_combo.bind("<<ComboboxSelected>>", lambda event: self._load_preset_by_name(self.preset_name_var.get()))

        media_type_frame = ttk.LabelFrame(main_frame, text="Type de Média", padding="5")
        media_type_frame.pack(fill=tk.X, pady=(0, 5))
        
        # Boutons de sélection avec indication visuelle
        button_frame = ttk.Frame(media_type_frame)
        button_frame.pack(fill=tk.X, pady=(5, 0))
        
        # Stocker les références des boutons pour pouvoir changer leurs couleurs
        self.media_type_buttons = {}
        
        self.media_type_buttons["video"] = tk.Button(button_frame, text="VIDEO", 
                                                     command=lambda: self._select_media_type("video"),
                                                     relief=tk.RAISED, bd=2, padx=15, pady=5)
        self.media_type_buttons["video"].pack(side=tk.LEFT, padx=(0, 5))
        
        self.media_type_buttons["audio"] = tk.Button(button_frame, text="AUDIO", 
                                                     command=lambda: self._select_media_type("audio"),
                                                     relief=tk.RAISED, bd=2, padx=15, pady=5)
        self.media_type_buttons["audio"].pack(side=tk.LEFT, padx=(0, 5))
        
        self.media_type_buttons["image"] = tk.Button(button_frame, text="IMAGE", 
                                                     command=lambda: self._select_media_type("image"),
                                                     relief=tk.RAISED, bd=2, padx=15, pady=5)
        self.media_type_buttons["image"].pack(side=tk.LEFT)
        
        # Initialiser l'apparence des boutons
        self._update_media_type_buttons_appearance()

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
        
        # Enregistrer comme attribut pour pouvoir l'afficher/masquer dynamiquement
        self.format_frame = ttk.LabelFrame(main_frame, text="Format et Codec", padding="5")
        self.format_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(self.format_frame, text="Conteneur:").grid(row=0, column=0, sticky="w", pady=2)
        self.container_combo = ttk.Combobox(self.format_frame, textvariable=self.container_var, state="readonly")
        self.container_combo.grid(row=0, column=1, sticky="ew", pady=2)
        self.container_combo.bind("<<ComboboxSelected>>", self._on_container_change)
        
        ttk.Label(self.format_frame, text="Codec Vidéo:").grid(row=1, column=0, sticky="w", pady=2)
        self.global_codec_combo = ttk.Combobox(self.format_frame, textvariable=self.global_codec_var, state="readonly")
        self.global_codec_combo.grid(row=1, column=1, sticky="ew", pady=2)
        self.global_codec_combo.bind("<<ComboboxSelected>>", self._on_codec_change)
        ttk.Label(self.format_frame, text="Encodeur:").grid(row=2, column=0, sticky="w", pady=2)
        self.global_encoder_combo = ttk.Combobox(self.format_frame, textvariable=self.global_encoder_var, state="readonly", width=40)
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
        self.format_frame.columnconfigure(1, weight=1)
        self.quality_frame.columnconfigure(2, weight=1)
        self.subtitle_frame.columnconfigure(2, weight=1)
        
        # Le label du codec sera mis à jour dynamiquement
        self.codec_label = ttk.Label(self.format_frame, text="Codec:")
        self.codec_label.grid(row=1, column=0, sticky="w", pady=2)
        self.global_codec_combo = ttk.Combobox(self.format_frame, textvariable=self.global_codec_var, state="readonly")
        self.global_codec_combo.grid(row=1, column=1, sticky="ew", pady=2)
        self.global_codec_combo.bind("<<ComboboxSelected>>", self._on_codec_change)
        
        ttk.Label(self.format_frame, text="Encodeur:").grid(row=2, column=0, sticky="w", pady=2)
        self.global_encoder_combo = ttk.Combobox(self.format_frame, textvariable=self.global_encoder_var, state="readonly", width=40)
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
        




    def _select_media_type(self, media_type):
        """Sélectionne un type de média via les boutons"""
        self.logger.info(f"🎯 SÉLECTION BOUTON: {media_type}")
        self.global_type_var.set(media_type)
        self.state.set_global_media_type(media_type)
        
        # Mettre à jour l'apparence des boutons
        self._update_media_type_buttons_appearance()
        
        # Déclencher l'adaptation de l'interface
        self._adapt_ui_simple(media_type)
        
        # Puis mettre à jour les paramètres
        self._update_ui_for_media_type_and_settings(
            media_type=media_type,
            output_config=None,
            is_global_context=True
        )

    def _update_media_type_buttons_appearance(self):
        """Met à jour l'apparence des boutons selon la sélection actuelle"""
        current_type = self.global_type_var.get()
        
        for media_type, button in self.media_type_buttons.items():
            if media_type == current_type:
                # Bouton sélectionné : bleu avec texte blanc
                button.config(
                    bg="#0078d4",  # Bleu Microsoft
                    fg="white",
                    relief=tk.SUNKEN,
                    bd=3,
                    font=("Arial", 9, "bold")
                )
            else:
                # Bouton non sélectionné : gris clair
                button.config(
                    bg="#f0f0f0",
                    fg="black",
                    relief=tk.RAISED,
                    bd=2,
                    font=("Arial", 9, "normal")
                )



    def _adapt_ui_simple(self, media_type):
        """Adaptation simple et directe de l'interface selon le type de média"""
        self.logger.info(f"🎨 ADAPTATION SIMPLE pour: {media_type}")
        
        # Masquer TOUTES les sections d'abord
        all_frames = [
            self.transform_frame,
            self.format_frame, 
            self.quality_frame,
            self.hdr_frame,
            self.subtitle_frame,
            self.lut_frame
        ]
        
        for frame in all_frames:
            frame.pack_forget()
            self.logger.info(f"❌ Frame masquée: {frame}")
        
        # Puis afficher seulement celles nécessaires
        if media_type == "video":
            frames_to_show = [
                self.transform_frame,
                self.format_frame,
                self.quality_frame,
                self.hdr_frame,
                self.subtitle_frame,
                self.lut_frame
            ]
        elif media_type == "audio":
            frames_to_show = [
                self.format_frame,
                self.quality_frame
            ]
        elif media_type == "image":
            frames_to_show = [
                self.transform_frame,
                self.format_frame,
                self.quality_frame,
                self.lut_frame
            ]
        else:
            frames_to_show = []
        
        # Afficher les frames nécessaires
        for frame in frames_to_show:
            frame.pack(fill=tk.X, pady=(0, 5))
            self.logger.info(f"✅ Frame affichée: {frame}")
        
        # Forcer la mise à jour
        self.root.update()
        self.root.update_idletasks()
        
        self.logger.info(f"🏁 ADAPTATION TERMINÉE pour: {media_type}")

    def _update_media_type_ui(self, media_type):
        """Version simplifiée - délègue à _adapt_ui_simple"""
        self.logger.info(f"🔄 _update_media_type_ui délègue vers _adapt_ui_simple")
        self._adapt_ui_simple(media_type)
        
        # Rafraîchir les listes dépendantes du média
        self._new_update_codec_choices(for_media_type=media_type)
        self._new_update_encoder_choices(for_media_type=media_type)
        self._new_update_container_choices(for_media_type=media_type)

    def _on_codec_change(self, event=None):
        """Appelé quand le codec sélectionné change."""
        codec_display_name = self.global_codec_var.get()
        codec_name = self.codec_name_map.get(codec_display_name, "")
        self.logger.info(f"Codec changé vers: {codec_display_name} ({codec_name})")
        self.logger.debug(f"Codec name map actuel: {self.codec_name_map}")
        
        if not codec_name:
            self.logger.warning(f"Codec '{codec_display_name}' non trouvé dans le mapping")
            return
            
        self.state.update_global_encoding_settings(codec=codec_name)
        self._new_update_encoder_choices()
        self._new_update_container_choices()

    def _on_encoder_change(self, event=None):
        """Appelé quand l'encodeur sélectionné change."""
        encoder = self.global_encoder_var.get()
        self.logger.info(f"Encodeur changé vers: {encoder}")
        self.state.update_global_encoding_settings(encoder=encoder)
        self._update_quality_preset_controls(encoder=encoder)
    
    def _on_container_change(self, event=None):
        """Appelé quand le conteneur sélectionné change."""
        container = self.container_var.get()
        self.logger.info(f"Conteneur changé vers: {container}")
        self.state.update_global_encoding_settings(container=container)

    def _get_current_job_output_config_for_ui(self) -> Optional[OutputConfig]:
        """Helper to get the OutputConfig for the currently selected job in the settings UI, or None."""
        job_id = self.selected_job_for_settings_var.get()
        if not job_id:
            return None

        job = self.state.get_job_by_id(job_id)
        if job and job.outputs:
            return job.outputs[0]
        
        return None

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
        #this part do that
        selected_items = self.tree.selection()
        if not selected_items:
            return
        job_ids = [self.job_rows[item]['job'].job_id for item in selected_items if item in self.job_rows]
        for jid in job_ids:
            self.controller.pause_job(jid)

    def _resume_selected_job(self):
        #this other part do that
        selected_items = self.tree.selection()
        if not selected_items:
            return
        job_ids = [self.job_rows[item]['job'].job_id for item in selected_items if item in self.job_rows]
        for jid in job_ids:
            self.controller.resume_job(jid)

    def _cancel_selected_job(self):
        """Annule le job sélectionné"""
        messagebox.showinfo("Non implémenté", "Annulation du job en cours de développement")

    def _remove_selected_job(self):
        """Supprime le job sélectionné"""
        selected_items = self.tree.selection()
        if not selected_items:
            return
        
        if messagebox.askyesno("Confirmer", "Supprimer les jobs sélectionnés de la file d'attente ?"):
            for item in selected_items:
                self.tree.delete(item)
                job_to_remove = self.job_rows.pop(item, {}).get("job")
                if job_to_remove:
                    self.jobs.remove(job_to_remove)
            self._update_job_selector_combobox()
            self._update_inspector_file_list()

    def _on_inspector_selection_change(self, event):
        """Appelée quand la sélection change dans l'inspecteur"""
        self.logger.info("🔍 INSPECTEUR: Changement de sélection détecté")
        selected_items = self.inspector_tree.selection()
        self.logger.info(f"📋 INSPECTEUR: Items sélectionnés: {selected_items}")
        
        if not selected_items:
            self.logger.info("❌ INSPECTEUR: Aucun item sélectionné")
            return
        
        job_id = selected_items[0]
        self.logger.info(f"🎯 INSPECTEUR: Job ID sélectionné: {job_id}")
        
        job = next((j for j in self.jobs if j.job_id == job_id), None)
        self.logger.info(f"🔍 INSPECTEUR: Job trouvé: {job.src_path.name if job else 'Non trouvé'}")
        
        if job:
            self.logger.info("📊 INSPECTEUR: Affichage des infos du job...")
            self._display_job_info_in_inspector(job)
        else:
            self.logger.warning(f"⚠️ INSPECTEUR: Job non trouvé pour ID: {job_id}")

    def _display_job_info_in_inspector(self, job: EncodeJob):
        self.logger.info(f"📊 INSPECTEUR: Affichage des infos pour {job.src_path.name}")
        
        # Nettoyer l'ancien contenu
        for widget in self.inspector_info_frame.winfo_children():
            widget.destroy()
        self.logger.info("🧹 INSPECTEUR: Ancien contenu nettoyé")
            
        # Créer un Notebook (onglets)
        notebook = ttk.Notebook(self.inspector_info_frame)
        notebook.pack(expand=True, fill='both')

        # --- Onglet Résumé ---
        summary_frame = ttk.Frame(notebook, padding="5")
        notebook.add(summary_frame, text='Résumé')

        try:
            info = job.get_media_info()
            self.logger.info(f"📋 INSPECTEUR: Infos média récupérées: {info}")
        except Exception as e:
            self.logger.error(f"💥 INSPECTEUR: Erreur récupération infos: {e}", exc_info=True)
            info = None
        
        if info:
            row = 0
            self.logger.info(f"✅ INSPECTEUR: Affichage de {len(info)} propriétés")
            for key, value in info.items():
                ttk.Label(summary_frame, text=f"{key.replace('_', ' ').title()}:", font=("Helvetica", 10, "bold")).grid(row=row, column=0, sticky="w", padx=5, pady=2)
                ttk.Label(summary_frame, text=str(value), wraplength=400).grid(row=row, column=1, sticky="w", padx=5, pady=2)
                row += 1
            summary_frame.columnconfigure(1, weight=1)
        else:
            self.logger.warning("⚠️ INSPECTEUR: Aucune info média disponible")
            ttk.Label(summary_frame, text="Informations média non disponibles.").grid(row=0, column=0, columnspan=2, sticky="w", padx=5, pady=2)

        # --- Onglet FFprobe ---
        ffprobe_frame = ttk.Frame(notebook, padding="5")
        notebook.add(ffprobe_frame, text='Détails (ffprobe)')

        ffprobe_text = tk.Text(ffprobe_frame, wrap='none', height=10, width=60)
        ffprobe_v_scroll = ttk.Scrollbar(ffprobe_frame, orient='vertical', command=ffprobe_text.yview)
        ffprobe_h_scroll = ttk.Scrollbar(ffprobe_frame, orient='horizontal', command=ffprobe_text.xview)
        ffprobe_text.configure(yscrollcommand=ffprobe_v_scroll.set, xscrollcommand=ffprobe_h_scroll.set)

        ffprobe_v_scroll.pack(side='right', fill='y')
        ffprobe_h_scroll.pack(side='bottom', fill='x')
        ffprobe_text.pack(expand=True, fill='both')

        try:
            ffprobe_data = job.get_raw_ffprobe_info()
            if ffprobe_data:
                pretty_json = json.dumps(ffprobe_data, indent=2)
                ffprobe_text.insert('1.0', pretty_json)
            else:
                ffprobe_text.insert('1.0', "Impossible de récupérer les données de ffprobe.")
        except Exception as e:
            ffprobe_text.insert('1.0', f"Erreur lors de la récupération des données ffprobe:\n{e}")
        
        ffprobe_text.config(state='disabled')

    # _load_settings_from_selected_job is now effectively replaced by calling
    # _update_ui_for_media_type_and_settings(job.mode, job.outputs[0], is_global_context=False)
    # from _on_job_selected_for_settings_change. So, this method can be removed.

    

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
        """Met à jour la liste des encodeurs compatibles avec le codec sélectionné."""
        media_type_to_use = for_media_type if for_media_type is not None else self.global_type_var.get()
        codec_to_use = codec if codec is not None else self.global_codec_var.get()
        
        self.logger.debug(f"Updating encoder choices for media: {media_type_to_use}, codec: {codec_to_use}")

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
                'webp': ['libwebp - WebP encoder'],
                'jpegxl': ['libjxl - JPEG XL encoder'],
                'heic': ['libx265 - HEIC encoder'], # Typically uses HEVC encoders
                'avif': ['libaom-av1 - AVIF encoder'], # Typically uses AV1 encoders
                'png': ['png - PNG encoder'],
                'jpeg': ['mjpeg - Motion JPEG encoder'], # or libjpeg-turbo
                'h264': ['libx264 - H.264 encoder'],
                'hevc': ['libx265 - H.265/HEVC encoder'],
                'av1': ['libaom-av1 - AV1 encoder'], # Added AV1
                'vp9': ['libvpx-vp9 - VP9 encoder'], # Added VP9

                # Audio codecs - ensure keys match output of FFmpegHelpers.available_codecs()
                'aac': ['aac - AAC encoder'],
                'mp3': ['libmp3lame - MP3 encoder'], # Key is "mp3"
                'flac': ['flac - FLAC encoder'],
                'opus': ['libopus - Opus encoder'],
                'vorbis': ['libvorbis - Vorbis encoder'], # Added Vorbis
                'pcm_s16le': ['pcm_s16le - PCM S16LE encoder'], # For WAV
                'alac': ['alac - ALAC (Apple Lossless Audio Codec) encoder'], # Added ALAC
                'pcm_alaw': ['pcm_alaw - PCM A-law encoder'],
                'pcm_mulaw': ['pcm_mulaw - PCM mu-law encoder'],
                # Add more common PCM formats if necessary
            }
            if codec_to_use in fallback_encoders:
                compatible_local = fallback_encoders[codec_to_use]

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
                self.video_mode_radio_quality.config(text="Qualité VBR (varie)", state="normal")
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
            elif 'png' in encoder_to_use: q_text = "PNG Compression (0-9)"
            self.video_mode_radio_quality.config(text=q_text)
        
        # TODO: Implémenter l'option lossless pour les images
        # if any(x in encoder_to_use for x in ['webp', 'png', 'jpegxl', 'avif']):
        #     self.lossless_check.grid()
        # else:
        #     self.lossless_check.grid_remove()

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

    def _update_codec_choices(self, for_media_type: Optional[str] = None):
        media_type = for_media_type or self.state.global_media_type
        self.logger.debug(f"Mise à jour des codecs pour le type de média: {media_type}")
        
        # Ajout de logs pour diagnostiquer le problème des images
        self.logger.info(f"🔍 DIAGNOSTIC CODECS: Recherche codecs pour {media_type}")
        
        # Utiliser la méthode corrigée qui charge depuis codecs.json
        codecs = self.ffmpeg_helpers.get_available_codecs(media_type)
        self.logger.info(f"📋 DIAGNOSTIC CODECS: Données codecs brutes pour {media_type}: {codecs}")
        self.logger.debug(f"Données codecs brutes pour {media_type}: {codecs}")
        
        self.codec_name_map = {c['name']: c['codec'] for c in codecs}
        codec_display_names = list(self.codec_name_map.keys())
        
        self.logger.info(f"🗺️ DIAGNOSTIC CODECS: Codec name map créé: {self.codec_name_map}")
        self.logger.info(f"📝 DIAGNOSTIC CODECS: Noms d'affichage des codecs: {codec_display_names}")
        self.logger.debug(f"Codec name map créé: {self.codec_name_map}")
        self.logger.debug(f"Noms d'affichage des codecs: {codec_display_names}")
        
        current_codec_display_name = self.global_codec_var.get()
        
        self.global_codec_combo['values'] = codec_display_names
        if codec_display_names:
            if current_codec_display_name not in codec_display_names:
                self.global_codec_var.set(codec_display_names[0])
                self.logger.info(f"✅ DIAGNOSTIC CODECS: Codec par défaut sélectionné: {codec_display_names[0]}")
                self.logger.debug(f"Codec par défaut sélectionné: {codec_display_names[0]}")
            else:
                self.global_codec_var.set(current_codec_display_name)
        else:
            self.global_codec_var.set("")
            self.logger.warning(f"❌ DIAGNOSTIC CODECS: Aucun codec trouvé pour {media_type}")
            self.logger.warning(f"Aucun codec trouvé pour {media_type}")
        
        self.logger.info(f"🏁 DIAGNOSTIC CODECS: Terminé pour {media_type}. {len(codec_display_names)} codecs chargés.")

    def _update_preset_list(self):
        #je dois aussi mettre à jour le menu des préréglages
        preset_menu = self.menu_bar.winfo_children()[2] # Attention, c'est fragile
        preset_menu.delete(2, tk.END) # Supprimer les anciens préréglages
        
        presets = self.state.get_preset_names()
        self.preset_combo['values'] = presets
        # self.watch_preset_combo['values'] = presets  # Commenté car non défini
        
        for preset_name in presets:
            preset_menu.add_command(label=preset_name, command=lambda name=preset_name: self._load_preset_by_name(name))
 



    def _new_update_codec_choices(self, for_media_type: Optional[str] = None):
        media_type = for_media_type or self.state.global_media_type
        self.logger.debug(f"Mise à jour des codecs pour le type de média: {media_type}")
        
        codecs = self.ffmpeg_helpers.get_available_codecs(media_type)
        self.codec_name_map = {c['name']: c['codec'] for c in codecs}
        codec_display_names = list(self.codec_name_map.keys())
        
        current_codec_display_name = self.global_codec_var.get()
        
        self.global_codec_combo['values'] = codec_display_names
        if codec_display_names:
            if current_codec_display_name not in codec_display_names:
                self.global_codec_var.set(codec_display_names[0])
            else:
                self.global_codec_var.set(current_codec_display_name)
        else:
            self.global_codec_var.set("")

    def _new_update_container_choices(self, for_media_type: Optional[str] = None):
        media_type = for_media_type or self.state.global_media_type
        codec_display_name = self.global_codec_var.get()
        
        if not codec_display_name:
            self.container_combo['values'] = []
            self.container_var.set("")
            return

        codec_name = self.codec_name_map.get(codec_display_name)

        if not codec_name:
            self.container_combo['values'] = []
            self.container_var.set("")
            return
        
        extensions = self.ffmpeg_helpers.get_extensions_for_codec(media_type, codec_name)
        
        self.container_combo['values'] = extensions
        if extensions:
            current_container = self.container_var.get()
            if current_container not in extensions:
                self.container_var.set(extensions[0])
        else:
            self.container_var.set("")

    def _new_update_encoder_choices(self, for_media_type: Optional[str] = None, codec: Optional[str] = None):
        media_type = for_media_type or self.state.global_media_type
        codec_display_name = codec or self.global_codec_var.get()
        
        self.logger.debug(f"Mise à jour des encodeurs pour {media_type} / {codec_display_name}")

        if not codec_display_name:
            self.global_encoder_combo['values'] = []
            self.global_encoder_var.set("")
            self.logger.debug("Pas de codec sélectionné, liste d'encodeurs vide")
            return

        codec_name = self.codec_name_map.get(codec_display_name)
        self.logger.debug(f"Tentative de mapping: '{codec_display_name}' -> '{codec_name}'")
        self.logger.debug(f"Codec name map disponible: {list(self.codec_name_map.keys())}")

        if not codec_name:
            # Essayer de trouver le codec par correspondance directe
            for display_name, internal_name in self.codec_name_map.items():
                if display_name.lower() == codec_display_name.lower():
                    codec_name = internal_name
                    self.logger.debug(f"Mapping trouvé par correspondance: {display_name} -> {codec_name}")
                    break
        
        if not codec_name:
            self.global_encoder_combo['values'] = []
            self.global_encoder_var.set("")
            self.logger.warning(f"Impossible de mapper le codec '{codec_display_name}'. Codecs disponibles: {list(self.codec_name_map.keys())}")
            return

        encoders = self.ffmpeg_helpers.get_available_encoders_for_codec(media_type, codec_name)
        self.logger.debug(f"Encodeurs trouvés: {encoders}")
        
        self.global_encoder_combo['values'] = encoders
        
        current_encoder = self.global_encoder_var.get()
        
        if encoders:
            if current_encoder not in encoders:
                # Sélectionner le premier encodeur disponible
                self.global_encoder_var.set(encoders[0])
                self.logger.debug(f"Encodeur par défaut sélectionné: {encoders[0]}")
            else:
                 self.global_encoder_var.set(current_encoder)
                 self.logger.debug(f"Encodeur actuel conservé: {current_encoder}")
        else:
            self.global_encoder_var.set("")
            self.logger.warning(f"Aucun encodeur trouvé pour {codec_name} ({media_type})")

    def _apply_ui_settings_to_job(self, job):
        """Applique les paramètres de l'UI à un job"""
        self.logger.info(f"🔧 APPLY: Application des paramètres UI au job: {job.src_path.name}")
        
        if not job.outputs:
            # Créer un OutputConfig par défaut si aucun n'existe
            from core.encode_job import OutputConfig
            output_name = f"{job.src_path.stem} - Default"
            dst_path = job.src_path.with_suffix(f".{self.container_var.get()}")
            output_cfg = OutputConfig(output_name, dst_path, job.mode)
            job.outputs.append(output_cfg)
            self.logger.info(f"📦 APPLY: OutputConfig créé pour {job.src_path.name}")
        
        # Appliquer les paramètres UI au premier output
        output_cfg = job.outputs[0]
        self.logger.info(f"🎯 APPLY: Configuration de l'output: {output_cfg}")
        
        # Mettre à jour le mode de job si nécessaire
        old_mode = job.mode
        job.mode = self.global_type_var.get()
        output_cfg.mode = job.mode
        self.logger.info(f"📝 APPLY: Mode changé de {old_mode} vers {job.mode}")
        
        # Encodeur et codec
        encoder_display = self.global_encoder_var.get()
        encoder_name = self._get_encoder_name_from_display(encoder_display)
        output_cfg.encoder = encoder_name
        self.logger.info(f"🔧 APPLY: Encodeur: {encoder_display} -> {encoder_name}")
        
        codec_display_name = self.global_codec_var.get()
        codec_name = self.codec_name_map.get(codec_display_name, "")
        output_cfg.codec = codec_name
        self.logger.info(f"🎬 APPLY: Codec: {codec_display_name} -> {codec_name}")
        
        # Container et chemin de destination
        container = self.container_var.get()
        output_cfg.container = container
        self.logger.info(f"📦 APPLY: Container: {container}")
        
        if container:
            # Mettre à jour le chemin de destination avec la nouvelle extension
            if output_cfg.dst_path:
                old_path = output_cfg.dst_path
                output_cfg.dst_path = output_cfg.dst_path.with_suffix(f".{container}")
                self.logger.info(f"📁 APPLY: Chemin destination: {old_path} -> {output_cfg.dst_path}")
            else:
                # Si le chemin de destination n'existait pas, en créer un
                output_folder = self.output_folder.get()
                if output_folder and not output_folder.startswith("No"):
                    output_cfg.dst_path = Path(output_folder) / job.src_path.with_suffix(f".{container}").name
                else: # Fallback au dossier source
                    output_cfg.dst_path = job.src_path.with_suffix(f".{container}")
                self.logger.info(f"📁 APPLY: Nouveau chemin destination créé: {output_cfg.dst_path}")

        # Paramètres de qualité
        output_cfg.video_mode = self.video_mode_var.get()
        output_cfg.quality = self.quality_var.get()
        output_cfg.bitrate = self.bitrate_var.get()
        output_cfg.multipass = self.multipass_var.get()
        output_cfg.preset = self.preset_var.get()
        
        # Paramètres personnalisés
        output_cfg.custom_flags = self.custom_flags_var.get()

        self.logger.info(f"✅ APPLY: Paramètres appliqués au job {job.src_path.name}: "
                         f"Codec={output_cfg.codec}, Encoder={output_cfg.encoder}, Container={output_cfg.container}")
        self.controller.state.notify_observers("jobs_changed")

    def _update_container_choices(self, for_media_type: Optional[str] = None):
        """Met à jour les choix de conteneurs disponibles selon le type de média et le codec sélectionné."""
        media_type = for_media_type or self.state.global_media_type
        codec_display_name = self.global_codec_var.get()
        
        if not codec_display_name:
            self.container_combo['values'] = []
            self.container_var.set("")
            return

        codec_name = self.codec_name_map.get(codec_display_name)

        if not codec_name:
            self.container_combo['values'] = []
            self.container_var.set("")
            return
        
        extensions = self.ffmpeg_helpers.get_extensions_for_codec(media_type, codec_name)
        
        self.container_combo['values'] = extensions
        if extensions:
            current_container = self.container_var.get()
            if current_container not in extensions:
                self.container_var.set(extensions[0])
        else:
            self.container_var.set("")

    def _on_tonemap_change(self, event=None):
        """Appelé quand les paramètres de tone mapping changent."""
        # Méthode stub pour éviter les erreurs - à implémenter plus tard
        pass

    def _on_subtitle_mode_change(self, event=None):
        """Appelé quand le mode de sous-titres change."""
        # Méthode stub pour éviter les erreurs - à implémenter plus tard
        pass

    def _get_encoder_name_from_display(self, display_name: str) -> str:
        """Extrait le nom court de l'encodeur à partir du nom d'affichage."""
        if not display_name:
            return ""
        
        # Extraire le nom court de l'encodeur avant le premier " - "
        if " - " in display_name:
            return display_name.split(" - ")[0]
        
        return display_name

    def _load_preset_by_name(self, preset_name: str):
        """Charge un preset par son nom."""
        if self.controller.load_preset(preset_name):
            self.logger.info(f"Preset '{preset_name}' chargé avec succès")
        else:
            self.logger.warning(f"Échec du chargement du preset '{preset_name}'")

    def _on_double_click(self, event):
        """Appelé lors d'un double-clic sur un élément de la queue."""
        selected_items = self.tree.selection()
        if selected_items:
            self._edit_selected_job()

    def _on_right_click(self, event):
        """Appelé lors d'un clic droit sur un élément de la queue."""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def _on_queue_selection_change(self, event):
        """Appelé quand la sélection change dans la queue."""
        selected_items = self.tree.selection()
        self._update_buttons_state()

    def _start_encoding(self):
        """Démarre l'encodage des jobs en attente."""
        self.run_async_func(self.controller.start_encoding())

    def _pause_all(self):
        """Met en pause tous les jobs en cours."""
        # Méthode stub - à implémenter
        pass

    def _resume_all(self):
        """Reprend tous les jobs en pause."""
        # Méthode stub - à implémenter
        pass

    def _reassign_selected_jobs(self):
        """Réassigne les jobs sélectionnés."""
        # Méthode stub - à implémenter
        pass

    def _on_job_selected_for_settings_change(self, event=None):
        """Appelé quand un job est sélectionné dans le combobox des paramètres."""
        job_id = self.selected_job_for_settings_var.get()
        if job_id:
            job = self.state.get_job_by_id(job_id)
            if job and job.outputs:
                self._update_ui_for_media_type_and_settings(job.mode, job.outputs[0], is_global_context=False)

    def _apply_ui_settings_to_selected_job_via_combobox(self):
        """Applique les paramètres UI au job sélectionné via le combobox."""
        job_id = self.selected_job_for_settings_var.get()
        if job_id:
            job = self.state.get_job_by_id(job_id)
            if job:
                self._apply_ui_settings_to_job(job)

    def _apply_ui_settings_to_last_import_batch(self):
        """Applique les paramètres UI au dernier lot de fichiers importés."""
        self.logger.info("🔄 BATCH: Début d'application des paramètres au dernier import")
        self.logger.info(f"📋 BATCH: IDs du dernier import: {self.last_import_job_ids}")
        
        if not self.last_import_job_ids:
            self.logger.warning("⚠️ BATCH: Aucun ID de dernier import trouvé!")
            return
        
        applied_count = 0
        for job_id in self.last_import_job_ids:
            self.logger.info(f"🔍 BATCH: Recherche job avec ID: {job_id}")
            job = self.state.get_job_by_id(job_id)
            if job:
                self.logger.info(f"✅ BATCH: Job trouvé: {job.src_path.name}")
                self._apply_ui_settings_to_job(job)
                applied_count += 1
            else:
                self.logger.warning(f"❌ BATCH: Job non trouvé pour ID: {job_id}")
        
        self.logger.info(f"🏁 BATCH: Paramètres appliqués à {applied_count}/{len(self.last_import_job_ids)} jobs")

    def _on_frame_configure(self, event):
        """Appelé quand la taille du frame change."""
        self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        """Appelé quand la taille du canvas change."""
        canvas_width = event.width
        self.settings_canvas.itemconfig(self._canvas_window, width=canvas_width)

    def _setup_drag_drop(self):
        """Configure le drag & drop si disponible."""
        if self.dnd_available:
            try:
                self.root.drop_target_register(DND_FILES)
                self.root.dnd_bind('<<Drop>>', self._on_drop)
            except Exception as e:
                self.logger.warning(f"Impossible de configurer le drag & drop: {e}")

    def _on_drop(self, event):
        """Appelé quand des fichiers sont déposés dans l'application."""
        self.logger.info("🎯 Événement drag & drop détecté")
        try:
            self.logger.info(f"📦 Données reçues: {event.data}")
            files = self.root.tk.splitlist(event.data)
            self.logger.info(f"📄 Fichiers extraits: {files}")
            
            file_paths = [Path(f) for f in files if Path(f).exists()]
            self.logger.info(f"🔗 Chemins valides: {file_paths}")
            
            if file_paths:
                self.logger.info("🚀 Appel du contrôleur pour ajouter les fichiers droppés...")
                added_jobs = self.controller.add_files_to_queue(file_paths)
                self.logger.info(f"✅ Jobs créés: {len(added_jobs) if added_jobs else 0}")
                
                if added_jobs:
                    self.last_import_job_ids = [job.job_id for job in added_jobs]
                    self.logger.info(f"💾 IDs des derniers jobs: {self.last_import_job_ids}")
                else:
                    self.logger.warning("⚠️ Aucun job créé malgré des fichiers valides")
            else:
                self.logger.warning("❌ Aucun fichier valide trouvé dans le drop")
        except Exception as e:
            self.logger.error(f"💥 Erreur lors du drag & drop: {e}", exc_info=True)

    def _update_job_selector_combobox(self):
        """Met à jour le combobox de sélection des jobs."""
        job_names = []
        for job in self.state.jobs:
            job_names.append(f"{job.job_id[:8]} - {job.src_path.name}")
        
        self.job_selector_combobox['values'] = job_names
        
        # Si un job était sélectionné et qu'il existe encore, le garder sélectionné
        current_selection = self.selected_job_for_settings_var.get()
        if current_selection and current_selection in job_names:
            self.selected_job_for_settings_var.set(current_selection)
        elif job_names:
            self.selected_job_for_settings_var.set(job_names[0])
        else:
            self.selected_job_for_settings_var.set("")

    def _update_inspector_file_list(self):
        """Met à jour la liste des fichiers dans l'inspecteur."""
        self.logger.info("🔄 INSPECTEUR: Mise à jour de la liste des fichiers")
        
        # Vider la liste actuelle
        current_items = self.inspector_tree.get_children()
        self.logger.info(f"🗑️ INSPECTEUR: Suppression de {len(current_items)} items existants")
        for item in current_items:
            self.inspector_tree.delete(item)
        
        # Ajouter tous les jobs à la liste
        self.logger.info(f"📁 INSPECTEUR: Ajout de {len(self.state.jobs)} jobs")
        for job in self.state.jobs:
            self.inspector_tree.insert("", "end", iid=job.job_id, values=(job.src_path.name,))
            self.logger.debug(f"➕ INSPECTEUR: Job ajouté: {job.job_id} - {job.src_path.name}")
        
        self.logger.info("✅ INSPECTEUR: Liste mise à jour")

    def _update_resolution_choices(self):
        """Met à jour les choix de résolution disponibles selon le type de média."""
        media_type = self.state.global_media_type
        
        if media_type == "video":
            # Résolutions communes pour vidéo
            resolutions = [
                "Original", "4K (3840x2160)", "1440p (2560x1440)", 
                "1080p (1920x1080)", "720p (1280x720)", "480p (854x480)",
                "360p (640x360)", "240p (426x240)"
            ]
        elif media_type == "image":
            # Options pour images
            resolutions = [
                "Original", "4K (3840x2160)", "2K (2048x1080)",
                "1080p (1920x1080)", "720p (1280x720)", "Personnalisé"
            ]
        else:
            # Pour audio ou autres
            resolutions = ["Original"]
        
        if hasattr(self, 'resolution_combo'):
            self.resolution_combo['values'] = resolutions
            if not self.resolution_var.get() or self.resolution_var.get() not in resolutions:
                self.resolution_var.set("Original")

    def _remove_selected_jobs(self):
        """Supprime les jobs sélectionnés de la queue."""
        selected_items = self.tree.selection()
        if not selected_items:
            self.logger.warning("Aucun job sélectionné pour la suppression")
            return
        
        # Demander confirmation
        job_count = len(selected_items)
        if job_count == 1:
            message = "Êtes-vous sûr de vouloir supprimer ce job ?"
        else:
            message = f"Êtes-vous sûr de vouloir supprimer ces {job_count} jobs ?"
        
        result = messagebox.askyesno("Confirmation", message)
        if not result:
            return
        
        # Récupérer les IDs des jobs à supprimer
        job_ids = []
        for item in selected_items:
            job_id = self.tree.item(item)['values'][0]  # L'ID est dans la première colonne
            job_ids.append(job_id)
        
        # Supprimer les jobs via le contrôleur
        self.controller.remove_jobs(job_ids)
        self.logger.info(f"{job_count} job(s) supprimé(s)")

    #this part do that
    # Met à jour la zone de défilement et affiche/masque la scrollbar selon le contenu
    def _update_scroll_state(self):
        # Ajuster le scrollregion pour englober tout le contenu
        self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))

        # Calculer la hauteur du contenu et celle du canvas
        bbox = self.settings_canvas.bbox("all")
        content_height = bbox[3] if bbox else 0
        canvas_height = self.settings_canvas.winfo_height()

        # Afficher la scrollbar uniquement si le contenu déborde
        if content_height > canvas_height:
            # Pack si pas déjà affichée
            if not self._scrollbar.winfo_ismapped():
                self._scrollbar.pack(side="right", fill="y")
        else:
            # Masquer pour économiser de l'espace
            if self._scrollbar.winfo_ismapped():
                self._scrollbar.pack_forget()

        # Planifier une mise à jour périodique pour garder l'UX fluide
        self.root.after(250, self._update_scroll_state)

    #this part do that
    # Met à jour l'affichage/état des serveurs dans l'interface
    def update_server_status(self, servers):
        """Stub d'affichage de statut serveur pour éviter les erreurs fatales."""
        # Pour l'instant on se contente de journaliser ; implémentation détaillée à venir
        server_names = [getattr(s, 'name', 'srv') for s in servers]
        self.logger.debug(f"Serveurs connectés: {server_names}")
