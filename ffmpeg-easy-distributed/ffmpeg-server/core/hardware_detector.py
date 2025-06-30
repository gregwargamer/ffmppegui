#this part ajoute dynamiquement le chemin racine du projet pour trouver le module commun
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

#this other part tente d'importer le détecteur depuis le module commun
from common.hardware_detector import HardwareDetector, detect_capabilities  # type: ignore

__all__ = ["HardwareDetector", "detect_capabilities"]
