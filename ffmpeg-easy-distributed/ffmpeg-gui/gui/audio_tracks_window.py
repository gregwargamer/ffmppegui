import subprocess
from tkinter import Toplevel, ttk, StringVar, messagebox


class AudioTracksWindow:
    """Fenêtre pour configurer les pistes audio (sélection, réencodage, suppression)"""
    
    def __init__(self, parent, job):
        self.window = Toplevel(parent)
        self.window.title(f"Audio Tracks - {job.src_path.name}")
        self.window.geometry("600x400")
        self.window.minsize(500, 300)
        
        self.job = job
        self.audio_tracks = []
        
        self._build_interface()
        self._load_audio_tracks()
        
    def _build_interface(self):
        """Construit l'interface de configuration audio"""
        main_frame = ttk.Frame(self.window, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # En-tête
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(header_frame, text="Audio Track Configuration", 
                 font=("Helvetica", 14, "bold")).pack()
        
        # Liste des pistes audio
        tracks_frame = ttk.LabelFrame(main_frame, text="Audio Tracks", padding=10)
        tracks_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        # Treeview pour les pistes
        columns = ("include", "track", "action", "codec", "channels", "sample_rate", "bitrate", "language")
        self.tracks_tree = ttk.Treeview(tracks_frame, columns=columns, show="headings", height=8)
        
        # Définir les colonnes avec des labels plus clairs
        column_labels = {
            "include": "Incl.",
            "track": "Piste",
            "action": "Action",
            "codec": "Codec",
            "channels": "Canaux",
            "sample_rate": "Fréquence",
            "bitrate": "Débit",
            "language": "Langue"
        }

        for col, label in column_labels.items():
            self.tracks_tree.heading(col, text=label)
            if col == "include":
                self.tracks_tree.column(col, width=40, anchor="center")
            elif col == "track":
                self.tracks_tree.column(col, width=60)
            elif col == "action":
                self.tracks_tree.column(col, width=100)
            else:
                self.tracks_tree.column(col, width=80)
        
        scrollbar_tracks = ttk.Scrollbar(tracks_frame, orient="vertical", command=self.tracks_tree.yview)
        self.tracks_tree.configure(yscrollcommand=scrollbar_tracks.set)
        
        self.tracks_tree.pack(side="left", fill="both", expand=True)
        scrollbar_tracks.pack(side="right", fill="y")
        
        # Configuration d'encodage
        encode_frame = ttk.LabelFrame(main_frame, text="Re-encoding Settings", padding=10)
        encode_frame.pack(fill="x", pady=(0, 10))
        
        settings_row = ttk.Frame(encode_frame)
        settings_row.pack(fill="x")
        
        ttk.Label(settings_row, text="Audio Codec:").pack(side="left", padx=(0, 5))
        self.audio_codec_var = StringVar(value=self.job.audio_config["audio_codec"])
        codec_combo = ttk.Combobox(settings_row, textvariable=self.audio_codec_var, 
                                  values=["aac", "mp3", "opus", "vorbis", "flac"], width=10, state="readonly")
        codec_combo.pack(side="left", padx=(0, 15))
        
        ttk.Label(settings_row, text="Bitrate:").pack(side="left", padx=(0, 5))
        self.audio_bitrate_var = StringVar(value=self.job.audio_config["audio_bitrate"])
        bitrate_combo = ttk.Combobox(settings_row, textvariable=self.audio_bitrate_var,
                                   values=["96k", "128k", "192k", "256k", "320k"], width=8, state="readonly")
        bitrate_combo.pack(side="left")
        
        # Boutons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x")
        
        ttk.Button(button_frame, text="Detect Tracks", command=self._load_audio_tracks).pack(side="left")
        ttk.Button(button_frame, text="Select All", command=self._select_all_tracks).pack(side="left", padx=(10, 0))
        ttk.Button(button_frame, text="Select None", command=self._select_no_tracks).pack(side="left", padx=(5, 0))
        
        ttk.Button(button_frame, text="Cancel", command=self.window.destroy).pack(side="right")
        ttk.Button(button_frame, text="Apply to Encode", command=self._apply_settings).pack(side="right", padx=(0, 10)) # Renamed Apply

        # Extraction Section
        extract_frame = ttk.LabelFrame(main_frame, text="Extract Audio Track", padding=10)
        extract_frame.pack(fill="x", pady=(10,0))

        extract_controls_frame = ttk.Frame(extract_frame)
        extract_controls_frame.pack(fill="x")

        ttk.Label(extract_controls_frame, text="Format:").pack(side=tk.LEFT, padx=(0,5))
        self.extract_audio_format_var = StringVar(value="copy") # Default to copy stream
        extract_audio_format_options = ["copy", "aac", "mp3", "flac", "wav", "opus"]
        self.extract_audio_format_combo = ttk.Combobox(extract_controls_frame, textvariable=self.extract_audio_format_var,
                                                       values=extract_audio_format_options, state="readonly", width=10)
        self.extract_audio_format_combo.pack(side=tk.LEFT, padx=(0,10))
        self.extract_audio_format_combo.bind("<<ComboboxSelected>>", self._on_extract_format_change)


        self.extract_audio_bitrate_label = ttk.Label(extract_controls_frame, text="Bitrate:")
        self.extract_audio_bitrate_label.pack(side=tk.LEFT, padx=(5,5))
        self.extract_audio_bitrate_var = StringVar(value="192k")
        extract_audio_bitrate_options = ["96k", "128k", "160k", "192k", "256k", "320k"]
        self.extract_audio_bitrate_combo = ttk.Combobox(extract_controls_frame, textvariable=self.extract_audio_bitrate_var,
                                                       values=extract_audio_bitrate_options, width=8) # Not readonly, user can type
        self.extract_audio_bitrate_combo.pack(side=tk.LEFT, padx=(0,10))
        self._on_extract_format_change() # Initial state for bitrate combo

        extract_button = ttk.Button(extract_controls_frame, text="Extract Selected Track...", command=self._extract_selected_track)
        extract_button.pack(side=tk.LEFT, padx=(10,0))
        
        # Bind double-click pour toggle include
        self.tracks_tree.bind("<Double-1>", self._toggle_track_include)
        # Bind simple-click pour éditer l'action
        self.tracks_tree.bind("<Button-1>", self._edit_track_action)
    
    def _load_audio_tracks(self):
        """Charge les informations des pistes audio du fichier"""
        try:
            # Utiliser ffprobe pour obtenir les infos des pistes audio
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json", 
                "-show_streams", "-select_streams", "a", str(self.job.src_path)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                self.audio_tracks.clear()
                
                # Effacer l'arbre
                for item in self.tracks_tree.get_children():
                    self.tracks_tree.delete(item)
                
                # Ajouter chaque piste audio
                for i, stream in enumerate(data.get("streams", [])):
                    if stream.get("codec_type") == "audio":
                        # Utiliser l'index de flux réel de ffmpeg
                        track_index = stream.get("index")

                        # Configuration initiale des pistes
                        # Si aucune configuration n'existe, toutes les pistes sont incluses par défaut
                        if self.job.audio_config.get("tracks") is None:
                             is_included = True
                             action = "copy" # Par défaut, on copie
                        else:
                             track_config = next((t for t in self.job.audio_config["tracks"] if t["stream_index"] == track_index), None)
                             if track_config:
                                 is_included = track_config["included"]
                                 action = track_config["action"]
                             else: # Piste non trouvée dans la config, on la met par défaut
                                 is_included = True
                                 action = "copy"

                        track_info = {
                            "stream_index": track_index,
                            "codec": stream.get("codec_name", "n/a"),
                            "channels": stream.get("channels", "n/a"),
                            "sample_rate": stream.get("sample_rate", "n/a"),
                            "bit_rate": stream.get("bit_rate", "n/a"),
                            "language": stream.get("tags", {}).get("language", "n/a"),
                            "included": is_included,
                            "action": action
                        }
                        self.audio_tracks.append(track_info)
                        
                        # Ajouter à l'arbre
                        bitrate = track_info["bit_rate"]
                        if bitrate != "n/a" and str(bitrate).isdigit():
                            bitrate = f"{int(bitrate)//1000}k"
                        
                        values = (
                            "✓" if track_info["included"] else "✗",
                            f"{track_info['stream_index']}:{i}", # Affiche index_flux:index_piste_audio
                            track_info["action"].capitalize(),
                            track_info["codec"],
                            str(track_info["channels"]),
                            f"{track_info['sample_rate']} Hz" if track_info["sample_rate"] != "n/a" else "n/a",
                            bitrate,
                            track_info["language"],
                        )
                        
                        # Utiliser l'index de la liste python comme identifiant
                        item_id = self.tracks_tree.insert("", "end", values=values, iid=str(len(self.audio_tracks)-1))
                
                if not self.audio_tracks:
                    # Pas de pistes audio trouvées
                    self.tracks_tree.insert("", "end", values=("", "No audio tracks found", "", "", "", "", "", ""))
                    
            else:
                messagebox.showerror("Error", f"Failed to analyze audio tracks: {result.stderr}")
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load audio tracks: {e}")
    
    def _edit_track_action(self, event):
        """Affiche un combobox pour éditer l'action d'une piste"""
        # Détruire l'ancien combobox s'il existe
        if hasattr(self, "_action_editor") and self._action_editor.winfo_exists():
            self._action_editor.destroy()

        item_id = self.tracks_tree.identify_row(event.y)
        column_id = self.tracks_tree.identify_column(event.x)

        if not item_id or column_id != "#3":  # #3 correspond à la colonne "action"
            return
        
        # Obtenir la boîte englobante de la cellule
        x, y, width, height = self.tracks_tree.bbox(item_id, column="action")
        
        track_list_index = int(item_id)
        track_info = self.audio_tracks[track_list_index]
        
        # Créer le combobox par-dessus la cellule
        self._action_editor = ttk.Combobox(self.tracks_tree, values=["copy", "encode"], state="readonly")
        self._action_editor.set(track_info["action"])
        self._action_editor.place(x=x, y=y, width=width, height=height)
        
        # Callback pour quand une nouvelle valeur est sélectionnée
        def on_action_selected(event):
            new_action = self._action_editor.get()
            track_info["action"] = new_action
            self._action_editor.destroy()
            self._update_tree_item_display(item_id)

        self._action_editor.bind("<<ComboboxSelected>>", on_action_selected)
        self._action_editor.bind("<FocusOut>", lambda e: self._action_editor.destroy()) # Disparaît si on clique ailleurs
        self._action_editor.focus_set()
        self._action_editor.event_generate('<Button-1>') # Ouvrir la liste déroulante
    
    def _toggle_track_include(self, event):
        """Toggle l'inclusion d'une piste audio au double-clic"""
        # Identifier la colonne pour ne pas interférer avec l'éditeur d'action
        column_id = self.tracks_tree.identify_column(event.x)
        if column_id != "#1": # Colonne "Incl."
            return

        selected_items = self.tracks_tree.selection()
        if not selected_items:
            return
        
        item_id = selected_items[0]
        try:
            # L'ID de l'item est l'index dans notre liste self.audio_tracks
            track_list_index = int(item_id)
            track_info = self.audio_tracks[track_list_index]
            track_info["included"] = not track_info["included"]
            
            # Mettre à jour l'affichage de la ligne
            self._update_tree_item_display(item_id)

        except (ValueError, IndexError):
            # Ignorer si l'ID n'est pas un index valide
            pass
    
    def _select_all_tracks(self):
        """Sélectionne toutes les pistes audio pour inclusion"""
        for i, track in enumerate(self.audio_tracks):
            track["included"] = True
            self._update_tree_item_display(str(i))
    
    def _select_no_tracks(self):
        """Désélectionne toutes les pistes audio"""
        for i, track in enumerate(self.audio_tracks):
            track["included"] = False
            self._update_tree_item_display(str(i))
    
    def _update_tree_item_display(self, item_id):
        """Met à jour une seule ligne dans le Treeview pour refléter son état."""
        try:
            track_list_index = int(item_id)
            track_info = self.audio_tracks[track_list_index]
            
            # Recalculer le bitrate pour l'affichage
            bitrate = track_info["bit_rate"]
            if bitrate != "n/a" and str(bitrate).isdigit():
                bitrate = f"{int(bitrate)//1000}k"
            
            values = (
                "✓" if track_info["included"] else "✗",
                f"{track_info['stream_index']}:{track_list_index}",
                track_info["action"].capitalize(),
                track_info["codec"],
                str(track_info["channels"]),
                f"{track_info['sample_rate']} Hz" if track_info['sample_rate'] != "n/a" else "n/a",
                bitrate,
                track_info["language"],
            )
            # S'assurer que l'item existe avant de le modifier
            if self.tracks_tree.exists(item_id):
                 self.tracks_tree.item(item_id, values=values, iid=item_id)
            else: # Si l'item n'existe pas, on le crée
                 self.tracks_tree.insert("", "end", values=values, iid=item_id)

        except (ValueError, IndexError):
            pass # Ignorer les erreurs si l'item n'est plus valide

    def _refresh_tree_display(self):
        """Met à jour l'affichage de tout l'arbre"""
        for i in range(len(self.audio_tracks)):
            self._update_tree_item_display(str(i))
    
    def _apply_settings(self):
        """Applique les paramètres audio au job"""
        # Sauvegarder la configuration de réencodage
        self.job.audio_config["audio_codec"] = self.audio_codec_var.get()
        self.job.audio_config["audio_bitrate"] = self.audio_bitrate_var.get()
        
        # Sauvegarder la configuration détaillée de chaque piste
        tracks_config = [
            {
                "stream_index": track["stream_index"],
                "included": track["included"],
                "action": track["action"]
            } 
            for track in self.audio_tracks
        ]
        self.job.audio_config["tracks"] = tracks_config
        
        messagebox.showinfo("Success", "Audio configuration applied successfully!")
        self.window.destroy()

    def _on_extract_format_change(self, event=None):
        """Enable/disable bitrate combobox based on selected extract format."""
        selected_format = self.extract_audio_format_var.get()
        lossy_formats = ["aac", "mp3", "opus", "vorbis"] # vorbis not in options yet but good to list

        if selected_format in lossy_formats:
            self.extract_audio_bitrate_combo.config(state="normal")
            self.extract_audio_bitrate_label.config(state="normal")
        else: # copy, flac, wav (lossless or copy)
            self.extract_audio_bitrate_combo.config(state="disabled")
            self.extract_audio_bitrate_label.config(state="disabled")

    def _extract_selected_track(self):
        selected_items = self.tracks_tree.selection()
        if not selected_items:
            messagebox.showwarning("No Selection", "Please select an audio track to extract.", parent=self.window)
            return

        item_id = selected_items[0] # This is the python list index
        try:
            track_list_index = int(item_id)
            track_info = self.audio_tracks[track_list_index]
        except (ValueError, IndexError):
            messagebox.showerror("Error", "Invalid track selection.", parent=self.window)
            return

        source_ffmpeg_track_index = track_info["stream_index"] # This is the actual index from ffprobe
        extract_format = self.extract_audio_format_var.get()

        # Determine default extension and initial filename
        default_ext = extract_format
        if extract_format == "copy":
            # Try to guess original extension from codec, or use a generic one
            codec_to_ext = {"aac": "m4a", "mp3": "mp3", "flac": "flac", "pcm_s16le": "wav", "opus": "opus"}
            default_ext = codec_to_ext.get(track_info["codec"], "audio") # Fallback to .audio

        default_filename = f"{self.job.src_path.stem}_audio_track_{source_ffmpeg_track_index}.{default_ext}"

        # Determine output directory
        # Try to use the main output folder if set in MainWindow, else source file's parent
        output_dir = self.job.src_path.parent # Default
        if hasattr(self.window.master, "output_folder"): # Access MainWindow's output_folder
            main_output_folder_str = self.window.master.output_folder.get()
            if main_output_folder_str and not main_output_folder_str.startswith("No output"):
                output_dir = Path(main_output_folder_str)

        output_dir.mkdir(parents=True, exist_ok=True)

        save_path_str = filedialog.asksaveasfilename(
            parent=self.window,
            title="Save Extracted Audio Track As",
            initialdir=str(output_dir),
            initialfile=default_filename,
            defaultextension=f".{default_ext}",
            filetypes=[(f"{default_ext.upper()} files", f"*.{default_ext}"), ("All files", "*.*")]
        )

        if not save_path_str:
            return

        save_path = Path(save_path_str)

        # Prepare FFmpeg command
        cmd_extract = ["ffmpeg", "-y", "-i", str(self.job.src_path), "-map", f"0:{source_ffmpeg_track_index}"]

        if extract_format == "copy":
            cmd_extract.extend(["-c:a", "copy"])
        else:
            cmd_extract.extend(["-c:a", extract_format])
            if self.extract_audio_bitrate_combo.cget('state') != 'disabled': # Check if bitrate is applicable
                bitrate = self.extract_audio_bitrate_var.get()
                if bitrate:
                    cmd_extract.extend(["-b:a", bitrate])

        cmd_extract.append(str(save_path))

        # Run in a thread
        self.window.master.status_label.config(text=f"Extracting audio to {save_path.name}...") # Use main window status

        def do_extract_audio():
            try:
                process = subprocess.run(cmd_extract, capture_output=True, text=True, check=True, encoding='utf-8')
                self.window.master.status_label.config(text=f"Audio extracted: {save_path.name}")
                messagebox.showinfo("Success", f"Audio track extracted successfully to:\n{save_path}", parent=self.window)
            except FileNotFoundError:
                messagebox.showerror("Error", "FFmpeg not found.", parent=self.window)
                self.window.master.status_label.config(text="Error: FFmpeg not found.")
            except subprocess.CalledProcessError as e:
                error_msg = f"FFmpeg extraction error: {e.stderr or e.stdout or 'Unknown FFmpeg error'}"
                print(error_msg)
                messagebox.showerror("Error", error_msg, parent=self.window)
                self.window.master.status_label.config(text="Audio extraction failed.")
            except Exception as e:
                messagebox.showerror("Error", f"An unexpected error occurred: {e}", parent=self.window)
                self.window.master.status_label.config(text="Audio extraction error.")

        threading.Thread(target=do_extract_audio, daemon=True).start()
