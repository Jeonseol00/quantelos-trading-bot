import sys
import os
import pandas as pd
import numpy as np

# Ensure python_node is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../python_node")))

from strategies import get_strategy

def mock_candlesticks(rows=50):
    """Generates dummy DataFrame representing candles."""
    np.random.seed(42)
    close_prices = 2000.0 + np.cumsum(np.random.normal(0, 5, rows))
    open_prices = close_prices - np.random.normal(0, 2, rows)
    high_prices = np.maximum(open_prices, close_prices) + np.abs(np.random.normal(0, 3, rows))
    low_prices = np.minimum(open_prices, close_prices) - np.abs(np.random.normal(0, 3, rows))
    volumes = np.random.randint(100, 1000, rows)
    
    times = pd.date_range(end=pd.Timestamp.now(), periods=rows, freq="5min")
    
    df = pd.DataFrame({
        "time": times,
        "open": open_prices,
        "high": high_prices,
        "low": low_prices,
        "close": close_prices,
        "volume": volumes
    })
    return df

def main():
    print("Testing Strategy Decoupling integration...")
    
    config = {
        "oanda": {
            "instruments": ["XAU_USD"]
        },
        "strategy": {
            "atr_period": 14,
            "atr_squeeze_threshold_pips": 80.0,
            "bollinger_period": 20,
            "bollinger_std": 2.0,
            "bollinger_bandwidth_percentile": 25,
            "rsi_period": 14,
            "rsi_neutral_low": 40,
            "rsi_neutral_high": 60,
            "scalping_rsi_low": 38,
            "scalping_rsi_high": 62,
            "scalping_vwap_std": 1.8,
            "scalping_trend_filter": True,
            "scalping_atr_tp_mult": 2.0,
            "scalping_atr_sl_mult": 1.5,
        }
    }
    
    df_m5 = mock_candlesticks(50)
    df_m15 = mock_candlesticks(50)
    df_h1 = mock_candlesticks(250)
    
    # Test MTF_SCALPER Strategy
    print("\n[TEST] Initializing MTF_SCALPER...")
    scalper = get_strategy("MTF_SCALPER", config)
    analysis_scalper = scalper.analyze(df_m5, df_m15, df_h1)
    sig_scalper = scalper.detect_signal(df_m5.iloc[-1]["close"], analysis_scalper)
    print(f"MTF_SCALPER Analyze Result keys: {list(analysis_scalper.keys())}")
    print(f"MTF_SCALPER Signal: {sig_scalper}")
    
    # Calculate Targets (BUY/SELL)
    sl_buy, tp_buy = scalper.calculate_targets(df_m5.iloc[-1]["close"], "BUY", analysis_scalper)
    sl_sell, tp_sell = scalper.calculate_targets(df_m5.iloc[-1]["close"], "SELL", analysis_scalper)
    print(f"MTF_SCALPER Targets (BUY): Current={df_m5.iloc[-1]['close']:.2f}, SL={sl_buy:.2f}, TP={tp_buy:.2f}")
    print(f"MTF_SCALPER Targets (SELL): Current={df_m5.iloc[-1]['close']:.2f}, SL={sl_sell:.2f}, TP={tp_sell:.2f}")

    # Test QUANTITATIVE_SNIPER Strategy
    print("\n[TEST] Initializing QUANTITATIVE_SNIPER...")
    sniper = get_strategy("QUANTITATIVE_SNIPER", config)
    analysis_sniper = sniper.analyze(df_m5, df_m15, df_h1)
    sig_sniper = sniper.detect_signal(df_m5.iloc[-1]["close"], analysis_sniper)
    print(f"QUANTITATIVE_SNIPER Analyze Result keys: {list(analysis_sniper.keys())}")
    print(f"QUANTITATIVE_SNIPER Signal: {sig_sniper}")
    
    # Calculate Targets (BUY/SELL)
    sl_buy_sn, tp_buy_sn = sniper.calculate_targets(df_m5.iloc[-1]["close"], "BUY", analysis_sniper)
    sl_sell_sn, tp_sell_sn = sniper.calculate_targets(df_m5.iloc[-1]["close"], "SELL", analysis_sniper)
    print(f"QUANTITATIVE_SNIPER Targets (BUY): Current={df_m5.iloc[-1]['close']:.2f}, SL={sl_buy_sn:.2f}, TP={tp_buy_sn:.2f}")
    print(f"QUANTITATIVE_SNIPER Targets (SELL): Current={df_m5.iloc[-1]['close']:.2f}, SL={sl_sell_sn:.2f}, TP={tp_sell_sn:.2f}")
    
    print("\nDecoupling verification test passed successfully!")

if __name__ == "__main__":
    main()
