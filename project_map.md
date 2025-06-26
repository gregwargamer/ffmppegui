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

- **`core/encode_job.py`**: Defines the `EncodeJob` class, which represents a single encoding task. It stores information about the source and destination paths, encoding parameters, and the status of the job. Its `audio_config` dictionary has been updated to store a detailed list of configurations for each individual audio track.

- **`core/ffmpeg_helpers.py`**: Provides helper functions for interacting with FFmpeg. It includes functions to get available encoders and codecs, including detection of hardware-accelerated encoders.

- **`core/worker_pool.py`**: Implements a worker pool for running encoding jobs in parallel. It uses a queue to manage jobs and a thread pool to execute them.

### `gui` Directory

This directory contains the user interface components of the application.

- **`gui/main_window.py`**: The main window of the application. It contains the file selection, encoding settings, and job queue sections. It also handles user interactions and manages the worker pool. It now features a dedicated `_build_ffmpeg_command` method that centralizes the logic for constructing the final FFmpeg command string, taking into account detailed video, audio, and filter settings. It also includes a `_build_filter_string` method to generate filter strings for previews and final renders, a "Generate Preview" button to create a short preview clip, and the UI has been updated to include a "gif" media type and a dedicated GIF options frame.

- **`gui/settings_window.py`**: The settings window, which allows users to configure the application's preferences.

- **`gui/job_edit_window.py`**: The job edit window, which allows users to edit the encoding parameters of a single job.

- **`gui/log_viewer_window.py`**: The log viewer window, which displays the FFmpeg logs for each job.

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
