import subprocess
import re
from tkinter import messagebox
from typing import List, Dict, Any, Optional
import os
import json
import logging
import threading

#verrou global pour sécuriser l'accès au cache entre threads
_cache_lock = threading.Lock()

#cache des encodeurs ffmpeg
_ffmpeg_encoders_cache: Optional[List[str]] = None

# Cache pour les informations détaillées des encodeurs disponibles
_available_encoders_info_cache: Optional[List[Dict[str, str]]] = None

# Cache pour la liste des codecs disponibles classée par type
_available_codecs_cache: Optional[Dict[str, List[str]]] = None

def get_ffmpeg_encoders() -> List[str]:
    """Récupère la liste des noms d'encodeurs depuis la sortie de 'ffmpeg -encoders'."""
    global _ffmpeg_encoders_cache
    #accès thread-safe au cache
    with _cache_lock:
        if _ffmpeg_encoders_cache is not None:
            return _ffmpeg_encoders_cache

    # Ajout de logs pour diagnostiquer
    logger = logging.getLogger(__name__)
    logger.info("🔍 DIAGNOSTIC: Interrogation de FFmpeg pour les encodeurs...")

    try:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=True)
        encoders = []
        encoder_line_re = re.compile(r"^\s[VAS][.FSXBD]{5}\s+(\w+)")
        
        # Compter les lignes pour diagnostic
        total_lines = len(result.stdout.splitlines())
        logger.info(f"📄 DIAGNOSTIC: {total_lines} lignes dans la sortie FFmpeg")
        
        for line in result.stdout.splitlines():
            match = encoder_line_re.match(line)
            if match:
                encoder_name = match.group(1)
                encoders.append(encoder_name)
                # Log spécifiquement les encodeurs d'images
                if any(img_enc in encoder_name for img_enc in ['png', 'jpeg', 'mjpeg', 'webp', 'avif']):
                    logger.info(f"🖼️ DIAGNOSTIC: Encodeur image détecté: {encoder_name}")
        
        logger.info(f"✅ DIAGNOSTIC: {len(encoders)} encodeurs FFmpeg détectés au total")
        logger.info(f"🖼️ DIAGNOSTIC: Encodeurs d'images trouvés: {[e for e in encoders if any(img in e for img in ['png', 'jpeg', 'mjpeg', 'webp', 'avif'])]}")
        
        with _cache_lock:
            _ffmpeg_encoders_cache = encoders
        return encoders
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.error(f"❌ DIAGNOSTIC: Erreur FFmpeg: {e}")
        messagebox.showerror("Erreur FFmpeg", f"Impossible de lister les encodeurs: {e}\nAssurez-vous que ffmpeg est dans le PATH.")
        with _cache_lock:
            _ffmpeg_encoders_cache = []
        return []

