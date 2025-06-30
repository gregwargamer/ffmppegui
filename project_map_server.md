# Project Map - FFmpeg Easy Server Worker

## Vue d'ensemble
Application serveur pour traitement d'encodage FFmpeg distribué. Worker autonome capable de détecter ses capacités matérielles et de traiter des jobs d'encodage reçus via WebSocket.

## Structure du projet

```
ffmpeg-server/
├── main.py                          # Point d'entrée principal serveur
├── requirements.txt                 # Dépendances serveur
├── project_map_server.md           # Ce fichier
├── server/                          # Logique serveur
│   ├── encode_server.py             # Serveur WebSocket principal
│   ├── job_processor.py             # Traitement jobs FFmpeg
│   ├── file_manager.py              # Gestion transfert fichiers
│   ├── capability_detector.py       # Détection capacités (legacy)
│   ├── progress_reporter.py         # Rapport progression temps réel
│   └── config_manager.py            # Configuration serveur
├── core/                            # Modules métier
│   ├── ffmpeg_executor.py           # Exécution FFmpeg avec monitoring
│   ├── hardware_detector.py         # Détection matériel avancée
│   ├── encode_job.py                # Définition jobs (partagé)
│   └── worker_pool.py               # Pool workers local
├── shared/                          # Protocoles partagés (lien symbolique)
│   ├── protocol.py                  # Protocole WebSocket
│   ├── messages.py                  # Types de messages
│   └── utils.py                     # Utilitaires partagés
├── Dockerfile                       # Conteneurisation
├── docker-compose.yml              # Multi-serveurs
└── scripts/                        # Scripts déploiement
    ├── install.sh                   # Installation Linux
    ├── install.bat                  # Installation Windows
    └── systemd-service.sh           # Service système
```

## Modules principaux

### SERVER - Logique serveur

#### encode_server.py
- **Rôle** : Serveur WebSocket principal gérant les connexions clients
- **Responsabilités** :
  - Écoute connexions WebSocket sur port configurable
  - Authentification et validation clients
  - Dispatch messages vers handlers appropriés
  - Gestion pool connexions multiples
  - Monitoring santé serveur

#### job_processor.py
- **Rôle** : Traitement individuel des jobs d'encodage
- **Responsabilités** :
  - Réception et validation configuration job
  - Orchestration transfert fichier source
  - Exécution FFmpeg avec monitoring
  - Rapport progression temps réel
  - Retour fichier résultat

#### file_manager.py
- **Rôle** : Gestion transferts fichiers par chunks
- **Responsabilités** :
  - Réception fichiers par fragments
  - Validation intégrité (checksums)
  - Stockage temporaire sécurisé
  - Envoi résultats par chunks
  - Nettoyage automatique

#### progress_reporter.py
- **Rôle** : Monitoring et rapport progression FFmpeg
- **Responsabilités** :
  - Parsing sortie FFmpeg temps réel
  - Calcul progression, FPS, ETA
  - Détection erreurs et warnings
  - Envoi updates périodiques client

### CORE - Modules métier

#### ffmpeg_executor.py
- **Rôle** : Exécuteur FFmpeg avec monitoring avancé
- **Responsabilités** :
  - Construction commandes FFmpeg optimisées
  - Gestion processus avec timeout
  - Capture sortie stdout/stderr
  - Gestion priorités système (nice/ionice)
  - Détection blocages et recovery

#### hardware_detector.py
- **Rôle** : Détection exhaustive capacités matérielles
- **Responsabilités** :
  - Test encodeurs NVENC/QuickSync/AMF/VideoToolbox
  - Détection GPU et capacités
  - Benchmark performance relative
  - Test support résolutions/formats
  - Génération profil capacités

This file now simply re-exports `HardwareDetector` and `detect_capabilities` from `common.hardware_detector`. All detection logic is shared between GUI and server.

## Fonctionnalités clés

### Détection automatique capacités
- **NVIDIA NVENC** : h264_nvenc, hevc_nvenc, av1_nvenc
- **Intel QuickSync** : h264_qsv, hevc_qsv, av1_qsv  
- **AMD AMF** : h264_amf, hevc_amf
- **Apple VideoToolbox** : h264_videotoolbox, hevc_videotoolbox
- **Encodeurs logiciels** : libx264, libx265, libvpx, etc.

### Monitoring système temps réel
- Charge CPU/RAM actuelle
- Espace disque disponible
- Température GPU (si disponible)
- Nombre jobs simultanés
- Débit réseau entrée/sortie

### Gestion jobs intelligente
- File d'attente avec priorités
- Limitation ressources par job
- Annulation propre jobs en cours
- Recovery automatique sur erreur
- Logging détaillé toutes opérations

## Protocole de communication

