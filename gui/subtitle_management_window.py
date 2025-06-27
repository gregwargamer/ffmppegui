import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import json
from pathlib import Path
import threading

from core.encode_job import EncodeJob # Assuming EncodeJob is accessible

class SubtitleManagementWindow(tk.Toplevel):
    def __init__(self, master, parent_job: EncodeJob):
        super().__init__(master)
        self.parent_job = parent_job
        self.title(f"Manage Subtitles for {parent_job.src_path.name}")
        self.geometry("600x400")
        self.transient(master)
        self.grab_set()

        self.subtitle_tracks = [] # To store {index, title, lang, codec}

        self._build_ui()
        self._probe_subtitles()

    def _build_ui(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Treeview for subtitle tracks
        cols = ("index", "lang", "title", "codec")
        self.tree = ttk.Treeview(main_frame, columns=cols, show="headings", selectmode="browse")
        for col_name in cols:
            self.tree.heading(col_name, text=col_name.capitalize())
            self.tree.column(col_name, width=100, anchor=tk.W)

        self.tree.column("index", width=50, anchor=tk.CENTER)
        self.tree.column("title", width=200)

        tree_scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scrollbar.set)

        self.tree.grid(row=0, column=0, sticky="nsew", columnspan=2)
        tree_scrollbar.grid(row=0, column=2, sticky="ns")
        main_frame.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)

        # Extraction options
        extract_options_frame = ttk.Frame(main_frame, padding=(0, 10))
        extract_options_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10,0))

        ttk.Label(extract_options_frame, text="Extract format:").pack(side=tk.LEFT, padx=(0,5))
        self.extract_format_var = tk.StringVar(value="srt")
        extract_format_combo = ttk.Combobox(extract_options_frame, textvariable=self.extract_format_var,
                                            values=["srt", "ass", "vtt", "sub"], state="readonly", width=7)
        extract_format_combo.pack(side=tk.LEFT, padx=(0,10))

        extract_button = ttk.Button(extract_options_frame, text="Extract Selected Subtitle", command=self._on_extract_selected)
        extract_button.pack(side=tk.LEFT)

        self.status_label = ttk.Label(main_frame, text="")
        self.status_label.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(5,0))


    def _probe_subtitles(self):
        self.status_label.config(text="Probing subtitle tracks...")
        self.update_idletasks() # Ensure label updates

        def do_probe():
            try:
                cmd = [
                    "ffprobe",
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_streams",
                    "-select_streams", "s", # Select only subtitle streams
                    str(self.parent_job.src_path)
                ]
                process = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
                probe_data = json.loads(process.stdout)

                tracks = []
                if "streams" in probe_data:
                    for stream in probe_data["streams"]:
                        if stream.get("codec_type") == "subtitle":
                            index = stream.get("index") # This is the absolute index in the file
                            # We might want the relative subtitle stream index (0, 1, 2 for subtitles)
                            # FFmpeg -map 0:s:0 refers to the first subtitle track.
                            # For simplicity, we'll use the ffprobe index and assume user maps correctly or we adjust mapping.
                            # Let's store the stream 'index' from ffprobe.

                            lang = stream.get("tags", {}).get("language", "unk")
                            title = stream.get("tags", {}).get("title", "N/A")
                            codec = stream.get("codec_name", "N/A")
                            tracks.append({"id": index, "index_display": stream.get("index"), "lang": lang, "title": title, "codec": codec})

                self.after(0, self._populate_subtitle_tree, tracks)
                self.after(0, lambda: self.status_label.config(text=f"Found {len(tracks)} subtitle track(s)."))

            except FileNotFoundError:
                self.after(0, lambda: messagebox.showerror("Error", "FFprobe not found. Please ensure FFmpeg (and ffprobe) is installed and in your system's PATH."))
                self.after(0, lambda: self.status_label.config(text="Error: FFprobe not found."))
            except subprocess.CalledProcessError as e:
                self.after(0, lambda: messagebox.showerror("Error", f"FFprobe error: {e.stderr}"))
                self.after(0, lambda: self.status_label.config(text="Error probing subtitles."))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", f"An unexpected error occurred during probing: {e}"))
                self.after(0, lambda: self.status_label.config(text="Error probing subtitles."))

        threading.Thread(target=do_probe, daemon=True).start()

    def _populate_subtitle_tree(self, tracks):
        for item in self.tree.get_children():
            self.tree.delete(item)

        self.subtitle_tracks = tracks # Store for later use

        for i, track in enumerate(tracks):
            # For display, let's show a relative index 0, 1, 2... for subtitles
            # The actual mapping will use track['id'] which is ffprobe's stream index.
            self.tree.insert("", tk.END, iid=str(track['id']),
                             values=(i, track['lang'], track['title'], track['codec']))

    def _on_extract_selected(self):
        selected_item_id = self.tree.selection()
        if not selected_item_id:
            messagebox.showwarning("No Selection", "Please select a subtitle track to extract.", parent=self)
            return

        selected_ffprobe_idx = selected_item_id[0] # This is the ffprobe stream index (stored as iid)

        # Find the relative subtitle index for ffmpeg -map 0:s:N
        # This requires knowing how many audio/video streams are before this subtitle stream if ffprobe index is absolute.
        # Or, if ffprobe -select_streams s gives indices relative to subtitles, then it's simpler.
        # FFmpeg's -map 0:s:N maps the Nth subtitle stream.
        # We need to find which N this selected ffprobe index corresponds to.

        # Let's find the original track details using its ffprobe stream index
        selected_track_details = next((t for t in self.subtitle_tracks if str(t['id']) == selected_ffprobe_idx), None)
        if not selected_track_details:
            messagebox.showerror("Error", "Could not find details for selected track.", parent=self)
            return

        # Determine the N for -map 0:s:N. This N is the count of subtitle streams up to and including the selected one.
        # We need to sort self.subtitle_tracks by their original ffprobe index first to be sure.
        sorted_subtitle_tracks = sorted(self.subtitle_tracks, key=lambda t: t['id'])
        map_idx = -1
        for i, track_in_file in enumerate(sorted_subtitle_tracks):
            if track_in_file['id'] == selected_track_details['id']:
                map_idx = i
                break

        if map_idx == -1:
            messagebox.showerror("Error", "Could not determine mapping index for selected track.", parent=self)
            return

        extract_format = self.extract_format_var.get()

        default_filename = f"{self.parent_job.src_path.stem}_sub_track_{map_idx}.{extract_format}"

        # Determine output directory (use parent job's output dir if set, else source dir)
        # This assumes parent_job.outputs[0].dst_path is set.
        # A more robust way might be to use MainWindow's output_folder setting.
        output_dir = Path(Settings.data.get("output_folder", self.parent_job.src_path.parent))
        if hasattr(self.master, "output_folder") and self.master.output_folder.get() and not self.master.output_folder.get().startswith("No output"):
            output_dir = Path(self.master.output_folder.get())
        elif self.parent_job.outputs and self.parent_job.outputs[0].dst_path:
             output_dir = self.parent_job.outputs[0].dst_path.parent
        else:
            output_dir = self.parent_job.src_path.parent

        output_dir.mkdir(parents=True, exist_ok=True)

        save_path = filedialog.asksaveasfilename(
            parent=self,
            title="Save Extracted Subtitle As",
            initialdir=str(output_dir),
            initialfile=default_filename,
            defaultextension=f".{extract_format}",
            filetypes=[(f"{extract_format.upper()} files", f"*.{extract_format}"), ("All files", "*.*")]
        )

        if not save_path:
            return

        self.status_label.config(text=f"Extracting track {map_idx} to {Path(save_path).name}...")
        self.update_idletasks()

        def do_extract():
            try:
                cmd_extract = [
                    "ffmpeg", "-y",
                    "-i", str(self.parent_job.src_path),
                    "-map", f"0:s:{map_idx}", # Map the Nth subtitle stream
                    "-c:s", extract_format if extract_format != "sub" else "subrip", # use 'subrip' codec for .sub (DVD subtitles)
                    str(save_path)
                ]
                # For some formats like 'sub' (DVD Subtitles), they are bitmap and might not directly convert to srt/ass/vtt
                # FFmpeg handles this if the output extension implies text format.
                # If extracting to original format, -c:s copy might be better if possible.
                # For simplicity, we re-encode to the desired text format.

                process = subprocess.run(cmd_extract, capture_output=True, text=True, check=True, encoding='utf-8')
                self.after(0, lambda: self.status_label.config(text=f"Successfully extracted to {Path(save_path).name}"))
                self.after(0, lambda: messagebox.showinfo("Success", f"Subtitle track extracted to:\n{save_path}", parent=self))

            except FileNotFoundError:
                self.after(0, lambda: messagebox.showerror("Error", "FFmpeg not found.", parent=self))
                self.after(0, lambda: self.status_label.config(text="Error: FFmpeg not found."))
            except subprocess.CalledProcessError as e:
                error_msg = f"FFmpeg extraction error: {e.stderr}"
                print(error_msg) # Log to console for more details
                self.after(0, lambda: messagebox.showerror("Error", error_msg, parent=self))
                self.after(0, lambda: self.status_label.config(text="Extraction failed."))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", f"An unexpected error occurred: {e}", parent=self))
                self.after(0, lambda: self.status_label.config(text="Extraction error."))

        threading.Thread(target=do_extract, daemon=True).start()

