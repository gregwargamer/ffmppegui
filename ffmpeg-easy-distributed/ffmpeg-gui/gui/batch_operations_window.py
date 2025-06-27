from tkinter import Toplevel, ttk, StringVar, messagebox

from core.ffmpeg_helpers import FFmpegHelpers


class BatchOperationsWindow:
    """Fenêtre pour configurer des opérations batch sur plusieurs fichiers sélectionnés"""
    
    def __init__(self, parent, selected_jobs):
        self.window = Toplevel(parent)
        self.window.title("Batch Operations")
        self.window.geometry("600x500")
        self.window.minsize(500, 400)
        
        self.selected_jobs = selected_jobs
        self.parent = parent
        
        # Variables pour les différentes configurations
        self.batch_configs = []
        
        self._build_interface()
        self._populate_initial_data()
        
    def _build_interface(self):
        """Construit l'interface de la fenêtre batch"""
        main_frame = ttk.Frame(self.window, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # En-tête avec information
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill="x", pady=(0, 10))
        
        ttk.Label(header_frame, text=f"Batch Operations - {len(self.selected_jobs)} jobs selected", 
                 font=("Helvetica", 14, "bold")).pack()
        
        # Liste des jobs avec leurs configurations
        list_frame = ttk.LabelFrame(main_frame, text="Job Configurations", padding=10)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        # Créer le treeview pour les configurations batch
        columns = ("file", "encoder", "quality", "preset", "container")
        self.batch_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=10)
        
        # Configuration des en-têtes
        for col, label in zip(columns, ["File", "Encoder", "Quality", "Preset", "Container"]):
            self.batch_tree.heading(col, text=label)
            self.batch_tree.column(col, width=120)
        
        # Scrollbar pour le treeview
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.batch_tree.yview)
        self.batch_tree.configure(yscrollcommand=scrollbar.set)
        
        self.batch_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Bind double-click pour éditer
        self.batch_tree.bind("<Double-1>", self._edit_batch_item)
        
        # Contrôles pour modification batch
        controls_frame = ttk.LabelFrame(main_frame, text="Batch Controls", padding=10)
        controls_frame.pack(fill="x", pady=(0, 10))
        
        # Première ligne: Type et codec
        row1 = ttk.Frame(controls_frame)
        row1.pack(fill="x", pady=(0, 5))
        
        ttk.Label(row1, text="Type:").pack(side="left", padx=(0, 5))
        self.batch_type_var = StringVar(value="video")
        type_combo = ttk.Combobox(row1, textvariable=self.batch_type_var, values=["video", "audio", "image"], width=10, state="readonly")
        type_combo.pack(side="left", padx=(0, 10))
        type_combo.bind("<<ComboboxSelected>>", self._update_batch_codecs)
        
        ttk.Label(row1, text="Codec:").pack(side="left", padx=(0, 5))
        self.batch_codec_var = StringVar()
        self.batch_codec_combo = ttk.Combobox(row1, textvariable=self.batch_codec_var, width=15)
        self.batch_codec_combo.pack(side="left", padx=(0, 10))
        self.batch_codec_combo.bind("<<ComboboxSelected>>", self._update_batch_encoders)
        
        ttk.Label(row1, text="Encoder:").pack(side="left", padx=(0, 5))
        self.batch_encoder_var = StringVar()
        self.batch_encoder_combo = ttk.Combobox(row1, textvariable=self.batch_encoder_var, width=25)
        self.batch_encoder_combo.pack(side="left")
        
        # Deuxième ligne: Qualité et preset
        row2 = ttk.Frame(controls_frame)
        row2.pack(fill="x", pady=(0, 5))
        
        ttk.Label(row2, text="Quality:").pack(side="left", padx=(0, 5))
        self.batch_quality_var = StringVar()
        quality_entry = ttk.Entry(row2, textvariable=self.batch_quality_var, width=10)
        quality_entry.pack(side="left", padx=(0, 10))
        
        ttk.Label(row2, text="Preset:").pack(side="left", padx=(0, 5))
        self.batch_preset_var = StringVar()
        preset_combo = ttk.Combobox(row2, textvariable=self.batch_preset_var, width=12)
        preset_combo.pack(side="left", padx=(0, 10))
        
        ttk.Label(row2, text="Container:").pack(side="left", padx=(0, 5))
        self.batch_container_var = StringVar(value="mp4")
        container_combo = ttk.Combobox(row2, textvariable=self.batch_container_var, 
                                     values=["mp4", "mkv", "mov", "mxf", "webp", "png"], width=8, state="readonly")
        container_combo.pack(side="left")
        
        # Boutons d'action
        button_frame = ttk.Frame(controls_frame)
        button_frame.pack(fill="x", pady=(10, 0))
        
        ttk.Button(button_frame, text="Apply to Selected", command=self._apply_to_selected).pack(side="left", padx=(0, 5))
        ttk.Button(button_frame, text="Apply to All", command=self._apply_to_all).pack(side="left", padx=(0, 5))
        ttk.Button(button_frame, text="Reset All", command=self._reset_all).pack(side="left", padx=(5, 0))
        
        # Boutons de dialogue
        dialog_frame = ttk.Frame(main_frame)
        dialog_frame.pack(fill="x")
        
        ttk.Button(dialog_frame, text="Apply Changes", command=self._apply_changes).pack(side="right", padx=(5, 0))
        ttk.Button(dialog_frame, text="Cancel", command=self.window.destroy).pack(side="right")
        
        self._update_batch_codecs()
    
    def _populate_initial_data(self):
        """Remplit le treeview avec les données initiales des jobs"""
        for job in self.selected_jobs:
            values = (
                job.src_path.name,
                job.encoder or "libx264",
                job.quality or "23",
                job.preset or "medium",
                job.dst_path.suffix[1:] if job.dst_path.suffix else "mp4"
            )
            item_id = self.batch_tree.insert("", "end", values=values)
            # Stocker la référence au job
            self.batch_tree.set(item_id, "#0", str(id(job)))
    
    def _update_batch_codecs(self, event=None):
        """Met à jour la liste des codecs pour le type sélectionné"""
        media_type = self.batch_type_var.get()
        codecs = FFmpegHelpers.available_codecs()
        
        # Filtrer les codecs par type
        if media_type == "video":
            filtered_codecs = [c for c in codecs if c in ["h264", "hevc", "av1", "vp9", "vp8"]]
        elif media_type == "audio":
            filtered_codecs = [c for c in codecs if c in ["aac", "mp3", "opus", "vorbis", "flac"]]
        else:  # image
            filtered_codecs = [c for c in codecs if c in ["webp", "png", "jpeg"]]
        
        self.batch_codec_combo['values'] = filtered_codecs
        if filtered_codecs:
            self.batch_codec_var.set(filtered_codecs[0])
            self._update_batch_encoders()
    
    def _update_batch_encoders(self, event=None):
        """Met à jour la liste des encodeurs pour le codec sélectionné"""
        codec = self.batch_codec_var.get()
        if not codec:
            return
            
        all_encoders = FFmpegHelpers.available_encoders()
        compatible_encoders = []
        
        for encoder_name, description in all_encoders:
            if codec in encoder_name.lower():
                display_text = f"{encoder_name} - {description}"
                compatible_encoders.append(display_text)
        
        self.batch_encoder_combo['values'] = compatible_encoders
        if compatible_encoders:
            self.batch_encoder_combo.set(compatible_encoders[0])
    
    def _edit_batch_item(self, event):
        """Édite un item spécifique du batch"""
        selection = self.batch_tree.selection()
        if not selection:
            return
            
        item_id = selection[0]
        values = self.batch_tree.item(item_id, 'values')
        
        # Ouvrir une petite fenêtre d'édition
        edit_window = Toplevel(self.window)
        edit_window.title("Edit Job Settings")
        edit_window.geometry("400x200")
        edit_window.transient(self.window)
        edit_window.grab_set()
        
        # Variables pour l'édition
        edit_vars = {}
        labels = ["Encoder", "Quality", "Preset", "Container"]
        for i, label in enumerate(labels, 1):  # Skip filename
            ttk.Label(edit_window, text=f"{label}:").grid(row=i-1, column=0, padx=10, pady=5, sticky="w")
            var = StringVar(value=values[i])
            entry = ttk.Entry(edit_window, textvariable=var, width=30)
            entry.grid(row=i-1, column=1, padx=10, pady=5)
            edit_vars[label.lower()] = var
        
        def save_edit():
            new_values = (values[0],) + tuple(edit_vars[key].get() for key in ["encoder", "quality", "preset", "container"])
            self.batch_tree.item(item_id, values=new_values)
            edit_window.destroy()
        
        button_frame = ttk.Frame(edit_window)
        button_frame.grid(row=len(labels), column=0, columnspan=2, pady=10)
        ttk.Button(button_frame, text="Save", command=save_edit).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Cancel", command=edit_window.destroy).pack(side="left", padx=5)
    
    def _apply_to_selected(self):
        """Applique les paramètres actuels aux items sélectionnés"""
        selection = self.batch_tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select items to modify.")
            return
        
        encoder_display = self.batch_encoder_var.get()
        encoder_name = encoder_display.split(" - ")[0] if " - " in encoder_display else encoder_display
        
        for item_id in selection:
            values = list(self.batch_tree.item(item_id, 'values'))
            values[1] = encoder_name
            values[2] = self.batch_quality_var.get()
            values[3] = self.batch_preset_var.get()
            values[4] = self.batch_container_var.get()
            self.batch_tree.item(item_id, values=values)
    
    def _apply_to_all(self):
        """Applique les paramètres actuels à tous les items"""
        encoder_display = self.batch_encoder_var.get()
        encoder_name = encoder_display.split(" - ")[0] if " - " in encoder_display else encoder_display
        
        for item_id in self.batch_tree.get_children():
            values = list(self.batch_tree.item(item_id, 'values'))
            values[1] = encoder_name
            values[2] = self.batch_quality_var.get()
            values[3] = self.batch_preset_var.get()
            values[4] = self.batch_container_var.get()
            self.batch_tree.item(item_id, values=values)
    
    def _reset_all(self):
        """Remet tous les items à leurs valeurs par défaut"""
        for job in self.selected_jobs:
            for item_id in self.batch_tree.get_children():
                if self.batch_tree.set(item_id, "#0") == str(id(job)):
                    values = (
                        job.src_path.name,
                        job.encoder or "libx264",
                        job.quality or "23",
                        job.preset or "medium",
                        job.dst_path.suffix[1:] if job.dst_path.suffix else "mp4"
                    )
                    self.batch_tree.item(item_id, values=values)
                    break
    
    def _apply_changes(self):
        """Applique tous les changements aux jobs réels"""
        try:
            for item_id in self.batch_tree.get_children():
                job_id = self.batch_tree.set(item_id, "#0")
                job = next((j for j in self.selected_jobs if str(id(j)) == job_id), None)
                if job:
                    values = self.batch_tree.item(item_id, 'values')
                    job.encoder = values[1]
                    job.quality = values[2]
                    job.preset = values[3]
                    # Mettre à jour l'extension si nécessaire
                    new_container = values[4]
                    if job.dst_path.suffix[1:] != new_container:
                        job.dst_path = job.dst_path.with_suffix(f".{new_container}")
            
            messagebox.showinfo("Success", f"Applied changes to {len(self.selected_jobs)} jobs.")
            self.window.destroy()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply changes: {e}")
