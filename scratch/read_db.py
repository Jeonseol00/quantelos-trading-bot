#!/usr/bin/env python3
import sqlite3
import pandas as pd

conn = sqlite3.connect("./data/quantelos.db")

print("--- ACTIVE POSITIONS ---")
df_pos = pd.read_sql_query("SELECT * FROM active_positions", conn)
print(df_pos.to_string())

print("\n--- TRADE LOGS EVALUATION ---")
df_eval = pd.read_sql_query("SELECT * FROM trade_logs_evaluation", conn)
print(df_eval.to_string())

conn.close()
