# =============================================================================
# Quantelos AI Trader — OANDA API & Pricing Stream Client
# =============================================================================
# Fetches historical M15 candles and subscribes to the live pricing stream
# to build and maintain the real-time OHLCV DataFrame for Squeeze Detection.
# =============================================================================
import logging
import json
import threading
import time
from datetime import datetime, timezone
import requests
import pandas as pd
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("quantelos.oanda")

class OANDAClient:
    def __init__(self, api_url: str, stream_url: str, account_id: str, api_token: str, instrument: str = "EUR_USD", granularity: str = "M15"):
        self.api_url = api_url.rstrip("/")
        self.stream_url = stream_url.rstrip("/")
        self.account_id = account_id
        self.api_token = api_token
        self.instrument = instrument
        self.granularity = granularity

        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

        self.df = pd.DataFrame()
        self.df_lock = threading.Lock()
        self._running = False
        self._thread = None

    def fetch_historical_candles(self, count: int = 100, granularity: str = None, include_incomplete: bool = True) -> pd.DataFrame:
        """Fetch historical candles to bootstrap the analyzer."""
        gran = granularity or self.granularity
        url = f"{self.api_url}/v3/instruments/{self.instrument}/candles"
        params = {
            "count": str(count),
            "granularity": gran,
            "price": "M"  # Midpoint pricing
        }
        logger.info("Fetching %s %s candles from OANDA...", count, gran)
        try:
            r = requests.get(url, headers=self.headers, params=params, timeout=15, verify=False)
            r.raise_for_status()
            data = r.json()
            
            candles_list = []
            for c in data.get("candles", []):
                if not include_incomplete and not c.get("complete"):
                    continue
                # Parse OANDA time format (e.g., '2026-06-09T15:15:00.000000000Z')
                raw_time = c["time"]
                # Convert to datetime (keep UTC)
                dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                mid = c["mid"]
                candles_list.append({
                    "time": dt,
                    "open": float(mid["o"]),
                    "high": float(mid["h"]),
                    "low": float(mid["l"]),
                    "close": float(mid["c"]),
                    "volume": int(c["volume"])
                })
            
            new_df = pd.DataFrame(candles_list)
            with self.df_lock:
                self.df = new_df
            logger.info("Successfully fetched %s historical candles (include_incomplete=%s).", len(self.df), include_incomplete)
            return self.df
        except Exception as e:
            logger.error("Failed to fetch historical candles: %s", e)
            raise

    def get_latest_dataframe(self) -> pd.DataFrame:
        """Thread-safe getter for the latest OHLCV DataFrame."""
        with self.df_lock:
            return self.df.copy()

    def get_account_summary(self) -> dict:
        """Fetch OANDA account balance, equity, and margin details."""
        url = f"{self.api_url}/v3/accounts/{self.account_id}/summary"
        try:
            r = requests.get(url, headers=self.headers, timeout=10, verify=False)
            r.raise_for_status()
            data = r.json()
            acc = data.get("account", {})
            return {
                "balance": float(acc.get("balance", 0)),
                "equity": float(acc.get("NAV", acc.get("equity", 0))),
                "unrealized_pl": float(acc.get("unrealizedPL", 0)),
                "margin_used": float(acc.get("marginUsed", 0)),
                "margin_available": float(acc.get("marginAvailable", 0)),
                "currency": acc.get("currency", "USD")
            }
        except Exception as e:
            logger.error("Failed to fetch account summary: %s", e)
            return {
                "balance": 0.0,
                "equity": 0.0,
                "unrealized_pl": 0.0,
                "margin_used": 0.0,
                "margin_available": 0.0,
                "currency": "USD"
            }

    def get_open_trades(self) -> list:
        """Fetch currently open trades from OANDA."""
        url = f"{self.api_url}/v3/accounts/{self.account_id}/openTrades"
        try:
            r = requests.get(url, headers=self.headers, timeout=10, verify=False)
            r.raise_for_status()
            data = r.json()
            return data.get("trades", [])
        except Exception as e:
            logger.error("Failed to fetch open trades: %s", e)
            return []

    def _find_closed_trade_in_transactions(self, trade_id: str) -> dict:
        """
        Scan recent transactions to find the close details for a trade.
        OANDA's /trades/{trade_id} endpoint returns 404 once a trade is closed.
        """
        try:
            # 1. Get the last transaction ID
            url = f"{self.api_url}/v3/accounts/{self.account_id}/transactions"
            r = requests.get(url, headers=self.headers, timeout=10, verify=False)
            r.raise_for_status()
            data = r.json()
            last_id_str = data.get("lastTransactionID")
            if not last_id_str:
                return {}
            
            last_id = int(last_id_str)
            from_id = max(1, last_id - 99)
            
            # 2. Fetch transaction details in that range
            range_url = f"{self.api_url}/v3/accounts/{self.account_id}/transactions/idrange"
            params = {
                "from": str(from_id),
                "to": str(last_id)
            }
            r_range = requests.get(range_url, headers=self.headers, params=params, timeout=10, verify=False)
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
                            logger.info("✓ Found closed trade %s details in transaction %s.", trade_id, tx.get("id"))
                            return {
                                "realizedPL": closed.get("realizedPL", "0.0"),
                                "averageClosePrice": closed.get("price", "0.0")
                            }
        except Exception as e:
            logger.error("Failed to find closed trade %s in transactions: %s", trade_id, e)
            
        return {}

    def get_trade_details(self, trade_id: str) -> dict:
        """Fetch details of a specific trade (open or closed)."""
        url = f"{self.api_url}/v3/accounts/{self.account_id}/trades/{trade_id}"
        try:
            r = requests.get(url, headers=self.headers, timeout=10, verify=False)
            if r.status_code == 404:
                # Fallback to scanning transaction history
                logger.info("Trade %s returned 404. Attempting transaction fallback scanning...", trade_id)
                return self._find_closed_trade_in_transactions(trade_id)
            r.raise_for_status()
            data = r.json()
            return data.get("trade", {})
        except Exception as e:
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None and e.response.status_code == 404:
                logger.info("Trade %s HTTP Error 404. Attempting transaction fallback scanning...", trade_id)
                return self._find_closed_trade_in_transactions(trade_id)
            logger.error("Failed to fetch trade %s details: %s", trade_id, e)
            return {}

    def start_stream(self):
        """Start the pricing stream in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._thread.start()
        logger.info("OANDA real-time pricing stream thread started.")

    def stop_stream(self):
        """Stop the background pricing stream."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            logger.info("OANDA pricing stream stopped.")

    def _get_candle_start_time(self, dt: datetime) -> datetime:
        """Round datetime down to the start of the current granularity window."""
        if self.granularity == "M5":
            minute = (dt.minute // 5) * 5
            return dt.replace(minute=minute, second=0, microsecond=0)
        elif self.granularity == "M1":
            return dt.replace(second=0, microsecond=0)
        else:
            minute = (dt.minute // 15) * 15
            return dt.replace(minute=minute, second=0, microsecond=0)

    def _process_tick(self, tick_time: datetime, mid_price: float):
        """Update the current candle or spawn a new one based on incoming tick."""
        with self.df_lock:
            if self.df.empty:
                # Bootstrap if empty
                candle_start = self._get_candle_start_time(tick_time)
                self.df = pd.DataFrame([{
                    "time": candle_start,
                    "open": mid_price,
                    "high": mid_price,
                    "low": mid_price,
                    "close": mid_price,
                    "volume": 1
                }])
                return

            last_candle_time = self.df.iloc[-1]["time"]
            tick_candle_time = self._get_candle_start_time(tick_time)

            if tick_candle_time == last_candle_time:
                # Update current candle
                idx = self.df.index[-1]
                self.df.at[idx, "close"] = mid_price
                if mid_price > self.df.at[idx, "high"]:
                    self.df.at[idx, "high"] = mid_price
                if mid_price < self.df.at[idx, "low"]:
                    self.df.at[idx, "low"] = mid_price
                self.df.at[idx, "volume"] += 1
            elif tick_candle_time > last_candle_time:
                # Spawn new candle
                logger.info("New candle window detected: %s", tick_candle_time)
                new_row = pd.DataFrame([{
                    "time": tick_candle_time,
                    "open": mid_price,
                    "high": mid_price,
                    "low": mid_price,
                    "close": mid_price,
                    "volume": 1
                }])
                self.df = pd.concat([self.df, new_row], ignore_index=True)
                # Maintain lookback size (keep last 500 rows)
                if len(self.df) > 500:
                    self.df = self.df.iloc[-500:].reset_index(drop=True)

    def _poll_fallback(self):
        """Fallback to REST API polling when streaming is blocked or disconnected."""
        logger.warning("Falling back to REST API polling for live candles (Indonesia ISP bypass mode)...")
        # Poll for 30 seconds (6 iterations * 5s) before trying stream again
        for _ in range(6):
            if not self._running:
                break
            try:
                self.fetch_historical_candles(count=500, include_incomplete=True)
            except Exception as e:
                logger.error("REST fallback poll failed: %s", e)
            time.sleep(5)

    def _stream_loop(self):
        """Main loop parsing OANDA's chunked JSON responses."""
        stream_url = f"{self.stream_url}/v3/accounts/{self.account_id}/pricing/stream"
        params = {"instruments": self.instrument}

        while self._running:
            try:
                logger.info("Connecting to OANDA pricing stream: %s", stream_url)
                r = requests.get(stream_url, headers=self.headers, params=params, stream=True, timeout=30, verify=False)
                if r.status_code != 200:
                    logger.error("Pricing stream returned status %s: %s", r.status_code, r.text)
                    self._poll_fallback()
                    continue

                for line in r.iter_lines():
                    if not self._running:
                        break
                    if not line:
                        continue
                    
                    try:
                        data = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue

                    if data.get("type") == "PRICE":
                        instrument = data.get("instrument")
                        if instrument != self.instrument:
                            continue
                        
                        bids = data.get("bids")
                        asks = data.get("asks")
                        if not bids or not asks:
                            continue

                        # Calculate midpoint price
                        bid_price = float(bids[0]["price"])
                        ask_price = float(asks[0]["price"])
                        mid_price = (bid_price + ask_price) / 2.0
                        
                        # Parse tick timestamp (ISO format)
                        raw_time = data.get("time")
                        tick_time = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                        
                        # Process tick into OHLCV DataFrame
                        self._process_tick(tick_time, mid_price)
                        
            except requests.exceptions.RequestException as e:
                logger.warning("Stream connection lost: %s.", e)
                self._poll_fallback()
            except Exception as e:
                logger.error("Error in pricing stream loop: %s", e)
                self._poll_fallback()
