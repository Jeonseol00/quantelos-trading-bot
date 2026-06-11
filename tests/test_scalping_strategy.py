#!/usr/bin/env python3
import sys
import os
import pandas as pd
import numpy as np

# Ensure python_node directory is in import path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../python_node")))

from technical_analyzer import TechnicalAnalyzer, ScalpingSignal

def create_mock_candle(close_price, volume=1000):
    return {
        "open": close_price,
        "high": close_price + 1.0,
        "low": close_price - 1.0,
        "close": close_price,
        "volume": volume
    }

def test_scalping_buy_signal():
    ta = TechnicalAnalyzer()

    # 1. Prepare H1 candles (Bullish Trend: EMA200 = 2300, current price = 2320)
    h1_data = []
    # Fill older candles with lower prices so EMA200 is around 2300
    for i in range(199):
        h1_data.append(create_mock_candle(2300.0))
    h1_data.append(create_mock_candle(2320.0))
    df_h1 = pd.DataFrame(h1_data)

    # 2. Prepare M15 candles (Price is above EMA50, e.g., EMA50 = 2315, price = 2320)
    m15_data = []
    for i in range(49):
        m15_data.append(create_mock_candle(2315.0))
    m15_data.append(create_mock_candle(2320.0))
    df_m15 = pd.DataFrame(m15_data)

    # 3. Prepare M5 candles (Price pulled back to lower VWAP band, RSI is oversold)
    # Let's mock a pullback:
    # We want current price = 2310.0 (near lower VWAP band)
    m5_data = []
    for i in range(29):
        m5_data.append(create_mock_candle(2315.0, volume=1000))
    
    # We want a pullback candle
    m5_data.append(create_mock_candle(2310.0, volume=5000))
    df_m5 = pd.DataFrame(m5_data)

    # We manually override VWAP/RSI columns in TechnicalAnalysis check or let indicators compute them.
    # To test indicator logic precisely, let's pass a dataframe that naturally computes these signals.
    # For H1: EMA200 of df_h1 close
    # For M15: Bollinger Bands & EMA50 of df_m15 close
    # For M5: VWAP & RSI of df_m5 close

    # Let's craft the dataframe values so they compute correctly.
    # H1 Trend:
    closes_h1 = np.ones(200) * 2300.0
    closes_h1[-1] = 2320.0 # Price > EMA200
    df_h1 = pd.DataFrame({
        "open": closes_h1, "high": closes_h1 + 1, "low": closes_h1 - 1, "close": closes_h1, "volume": 1000
    })

    # M15 Trend (Bullish: Price > EMA50):
    closes_m15 = np.ones(50) * 2315.0
    closes_m15[-1] = 2322.0
    df_m15 = pd.DataFrame({
        "open": closes_m15, "high": closes_m15 + 1.5, "low": closes_m15 - 1.5, "close": closes_m15, "volume": 1000
    })

    # M5 Pullback (VWAP lower band = 2311.0, current price = 2310.0, RSI = 28.0):
    closes_m5 = np.ones(30) * 2315.0
    # Drop price significantly at the end to force oversold RSI (< 35)
    closes_m5[-5:] = [2314.0, 2313.0, 2311.0, 2310.0, 2309.0]
    volumes_m5 = np.ones(30) * 1000
    volumes_m5[-1] = 5000 # High volume spike
    df_m5 = pd.DataFrame({
        "open": closes_m5, "high": closes_m5 + 0.5, "low": closes_m5 - 0.5, "close": closes_m5, "volume": volumes_m5
    })

    # Analyze
    sig = ta.analyze_scalping(df_m5, df_m15, df_h1)

    print("\n--- MTF Scalper Test Results (BUY Setup) ---")
    print(f"H1 Trend: {sig.h1_trend}")
    print(f"M15 BB Mid: {sig.m15_bb_mid}")
    print(f"M5 Price: {df_m5.iloc[-1]['close']}")
    print(f"M5 VWAP Mid: {sig.m5_vwap}")
    print(f"M5 VWAP Lower: {sig.m5_vwap_lower}")
    print(f"M5 RSI: {sig.m5_rsi}")
    print(f"Direction: {sig.direction}")

    assert sig.h1_trend == "BULLISH", f"Expected Bullish trend, got {sig.h1_trend}"
    assert sig.direction == "BUY", f"Expected BUY signal, got {sig.direction}"
    print("✓ test_scalping_buy_signal PASSED!")

def test_scalping_sell_signal():
    ta = TechnicalAnalyzer()

    # Bearish setup
    # H1 Trend: Bearish (Price < EMA200)
    closes_h1 = np.ones(200) * 2350.0
    closes_h1[-1] = 2320.0
    df_h1 = pd.DataFrame({
        "open": closes_h1, "high": closes_h1 + 1, "low": closes_h1 - 1, "close": closes_h1, "volume": 1000
    })

    # M15 Trend: Bearish (Price < EMA50)
    closes_m15 = np.ones(50) * 2330.0
    closes_m15[-1] = 2320.0
    df_m15 = pd.DataFrame({
        "open": closes_m15, "high": closes_m15 + 1.5, "low": closes_m15 - 1.5, "close": closes_m15, "volume": 1000
    })

    # M5 Pullback (VWAP upper band = 2324.0, current price = 2325.0, RSI = 72.0)
    closes_m5 = np.ones(30) * 2315.0
    closes_m5[-5:] = [2316.0, 2318.0, 2322.0, 2330.0, 2332.0]
    volumes_m5 = np.ones(30) * 1000
    volumes_m5[-1] = 4000
    df_m5 = pd.DataFrame({
        "open": closes_m5, "high": closes_m5 + 0.5, "low": closes_m5 - 0.5, "close": closes_m5, "volume": volumes_m5
    })

    # Analyze
    sig = ta.analyze_scalping(df_m5, df_m15, df_h1)

    print("\n--- MTF Scalper Test Results (SELL Setup) ---")
    print(f"H1 Trend: {sig.h1_trend}")
    print(f"M15 BB Mid: {sig.m15_bb_mid}")
    print(f"M5 Price: {df_m5.iloc[-1]['close']}")
    print(f"M5 VWAP Mid: {sig.m5_vwap}")
    print(f"M5 VWAP Upper: {sig.m5_vwap_upper}")
    print(f"M5 RSI: {sig.m5_rsi}")
    print(f"Direction: {sig.direction}")

    assert sig.h1_trend == "BEARISH", f"Expected Bearish trend, got {sig.h1_trend}"
    assert sig.direction == "SELL", f"Expected SELL signal, got {sig.direction}"
    print("✓ test_scalping_sell_signal PASSED!")

if __name__ == "__main__":
    try:
        test_scalping_buy_signal()
        test_scalping_sell_signal()
        print("\n🎉 ALL SCALPER STRATEGY TESTS PASSED SUCCESSFULLY!")
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
