# Project Map - FFmpeg Easy GUI Client

## Vue d'ensemble
Application cliente pour interface graphique d'encodage distribué FFmpeg. Permet de gérer les serveurs distants, soumettre des jobs et suivre les progressions.

## Structure du projet

```
ffmpeg-gui/
├── main.py                           # Point d'entrée principal
├── requirements.txt                  # Dépendances GUI
├── project_map_gui.md               # Ce fichier
├── gui/                             # Interfaces utilisateur
│   ├── main_window.py               # Fenêtre principale existante (modifiée)
│   ├── server_manager_window.py     # NOUVEAU: Gestion serveurs
│   ├── job_queue_window.py          # NOUVEAU: File d'attente jobs
│   ├── capability_viewer.py         # NOUVEAU: Visualisation capacités
│   ├── job_edit_window.py           # Édition jobs (modifié pour distribution)
│   ├── settings_window.py           # Paramètres
│   ├── log_viewer_window.py         # Visualisation logs
│   ├── batch_operations_window.py   # Opérations par lots
│   ├── advanced_filters_window.py   # Filtres avancés
│   ├── audio_tracks_window.py       # Gestion pistes audio
│   ├── merge_videos_window.py       # Fusion vidéos
│   └── subtitle_management_window.py # Gestion sous-titres
├── core/                            # Logique métier client
│   ├── distributed_client.py        # NOUVEAU: Client distribué principal
│   ├── server_discovery.py          # NOUVEAU: Découverte serveurs
│   ├── job_scheduler.py             # NOUVEAU: Planificateur jobs
│   ├── capability_matcher.py        # NOUVEAU: Correspondance capacités
│   ├── encode_job.py                # Définition jobs (partagé)
│   ├── settings.py                  # Paramètres GUI
│   └── ffmpeg_helpers.py            # Utilitaires FFmpeg
└── shared/                          # Protocoles partagés (lien symbolique)
    ├── protocol.py                  # Protocole WebSocket
    ├── messages.py                  # Types de messages
    └── utils.py                     # Utilitaires partagés
```

## Modules principaux

### GUI - Interfaces utilisateur

#### main_window.py (MODIFIÉ)
- **Rôle** : Fenêtre principale de l'application
- **Modifications** : 
  - Ajout menu "Serveurs" avec gestion serveurs distants
  - Indicateur statut serveurs connectés
  - Option choix serveur pour nouveaux jobs
  - Affichage jobs distribués en cours

#### server_manager_window.py (NOUVEAU)
- **Rôle** : Interface de gestion des serveurs d'encodage
- **Fonctionnalités** :
  - Ajout serveurs par IP:port
  - Test connexion et ping
  - Affichage capacités détaillées (NVENC, QuickSync, etc.)
  - Statut temps réel (charge, jobs actifs)
  - Configuration priorités serveurs

#### job_queue_window.py (NOUVEAU)
- **Rôle** : Visualisation et gestion de la file d'attente
- **Fonctionnalités** :
  - Liste jobs en attente/en cours/terminés
  - Réassignation jobs vers autres serveurs
  - Priorités et ordonnancement
  - Statut détaillé par job

#### capability_viewer.py (NOUVEAU)
- **Rôle** : Visualisation des capacités serveurs
- **Fonctionnalités** :
  - Matrice compatibilité encodeurs/serveurs
  - Comparaison performances
  - Recommandations serveur optimal par job
  - Alertes incompatibilités

### CORE - Logique métier

#### distributed_client.py (NOUVEAU)
- **Rôle** : Client principal pour communication serveurs
- **Responsabilités** :
  - Gestion connexions WebSocket multiples
  - Pool de serveurs actifs
  - Distribution automatique jobs
  - Gestion reconnexions

#### server_discovery.py (NOUVEAU)
- **Rôle** : Découverte et monitoring serveurs
- **Responsabilités** :
  - Connexion initiale aux serveurs
  - Test périodique disponibilité
  - Détection nouvelles capacités
  - Mise à jour statuts

