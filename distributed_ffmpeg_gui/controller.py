import multiprocessing
import os
import threading
import uuid
import json
import time
from server_connector import ServerConnector
from integrated_server import IntegratedServer

# Constants
SUPPORTED_FORMATS = ('.flac', '.wav', '.mp3', '.m4a', '.aac', '.opus', '.aiff')
CONFIG_FILE = 'servers.json'
BUFFER_FACTOR = 3

class DistributedController:
    def __init__(self, gui):
        self.gui = gui
        # self.servers stores the state of each server connection
        # format: { "ip:port": {"connector": ServerConnector, "info": {...}, "active_tasks": 0} }
        self.servers = {}
        self.task_queue = []
        self.active_tasks = {}  # { "task_id": task_info }
        self.lock = threading.Lock()
        self.is_running = True

        self.load_config()
        self.start_integrated_server()
        
        # Start the main distribution loop in a background thread
        self.distributor_thread = threading.Thread(target=self.distribute_tasks_loop, daemon=True)
        self.distributor_thread.start()

    def stop(self):
        """Signals all background threads to stop."""
        self.is_running = False
        # Disconnect all servers
        with self.lock:
            for key, server in self.servers.items():
                server["connector"].disconnect()
        if hasattr(self, 'local_server_process') and self.local_server_process.is_alive():
            self.local_server_process.terminate()

    def start_integrated_server(self):
        # The integrated server prints its ready message to stdout.
        # We need a way to capture that to get the port.
        # For now, we'll have to manually add it.
        self.gui.log_message("Starting integrated server...")
        try:
            self.local_server_process = multiprocessing.Process(target=IntegratedServer, daemon=True)
            self.local_server_process.start()
            self.gui.log_message("Integrated server process started. Please connect to it manually.")
            # A more advanced implementation would use a pipe or queue to get the port back.
            # For now, we will rely on the user to connect via the GUI.
        except Exception as e:
            self.gui.log_message(f"Failed to start integrated server: {e}")
    
    def connect_to_server(self, ip, port):
        """Initiates a connection and stores the server state."""
        key = f"{ip}:{port}"
        self.gui.log_message(f"Attempting to connect to server at {ip}:{port}...")
        connector = ServerConnector(ip, port, self)
        server_info = connector.connect()
        
        if server_info:
            with self.lock:
                self.servers[key] = {
                    "connector": connector,
                    "info": server_info,
                    "active_tasks": 0
                }
            self.gui.update_server_list(self.get_server_status())
            self.gui.log_message(f"Successfully connected to {server_info.get('name')} at {key}.")
            self.save_config()
        else:
            self.gui.log_message(f"Failed to connect to {ip}:{port}.")
            
    def get_server_status(self):
        """Returns a list of dictionaries with current server info."""
        status_list = []
        with self.lock:
            for key, server in self.servers.items():
                if server["connector"].is_connected:
                    status_list.append(server["info"])
        return status_list
        
    def load_config(self):
        self.gui.log_message("Loading server configuration...")
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    servers = json.load(f)
                for server in servers:
                    # Automatically connect to saved servers on startup
                    threading.Thread(target=self.connect_to_server, args=(server['ip'], server['port']), daemon=True).start()
        except Exception as e:
            self.gui.log_message(f"Could not load server config: {e}")

    def save_config(self):
        """Saves the current list of connected servers."""
        to_save = []
        with self.lock:
            for key, server in self.servers.items():
                if server["connector"].is_connected:
                    to_save.append({"ip": key.split(':')[0], "port": key.split(':')[1]})
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(to_save, f, indent=4)
        except Exception as e:
            self.gui.log_message(f"Could not save server config: {e}")

    def scan_input_folder(self):
        input_path = self.gui.input_entry.get()
        if not input_path:
            self.gui.log_message("Error: Input folder not specified.")
            return

        self.gui.log_message(f"Scanning {input_path} for supported files...")
        found_files = 0
        for root, _, files in os.walk(input_path):
            for file in files:
                if file.lower().endswith(SUPPORTED_FORMATS):
                    if self.gui.keep_structure.get():
                        rel_path = os.path.relpath(root, input_path)
                        if rel_path == ".":
                            rel_path = ""
                    else:
                        rel_path = ""
                    
                    self.task_queue.append({
                        "input_path": os.path.join(root, file),
                        "output_rel": rel_path,
                        "status": "Pending"
                    })
                    found_files += 1
        
        self.gui.log_message(f"Scan complete. Found {found_files} files.")
        self.gui.update_queue_view()
    
    def distribute_tasks_loop(self):
        """The main loop that assigns tasks from the queue to servers."""
        while self.is_running:
            try:
                with self.lock:
                    if not self.task_queue or not self.servers:
                        time.sleep(1) # Wait if there's nothing to do
                        continue

                    total_threads = sum(s['info'].get('threads', 0) for s in self.servers.values())
                    if total_threads == 0:
                        time.sleep(1)
                        continue

                    # Check if buffer is full
                    if len(self.active_tasks) >= total_threads * BUFFER_FACTOR:
                        time.sleep(1)
                        continue
                    
                    # We have capacity, let's assign a task
                    task = self.task_queue.pop(0)
                    target_server_key = self.select_optimal_server()

                    if target_server_key:
                        self.gui.log_message(f"Assigning {os.path.basename(task['input_path'])} to {target_server_key}.")
                        # Increment task count before sending
                        self.servers[target_server_key]['active_tasks'] += 1
                        threading.Thread(target=self.send_task, args=(target_server_key, task), daemon=True).start()
                    else:
                        # No server available, put task back at the front
                        self.task_queue.insert(0, task)
                        time.sleep(1) # Wait before retrying

                # Update the GUI outside the lock
                self.gui.update_task_list(self.get_task_status())
                time.sleep(0.1) # Small delay to prevent busy-waiting
            except Exception as e:
                self.gui.log_message(f"Error in distribution loop: {e}")
                time.sleep(5)

    def select_optimal_server(self):
        """Finds the server with the lowest load ratio."""
        with self.lock:
            if not self.servers:
                return None

            best_server_key = None
            min_load = float('inf')

            # Filter for connected servers
            connected_servers = {k: v for k, v in self.servers.items() if v['connector'].is_connected}
            if not connected_servers:
                return None

            for key, server_data in connected_servers.items():
                threads = server_data['info'].get('threads', 1) or 1 # Avoid division by zero
                load_ratio = server_data['active_tasks'] / threads
                
                if load_ratio < min_load:
                    min_load = load_ratio
                    best_server_key = key
            
            return best_server_key

    def send_task(self, server_key, task):
        task_id = str(uuid.uuid4())
        task['id'] = task_id

        with self.lock:
            connector = self.servers[server_key]["connector"]
            if not connector or not connector.is_connected:
                self.gui.log_message(f"Server {server_key} is not available. Re-queuing task.")
                self.task_queue.insert(0, task)
                return
            self.active_tasks[task_id] = task

        try:
            # Prepare the TASK payload
            with open(task['input_path'], 'rb') as f:
                file_data = f.read()

            output_filename = os.path.basename(task['input_path'])
            base, _ = os.path.splitext(output_filename)
            codec = self.gui.codec_var.get().lower()
            extension = codec if codec != 'alac' else 'm4a'
            output_filename = f"{base}.{extension}"

            payload = {
                "type": "TASK",
                "task_id": task_id,
                "filename": os.path.basename(task['input_path']),
                "file_data": file_data.hex(),
                "codec": codec,
                "quality": self.gui.quality_var.get(),
                "bitrate": self.gui.bitrate_var.get(),
                "output_filename": output_filename
            }
            
            result = connector.send_task_and_get_result(payload)
            self.handle_server_response(result, task)

        except Exception as e:
            self.gui.log_message(f"Error processing task {task_id}: {e}")
            self.handle_server_response({"type": "ERROR", "task_id": task_id, "message": str(e)}, task)
        finally:
            # Decrement the active task count for the server
            with self.lock:
                if server_key in self.servers:
                    self.servers[server_key]['active_tasks'] -= 1

    def handle_server_response(self, result, original_task):
        task_id = result.get('task_id')
        with self.lock:
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]

        if result.get('type') == 'RESULT':
            output_dir = self.gui.output_entry.get()
            rel_path = original_task['output_rel']
            filename = result['filename']
            
            # Determine the full output path
            if self.gui.keep_structure.get() and rel_path:
                output_path = os.path.join(output_dir, rel_path, filename)
            else:
                output_path = os.path.join(output_dir, filename)

            # Ensure the directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Write the file
            with open(output_path, 'wb') as f:
                f.write(bytes.fromhex(result['file_data']))
            self.gui.log_message(f"SUCCESS: Task {task_id} complete. Saved to {output_path}")
        
        elif result.get('type') == 'ERROR':
            self.gui.log_message(f"ERROR: Task {task_id} failed: {result.get('message')}")
            # Optionally, re-queue the task
            # self.task_queue.append(original_task)

        self.gui.update_task_list(self.get_task_status()) 