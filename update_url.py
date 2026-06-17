import sqlite3
import requests
import json
import os

# Load config
try:
    import tomllib
except ImportError:
    import tomli as tomllib

with open("config/settings.toml", "rb") as f:
    cfg = tomllib.load(f)

import sys

# Get new URL from CLI argument, or fallback to default
if len(sys.argv) >= 2:
    new_url = sys.argv[1]
else:
    new_url = "https://desktops-college-timothy-treo.trycloudflare.com"

if not new_url.startswith("http://") and not new_url.startswith("https://"):
    print("Error: URL must start with http:// or https://")
    sys.exit(1)

# 1. Update SQLite DB
print("Updating local SQLite DB...")
db_path = cfg["database"]["path"]
conn = sqlite3.connect(db_path)
conn.execute(
    "INSERT INTO system_state (config_key, config_value, last_updated) VALUES (?, ?, datetime('now')) "
    "ON CONFLICT(config_key) DO UPDATE SET config_value = excluded.config_value, last_updated = excluded.last_updated",
    ("kaggle_ngrok_url", new_url)
)
conn.commit()
conn.close()
print("Local SQLite DB updated successfully.")

# 2. Update Supabase
print("Updating Supabase...")
sb_url = cfg["kaggle"]["supabase_url"]
sb_key = cfg["kaggle"]["supabase_key"]
headers = {
    "apikey": sb_key,
    "Authorization": f"Bearer {sb_key}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

payload = {
    "key": "kaggle_ngrok_url",
    "value": new_url
}

# Try PATCH first
try:
    patch_url = f"{sb_url}/rest/v1/quantelos_config?key=eq.kaggle_ngrok_url"
    r = requests.patch(patch_url, headers=headers, json={"value": new_url}, timeout=10)
    print(f"PATCH Status: {r.status_code}")
    if r.status_code >= 400:
        print(f"PATCH failed: {r.text}. Trying POST insert/upsert...")
        # Try POST upsert
        upsert_headers = headers.copy()
        upsert_headers["Prefer"] = "resolution=merge-duplicates"
        r2 = requests.post(f"{sb_url}/rest/v1/quantelos_config", headers=upsert_headers, json=payload, timeout=10)
        print(f"POST Upsert Status: {r2.status_code}, response: {r2.text}")
except Exception as e:
    print(f"Error updating Supabase: {e}")

print("Done updating URLs.")
