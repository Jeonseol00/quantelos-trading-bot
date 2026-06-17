#!/usr/bin/env python3
import pandas_ta as ta
from oanda_client import OANDAClient
try:
    import tomllib
except ImportError:
    import tomli as tomllib

with open("config/settings.toml", "rb") as f:
    cfg = tomllib.load(f)

oanda = OANDAClient(
    api_url=cfg['oanda']['api_url'],
    account_id=cfg['oanda']['account_id'],
    api_token=cfg['oanda']['api_token'],
    stream_url=cfg['oanda']['stream_url'],
    instrument="XAU_USD"
)

print("Fetching H1 historical candles...")
df_h1 = oanda.fetch_historical_candles(count=250, granularity="H1")
if df_h1 is not None and not df_h1.empty:
    ema50 = ta.ema(df_h1["close"], length=50).iloc[-1]
    ema200 = ta.ema(df_h1["close"], length=200).iloc[-1]
    current_price = df_h1["close"].iloc[-1]
    print(f"Current XAU_USD Price: {current_price}")
    print(f"H1 EMA 50: {ema50} | Trend (EMA50): {'BULLISH' if current_price > ema50 else 'BEARISH'}")
    print(f"H1 EMA 200: {ema200} | Trend (EMA200): {'BULLISH' if current_price > ema200 else 'BEARISH'}")
else:
    print("Failed to fetch candles.")
