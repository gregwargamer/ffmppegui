import tkinter as tk
from tkinter import ttk, filedialog

class MergeVideosWindow(tk.Toplevel):
    def __init__(self, parent, main_window):
        super().__init__(parent)
        self.title("Merge Videos")
        self.geometry("500x400")

        self.main_window = main_window
        self.files_to_merge = []

        self.create_widgets()

    def create_widgets(self):
        self.listbox = tk.Listbox(self, selectmode=tk.MULTIPLE)
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        button_frame = ttk.Frame(self)
        button_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(button_frame, text="Add Files", command=self.add_files).pack(side=tk.LEFT)
        ttk.Button(button_frame, text="Merge", command=self.merge_files).pack(side=tk.RIGHT)

    def add_files(self):
        files = filedialog.askopenfilenames(title="Select files to merge")
        for file in files:
            self.files_to_merge.append(file)
            self.listbox.insert(tk.END, file)

    def merge_files(self):
        if len(self.files_to_merge) < 2:
            return

        output_file = filedialog.asksaveasfilename(title="Save merged file as")
        if not output_file:
            return

        # Create a temporary file with the list of files to merge
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            for file in self.files_to_merge:
                f.write(f"file '{file}'\n")
            temp_file_path = f.name

        # Build the ffmpeg command to merge the files
        command = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", temp_file_path,
            "-c", "copy",
            output_file
        ]

        # Run the command
        import subprocess
        try:
            subprocess.run(command, check=True)
            self.destroy()
        except Exception as e:
            print(f"Error merging files: {e}")
