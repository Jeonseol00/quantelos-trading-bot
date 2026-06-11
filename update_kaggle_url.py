#!/usr/bin/env python3
import sys
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data/quantelos.db"

def update_url(new_url: str):
    if not new_url.startswith("http://") and not new_url.startswith("https://"):
        print("Error: URL must start with http:// or https://")
        sys.exit(1)
        
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO system_state (config_key, config_value, last_updated)
        VALUES ('kaggle_ngrok_url', ?, datetime('now'))
        ON CONFLICT(config_key) DO UPDATE SET
            config_value = excluded.config_value,
            last_updated = excluded.last_updated
    """, (new_url,))
    conn.commit()
    conn.close()
    print(f"[SUCCESS] Kaggle URL updated locally to: {new_url}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python update_kaggle_url.py <new_url>")
        sys.exit(1)
    update_url(sys.argv[1])
