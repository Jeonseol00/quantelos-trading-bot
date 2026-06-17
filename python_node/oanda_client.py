# =============================================================================
# Quantelos AI Trader — OANDA API & Pricing Stream Client
# =============================================================================
# FIXES APPLIED (Audit v1.0):
#   C-01: Nanosecond timestamp crash — added _parse_oanda_time() normalizer
#   H-03: SSL verification — now configurable with secure-by-default
#   M-06: REST polling fallback interval increased to 15s with jitter
#   M-08: Timezone-aware datetime consistency enforced throughout
# =============================================================================
import logging
import json
import threading
import time
import re
from datetime import datetime, timezone
import requests
import pandas as pd
import urllib3

logger = logging.getLogger("quantelos.oanda")


class OANDAClient:
    def __init__(
        self,
        api_url: str,
        stream_url: str,
        account_id: str,
        api_token: str,
        instrument: str = "EUR_USD",
        granularity: str = "M15",
        verify_ssl: bool = True,          # FIX H-03: secure-by-default
        ssl_cert_bundle: str | None = None, # FIX H-03: allow custom CA bundle
        event_bus=None
    ):
        self.api_url = api_url.rstrip("/")
        self.stream_url = stream_url.rstrip("/")
        self.account_id = account_id
        self.api_token = api_token
        self.instrument = instrument
        self.granularity = granularity
        self.event_bus = event_bus

        # FIX H-03: Configurable SSL — never silently disable
        if not verify_ssl:
            logger.warning(
                "⚠️  SSL VERIFICATION DISABLED. API token transmitted insecurely. "
                "Set REQUESTS_CA_BUNDLE env var or pass ssl_cert_bundle= to fix."
            )
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._ssl = ssl_cert_bundle if ssl_cert_bundle else verify_ssl

        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

        self.df = pd.DataFrame()
        self.df_lock = threading.Lock()
        self._running = False
        self._thread = None

    # ─── FIX C-01: Nanosecond timestamp normalizer ────────────────────────────
    def _parse_oanda_time(self, raw_time: str) -> datetime:
        """
        Safely parse OANDA's nanosecond-precision ISO timestamps.

        OANDA v20 returns:  '2026-06-09T15:15:00.000000000Z'  (9 decimal places)
        Python fromisoformat: only handles up to 6 decimal places (microseconds)
        → Without this fix: ValueError crash on every candle parse.
        """
        # Strip nanoseconds to microsecond precision (6 digits max), replace Z suffix
        normalized = re.sub(r'(\.\d{6})\d+(Z?)$', r'\1+00:00', raw_time)
        # Handle case where timestamp has no fractional seconds at all
        if normalized == raw_time:
            normalized = raw_time.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            # Last-resort fallback: strip all sub-second precision
            base = raw_time.split('.')[0]
            logger.warning("Timestamp parse fallback for: %s", raw_time)
            return datetime.fromisoformat(base + "+00:00")

    def fetch_historical_candles(self, count: int = 100, granularity: str = None, include_incomplete: bool = True) -> pd.DataFrame:
        """Fetch historical candles to bootstrap the analyzer."""
        gran = granularity or self.granularity
        url = f"{self.api_url}/v3/instruments/{self.instrument}/candles"
        params = {
            "count": str(count),
            "granularity": gran,
            "price": "M"
        }
        logger.info("Fetching %s %s candles from OANDA...", count, gran)
        try:
            r = requests.get(url, headers=self.headers, params=params, timeout=15, verify=self._ssl)
            r.raise_for_status()
            data = r.json()

            candles_list = []
            for c in data.get("candles", []):
                if not include_incomplete and not c.get("complete"):
                    continue
                # FIX C-01: Use the safe timestamp parser
                dt = self._parse_oanda_time(c["time"])
                mid = c["mid"]
                candles_list.append({
                    "time": dt,
                    "open": float(mid["o"]),
                    "high": float(mid["h"]),
                    "low":  float(mid["l"]),
                    "close": float(mid["c"]),
                    "volume": int(c["volume"])
                })

            new_df = pd.DataFrame(candles_list)
            with self.df_lock:
                self.df = new_df
            logger.info(
                "Successfully fetched %s historical candles (include_incomplete=%s).",
                len(self.df), include_incomplete
            )
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
            r = requests.get(url, headers=self.headers, timeout=10, verify=self._ssl)
            r.raise_for_status()
            data = r.json()
            acc = data.get("account", {})
            return {
                "balance":          float(acc.get("balance", 0)),
                "equity":           float(acc.get("NAV", acc.get("equity", 0))),
                "unrealized_pl":    float(acc.get("unrealizedPL", 0)),
                "margin_used":      float(acc.get("marginUsed", 0)),
                "margin_available": float(acc.get("marginAvailable", 0)),
                "currency":         acc.get("currency", "USD")
            }
        except Exception as e:
            logger.error("Failed to fetch account summary: %s", e)
            return {
                "balance": 0.0, "equity": 0.0, "unrealized_pl": 0.0,
                "margin_used": 0.0, "margin_available": 0.0, "currency": "USD"
            }

    def get_open_trades(self) -> list:
        """Fetch currently open trades from OANDA."""
        url = f"{self.api_url}/v3/accounts/{self.account_id}/openTrades"
        try:
            r = requests.get(url, headers=self.headers, timeout=10, verify=self._ssl)
            r.raise_for_status()
            return r.json().get("trades", [])
        except Exception as e:
            logger.error("Failed to fetch open trades: %s", e)
            return []

    def _find_closed_trade_in_transactions(self, trade_id: str) -> dict:
        """Scan recent transactions to find close details for a completed trade."""
        try:
            url = f"{self.api_url}/v3/accounts/{self.account_id}/transactions"
            r = requests.get(url, headers=self.headers, timeout=10, verify=self._ssl)
            r.raise_for_status()
            data = r.json()
            last_id_str = data.get("lastTransactionID")
            if not last_id_str:
                return {}

            last_id = int(last_id_str)
            from_id = max(1, last_id - 99)

            range_url = f"{self.api_url}/v3/accounts/{self.account_id}/transactions/idrange"
            r_range = requests.get(
                range_url, headers=self.headers,
                params={"from": str(from_id), "to": str(last_id)},
                timeout=10, verify=self._ssl
            )
            r_range.raise_for_status()
            transactions = r_range.json().get("transactions", [])

            for tx in reversed(transactions):
                if tx.get("type") == "ORDER_FILL":
                    for closed in tx.get("tradesClosed", []):
                        if str(closed.get("tradeID")) == str(trade_id):
                            logger.info("✓ Found closed trade %s in transaction %s.", trade_id, tx.get("id"))
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
            r = requests.get(url, headers=self.headers, timeout=10, verify=self._ssl)
            if r.status_code == 404:
                logger.info("Trade %s returned 404. Scanning transaction history...", trade_id)
                return self._find_closed_trade_in_transactions(trade_id)
            r.raise_for_status()
            return r.json().get("trade", {})
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return self._find_closed_trade_in_transactions(trade_id)
            logger.error("Failed to fetch trade %s: %s", trade_id, e)
            return {}
        except Exception as e:
            logger.error("Failed to fetch trade %s: %s", trade_id, e)
            return {}

    def close_trade(self, trade_id: str) -> bool:
        """Close a specific open trade on OANDA."""
        url = f"{self.api_url}/v3/accounts/{self.account_id}/trades/{trade_id}/close"
        try:
            r = requests.put(url, headers=self.headers, json={"units": "ALL"},
                             timeout=10, verify=self._ssl)
            r.raise_for_status()
            logger.info("Successfully closed trade %s on OANDA.", trade_id)
            return True
        except Exception as e:
            logger.error("Failed to close trade %s: %s", trade_id, e)
            return False

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
        """
        Round datetime down to the start of the current granularity window.
        FIX M-08: Preserve timezone info when rounding to ensure consistent comparisons.
        """
        if self.granularity == "M5":
            minute = (dt.minute // 5) * 5
        elif self.granularity == "M1":
            minute = dt.minute
        else:  # M15 default
            minute = (dt.minute // 15) * 15
        # replace() preserves tzinfo — both tick and candle times stay timezone-aware
        return dt.replace(minute=minute, second=0, microsecond=0)

    def _process_tick(self, tick_time: datetime, mid_price: float):
        """Update the current candle or spawn a new one based on incoming tick."""
        with self.df_lock:
            if self.df.empty:
                candle_start = self._get_candle_start_time(tick_time)
                self.df = pd.DataFrame([{
                    "time": candle_start,
                    "open": mid_price, "high": mid_price,
                    "low":  mid_price, "close": mid_price, "volume": 1
                }])
                return

            last_candle_time = self.df.iloc[-1]["time"]
            tick_candle_time = self._get_candle_start_time(tick_time)

            # FIX M-08: Normalize both to UTC-aware before comparison
            if hasattr(last_candle_time, 'tzinfo') and last_candle_time.tzinfo is None:
                last_candle_time = last_candle_time.replace(tzinfo=timezone.utc)

            if tick_candle_time == last_candle_time:
                idx = self.df.index[-1]
                self.df.at[idx, "close"] = mid_price
                if mid_price > self.df.at[idx, "high"]:
                    self.df.at[idx, "high"] = mid_price
                if mid_price < self.df.at[idx, "low"]:
                    self.df.at[idx, "low"] = mid_price
                self.df.at[idx, "volume"] += 1
            elif tick_candle_time > last_candle_time:
                logger.info("New candle window: %s", tick_candle_time)
                new_row = pd.DataFrame([{
                    "time": tick_candle_time,
                    "open": mid_price, "high": mid_price,
                    "low":  mid_price, "close": mid_price, "volume": 1
                }])
                self.df = pd.concat([self.df, new_row], ignore_index=True)
                if len(self.df) > 500:
                    self.df = self.df.iloc[-500:].reset_index(drop=True)
                    
            if self.event_bus:
                self.event_bus.put(("TICK", tick_time, mid_price))

    def _poll_fallback(self):
        """
        Fallback to REST API polling when stream is blocked or disconnected.
        FIX M-06: Increased interval to 15s with ±2s jitter to avoid rate limit storms.
        """
        import random
        logger.warning("Falling back to REST polling (stream unavailable)...")
        for _ in range(4):  # 4 × ~15s = ~60s before retrying stream
            if not self._running:
                break
            try:
                self.fetch_historical_candles(count=500, include_incomplete=True)
            except Exception as e:
                logger.error("REST fallback poll failed: %s", e)
            jitter = random.uniform(-2.0, 2.0)
            time.sleep(15.0 + jitter)

    def _stream_loop(self):
        """Main loop parsing OANDA's chunked JSON responses."""
        stream_url = f"{self.stream_url}/v3/accounts/{self.account_id}/pricing/stream"
        params = {"instruments": self.instrument}

        while self._running:
            try:
                logger.info("Connecting to OANDA pricing stream...")
                r = requests.get(
                    stream_url, headers=self.headers, params=params,
                    stream=True, timeout=30, verify=self._ssl
                )
                if r.status_code != 200:
                    logger.error("Stream returned status %s: %s", r.status_code, r.text)
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
                        if data.get("instrument") != self.instrument:
                            continue
                        bids = data.get("bids")
                        asks = data.get("asks")
                        if not bids or not asks:
                            continue
                        bid_price = float(bids[0]["price"])
                        ask_price = float(asks[0]["price"])
                        mid_price = (bid_price + ask_price) / 2.0
                        # FIX C-01: Use safe timestamp parser
                        tick_time = self._parse_oanda_time(data["time"])
                        self._process_tick(tick_time, mid_price)

            except requests.exceptions.RequestException as e:
                logger.warning("Stream connection lost: %s.", e)
                self._poll_fallback()
            except Exception as e:
                logger.error("Error in pricing stream loop: %s", e)
                self._poll_fallback()
