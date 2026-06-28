#!/bin/bash
# Run the interactive Telegram bot using the local venv.
# Usage: ./run_interactive.sh

set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "ERROR: .venv not found. Create it with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

exec .venv/bin/python -m alerts.bot_interactive
