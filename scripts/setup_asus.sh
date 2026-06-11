#!/usr/bin/env bash
# =============================================================================
# Quantelos AI Trader — ASUS Headless Server Setup Script
# =============================================================================
# Target: ASUS Laptop (Intel i5-7200U, 4GB RAM, Ubuntu 24.04 LTS Headless)
# Run as: sudo bash scripts/setup_asus.sh
# =============================================================================
set -euo pipefail

echo "═══════════════════════════════════════════════"
echo "  Quantelos — ASUS Headless Server Setup"
echo "═══════════════════════════════════════════════"

# ─── System Packages ──────────────────────────────────────────────────────────
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    build-essential cmake \
    python3 python3-pip python3-venv \
    libzmq3-dev libsqlite3-dev sqlite3 \
    chrony curl git htop

# ─── NTP Time Sync (BRD Section 5, Gap 5) ────────────────────────────────────
echo "[2/7] Configuring NTP time synchronization..."
systemctl enable chrony
systemctl start chrony
chronyc makestep
echo "  → Time synchronized."

# ─── Headless Mode (GUI Enabled) ─────────────────────────────────────────────
echo "[3/7] Keeping default graphical target (GUI active)..."
# systemctl set-default multi-user.target
echo "  → GUI remains enabled."

# ─── Python Virtual Environment ──────────────────────────────────────────────
echo "[4/7] Setting up Python virtual environment..."
PROJ_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  → Python venv ready."

# ─── Build C++ Execution Engine ───────────────────────────────────────────────
echo "[5/7] Building C++ Execution Engine..."
cd cpp_engine
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . -j$(nproc)
echo "  → C++ binary compiled."

# ─── Run TDD Suite ────────────────────────────────────────────────────────────
echo "[6/7] Running TDD test suite..."
cd ..
g++ -std=c++17 -I./include tests/tdd_suite.cpp -o tdd_runner
./tdd_runner
echo "  → All TDD tests passed."

# ─── Initialize Database ─────────────────────────────────────────────────────
echo "[7/7] Initializing SQLite3 database..."
cd "$PROJ_DIR"
mkdir -p data logs
sqlite3 data/quantelos.db < database/schema.sql
echo "  → Database initialized with WAL mode."

echo ""
echo "═══════════════════════════════════════════════"
echo "  ✓ ASUS Headless Server Setup Complete"
echo "═══════════════════════════════════════════════"
echo "  Next: Configure config/settings.toml with"
echo "  your OANDA API credentials and run:"
echo "    source .venv/bin/activate"
echo "    cd python_node && python main.py"
echo "═══════════════════════════════════════════════"
