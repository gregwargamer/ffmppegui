import multiprocessing
import os
import sys
import threading
import uuid
import json
import time

# --- Path Setup ---
# This ensures that the child process (integrated_server) can find the 'server' module.
_controller_dir = os.path.dirname(__file__)
_server_dir = os.path.abspath(os.path.join(_controller_dir, '..', 'distributed_ffmpeg_server'))
if _server_dir not in sys.path:
    sys.path.insert(0, _server_dir)
# --- End Path Setup ---

from server_connector import ServerConnector
from integrated_server import IntegratedServer

# Constants
SUPPORTED_FORMATS = ('.flac', '.wav', '.mp3', '.m4a', '.aac', '.opus', '.aiff')
CONFIG_FILE = os.path.join(_controller_dir, 'servers.json')
BUFFER_FACTOR = 3

class DistributedController:
    def __init__(self, gui):
        self.gui = gui
        # self.servers stores the state of each server connection
        # format: { "ip:port": {"connector": ServerConnector, "info": {...}, "active_tasks": 0} }
        self.servers = {}
        self.scanned_files = []
        self.task_queue = []
        self.active_tasks = {}  # { "task_id": task_info }
        self.lock = threading.Lock()
        self.is_running = True

        self.load_config()
        self.start_integrated_server()

    def stop(self):
        """Signals all background threads to stop."""
        self.is_running = False
        # Disconnect all servers
        with self.lock:
            # Create a copy of items to avoid issues with modifying dict during iteration
            for key, server in list(self.servers.items()):
                if server and server.get("connector"):
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
                    "allocated_tasks": 0 # Track tasks allocated, not just running
                }
            self.gui.update_server_list(self.get_server_status())
            self.gui.log_message(f"Successfully connected to {server_info.get('name')} at {key}.")
            self.save_config()
        else:
            self.gui.log_message(f"Failed to connect to {ip}:{port}.")
            
    def disconnect_from_server(self, server_key):
        """Disconnects from a specific server."""
        with self.lock:
            server = self.servers.get(server_key)
            if server and server.get("connector"):
                server["connector"].disconnect()
            if server_key in self.servers:
                del self.servers[server_key]
        self.gui.log_message(f"Disconnected from {server_key}.")
        self.gui.update_server_list(self.get_server_status())
        
    def get_server_status(self):
        """Returns a list of dictionaries with current server info."""
        status_list = []
        with self.lock:
            for key, server in self.servers.items():
                if server["connector"].is_connected:
                    # Add allocated task count to the info dict for the GUI
                    server["info"]["allocated_tasks"] = server.get("allocated_tasks", 0)
                    status_list.append(server["info"])
        return status_list
        
    def get_task_status(self):
        """Returns a dictionary with the current state of tasks."""
        with self.lock:
            # Return copies to prevent modification outside the lock
            return {
                "scanned": list(self.scanned_files),
                "queued": list(self.task_queue),
                "active": dict(self.active_tasks)
            }

    def load_config(self):
        self.gui.log_message("Loading server configuration...")
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                # Ensure config is a list
                if isinstance(config, list):
                    servers_to_connect = config
                elif isinstance(config, dict): # Handle old format
                    servers_to_connect = [{"ip": s.split(':')[0], "port": s.split(':')[1]} for s in config.get('servers', [])]
                else:
                    servers_to_connect = []

                for server in servers_to_connect:
                    # Automatically connect to saved servers on startup
                    threading.Thread(target=self.connect_to_server, args=(server['ip'], int(server['port'])), daemon=True).start()
        except Exception as e:
            self.gui.log_message(f"Could not load server config: {e}")

    def save_config(self):
        """Saves the current list of connected servers."""
        to_save = []
        with self.lock:
            for key, server in self.servers.items():
                if server["connector"].is_connected:
                    # Save with port as integer
                    to_save.append({"ip": key.split(':')[0], "port": int(key.split(':')[1])})
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

        with self.lock:
            # Clear previous scans and queues
            self.scanned_files.clear()
            self.task_queue.clear()
            self.active_tasks.clear()

        self.gui.log_message(f"Scanning {input_path} for supported files...")
        found_files = []
        for root, _, files in os.walk(input_path):
            for file in files:
                if file.lower().endswith(SUPPORTED_FORMATS):
                    if self.gui.keep_structure.get():
                        rel_path = os.path.relpath(root, input_path)
                        if rel_path == ".":
                            rel_path = ""
                    else:
                        rel_path = ""
                    
                    found_files.append({
                        "id": str(uuid.uuid4()),
                        "input_path": os.path.join(root, file),
                        "output_rel": rel_path,
                        "status": "Scanned"
                    })
        
        with self.lock:
            self.scanned_files = found_files

        self.gui.log_message(f"Scan complete. Found {len(found_files)} files.")
        self.gui.update_task_list(self.get_task_status())
    
    def add_scanned_to_queue(self):
        """Moves all scanned files into the main task queue."""
        with self.lock:
            if not self.scanned_files:
                return
            for task in self.scanned_files:
                task['status'] = 'Queued'
                self.task_queue.append(task)
            self.scanned_files.clear()
        
        self.gui.log_message(f"Added {len(self.task_queue)} files to the queue.")
        self.gui.update_task_list(self.get_task_status())

    def reserve_tasks(self):
        """Reserves all queued tasks on the optimal servers."""
        self.gui.log_message("Reserving tasks on servers...")
        with self.lock:
            if not self.task_queue:
                self.gui.log_message("No tasks in queue to reserve.")
                return

            tasks_to_remove_from_queue = []
            for task in self.task_queue:
                target_server_key = self.select_optimal_server()
                if not target_server_key:
                    self.gui.log_message("No available servers to reserve tasks. Aborting.")
                    break

                connector = self.servers[target_server_key]["connector"]
                task['server_key'] = target_server_key
                
                try:
                    output_filename = os.path.basename(task['input_path'])
                    base, _ = os.path.splitext(output_filename)
                    codec = self.gui.codec_var.get().lower()
                    extension = codec if codec != 'alac' else 'm4a'
                    output_filename = f"{base}.{extension}"

                    payload = {
                        "type": "RESERVE_TASK",
                        "task_id": task['id'],
                        "codec": codec,
                        "quality": self.gui.quality_var.get(),
                        "bitrate": self.gui.bitrate_var.get(),
                        "output_filename": output_filename
                    }
                except Exception as e:
                    self.gui.log_message(f"Failed to prepare reservation for task {task['id']}: {e}")
                    continue

                response = connector.reserve_task(payload)

                if response and response.get("status") == "SUCCESS":
                    task['status'] = 'Reserved'
                    self.active_tasks[task['id']] = task
                    self.servers[target_server_key]['allocated_tasks'] += 1
                    tasks_to_remove_from_queue.append(task)
                    self.gui.log_message(f"Reserved {os.path.basename(task['input_path'])} on {target_server_key}.")
                else:
                    self.gui.log_message(f"Failed to reserve task on {target_server_key}: {response.get('message', 'No response')}")
            
            self.task_queue = [t for t in self.task_queue if t not in tasks_to_remove_from_queue]

        self.gui.update_task_list(self.get_task_status())
        self.gui.update_server_list(self.get_server_status())

    def start_processing(self):
        """Streams the file data for each reserved task to its assigned server."""
        self.gui.log_message("Starting encoding process...")
        with self.lock:
            # Create a copy of tasks to process to avoid issues with modifying dict during iteration
            tasks_to_process = [task for task in self.active_tasks.values() if task.get('status') == 'Reserved']

        if not tasks_to_process:
            self.gui.log_message("No reserved tasks to start.")
            return

        for task in tasks_to_process:
            # Start each file transfer and encoding in its own thread
            threading.Thread(target=self._stream_and_encode_task, args=(task,), daemon=True).start()

    def _stream_and_encode_task(self, task):
        """Reads a file and sends it to the server for encoding."""
        task_id = task['id']
        server_key = task['server_key']
        
        with self.lock:
            connector = self.servers.get(server_key, {}).get("connector")
            if not connector:
                self.gui.log_message(f"Cannot start task {task_id}: connector for {server_key} not found.")
                return

        try:
            self.gui.log_message(f"Streaming {os.path.basename(task['input_path'])} to {server_key}...")
            with open(task['input_path'], 'rb') as f:
                file_data = f.read()

            payload = {
                "type": "ENCODE_TASK",
                "task_id": task_id,
                "file_data": file_data.hex(),
                "filename": os.path.basename(task['input_path']) # Send filename for temp file on server
            }
            
            # Optimistically update status in the GUI
            with self.lock:
                if task_id in self.active_tasks:
                    self.active_tasks[task_id]['status'] = 'Encoding'
            self.gui.update_task_list(self.get_task_status())

            # Send the file data to the server
            success = connector.encode_task(payload)
            if not success:
                self.gui.log_message(f"Failed to send file data for task {task_id} to {server_key}.")
                # Revert status if sending failed
                with self.lock:
                    if task_id in self.active_tasks:
                        self.active_tasks[task_id]['status'] = 'Reserved'
                self.gui.update_task_list(self.get_task_status())

        except Exception as e:
            self.gui.log_message(f"Error streaming task {task_id}: {e}")
            with self.lock:
                if task_id in self.active_tasks:
                    self.active_tasks[task_id]['status'] = 'Error'
            self.gui.update_task_list(self.get_task_status())
    
    def select_optimal_server(self):
        """Finds the server with the lowest load ratio."""
        with self.lock:
            if not self.servers:
                return None

            best_server_key = None
            min_load = float('inf')

            # Filter for connected servers
            connected_servers = {k: v for k, v in self.servers.items() if v.get('connector') and v['connector'].is_connected}
            if not connected_servers:
                return None

            for key, server_data in connected_servers.items():
                threads = server_data['info'].get('threads', 1) or 1 # Avoid division by zero
                load_ratio = server_data.get('allocated_tasks', 0) / threads
                
                if load_ratio < min_load:
                    min_load = load_ratio
                    best_server_key = key
            
            return best_server_key

    def handle_server_response(self, result, server_key):
        """Handles asynchronous messages (RESULT, ERROR) from a server."""
        task_id = result.get('task_id')
        if not task_id:
            return

        with self.lock:
            original_task = self.active_tasks.get(task_id)
            if not original_task:
                return # Task might have been cleared already
            
            # Decrement the allocated task count for the server
            if server_key in self.servers:
                self.servers[server_key]['allocated_tasks'] = max(0, self.servers[server_key]['allocated_tasks'] - 1)

            # Remove from active tasks
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
            try:
                # The file data is now sent with the ALLOCATE call, so it's not in the result.
                # The result contains the final encoded data.
                with open(output_path, 'wb') as f:
                    f.write(bytes.fromhex(result['file_data']))
                self.gui.log_message(f"SUCCESS: Task {task_id} complete. Saved to {output_path}")
            except Exception as e:
                self.gui.log_message(f"ERROR: Failed to save file for task {task_id}: {e}")

        elif result.get('type') == 'ERROR':
            self.gui.log_message(f"ERROR: Task {task_id} on {server_key} failed: {result.get('message')}")
            # Optionally, re-queue the task
            # self.task_queue.append(original_task)
        
        self.gui.update_task_list(self.get_task_status())
        self.gui.update_server_list(self.get_server_status())

    def handle_server_disconnect(self, server_key):
        """Handles a server disconnection event."""
        self.gui.log_message(f"Connection lost to server {server_key}.")
        tasks_to_requeue = []
        with self.lock:
            if server_key in self.servers:
                # Update status for GUI
                self.servers[server_key]['info']['status'] = 'Disconnected'

            # Find tasks that were running on the disconnected server
            for task_id, task in list(self.active_tasks.items()):
                if task.get('server_key') == server_key:
                    tasks_to_requeue.append(task)
                    del self.active_tasks[task_id]
        
        if tasks_to_requeue:
            self.gui.log_message(f"Re-queueing {len(tasks_to_requeue)} tasks from {server_key}.")
            with self.lock:
                for task in tasks_to_requeue:
                    task['status'] = 'Queued'
                    self.task_queue.insert(0, task) # Add to front of queue

        self.gui.update_task_list(self.get_task_status())
        self.gui.update_server_list(self.get_server_status()) 