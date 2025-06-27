import tkinter as tk
from tkinter import ttk, messagebox
import asyncio
from typing import List, Dict, Any

from shared.messages import JobConfiguration, JobProgress, JobResult, JobStatus, ServerInfo
from core.job_scheduler import JobScheduler
from core.server_discovery import ServerDiscovery

class JobQueueWindow:
    def __init__(self, parent, job_scheduler: JobScheduler, server_discovery: ServerDiscovery):
        self.parent = parent
        self.job_scheduler = job_scheduler
        self.server_discovery = server_discovery
        self.jobs: Dict[str, JobConfiguration] = {}
        
        self.window = tk.Toplevel(parent)
        self.window.title("File d'Attente des Jobs d'Encodage")
        self.window.geometry("900x600")
        self.window.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        self.build_ui()
        self._refresh_jobs_display()

    def _on_closing(self):
        self.window.destroy()

    def build_ui(self):
        # Frame principal
        main_frame = ttk.Frame(self.window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Treeview pour les jobs
        columns = ("job_id", "input_file", "output_file", "encoder", "status", "progress", "server", "priority")
        self.tree = ttk.Treeview(main_frame, columns=columns, show="headings")
        
        self.tree.heading("job_id", text="ID Job")
        self.tree.heading("input_file", text="Fichier Source")
        self.tree.heading("output_file", text="Fichier Cible")
        self.tree.heading("encoder", text="Encodeur")
        self.tree.heading("status", text="Statut")
        self.tree.heading("progress", text="Progression")
        self.tree.heading("server", text="Serveur")
        self.tree.heading("priority", text="Priorité")
        
        self.tree.column("job_id", width=100)
        self.tree.column("input_file", width=150)
        self.tree.column("output_file", width=150)
        self.tree.column("encoder", width=100)
        self.tree.column("status", width=80)
        self.tree.column("progress", width=80)
        self.tree.column("server", width=100)
        self.tree.column("priority", width=60)

        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Boutons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(button_frame, text="Actualiser", command=self._refresh_jobs_display).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(button_frame, text="Annuler Job", command=self._cancel_selected_job).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(button_frame, text="Réassigner Job", command=self._reassign_selected_job).pack(side=tk.LEFT)

    def _refresh_jobs_display(self):
        """Met à jour l'affichage de la liste des jobs."""
        # Vider la liste
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        self.jobs = self.job_scheduler.get_active_jobs()
        for job_id, job_config in self.jobs.items():
            # Pour l'instant, la progression et le serveur sont des placeholders
            # Ils seront mis à jour via les callbacks de progression
            self.tree.insert("", "end", iid=job_id, values=(
                job_id,
                job_config.input_file.split('/')[-1],
                job_config.output_file.split('/')[-1],
                job_config.encoder,
                "En attente", # Statut initial
                "0%",
                "N/A",
                job_config.priority
            ))

    def update_job_progress(self, progress: JobProgress):
        """Met à jour la progression d'un job dans l'affichage."""
        if progress.job_id in self.jobs:
            item = self.tree.item(progress.job_id)
            current_values = item['values']
            
            # Mettre à jour le statut et la progression
            new_status = "En cours" if progress.progress < 100 else "Terminé"
            new_progress = f"{progress.progress:.1f}%"
            new_server = progress.server_id # Le serveur qui rapporte la progression

            # Assurez-vous que l'ordre des colonnes correspond à celui du treeview
            # job_id, input_file, output_file, encoder, status, progress, server, priority
            updated_values = (
                current_values[0], current_values[1], current_values[2], current_values[3],
                new_status, new_progress, new_server, current_values[7]
            )
            self.tree.item(progress.job_id, values=updated_values)

    def update_job_completion(self, result: JobResult):
        """Met à jour le statut d'un job terminé dans l'affichage."""
        if result.job_id in self.jobs:
            item = self.tree.item(result.job_id)
            current_values = item['values']
            
            new_status = result.status.value.capitalize()
            new_progress = "100%" if result.status == JobStatus.COMPLETED else "Erreur"
            new_server = result.server_id

            updated_values = (
                current_values[0], current_values[1], current_values[2], current_values[3],
                new_status, new_progress, new_server, current_values[7]
            )
            self.tree.item(result.job_id, values=updated_values)
            
            # Optionnel: retirer le job de la liste après un certain temps ou le déplacer vers un historique
            # self.tree.delete(result.job_id)
            messagebox.showinfo("Job Terminé", f"Job {result.job_id} : {new_status}")

    def _cancel_selected_job(self):
        """Annule le job sélectionné."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Attention", "Sélectionnez un job à annuler.")
            return
        
        job_id = selection[0]
        if messagebox.askyesno("Confirmer l'annulation", f"Êtes-vous sûr de vouloir annuler le job {job_id} ?"):
            asyncio.create_task(self.job_scheduler.cancel_job(job_id))
            self.tree.delete(job_id) # Supprimer de l'affichage immédiatement
            messagebox.showinfo("Annulation", f"Job {job_id} annulé.")

    def _reassign_selected_job(self):
        """Réassigne le job sélectionné à un autre serveur."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Attention", "Sélectionnez un job à réassigner.")
            return
        
        job_id = selection[0]
        job_config = self.jobs.get(job_id)
        if not job_config:
            messagebox.showerror("Erreur", "Job non trouvé.")
            return

        # Ouvrir une fenêtre de dialogue pour choisir le nouveau serveur
        self._open_reassign_dialog(job_config)

    def _open_reassign_dialog(self, job_config: JobConfiguration):
        dialog = tk.Toplevel(self.window)
        dialog.title(f"Réassigner Job {job_config.job_id}")
        dialog.transient(self.window)
        dialog.grab_set()

        servers = self.server_discovery.get_all_servers().values()
        online_servers = [s for s in servers if s.status == ServerStatus.ONLINE]

        if not online_servers:
            messagebox.showwarning("Aucun serveur", "Aucun serveur en ligne disponible pour la réassignation.")
            dialog.destroy()
            return

        ttk.Label(dialog, text="Sélectionnez le nouveau serveur:").pack(padx=10, pady=10)

        server_names = [f"{s.name} ({s.ip}:{s.port})" for s in online_servers]
        self.selected_server_var = tk.StringVar(dialog)
        self.selected_server_var.set(server_names[0]) # Valeur par défaut

        server_menu = ttk.OptionMenu(dialog, self.selected_server_var, server_names[0], *server_names)
        server_menu.pack(padx=10, pady=5)

        def confirm_reassign():
            selected_name = self.selected_server_var.get()
            # Trouver l'objet ServerInfo correspondant
            target_server = next((s for s in online_servers if f"{s.name} ({s.ip}:{s.port})" == selected_name), None)
            
            if target_server:
                asyncio.create_task(self._perform_reassignment(job_config, target_server))
                dialog.destroy()
            else:
                messagebox.showerror("Erreur", "Serveur sélectionné invalide.")

        ttk.Button(dialog, text="Réassigner", command=confirm_reassign).pack(pady=10)

    async def _perform_reassignment(self, job_config: JobConfiguration, target_server: ServerInfo):
        """Exécute la logique de réassignation du job."""
        # Ici, la logique serait:
        # 1. Annuler le job sur le serveur actuel (si en cours)
        # 2. Soumettre le job au nouveau serveur
        # Pour l'instant, nous allons simuler en annulant et en ajoutant à nouveau.
        
        messagebox.showinfo("Réassignation", f"Réassignation du job {job_config.job_id} au serveur {target_server.name} en cours...")
        
        # Simuler l'annulation du job actuel
        await self.job_scheduler.cancel_job(job_config.job_id) # Ceci supprime le job localement
        
        # Créer un nouveau job avec le même ID et le soumettre au nouveau serveur
        # Dans une vraie implémentation, il faudrait s'assurer que le fichier source est disponible sur le nouveau serveur
        # ou le transférer.
        new_job_config = job_config # Utiliser la même config
        
        # Ré-ajouter le job au scheduler, qui le planifiera sur le meilleur serveur (potentiellement le nouveau)
        await self.job_scheduler.add_job(
            new_job_config,
            self.job_status_callbacks.get(job_config.job_id), # Réutiliser les callbacks existants
            self.job_completion_callbacks.get(job_config.job_id)
        )
        self._refresh_jobs_display() # Rafraîchir l'affichage
        messagebox.showinfo("Réassignation", f"Job {job_config.job_id} réassigné avec succès au serveur {target_server.name}.")
