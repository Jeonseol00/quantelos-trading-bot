# =============================================================================
# Quantelos AI Trader — Main Orchestrator (Python Logic Node)
# =============================================================================
# Entry point for the Python-side autonomous trading loop.
# Runs on ASUS Headless Server (Ubuntu 24.04 LTS, 4GB RAM).
# =============================================================================
import sys
import time
import logging
import signal
import os
import psutil
import threading
from datetime import datetime, timezone
from pathlib import Path

# ─── Configure Logging ────────────────────────────────────────────────────────
os.makedirs("./logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-18s] %(levelname)-7s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("./logs/quantelos.log"),
    ],
)
logger = logging.getLogger("quantelos.main")

# ─── Local Imports ────────────────────────────────────────────────────────────
from db_manager import DatabaseManager
from zmq_publisher import ZMQPublisher, TradeSignal
from technical_analyzer import TechnicalAnalyzer
from news_scraper import NewsScraper
from kaggle_bridge import KaggleBridge
from notifier import Notifier
from oanda_client import OANDAClient

try:
    import tomllib
except ImportError:
    import tomli as tomllib


class QuantelosOrchestrator:
    """
    Main autonomous trading loop coordinating all subsystems:
    1. Technical Squeeze Detection (ATR + BB + RSI)
    2. News Catalyst Scraping (Multi-Layer)
    3. AI Sentiment Inference (Kaggle Mini-Debate)
    4. ZMQ Signal Publishing (→ C++ Execution Engine)
    5. Health Monitoring & Notifications
    """

    def __init__(self, config_path: str = "./config/settings.toml"):
        logger.info("═" * 60)
        logger.info("  Quantelos AI Trader — Initializing...")
        logger.info("═" * 60)

        # Load configuration
        with open(config_path, "rb") as f:
            self.cfg = tomllib.load(f)

        # Initialize subsystems
        self.db = DatabaseManager(
            db_path=self.cfg["database"]["path"],
            schema_path="./database/schema.sql",
        )
        self.zmq = ZMQPublisher(
            ipc_path=self.cfg["zmq"]["ipc_path"],
            protocol=self.cfg["zmq"]["protocol"],
        )
        self.ta = TechnicalAnalyzer(
            atr_period=self.cfg["strategy"]["atr_period"],
            bb_period=self.cfg["strategy"]["bollinger_period"],
            bb_std=self.cfg["strategy"]["bollinger_std"],
            rsi_period=self.cfg["strategy"]["rsi_period"],
            atr_threshold_pips=self.cfg["strategy"]["atr_squeeze_threshold_pips"],
            bb_percentile=self.cfg["strategy"]["bollinger_bandwidth_percentile"],
            rsi_low=self.cfg["strategy"]["rsi_neutral_low"],
            rsi_high=self.cfg["strategy"]["rsi_neutral_high"],
            instrument=self.cfg["oanda"]["instruments"][0],
            scalping_rsi_low=self.cfg["strategy"].get("scalping_rsi_low", 30),
            scalping_rsi_high=self.cfg["strategy"].get("scalping_rsi_high", 70),
            scalping_vwap_std=self.cfg["strategy"].get("scalping_vwap_std", 2.0),
            scalping_trend_filter=self.cfg["strategy"].get("scalping_trend_filter", True),
        )
        self.scraper = NewsScraper(
            layer1_timeout_ms=self.cfg["scraper"]["layer1_timeout_ms"],
            layer2_enabled=self.cfg["scraper"]["layer2_enabled"],
            layer2_max_ram_mb=self.cfg["scraper"]["layer2_max_ram_mb"],
        )
        self.ai = KaggleBridge(db_manager=self.db,
                               timeout=self.cfg["kaggle"]["inference_timeout_s"])
        self.notifier = Notifier(
            discord_webhook=self.cfg["notifications"]["discord_webhook"],
            telegram_token=self.cfg["notifications"]["telegram_bot_token"],
            telegram_chat_id=self.cfg["notifications"]["telegram_chat_id"],
            enabled=self.cfg["notifications"]["enabled"],
        )
        self.oanda = OANDAClient(
            api_url=self.cfg["oanda"]["api_url"],
            stream_url=self.cfg["oanda"]["stream_url"],
            account_id=self.cfg["oanda"]["account_id"],
            api_token=self.cfg["oanda"]["api_token"],
            instrument=self.cfg["oanda"]["instruments"][0],
            granularity=self.cfg["strategy"]["timeframe"]
        )

        self.active_mode = self.cfg["strategy"].get("active_mode", "QUANTITATIVE_SNIPER")
        self.df_m15_cache = None
        self.df_m15_last_fetch = 0
        self.df_h1_cache = None
        self.df_h1_last_fetch = 0
        self.last_trade_time = 0

        self._running = True
        self.shutdown_event = threading.Event()
        self._setup_signal_handlers()
        logger.info("All subsystems initialized. Mode: %s", self.cfg["project"]["mode"])

    def _setup_signal_handlers(self):
        """Graceful shutdown on SIGINT/SIGTERM."""
        def handler(sig, frame):
            logger.info("Shutdown signal received (%s). Cleaning up...", sig)
            self._running = False
            self.shutdown_event.set()
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def _is_trading_blocked(self) -> bool:
        """Check if current time falls in midnight block window (MRD Section 2.2)."""
        now = datetime.now()
        block_start = self.cfg["market_windows"]["midnight_block_start"]
        block_end = self.cfg["market_windows"]["midnight_block_end"]
        current_time = now.strftime("%H:%M")
        return block_start <= current_time <= block_end

    def _is_news_blocked(self) -> bool:
        """
        Check if there is an active high-impact news event window.
        Blocks trading 5 minutes before and 10 minutes after high-impact USD/EUR news.
        """
        pre_min = self.cfg["strategy"].get("news_block_pre_min", 5)
        post_min = self.cfg["strategy"].get("news_block_post_min", 10)
        
        try:
            with self.db._connect() as conn:
                rows = conn.execute(
                    "SELECT scheduled_at, currency, event_name FROM news_events "
                    "WHERE impact_level = 'HIGH' "
                    "AND datetime(scheduled_at) >= datetime('now', '-2 hours') "
                    "AND datetime(scheduled_at) <= datetime('now', '+2 hours')"
                ).fetchall()
                
                now_utc = datetime.now(timezone.utc)
                for sched_str, currency, event_name in rows:
                    if currency not in ("USD", "EUR"):
                        continue
                    
                    try:
                        sched_dt = datetime.strptime(sched_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    except Exception:
                        try:
                            clean_str = sched_str
                            if " " in clean_str and "T" not in clean_str:
                                parts = clean_str.split("+")
                                if len(parts) > 1:
                                    clean_str = parts[0].strip().replace(" ", "T") + "+" + parts[1]
                            sched_dt = datetime.fromisoformat(clean_str.replace("Z", "+00:00"))
                        except Exception:
                            continue
                    
                    diff_sec = (now_utc - sched_dt).total_seconds()
                    if -pre_min * 60 <= diff_sec <= post_min * 60:
                        logger.warning("🚫 NEWS BLOCK active for event: [%s] %s (scheduled: %s, now: %s). Blocking trade execution.", 
                                       currency, event_name, sched_str, now_utc.strftime("%Y-%m-%d %H:%M:%S"))
                        return True
        except Exception as e:
            logger.error("Failed to check news blocks: %s", e)
            
        return False

    def _get_market_session(self) -> str:
        """Determine current market session for mode selection."""
        now = datetime.now().strftime("%H:%M")
        windows = self.cfg["market_windows"]
        if windows["golden_window_start"] <= now <= windows["golden_window_end"]:
            return "GOLDEN_WINDOW"
        elif windows["london_session_start"] <= now <= windows["london_session_end"]:
            return "LONDON"
        elif windows["asia_session_start"] <= now <= windows["asia_session_end"]:
            return "ASIA"
        return "OFF_HOURS"

    def _record_health(self):
        """Record system health metrics."""
        proc = psutil.Process()
        ram_mb = proc.memory_info().rss / (1024 * 1024)
        cpu_pct = proc.cpu_percent(interval=0.1)
        self.db.record_heartbeat("python_logic", "ALIVE", ram_mb, cpu_pct)
        self.zmq.publish_heartbeat()

        # Check Kaggle AI Swarm status and record its heartbeat
        import requests
        kaggle_url = self.ai._get_kaggle_url()
        if kaggle_url:
            try:
                resp = requests.get(f"{kaggle_url}/api/tags", timeout=3)
                if resp.status_code == 200:
                    self.db.record_heartbeat("kaggle_brain", "ALIVE", 0, 0)
                else:
                    self.db.record_heartbeat("kaggle_brain", "TIMEOUT", 0, 0)
            except Exception:
                try:
                    resp = requests.get(f"{kaggle_url}/", timeout=3)
                    self.db.record_heartbeat("kaggle_brain", "ALIVE" if resp.status_code == 200 else "TIMEOUT", 0, 0)
                except Exception:
                    self.db.record_heartbeat("kaggle_brain", "TIMEOUT", 0, 0)
        else:
            self.db.record_heartbeat("kaggle_brain", "TIMEOUT", 0, 0)

        # Check OANDA account summary
        try:
            summary = self.oanda.get_account_summary()
            self.db.set_config("account_balance", str(summary["balance"]))
            self.db.set_config("account_equity", str(summary["equity"]))
            self.db.set_config("account_unrealized_pl", str(summary["unrealized_pl"]))
            self.db.set_config("account_currency", summary["currency"])
        except Exception as e:
            logger.error("Failed to update account summary state: %s", e)

        # Check RAM threshold (BRD: stay under 2.5 GB total system)
        total_ram = psutil.virtual_memory()
        if total_ram.used / (1024**3) > 2.5:
            logger.warning("⚠️ System RAM usage exceeds 2.5 GB: %.1f GB",
                           total_ram.used / (1024**3))

    def _monitor_open_trades(self):
        """Check if any open positions have been closed by SL/TP on OANDA (Gap 3)."""
        try:
            with self.db._connect() as conn:
                db_positions = conn.execute(
                    "SELECT trade_id, pair, direction, entry_price FROM active_positions WHERE status = 'OPEN'"
                ).fetchall()
            if not db_positions:
                return

            oanda_trades = self.oanda.get_open_trades()
            oanda_trade_ids = {t["id"] for t in oanda_trades}

            for pos in db_positions:
                trade_id = pos["trade_id"]
                if trade_id not in oanda_trade_ids:
                    logger.info("📊 Trade %s closed on OANDA. Recording P/L...", trade_id)
                    details = self.oanda.get_trade_details(trade_id)
                    realized_pl = float(details.get("realizedPL", 0.0))
                    close_price = float(details.get("averageClosePrice", 0.0))
                    entry_price = float(pos["entry_price"])
                    
                    # Convert to US Cents (multiply raw dollar profit/loss by 100)
                    realized_pl_cents = realized_pl * 100.0
                    
                    # Determine pip size based on instrument (Gold = 0.1, JPY = 0.01, standard = 0.0001)
                    pair = pos.get("pair", "") or self.oanda.instrument
                    pip_size = 0.1 if "XAU" in pair else (0.01 if "JPY" in pair else 0.0001)
                    
                    raw_diff = close_price - entry_price
                    if pos.get("direction") == "SELL":
                        raw_diff = -raw_diff
                        
                    pips = raw_diff / pip_size if close_price else 0.0

                    with self.db._connect() as conn:
                        conn.execute(
                            "UPDATE active_positions SET status='CLOSED', closed_at=datetime('now') WHERE trade_id=?",
                            (trade_id,)
                        )
                        conn.execute(
                            """INSERT INTO trade_logs_evaluation
                               (trade_id, usc_profit_loss, pips_gained, strategy_tag)
                               VALUES (?, ?, ?, 'QUANTITATIVE_SNIPER')""",
                            (trade_id, realized_pl_cents, pips)
                        )

                    result_emoji = "🟢 WIN" if realized_pl > 0 else "🔴 LOSS"
                    self.notifier.send(
                        f"Trade Closed — {result_emoji}",
                        f"ID: `{trade_id}` | P/L: `{realized_pl:.2f}` | Pips: `{pips:.1f}`",
                        "TRADE"
                    )
                    logger.info("Recorded closed trade %s: P/L=%.2f, Pips=%.1f", trade_id, realized_pl, pips)
        except Exception as e:
            logger.error("Trade monitor error: %s", e)

    def run(self):
        """Main autonomous trading loop."""
        logger.info("Bootstrapping historical candles for Technical Analysis...")
        try:
            self.oanda.fetch_historical_candles(500)
        except Exception as e:
            logger.error("Initial bootstrap failed, retrying on loop. Error: %s", e)

        logger.info("Starting OANDA live price streaming...")
        self.oanda.start_stream()

        logger.info("Starting main trading loop...")
        heartbeat_interval = 60  # seconds
        last_heartbeat = 0
        last_news_scrape = 0

        while self._running:
            try:
                # ── Heartbeat and Monitoring ──────────────────────────────
                # Record health and monitor open positions even during passive sessions
                now = time.time()
                if now - last_heartbeat >= heartbeat_interval:
                    self._record_health()
                    self._monitor_open_trades()
                    last_heartbeat = now

                # Periodically scrape calendar (every 15 minutes)
                if now - last_news_scrape >= 900:
                    last_news_scrape = now
                    try:
                        logger.info("Scraping news calendar for dashboard...")
                        events = self.scraper.fetch_calendar()
                        if events:
                            with self.db._connect() as conn:
                                conn.execute("DELETE FROM news_events WHERE scraped_at < datetime('now', '-2 days')")
                            for e in events:
                                with self.db._connect() as conn:
                                    exists = conn.execute(
                                        "SELECT 1 FROM news_events WHERE event_name = ? AND currency = ? AND date(scraped_at) = date('now')",
                                        (e.event_name, e.currency)
                                    ).fetchone()
                                if not exists:
                                    sched = e.scheduled_at if e.scheduled_at else datetime.now(timezone.utc).isoformat()
                                    self.db.insert_news_event(
                                        currency=e.currency,
                                        event_name=e.event_name,
                                        impact=e.impact_level,
                                        forecast=e.forecast,
                                        actual=e.actual,
                                        previous=e.previous,
                                        scheduled_at=sched
                                    )
                    except Exception as ex:
                        logger.error("Failed to periodically scrape news: %s", ex)

                # Determine market session and trading restrictions
                session = self._get_market_session()
                is_halted = self.db.is_emergency_halt()
                is_blocked = self._is_trading_blocked()
                is_news_blocked = self._is_news_blocked()
                is_asia = (session == "ASIA")

                allow_asia = (self.active_mode == "MTF_SCALPER" and self.cfg["strategy"].get("allow_asia_scalping", False))
                is_passive = is_halted or is_blocked or is_news_blocked or (is_asia and not allow_asia)
                passive_reason = None
                if is_halted:
                    passive_reason = "EMERGENCY HALT"
                elif is_blocked:
                    passive_reason = "MIDNIGHT BLOCK"
                elif is_news_blocked:
                    passive_reason = "HIGH-IMPACT NEWS BLOCK"
                elif is_asia and not allow_asia:
                    passive_reason = "ASIA SESSION"

                if is_passive:
                    logger.debug("System operating in passive monitoring mode due to: %s", passive_reason)

                # Enforce cooldown period between trade signals
                cooldown_sec = self.cfg["strategy"].get("cooldown_sec", 120 if self.active_mode == "MTF_SCALPER" else 600)
                time_since_last = time.time() - self.last_trade_time
                if time_since_last < cooldown_sec:
                    logger.debug("Cooldown active (%.1fs remaining). Skipping execution checks.", cooldown_sec - time_since_last)
                    self.shutdown_event.wait(15)
                    continue

                # ── Phase 1: Technical Analysis & Signal Detection ──────────
                logger.info("🔍 [TIMEFRAME CHECK] Fetching M5 candledata from OANDA...")
                df = self.oanda.get_latest_dataframe()
                if df.empty or len(df) < 30:
                    logger.warning("Waiting for OANDA historical candles to bootstrap...")
                    self.shutdown_event.wait(5)
                    continue

                direction = None
                current_price = df.iloc[-1]["close"]
                squeeze_sig = None
                scalp_sig = None

                if self.active_mode == "MTF_SCALPER":
                    logger.info("⚙️ [STRATEGY] Running Multi-Timeframe (MTF) Scalper strategy analysis...")
                    now_ts = time.time()
                    
                    # Refresh M15 historical cache if expired (120 sec)
                    if self.df_m15_cache is None or now_ts - self.df_m15_last_fetch > 120:
                        logger.info("📥 [DATA FETCH] Fetching fresh M15 candles for intermediate structure analysis...")
                        try:
                            self.df_m15_cache = self.oanda.fetch_historical_candles(count=50, granularity="M15")
                            self.df_m15_last_fetch = now_ts
                            logger.info("✓ [DATA FETCH] M15 candles cached successfully.")
                        except Exception as e:
                            logger.error("Failed to fetch M15 candles for scalper: %s", e)
                    else:
                        logger.info("💾 [CACHE] Utilizing cached M15 candles (expires in %d seconds).", int(120 - (now_ts - self.df_m15_last_fetch)))

                    # Refresh H1 historical cache if expired (600 sec)
                    if self.df_h1_cache is None or now_ts - self.df_h1_last_fetch > 600:
                        logger.info("📥 [DATA FETCH] Fetching fresh H1 candles for macro trend analysis...")
                        try:
                            self.df_h1_cache = self.oanda.fetch_historical_candles(count=250, granularity="H1")
                            self.df_h1_last_fetch = now_ts
                            logger.info("✓ [DATA FETCH] H1 candles cached successfully.")
                        except Exception as e:
                            logger.error("Failed to fetch H1 candles for scalper: %s", e)
                    else:
                        logger.info("💾 [CACHE] Utilizing cached H1 candles (expires in %d seconds).", int(600 - (now_ts - self.df_h1_last_fetch)))

                    if self.df_m15_cache is None or self.df_h1_cache is None:
                        logger.warning("Waiting for multi-timeframe historical candles...")
                        self.shutdown_event.wait(5)
                        continue

                    # Run Multi-Timeframe Scalper analysis
                    logger.info("📊 [TECHNICAL ANALYSIS] Running calculations: M5 Keltner Channel, M5 RSI, M15 Bollinger Bands, H1 EMA(50)...")
                    scalp_sig = self.ta.analyze_scalping(df, self.df_m15_cache, self.df_h1_cache)
                    direction = scalp_sig.direction

                    logger.info("📈 [INDICATORS] H1 Trend: %s (EMA50: %.2f) | M15 BB Mid: %.2f | M5 RSI: %.1f | Price: %.2f",
                                scalp_sig.h1_trend, scalp_sig.h1_ema, scalp_sig.m15_bb_mid, scalp_sig.m5_rsi, current_price)

                    # Log fundamental check status
                    logger.info("📰 [FUNDAMENTALS] Inspecting news database for active USD/EUR high-impact calendar events...")
                    with self.db._connect() as conn:
                        active_news = conn.execute(
                            "SELECT event_name, currency, impact_level, scheduled_at FROM news_events WHERE datetime(scheduled_at) >= datetime('now', '-4 hours') AND datetime(scheduled_at) <= datetime('now', '+12 hours') ORDER BY scheduled_at ASC LIMIT 3"
                        ).fetchall()
                    if active_news:
                        for row in active_news:
                            logger.info("  ├─ Event: %s (%s) | Impact: %s | Sched: %s", row[0], row[1], row[2], row[3])
                    else:
                        logger.info("  └─ No active high-impact news events in next 12 hours.")

                    if direction is None:
                        status_str = f"Passive ({passive_reason})" if is_passive else "Active"
                        logger.info("⏸ [STATUS] Monitoring [%s]: No scalp triggers met. Main loop sleeping for 15 seconds...\n", status_str)
                    else:
                        logger.info("🔥 [TRIGGER] Scalping signal detected: %s at %.2f", direction, current_price)

                    # Update cognitive state in database
                    import json
                    try:
                        cognitive_state = {
                            "last_updated": datetime.now(timezone.utc).isoformat(),
                            "current_price": round(current_price, 5),
                            "atr_value": round(scalp_sig.m5_rsi, 2),  # Display M5 RSI in ATR card
                            "atr_status": scalp_sig.h1_trend,          # Display H1 Trend status
                            "squeeze_status": f"SCALPING: {direction}" if direction else "MONITORING SCALP",
                            "bb_bandwidth": round(scalp_sig.m5_vwap, 5),
                            "bb_status": "KC BAND",
                            "rsi_value": round(scalp_sig.m5_rsi, 2),
                            "rsi_status": f"H1: {scalp_sig.h1_trend} | M15 BB Mid: {round(scalp_sig.m15_bb_mid, 2)}",
                            "support": round(scalp_sig.m5_vwap_lower, 5),
                            "bb_mid": round(scalp_sig.m5_vwap, 5),
                            "resistance": round(scalp_sig.m5_vwap_upper, 5),
                            "strategy_mode": "MTF_SCALPER",
                            "current_action": f"MONITORING {self.oanda.instrument.replace('_', '/')} (SCALPING)" if not direction else f"SCALP SIGNAL: {direction}!"
                        }
                        self.db.set_config("ai_cognitive_state", json.dumps(cognitive_state))
                    except Exception as e:
                        logger.error("Failed to update AI cognitive state for scalper: %s", e)
                else:
                    # ── Legacy Quantitative Sniper Strategy (M15 Squeeze / Breakout)
                    squeeze_sig = self.ta.analyze(df)
                    direction = self.ta.detect_breakout(current_price, squeeze_sig)

                    # Update cognitive state in database
                    import json
                    try:
                        cognitive_state = {
                            "last_updated": datetime.now(timezone.utc).isoformat(),
                            "current_price": round(current_price, 5),
                            "atr_value": round(squeeze_sig.atr_value, 2),
                            "atr_status": "COMPRESSED" if squeeze_sig.atr_is_compressed else "EXPANDED",
                            "squeeze_status": "COMPRESSED (Squeeze Active)" if squeeze_sig.is_squeeze else ("COMPRESSED (Recent Squeeze)" if squeeze_sig.recent_squeeze else "NO SQUEEZE"),
                            "bb_bandwidth": round(squeeze_sig.bb_bandwidth, 5),
                            "bb_status": "COMPRESSED" if squeeze_sig.bb_is_compressed else "EXPANDED",
                            "rsi_value": round(squeeze_sig.rsi_value, 2),
                            "rsi_status": "NEUTRAL (40-60)" if squeeze_sig.rsi_is_neutral else ("OVERBOUGHT (>60)" if squeeze_sig.rsi_value > 60 else "OVERSOLD (<40)"),
                            "support": round(squeeze_sig.support, 5),
                            "bb_mid": round(squeeze_sig.bb_mid, 5),
                            "resistance": round(squeeze_sig.resistance, 5),
                            "strategy_mode": "BREAKOUT" if squeeze_sig.recent_squeeze else "MEAN_REVERSION",
                            "current_action": f"MONITORING {self.oanda.instrument.replace('_', '/')}" if not direction else f"BREAKOUT DETECTED: {direction}!"
                        }
                        self.db.set_config("ai_cognitive_state", json.dumps(cognitive_state))
                    except Exception as e:
                        logger.error("Failed to update AI cognitive state: %s", e)

                # ── Phase 2: Trade Validation and Filtering ──────────────────
                if direction:
                    is_breakout = squeeze_sig.recent_squeeze if squeeze_sig else False
                    
                    if is_breakout:
                        logger.info("🎯 Breakout detected (%s at %.5f). Running fakeout filter...", direction, current_price)

                        # ── Phase 1.5: Anti-Whipsaw Fakeout Filter (MRD §4.2) ─────
                        breakout_time = time.time()
                        is_fakeout = False
                        while time.time() - breakout_time < 3.0:
                            df_chk = self.oanda.get_latest_dataframe()
                            if not df_chk.empty:
                                live_px = df_chk.iloc[-1]["close"]
                                if direction == "BUY" and live_px < squeeze_sig.support:
                                    is_fakeout = True
                                    break
                                elif direction == "SELL" and live_px > squeeze_sig.resistance:
                                    is_fakeout = True
                                    break
                            self.shutdown_event.wait(0.1)

                        if is_fakeout:
                            freeze_sec = self.cfg["risk"].get("whipsaw_freeze_sec", 1800)
                            logger.warning("⚠️ WHIPSAW FAKEOUT detected! Freezing for %d seconds.", freeze_sec)
                            self.notifier.send(
                                "⚠️ Whipsaw Alert",
                                f"Fakeout on {direction} at {current_price:.5f}. Freeze {freeze_sec//60} min.",
                                "ERROR"
                            )
                            self.shutdown_event.wait(freeze_sec)
                            continue

                        logger.info("✓ Fakeout filter passed. Checking news catalysts...")
                        events = self.scraper.fetch_calendar()
                        catalysts = self.scraper.filter_catalysts(events)
                        
                        catalyst_summary = ""
                        if catalysts:
                            catalyst_summary = " | ".join([f"{c.event_name} ({c.impact})" for c in catalysts])
                            logger.info("📰 Active news catalyst(s): %s", catalyst_summary)
                        else:
                            catalyst_summary = "Technical Breakout (No High-Impact News)"
                            logger.info("📰 No high-impact news catalyst. Proceeding with technical breakout...")
                    else:
                        logger.info("🔄 Mean Reversion/Scalp triggered (%s at %.5f). Skipping breakout filters...", direction, current_price)
                        catalyst_summary = "MTF Scalp (Pullback to Value Zone)" if self.active_mode == "MTF_SCALPER" else "Mean Reversion (Overextended Range)"

                    # ── Phase 3: AI Inference (Kaggle Mini-Debate) ─────────────
                    logger.info("🧠 Initializing AI Brain inference for trade validation...")
                    prompt = self.ai.build_debate_prompt(
                        pair=self.oanda.instrument,
                        direction=direction,
                        entry=current_price,
                        catalyst=catalyst_summary
                    )
                    
                    decision = self.ai.get_decision(prompt)
                    logger.info("🧠 AI Inference decision: %s", decision.get("decision", "HOLD"))

                    # Save debate simulation data for dashboard rendering
                    try:
                        import json
                        debate_data = {
                            "last_updated": datetime.now(timezone.utc).isoformat(),
                            "pair": self.oanda.instrument,
                            "direction": direction,
                            "price": round(current_price, 5),
                            "catalyst": catalyst_summary,
                            "retail_trader_sentiment": decision.get("retail_trader_sentiment", "No debate session run yet."),
                            "institutional_whale_outlook": decision.get("institutional_whale_outlook", "No debate session run yet."),
                            "policymaker_fundamental_view": decision.get("policymaker_fundamental_view", "No debate session run yet."),
                            "risk_assessment": decision.get("risk_assessment", "No evaluation."),
                            "final_sentiment": decision.get("final_sentiment", "NEUTRAL"),
                            "confidence": decision.get("confidence", 0.0),
                            "recommended_direction": decision.get("recommended_direction", "HOLD")
                        }
                        self.db.set_config("latest_market_debate", json.dumps(debate_data))
                    except Exception as e:
                        logger.error("Failed to save latest market debate: %s", e)

                    if decision.get("decision") == "BUY" or decision.get("decision") == "SELL":
                        if is_passive:
                            logger.warning("⚠️ [PASSIVE MODE] Signal %s confirmed by AI, but execution is SKIPPED due to %s.", decision.get("decision"), passive_reason)
                            self.notifier.send(
                                "ℹ️ Passive Signal",
                                f"Signal {decision.get('decision')} at {current_price:.5f} confirmed but skipped ({passive_reason}).",
                                "INFO"
                            )
                            continue

                        # Confirmed by LLM debate! Construct target levels (min 1:2 Risk:Reward)
                        if is_breakout:
                            stop_loss = squeeze_sig.support if direction == "BUY" else squeeze_sig.resistance
                            risk = abs(current_price - stop_loss)
                            take_profit = current_price + (risk * 2.0) if direction == "BUY" else current_price - (risk * 2.0)
                        elif self.active_mode == "MTF_SCALPER":
                            pip_size = 0.1 if "XAU" in self.oanda.instrument else (0.01 if "JPY" in self.oanda.instrument else 0.0001)
                            
                            # Read ATR multipliers from config (default to TP: 1.5, SL: 1.2)
                            tp_mult = self.cfg["strategy"].get("scalping_atr_tp_mult", 1.5)
                            sl_mult = self.cfg["strategy"].get("scalping_atr_sl_mult", 1.2)
                            
                            # Calculate distance based on M5 ATR if available, else fallback to configured static pips
                            m5_atr = scalp_sig.m5_atr if (scalp_sig and hasattr(scalp_sig, "m5_atr")) else 0.0
                            
                            if m5_atr > 0:
                                # Dynamic ATR-based distances
                                # Min target safeguard to prevent spread-eating (min 5 pips = 5 * pip_size)
                                min_tp_distance = 5.0 * pip_size
                                min_sl_distance = 5.0 * pip_size
                                
                                tp_distance = max(tp_mult * m5_atr, min_tp_distance)
                                sl_distance = max(sl_mult * m5_atr, min_sl_distance)
                                
                                logger.info("📐 [DYNAMIC ATR TARGETS] M5 ATR: %.5f | TP Mult: %.1f (Dist: %.5f) | SL Mult: %.1f (Dist: %.5f)",
                                            m5_atr, tp_mult, tp_distance, sl_mult, sl_distance)
                            else:
                                # Fallback to static config pips
                                sl_pips = self.cfg["strategy"].get("scalping_stop_pips", 12.0)
                                tp_pips = self.cfg["strategy"].get("scalping_target_pips", 12.0)
                                tp_distance = tp_pips * pip_size
                                sl_distance = sl_pips * pip_size
                                logger.info("📐 [STATIC TARGETS] Fallback to configured pips — TP Dist: %.5f | SL Dist: %.5f",
                                            tp_distance, sl_distance)
                                
                            if direction == "BUY":
                                stop_loss = current_price - sl_distance
                                take_profit = current_price + tp_distance
                            else:
                                stop_loss = current_price + sl_distance
                                take_profit = current_price - tp_distance
                        else:
                            # Mean Reversion: stop loss is 1.0 * ATR away, take profit is 2.0 * ATR away
                            pip_size = 0.1 if "XAU" in self.oanda.instrument else (0.01 if "JPY" in self.oanda.instrument else 0.0001)
                            atr_price_offset = squeeze_sig.atr_value * pip_size
                            if direction == "BUY":
                                stop_loss = current_price - (1.0 * atr_price_offset)
                                take_profit = current_price + (2.0 * atr_price_offset)
                            else:
                                stop_loss = current_price + (1.0 * atr_price_offset)
                                take_profit = current_price - (2.0 * atr_price_offset)

                        signal_payload = TradeSignal(
                            decision="EXECUTE_TRADE",
                            pair=self.oanda.instrument,
                            direction=direction,
                            entry_price=current_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            confidence=decision.get("confidence", 0.5),
                            news_catalyst=catalyst_summary,
                            timestamp=datetime.now(timezone.utc).isoformat()
                        )
                        
                        logger.warning("🔥 SIGNAL CONFIRMED! Sending to C++ execution engine...")
                        self.zmq.publish_signal(signal_payload)
                        self.notifier.notify_trade(self.oanda.instrument, direction, current_price, stop_loss, take_profit, decision.get("confidence", 0.5))
                        
                        # Record execution time to enforce 10-minute cooldown
                        self.last_trade_time = time.time()
                        continue

                # Heartbeat updated at the start of loop to prevent skips

                # Main loop interval
                self.shutdown_event.wait(15)  # Check every 15 seconds during active sessions

            except Exception as e:
                logger.exception("Unhandled error in main loop: %s", e)
                self.notifier.notify_error(str(e))
                self.shutdown_event.wait(30)

        # Cleanup
        self.oanda.stop_stream()
        self.zmq.close()
        logger.info("Quantelos AI Trader shut down gracefully.")


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    orchestrator = QuantelosOrchestrator()
    orchestrator.run()
