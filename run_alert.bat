@echo off
cd /d "%~dp0"

echo [SYSTEM] Checking and installing required dependencies...
python -m pip install -q requests google-genai python-dotenv

echo [SYSTEM] Dependencies verified. Starting pub crawl...
echo.

python crawl.py

echo.
pause