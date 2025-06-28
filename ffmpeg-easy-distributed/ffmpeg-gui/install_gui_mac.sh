#!/bin/bash

# Installer for FFmpeg Easy GUI on macOS
# Creates a user-local installation.

set -e # Exit immediately if a command exits with a non-zero status.

# --- Configuration ---
APP_NAME="FFmpeg Easy GUI"
INSTALL_BASE_DIR="$HOME/.local/share"
LAUNCHER_BASE_DIR="$HOME/.local/bin"

APP_ID="ffmpeg-easy-gui" # Used for directory names

INSTALL_DIR="$INSTALL_BASE_DIR/$APP_ID"
APP_CONTENT_DIR="$INSTALL_DIR/app" # Where actual app files go
VENV_DIR="$INSTALL_DIR/venv"
LAUNCHER_DIR="$LAUNCHER_BASE_DIR"
LAUNCHER_NAME="$APP_ID"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Script Directory ---
# Absolute path to the directory where this script is located.
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# --- Helper Functions ---
print_header() {
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}  $APP_NAME Installer for macOS${NC}"
    echo -e "${BLUE}================================${NC}"
    echo ""
}

print_step() {
    echo -e "${GREEN}[STEP]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    exit 1
}

# --- Main Functions ---

check_command() {
    if ! command -v "$1" &> /dev/null; then
        # Use provided error message or a default one
        local error_message=${2:-"$1 command not found. Please install it first."}
        print_error "$error_message"
    fi
}

