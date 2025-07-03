import socket
import threading
import json
import tempfile
import subprocess
import logging
import os
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class EncodingServer:
    def __init__(self, host='0.0.0.0', port=0, server_name="Default FFMpeg Server"):
        self.host = host
        self.port = port
        self.server_name = server_name
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.active_tasks = 0
        self.max_threads = os.cpu_count()
        self.lock = threading.Lock()
        self.allocated_tasks = {} # task_id -> task_data
        self.client_sockets = {} # addr -> socket for async results
        self._buffer = ""

    def start(self):
        self.socket.bind((self.host, self.port))
        self.socket.listen(5)
        self.port = self.socket.getsockname()[1]
        # Use a more reliable way to get the primary IP address
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            host_ip = s.getsockname()[0]
            s.close()
        except Exception:
            host_ip = socket.gethostbyname(socket.gethostname())
        
        print(f"SERVER_READY|{host_ip}|{self.port}")
        logging.info(f"Server '{self.server_name}' started on {host_ip}:{self.port} with {self.max_threads} threads.")
        
        while True:
            client, addr = self.socket.accept()
            logging.info(f"Accepted connection from {addr}")
            self.client_sockets[addr] = client
            threading.Thread(target=self.handle_client, args=(client, addr), daemon=True).start()

    def _send_json(self, client_socket, data):
        """Sends a JSON object with a newline delimiter."""
        try:
            message = json.dumps(data) + '\n'
            client_socket.sendall(message.encode('utf-8'))
        except Exception as e:
            logging.error(f"Error sending data: {e}")

    def _receive_json(self, client_socket):
        """Receives data and decodes one newline-delimited JSON object."""
        buffer = ""
        while '\n' not in buffer:
            try:
                chunk = client_socket.recv(4096)
                if not chunk:
                    return None # Connection closed
                buffer += chunk.decode('utf-8')
            except Exception:
                return None
        
        message, buffer = buffer.split('\n', 1)
        # self._buffer should be an instance variable if we want to handle partial messages across calls,
        # but since handle_client runs in its own thread, a local buffer is safer.
        return json.loads(message)

    def handle_client(self, client, addr):
        try:
            # Send server capabilities on connect
            self._send_json(client, {
                "type": "HELLO",
                "threads": self.max_threads,
                "server_name": self.server_name
            })
            
            while True:
                request = self._receive_json(client)
                if request is None:
                    logging.info(f"Client {addr} disconnected.")
                    break
                
                req_type = request.get("type")
                if req_type == "ALLOCATE_TASK":
                    logging.info(f"Received allocation request for task {request.get('task_id')} from {addr}")
                    self.handle_allocation(client, request)
                elif req_type == "START_ENCODING":
                    logging.info(f"Received START command from {addr}")
                    self.start_all_tasks(client)
                else:
                    logging.warning(f"Received unknown message type from {addr}: {req_type}")
        except Exception as e:
            logging.error(f"An error occurred with client {addr}: {e}")
        finally:
            client.close()
            del self.client_sockets[addr]

    def handle_allocation(self, client, task_data):
        task_id = task_data.get('task_id')
        if not task_id:
            self._send_json(client, {"type": "ACK_ALLOCATE", "task_id": None, "status": "ERROR", "message": "Missing task_id"})
            return

        with self.lock:
            # Check for overall capacity, not just running tasks
            if (len(self.allocated_tasks) + self.active_tasks) >= self.max_threads:
                logging.warning(f"Allocation rejected for task {task_id}. Server at capacity.")
                self._send_json(client, {"type": "ACK_ALLOCATE", "task_id": task_id, "status": "ERROR", "message": "Server at capacity"})
                return

            self.allocated_tasks[task_id] = task_data
            logging.info(f"Task {task_id} allocated. Total allocated: {len(self.allocated_tasks)}")
            self._send_json(client, {"type": "ACK_ALLOCATE", "task_id": task_id, "status": "SUCCESS"})

    def start_all_tasks(self, client):
        """Starts processing all allocated tasks."""
        with self.lock:
            tasks_to_start = list(self.allocated_tasks.keys())
            if not tasks_to_start:
                logging.info("START command received, but no tasks to start.")
                return

        logging.info(f"Starting {len(tasks_to_start)} tasks.")
        for task_id in tasks_to_start:
            with self.lock:
                task_data = self.allocated_tasks.pop(task_id, None)
            if task_data:
                # Pass the original client socket to send the final result back
                threading.Thread(target=self.process_task, args=(client, task_data), daemon=True).start()
    
    def process_task(self, client, task):
        task_id = task.get("task_id", str(uuid.uuid4()))
        with self.lock:
            if self.active_tasks >= self.max_threads:
                logging.warning(f"Server busy. Rejecting task {task_id}.")
                self._send_json(client, {"type": "ERROR", "task_id": task_id, "message": "Server busy"})
                return
            self.active_tasks += 1
        
        logging.info(f"Starting task {task_id}. Active tasks: {self.active_tasks}")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                input_path = os.path.join(tmpdir, task["filename"])
                with open(input_path, "wb") as f:
                    f.write(bytes.fromhex(task["file_data"]))
                
                output_path = os.path.join(tmpdir, task["output_filename"])
                cmd = self.build_ffmpeg_cmd(input_path, output_path, task)
                
                logging.info(f"Executing FFmpeg for task {task_id}: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)

                if result.returncode != 0:
                    error_message = f"FFmpeg error (code {result.returncode}): {result.stderr}"
                    logging.error(f"Task {task_id} failed. {error_message}")
                    self._send_json(client, {"type": "ERROR", "task_id": task_id, "message": error_message})
                    return

                with open(output_path, "rb") as f:
                    encoded_data = f.read()

                logging.info(f"Task {task_id} completed successfully.")
                self._send_json(client, {
                    "type": "RESULT",
                    "task_id": task_id,
                    "file_data": encoded_data.hex(),
                    "filename": task["output_filename"]
                })
        except Exception as e:
            error_msg = f"An unexpected error occurred: {str(e)}"
            logging.error(f"Task {task_id} failed. {error_msg}")
            self._send_json(client, {"type": "ERROR", "task_id": task_id, "message": error_msg})
        finally:
            with self.lock:
                self.active_tasks -= 1
            logging.info(f"Finished task {task_id}. Active tasks: {self.active_tasks}")
    
    def build_ffmpeg_cmd(self, input_path, output_path, task):
        codec = task["codec"].lower()
        
        # Base command with '-y' to overwrite, and '-i' for input
        base_cmd = ["ffmpeg", "-y", "-i", input_path]
        
        # Map all streams from the input. This is key for preserving album art.
        base_cmd += ["-map", "0"]
        
        # Define the audio codec based on user selection
        if codec == "aac":
            bitrate = task.get("bitrate", 256)
            base_cmd += ["-c:a", "aac", "-b:a", f"{bitrate}k"]
        elif codec == "alac":
            base_cmd += ["-c:a", "alac"]
        elif codec == "flac":
            quality = task.get("quality", 5) # FLAC compression level 0-12
            base_cmd += ["-c:a", "flac", "-compression_level", str(quality)]
        elif codec == "opus":
            bitrate = task.get("bitrate", 128)
            base_cmd += ["-c:a", "libopus", "-b:a", f"{bitrate}k"]
        elif codec == "mp3":
            quality = task.get("quality", 2) # MP3 VBR quality 0-9 (best-worst)
            base_cmd += ["-c:a", "libmp3lame", "-q:a", str(quality)]
        elif codec == "wav":
            base_cmd += ["-c:a", "pcm_s16le"] # Standard CD quality WAV
        else:
            raise ValueError(f"Unsupported codec: {codec}")

        # For all other stream types (video for album art, subtitles), copy them without re-encoding.
        # This is more robust than just copying the video stream.
        base_cmd += ["-c:v", "copy", "-c:s", "copy"]
        
        # Add the output path at the very end.
        base_cmd.append(output_path)
        return base_cmd

if __name__ == "__main__":
    server = EncodingServer()
    server.start() 