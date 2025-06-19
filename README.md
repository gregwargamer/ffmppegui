# FFmpeg Easy

A simple graphical interface for FFmpeg to encode video, audio, and image files.

## What it does

This app makes FFmpeg easy to use with a point-and-click interface. Just add your files, pick an encoder, and start encoding.

## Quick Start

1. **Install requirements:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Make sure FFmpeg is installed** on your system

3. **Run the app:**
   ```bash
   python main.py
   ```

## How to use

1. **Add files** - Click "Add Files" or drag & drop files into the app
2. **Choose codec** - Pick video codec like H.264, H.265, AV1, etc.
3. **Select encoder** - The app shows compatible encoders (including hardware ones)
4. **Set quality** - Adjust CRF/quality settings
5. **Apply settings** - Click "Apply Settings" 
6. **Start encoding** - Click "Start Encoding"

**Note:** You don't need to select an output folder. Files will be saved in the same location as the source with a suffix like `_x265.mp4`.

## Features

- **Video encoding**: H.264, H.265, AV1, VP9 with hardware acceleration
- **Audio encoding**: AAC, MP3, Opus, FLAC
- **Image conversion**: WebP, AVIF, JPEG, PNG  
- **Batch processing**: Handle multiple files at once
- **Smart apply buttons**: Automatically applies to selected items or all items
- **Advanced filters**: Color correction, cropping, rotation
- **Preset system**: Save and load your favorite settings

## Smart features

- **No output folder required** - Files saved next to originals with encoder suffix
- **Hardware acceleration** - Supports NVIDIA, Intel, AMD, and Apple encoders  
- **Unified controls** - One "Apply" button that's context-aware
- **Real-time progress** - See encoding progress and logs

That's it! The app is designed to be simple and just work. 