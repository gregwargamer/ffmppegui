# FFmpeg-Easy – Feuille de route restante

> Mise à jour : 2025-06-29

## ✅ Accompli récemment
1. Limitation des reconnexions infinies (`max_reconnect_attempts`).
2. Cache FFmpeg thread-safe.
3. Debounce du `FolderWatcher`.
4. File de priorité dans `JobScheduler`.
5. Annulation de jobs local/distant.
6. Unification du `HardwareDetector` (module commun).
7. Import dynamique sûr pour `shared.messages`.
8. Boucle Tk/asyncio paramétrable + purge complète des callbacks `after`.
9. Documentation `project_map*.md` mise à jour en continu.
10. Persistance de la file d'attente à l'ouverture (`queue.json` + reprise automatique).

---

## 📝 Tâches restantes (par priorité proposée)

### 1. Externalisation des constantes & paramètres
- Déplacer dans `settings.json` :
  - Délais de timeout réseau (pings, reconnexion, uploads).
  - Back-off exponentiel (min/max, facteur).
  - Chemins temporaires (`temp_dir`).
  - Intervalles de maintenance (`EncodeServer.maintenance_loop`).
- Ajouter getters dans `core/settings.py` + UI (onglet **Avancé**).

### 2. Équilibrage charge & autoscaling
- Exploiter `ServerCapabilities.current_load` + `estimated_performance`.
- Algorithme : coefficient pondéré pour choisir le meilleur serveur.
- Option *auto-preemption* : déplacer un job vers un autre serveur si surcharge.

### 3. Packaging & installation
- Créer `pyproject.toml` / `setup.cfg` (PEP 621).
- Scripts de build :
  - macOS `.app` (py2app ou PyInstaller).
  - Windows `.exe` (PyInstaller + ffmpeg statique).
  - Linux AppImage / `.deb`.
- Mise à jour `requirements.txt` (versions figées).

### 4. Tests & CI
- `pytest` minimal : encoder un échantillon court en local & mock WebSocket.
- Workflows GitHub : lint (ruff), type-check (mypy/pyright), tests.

### 5. Améliorations UI/UX
- Barre de recherche dans la liste de jobs.
- Dark mode automatique.
- Notifications système (macOS : `pync`, Windows : `win10toast`).

### 6. Sécurité & réseau
- TLS pour WebSocket (wss://).
- Authentification simple par token partagé.
- Vérification checksum après upload fichier.

### 7. Documentation & exemples
- Tutoriel pas-à-pas (README).
- Exemples de ligne de commande pour lancer le serveur Docker.

---

## 💡 Idées futures
- Support NVIDIA NVENC AV1 « workaround » (--enable-non-free).
- Plugin de post-traitement (ex. upload automatisé vers YouTube).
- Tableur d'analyse de performance automatique.

---

> **Organisation** : chaque tâche majeure doit être reflétée dans `project_map.md` & faire l'objet d'un test automatisé si possible. 