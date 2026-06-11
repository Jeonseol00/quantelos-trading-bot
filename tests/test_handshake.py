#!/usr/bin/env python3
# =============================================================================
# Quantelos AI Trader — Credential Handshake Verification
# =============================================================================
# Validates connectivity to ALL external endpoints before system launch.
# Run: python tests/test_handshake.py
# =============================================================================
import sys
import json
import requests

# ─── Load Config ──────────────────────────────────────────────────────────────
try:
    import tomllib
except ImportError:
    import tomli as tomllib

with open("config/settings.toml", "rb") as f:
    cfg = tomllib.load(f)

passed = 0
failed = 0
total = 4

def test(name, func):
    global passed, failed
    print(f"\n  [{passed+failed+1}/{total}] {name}...", flush=True)
    try:
        func()
        passed += 1
        print(f"       ✓ PASS")
    except Exception as e:
        failed += 1
        print(f"       ✗ FAIL: {e}")

# ─── Test 1: OANDA v20 API ───────────────────────────────────────────────────
def test_oanda():
    url = f"{cfg['oanda']['api_url']}/v3/accounts/{cfg['oanda']['account_id']}/summary"
    headers = {
        "Authorization": f"Bearer {cfg['oanda']['api_token']}",
        "Content-Type": "application/json",
    }
    r = requests.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    acct = data["account"]
    balance = acct.get("balance", "N/A")
    currency = acct.get("currency", "N/A")
    print(f"       Account : {cfg['oanda']['account_id']}")
    print(f"       Balance : {balance} {currency}")
    print(f"       Region  : OANDA Asia Pacific (fxPractice)")

# ─── Test 2: Discord Webhook ─────────────────────────────────────────────────
def test_discord():
    payload = {
        "embeds": [{
            "title": "🟢 Quantelos Handshake",
            "description": "Credential verification **PASSED**.\nAll systems nominal.",
            "color": 3066993,
            "footer": {"text": "Quantelos AI Trader v1.0 — Handshake Test"},
        }]
    }
    r = requests.post(cfg["notifications"]["discord_webhook"], json=payload, timeout=10)
    r.raise_for_status()
    print(f"       Webhook : Connected (message sent)")

# ─── Test 3: Telegram Bot API ────────────────────────────────────────────────
def test_telegram():
    token = cfg["notifications"]["telegram_bot_token"]
    chat_id = cfg["notifications"]["telegram_chat_id"]
    text = "🟢 *Quantelos Handshake*\n\nCredential verification *PASSED*.\nAll systems nominal."
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    assert data.get("ok"), f"Telegram API error: {data}"
    print(f"       Bot     : Connected (message sent to chat {chat_id})")

# ─── Test 4: Supabase Connection ──────────────────────────────────────────────
def test_supabase():
    url = cfg["kaggle"]["supabase_url"]
    key = cfg["kaggle"]["supabase_key"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    # Query the actual configuration table
    r = requests.get(f"{url}/rest/v1/quantelos_config", headers=headers, timeout=10)
    print(f"       URL     : {url}")
    print(f"       Status  : {r.status_code}")
    if r.status_code == 404:
        raise Exception("Table 'quantelos_config' not found. Please run the SQL setup script in your Supabase SQL Editor.")
    elif r.status_code >= 400:
        raise Exception(f"Supabase returned HTTP {r.status_code}: {r.text}")
    else:
        print(f"       Table   : 'quantelos_config' verified (accessible)")

# ─── Run All Tests ────────────────────────────────────────────────────────────
print("═" * 55)
print("  Quantelos — Credential Handshake Verification")
print("═" * 55)

test("OANDA v20 API (Asia Pacific Demo)", test_oanda)
test("Discord Webhook", test_discord)
test("Telegram Bot API", test_telegram)
test("Supabase Cloud DB", test_supabase)

print("\n" + "═" * 55)
print(f"  Results: {passed} passed, {failed} failed out of {total}")
print("═" * 55)

sys.exit(1 if failed > 0 else 0)
