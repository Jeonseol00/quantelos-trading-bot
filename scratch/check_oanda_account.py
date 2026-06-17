import sys
import os
import requests
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../python_node")))

try:
    import tomllib
except ImportError:
    import tomli as tomllib

def main():
    with open("config/settings.toml", "rb") as f:
        cfg = tomllib.load(f)
        
    oanda_cfg = cfg["oanda"]
    api_url = oanda_cfg["api_url"]
    account_id = oanda_cfg["account_id"]
    api_token = oanda_cfg["api_token"]
    
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    
    tx_url = f"{api_url}/v3/accounts/{account_id}/transactions"
    try:
        r = requests.get(tx_url, headers=headers, timeout=10)
        r.raise_for_status()
        last_id = int(r.json().get("lastTransactionID", 0))
        
        if last_id > 0:
            from_id = max(1, last_id - 35)
            range_url = f"{api_url}/v3/accounts/{account_id}/transactions/idrange"
            r_range = requests.get(range_url, headers=headers, params={"from": str(from_id), "to": str(last_id)}, timeout=10)
            r_range.raise_for_status()
            txs = r_range.json().get("transactions", [])
            for tx in reversed(txs):
                print(f"--- Transaction {tx.get('id')} ({tx.get('type')}) ---")
                print(json.dumps(tx, indent=2))
    except Exception as e:
        print(f"Error fetching transactions: {e}")

if __name__ == "__main__":
    main()
