```markdown
# Project Map for FFmpegEasyUI

This document outlines the structure and key components of the FFmpegEasyUI project.

## Project Root

-   `.cursor/`: Cursor IDE specific files.
    -   `rules/gen.mdc`: Cursor generation rules.
-   `.gitignore`: Specifies intentionally untracked files that Git should ignore.
-   `README.md`: General information about the project.
-   `core/`: Contains the backend logic and core functionalities.
-   `gui/`: Contains all the user interface elements and logic.
-   `license.txt`: Project license information.
-   `main.py`: The main entry point for the application.
-   `requirements.txt`: Lists Python package dependencies.
-   `@project_map.md`: This file, outlining the project structure.

## Core Components (`core/`)

The `core` directory is responsible for the non-GUI logic of the application.

-   `__pycache__/`: Python bytecode cache.
-   `encode_job.py`: Defines the `EncodeJob` class, representing a single encoding task, its parameters, and status. Handles the execution of FFmpeg commands.
-   `ffmpeg_helpers.py`: Provides utility functions related to FFmpeg, such as querying available encoders, codecs, and determining hardware acceleration capabilities (`FFmpegHelpers` class).
-   `settings.py`: Manages application settings, likely using a configuration file (e.g., JSON). Handles loading and saving of user preferences and presets (`Settings` class).
-   `worker_pool.py`: Implements a worker pool (`WorkerPool` class) for managing concurrent encoding jobs, allowing multiple files to be processed simultaneously without freezing the UI.

## GUI Components (`gui/`)

The `gui` directory contains all code related to the graphical user interface, built with Tkinter.

-   `__pycache__/`: Python bytecode cache.
-   `main_window.py`: Defines the main application window (`MainWindow` class), orchestrating all UI elements, job queues, settings panels, and interactions. This is the central hub of the UI.
-   `settings_window.py`: Defines the `SettingsWindow` class for managing application-wide preferences.
-   `job_edit_window.py`: Defines the `JobEditWindow` class, allowing users to modify parameters for individual encoding jobs.
-   `log_viewer_window.py`: Defines the `LogViewerWindow` class for displaying FFmpeg logs and application messages.
-   `batch_operations_window.py`: Defines the `BatchOperationsWindow` class for applying settings to multiple selected jobs at once.
-   `advanced_filters_window.py`: Defines the `AdvancedFiltersWindow` class for configuring complex FFmpeg video/audio filters.
-   `audio_tracks_window.py`: Defines the `AudioTracksWindow` class for managing audio track selection and configuration for encoding jobs.
-   `folder_watcher.py`: Implements folder watching functionality (`FolderWatcher` class using `watchdog`) to automatically add files to the queue when they appear in a specified input directory.

## Main Application

-   `main.py`: Initializes the Tkinter root window and launches the `MainWindow` from the `gui` package. This script starts the application.

## Dependencies

-   `requirements.txt`: Specifies external Python libraries required by the project (e.g., `psutil`, `tkinterdnd2`, `watchdog`, `Pillow`).

## Key Functionalities

-   **File/Folder Addition**: Adding media files or entire folders to an encoding queue.
-   **Encoding Configuration**: Selecting codecs, encoders (including hardware accelerated), quality/bitrate, resolution, container formats, and custom FFmpeg flags.
-   **Preset Management**: Saving and loading encoding configurations as presets.
-   **Job Queue Management**: Viewing, reordering, editing, pausing, resuming, and canceling encoding jobs.
-   **Concurrent Encoding**: Processing multiple files in parallel using a worker pool.
-   **Media Inspection**: Viewing details of media files using `ffprobe`.
-   **Real-time Progress & Logs**: Displaying encoding progress and FFmpeg output.
-   **Drag and Drop**: Adding files/folders via drag and drop.
-   **Folder Watching**: Automatically processing files added to a monitored folder.
-   **Advanced Filtering & Audio Track Selection**: Fine-grained control over video and audio processing.
```
