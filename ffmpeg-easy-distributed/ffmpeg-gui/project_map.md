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

    A new field **`max_reconnect_attempts`** has been added to `DistributedSettings`. It limits how many times the application will attempt to reconnect to a distributed server (default `10`, `-1` for unlimited).

    **`reconnect_initial_delay`** and **`reconnect_max_delay`** allow tuning the exponential back-off (defaults: 5 s → 60 s).

    **`ping_timeout`** sets how long the GUI waits (seconds) for a PONG or SERVER_INFO response (default 5 s).

- **`core/encode_job.py`**: Defines the `EncodeJob` class, representing a single source file to be processed. Crucially, an `EncodeJob` now contains a list of `OutputConfig` objects. Each `OutputConfig` defines a specific output to be generated from the source, including its own encoder, quality settings (CRF, bitrate, image quality), presets, custom flags, container type, and filter configurations (like scaling, LUTs, watermarks). This allows for generating multiple different outputs (e.g., 1080p H.264, 720p AV1, audio-only AAC) from a single source job. The overall status and progress of an `EncodeJob` are aggregated from its `OutputConfig` instances.

- **`core/ffmpeg_helpers.py`**: Provides helper functions for interacting with FFmpeg. It includes functions to get available encoders and codecs, including detection of hardware-accelerated encoders. It has been updated to better recognize and categorize newer image formats like HEIC, AVIF, and JPEG XL.

    The global cache of encoders is now **thread-safe** via a `threading.Lock`, preventing race conditions when multiple threads query FFmpeg concurrently.

    UI loop interval is now configurable via `UISettings.tk_loop_interval_ms` (default **50 ms**). `AsyncTkApp` uses this value instead of a hard-coded constant.

    All pending `after` callbacks are now cancelled on application exit to avoid Tcl errors like *invalid command name "_update_loop"*.

    The cancellation loop now properly splits the list returned by `after info`, ensuring every remaining callback is removed.

- **`core/job_scheduler.py`**: Implements a worker pool for running encoding tasks in parallel. Each task in the pool now corresponds to processing a single `OutputConfig` from an `EncodeJob`.

    The `cancel_job()` method now fully supports cancellation: if the job is assigned to the local server, it calls `LocalServer.cancel_job`; if assigned to a remote server, it uses `DistributedClient.cancel_job_on_server` which sends a `JOB_CANCEL` message. Jobs still queued are removed from the asyncio queue.

- **`core/distributed_client.py`**: Provides the high-level WebSocket client for communicating with distributed encoding servers. It maintains active connections, listens for messages, and handles job submission and file downloads.

    The `_reconnect_server()` coroutine now respects `settings.distributed.max_reconnect_attempts`, applies exponential back-off (capped at 60 s) and stops trying after the configured limit, logging a clear error message.

    A new `cancel_job_on_server()` method sends a `JOB_CANCEL` message to a given server, used by the scheduler for remote cancellation.

- **`core/hardware_detector.py`**: Proxy that re-exports `HardwareDetector` from the shared `common.hardware_detector` module (no more duplication).

### `gui` Directory

This directory contains the user interface components of the application.

- **`gui/main_window.py`**: The main window of the application. It contains the file selection, global encoding settings panel, and job queue. It handles user interactions, manages the worker pool, and its `_build_ffmpeg_command_for_output_config` method constructs FFmpeg commands for each `OutputConfig`. The global encoding settings panel has been refined:
    - Quality control labels and default values now adapt dynamically based on the selected global media type, codec, and encoder (e.g., showing "CRF Value" for x264, "FLAC Level" for FLAC, "Quality %" for JPEG).
    - UI sections for HDR, LUTs, and Subtitles are conditionally hidden if the selected global media type is audio or image.
    - The UI now supports selecting newer image formats like HEIC, AVIF, and JPEG XL in the global settings.
    It also includes a `_build_filter_string` method (likely for previews, though its direct usage might have evolved), a "Generate Preview" button, and UI for "gif" media type.

- **`gui/settings_window.py`**: The settings window, which allows users to configure the application's preferences.

- **`gui/job_edit_window.py`**: Allows editing a single job before/after it is enqueued. Users can now choose the encoding server (auto/manual) **and assign a priority (1–10)** via a spinbox; the chosen value is saved to `job.priority`.

- **`gui/log_viewer_window.py`**: The log viewer window, which displays the FFmpeg logs for each job (likely per `OutputConfig` task).

- **`gui/folder_watcher.py`**: Implements a folder watcher that automatically adds new files to the encoding queue.

    A simple **debounce** (1 s) has been added to avoid duplicate "file created" events when applications write a file in several passes.

- **`gui/batch_operations_window.py`**: The batch operations window, which allows users to apply encoding settings to multiple jobs at once.

- **`gui/audio_tracks_window.py`**: The audio tracks window. It has been significantly enhanced to allow per-track management. For each audio track, the user can choose to include it, and if so, whether to 'copy' it (stream copy) or 're-encode' it using the globally defined audio settings.

- **`gui/advanced_filters_window.py`