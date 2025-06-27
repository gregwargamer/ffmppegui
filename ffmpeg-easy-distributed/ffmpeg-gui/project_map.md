# FFmpeg Easy GUI - Project Map

This document provides a map of the project's structure and functionality, intended to help developers (and LLMs) understand the codebase and locate relevant files for modifications.

## Project Overview

FFmpeg Easy GUI is a graphical user interface for the FFmpeg command-line tool. It allows users to add files, configure encoding settings, and manage a queue of encoding jobs. The application is built with Python and the Tkinter library for the GUI.

## Core Components

The project is divided into two main directories: `core` and `gui`.

### `core` Directory

This directory contains the backend logic of the application.

- **`main.py`**: The entry point of the application. It initializes the settings, creates the main window, and starts the Tkinter event loop.

- **`core/settings.py`**: Manages the application's settings. It defines the default settings, loads settings from a JSON file, and saves settings to a JSON file.

- **`core/encode_job.py`**: Defines the `EncodeJob` class, representing a single source file to be processed. Crucially, an `EncodeJob` now contains a list of `OutputConfig` objects. Each `OutputConfig` defines a specific output to be generated from the source, including its own encoder, quality settings (CRF, bitrate, image quality), presets, custom flags, container type, and filter configurations (like scaling, LUTs, watermarks). This allows for generating multiple different outputs (e.g., 1080p H.264, 720p AV1, audio-only AAC) from a single source job. The overall status and progress of an `EncodeJob` are aggregated from its `OutputConfig` instances.

- **`core/ffmpeg_helpers.py`**: Provides helper functions for interacting with FFmpeg. It includes functions to get available encoders and codecs, including detection of hardware-accelerated encoders. It has been updated to better recognize and categorize newer image formats like HEIC, AVIF, and JPEG XL.

- **`core/worker_pool.py`**: Implements a worker pool for running encoding tasks in parallel. Each task in the pool now corresponds to processing a single `OutputConfig` from an `EncodeJob`.

### `gui` Directory

This directory contains the user interface components of the application.

- **`gui/main_window.py`**: The main window of the application. It contains the file selection, global encoding settings panel, and job queue. It handles user interactions, manages the worker pool, and its `_build_ffmpeg_command_for_output_config` method constructs FFmpeg commands for each `OutputConfig`. The global encoding settings panel has been refined:
    - Quality control labels and default values now adapt dynamically based on the selected global media type, codec, and encoder (e.g., showing "CRF Value" for x264, "FLAC Level" for FLAC, "Quality %" for JPEG).
    - UI sections for HDR, LUTs, and Subtitles are conditionally hidden if the selected global media type is audio or image.
    - The UI now supports selecting newer image formats like HEIC, AVIF, and JPEG XL in the global settings.
    It also includes a `_build_filter_string` method (likely for previews, though its direct usage might have evolved), a "Generate Preview" button, and UI for "gif" media type.

- **`gui/settings_window.py`**: The settings window, which allows users to configure the application's preferences.

- **`gui/job_edit_window.py`**: This window has been significantly overhauled. It no longer edits parameters for a single job monolithically. Instead, it allows users to manage and edit multiple `OutputConfig` instances for a given `EncodeJob` (source file). Key features:
    - Displays a list of `OutputConfig`s for the job.
    - Allows adding, removing, and duplicating `OutputConfig`s.
    - When an `OutputConfig` is selected, its specific settings (name, mode, encoder, quality, preset, container, custom flags) are loaded into dedicated tabs (Video, Audio, Image).
    - The quality input fields and labels within these tabs adapt dynamically based on the selected encoder and mode for that specific `OutputConfig` (similar to the main window's global panel).
    - Saves changes back to the specific `OutputConfig` object within the `EncodeJob`.

- **`gui/log_viewer_window.py`**: The log viewer window, which displays the FFmpeg logs for each job (likely per `OutputConfig` task).

- **`gui/folder_watcher.py`**: Implements a folder watcher that automatically adds new files to the encoding queue.

- **`gui/batch_operations_window.py`**: The batch operations window, which allows users to apply encoding settings to multiple jobs at once.

- **`gui/audio_tracks_window.py`**: The audio tracks window. It has been significantly enhanced to allow per-track management. For each audio track, the user can choose to include it, and if so, whether to 'copy' it (stream copy) or 're-encode' it using the globally defined audio settings.

- **`gui/advanced_filters_window.py`**: The advanced filters window, which allows users to apply advanced filters to a job, such as brightness, contrast, and saturation.

## Key Functionality

### Adding Files

Files can be added to the encoding queue in several ways:

- **"Add Files" button**: Opens a file dialog to select one or more files.
- **"Add Folder" button**: Opens a folder dialog to add all files in a folder and its subdirectories.
- **"Add from URL" button**: Opens a dialog to paste a URL. The application will use `yt-dlp` to download the video in the background and add it to the queue automatically.
- **Drag and drop**: Files and folders can be dragged and dropped onto the main window.
- **Folder watcher**: The application can be configured to watch a folder for new files and automatically add them to the queue.

### Encoding Settings

The encoding settings can be configured in the main window. The settings are divided into three sections:

- **File Selection**: Allows users to select the input and output folders.
- **Encoding Settings**: Allows users to select the encoder, quality, preset, and container for the output file. This section now also includes **Trimming** controls to set a start and end time for the encode.
- **Encoding Queue**: Displays the list of encoding jobs and their status.

### Job Management

The encoding queue displays the list of jobs and their status. Users can perform the following actions on jobs:

- **Edit**: Opens the job edit window to modify the encoding parameters of a single job.
- **Pause**: Pauses a running job.
- **Resume**: Resumes a paused job.
- **Cancel**: Cancels a running or pending job.
- **Remove**: Removes a job from the queue.
- **Merge Videos**: Opens a window to merge multiple video files into a single file.

### Presets

The application supports presets, which allow users to save and load encoding settings. Presets are stored in the `settings.json` file.

### Logging

The application provides a log viewer that displays the FFmpeg logs for each job. This is useful for debugging encoding issues.

### Batch Operations

The batch operations window allows users to apply encoding settings to multiple jobs at once. This is useful for encoding a large number of files with the same settings.

### Advanced Filters

The advanced filters window allows users to apply advanced filters to a job, such as brightness, contrast, and saturation.

### Audio Tracks

The application provides a powerful audio track management window. For each audio track detected in the source file, users can:
- **Include or Exclude**: Choose whether to keep the track in the output.
- **Set Action per Track**:
    - **Copy**: Perform a direct stream copy of the track, which is fast and preserves quality.
    - **Re-encode**: Re-encode the track using the codec and bitrate specified in the "Re-encoding Settings" section of the window.
This allows for fine-grained control, such as keeping a 5.1 track while re-encoding a commentary track to a lower bitrate.
