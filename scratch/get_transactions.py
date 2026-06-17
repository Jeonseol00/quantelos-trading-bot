#!/usr/bin/env python3
import requests
import json

try:
    import tomllib
except ImportError:
    import tomli as tomllib

with open("config/settings.toml", "rb") as f:
    cfg = tomllib.load(f)

url = f"{cfg['oanda']['api_url']}/v3/accounts/{cfg['oanda']['account_id']}/transactions"
headers = {
    "Authorization": f"Bearer {cfg['oanda']['api_token']}",
    "Content-Type": "application/json"
}

# Fetch the last 50 transactions
params = {
    "pageSize": "50"
}

print("Fetching transaction history...")
r = requests.get(url, headers=headers, params=params, verify=False)
print(f"HTTP Status: {r.status_code}")
if r.status_code == 200:
    print("Response JSON:")
    print(json.dumps(r.json(), indent=2))
else:
    print(f"Error content: {r.text}")