### Messages entrants (du client)
- **CAPABILITY_REQUEST** : Demande capacités serveur
- **JOB_SUBMIT** : Soumission nouveau job
- **FILE_CHUNK** : Fragment fichier source
- **JOB_CANCEL** : Annulation job en cours
- **PING** : Test connectivité

### Messages sortants (vers client)
- **SERVER_INFO** : Informations serveur et capacités
- **JOB_ACCEPTED/REJECTED** : Réponse soumission
- **JOB_PROGRESS** : Progression encodage
- **JOB_COMPLETED** : Job terminé avec succès
- **JOB_FAILED** : Échec job avec détails erreur

## Configuration serveur

### Paramètres réseau
```bash
--host 0.0.0.0          # Interface écoute
--port 8765              # Port WebSocket
--max-connections 10     # Connexions simultanées max
```

### Paramètres jobs
```bash
--max-jobs 2             # Jobs simultanés max
--max-file-size 10GB     # Taille fichier max
--job-timeout 3600       # Timeout job (secondes)
--temp-dir /tmp/ffmpeg   # Répertoire temporaire
```

### Paramètres performance
```bash
--nice-level 10          # Priorité processus (0-19)
--io-priority best-effort # Priorité I/O
--cpu-limit 80           # Limite CPU % par job
```

## Points d'entrée

### main.py
```python
#!/usr/bin/env python3
"""
FFmpeg Easy Server Worker
Point d'entrée principal du serveur d'encodage
"""

import asyncio
import argparse
import signal
import sys
from pathlib import Path

from server.encode_server import EncodeServer
from server.config_manager import ServerConfig
from core.hardware_detector import detect_capabilities

async def main():
    parser = argparse.ArgumentParser()
    
    # Configuration réseau
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    
    # Configuration jobs  
    parser.add_argument("--max-jobs", type=int, default=2)
    parser.add_argument("--max-file-size", default="10GB")
    
    # Mode test
    parser.add_argument("--test-capabilities", action="store_true")
    
    args = parser.parse_args()
    
    if args.test_capabilities:
        # Afficher capacités et quitter
        caps = detect_capabilities()
        print_capabilities(caps)
        return 0
    
    # Démarrer serveur
    config = ServerConfig.from_args(args)
    server = EncodeServer(config)
    
    # Gestion signaux arrêt propre
    setup_signal_handlers(server)
    
    await server.start()

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

## Déploiement

### Installation manuelle
```bash
# Cloner et installer dépendances
git clone [repo] ffmpeg-server
cd ffmpeg-server
pip install -r requirements.txt

# Tester capacités
python main.py --test-capabilities

# Lancer serveur
python main.py --host 0.0.0.0 --port 8765 --max-jobs 2
```

### Docker
```dockerfile
FROM python:3.11-slim

# Installer FFmpeg et dépendances
RUN apt-get update && apt-get install -y \
    ffmpeg \
    nvidia-utils-470 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8765
CMD ["python", "main.py"]
```

### Service systemd
```ini
[Unit]
Description=FFmpeg Easy Encoding Server
After=network.target

[Service]
Type=simple
User=ffmpeg-server
WorkingDirectory=/opt/ffmpeg-server
ExecStart=/usr/bin/python3 main.py --host 0.0.0.0 --port 8765
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Monitoring et logs

### Logs structurés
```
2024-01-15 10:30:15 - server - INFO - 🚀 Serveur démarré - ID: abc123
2024-01-15 10:30:15 - server - INFO - 📊 Capacités: 8 CPU, 32GB RAM
2024-01-15 10:30:15 - server - INFO - 🎯 NVENC: h264_nvenc, hevc_nvenc
2024-01-15 10:30:20 - server - INFO - 👋 Client connecté: 192.168.1.100
2024-01-15 10:30:25 - server - INFO - ✅ Job accepté: job_001
2024-01-15 10:30:26 - processor - INFO - 🎬 Démarrage encodage: job_001
2024-01-15 10:30:30 - processor - INFO - 📈 Progression: 15% (fps=45.2)
```

### Métriques exposées
- Jobs traités total/succès/échecs
- Temps moyen traitement par type
- Utilisation ressources temps réel
- Erreurs réseau et timeouts

## Sécurité

### Validation entrées
- Taille fichiers limitée
- Commandes FFmpeg sanitizées
- Timeout sur toutes opérations
- Isolation processus jobs

### Gestion ressources
- Limitation CPU/RAM par job
- Nettoyage automatique fichiers temporaires
- Protection contre saturation disque
- Monitoring dépassements limites

## Extensions futures

### Clustering
- Discovery automatique autres serveurs
- Load balancing inter-serveurs
- Réplication jobs critiques
- Failover automatique

### Optimisations
- Cache préprocessing fréquent
- Parallel processing segments
- GPU memory management
- Adaptive quality based on load 