#### job_scheduler.py (NOUVEAU)
- **Rôle** : Planification et répartition des jobs
- **Responsabilités** :
  - Algorithmes de distribution
  - Gestion priorités
  - Équilibrage charge
  - Réassignation automatique

#### capability_matcher.py (NOUVEAU)
- **Rôle** : Correspondance jobs/serveurs selon capacités
- **Responsabilités** :
  - Analyse compatibilité encodeurs
  - Score de recommandation
  - Détection conflits
  - Suggestions alternatives

## Flux de données

### Connexion serveur
1. Utilisateur saisit IP:port dans server_manager_window
2. server_discovery teste connexion
3. Récupération capacités serveur
4. Ajout à la liste serveurs actifs
5. Monitoring périodique statut

### Soumission job
1. Utilisateur configure job dans job_edit_window
2. capability_matcher analyse compatibilité serveurs
3. job_scheduler sélectionne serveur optimal
4. distributed_client soumet job
5. Suivi progression temps réel

### Réassignation job
1. Utilisateur sélectionne job dans job_queue_window
2. Choix nouveau serveur cible
3. Vérification compatibilité automatique
4. Annulation job sur serveur actuel
5. Soumission sur nouveau serveur

## Points d'entrée principaux

### main.py
```python
#!/usr/bin/env python3
"""
FFmpeg Easy GUI Client
Point d'entrée principal de l'interface graphique
"""

import sys
import tkinter as tk
from pathlib import Path

# Ajouter shared au path
sys.path.append(str(Path(__file__).parent.parent))

from gui.main_window import MainWindow
from core.distributed_client import DistributedClient
from core.settings import load_settings

def main():
    # Chargement configuration
    settings = load_settings()
    
    # Initialisation client distribué
    distributed_client = DistributedClient(settings)
    
    # Interface graphique
    root = tk.Tk()
    app = MainWindow(root, distributed_client)
    
    # Démarrage
    root.mainloop()

if __name__ == "__main__":
    main()
```

## Intégrations importantes

### Menu principal modifié
- **Serveurs** → Gestion des serveurs d'encodage
- **Jobs** → File d'attente et monitoring
- **Capacités** → Visualisation matériel disponible

### Statut bar étendu
- Nombre serveurs connectés
- Jobs distribués actifs
- Indicateur santé réseau

### Dialog job edit étendu
- Sélecteur serveur cible
- Indicateur compatibilité temps réel
- Estimation durée par serveur

## Dépendances

### requirements.txt
```
tkinter-tooltip>=2.0.0
websockets>=10.0
asyncio-tkinter>=0.3.0
Pillow>=9.0.0
numpy>=1.21.0
```

## Configuration

### settings.json (exemple)
```json
{
    "distributed": {
        "auto_connect_servers": [],
        "default_timeout": 30,
        "max_concurrent_jobs": 10,
        "preferred_encoders": ["h264_nvenc", "libx264"]
    },
    "ui": {
        "refresh_interval": 5,
        "show_server_details": true,
        "auto_select_best_server": true
    }
}
```

## Cas d'usage typiques

### 1. Premier lancement
- Aucun serveur configuré
- Guide d'ajout premier serveur
- Test capacités automatique

### 2. Ajout serveur NVENC
- Saisie IP machine avec GPU NVIDIA
- Test automatique encodeurs NVENC
- Affichage capacités détectées
- Configuration priorité haute pour H.264/HEVC

### 3. Job nécessitant VideoToolbox
- Sélection encodeur h264_videotoolbox
- Système détecte besoin serveur macOS
- Alerte si aucun serveur compatible
- Suggestion alternatives (libx264)

### 4. Réassignation job urgent
- Job en attente sur serveur chargé
- Utilisateur force réassignation
- Vérification compatibilité automatique
- Migration transparente

## Evolutivité

### Extensions futures possibles
- Auto-découverte serveurs sur réseau local
- Load balancing avancé
- Clustering serveurs par type
- Historique performances
- Notifications push terminaison jobs 