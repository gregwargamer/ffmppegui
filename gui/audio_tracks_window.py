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
        
        # Mode de traitement audio
        mode_frame = ttk.LabelFrame(main_frame, text="Audio Processing Mode", padding=10)
        mode_frame.pack(fill="x", pady=(0, 10))
        
        self.audio_mode_var = StringVar(value=self.job.audio_config["mode"])
        
        ttk.Radiobutton(mode_frame, text="Auto (Copy if compatible, encode if needed)", 
                       variable=self.audio_mode_var, value="auto").pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Copy all audio tracks (no re-encoding)", 
                       variable=self.audio_mode_var, value="copy").pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Re-encode all audio tracks", 
                       variable=self.audio_mode_var, value="encode").pack(anchor="w")
        ttk.Radiobutton(mode_frame, text="Remove all audio tracks", 
                       variable=self.audio_mode_var, value="remove").pack(anchor="w")
        
        # Liste des pistes audio
        tracks_frame = ttk.LabelFrame(main_frame, text="Audio Tracks", padding=10)
        tracks_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        # Treeview pour les pistes
        columns = ("track", "codec", "channels", "sample_rate", "bitrate", "language", "include")
        self.tracks_tree = ttk.Treeview(tracks_frame, columns=columns, show="headings", height=8)
        
        for col, label in zip(columns, ["Track", "Codec", "Channels", "Sample Rate", "Bitrate", "Language", "Include"]):
            self.tracks_tree.heading(col, text=label)
            if col == "include":
                self.tracks_tree.column(col, width=60)
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
        ttk.Button(button_frame, text="Apply", command=self._apply_settings).pack(side="right", padx=(0, 10))
        
        # Bind double-click pour toggle include
        self.tracks_tree.bind("<Double-1>", self._toggle_track_include)
    
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
                        track_info = {
                            "index": i,
                            "codec": stream.get("codec_name", "unknown"),
                            "channels": stream.get("channels", "unknown"),
                            "sample_rate": stream.get("sample_rate", "unknown"),
                            "bit_rate": stream.get("bit_rate", "unknown"),
                            "language": stream.get("tags", {}).get("language", "unknown"),
                            "included": i in self.job.audio_config.get("selected_tracks", []) or len(self.job.audio_config.get("selected_tracks", [])) == 0
                        }
                        self.audio_tracks.append(track_info)
                        
                        # Ajouter à l'arbre
                        bitrate = track_info["bit_rate"]
                        if bitrate != "unknown" and bitrate.isdigit():
                            bitrate = f"{int(bitrate)//1000}k"
                        
                        values = (
                            f"Track {i}",
                            track_info["codec"],
                            str(track_info["channels"]),
                            f"{track_info['sample_rate']} Hz" if track_info["sample_rate"] != "unknown" else "unknown",
                            bitrate,
                            track_info["language"],
                            "" if track_info["included"] else ""
                        )
                        
                        item_id = self.tracks_tree.insert("", "end", values=values)
                        # Stocker l'index de la piste
                        self.tracks_tree.set(item_id, "#0", str(i))
                
                if not self.audio_tracks:
                    # Pas de pistes audio trouvées
                    self.tracks_tree.insert("", "end", values=("No audio tracks found", "", "", "", "", "", ""))
                    
            else:
                messagebox.showerror("Error", f"Failed to analyze audio tracks: {result.stderr}")
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load audio tracks: {e}")
    
    def _toggle_track_include(self, event):
        """Toggle l'inclusion d'une piste audio"""
        item = self.tracks_tree.selection()[0] if self.tracks_tree.selection() else None
        if not item:
            return
            
        try:
            track_index = int(self.tracks_tree.set(item, "#0"))
            track_info = self.audio_tracks[track_index]
            track_info["included"] = not track_info["included"]
            
            # Mettre à jour l'affichage
            values = list(self.tracks_tree.item(item, "values"))
            values[6] = "" if track_info["included"] else ""
            self.tracks_tree.item(item, values=values)
            
        except (ValueError, IndexError):
            pass
    
    def _select_all_tracks(self):
        """Sélectionne toutes les pistes audio"""
        for track in self.audio_tracks:
            track["included"] = True
        self._refresh_tree_display()
    
    def _select_no_tracks(self):
        """Désélectionne toutes les pistes audio"""
        for track in self.audio_tracks:
            track["included"] = False
        self._refresh_tree_display()
    
    def _refresh_tree_display(self):
        """Met à jour l'affichage de l'arbre"""
        for item in self.tracks_tree.get_children():
            try:
                track_index = int(self.tracks_tree.set(item, "#0"))
                track_info = self.audio_tracks[track_index]
                values = list(self.tracks_tree.item(item, "values"))
                values[6] = "" if track_info["included"] else ""
                self.tracks_tree.item(item, values=values)
            except (ValueError, IndexError):
                pass
    
    def _apply_settings(self):
        """Applique les paramètres audio au job"""
        # Sauvegarder la configuration
        self.job.audio_config["mode"] = self.audio_mode_var.get()
        self.job.audio_config["audio_codec"] = self.audio_codec_var.get()
        self.job.audio_config["audio_bitrate"] = self.audio_bitrate_var.get()
        
        # Sauvegarder les pistes sélectionnées
        selected_tracks = [track["index"] for track in self.audio_tracks if track["included"]]
        self.job.audio_config["selected_tracks"] = selected_tracks
        
        messagebox.showinfo("Success", "Audio configuration applied successfully!")
        self.window.destroy()
