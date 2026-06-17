#!/usr/bin/env bash
# =============================================================================
# Update Kaggle Ngrok URL (Local & ASUS Remote)
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -z "$1" ]; then
    echo "Penggunaan: ./update_kaggle_url.sh <URL_NGROK_KAGGLE_BARU>"
    echo "Contoh: ./update_kaggle_url.sh https://contoh-link.trycloudflare.com"
    exit 1
fi

NEW_URL="$1"

echo "1. Memperbarui database di laptop HP lokal..."
python3 -c "import sqlite3; conn = sqlite3.connect('$PROJECT_DIR/data/quantelos.db'); conn.execute('UPDATE system_state SET config_value = \"$NEW_URL\" WHERE config_key = \"kaggle_ngrok_url\"'); conn.commit()"
if [ $? -eq 0 ]; then
    echo "✓ Lokal (HP) berhasil diperbarui."
else
    echo "✗ Gagal memperbarui lokal (HP)."
fi

echo "2. Memperbarui database di laptop ASUS secara remote..."
ssh asus "python3 -c \"import sqlite3; conn = sqlite3.connect('/home/titiw/Downloads/forexbackend/quantelos/data/quantelos.db'); conn.execute('UPDATE system_state SET config_value = \\\"$NEW_URL\\\" WHERE config_key = \\\"kaggle_ngrok_url\\\"'); conn.commit()\""
if [ $? -eq 0 ]; then
    echo "✓ Remote (ASUS) berhasil diperbarui."
else
    echo "✗ Gagal memperbarui remote (ASUS)."
fi

echo "============================================================================="
echo "Selesai! URL Kaggle berhasil disinkronkan ke semua sistem."
echo "Quantelos AI Trader akan otomatis mendeteksi URL baru ini dalam hitungan detik."
