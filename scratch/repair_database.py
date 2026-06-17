import sqlite3

def check_and_fix_database():
    db_path = "/home/titiw/Downloads/Forex/quantelos/data/quantelos.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    print("--- Current Corrupted RL Parameters ---")
    cursor.execute("SELECT config_key, config_value FROM system_state WHERE config_key LIKE 'strategy_scalping_%';")
    rows = cursor.fetchall()
    for row in rows:
        print(f"{row['config_key']}: {row['config_value']}")

    print("\n--- Identifying Corrupted Logs ---")
    cursor.execute("SELECT COUNT(*) FROM weekend_training_logs;")
    corrupt_count = cursor.fetchone()[0]
    print(f"Found {corrupt_count} phantom training logs generated during the compromised session.")

    print("\n--- Reverting Strategy Parameters to Baseline ---")
    # Restore defaults from settings.toml
    baseline_params = {
        "strategy_scalping_rsi_low": "40",
        "strategy_scalping_rsi_high": "60",
        "strategy_scalping_vwap_std": "1.30"
    }

    for key, val in baseline_params.items():
        cursor.execute("UPDATE system_state SET config_value = ? WHERE config_key = ?", (val, key))
        print(f"Restored {key} to {val}")

    print("\n--- Deleting Corrupted Logs ---")
    cursor.execute("DELETE FROM weekend_training_logs;")
    print(f"Deleted {cursor.rowcount} corrupted training episodes.")

    conn.commit()
    conn.close()
    print("\n[SUCCESS] Database surgically repaired. Phantom data amputated.")

if __name__ == "__main__":
    check_and_fix_database()
