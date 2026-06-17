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
    
    # Fetch all transactions from 1 to lastTransactionID
    print("Fetching last transaction ID...")
    tx_url = f"{api_url}/v3/accounts/{account_id}/transactions"
    r = requests.get(tx_url, headers=headers, timeout=10)
    r.raise_for_status()
    last_id = int(r.json().get("lastTransactionID", 0))
    print(f"Last Transaction ID is {last_id}")
    
    # We will fetch transactions in chunks of 500 (OANDA limit is usually 1000)
    all_txs = []
    chunk_size = 500
    for from_id in range(1, last_id + 1, chunk_size):
        to_id = min(last_id, from_id + chunk_size - 1)
        print(f"Fetching transactions {from_id} to {to_id}...")
        range_url = f"{api_url}/v3/accounts/{account_id}/transactions/idrange"
        r_range = requests.get(range_url, headers=headers, params={"from": str(from_id), "to": str(to_id)}, timeout=10)
        r_range.raise_for_status()
        txs = r_range.json().get("transactions", [])
        all_txs.extend(txs)
        
    print(f"Fetched {len(all_txs)} transactions.")
    
    # Parse trades
    trades = {}
    
    for tx in all_txs:
        tx_type = tx.get("type")
        tx_id = tx.get("id")
        
        if tx_type == "ORDER_FILL":
            # Check if this transaction opened or closed trades
            trade_opened = tx.get("tradeOpened")
            if trade_opened:
                t_id = trade_opened.get("tradeID")
                units_val = float(tx.get("units", 0))
                trades[t_id] = {
                    "trade_id": t_id,
                    "pair": tx.get("instrument"),
                    "direction": "BUY" if units_val > 0 else "SELL",
                    "units": abs(units_val),
                    "entry_price": float(tx.get("price")),
                    "entry_time": tx.get("time"),
                    "close_price": None,
                    "close_time": None,
                    "realized_pl": 0.0,
                    "close_reason": None,
                    "status": "OPEN"
                }
            
            trades_closed = tx.get("tradesClosed", [])
            for tc in trades_closed:
                t_id = tc.get("tradeID")
                units_val = float(tc.get("units", 0))
                if t_id not in trades:
                    # Occurs if trade was opened before the first transaction we fetched,
                    # or if OANDA opened it via some other way. Let's initialize it.
                    trades[t_id] = {
                        "trade_id": t_id,
                        "pair": tx.get("instrument"),
                        "direction": None,
                        "units": abs(units_val),
                        "entry_price": None,
                        "entry_time": None,
                        "close_price": float(tc.get("price")),
                        "close_time": tx.get("time"),
                        "realized_pl": float(tc.get("realizedPL", 0.0)),
                        "close_reason": tx.get("reason"),
                        "status": "CLOSED"
                    }
                else:
                    trades[t_id]["close_price"] = float(tc.get("price"))
                    trades[t_id]["close_time"] = tx.get("time")
                    trades[t_id]["realized_pl"] = float(tc.get("realizedPL", 0.0))
                    trades[t_id]["close_reason"] = tx.get("reason")
                    trades[t_id]["status"] = "CLOSED"
                    
    df_rebuilt = pd.DataFrame(trades.values())
    print("\n--- REBUILT TRADE HISTORY FROM OANDA TRANSACTIONS ---")
    print(df_rebuilt.to_string())
    
    # Query database
    conn = sqlite3.connect("./data/quantelos.db")
    df_db_pos = pd.read_sql_query("SELECT * FROM active_positions", conn)
    df_db_eval = pd.read_sql_query("SELECT * FROM trade_logs_evaluation", conn)
    conn.close()
    
    # Check if any OANDA trade is missing in db_pos or db_eval
    print("\n--- DISCREPANCY ANALYSIS ---")
    for idx, row in df_rebuilt.iterrows():
        t_id = row["trade_id"]
        
        # Check active_positions
        db_pos_matches = df_db_pos[df_db_pos["trade_id"] == t_id]
        db_eval_matches = df_db_eval[df_db_eval["trade_id"] == t_id]
        
        pos_status = "OK" if len(db_pos_matches) > 0 else "MISSING"
        eval_status = "OK" if len(db_eval_matches) > 0 else "MISSING"
        
        print(f"Trade ID {t_id:3s} ({row['pair']}): db_positions={pos_status:7s} | db_evaluations={eval_status:7s} | PL={row['realized_pl']:10.4f} | Reason={str(row['close_reason']):20s}")
        
if __name__ == "__main__":
    main()
