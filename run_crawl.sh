#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null; then
    echo "[ERROR] python3 not found. Install Python 3 first."
    exit 1
fi

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "[SYSTEM] Creating virtual environment in .venv..."
    python3 -m venv --clear .venv || {
        echo "[ERROR] Could not create .venv. On Debian/Ubuntu, run: sudo apt install python3-venv"
        exit 1
    }
fi

echo "[SYSTEM] Checking and installing required dependencies..."
.venv/bin/python -m pip install -q requests google-genai python-dotenv || exit 1

echo "[SYSTEM] Dependencies verified. Starting pub crawl..."
echo

.venv/bin/python crawl.py
