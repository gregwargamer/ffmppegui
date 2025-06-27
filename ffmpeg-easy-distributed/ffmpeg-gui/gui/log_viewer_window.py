import time
from tkinter import Toplevel, ttk, BooleanVar, StringVar, Text, filedialog, messagebox


class LogViewerWindow:
    """Fenêtre pour afficher les logs FFmpeg en temps réel"""
    
    def __init__(self, parent):
        self.window = Toplevel(parent)
        self.window.title("FFmpeg Logs")
        self.window.geometry("800x600")
        self.window.minsize(600, 400)
        
        # Créer l'interface
        self._build_interface()
        
        # Liste des logs par job
        self.job_logs = {}
        
    def _build_interface(self):
        """Construit l'interface du viewer de logs"""
        main_frame = ttk.Frame(self.window, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # Barre d'outils en haut
        toolbar = ttk.Frame(main_frame)
        toolbar.pack(fill="x", pady=(0, 10))
        
        # Sélecteur de job
        ttk.Label(toolbar, text="Job:").pack(side="left", padx=(0, 5))
        self.job_var = StringVar()
        self.job_combo = ttk.Combobox(toolbar, textvariable=self.job_var, width=40, state="readonly")
        self.job_combo.pack(side="left", padx=(0, 10))
        self.job_combo.bind("<<ComboboxSelected>>", self._on_job_selected)
        
        # Boutons de contrôle
        ttk.Button(toolbar, text="Clear", command=self._clear_logs).pack(side="left", padx=(0, 5))
        ttk.Button(toolbar, text="Save to File", command=self._save_logs).pack(side="left", padx=(0, 5))
        
        # Auto-scroll checkbox
        self.autoscroll_var = BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="Auto-scroll", variable=self.autoscroll_var).pack(side="right")
        
        # Zone de texte avec scrollbar
        text_frame = ttk.Frame(main_frame)
        text_frame.pack(fill="both", expand=True)
        
        self.text_area = Text(text_frame, wrap="none", font=("Consolas", 10))
        
        # Scrollbars
        v_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text_area.yview)
        h_scroll = ttk.Scrollbar(text_frame, orient="horizontal", command=self.text_area.xview)
        
        self.text_area.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        
        # Pack scrollbars et text area
        v_scroll.pack(side="right", fill="y")
        h_scroll.pack(side="bottom", fill="x")
        self.text_area.pack(side="left", fill="both", expand=True)
        
        # Tags pour coloration
        self.text_area.tag_configure("error", foreground="red")
        self.text_area.tag_configure("warning", foreground="orange")
        self.text_area.tag_configure("info", foreground="blue")
        self.text_area.tag_configure("progress", foreground="green")
    
    def add_job(self, job):
        """Ajoute un nouveau job à surveiller"""
        job_name = f"{job.src_path.name} -> {job.dst_path.name}"
        self.job_logs[str(id(job))] = {
            "name": job_name,
            "logs": [],
            "job": job
        }
        self._update_job_list()
    
    def _update_job_list(self):
        """Met à jour la liste des jobs dans le combobox"""
        job_names = [data["name"] for data in self.job_logs.values()]
        self.job_combo['values'] = job_names
        if job_names and not self.job_var.get():
            self.job_combo.set(job_names[0])
            self._on_job_selected()
    
    def _on_job_selected(self, event=None):
        """Affiche les logs du job sélectionné"""
        selected_name = self.job_var.get()
        if not selected_name:
            return
            
        # Trouver le job correspondant
        for job_data in self.job_logs.values():
            if job_data["name"] == selected_name:
                self._display_logs(job_data["logs"])
                break
    
    def _display_logs(self, logs):
        """Affiche les logs dans la zone de texte"""
        self.text_area.delete(1.0, "end")
        for log_entry in logs:
            self._append_log_line(log_entry["text"], log_entry["type"])
    
    def add_log(self, job, text, log_type="info"):
        """Ajoute une ligne de log pour un job"""
        job_id = str(id(job))
        if job_id not in self.job_logs:
            self.add_job(job)
        
        # Ajouter le log
        self.job_logs[job_id]["logs"].append({
            "text": text,
            "type": log_type,
            "timestamp": time.strftime("%H:%M:%S")
        })
        
        # Si ce job est actuellement affiché, mettre à jour l'affichage
        current_job_name = self.job_var.get()
        if current_job_name == self.job_logs[job_id]["name"]:
            self._append_log_line(text, log_type)
    
    def _append_log_line(self, text, log_type="info"):
        """Ajoute une ligne de texte à la zone de texte"""
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}\n"
        
        self.text_area.insert("end", line, log_type)
        
        # Auto-scroll si activé
        if self.autoscroll_var.get():
            self.text_area.see("end")
    
    def _clear_logs(self):
        """Efface les logs du job actuel"""
        selected_name = self.job_var.get()
        if not selected_name:
            return
            
        for job_id, job_data in self.job_logs.items():
            if job_data["name"] == selected_name:
                job_data["logs"].clear()
                break
        
        self.text_area.delete(1.0, "end")
    
    def _save_logs(self):
        """Sauvegarde les logs dans un fichier"""
        selected_name = self.job_var.get()
        if not selected_name:
            messagebox.showwarning("No Selection", "Please select a job first.")
            return
            
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Save logs to file"
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    content = self.text_area.get(1.0, "end")
                    f.write(content)
                messagebox.showinfo("Success", f"Logs saved to {filename}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save logs: {e}")