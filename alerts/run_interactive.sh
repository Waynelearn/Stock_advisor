#!/bin/bash
# Run the interactive Telegram bot as a persistent background process.
# Usage: ./run_interactive.sh
# Or set up as systemd service (see mu-bot-interactive.service)

cd /home/wayne/website/mu_advisor
exec /home/wayne/miniconda3/bin/conda run -n mu_advisor python3 -m alerts.bot_interactive
