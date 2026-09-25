#!/usr/bin/env bash
# Pawan Flipkart Label Cropper Automatically - macOS / Linux launcher
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: creating virtual environment and installing packages..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi
echo "Open http://127.0.0.1:8000  (Ctrl+C to stop)"
( sleep 2; (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8000) || (command -v open >/dev/null && open http://127.0.0.1:8000) || true ) &
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
