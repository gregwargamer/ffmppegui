# FFmpeg Easy

A modern graphical interface for FFmpeg that makes encoding video, audio, and image files incredibly simple.

## What it does

This application makes FFmpeg accessible to everyone with an intuitive interface. Add your files, choose an encoder, and start encoding. The interface intelligently adapts based on the selected media type.

## Quick Start

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Make sure FFmpeg is installed** on your system

3. **Run the application:**
   ```bash
   python main.py
   ```

## Usage Guide

### Basic Workflow
1. **Add files** - Click "Add Files" or drag & drop your files
2. **Select media type** - Video, Audio, or Image
3. **Choose codec** - H.264, H.265, AV1, AAC, WebP, etc.
4. **Select encoder** - App shows compatible encoders (hardware included)
5. **Configure quality** - Adjust parameters based on media type
6. **Apply settings** - Smart button that adapts to your selection
7. **Start encoding** - Automatic notification when finished

### Video Encoding Modes
- **Quality Mode (CRF)** - For best quality (default)
- **Bitrate Mode (CBR/VBR)** - For precise file size control
- **Multi-pass** - Multi-pass encoding for optimized quality

### Image Specialized Controls
- **Longest side** - 5200px, 4096px, 3840px, etc. + custom
- **Megapixels** - 50MP, 25MP, 16MP, etc. + custom
- **Aspect ratio preservation** - Smart resizing

### Audio Bitrate Selection
- **Lossy codecs** - Optimized bitrate selectors per codec
  - AAC: 96k to 320k
  - MP3: 96k to 320k
  - Opus: 64k to 256k
- **Lossless codecs** - Quality levels (FLAC, ALAC, etc.)

## Advanced Features

### Smart Interface
- **Dynamic media inspector** - Shows relevant information based on file type
- **Adaptive UI** - Controls change based on selected media type
- **Completion notifications** - Encoding summary with system sound
- **Folder watching** - Automatic addition of new files

### Professional Tools
- **Hardware acceleration** - Support for NVIDIA, Intel, AMD, Apple
- **Advanced filters** - Color correction, cropping, rotation, sharpening
- **Audio configuration** - Multi-track management, selective re-encoding
- **EXIF editor** - Edit image metadata directly from the interface
- **Batch operations** - Bulk parameter modification
- **Preset system** - Save and load your favorite configurations

### Monitoring and Logs
- **Real-time progress** - Detailed progress bars
- **Log viewer** - Real-time FFmpeg logs per job
- **Job control** - Individual pause, resume, cancel
- **Worker pool** - Configurable concurrent encoding

## Smart Features

- **No output folder required** - Files saved next to originals with suffix
- **Context-aware buttons** - "Apply" adapts to your selection automatically
- **Automatic detection** - Media type recognition
- **Drag and drop** - Native support for files and folders
- **Folder structure** - Optional preservation of directory tree

## Supported Codecs

### Video
- **H.264/AVC** - libx264, hardware (NVENC, QSV, AMF, VideoToolbox)
- **H.265/HEVC** - libx265, hardware (NVENC, QSV, AMF, VideoToolbox)
- **AV1** - SVT-AV1, libaom-av1, hardware (NVENC, QSV)
- **VP9/VP8** - libvpx
- **ProRes, DNxHD** - For professional workflows

### Audio
- **Lossy** - AAC, MP3, Opus, Vorbis (with bitrate selectors)
- **Lossless** - FLAC, ALAC (Apple Lossless), PCM
- **Professional** - AC3, DTS

### Images
- **Modern** - WebP, JPEG XL, AVIF 
- **Classic** - JPEG, PNG, TIFF, BMP

This application is designed to be simple, powerful, and just work. 