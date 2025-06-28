# Copyright (c) 2025 Greg Oire
# MIT License – see LICENSE file

import asyncio
import logging
import os
import sys
import tkinter as tk
from tkinter import messagebox

# Ajouter le répertoire racine du projet au sys.path
# Cela garantit que les importations de modules comme 'core' et 'shared' fonctionnent de manière fiable.
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.distributed_client import DistributedClient
from core.local_server import LocalServer
from core.server_discovery import ServerDiscovery
from core.job_scheduler import JobScheduler
from core.capability_matcher import CapabilityMatcher
from core.settings import load_settings
from gui.main_window import MainWindow
from shared.settings_manager import SettingsManager


def setup_logging(settings):
    """Configure le système de logging de l'application."""
    log_level = settings.get("log_level", "INFO").upper()
    log_file = settings.get("log_file", "ffmpeg_easy_gui.log")
    
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    numeric_level = getattr(logging, log_level, logging.INFO)
    
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    formatter = logging.Formatter(log_format)

    try:
        file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception as e:
        print(f"Impossible de configurer le logging de fichier: {e}")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    root_logger.setLevel(numeric_level)


class AsyncTkApp:
    """Classe de base pour gérer l'intégration d'une boucle asyncio avec Tkinter."""
    def __init__(self, root_widget):
        self.root = root_widget
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.is_running = True
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def run_forever(self):
        """Démarre la boucle principale qui gère à la fois Tkinter et asyncio."""
        self._update_loop()
        self.root.mainloop()

    def _update_loop(self):
        """Exécute une passe de la boucle asyncio et des mises à jour de Tkinter."""
        if not self.is_running:
            return
            
        self.loop.stop()
        self.loop.run_forever()
        self.root.update()
        self.root.after(50, self._update_loop)

    async def _shutdown_async_tasks(self):
        """Annule et attend la terminaison des tâches asyncio en cours."""
        tasks = [t for t in asyncio.all_tasks(loop=self.loop) if t is not asyncio.current_task(loop=self.loop)]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def on_close(self):
        """Gère la fermeture propre de l'application."""
        if not self.is_running:
            return
        self.is_running = False
        
        try:
            self.loop.run_until_complete(self._shutdown_async_tasks())
        finally:
            self.loop.close()
            self.root.destroy()


def main():
    """Point d'entrée principal de l'application."""
    # Tentative d'initialisation de TkinterDnD avec fallback
    dnd_available = False
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        dnd_available = True
        print("TkinterDnD2 initialisé avec succès - Drag & Drop disponible")
    except Exception as e:
        print(f"Impossible d'initialiser TkinterDnD2: {e}")
        print("Utilisation de Tkinter standard - Drag & Drop désactivé")
        import tkinter as tk
        root = tk.Tk()
        dnd_available = False

    app = AsyncTkApp(root)

    try:
        # Charger les settings pour le logging (format dictionnaire)
        settings_manager = SettingsManager('settings.json')
        logging_settings = settings_manager.get_settings()
        setup_logging(logging_settings)

        # Charger les settings pour les composants (format dataclass)
        settings = load_settings()

        # Initialisation de la nouvelle architecture State/Controller
        from core.app_state import AppState
        from core.app_controller import AppController
        
        app_state = AppState(settings)
        
        # Initialisation des composants principaux
        distributed_client = DistributedClient(settings)
        server_discovery = ServerDiscovery(distributed_client, settings)
        capability_matcher = CapabilityMatcher()
        job_scheduler = JobScheduler(distributed_client, capability_matcher)
        
        # Création du contrôleur principal
        app_controller = AppController(app_state, job_scheduler, distributed_client, server_discovery)
        
        # Fonction pour exécuter des tâches async depuis l'interface
        def run_async_func(coro, loop=None):
            # Le paramètre loop est ignoré car on utilise toujours app.loop
            return app.loop.create_task(coro)

        # Création de la fenêtre principale avec la nouvelle architecture
        main_window = MainWindow(root, app_state, app_controller, app.loop, run_async_func, dnd_available=dnd_available)
        
        # Lancer les tâches de fond
        app.loop.create_task(server_discovery.start_discovery())
        app.loop.create_task(job_scheduler.start_scheduler())
        
        # Démarrer l'application
        app.run_forever()

    except Exception as e:
        logging.exception("Une erreur fatale et non gérée est survenue")
        messagebox.showerror(
            "Erreur Fatale", 
            f"Une erreur critique est survenue:\n\n{e}\n\nL'application va se fermer. "
            "Consultez le fichier de log pour plus de détails."
        )
        if app.is_running:
            app.on_close()

if __name__ == "__main__":
    main()