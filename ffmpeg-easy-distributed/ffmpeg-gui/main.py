# Copyright (c) 2025 Greg Oire
# MIT License – see LICENSE file

from tkinter import Tk
import asyncio
import logging

from core.settings import load_settings
from gui.main_window import MainWindow
from core.distributed_client import DistributedClient
from core.server_discovery import ServerDiscovery
from core.job_scheduler import JobScheduler
from core.capability_matcher import CapabilityMatcher

try:
    from tkinterdnd2 import TkinterDnD
    DND_AVAILABLE = True
except (ImportError, Exception):
    # Gérer les erreurs tkinterdnd2 sur macOS beta
    DND_AVAILABLE = False

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    settings = load_settings()
    
    # Initialisation des composants distribués
    distributed_client = DistributedClient(settings)
    server_discovery = ServerDiscovery(distributed_client, settings)
    capability_matcher = CapabilityMatcher()
    job_scheduler = JobScheduler(distributed_client, capability_matcher)

    # Utiliser TkinterDnD si disponible pour le drag & drop
    if DND_AVAILABLE:
        try:
            root = TkinterDnD.Tk()
        except Exception as e:
            logging.warning(f"Erreur avec TkinterDnD: {e}. Utilisation de Tk standard.")
            root = Tk()
    else:
        root = Tk()
    
    root.geometry("1200x700")
    root.minsize(800, 500)
    
    app = MainWindow(root, distributed_client, server_discovery, job_scheduler)
    
    # Démarrer les tâches asynchrones
    async def start_tasks():
        await server_discovery.start_discovery()
        await job_scheduler.start_scheduler()

    # Exécuter les tâches asynchrones en arrière-plan
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.create_task(start_tasks())

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        # Arrêter les composants proprement
        loop.run_until_complete(job_scheduler.stop_scheduler())
        loop.run_until_complete(server_discovery.stop_discovery())
        loop.run_until_complete(distributed_client.shutdown())
        # app.pool.stop() # Le pool de workers local n'est plus utilisé de la même manière


if __name__ == "__main__":
    main()