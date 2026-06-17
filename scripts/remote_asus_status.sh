#!/usr/bin/env bash
# =============================================================================
# Check services status on ASUS laptop from HP laptop
# =============================================================================
echo "═══════════════════════════════════════════════"
echo "  Status Layanan Quantelos di ASUS Laptop"
echo "═══════════════════════════════════════════════"
ssh asus "ps aux | grep -iE 'quantelos_executor|main.py|dashboard.py' | grep -v grep" || echo "Tidak ada layanan yang sedang berjalan."
echo "═══════════════════════════════════════════════"
