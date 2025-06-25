import copy
import os
import subprocess
import tempfile
from tkinter import Toplevel, ttk, BooleanVar, StringVar, IntVar, DoubleVar, messagebox
import tkinter as tk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


class AdvancedFiltersWindow:
    """Fenêtre pour configurer les filtres avancés d'un job"""
    
    def __init__(self, parent, job):
        self.parent = parent
        self.job = job
        self.window = Toplevel(parent)
        self.window.title(f"Filtres avancés - {job.src_path.name}")
        self.window.geometry("600x400")
        self.window.transient(parent)
        self.window.grab_set()
        
        # Variables pour les filtres
        self.filter_vars = {}
        self._create_filter_vars()
        
        # Variable pour la gestion du throttle
        self._preview_update_job = None
        
        self._build_interface()
        self._load_current_filters()
    
    def _create_filter_vars(self):
        """Crée les variables pour tous les filtres"""
        # Filtres de couleur et luminosité
        self.brightness_var = IntVar(value=0)
        self.contrast_var = IntVar(value=0)
        self.saturation_var = IntVar(value=0)
        self.gamma_var = DoubleVar(value=1.0)
        self.hue_var = IntVar(value=0)
        
        # Filtres de géométrie
        self.crop_x_var = IntVar(value=0)
        self.crop_y_var = IntVar(value=0)
        self.crop_w_var = IntVar(value=0)
        self.crop_h_var = IntVar(value=0)
        self.rotate_var = IntVar(value=0)
        self.flip_h_var = BooleanVar(value=False)
        self.flip_v_var = BooleanVar(value=False)
        
        # Filtres d'amélioration
        self.sharpness_var = IntVar(value=0)
        self.noise_reduction_var = IntVar(value=0)
    
    def _build_interface(self):
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Notebook pour les onglets
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True, pady=5)

        # Onglets
        color_frame = ttk.Frame(notebook)
        geometry_frame = ttk.Frame(notebook)
        enhancement_frame = ttk.Frame(notebook)
        notebook.add(color_frame, text="Couleur")
        notebook.add(geometry_frame, text="Géométrie")
        notebook.add(enhancement_frame, text="Amélioration")

        self._build_color_tab(color_frame)
        self._build_geometry_tab(geometry_frame)
        self._build_enhancement_tab(enhancement_frame)

        # Boutons
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=10)
        ttk.Button(button_frame, text="Appliquer", command=self._apply_filters).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Annuler", command=self._cancel).pack(side=tk.RIGHT, padx=5)
        ttk.Button(button_frame, text="Réinitialiser tout", command=self._reset_all_filters).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Afficher l'aperçu", command=self._show_preview_frame).pack(side=tk.LEFT, padx=5)
    
    def _build_color_tab(self, parent):
        """Construit l'onglet des filtres de couleur et luminosité"""
        # Brightness
        self._create_slider_control(parent, "Brightness", self.brightness_var, -100, 100, 0)
        
        # Contrast
        self._create_slider_control(parent, "Contrast", self.contrast_var, -100, 100, 1)
        
        # Saturation
        self._create_slider_control(parent, "Saturation", self.saturation_var, -100, 100, 2)
        
        # Gamma
        self._create_slider_control(parent, "Gamma", self.gamma_var, 0.1, 3.0, 3, is_float=True, resolution=0.1)
        
        # Hue
        self._create_slider_control(parent, "Hue", self.hue_var, -180, 180, 4)
    
    def _build_geometry_tab(self, parent):
        """Construit l'onglet des filtres de géométrie"""
        # Crop controls
        crop_frame = ttk.LabelFrame(parent, text="Crop", padding=10)
        crop_frame.pack(fill="x", pady=(0, 15))
        
        ttk.Label(crop_frame, text="X:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        ttk.Entry(crop_frame, textvariable=self.crop_x_var, width=8).grid(row=0, column=1, padx=(0, 10))
        
        ttk.Label(crop_frame, text="Y:").grid(row=0, column=2, sticky="w", padx=(0, 5))
        ttk.Entry(crop_frame, textvariable=self.crop_y_var, width=8).grid(row=0, column=3, padx=(0, 10))
        
        ttk.Label(crop_frame, text="Width:").grid(row=1, column=0, sticky="w", padx=(0, 5), pady=(5, 0))
        ttk.Entry(crop_frame, textvariable=self.crop_w_var, width=8).grid(row=1, column=1, padx=(0, 10), pady=(5, 0))
        
        ttk.Label(crop_frame, text="Height:").grid(row=1, column=2, sticky="w", padx=(0, 5), pady=(5, 0))
        ttk.Entry(crop_frame, textvariable=self.crop_h_var, width=8).grid(row=1, column=3, padx=(0, 10), pady=(5, 0))
        
        # Rotation
        rotate_frame = ttk.LabelFrame(parent, text="Rotation", padding=10)
        rotate_frame.pack(fill="x", pady=(0, 15))
        
        ttk.Radiobutton(rotate_frame, text="0°", variable=self.rotate_var, value=0).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(rotate_frame, text="90°", variable=self.rotate_var, value=90).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(rotate_frame, text="180°", variable=self.rotate_var, value=180).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(rotate_frame, text="270°", variable=self.rotate_var, value=270).pack(side="left")
        
        # Flip
        flip_frame = ttk.LabelFrame(parent, text="Flip", padding=10)
        flip_frame.pack(fill="x")
        
        ttk.Checkbutton(flip_frame, text="Flip Horizontal", variable=self.flip_h_var).pack(side="left", padx=(0, 15))
        ttk.Checkbutton(flip_frame, text="Flip Vertical", variable=self.flip_v_var).pack(side="left")
    
    def _build_enhancement_tab(self, parent):
        """Construit l'onglet des filtres d'amélioration"""
        # Sharpness
        self._create_slider_control(parent, "Sharpness", self.sharpness_var, -10, 10, 0)
        
        # Noise Reduction
        self._create_slider_control(parent, "Noise Reduction", self.noise_reduction_var, 0, 100, 1)
    
    def _create_slider_control(self, parent, label, variable, min_val, max_val, row, is_float=False, resolution=1):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=2, pady=5, padx=5, sticky="ew")
        ttk.Label(frame, text=label).pack(side=tk.LEFT, padx=5)
        
        display_var = StringVar()
        ttk.Label(frame, textvariable=display_var, width=5).pack(side=tk.RIGHT, padx=5)
        
        def update_label(*args):
            value = variable.get()
            display_var.set(f"{value}")
        
        variable.trace_add("write", update_label)
        update_label()
        
        scale = ttk.Scale(frame, from_=min_val, to=max_val, variable=variable, orient=tk.HORIZONTAL, length=300)
        if is_float:
            scale.configure(resolution=resolution)
        scale.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        scale.config(command=lambda *args: self._schedule_preview_update())
        return frame
    
    def _load_current_filters(self):
        """Charge les filtres actuels du job dans l'interface"""
        filters = self.job.filters
        
        # Couleur et luminosité
        self.brightness_var.set(filters.get("brightness", 0))
        self.contrast_var.set(filters.get("contrast", 0))
        self.saturation_var.set(filters.get("saturation", 0))
        self.gamma_var.set(filters.get("gamma", 1.0))
        self.hue_var.set(filters.get("hue", 0))
        
        # Géométrie
        self.crop_x_var.set(filters.get("crop_x", 0))
        self.crop_y_var.set(filters.get("crop_y", 0))
        self.crop_w_var.set(filters.get("crop_w", 0))
        self.crop_h_var.set(filters.get("crop_h", 0))
        self.rotate_var.set(filters.get("rotate", 0))
        self.flip_h_var.set(filters.get("flip_h", False))
        self.flip_v_var.set(filters.get("flip_v", False))
        
        # Amélioration
        self.sharpness_var.set(filters.get("sharpness", 0))
        self.noise_reduction_var.set(filters.get("noise_reduction", 0))
    
    def _reset_all_filters(self):
        """Remet tous les filtres à leurs valeurs par défaut"""
        self.brightness_var.set(0)
        self.contrast_var.set(0)
        self.saturation_var.set(0)
        self.gamma_var.set(1.0)
        self.hue_var.set(0)
        
        self.crop_x_var.set(0)
        self.crop_y_var.set(0)
        self.crop_w_var.set(0)
        self.crop_h_var.set(0)
        self.rotate_var.set(0)
        self.flip_h_var.set(False)
        self.flip_v_var.set(False)
        
        self.sharpness_var.set(0)
        self.noise_reduction_var.set(0)
    
    def _show_preview_frame(self):
        # Vérifier si PIL est disponible
        if Image is None or ImageTk is None:
            messagebox.showerror("Dépendance manquante", "Le module 'Pillow' n'est pas installé. Veuillez l'installer avec 'pip install Pillow'.")
            return
            
        # Calculer le timestamp au milieu de la vidéo pour un cadre représentatif
        try:
            probe_cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(self.job.src_path)
            ]
            duration = float(subprocess.check_output(probe_cmd).decode().strip())
            timestamp = duration / 2
        except:
            timestamp = 10  # Valeur par défaut si la durée ne peut pas être déterminée
        
        # Créer un job temporaire avec les filtres actuels
        temp_job = copy.deepcopy(self.job)
        self._apply_vars_to_job(temp_job)
        
        # Construire la commande FFmpeg pour extraire une image avec les filtres appliqués
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            output_path = temp_file.name
        
        filter_string = self._build_filter_string(temp_job.filters)
        
        command = [
            "ffmpeg",
            "-ss", str(timestamp),
            "-i", str(self.job.src_path),
            "-vf", filter_string,
            "-vframes", "1",
            "-y",  # Écraser le fichier temporaire si nécessaire
            output_path
        ]
        
        # Exécuter la commande
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # Afficher l'image dans une nouvelle fenêtre
            self._display_preview_image(output_path)
            os.unlink(output_path)  # Supprimer le fichier temporaire
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Erreur", f"Erreur lors de la génération de l'aperçu: {e.stderr.decode()}")

    def _build_filter_string(self, filters: dict) -> str:
        # Construire la chaîne de filtres à partir du dictionnaire des filtres
        filter_parts = []
        
        # Couleur
        brightness = filters.get('brightness', 0)
        if brightness != 0:
            filter_parts.append(f"eq=brightness={brightness/100}")
        contrast = filters.get('contrast', 0)
        if contrast != 0:
            filter_parts.append(f"eq=contrast={1 + contrast/100}")
        saturation = filters.get('saturation', 0)
        if saturation != 0:
            filter_parts.append(f"eq=saturation={1 + saturation/100}")
        gamma = filters.get('gamma', 0)
        if gamma != 0:
            filter_parts.append(f"eq=gamma={1 + gamma/100}")
        
        # Géométrie
        rotate = filters.get('rotate', 0)
        if rotate != 0:
            filter_parts.append(f"rotate={rotate}*PI/180")
        flip_h = filters.get('flip_h', 0)
        if flip_h:
            filter_parts.append("hflip")
        flip_v = filters.get('flip_v', 0)
        if flip_v:
            filter_parts.append("vflip")
        crop = filters.get('crop_w', 0)
        if crop > 0:
            filter_parts.append(f"crop=in_w*{crop/100}:in_h*{crop/100}:in_w*(1-{crop/100})/2:in_h*(1-{crop/100})/2")
        
        # Amélioration
        sharpness = filters.get('sharpness', 0)
        if sharpness != 0:
            filter_parts.append(f"unsharp=5:5:{sharpness/10}")
        denoise = filters.get('denoise', 0)
        if denoise != 0:
            filter_parts.append(f"nlmeans={denoise/10}")
        deblock = filters.get('deblock', 0)
        if deblock != 0:
            filter_parts.append(f"deblock={deblock/10}")
        
        return ','.join(filter_parts) if filter_parts else 'null'

    def _display_preview_image(self, image_path):
        # Créer la fenêtre d'aperçu si elle n'existe pas ou a été fermée
        if not hasattr(self, 'preview_window') or not self.preview_window.winfo_exists():
            self.preview_window = tk.Toplevel(self.window)
            self.preview_window.title("Aperçu des filtres")
            self.preview_label = ttk.Label(self.preview_window)
            self.preview_label.pack(padx=10, pady=10)
        
        # Charger et afficher l'image
        image = Image.open(image_path)
        image.thumbnail((640, 480))  # Redimensionner si trop grand
        self.photo_image = ImageTk.PhotoImage(image)
        self.preview_label.configure(image=self.photo_image)

    def _schedule_preview_update(self):
        # Mettre à jour l'aperçu avec un délai pour éviter les exécutions trop fréquentes
        if hasattr(self, '_preview_update_job') and self._preview_update_job is not None:
            self.window.after_cancel(self._preview_update_job)
        self._preview_update_job = self.window.after(500, self._show_preview_frame)
    
    def _apply_vars_to_job(self, job):
        """Applique les variables actuelles aux filtres du job"""
        job.filters.update({
            "brightness": self.brightness_var.get(),
            "contrast": self.contrast_var.get(),
            "saturation": self.saturation_var.get(),
            "gamma": self.gamma_var.get(),
            "hue": self.hue_var.get(),
            "crop_x": self.crop_x_var.get(),
            "crop_y": self.crop_y_var.get(),
            "crop_w": self.crop_w_var.get(),
            "crop_h": self.crop_h_var.get(),
            "rotate": self.rotate_var.get(),
            "flip_h": self.flip_h_var.get(),
            "flip_v": self.flip_v_var.get(),
            "sharpness": self.sharpness_var.get(),
            "noise_reduction": self.noise_reduction_var.get()
        })
    
    def _apply_filters(self):
        """Applique les filtres au job et ferme la fenêtre"""
        self._apply_vars_to_job(self.job)
        messagebox.showinfo("Applied", "Filters have been applied to the selected job.")
        self.window.destroy()
    
    def _cancel(self):
        """Ferme la fenêtre sans appliquer les changements"""
        self.window.destroy()
