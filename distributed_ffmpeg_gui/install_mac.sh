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
# Use a 'here document' (cat <<'EOF') to create the launcher script.
# This is the most robust way to write a multi-line script to a file,
# as it avoids all issues with character escaping.
cat <<'EOF' > launch_gui.command
#!/bin/bash
# Get the absolute path of the directory where this script is located.
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# Change to that directory. This is the most important step.
cd "$DIR" || exit

# Now that we are in the correct directory, source the venv and run Python.
source venv/bin/activate
python main.py
EOF

chmod +x launch_gui.command

echo "--- Installation Complete ---"
echo "To run the application, double-click 'launch_gui.command' or run it from the terminal." 