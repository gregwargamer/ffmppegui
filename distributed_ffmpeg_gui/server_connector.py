import socket
import json
import logging

class ServerConnector:
    """Handles all TCP communication for a single server connection."""
    def __init__(self, host, port, controller, timeout=5):
        self.host = host
        self.port = port
        self.controller = controller
        self.timeout = timeout
        self.socket = None
        self.server_info = {}
        self._buffer = ""
        self.is_connected = False

    def connect(self):
        """Establishes a connection and performs the initial HELLO handshake."""
        try:
            self.socket = socket.create_connection((self.host, self.port), self.timeout)
            self.is_connected = True
            
            hello_data = self._receive_json()
            if hello_data and hello_data.get("type") == "HELLO":
                self.server_info = {
                    "ip": self.host,
                    "port": self.port,
                    "threads": hello_data.get("threads"),
                    "name": hello_data.get("server_name", "N/A"),
                    "status": "Connected"
                }
                logging.info(f"Handshake complete with {self.host}:{self.port}")
                return self.server_info
            else:
                logging.error(f"Handshake failed with {self.host}:{self.port}. Unexpected response: {hello_data}")
                self.disconnect()
                return None
        except Exception as e:
            logging.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            self.disconnect()
            return None

    def send_task_and_get_result(self, task_payload):
        """Sends a single task and waits for a single result (blocking)."""
        if not self.is_connected:
            return {"type": "ERROR", "task_id": task_payload.get("task_id"), "message": "Not connected"}

        try:
            self._send_json(task_payload)
            result = self._receive_json()
            return result
        except Exception as e:
            logging.error(f"Error during task communication with {self.host}:{self.port}: {e}")
            self.disconnect()
            return {"type": "ERROR", "task_id": task_payload.get("task_id"), "message": str(e)}

    def disconnect(self):
        """Closes the socket connection."""
        if self.socket:
            self.socket.close()
            self.socket = None
        self.is_connected = False
        logging.info(f"Disconnected from {self.host}:{self.port}")

    def _send_json(self, data):
        """Appends a newline and sends the JSON-encoded data."""
        message = json.dumps(data) + '\n'
        self.socket.sendall(message.encode('utf-8'))

    def _receive_json(self):
        """Reads from the socket until a newline is found, then decodes the JSON."""
        while '\n' not in self._buffer:
            chunk = self.socket.recv(4096)
            if not chunk:
                self.is_connected = False
                return None
            self._buffer += chunk.decode('utf-8')
        
        message, self._buffer = self._buffer.split('\n', 1)
        return json.loads(message) 