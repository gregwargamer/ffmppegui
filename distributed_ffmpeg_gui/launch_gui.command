#!/bin/bash
# Get the absolute path of the directory where this script is located.
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# Change to that directory. This is the most important step.
cd "$DIR" || exit

# Now that we are in the correct directory, source the venv and run Python.
source venv/bin/activate
python main.py
