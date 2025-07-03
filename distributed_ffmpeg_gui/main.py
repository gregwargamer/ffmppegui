import tkinter as tk
from tkinter import ttk, filedialog, simpledialog
import controller
import threading
import os
import multiprocessing
import sys

class MainApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Distributed FFmpeg Encoder")
        self.setup_ui()
        self.controller = controller.DistributedController(self)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.mainloop()
    
    def on_closing(self):
        """Handle window closing event."""
        self.controller.stop()
        self.root.destroy()

    def setup_ui(self):
        # --- Main Frame ---
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # --- Input/Output Section ---
        io_frame = ttk.LabelFrame(main_frame, text="File Locations", padding="10")
        io_frame.grid(row=0, column=0, columnspan=3, sticky="ew")

        ttk.Label(io_frame, text="Input Folder:").grid(row=0, column=0, sticky="w")
        self.input_entry = ttk.Entry(io_frame, width=50)
        self.input_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(io_frame, text="Browse...", command=self.browse_input).grid(row=0, column=2)
        
        ttk.Label(io_frame, text="Output Folder:").grid(row=1, column=0, sticky="w")
        self.output_entry = ttk.Entry(io_frame, width=50)
        self.output_entry.grid(row=1, column=1, sticky="ew")
        ttk.Button(io_frame, text="Browse...", command=self.browse_output).grid(row=1, column=2)
        
        # --- Encoding Settings ---
        settings_frame = ttk.LabelFrame(main_frame, text="Encoding Settings", padding="10")
        settings_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=5)

        ttk.Label(settings_frame, text="Codec:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.codec_var = tk.StringVar(value="MP3")
        self.codec_menu = ttk.Combobox(settings_frame, textvariable=self.codec_var, 
                                 values=["AAC", "ALAC", "FLAC", "Opus", "MP3", "WAV"])
        self.codec_menu.grid(row=0, column=1, sticky="ew")
        self.codec_menu.bind("<<ComboboxSelected>>", self.update_quality_options)

        self.quality_label = ttk.Label(settings_frame, text="Quality (0-9):")
        self.quality_label.grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.quality_var = tk.IntVar(value=2)
        self.quality_scale = ttk.Scale(settings_frame, from_=0, to=9, orient=tk.HORIZONTAL, variable=self.quality_var)
        self.quality_scale.grid(row=1, column=1, sticky="ew")

        self.bitrate_label = ttk.Label(settings_frame, text="Bitrate (kbps):", state=tk.DISABLED)
        self.bitrate_label.grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.bitrate_var = tk.IntVar(value=256)
        self.bitrate_entry = ttk.Entry(settings_frame, textvariable=self.bitrate_var, state=tk.DISABLED)
        self.bitrate_entry.grid(row=2, column=1, sticky="ew")

        self.keep_structure = tk.BooleanVar(value=True)
        ttk.Checkbutton(settings_frame, text="Keep Folder Structure", variable=self.keep_structure).grid(row=3, column=0, columnspan=2, sticky="w", pady=5)
        self.update_quality_options()

        # --- Server Connections Frame ---
        server_frame = ttk.LabelFrame(main_frame, text="Encoding Servers", padding="10")
        server_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=5)
        self.server_tree = ttk.Treeview(server_frame, columns=("name", "ip", "port", "threads", "status"), show="headings")
        self.server_tree.heading("name", text="Name")
        self.server_tree.heading("ip", text="IP Address")
        self.server_tree.heading("port", text="Port")
        self.server_tree.heading("threads", text="Threads")
        self.server_tree.heading("status", text="Status")
        self.server_tree.grid(row=0, column=0, columnspan=2, sticky="nsew")

        server_button_frame = ttk.Frame(server_frame)
        server_button_frame.grid(row=0, column=2, sticky="ns", padx=5)
        ttk.Button(server_button_frame, text="Add", command=self.add_server).pack(fill='x')
        ttk.Button(server_button_frame, text="Remove", command=self.remove_server).pack(fill='x')

        # --- Task Queue Frame ---
        queue_frame = ttk.LabelFrame(main_frame, text="Task Queue", padding="10")
        queue_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=5)
        self.task_tree = ttk.Treeview(queue_frame, columns=("file", "status", "server"), show="headings")
        self.task_tree.heading("file", text="File Name")
        self.task_tree.heading("status", text="Status")
        self.task_tree.heading("server", text="Assigned Server")
        self.task_tree.grid(row=0, column=0, sticky="nsew")
        
        # --- Right-click Menu ---
        self.task_menu = tk.Menu(self.root, tearoff=0)
        self.task_tree.bind("<Button-3>", self.show_task_menu)

        # --- Log Section ---
        log_frame = ttk.LabelFrame(main_frame, text="Log", padding="10")
        log_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=5)
        self.log_text = tk.Text(log_frame, height=8)
        self.log_text.grid(row=0, column=0, sticky="ew")

        # --- Action Buttons ---
        action_frame = ttk.Frame(main_frame, padding="10")
        action_frame.grid(row=6, column=0, columnspan=3, sticky="e")
        ttk.Button(action_frame, text="Start Scan", command=self.start_scan).grid(row=0, column=0, padx=5)
        self.add_to_queue_button = ttk.Button(action_frame, text="Add All to Queue", command=self.add_to_queue, state=tk.DISABLED)
        self.add_to_queue_button.grid(row=0, column=1, padx=5)
        self.allocate_button = ttk.Button(action_frame, text="Allocate Tasks", command=self.allocate_tasks, state=tk.DISABLED)
        self.allocate_button.grid(row=0, column=2, padx=5)
        self.start_button = ttk.Button(action_frame, text="Start Encoding", command=self.start_all, state=tk.DISABLED)
        self.start_button.grid(row=0, column=3, padx=5)

    def browse_input(self):
        path = filedialog.askdirectory()
        if path:
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, path)

    def browse_output(self):
        path = filedialog.askdirectory()
        if path:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, path)
    
    def log_message(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def start_scan(self):
        """Kicks off the input folder scan."""
        self.log_message("Scanning input folder...")
        # Run the scan in a thread to keep the GUI responsive
        threading.Thread(target=self.controller.scan_input_folder, daemon=True).start()

    def update_quality_options(self, event=None):
        codec = self.codec_var.get().lower()
        if codec == 'mp3':
            self.quality_label.config(text="Quality (0-9):", state=tk.NORMAL)
            self.quality_scale.config(from_=0, to=9, state=tk.NORMAL)
            self.quality_var.set(2)
            self.bitrate_label.config(state=tk.DISABLED)
            self.bitrate_entry.config(state=tk.DISABLED)
        elif codec == 'flac':
            self.quality_label.config(text="Compression (0-12):", state=tk.NORMAL)
            self.quality_scale.config(from_=0, to=12, state=tk.NORMAL)
            self.quality_var.set(5)
            self.bitrate_label.config(state=tk.DISABLED)
            self.bitrate_entry.config(state=tk.DISABLED)
        elif codec in ['aac', 'opus']:
            self.quality_label.config(state=tk.DISABLED)
            self.quality_scale.config(state=tk.DISABLED)
            self.bitrate_label.config(state=tk.NORMAL)
            self.bitrate_entry.config(state=tk.NORMAL)
            self.bitrate_var.set(256 if codec == 'aac' else 128)
        else:
            self.quality_label.config(state=tk.DISABLED)
            self.quality_scale.config(state=tk.DISABLED)
            self.bitrate_label.config(state=tk.DISABLED)
            self.bitrate_entry.config(state=tk.DISABLED)

    def add_server(self):
        server_address = simpledialog.askstring("Add Server", "Enter server address (e.g., 192.168.1.10:12345):", parent=self.root)
        if server_address and ':' in server_address:
            ip, port_str = server_address.rsplit(':', 1)
            try:
                port = int(port_str)
                threading.Thread(target=self.controller.connect_to_server, args=(ip, port), daemon=True).start()
            except ValueError:
                self.log_message(f"Invalid port: {port_str}")
        elif server_address:
            self.log_message(f"Invalid format. Use IP:PORT.")

    def remove_server(self):
        selected_item = self.server_tree.focus()
        if not selected_item:
            self.log_message("Please select a server to remove.")
            return
        
        server_details = self.server_tree.item(selected_item)
        ip = server_details['values'][1]
        port = server_details['values'][2]
        
        self.controller.disconnect_from_server(f"{ip}:{port}")

    def update_server_list(self, server_list):
        self.root.after(0, self._update_server_list_threadsafe, server_list)

    def _update_server_list_threadsafe(self, server_list):
        for i in self.server_tree.get_children():
            self.server_tree.delete(i)
        for server in server_list:
            self.server_tree.insert("", "end", values=(
                server.get('name', 'N/A'),
                server.get('ip'),
                server.get('port'),
                server.get('threads'),
                server.get('status')
            ))

    def show_task_menu(self, event):
        """Show a context menu for a selected task."""
        selected_item = self.task_tree.focus()
        if not selected_item:
            return
        
        # Clear previous menu entries
        self.task_menu.delete(0, tk.END)
        self.task_menu.add_command(label="Reassign To:", state=tk.DISABLED)
        
        servers = self.controller.get_server_status()
        for server in servers:
            server_key = f"{server['ip']}:{server['port']}"
            label = f"{server.get('name', 'Unknown')} ({server_key})"
            # The command will be implemented fully later
            self.task_menu.add_command(label=label, command=lambda s=server_key: self.reassign_task(selected_item, s))
            
        self.task_menu.post(event.x_root, event.y_root)

    def reassign_task(self, item_id, server_key):
        """Placeholder for telling the controller to reassign a task."""
        self.log_message(f"UI: Request to reassign task {item_id} to {server_key} (feature pending).")
        # In the final step, this will call:
        # self.controller.reassign_task(item_id, server_key)

    def update_task_list(self, task_status):
        """Thread-safe update of the task list Treeview."""
        self.root.after(0, self._update_task_list_threadsafe, task_status)

    def _update_task_list_threadsafe(self, task_status):
        self.task_tree.delete(*self.task_tree.get_children())
        # The task_status will be a combined list of scanned, queued and active tasks
        for task in task_status.get('scanned', []):
            self.task_tree.insert("", "end", iid=task.get('id'), values=(os.path.basename(task['input_path']), "Scanned", "N/A"))
        for task in task_status.get('queued', []):
            self.task_tree.insert("", "end", iid=task.get('id'), values=(os.path.basename(task['input_path']), "Queued", "N/A"))
        for task_id, task in task_status.get('active', {}).items():
            assigned_server = task.get('server_key', 'N/A')
            status = "Encoding" if task.get('status') == 'Encoding' else 'Allocated'
            self.task_tree.insert("", "end", iid=task_id, values=(os.path.basename(task['input_path']), status, assigned_server))

        # --- Button State Logic ---
        scanned_empty = not task_status.get('scanned')
        queue_empty = not task_status.get('queued')
        active_empty = not task_status.get('active')

        self.add_to_queue_button.config(state=tk.NORMAL if not scanned_empty else tk.DISABLED)
        self.allocate_button.config(state=tk.NORMAL if not queue_empty else tk.DISABLED)
        # Enable start if there are active (allocated) tasks and they are not all already encoding
        can_start = not active_empty and any(t.get('status') != 'Encoding' for t in task_status['active'].values())
        self.start_button.config(state=tk.NORMAL if can_start else tk.DISABLED)

    def add_to_queue(self):
        """Adds all scanned files to the processing queue."""
        self.controller.add_scanned_to_queue()

    def allocate_tasks(self):
        """Assigns queued tasks to available servers."""
        self.controller.allocate_tasks()

    def start_all(self):
        """Starts the encoding process on all servers."""
        self.controller.start_processing()

if __name__ == "__main__":
    # On macOS and Windows, 'spawn' is the default and safest start method.
    # Explicitly setting it at the entry point of the application can resolve
    # obscure initialization errors when scripts are launched from a GUI
    # or via a wrapper script.
    if sys.platform in ["darwin", "win32"]:
        multiprocessing.set_start_method('spawn', force=True)
        
    app = MainApp() 