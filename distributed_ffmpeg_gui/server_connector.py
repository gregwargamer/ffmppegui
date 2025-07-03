import socket
import json
import logging
import threading
import selectors
from collections import deque

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
        self.send_queue = deque()
        self.selector = selectors.DefaultSelector()
        self.io_thread = None
        self.send_queue = deque()
        self._buffer = ""
        self.is_connected = False

    def connect(self):
        """Establishes a connection, performs handshake, and starts listener thread."""
        try:
            self.socket = socket.create_connection((self.host, self.port), self.timeout)
            self.is_connected = True
            
            # Perform handshake with blocking I/O
            hello_data = self._receive_json_blocking()
            if hello_data and hello_data.get("type") == "HELLO":
                self.server_info = {
                    "ip": self.host,
                    "port": self.port,
                    "threads": hello_data.get("threads"),
                    "name": hello_data.get("server_name", "N/A"),
                    "status": "Connected"
                }
                logging.info(f"Handshake complete with {self.host}:{self.port}")
                # Start I/O thread after successful handshake
                self.io_thread = threading.Thread(target=self._handle_io_events, daemon=True)
                self.io_thread.start()
                return self.server_info
            else:
                logging.error(f"Handshake failed with {self.host}:{self.port}. Unexpected response: {hello_data}")
                self.disconnect()
                return None
        except Exception as e:
            logging.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            self.disconnect()
            return None

    def reserve_task(self, task_payload):
        """Sends a task for reservation and waits for acknowledgment."""
        if not self.is_connected:
            return {"status": "ERROR", "message": "Not connected"}

        try:
            self._send_json_blocking(task_payload)
            return self._receive_json_blocking()  # Should be ACK_RESERVE
        except Exception as e:
            logging.error(f"Error during task reservation with {self.host}:{self.port}: {e}")
            self.disconnect() # Connection is likely broken
            return {"status": "ERROR", "message": str(e)}

    def encode_task(self, task_payload):
        """Sends the full task with file data to be encoded immediately."""
        if not self.is_connected:
            return False
        
        try:
            self.send_queue.append(('command', task_payload))
            return True
        except Exception as e:
            logging.error(f"Error sending ENCODE_TASK command to {self.host}:{self.port}: {e}")
            self.disconnect()
            return False

    def _handle_io_events(self):
        """Handles I/O using selector for non-blocking multiplexing."""
        try:
            self.socket.setblocking(False)
            self.selector.register(self.socket, selectors.EVENT_READ | selectors.EVENT_WRITE)
            while self.is_connected:
                events = self.selector.select(timeout=1)
                for key, mask in events:
                    if mask & selectors.EVENT_READ:
                        data = self.socket.recv(4096)
                        if data:
                            self._buffer += data.decode('utf-8')
                            while '\n' in self._buffer:
                                message, self._buffer = self._buffer.split('\n', 1)
                                try:
                                    result = json.loads(message)
                                    self.controller.handle_server_response(result, f"{self.host}:{self.port}")
                                except json.JSONDecodeError:
                                    logging.warning(f"Received malformed JSON from {self.host}:{self.port}")
                        else:
                            self.disconnect()
                            break
                    if mask & selectors.EVENT_WRITE and self.send_queue:
                        msg_type, payload = self.send_queue.popleft()
                        message = json.dumps(payload) + '\n'
                        try:
                            sent = self.socket.send(message.encode('utf-8'))
                            if sent == 0:
                                self.disconnect()
                                break
                        except BlockingIOError:
                            self.send_queue.appendleft((msg_type, payload))
        except Exception as e:
            logging.error(f"I/O handler error: {e}")
            self.disconnect()

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

    def _send_json_blocking(self, data):
        """Sends JSON data immediately (blocking)."""
        message = json.dumps(data) + '\n'
        self.socket.sendall(message.encode('utf-8'))

    def _receive_json_blocking(self):
        """Blocking receive for handshake and critical operations."""
        while '\n' not in self._buffer:
            chunk = self.socket.recv(4096)
            if not chunk:
                self.is_connected = False
                return None
            self._buffer += chunk.decode('utf-8')
        
        message, self._buffer = self._buffer.split('\n', 1)
        return json.loads(message)
        
    def _send_json(self, data):
        """Queues JSON data for async sending."""
        self.send_queue.append(('response', data))

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
