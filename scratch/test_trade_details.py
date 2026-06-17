#!/usr/bin/env python3
import requests
import json

try:
    import tomllib
except ImportError:
    import tomli as tomllib

with open("config/settings.toml", "rb") as f:
    cfg = tomllib.load(f)

url = f"{cfg['oanda']['api_url']}/v3/accounts/{cfg['oanda']['account_id']}/trades"
headers = {
    "Authorization": f"Bearer {cfg['oanda']['api_token']}",
    "Content-Type": "application/json"
}

# Try fetching trades using state=CLOSED
params = {
    "state": "CLOSED",
    "count": "10"
}

print("Fetching trade 14 details via list trades endpoint...")
r = requests.get(url, headers=headers, params=params, verify=False)
print(f"HTTP Status: {r.status_code}")
if r.status_code == 200:
    print("Response JSON:")
    print(json.dumps(r.json(), indent=2))
else:
    print(f"Error content: {r.text}")
