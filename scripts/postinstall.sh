#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_DIR="$(dirname "$SCRIPT_DIR")/python"
VENV_DIR="$PYTHON_DIR/.venv"
MARKER="$VENV_DIR/.railclaw-installed"

if [ ! -d "$PYTHON_DIR" ]; then
  echo "[railclaw-pipeline] Python directory not found at $PYTHON_DIR — skipping Python setup."
  exit 0
fi

if [ -f "$MARKER" ]; then
  echo "[railclaw-pipeline] Python deps already installed — skipping. Delete $MARKER to force reinstall."
  exit 0
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "[railclaw-pipeline] Creating Python virtual environment..."
  if ! python3 -m venv "$VENV_DIR" 2>&1; then
    echo "[railclaw-pipeline] WARNING: Failed to create venv. Python bridge will not be available." >&2
    exit 0
  fi
fi

echo "[railclaw-pipeline] Installing Python package in development mode..."
if ! "$VENV_DIR/bin/pip" install -e "$PYTHON_DIR" 2>&1; then
  echo "[railclaw-pipeline] WARNING: pip install failed. Trying with --break-system-packages..." >&2
  if ! "$VENV_DIR/bin/pip" install --break-system-packages -e "$PYTHON_DIR" 2>&1; then
    echo "[railclaw-pipeline] WARNING: Python package installation failed. Python bridge will not be available." >&2
    exit 0
  fi
fi

touch "$MARKER"
echo "[railclaw-pipeline] Python package installed successfully."
