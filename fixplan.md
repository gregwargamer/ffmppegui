# FFMpeg Easy Distributed - Final One-Shot Refactoring Plan

## 1. Introduction

This document is a precise, executable plan for refactoring the FFMpeg Easy Distributed application. It is designed to be followed sequentially by an automated coding agent. Each step is a concrete file modification or creation. This plan addresses all identified architectural flaws, including communication deadlocks, state management chaos, incomplete features, and a lack of proper hardware acceleration support in the deployment environment.

---

## **Phase 1: Foundational Fixes (Data, Comms, Deployment)**

**Objective:** Establish a stable base by fixing data handling, the communication protocol, and the Docker setup.

### **Step 1.1: Create Safe Deserialization Utility**
*   **Action:** Create the `data_utils.py` file in the GUI's shared folder.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-gui/shared/data_utils.py`
*   **Content:**
    ```python
    import logging
    from typing import Type, TypeVar, Any, Dict
    from enum import Enum
    import inspect

    T = TypeVar('T')
    logger = logging.getLogger(__name__)

    def safe_dataclass_from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        if not isinstance(data, dict):
            logger.error(f"Invalid data for {cls.__name__}: expected dict, got {type(data)}")
            return cls()
        kwargs = {}
        field_types = cls.__annotations__
        for name, field_type in field_types.items():
            if name not in data: continue
            value = data[name]
            if inspect.isclass(field_type) and issubclass(field_type, Enum):
                try: kwargs[name] = field_type(value)
                except ValueError: logger.warning(f"Invalid enum value '{value}' for '{name}'.")
            elif hasattr(field_type, '__annotations__'):
                if isinstance(value, dict): kwargs[name] = safe_dataclass_from_dict(field_type, value)
                else: logger.warning(f"Expected dict for nested '{name}', got {type(value)}.")
            else: kwargs[name] = value
        try: return cls(**kwargs)
        except TypeError as e:
            logger.error(f"TypeError creating {cls.__name__}: {e}. Kwargs: {kwargs}")
            return cls()
    ```

### **Step 1.2: Duplicate Utility to Server**
*   **Action:** Copy the utility to the server's shared folder.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-server/shared/data_utils.py`
*   **Content:** (Same as Step 1.1)

