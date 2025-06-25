# Copyright (c) 2025 Greg Oire
# MIT License – see LICENSE file

from tkinter import Tk

from core.settings import Settings
from gui.main_window import MainWindow

try:
    from tkinterdnd2 import TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False

def main():
    Settings.load()
    
    # Utiliser TkinterDnD si disponible pour le drag & drop
    if DND_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = Tk()
    
    root.geometry("1200x700")
    root.minsize(800, 500)
    
    app = MainWindow(root)
    
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        # Arrêter le pool de workers proprement
        app.pool.stop()


if __name__ == "__main__":
    main()