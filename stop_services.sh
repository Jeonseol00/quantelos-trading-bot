#!/usr/bin/env bash
# =============================================================================
# Quantelos AI Trader — Stop Services Script
# =============================================================================
# Set current working directory dynamically
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Terminating all Quantelos services..."
pkill -f quantelos_executor || true
pkill -f "python_node/main.py" || true
pkill -f "python_node/dashboard.py" || true

echo "Waiting for processes to exit..."
while pgrep -f quantelos_executor >/dev/null || pgrep -f "python_node/main.py" >/dev/null || pgrep -f "python_node/dashboard.py" >/dev/null; do
    sleep 0.5
done

echo "All Quantelos services stopped successfully."