class FFmpegHelpers:
    """Fonctions utilitaires pour interagir avec les données des codecs."""

    def __init__(self, codec_info: Dict[str, Any]):
        self.logger = logging.getLogger(__name__)
        self.codec_info = codec_info
        self.available_ffmpeg_encoders = get_ffmpeg_encoders()

    def get_available_codecs(self, media_type: str) -> List[Dict[str, Any]]:
        """Retourne la liste des codecs disponibles pour un type de média."""
        return self.codec_info.get(media_type, [])

    def get_codec_details(self, media_type: str, codec_name: str) -> Optional[Dict[str, Any]]:
        """Retourne les détails d'un codec spécifique."""
        for codec in self.get_available_codecs(media_type):
            if codec.get("codec") == codec_name:
                return codec
        return None

    def get_available_encoders_for_codec(self, media_type: str, codec_name: str) -> List[str]:
        """
        Retourne les encodeurs disponibles pour un codec, en filtrant par ceux que ffmpeg supporte.
        """
        self.logger.info(f"🔍 DIAGNOSTIC: Recherche encodeurs pour {media_type}/{codec_name}")
        
        codec_details = self.get_codec_details(media_type, codec_name)
        if not codec_details:
            self.logger.warning(f"❌ DIAGNOSTIC: Aucun détail trouvé pour codec {codec_name}")
            return []

        self.logger.info(f"📋 DIAGNOSTIC: Détails codec trouvés: {codec_details}")

        all_encoders = []
        # Utiliser `[]` comme valeur par défaut, car c'est le format pour les images/audio.
        encoders_by_type = codec_details.get("encoders", [])

        if isinstance(encoders_by_type, dict):
            # Gérer la structure de dictionnaire pour la vidéo (cpu, nvidia, etc.)
            self.logger.debug("Structure d'encodeur de type 'dict' (vidéo) détectée.")
            for encoder_list in encoders_by_type.values():
                all_encoders.extend(encoder_list)
        elif isinstance(encoders_by_type, list):
            # Gérer la structure de liste pour audio/image
            self.logger.debug("Structure d'encodeur de type 'list' (audio/image) détectée.")
            all_encoders.extend(encoders_by_type)

        self.logger.info(f"📦 DIAGNOSTIC: Encodeurs définis dans JSON: {all_encoders}")
        self.logger.info(f"🔧 DIAGNOSTIC: Encodeurs FFmpeg disponibles: {len(self.available_ffmpeg_encoders)} encodeurs")
        self.logger.debug(f"🔧 DIAGNOSTIC: Liste complète FFmpeg: {self.available_ffmpeg_encoders}")

        # Filtrer pour ne garder que les encodeurs que l'instance ffmpeg locale connaît
        supported_encoders = [enc for enc in all_encoders if enc in self.available_ffmpeg_encoders]
        
        self.logger.info(f"✅ DIAGNOSTIC: Encodeurs supportés finalement: {supported_encoders}")
        
        if not supported_encoders and all_encoders:
            self.logger.warning(f"⚠️ DIAGNOSTIC: Aucun encodeur supporté pour {codec_name}!")
            self.logger.warning(f"   - Encodeurs demandés: {all_encoders}")
            self.logger.warning(f"   - Vérifiez que FFmpeg supporte ces encodeurs:")
            for enc in all_encoders:
                self.logger.warning(f"     * {enc}")
        
        return supported_encoders

    def get_extensions_for_codec(self, media_type: str, codec_name: str) -> List[str]:
        """Retourne les extensions de fichier pour un codec."""
        codec_details = self.get_codec_details(media_type, codec_name)
        return codec_details.get("extensions", []) if codec_details else []

    # Méthode statique pour récupérer une liste détaillée des encodeurs disponibles sur le système
    # La liste retournée contient des dictionnaires {"name", "codec", "description"}
    # Elle fusionne les informations fournies dans codecs.json avec celles réellement supportées par ffmpeg
    # Un cache est utilisé pour éviter de relire et reparser le fichier à chaque appel
    @staticmethod
    def available_encoders() -> List[Dict[str, str]]:
        global _available_encoders_info_cache

        # Retourner immédiatement si déjà calculé
        if _available_encoders_info_cache is not None:
            return _available_encoders_info_cache

        encoders_supported = set(get_ffmpeg_encoders())
        detailed_encoders: List[Dict[str, str]] = []

        # Calculer le chemin absolu vers codecs.json (un niveau au-dessus de ce module)
        codecs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "codecs.json"))

        try:
            # Charger la définition des codecs afin de disposer du mapping codec → encodeurs
            with open(codecs_path, "r", encoding="utf-8") as f:
                codec_db = json.load(f)

            media_categories = ["video", "audio", "image"]
            for category in media_categories:
                for codec_entry in codec_db.get(category, []):
                    codec_name = codec_entry.get("codec")
                    description = codec_entry.get("name", "")
                    encoders_field = codec_entry.get("encoders", {})

                    # Unifier le format en liste d'encodeurs
                    encoder_list: List[str] = []
                    if isinstance(encoders_field, dict):
                        for sublist in encoders_field.values():
                            encoder_list.extend(sublist)
                    elif isinstance(encoders_field, list):
                        encoder_list.extend(encoders_field)

                    # Garder uniquement ceux réellement supportés par ffmpeg
                    for enc in encoder_list:
                        if enc in encoders_supported:
                            detailed_encoders.append({
                                "name": enc,
                                "codec": codec_name,
                                "description": description
                            })

            # Tri alphabétique par nom pour un affichage cohérent
            detailed_encoders.sort(key=lambda e: e["name"])

        except Exception as e:
            # En cas d'erreur (fichier manquant, JSON invalide, …), on se rabat sur la liste brute d'encodeurs
            detailed_encoders = [{"name": enc, "codec": "unknown", "description": ""} for enc in sorted(encoders_supported)]

        # Mise en cache et retour
        _available_encoders_info_cache = detailed_encoders
        return detailed_encoders

    @staticmethod
    def available_codecs() -> Dict[str, List[str]]:
        global _available_codecs_cache

        # Retourner immédiatement si déjà construite
        if _available_codecs_cache is not None:
            return _available_codecs_cache

        codecs_by_type = {"video": [], "audio": [], "image": []}

        # Tentative primaire : interroger ffmpeg directement
        try:
            result = subprocess.run(["ffmpeg", "-hide_banner", "-codecs"], capture_output=True, text=True, check=True)

            # Regex pour extraire le nom du codec et son type (V=video, A=audio, S=subtitles=image proxy)
            codec_line_re = re.compile(r"^\s(?:D|E|\.) (V|A|S|\.) .... .\s+(\w+)")

            for line in result.stdout.splitlines():
                match = codec_line_re.match(line)
                if match:
                    type_flag, codec_name = match.groups()
                    if type_flag == 'V': codecs_by_type["video"].append(codec_name)
                    elif type_flag == 'A': codecs_by_type["audio"].append(codec_name)
                    elif type_flag == 'S': codecs_by_type["image"].append(codec_name)  # Approximation

        except Exception:
            # En cas d'échec, on passera à la seconde méthode (codecs.json)
            pass

        # Si l'une des catégories est vide, on complète via codecs.json
        if not all(codecs_by_type.values()):
            try:
                codecs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "codecs.json"))
                with open(codecs_path, "r", encoding="utf-8") as f:
                    codec_db = json.load(f)
                for category in ("video", "audio", "image"):
                    if not codecs_by_type[category]:
                        codecs_by_type[category] = [entry.get("codec") for entry in codec_db.get(category, [])]
            except Exception:
                # Dernier recours : valeurs par défaut
                if not codecs_by_type["video"]: codecs_by_type["video"] = ["h264", "hevc", "vp9", "av1"]
                if not codecs_by_type["audio"]: codecs_by_type["audio"] = ["aac", "mp3", "flac", "opus"]
                if not codecs_by_type["image"]: codecs_by_type["image"] = ["webp", "png", "jpeg"]

        # Tri alphabétique pour la cohérence
        for cat in codecs_by_type:
            codecs_by_type[cat] = sorted(set(codecs_by_type[cat]))

        _available_codecs_cache = codecs_by_type
        return codecs_by_type 