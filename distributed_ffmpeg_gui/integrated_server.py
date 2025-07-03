import sys
import os

# Add the server directory to the Python path to allow importing EncodingServer
server_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'distributed_ffmpeg_server'))
if server_path not in sys.path:
    sys.path.insert(0, server_path)

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