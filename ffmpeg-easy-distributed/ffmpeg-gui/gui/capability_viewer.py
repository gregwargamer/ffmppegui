import tkinter as tk
from tkinter import ttk, messagebox
from typing import List, Dict, Any

from shared.messages import ServerInfo, JobConfiguration, EncoderType
from core.server_discovery import ServerDiscovery
from core.capability_matcher import CapabilityMatcher

class CapabilityViewerWindow:
    def __init__(self, parent, server_discovery: ServerDiscovery, capability_matcher: CapabilityMatcher):
        self.parent = parent
        self.server_discovery = server_discovery
        self.capability_matcher = capability_matcher
        
        self.window = tk.Toplevel(parent)
        self.window.title("Visualiseur de Capacités Serveurs")
        self.window.geometry("1000x700")
        self.window.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        self.build_ui()
        self._refresh_display()

    def _on_closing(self):
        self.window.destroy()

    def build_ui(self):
        main_frame = ttk.Frame(self.window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Section de sélection de job (pour tester la compatibilité)
        job_frame = ttk.LabelFrame(main_frame, text="Tester la Compatibilité d'un Job")
        job_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(job_frame, text="Encodeur Requis:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.encoder_var = tk.StringVar(value="libx264")
        self.encoder_entry = ttk.Entry(job_frame, textvariable=self.encoder_var, width=30)
        self.encoder_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

        ttk.Label(job_frame, text="Résolution (ex: 1920x1080):").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.resolution_var = tk.StringVar(value="1920x1080")
        self.resolution_entry = ttk.Entry(job_frame, textvariable=self.resolution_var, width=30)
        self.resolution_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")

        ttk.Button(job_frame, text="Calculer Compatibilité", command=self._calculate_compatibility).grid(row=0, column=2, rowspan=2, padx=10, pady=5, sticky="ns")

        job_frame.grid_columnconfigure(1, weight=1)

        # Treeview pour afficher les capacités et la compatibilité
        columns = ("name", "ip_port", "status", "cpu", "memory", "software_encoders", "hardware_encoders", "compatibility_score", "performance_score", "load_score", "total_score", "warnings")
        self.tree = ttk.Treeview(main_frame, columns=columns, show="headings")
        
        self.tree.heading("name", text="Nom")
        self.tree.heading("ip_port", text="Adresse:Port")
        self.tree.heading("status", text="Statut")
        self.tree.heading("cpu", text="CPU")
        self.tree.heading("memory", text="RAM (GB)")
        self.tree.heading("software_encoders", text="Encodeurs SW")
        self.tree.heading("hardware_encoders", text="Encodeurs HW")
        self.tree.heading("compatibility_score", text="Compatibilité")
        self.tree.heading("performance_score", text="Performance")
        self.tree.heading("load_score", text="Charge")
        self.tree.heading("total_score", text="Score Total")
        self.tree.heading("warnings", text="Avertissements")
        
        self.tree.column("name", width=80)
        self.tree.column("ip_port", width=100)
        self.tree.column("status", width=60)
        self.tree.column("cpu", width=60)
        self.tree.column("memory", width=60)
        self.tree.column("software_encoders", width=100)
        self.tree.column("hardware_encoders", width=100)
        self.tree.column("compatibility_score", width=80, anchor="center")
        self.tree.column("performance_score", width=80, anchor="center")
        self.tree.column("load_score", width=60, anchor="center")
        self.tree.column("total_score", width=70, anchor="center")
        self.tree.column("warnings", width=150)

        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        ttk.Button(main_frame, text="Actualiser les Serveurs", command=self._refresh_display).pack(pady=10)

    def _refresh_display(self):
        """Met à jour l'affichage des capacités de tous les serveurs connus."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        servers = self.server_discovery.get_all_servers().values()
        for server in servers:
            self._add_server_to_tree(server)

    def _add_server_to_tree(self, server_info: ServerInfo, job_config: JobConfiguration = None):
        sw_encoders = ", ".join(server_info.capabilities.software_encoders) if server_info.capabilities.software_encoders else "N/A"
        hw_encoders = ", ".join([f"{k}: {', '.join(v)}" for k, v in server_info.capabilities.hardware_encoders.items() if v]) if server_info.capabilities.hardware_encoders else "N/A"
        
        compatibility_score = "N/A"
        performance_score = "N/A"
        load_score = "N/A"
        total_score = "N/A"
        warnings_str = ""

        if job_config:
            server_score = self.capability_matcher._evaluate_server(job_config, server_info)
            compatibility_score = f"{server_score.compatibility_score:.2f}"
            performance_score = f"{server_score.performance_score:.2f}"
            load_score = f"{server_score.load_score:.2f}"
            total_score = f"{server_score.total_score:.2f}"
            warnings_str = "; ".join(server_score.warnings)

        self.tree.insert("", "end", iid=server_info.server_id, values=(
            server_info.name,
            f"{server_info.ip}:{server_info.port}",
            server_info.status.value,
            f"{server_info.capabilities.cpu_cores} ({server_info.capabilities.current_load:.1%})",
            f"{server_info.capabilities.memory_gb:.1f}",
            sw_encoders,
            hw_encoders,
            compatibility_score,
            performance_score,
            load_score,
            total_score,
            warnings_str
        ))

    def _calculate_compatibility(self):
        encoder = self.encoder_var.get().strip()
        resolution = self.resolution_var.get().strip()

        if not encoder:
            messagebox.showwarning("Entrée Manquante", "Veuillez spécifier un encodeur.")
            return
        
        # Créer un job de test (simplifié)
        test_job = JobConfiguration(
            job_id="test_job",
            input_file="", # Non pertinent pour la compatibilité
            output_file="", # Non pertinent
            encoder=encoder,
            encoder_type=EncoderType.SOFTWARE, # Type générique pour le test
            preset="", quality_mode="", quality_value="",
            filters=[], ffmpeg_args=[], required_capabilities=[],
            priority=5, estimated_duration=0,
            file_size=0, resolution=resolution, codec="", container=""
        )

        # Effacer l'affichage actuel et recalculer
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        servers = self.server_discovery.get_all_servers().values()
        for server in servers:
            self._add_server_to_tree(server, test_job)

        # Afficher les suggestions si aucun serveur n'est idéal
        best_servers = self.capability_matcher.find_best_servers(test_job, list(servers))
        if not best_servers or best_servers[0].total_score < 0.5: # Si le meilleur score est faible
            suggestions = self.capability_matcher.suggest_alternatives(test_job, list(servers))
            if suggestions:
                messagebox.showinfo("Suggestions", "Aucun serveur idéal trouvé. Suggestions:\n" + "\n".join(suggestions))
            else:
                messagebox.showinfo("Aucune Suggestion", "Aucun serveur idéal trouvé et aucune alternative suggérée.")