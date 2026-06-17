#!/usr/bin/env bash
# =============================================================================
# Stop services on ASUS laptop from HP laptop
# =============================================================================
echo "Menghentikan layanan di ASUS..."
ssh asus "cd /home/titiw/Downloads/forexbackend/quantelos && ./stop_services.sh"
echo "✓ Semua layanan ASUS berhasil dimatikan."
