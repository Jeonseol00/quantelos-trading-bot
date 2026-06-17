#!/usr/bin/env python3
import requests
import json
try:
    import tomllib
except ImportError:
    import tomli as tomllib

with open("config/settings.toml", "rb") as f:
    cfg = tomllib.load(f)

api_url = cfg['oanda']['api_url']
account_id = cfg['oanda']['account_id']
headers = {
    "Authorization": f"Bearer {cfg['oanda']['api_token']}",
    "Content-Type": "application/json"
}

url = f"{api_url}/v3/accounts/{account_id}/openTrades"
r = requests.get(url, headers=headers, verify=False)
print(f"HTTP Status: {r.status_code}")
if r.status_code == 200:
    print(json.dumps(r.json(), indent=2))
else:
    print(r.text)