if __name__ == '__main__':
    # Example Usage (for testing this window standalone)
    class MockEncodeJob:
        def __init__(self, src_path_str):
            self.src_path = Path(src_path_str)
            self.outputs = [] # Needed for some shared logic if used by other windows

    root = tk.Tk()
    # Create a dummy EncodeJob. Replace 'path/to/your/video.mkv' with an actual file path.
    # Ensure the video has subtitle tracks for testing.
    # test_video_path = "path/to/your/video_with_subs.mkv"
    # if not Path(test_video_path).exists():
    #     print(f"Test video not found: {test_video_path}")
    #     # sys.exit(1) # or handle gracefully

    # For testing without a real file, you might need to mock ffprobe calls or use a known file
    # For now, this example assumes you'll provide a real file path for proper testing.

    # Dummy job for UI layout testing if no file is available:
    dummy_job = MockEncodeJob("dummy_source_file.mkv")

    # To test with a real file:
    # real_file_path = "YOUR_TEST_FILE_WITH_SUBTITLES.mkv" # <--- IMPORTANT: Set this path
    # if Path(real_file_path).exists():
    #    dummy_job = MockEncodeJob(real_file_path)
    # else:
    #    print(f"WARNING: Test file '{real_file_path}' not found. UI will be empty.")


    app = SubtitleManagementWindow(root, parent_job=dummy_job)
    root.mainloop()
