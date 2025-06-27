import asyncio
import tkinter as tk
from tkinter import ttk, messagebox
from typing import List

from shared.messages import ServerInfo, JobConfiguration, EncoderType

class JobEditWindow:
    def __init__(self, parent, job: JobConfiguration, distributed_client):
        self.parent = parent
        self.job = job
        self.distributed_client = distributed_client

        self.window = tk.Toplevel(parent)
        self.window.title(f"Edit Job: {job.job_id}")
        self.window.geometry("600x700")

        self.server_mode_var = tk.StringVar(value="auto")
        self.server_var = tk.StringVar()
        self.global_encoder_var = tk.StringVar(value=job.encoder)
        self.compatibility_var = tk.StringVar()

        self.build_ui()
        self._update_server_list()
        self._on_server_mode_change()

    def build_ui(self):
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        self._build_server_selection_frame(main_frame)
        # ... other job editing UI elements would go here ...

        save_button = ttk.Button(main_frame, text="Save Changes", command=self.save_changes)
        save_button.pack(pady=10)

    def _build_server_selection_frame(self, parent):
        server_frame = ttk.LabelFrame(parent, text="Serveur d'Encodage")
        server_frame.pack(fill=tk.X, padx=10, pady=5)
        
        auto_radio = ttk.Radiobutton(server_frame, text="Sélection automatique", 
                                    variable=self.server_mode_var, value="auto",
                                    command=self._on_server_mode_change)
        auto_radio.grid(row=0, column=0, sticky="w", padx=5, pady=2)
        
        manual_radio = ttk.Radiobutton(server_frame, text="Serveur spécifique:", 
                                      variable=self.server_mode_var, value="manual",
                                      command=self._on_server_mode_change)
        manual_radio.grid(row=1, column=0, sticky="w", padx=5, pady=2)
        
        self.server_combo = ttk.Combobox(server_frame, textvariable=self.server_var, state="readonly")
        self.server_combo.grid(row=1, column=1, sticky="ew", padx=5, pady=2)
        
        self.compatibility_label = ttk.Label(server_frame, 
                                            textvariable=self.compatibility_var,
                                            foreground="blue")
        self.compatibility_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=5)
        
        test_btn = ttk.Button(server_frame, text="Tester Compatibilité",
                             command=self._test_compatibility)
        test_btn.grid(row=3, column=0, sticky="w", padx=5, pady=5)

    def _on_server_mode_change(self):
        if self.server_mode_var.get() == "auto":
            self.server_combo.config(state="disabled")
        else:
            self.server_combo.config(state="readonly")
            self._update_server_list()

    def _update_server_list(self):
        servers = self.distributed_client.get_connected_servers()
        server_names = [f"{s.name} ({s.ip}:{s.port})" for s in servers]
        self.server_combo['values'] = server_names
        if server_names:
            self.server_var.set(server_names[0])

    def _test_compatibility(self):
        if self.server_mode_var.get() == "auto":
            self.compatibility_var.set("✅ Sélection automatique activée")
            return
        
        server_name = self.server_var.get()
        if not server_name:
            self.compatibility_var.set("⚠️ Aucun serveur sélectionné")
            return
        
        encoder = self.global_encoder_var.get()
        asyncio.create_task(self._async_test_compatibility(server_name, encoder))

    async def _async_test_compatibility(self, server_name: str, encoder: str):
        try:
            server_info = self._get_server_by_name(server_name)
            if not server_info:
                self.compatibility_var.set("❌ Serveur non trouvé")
                return
            
            all_encoders = (server_info.capabilities.software_encoders +
                            [enc for encs in server_info.capabilities.hardware_encoders.values() for enc in encs])
            
            if encoder in all_encoders:
                hw_type = next((vendor.upper() for vendor, hw_encoders in server_info.capabilities.hardware_encoders.items() if encoder in hw_encoders), None)
                if hw_type:
                    self.compatibility_var.set(f"✅ Compatible ({hw_type})")
                else:
                    self.compatibility_var.set("✅ Compatible (logiciel)")
            else:
                alternatives = self._find_encoder_alternatives(encoder, all_encoders)
                if alternatives:
                    alt_text = ", ".join(alternatives[:2])
                    self.compatibility_var.set(f"❌ Non compatible. Alternatives: {alt_text}")
                else:
                    self.compatibility_var.set("❌ Aucun encodeur compatible")
                    
        except Exception as e:
            self.compatibility_var.set(f"⚠️ Erreur test: {e}")

    def _get_server_by_name(self, server_name_str: str) -> ServerInfo | None:
        for server in self.distributed_client.get_connected_servers():
            if f"{server.name} ({server.ip}:{server.port})" == server_name_str:
                return server
        return None

    def _find_encoder_alternatives(self, target_encoder: str, available: List[str]) -> List[str]:
        alternatives_map = {
            'h264_nvenc': ['libx264', 'h264_qsv', 'h264_videotoolbox'],
            'hevc_nvenc': ['libx265', 'hevc_qsv', 'hevc_videotoolbox'], 
            'h264_videotoolbox': ['libx264', 'h264_nvenc'],
            'libx264': ['h264_nvenc', 'h264_qsv'],
            'libx265': ['hevc_nvenc', 'hevc_qsv']
        }
        possible_alts = alternatives_map.get(target_encoder, [])
        return [alt for alt in possible_alts if alt in available]

    def save_changes(self):
        # ... logic to save changes to the job object ...
        self.window.destroy()
