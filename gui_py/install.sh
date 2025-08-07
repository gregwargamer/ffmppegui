#!/usr/bin/env bash
set -euo pipefail

#this part do that
#détection OS et chemins
OS_NAME="$(uname -s)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PY_DIR="$PROJECT_ROOT/gui_py"
VENV_DIR="$PY_DIR/.venv"

#this other part do that
#pré-requis python3 et pip
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found" >&2; exit 1
fi
if ! command -v pip3 >/dev/null 2>&1; then
  echo "pip3 not found" >&2; exit 1
fi

echo "Creating venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip wheel setuptools

echo "Installing Python GUI requirements"
pip install -r "$PY_DIR/requirements.txt"

#this part do that
#création wrapper d'exécution
RUNNER="$PY_DIR/run_gui.sh"
cat > "$RUNNER" << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/.venv/bin/activate"
exec python "$SCRIPT_DIR/main.py"
EOF
chmod +x "$RUNNER"

echo "Python GUI installed. Launch with: $RUNNER"