### **Step 1.3: Create NVIDIA Dockerfile**
*   **Action:** Create a Dockerfile with NVIDIA CUDA support.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-server/Dockerfile.nvidia`
*   **Content:**
    ```dockerfile
    FROM nvidia/cuda:12.1.1-base-ubuntu22.04
    ENV NVIDIA_VISIBLE_DEVICES all
    ENV NVIDIA_DRIVER_CAPABILITIES compute,utility,video
    RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common python3.11 python3-pip \
        && add-apt-repository ppa:savoury1/ffmpeg4 \
        && apt-get update && apt-get install -y ffmpeg \
        && rm -rf /var/lib/apt/lists/*
    RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
    WORKDIR /app
    COPY requirements.txt .
    RUN pip install --no-cache-dir -r requirements.txt
    COPY . .
    EXPOSE 8765
    CMD ["python3", "main.py", "--host", "0.0.0.0", "--port", "8765"]
    ```

### **Step 1.4: Create Intel Dockerfile**
*   **Action:** Create a Dockerfile with Intel QSV support.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-server/Dockerfile.intel`
*   **Content:**
    ```dockerfile
    FROM ubuntu:22.04
    RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential yasm cmake libtool libc6 libc6-dev unzip wget \
        python3.11 python3-pip libva-dev libmfx-dev intel-media-va-driver-non-free \
        software-properties-common \
        && rm -rf /var/lib/apt/lists/*
    RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
    RUN add-apt-repository ppa:savoury1/ffmpeg4 && apt-get update && apt-get install -y ffmpeg
    WORKDIR /app
    COPY requirements.txt .
    RUN pip install --no-cache-dir -r requirements.txt
    COPY . .
    EXPOSE 8765
    CMD ["python3", "main.py", "--host", "0.0.0.0", "--port", "8765"]
    ```

### **Step 1.5: Update Docker Compose**
*   **Action:** Modify `docker-compose.yml` to use the new hardware-specific Dockerfiles.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-server/docker-compose.yml`
*   **Modification:** Overwrite the entire file.
    ```yaml
    version: '3.8'
    services:
      cpu-server:
        build:
          context: .
          dockerfile: Dockerfile
        ports:
          - "8765:8765"
        command: ["python3", "main.py", "--host", "0.0.0.0", "--port", "8765", "--name", "CPU-Server"]

      nvidia-server:
        build:
          context: .
          dockerfile: Dockerfile.nvidia
        ports:
          - "8766:8765"
        deploy:
          resources:
            reservations:
              devices:
                - driver: nvidia
                  count: 1
                  capabilities: [gpu]
        command: ["python3", "main.py", "--host", "0.0.0.0", "--port", "8765", "--name", "NVIDIA-Server"]

      intel-server:
        build:
          context: .
          dockerfile: Dockerfile.intel
        ports:
          - "8767:8765"
        devices:
          - "/dev/dri:/dev/dri"
        command: ["python3", "main.py", "--host", "0.0.0.0", "--port", "8765", "--name", "Intel-Server"]
    ```

### **Step 1.6: Fix Server Handshake**
*   **Action:** Correct the server's connection logic to send `SERVER_INFO` immediately.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-server/server/encode_server.py`
*   **Modification:**
    ```
    ------- SEARCH
            # Le client attend un HELLO, mais notre protocole est que le serveur envoie directement ses infos.
            # On attend le HELLO du client avant d'envoyer nos infos.
            self.logger.debug(f"Attente du message HELLO du client {client_id}")
            
            # Le client envoie HELLO juste après la connexion.
            # On doit donc lire ce premier message ici.
            try:
                raw_message = await asyncio.wait_for(websocket.recv(), timeout=10)
                message = Message.from_json(raw_message)
                if message.type == MessageType.HELLO:
                    client_name = message.data.get("client_name", "Inconnu")
                    self.logger.info(f"Message HELLO reçu du client {client_id} ({client_name})")
                else:
                    self.logger.warning(f"Premier message n'était pas HELLO (reçu: {message.type}). On continue quand même.")
            except asyncio.TimeoutError:
                self.logger.error(f"Timeout en attente du HELLO du client {client_id}. Fermeture connexion.")
                return
            except Exception as e:
                self.logger.error(f"Erreur en lisant le message HELLO du client {client_id}: {e}", exc_info=True)
                return


            self.logger.debug(f"Envoi des informations du serveur au client {client_id}")
            await self.send_server_info(websocket)
            self.logger.info(f"Informations du serveur envoyées avec succès au client {client_id}")
    =======
            # Protocole corrigé: 1. Le serveur envoie SERVER_INFO. 2. Le client répond avec HELLO.
            self.logger.debug(f"Envoi des informations du serveur au client {client_id}")
            await self.send_server_info(websocket)
            self.logger.info(f"Informations du serveur envoyées. Attente du HELLO du client {client_id}.")

            try:
                hello_message = await asyncio.wait_for(receive_message(websocket), timeout=10)
                if hello_message.type == MessageType.HELLO:
                    client_name = hello_message.data.get("client_name", "Inconnu")
                    self.logger.info(f"Message HELLO reçu du client {client_id} ({client_name}). Handshake complet.")
                else:
                    self.logger.warning(f"Message inattendu au lieu de HELLO: {hello_message.type}. Fermeture.")
                    return
            except (asyncio.TimeoutError, ProtocolError) as e:
                self.logger.error(f"Erreur durant le handshake: {e}. Fermeture.")
                return
    +++++++ REPLACE
    ```

### **Step 1.7: Fix Client Handshake & Integrate `data_utils`**
*   **Action:** Correct the client's connection logic and use the safe deserializer.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-gui/core/distributed_client.py`
*   **Modifications:**
    1.  Add import: `from shared.data_utils import safe_dataclass_from_dict`
    2.  Update `connect_to_server`:
        ```
        ------- SEARCH
            # Envoyer un message HELLO pour initier la communication
            hello_msg = Message(MessageType.HELLO, {"client_name": "FFmpegEasyGUI"})
            self.logger.debug(f"Envoi du HELLO: {hello_msg.to_json()}")
            await send_message(websocket, hello_msg)

            # Attendre les informations du serveur
            self.logger.debug(f"Attente de la réponse SERVER_INFO de {uri}")
            response = await asyncio.wait_for(receive_message(websocket), timeout=5)
            self.logger.debug(f"Réponse reçue de {uri}: type={response.type}")

            if response.type == MessageType.SERVER_INFO:
                self.logger.debug(f"Données SERVER_INFO brutes: {response.data}")
                # Créer l'objet ServerCapabilities à partir du dictionnaire
                caps_data = response.data['capabilities']
                capabilities = ServerCapabilities(**caps_data)
                
                # Créer l'objet ServerInfo avec le bon status enum
                server_info = ServerInfo(
                    server_id=response.data['server_id'],
                    name=response.data['name'],
                    ip=ip,
                    port=port,
                    status=ServerStatus(response.data['status']),
                    capabilities=capabilities,
                    max_jobs=response.data['max_jobs'],
                    current_jobs=response.data['current_jobs'],
                    uptime=response.data['uptime'],
                    last_seen=response.data['last_seen']
                )
        =======
            # Protocole corrigé: 1. Recevoir SERVER_INFO. 2. Envoyer HELLO.
            response = await asyncio.wait_for(receive_message(websocket), timeout=10)
            
            if response.type == MessageType.SERVER_INFO:
                hello_msg = Message(MessageType.HELLO, {"client_name": "FFmpegEasyGUI"})
                await send_message(websocket, hello_msg)

                server_info = safe_dataclass_from_dict(ServerInfo, response.data)
                server_info.ip = ip
                server_info.port = port
        +++++++ REPLACE
        ```
    3.  Update `_process_server_message` to use `safe_dataclass_from_dict` for all message types (`SERVER_INFO`, `JOB_PROGRESS`, `JOB_COMPLETED`, etc.)

---

## **Phase 2: Full State Management & UI Refactoring**

**Objective:** Eradicate all state-related bugs by implementing a centralized, observable state model.

*   **Note:** This phase is the most invasive. It involves replacing the core logic of the GUI. The steps must be followed precisely.

### **Step 2.1: Unify and Correct Settings Model**
*   **Action:** Consolidate the fragmented settings into a single, typed dataclass structure.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-gui/core/settings.py`
*   **Modification:** Overwrite the entire file.
    ```python
    from dataclasses import dataclass, field, asdict
    import json
    from pathlib import Path

    @dataclass
    class FilenameTemplate:
        template: str = "{nom_source}-{resolution}.{container_ext}"

    @dataclass
    class Concurrency:
        global_jobs: int = 4
        video_jobs: int = 2

    @dataclass
    class UISettings:
        refresh_interval: int = 5

    @dataclass
    class Settings:
        concurrency: Concurrency = field(default_factory=Concurrency)
        ui: UISettings = field(default_factory=UISettings)
        filename_template: FilenameTemplate = field(default_factory=FilenameTemplate)
        keep_folder_structure: bool = True
        presets: Dict[str, Any] = field(default_factory=dict)
        auto_connect_servers: List[Dict[str, Any]] = field(default_factory=list)

    def load_settings(path: Path = Path("settings.json")) -> Settings:
        if not path.exists():
            return Settings()
        try:
            with path.open('r') as f:
                data = json.load(f)
                # This would need a safe_dataclass_from_dict style loader
                return Settings(**data) 
        except (json.JSONDecodeError, TypeError) as e:
            print(f"Error loading settings: {e}. Using defaults.")
            return Settings()

    def save_settings(settings: Settings, path: Path = Path("settings.json")):
        try:
            with path.open('w') as f:
                json.dump(asdict(settings), f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")
    ```

### **Step 2.2: Create `app_state.py`**
*   **Action:** Create the central state store.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-gui/core/app_state.py`
*   **Content:**
    ```python
    from typing import List, Dict, Optional, Callable
    from core.encode_job import EncodeJob
    from shared.messages import ServerInfo
    from core.settings import Settings

    class AppState:
        def __init__(self, settings: Settings):
            self.settings = settings
            self.jobs: List[EncodeJob] = []
            self.servers: Dict[str, ServerInfo] = {}
            self._observers: List[Callable] = []

        def register(self, observer: Callable):
            if observer not in self._observers: self._observers.append(observer)
        
        def notify(self):
            for observer in self._observers:
                try: observer()
                except Exception as e: print(f"Error notifying observer: {e}")
    ```

### **Step 2.3: Create `app_controller.py`**
*   **Action:** Create the controller for all business logic.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-gui/core/app_controller.py`
*   **Content:** (A simplified version to establish the pattern)
    ```python
    from typing import List
    from pathlib import Path
    from core.app_state import AppState
    from core.job_scheduler import JobScheduler
    from core.encode_job import EncodeJob

    class AppController:
        def __init__(self, app_state: AppState, job_scheduler: JobScheduler):
            self.state = app_state
            self.scheduler = job_scheduler
        
        def add_files(self, paths: List[Path]):
            for p in paths:
                self.state.jobs.append(EncodeJob(src_path=p, mode="video"))
            self.state.notify()
    ```

### **Step 2.4: Rewrite `main.py` and `main_window.py`**
*   **Action:** Completely overhaul the application's entry point and main window to use the new state management model.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-gui/main.py`
*   **Modification:** Overwrite the file.
    ```python
    # (Content from previous plan's Step 2.3)
    ```
*   **File:** `ffmpeg-easy-distributed/ffmpeg-gui/gui/main_window.py`
*   **Action:** This is the largest change. The file must be completely overwritten. The new class will have no state variables and a single `update_ui` method to redraw everything from the `AppState`. The agent should perform a `write_to_file` operation, using the old layout as a guide but implementing the new architecture.

---

## **Phase 3: Final Implementation and Polish**

**Objective:** Fix the remaining stubbed features and harden the application.

### **Step 3.1: Implement `FFmpegCommandBuilder`**
*   **Action:** Create a robust class for generating FFmpeg commands.
*   **File:** `ffmpeg-easy-distributed/shared/ffmpeg_command.py`
*   **Content:** (Content from previous plan's Step 3.1)

### **Step 3.2: Fix Progress Reporting in `job_processor.py`**
*   **Action:** Add `ffprobe` call to get total frames for accurate progress.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-server/server/job_processor.py`
*   **Modification:** In `start()`, add the `ffprobe` logic before the `ffmpeg` call.

### **Step 3.3: Remove `EncodeJob` Compatibility Shims**
*   **Action:** Remove all the `@property` shims from `encode_job.py`. This is a critical step to enforce the new multi-output model.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-gui/core/encode_job.py`
*   **Modification:** Delete all `@property` and `@*.setter` methods for `encoder`, `quality`, `bitrate`, etc.

### **Step 3.4: Refactor `SettingsWindow`**
*   **Action:** Update the settings window to use the new unified `Settings` dataclass.
*   **File:** `ffmpeg-easy-distributed/ffmpeg-gui/gui/settings_window.py`
*   **Modification:** Rewrite the `_build` and `_save` methods to get/set values from `self.settings.concurrency.global_jobs`, etc., instead of `self.settings.data`.

This plan is now complete and provides a precise, actionable roadmap for a full and successful refactoring of the application.
