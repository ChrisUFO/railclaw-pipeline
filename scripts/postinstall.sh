#!/bin/bash

# Postinstall script: Install Python package in development mode

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_DIR="$(dirname "$SCRIPT_DIR")/python"

if [ -d "$PYTHON_DIR" ]; then
    echo "Installing Python package in development mode..."
    cd "$PYTHON_DIR"
    pip install -e .
    echo "Python package installed successfully."
else
    echo "Python directory not found at $PYTHON_DIR"
    exit 1
fi
