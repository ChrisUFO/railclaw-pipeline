#!/bin/bash

# Postinstall script: Install Python package in development mode using venv

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_DIR="$(dirname "$SCRIPT_DIR")/python"
VENV_DIR="$PYTHON_DIR/.venv"

if [ -d "$PYTHON_DIR" ]; then
  if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
  fi

  echo "Installing Python package in development mode..."
  "$VENV_DIR/bin/pip" install -e .
  echo "Python package installed successfully."
else
  echo "Python directory not found at $PYTHON_DIR"
  exit 1
fi
