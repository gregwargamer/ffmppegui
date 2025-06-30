import threading
from pathlib import Path
import time

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class FolderWatcher(threading.Thread):
    def __init__(self, path, callback, stop_event):
        super().__init__(daemon=True)
        self.path = path
        self.callback = callback
        self.stop_event = stop_event
        self.observer = Observer()
        self.event_handler = WatcherEventHandler(callback)
    
    def run(self):
        self.observer.schedule(self.event_handler, str(self.path), recursive=True)
        self.observer.start()
        self.stop_event.wait()
        self.observer.stop()
        self.observer.join()

class WatcherEventHandler(FileSystemEventHandler):
    #délai minimum entre deux événements sur le même fichier (en secondes)
    _DEBOUNCE_INTERVAL = 1.0

    def __init__(self, callback):
        self.callback = callback
        #dictionnaire chemin→horodatage du dernier événement traité
        self._last_event_time = {}
    
    def on_created(self, event):
        if event.is_directory:
            return

        path = Path(event.src_path)
        now = time.time()

        last_time = self._last_event_time.get(path)
        if last_time is not None and (now - last_time) < self._DEBOUNCE_INTERVAL:
            #ignore les événements rapprochés
            return

        #mise à jour de l'horodatage et exécution du callback
        self._last_event_time[path] = now
        self.callback(path)
