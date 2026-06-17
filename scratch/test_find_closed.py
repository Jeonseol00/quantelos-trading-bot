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

def find_closed_trade(trade_id):
    try:
        # 1. Get the last transaction ID
        url = f"{api_url}/v3/accounts/{account_id}/transactions"
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        r.raise_for_status()
        data = r.json()
        last_id_str = data.get("lastTransactionID")
        if not last_id_str:
            return {}
        
        last_id = int(last_id_str)
        from_id = max(1, last_id - 99)
        
        # 2. Fetch transaction details in that range
        range_url = f"{api_url}/v3/accounts/{account_id}/transactions/idrange"
        params = {
            "from": str(from_id),
            "to": str(last_id)
        }
        r_range = requests.get(range_url, headers=headers, params=params, timeout=10, verify=False)
        r_range.raise_for_status()
        range_data = r_range.json()
        
        # 3. Look for the closing transaction in reverse order (most recent first)
        transactions = range_data.get("transactions", [])
        for tx in reversed(transactions):
            tx_type = tx.get("type")
            if tx_type == "ORDER_FILL":
                # Check if this order fill closed our trade
                closed_list = tx.get("tradesClosed", [])
                for closed in closed_list:
                    if str(closed.get("tradeID")) == str(trade_id):
                        print(f"✓ Found closed trade {trade_id} details in transaction {tx.get('id')}.")
                        return {
                            "realizedPL": closed.get("realizedPL", "0.0"),
                            "averageClosePrice": closed.get("price", "0.0")
                        }
    except Exception as e:
        print(f"Error: {e}")
        
    return {}

print("Searching for closed trade 26...")
result = find_closed_trade("26")
print("Result:", result)
