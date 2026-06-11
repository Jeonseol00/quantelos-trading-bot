#!/usr/bin/env bash
# =============================================================================
# Quantelos AI Trader — Start Services Script
# =============================================================================
cd /home/titiw/Downloads/Forex/quantelos

# Kill any existing processes
pkill -f quantelos_executor || true
pkill -f main.py || true
pkill -f dashboard.py || true

echo "Waiting for old processes to terminate..."
while pgrep -f quantelos_executor >/dev/null || pgrep -f "python_node/main.py" >/dev/null || pgrep -f "python_node/dashboard.py" >/dev/null; do
    sleep 0.5
done
echo "Old processes terminated."

# Create logs directory
mkdir -p logs

# Start C++ executor
echo "Starting C++ Executor..."
nohup ./cpp_engine/build/quantelos_executor > logs/executor.log 2>&1 < /dev/null &

# Start Python Orchestrator
echo "Starting Python Orchestrator..."
nohup .venv/bin/python -u python_node/main.py > logs/main.log 2>&1 < /dev/null &

# Start Dashboard
echo "Starting Dashboard..."
nohup .venv/bin/python -u python_node/dashboard.py > logs/dashboard.log 2>&1 < /dev/null &

echo "All services started."
