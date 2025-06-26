import tkinter as tk
from tkinter import ttk

from core.encode_job import EncodeJob


class AdvancedFiltersWindow(tk.Toplevel):
    def __init__(self, parent, job: EncodeJob):
        super().__init__(parent)
        self.title("Advanced Filters")
        self.geometry("400x300")

        self.job = job

        self.create_widgets()

    def create_widgets(self):
        # Brightness
        ttk.Label(self, text="Brightness:").grid(row=0, column=0, padx=5, pady=5)
        self.brightness_var = tk.DoubleVar(value=self.job.filters.get("brightness", 0.0))
        self.brightness_scale = ttk.Scale(self, from_=-1.0, to=1.0, variable=self.brightness_var, orient=tk.HORIZONTAL)
        self.brightness_scale.grid(row=0, column=1, padx=5, pady=5)

        # Contrast
        ttk.Label(self, text="Contrast:").grid(row=1, column=0, padx=5, pady=5)
        self.contrast_var = tk.DoubleVar(value=self.job.filters.get("contrast", 1.0))
        self.contrast_scale = ttk.Scale(self, from_=0.0, to=2.0, variable=self.contrast_var, orient=tk.HORIZONTAL)
        self.contrast_scale.grid(row=1, column=1, padx=5, pady=5)

        # Saturation
        ttk.Label(self, text="Saturation:").grid(row=2, column=0, padx=5, pady=5)
        self.saturation_var = tk.DoubleVar(value=self.job.filters.get("saturation", 1.0))
        self.saturation_scale = ttk.Scale(self, from_=0.0, to=3.0, variable=self.saturation_var, orient=tk.HORIZONTAL)
        self.saturation_scale.grid(row=2, column=1, padx=5, pady=5)

        # Apply Button
        self.apply_button = ttk.Button(self, text="Apply", command=self.apply_filters)
        self.apply_button.grid(row=3, column=0, columnspan=2, pady=10)

    def apply_filters(self):
        self.job.filters["brightness"] = self.brightness_var.get()
        self.job.filters["contrast"] = self.contrast_var.get()
        self.job.filters["saturation"] = self.saturation_var.get()
        self.destroy()