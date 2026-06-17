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

# 1. Get the last transaction ID
url = f"{api_url}/v3/accounts/{account_id}/transactions"
r = requests.get(url, headers=headers, timeout=10, verify=False)
data = r.json()
last_id = int(data.get("lastTransactionID", 0))

# 2. Fetch the last 100 transactions using idrange
from_id = max(1, last_id - 99)
range_url = f"{api_url}/v3/accounts/{account_id}/transactions/idrange"
params = {"from": str(from_id), "to": str(last_id)}
r_range = requests.get(range_url, headers=headers, params=params, timeout=10, verify=False)
range_data = r_range.json()

transactions = range_data.get("transactions", [])
print(f"Loaded {len(transactions)} transactions (from ID {from_id} to {last_id}).\n")

print(f"{'Tx ID':<6} | {'Type':<12} | {'Time':<24} | {'Details'}")
print("-" * 80)
for tx in transactions:
    tx_id = tx.get("id")
    tx_type = tx.get("type")
    time_str = tx.get("time", "")[:23]
    
    details = ""
    if tx_type == "ORDER_FILL":
        reason = tx.get("reason", "")
        instrument = tx.get("instrument", "")
        units = tx.get("units", "")
        price = tx.get("price", "")
        pl = tx.get("pl", "0.0")
        
        details = f"{reason} {instrument} | Units: {units} @ {price} | PL: {pl}"
        
        # Check for closed trades details
        closed = tx.get("tradesClosed", [])
        if closed:
            details += f" | Closed: " + ", ".join([f"Trade {c.get('tradeID')} (PL: {c.get('realizedPL')})" for c in closed])
            
    elif tx_type == "MARKET_ORDER":
        instrument = tx.get("instrument", "")
        units = tx.get("units", "")
        details = f"MARKET_ORDER {instrument} | Units: {units}"
    elif tx_type == "STOP_LOSS_ORDER":
        trade_id = tx.get("tradeID", "")
        price = tx.get("price", "")
        details = f"SL_ORDER for Trade {trade_id} @ {price}"
    elif tx_type == "TAKE_PROFIT_ORDER":
        trade_id = tx.get("tradeID", "")
        price = tx.get("price", "")
        details = f"TP_ORDER for Trade {trade_id} @ {price}"
    else:
        details = f"{tx_type}"
        
    print(f"{tx_id:<6} | {tx_type:<12} | {time_str:<24} | {details}")
