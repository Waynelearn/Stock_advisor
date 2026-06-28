#!/bin/bash
# Launchd-friendly entrypoint for the interactive bot.
# Resolves the venv python explicitly to avoid macOS launchd symlink issues.

cd /Users/wayne_linn/Desktop/ai_projects/Stock_advisor
exec .venv/bin/python3.14 -m alerts.bot_interactive
