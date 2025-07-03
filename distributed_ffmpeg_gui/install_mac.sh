#!/bin/bash
# macOS GUI installer

# Ensure the script runs from its own directory
cd "$(dirname "$0")" || exit

# --- Check for Homebrew ---
if ! command -v brew &> /dev/null; then
    echo "Homebrew not found. Please install it by following the instructions at https://brew.sh/"
    exit 1
fi

echo "--- Installing dependencies (requires Homebrew) ---"
brew install python-tk ffmpeg

echo "--- Creating Python virtual environment ---"
python3 -m venv venv

echo "--- Activating virtual environment and installing packages ---"
source venv/bin/activate
# Use the existing requirements.txt file
pip install -r requirements.txt
deactivate

echo "--- Creating application launcher ---"
# Create app launcher that works regardless of where it's clicked from
LAUNCHER_CONTENT="#!/bin/bash
# Get the absolute path of the directory containing this script
DIR=\$(cd \"\$(dirname \"\${BASH_SOURCE[0]}\")\" &> /dev/null && pwd)
# Activate the virtual environment and launch the Python GUI application
source \"\$DIR/venv/bin/activate\"
python \"\$DIR/main.py\""

echo "$LAUNCHER_CONTENT" > launch_gui.command
chmod +x launch_gui.command

echo "--- Installation Complete ---"
echo "To run the application, double-click 'launch_gui.command' or run it from the terminal." 