import socket
import json
import logging
import threading

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
        self.listener_thread = None
        self.lock = threading.Lock()

    def connect(self):
        """Establishes a connection, performs handshake, and starts listener thread."""
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
                # Start listener thread
                self.listener_thread = threading.Thread(target=self._listen_for_results, daemon=True)
                self.listener_thread.start()
                return self.server_info
            else:
                logging.error(f"Handshake failed with {self.host}:{self.port}. Unexpected response: {hello_data}")
                self.disconnect()
                return None
        except Exception as e:
            logging.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            self.disconnect()
            return None

    def allocate_task(self, task_payload):
        """Sends a task for allocation and waits for acknowledgment."""
        if not self.is_connected:
            return {"status": "ERROR", "message": "Not connected"}

        try:
            with self.lock:
                self._send_json(task_payload)
                response = self._receive_json() # Should be ACK_ALLOCATE
            return response
        except Exception as e:
            logging.error(f"Error during task allocation with {self.host}:{self.port}: {e}")
            self.disconnect() # Connection is likely broken
            return {"status": "ERROR", "message": str(e)}

    def start_encoding(self):
        """Sends the fire-and-forget command to start all allocated tasks."""
        if not self.is_connected:
            return False
        
        try:
            with self.lock:
                self._send_json({"type": "START_ENCODING"})
            return True
        except Exception as e:
            logging.error(f"Error sending START command to {self.host}:{self.port}: {e}")
            self.disconnect()
            return False

    def _listen_for_results(self):
        """Dedicated thread for receiving asynchronous results from the server."""
        while self.is_connected:
            try:
                result = self._receive_json()
                if result:
                    # Pass the result back to the controller for handling
                    self.controller.handle_server_response(result, f"{self.host}:{self.port}")
                else:
                    # If receive returns None, connection is closed
                    logging.info(f"Listener thread for {self.host}:{self.port} detected disconnect.")
                    self.disconnect()
                    break
            except json.JSONDecodeError:
                logging.warning(f"Received malformed JSON from {self.host}:{self.port}. Ignoring.")
            except Exception as e:
                if self.is_connected: # Avoid logging errors during a normal disconnect
                    logging.error(f"Listener thread for {self.host}:{self.port} encountered an error: {e}")
                self.disconnect() # Ensure cleanup
                break

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
        if self.is_connected:
            self.is_connected = False # Signal listener to stop
            if self.socket:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass # Ignore if socket is already closed
                self.socket.close()
                self.socket = None
            logging.info(f"Disconnected from {self.host}:{self.port}")
            # Notify controller of the disconnection
            self.controller.handle_server_disconnect(f"{self.host}:{self.port}")

    def _send_json(self, data):
        """Appends a newline and sends the JSON-encoded data."""
        message = json.dumps(data) + '\n'
        self.socket.sendall(message.encode('utf-8'))

    def _receive_json(self):
        """Reads from the socket until a newline is found, then decodes the JSON."""
        while '\n' not in self._buffer:
            try:
                chunk = self.socket.recv(4096)
                if not chunk:
                    self.is_connected = False
                    return None
                self._buffer += chunk.decode('utf-8')
            except (ConnectionResetError, BrokenPipeError, OSError):
                 self.is_connected = False
                 return None
        
        message, self._buffer = self._buffer.split('\n', 1)
        return json.loads(message) 