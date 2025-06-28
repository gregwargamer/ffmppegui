# Copyright (c) 2025 Greg Oire
# MIT License – see LICENSE file

from tkinter import Tk
import asyncio
import logging
import threading # Added threading

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

# Helper to run asyncio tasks from Tkinter
def run_async(coro, loop):
    """Helper to run an asyncio coroutine from a synchronous context (like Tkinter callbacks)"""
    if loop and loop.is_running():
        return asyncio.run_coroutine_threadsafe(coro, loop)
    else:
        logging.error("Asyncio loop is not available or not running.")
        return None

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    settings = load_settings()
    
    # Setup asyncio loop to run in a separate thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Initialisation des composants distribués
    distributed_client = DistributedClient(settings) # loop is implicitly used by asyncio
    server_discovery = ServerDiscovery(distributed_client, settings, loop=loop)
    capability_matcher = CapabilityMatcher()
    job_scheduler = JobScheduler(distributed_client, capability_matcher, loop=loop)


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
    
    # Pass the loop and the run_async helper to the main application window
    app = MainWindow(root, distributed_client, server_discovery, job_scheduler, loop, run_async)
    
    # Démarrer les tâches asynchrones initiales
    async def start_initial_tasks():
        await server_discovery.start_discovery()
        await job_scheduler.start_scheduler()

    loop.create_task(start_initial_tasks()) # Schedule initial tasks

    # Function to run the asyncio event loop
    def run_loop():
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    # Start the asyncio event loop in a new thread
    async_thread = threading.Thread(target=run_loop, daemon=True)
    async_thread.start()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        logging.info("Application interrupted by user.")
    finally:
        logging.info("Shutting down application...")
        # Signal asyncio tasks to stop and wait for them
        if loop.is_running():
            # Schedule shutdown tasks in the loop
            loop.call_soon_threadsafe(loop.create_task, job_scheduler.stop_scheduler())
            loop.call_soon_threadsafe(loop.create_task, server_discovery.stop_discovery())
            loop.call_soon_threadsafe(loop.create_task, distributed_client.shutdown())

            # Give some time for tasks to complete
            # Note: Proper shutdown might need more sophisticated signaling
            import time
            time.sleep(1) # Adjust as necessary

            # Stop the loop
            if loop.is_running():
                loop.call_soon_threadsafe(loop.stop)

        async_thread.join(timeout=5) # Wait for the asyncio thread to finish
        logging.info("Application shutdown complete.")


if __name__ == "__main__":
    main()