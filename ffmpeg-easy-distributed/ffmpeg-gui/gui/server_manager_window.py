import tkinter as tk
from tkinter import ttk, messagebox
import asyncio
import websockets
import time
from typing import List, Dict, Any

from shared.protocol import Message, MessageType
from shared.messages import ServerInfo, ServerStatus
from core.server_discovery import ServerDiscovery

class ServerManagerWindow:
    def __init__(self, parent, server_discovery: ServerDiscovery, loop, run_async_func):
        self.parent = parent
        self.server_discovery = server_discovery
        self.loop = loop
        self.run_async_func = run_async_func
        self.servers: Dict[str, ServerInfo] = {}
        
        self.window = tk.Toplevel(parent)
        self.window.title("Gestion des Serveurs d'Encodage")
        self.window.geometry("800x600")
        self.window.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # Donner le focus à la fenêtre
        self.window.grab_set()
        self.window.focus_set()
        
        self.build_ui()
        self.server_discovery.register_server_update_callback(self.refresh_display)
        self.run_async_func(self._initial_load_servers())
        
        # Mettre le focus sur le champ IP après construction de l'interface
        self.window.after(100, lambda: self.ip_entry.focus_set())

    def _on_closing(self):
        self.window.destroy()

    async def _initial_load_servers(self):
        """Charge les serveurs connus au démarrage de la fenêtre."""
        # This method is now called via run_async_func.
        # get_all_servers is synchronous, call it directly.
        self.servers = self.server_discovery.get_all_servers()
        self.refresh_display(list(self.servers.values()))
        # refresh_servers is async and will be awaited.
        await self.refresh_servers()


    def build_ui(self):
        # Frame principal
        main_frame = ttk.Frame(self.window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Ajout de serveur
        add_frame = ttk.LabelFrame(main_frame, text="Ajouter un Serveur")
        add_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(add_frame, text="IP:").grid(row=0, column=0, padx=5, pady=5)
        self.ip_var = tk.StringVar(master=self.window, value="localhost")
        self.ip_entry = ttk.Entry(add_frame, textvariable=self.ip_var, width=15)
        self.ip_entry.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(add_frame, text="Port:").grid(row=0, column=2, padx=5, pady=5)
        self.port_var = tk.StringVar(master=self.window, value="8765")
        self.port_entry = ttk.Entry(add_frame, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=3, padx=5, pady=5)
        
        ttk.Button(add_frame, text="Ajouter", command=self.add_server_action).grid(row=0, column=4, padx=5, pady=5)
        
        # Liste des serveurs
        list_frame = ttk.LabelFrame(main_frame, text="Serveurs Connus")
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        # Treeview
        columns = ("name", "ip_port", "status", "jobs", "cpu", "memory", "software_encoders", "hardware_encoders")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        
        self.tree.heading("name", text="Nom")
        self.tree.heading("ip_port", text="Adresse:Port")
        self.tree.heading("status", text="Statut")
        self.tree.heading("jobs", text="Jobs (Actif/Max)")
        self.tree.heading("cpu", text="CPU (Cœurs/Charge)")
        self.tree.heading("memory", text="RAM (GB)")
        self.tree.heading("software_encoders", text="Encodeurs SW")
        self.tree.heading("hardware_encoders", text="Encodeurs HW")
        
        self.tree.column("name", width=100)
        self.tree.column("ip_port", width=120)
        self.tree.column("status", width=80)
        self.tree.column("jobs", width=100)
        self.tree.column("cpu", width=100)
        self.tree.column("memory", width=80)
        self.tree.column("software_encoders", width=120)
        self.tree.column("hardware_encoders", width=120)

        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Boutons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(button_frame, text="Actualiser", command=lambda: self.run_async_func(self.refresh_servers())).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(button_frame, text="Supprimer", command=self.remove_server_action).pack(side=tk.LEFT, padx=(0, 5)) # remove_server_action will call run_async_func
        ttk.Button(button_frame, text="Test Ping", command=lambda: self.run_async_func(self.ping_selected_servers())).pack(side=tk.LEFT)

    def add_server_action(self):
        """Action pour ajouter un serveur."""
        ip = self.ip_var.get().strip()
        try:
            port = int(self.port_var.get())
        except ValueError:
            messagebox.showerror("Erreur", "Port invalide")
            return
        
        if not ip:
            messagebox.showerror("Erreur", "IP requise")
            return
        
        self.run_async_func(self._add_server_and_update_ui(ip, port))

    async def _add_server_and_update_ui(self, ip: str, port: int):
        """Ajoute un serveur et met à jour l'UI."""
        server_info = await self.server_discovery.add_server(ip, port)
        if server_info:
            messagebox.showinfo("Succès", f"Serveur {ip}:{port} ({server_info.name}) ajouté et connecté.")
        else:
            messagebox.showerror("Erreur", f"Impossible de se connecter à {ip}:{port}. Vérifiez l'adresse et le port.")

    async def refresh_servers(self):
        """Actualise l'état de tous les serveurs connus."""
        self.servers = self.server_discovery.get_all_servers()
        for server_id, server_info in self.servers.items():
            # Tenter de se connecter ou de pinger pour rafraîchir l'état
            if server_info.status == ServerStatus.OFFLINE:
                await self.server_discovery.distributed_client.connect_to_server(server_info.ip, server_info.port)
            else:
                await self.server_discovery.distributed_client.ping_server(server_id)
        self.refresh_display(list(self.servers.values()))

    def refresh_display(self, updated_servers: List[ServerInfo]):
        """Met à jour l'affichage de la liste des serveurs."""
        # Vider la liste
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Mettre à jour la liste locale des serveurs
        self.servers = {s.server_id: s for s in updated_servers}

        # Ajouter les serveurs
        for server_id, server in self.servers.items():
            sw_encoders = ", ".join(server.capabilities.software_encoders) if server.capabilities.software_encoders else "N/A"
            hw_encoders = ", ".join([f"{k}: {', '.join(v)}" for k, v in server.capabilities.hardware_encoders.items() if v]) if server.capabilities.hardware_encoders else "N/A"
            
            self.tree.insert("", "end", iid=server.server_id, values=(
                server.name,
                f"{server.ip}:{server.port}",
                server.status.value,
                f"{server.current_jobs}/{server.max_jobs}",
                f"{server.capabilities.cpu_cores}/{server.capabilities.current_load:.1%}",
                f"{server.capabilities.memory_gb:.1f}",
                sw_encoders,
                hw_encoders
            ))
    
    def remove_server_action(self):
        """Action pour supprimer le serveur sélectionné."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Attention", "Sélectionnez un serveur à supprimer.")
            return
        
        server_id = selection[0]
        self.run_async_func(self._remove_server_and_update_ui(server_id))

    async def _remove_server_and_update_ui(self, server_id: str):
        """Supprime un serveur et met à jour l'UI."""
        if messagebox.askyesno("Confirmer la suppression", "Êtes-vous sûr de vouloir supprimer ce serveur ?"): 
            await self.server_discovery.remove_server(server_id)
            messagebox.showinfo("Succès", "Serveur supprimé.")
            self.refresh_display(list(self.server_discovery.get_all_servers().values()))

    async def ping_selected_servers(self):
        """Ping les serveurs sélectionnés."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Attention", "Sélectionnez au moins un serveur à pinger.")
            return
        
        for server_id in selection:
            server_info = self.servers.get(server_id)
            if server_info:
                is_reachable = await self.server_discovery.distributed_client.ping_server(server_id)
                if is_reachable:
                    messagebox.showinfo("Ping", f"Serveur {server_info.name} ({server_info.ip}:{server_info.port}) est joignable.")
                else:
                    messagebox.showerror("Ping", f"Serveur {server_info.name} ({server_info.ip}:{server_info.port}) n'est PAS joignable.")
            else:
                messagebox.showwarning("Ping", f"Serveur avec ID {server_id} non trouvé.")
        self.refresh_display(list(self.server_discovery.get_all_servers().values()))
