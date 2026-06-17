#!/usr/bin/env bash
# =============================================================================
# Start services on ASUS laptop from HP laptop
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "1. Menghentikan proses lokal di HP untuk menghindari konflik API..."
"$PROJECT_DIR/stop_services.sh"

echo "2. Menyinkronkan file kode ke ASUS laptop..."
rsync -avz --exclude '.venv' --exclude '.git' --exclude 'logs' --exclude 'data/quantelos.db' --exclude 'cpp_engine/build' --exclude '__pycache__' --exclude '*/__pycache__' "$PROJECT_DIR/" asus:/home/titiw/Downloads/forexbackend/quantelos/

echo "3. Menjalankan layanan di ASUS..."
ssh asus "cd /home/titiw/Downloads/forexbackend/quantelos && ./start_services.sh"

echo "✓ Semua layanan ASUS berhasil dinyalakan!"
