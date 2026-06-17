import sys
import os
import sqlite3
import requests
import json
import pandas as pd

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
    
    # Fetch all closed trades from OANDA
    print("Fetching trades from OANDA...")
    url = f"{api_url}/v3/accounts/{account_id}/trades"
    params = {"state": "ALL"}
    r = requests.get(url, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    print("OANDA Response JSON:")
    print(json.dumps(r.json(), indent=2))
    oanda_trades = r.json().get("trades", [])
    
    oanda_df_data = []
    for t in oanda_trades:
        oanda_df_data.append({
            "trade_id": t["id"],
            "pair": t["instrument"],
            "direction": "BUY" if int(t["currentUnits"]) > 0 else "SELL",
            "units": abs(int(t["currentUnits"])),
            "entry_price": float(t["price"]),
            "close_price": float(t.get("averageClosePrice", 0.0)) if t.get("averageClosePrice") else None,
            "realized_pl": float(t.get("realizedPL", 0.0)),
            "status": t["state"]
        })
    df_oanda = pd.DataFrame(oanda_df_data)
    
    # Fetch trades from DB
    conn = sqlite3.connect("./data/quantelos.db")
    df_db_pos = pd.read_sql_query("SELECT * FROM active_positions", conn)
    df_db_eval = pd.read_sql_query("SELECT * FROM trade_logs_evaluation", conn)
    conn.close()
    
    print("\n--- OANDA TRADES SUMMARY ---")
    print(df_oanda.to_string())
    
    print("\n--- DB ACTIVE POSITIONS ---")
    print(df_db_pos.to_string())
    
    print("\n--- DB TRADE EVALUATIONS ---")
    print(df_db_eval.to_string())
    
    # Find missing trades
    oanda_ids = set(df_oanda["trade_id"].tolist())
    db_ids = set(df_db_pos["trade_id"].tolist())
    
    missing_in_db = oanda_ids - db_ids
    print(f"\nMissing in DB (OANDA has them but DB doesn't): {missing_in_db}")
    for mid in missing_in_db:
        print(f"OANDA Trade Details for ID {mid}:")
        print(df_oanda[df_oanda["trade_id"] == mid].to_dict(orient="records"))

if __name__ == "__main__":
    main()