install_system_dependencies() {
    print_step "Installing system dependencies with Homebrew"

    check_command "brew" "Homebrew not found. Please install it from https://brew.sh"

    REQUIRED_PACKAGES=("python" "python-tk" "ffmpeg")
    PACKAGES_TO_INSTALL=()

    print_info "Checking for required Homebrew packages..."
    for pkg in "${REQUIRED_PACKAGES[@]}"; do
        if ! brew list "$pkg" &> /dev/null; then
            print_info "$pkg is not installed. Adding to installation list."
            PACKAGES_TO_INSTALL+=("$pkg")
        else
            print_info "$pkg is already installed."
        fi
    done

    if [ ${#PACKAGES_TO_INSTALL[@]} -ne 0 ]; then
        print_info "Updating Homebrew package lists..."
        if brew update; then
            print_info "Installing missing packages: ${PACKAGES_TO_INSTALL[*]}"
            if ! brew install "${PACKAGES_TO_INSTALL[@]}"; then
                print_error "Failed to install Homebrew dependencies. Please try again."
            fi
            print_info "System dependencies installed successfully."
        else
            print_error "Failed to update Homebrew. Please check your internet connection and Homebrew configuration."
        fi
    else
        print_info "All system dependencies are already satisfied."
    fi
}

create_directories() {
    print_step "Creating installation directories"
    mkdir -p "$APP_CONTENT_DIR"
    mkdir -p "$VENV_DIR"
    mkdir -p "$LAUNCHER_DIR"
    print_info "Created directory: $INSTALL_DIR"
    print_info "Created directory for app content: $APP_CONTENT_DIR"
    print_info "Created directory for venv: $VENV_DIR"
    print_info "Created directory for launcher: $LAUNCHER_DIR"
}

copy_application_files() {
    print_step "Copying application files to $APP_CONTENT_DIR"

    # Ensure the target application content directory exists
    mkdir -p "$APP_CONTENT_DIR"

    # List of items to copy from SCRIPT_DIR
    # This assumes the script is in the root of the GUI application source folder
    ITEMS_TO_COPY=(
        "core"
        "gui"
        "shared"
        "main.py"
        "requirements.txt"
        "LICENSE" # Typically good to include
        "README.md" # For reference
    )

    # Optional: copy settings.json if it exists in the source, as a default
    if [ -f "$SCRIPT_DIR/settings.json" ]; then
        ITEMS_TO_COPY+=("settings.json")
        print_info "Including 'settings.json' from source."
    else
        print_info "No 'settings.json' found in source, skipping."
    fi

    # Optional: copy project_map.md if it exists in the source
    if [ -f "$SCRIPT_DIR/project_map.md" ]; then
        ITEMS_TO_COPY+=("project_map.md")
        print_info "Including 'project_map.md' from source."
    else
        print_info "No 'project_map.md' found in source, skipping."
    fi

    for item in "${ITEMS_TO_COPY[@]}"; do
        src_path="$SCRIPT_DIR/$item"
        if [ -e "$src_path" ]; then # Check if file or directory exists
            print_info "Copying $item..."
            # Using -R on macOS is a good practice, similar to -r but handles symlinks better.
            if ! cp -R "$src_path" "$APP_CONTENT_DIR/"; then
                print_error "Failed to copy $item. Aborting."
            fi
        else
            print_warning "Source item $src_path not found. Skipping."
        fi
    done

    print_info "Application files copied successfully."
}

setup_virtual_environment() {
    print_step "Setting up Python virtual environment in $VENV_DIR"

    if ! python3 -m venv "$VENV_DIR"; then
        print_error "Failed to create virtual environment at $VENV_DIR. Aborting."
    fi
    print_info "Virtual environment created."

    print_info "Upgrading pip..."
    if ! "$VENV_DIR/bin/pip" install --upgrade pip; then
        print_error "Failed to upgrade pip in virtual environment. Aborting."
    fi

    REQUIREMENTS_FILE="$APP_CONTENT_DIR/requirements.txt"
    if [ -f "$REQUIREMENTS_FILE" ]; then
        print_info "Installing Python dependencies from $REQUIREMENTS_FILE..."
        if ! "$VENV_DIR/bin/pip" install -r "$REQUIREMENTS_FILE"; then
            print_error "Failed to install Python dependencies from $REQUIREMENTS_FILE. Aborting."
        fi
        print_info "Python dependencies installed successfully."
    else
        print_warning "requirements.txt not found at $REQUIREMENTS_FILE. Skipping dependency installation."
    fi
}

create_launcher_script() {
    print_step "Creating launcher script at $LAUNCHER_DIR/$LAUNCHER_NAME"

    # Using escaped variables for items that should be expanded when the launcher runs,
    # not when this installer script runs.
    # INSTALL_DIR and VENV_DIR are expanded now to bake their values into the launcher.
    cat > "$LAUNCHER_DIR/$LAUNCHER_NAME" << EOF
#!/bin/bash

# Launcher for $APP_NAME
# Installation directory: $INSTALL_DIR
# App content directory: $APP_CONTENT_DIR
# Venv directory: $VENV_DIR

# Navigate to the application's content directory
cd "$APP_CONTENT_DIR" || {
    echo "Error: Could not navigate to $APP_CONTENT_DIR" >&2
    exit 1
}

# Run the main Python script using the virtual environment's Python
"$VENV_DIR/bin/python" main.py "\$@"

# Exit with the status of the Python script
exit \$?
EOF

    if ! chmod +x "$LAUNCHER_DIR/$LAUNCHER_NAME"; then
        print_error "Failed to make launcher script executable at $LAUNCHER_DIR/$LAUNCHER_NAME. Aborting."
    fi

    print_info "Launcher script created successfully."
}

show_completion_message() {
    print_info "--------------------------------------------------"
    print_info "$APP_NAME installation complete!"
    print_info "--------------------------------------------------"
    echo -e "You can now run the application by typing: ${GREEN}$LAUNCHER_NAME${NC}"
    if [[ ":$PATH:" != *":$LAUNCHER_DIR:"* ]]; then
        print_warning "Your PATH does not seem to include $LAUNCHER_DIR."
        print_warning "Attempting to add it to your ~/.zshrc file..."
        
        # Add export to .zshrc if not already there
        ZSHRC_PATH="$HOME/.zshrc"
        EXPORT_LINE="export PATH=\"\$HOME/.local/bin:\$PATH\""
        
        if [ -f "$ZSHRC_PATH" ]; then
            if ! grep -q "export PATH=\"\$HOME/.local/bin:\$PATH\"" "$ZSHRC_PATH" && ! grep -q "export PATH=\"$LAUNCHER_DIR:\$PATH\"" "$ZSHRC_PATH"; then
                echo -e "\n# Add FFmpeg Easy GUI launcher to PATH\n$EXPORT_LINE" >> "$ZSHRC_PATH"
                print_info "Successfully added PATH to $ZSHRC_PATH."
                print_warning "Please restart your terminal session or run 'source ~/.zshrc' to apply changes."
            else
                print_info "$LAUNCHER_DIR is already in your PATH in $ZSHRC_PATH."
            fi
        else
            echo -e "\n# Add FFmpeg Easy GUI launcher to PATH\n$EXPORT_LINE" > "$ZSHRC_PATH"
            print_info "Created $ZSHRC_PATH and added PATH."
            print_warning "Please restart your terminal session or run 'source ~/.zshrc' to apply changes."
        fi
        
        echo -e "Alternatively, run with the full path:"
        echo -e "  ${GREEN}$LAUNCHER_DIR/$LAUNCHER_NAME${NC}"
    fi
    echo ""
}

# --- Main Execution ---
main() {
    print_header
    
    install_system_dependencies # Will use Homebrew
    
    check_command "python3"
    check_command "pip3"

    create_directories
    copy_application_files
    setup_virtual_environment
    create_launcher_script

    show_completion_message
}

# Run main
main "$@"

print_info "FFmpeg Easy GUI installation script finished." 