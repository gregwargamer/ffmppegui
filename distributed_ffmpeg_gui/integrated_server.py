import sys
import os

# --- Path Setup ---
# This is crucial for the multiprocessing context on macOS/Windows.
# It ensures the new process can find the 'server' module.
_gui_dir = os.path.dirname(__file__)
_server_dir = os.path.abspath(os.path.join(_gui_dir, '..', 'distributed_ffmpeg_server'))
if _server_dir not in sys.path:
    sys.path.insert(0, _server_dir)
# --- End Path Setup ---

from server import EncodingServer  # type: ignore

def IntegratedServer():
    """
    This function is run in a separate process. It instantiates
    and starts the real EncodingServer, configured to listen on a port.
    """
    # Port 0 lets the OS pick an available port
    integrated_server = EncodingServer(port=0, server_name="Integrated Server")
    integrated_server.start()

if __name__ == '__main__':
    IntegratedServer() 