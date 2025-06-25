import threading
from pathlib import Path

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
    def __init__(self, callback):
        self.callback = callback
    
    def on_created(self, event):
        if not event.is_directory:
            self.callback(Path(event.src_path))
