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
import queue
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

class TradingState(Enum):
    MONITORING = "MONITORING"
    AWAITING_AI = "AWAITING_AI"
    PENDING_ORDER = "PENDING_ORDER"
    OPEN_TRADE = "OPEN_TRADE"
    HALTED = "HALTED"

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
from news_scraper import NewsScraper
from kaggle_bridge import KaggleBridge
from notifier import Notifier
from oanda_client import OANDAClient
from vector_memory import VectorMemory

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
        self.event_bus = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.state = TradingState.MONITORING

        self.db = DatabaseManager(
            db_path=self.cfg["database"]["path"],
            schema_path="./database/schema.sql",
        )
        self.vector_mem = VectorMemory()
        # Gold v2.0: Clear any stale RL-adapted parameters from previous runs
        self.db.clear_stale_rl_params()
        self.zmq = ZMQPublisher(
            ipc_path=self.cfg["zmq"]["ipc_path"],
            protocol=self.cfg["zmq"]["protocol"],
        )
        from strategies import get_strategy
        self.active_mode = self.cfg["strategy"].get("active_mode", "QUANTITATIVE_SNIPER")
        self.strategy = get_strategy(self.active_mode, self.cfg)
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
            granularity=self.cfg["strategy"]["timeframe"],
            verify_ssl=self.cfg.get("oanda", {}).get("verify_ssl", False),
            event_bus=self.event_bus
        )

        from weekend_trainer import WeekendRLTrainer
        self.weekend_trainer = WeekendRLTrainer(
            db_manager=self.db,
            oanda_client=self.oanda,
            kaggle_bridge=self.ai,
            config=self.cfg
        )

        self.df_m15_cache = None
        self.df_m15_last_fetch = 0
        self.df_h1_cache = None
        self.df_h1_last_fetch = 0
        self.last_trade_time = 0
        self.kaggle_consecutive_failures = 0
        self.kaggle_is_healthy = True
        self.kaggle_disconnect_start_time = None
        self.kaggle_alert_sent = False

        # Gold v2.0: Loss-streak tracking (anti-overtrading)
        self.consecutive_losses = 0
        self.loss_streak_freeze_until = 0  # timestamp until trading is frozen
        self.max_consecutive_losses = self.cfg["risk"].get("max_consecutive_losses", 3)
        self.loss_streak_freeze_sec = self.cfg["risk"].get("loss_streak_freeze_sec", 1800)

        # Gold v2.0: AI confidence threshold from config
        self.min_ai_confidence = self.cfg["kaggle"].get("min_ai_confidence", 0.60)
        self.block_trade_on_ai_failure = self.cfg["kaggle"].get("block_trade_on_ai_failure", True)

        self._running = True
        self.shutdown_event = threading.Event()
        self._setup_signal_handlers()
        logger.info("All subsystems initialized. Mode: %s | Min AI Confidence: %.2f", self.cfg["project"]["mode"], self.min_ai_confidence)

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

    def _is_loss_streak_frozen(self) -> bool:
        """Check if trading is frozen due to consecutive loss streak."""
        if time.time() < self.loss_streak_freeze_until:
            remaining = int(self.loss_streak_freeze_until - time.time())
            logger.warning("❄️ LOSS-STREAK FREEZE active: %d consecutive losses. %ds remaining.",
                          self.consecutive_losses, remaining)
            return True
        # Reset freeze if it has expired
        if self.loss_streak_freeze_until > 0 and time.time() >= self.loss_streak_freeze_until:
            logger.info("✅ Loss-streak freeze expired. Resuming trading.")
            self.loss_streak_freeze_until = 0
        return False

    def _check_spread(self) -> bool:
        """Check if current spread is within acceptable bounds for Gold scalping.
        Returns True if spread is OK (trade allowed), False if too wide."""
        max_spread_pips = self.cfg["strategy"].get("max_spread_pips", 8.0)
        instrument = self.cfg["oanda"]["instruments"][0]
        pip_size = 0.1 if "XAU" in instrument else (0.01 if "JPY" in instrument else 0.0001)
        
        try:
            # Fetch current bid/ask from OANDA pricing stream
            df = self.oanda.get_latest_dataframe()
            if df.empty or len(df) < 2:
                return True  # Allow if we can't check
            
            # Use the last tick's OHLC to estimate spread proxy (high-low of last candle as proxy)
            # For real spread, we'd need bid/ask from pricing stream
            url = f"{self.oanda.api_url}/v3/accounts/{self.oanda.account_id}/pricing?instruments={instrument}"
            import requests
            r = requests.get(url, headers=self.oanda.headers, timeout=5, verify=self.oanda._ssl)
            r.raise_for_status()
            data = r.json()
            
            if data.get("prices") and len(data["prices"]) > 0:
                price_obj = data["prices"][0]
                bid = float(price_obj["bids"][0]["price"])
                ask = float(price_obj["asks"][0]["price"])
                spread = ask - bid
                spread_pips = spread / pip_size
                
                if spread_pips > max_spread_pips:
                    logger.warning("🛡️ SPREAD CHECK BLOCKED: Spread %.1f pips > max %.1f pips (bid: %.2f, ask: %.2f)",
                                  spread_pips, max_spread_pips, bid, ask)
                    return False
                else:
                    logger.info("✓ Spread OK: %.1f pips (max: %.1f)", spread_pips, max_spread_pips)
                    return True
        except Exception as e:
            logger.warning("Spread check failed (allowing trade): %s", e)
            return True  # Fail-open: don't block trade if spread check errors

    def _record_health(self):
        """Record system health metrics."""
        proc = psutil.Process()
        ram_mb = proc.memory_info().rss / (1024 * 1024)
        cpu_pct = proc.cpu_percent(interval=0.1)
        self.db.record_heartbeat("python_logic", "ALIVE", ram_mb, cpu_pct)
        self.zmq.publish_heartbeat()

        # Check Kaggle AI Swarm status and record its heartbeat
        is_alive = self.ai.ping_health_check()
                
        if is_alive:
            self.db.record_heartbeat("kaggle_brain", "ALIVE", 0, 0)
            self.kaggle_consecutive_failures = 0
            if not self.kaggle_is_healthy:
                logger.info("🟢 Kaggle AI Brain connection RESTORED.")
                self.notifier.send(
                    "🟢 Kaggle AI Brain Connected",
                    "Kaggle AI Brain connection has been restored. System returning to normal operations.",
                    "INFO"
                )
            self.kaggle_is_healthy = True
            self.kaggle_disconnect_start_time = None
            self.kaggle_alert_sent = False
        else:
            self.db.record_heartbeat("kaggle_brain", "TIMEOUT", 0, 0)
            self.kaggle_consecutive_failures += 1
            logger.warning("Kaggle AI Brain heartbeat timeout. Consecutive failures: %d", self.kaggle_consecutive_failures)
            if self.kaggle_consecutive_failures >= 3:
                self.kaggle_is_healthy = False
                if self.kaggle_disconnect_start_time is None:
                    self.kaggle_disconnect_start_time = time.time()
                if not self.kaggle_alert_sent:
                    logger.error("🚫 Kaggle AI Brain connection lost for 3 minutes. Entering Emergency Freeze mode.")
                    self.notifier.send(
                        "🚨 Kaggle AI Brain Disconnected",
                        "Kaggle AI Brain connection lost for 3 minutes. Entering Emergency Freeze mode (new entries blocked). Grace period monitoring active.",
                        "ERROR"
                    )
                    self.kaggle_alert_sent = True

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

            # Check if Kaggle AI brain disconnection exceeded grace period
            grace_period = self.cfg["kaggle"].get("disconnect_grace_sec", 600)
            if not self.kaggle_is_healthy and self.kaggle_disconnect_start_time is not None:
                downtime = time.time() - self.kaggle_disconnect_start_time
                if downtime >= grace_period:
                    logger.error("🚨 EMERGENCY: Kaggle AI Brain disconnected for %.1fs (>= %ds grace period). Closing all open positions!", downtime, grace_period)
                    self.notifier.send(
                        "🚨 Kaggle Connection Lost — Emergency Liquidate",
                        f"Kaggle AI Brain has been disconnected for {downtime:.1f}s. Liquidating all active positions for safety.",
                        "ERROR"
                    )
                    # Close all open positions on OANDA
                    for pos in db_positions:
                        trade_id = pos["trade_id"]
                        logger.info("Closing trade %s on OANDA due to Kaggle disconnection...", trade_id)
                        self.oanda.close_trade(trade_id)

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
                        # Gold v2.0: Use actual active strategy tag, not hardcoded
                        strategy_tag = self.active_mode if hasattr(self, 'active_mode') else 'MTF_SCALPER'
                        conn.execute(
                            """INSERT INTO trade_logs_evaluation
                               (trade_id, usc_profit_loss, pips_gained, strategy_tag)
                               VALUES (?, ?, ?, ?)""",
                            (trade_id, realized_pl_cents, pips, strategy_tag)
                        )

                    # Gold v2.0: Track consecutive losses for loss-streak freeze
                    if realized_pl < 0:
                        self.consecutive_losses += 1
                        logger.warning("📊 Consecutive losses: %d/%d", self.consecutive_losses, self.max_consecutive_losses)
                        if self.consecutive_losses >= self.max_consecutive_losses:
                            self.loss_streak_freeze_until = time.time() + self.loss_streak_freeze_sec
                            freeze_min = self.loss_streak_freeze_sec // 60
                            logger.error("❄️ LOSS-STREAK FREEZE TRIGGERED: %d consecutive losses. Freezing for %d minutes.",
                                        self.consecutive_losses, freeze_min)
                            self.notifier.send(
                                "❄️ Loss-Streak Freeze",
                                f"{self.consecutive_losses} consecutive losses. Trading frozen for {freeze_min} minutes.",
                                "ERROR"
                            )
                    else:
                        if self.consecutive_losses > 0:
                            logger.info("📊 Win resets loss streak (was %d consecutive losses)", self.consecutive_losses)
                        self.consecutive_losses = 0

                    result_emoji = "🟢 WIN" if realized_pl > 0 else "🔴 LOSS"
                    self.notifier.send(
                        f"Trade Closed — {result_emoji}",
                        f"ID: `{trade_id}` | P/L: `{realized_pl:.2f}` | Pips: `{pips:.1f}`",
                        "TRADE"
                    )
                    logger.info("Recorded closed trade %s: P/L=%.2f, Pips=%.1f", trade_id, realized_pl, pips)
                    
                    # Trigger Background Post-Mortem
                    threading.Thread(
                        target=self._run_live_post_mortem,
                        args=(trade_id, pair, pos.get("direction"), pips, realized_pl, realized_pl > 0),
                        daemon=True
                    ).start()
        except Exception as e:
            logger.error("Trade monitor error: %s", e)

    def _cognitive_pre_flight_check(self, direction: str, current_price: float, catalyst_summary: str) -> bool:
        """
        Phase 2.5: Cognitive Memory Pre-Flight Audit (TradeMemory Protocol)
        Queries ChromaDB Vector RAG for semantically similar past failures.
        Blocks the trade if the AI's past lessons indicate a high risk of repeating a mistake.
        Returns True if the trade is ALLOWED, False if BLOCKED.
        """
        try:
            logger.info("🧠 [MEMORY] Running Pre-Flight Cognitive Audit (Vector RAG) for %s...", direction)
            
            # Formulate the current context to search for
            current_context = f"Direction: {direction} | Price: {current_price} | Catalyst: {catalyst_summary}"
            
            # Semantic search for top 3 similar failures
            similar_failures = self.vector_mem.search_similar_failures(current_context, n_results=3)
            
            if not similar_failures:
                logger.info("  └─ No semantic historical memory for this setup. Trade ALLOWED.")
                return True
                
            logger.warning("  └─ ⚠️ Found %d similar historical failures in Vector Memory!", len(similar_failures))
            
            # Simple thresholding based on semantic failures
            # If we find 2 or more semantic failures, block the trade to prevent repeating mistakes.
            if len(similar_failures) >= 2:
                logger.error("🚫 COGNITIVE BLOCK: Semantically similar setups failed previously. Aborting trade.")
                for f in similar_failures:
                    logger.error("   - Recall: %s", f['semantic_lesson'])
                return False

            logger.info("  └─ Similarity threshold not reached (Safe to Proceed). Trade ALLOWED.")
            return True

        except Exception as e:
            logger.error("Cognitive Audit Error: %s", e)
            return True  # Fail-open if Vector RAG is down

    def _run_live_post_mortem(self, trade_id, pair, direction, pips, realized_pl, is_win):
        """Run post-mortem AI reflection in background thread so it doesn't block live trading."""
        try:
            import re
            outcome = "CORRECT" if is_win else "INCORRECT"
            logger.info("🧠 Spawning background thread for Live Trade Post-Mortem (%s)...", outcome)
            
            if is_win:
                reflection_prompt = f"""You are a Quantitative AI. You just WON a live trade.
Pair: {pair} | Direction: {direction} | Pips Gained: {pips:.1f}
Provide a brief analysis of why this was successful and formulate a clear LESSON_LEARNED."""
                reflection_text = self.ai.query_llm(reflection_prompt, temperature=0.7, broadcast_ui=False)
                if not reflection_text:
                    reflection_text = f"Live trade WON with {pips:.1f} pips."
            else:
                from react_post_mortem import ReActPostMortemAgent
                context = {
                    "instrument": pair,
                    "direction": direction,
                    "outcome": outcome,
                    "pips_gained": pips,
                    "ai_reasoning": "Live Trade Execution",
                }
                react_agent = ReActPostMortemAgent(self.ai, self.db, context)
                reflection_text = react_agent.run_analysis()
                if not reflection_text:
                    reflection_text = f"Live trade LOST with {pips:.1f} pips."

            with self.db._connect() as conn:
                conn.execute(
                    "UPDATE trade_logs_evaluation SET ai_lessons_learned = ? WHERE trade_id = ?",
                    (reflection_text, trade_id)
                )
                
            # Embed Lesson into Vector RAG Memory (ChromaDB)
            if not is_win:
                context_str = f"Direction: {direction} | Pair: {pair}"
                self.vector_mem.store_lesson(
                    trade_id=str(trade_id),
                    context=context_str,
                    lesson=reflection_text,
                    metadata={"source": "live_trade", "pnl": float(realized_pl)}
                )

            logger.info("✅ Live Post-Mortem completed and saved for trade %s", trade_id)
        except Exception as e:
            logger.error("Failed to run Live Post-Mortem for trade %s: %s", trade_id, e)

    def _run_ai_inference_task(self, direction, current_price, catalyst_summary, is_passive, passive_reason, analysis_results):
        """Phase 3: AI Inference executed in a non-blocking thread."""
        try:
            logger.info("🧠 Initializing AI Brain inference for trade validation (Non-Blocking)...")
            past_failures = self.db.get_recent_failures(limit=3)
            prompt = self.ai.build_debate_prompt(
                pair=self.oanda.instrument,
                direction=direction,
                entry=current_price,
                catalyst=catalyst_summary,
                past_failures=past_failures
            )
            
            ai_start_time = time.time()
            decision = self.ai.get_decision(prompt)
            ai_duration = time.time() - ai_start_time
            logger.info("🧠 AI Inference decision: %s (took %.1fs)", decision.get("decision", "HOLD"), ai_duration)

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

            ai_decision = decision.get("decision", "HOLD")
            ai_confidence = decision.get("confidence", 0.0)

            min_conf = self.min_ai_confidence
            if (ai_decision == "BUY" or ai_decision == "SELL") and ai_confidence >= min_conf:
                if is_passive:
                    logger.warning("⚠️ [PASSIVE MODE] Signal %s confirmed by AI (conf: %.2f), but execution is SKIPPED due to %s.", ai_decision, ai_confidence, passive_reason)
                    self.notifier.send(
                        "ℹ️ Passive Signal",
                        f"Signal {ai_decision} at {current_price:.5f} confirmed (conf: {ai_confidence:.2f}) but skipped ({passive_reason}).",
                        "INFO"
                    )
                    return

                # Staleness check
                staleness_sec = self.cfg["strategy"].get("staleness_check_sec", 60)
                if ai_duration > staleness_sec:
                    logger.warning("⏱️ STALENESS CHECK: AI took %.1fs (threshold: %ds). Re-fetching price...", ai_duration, staleness_sec)
                    df_fresh = self.oanda.get_latest_dataframe()
                    if not df_fresh.empty:
                        fresh_price = df_fresh.iloc[-1]["close"]
                        price_drift = abs(fresh_price - current_price)
                        pip_size = 0.1 if "XAU" in self.oanda.instrument else (0.01 if "JPY" in self.oanda.instrument else 0.0001)
                        drift_pips = price_drift / pip_size
                        max_drift_pips = self.cfg["strategy"].get("max_staleness_drift_pips", 30.0)
                        
                        if drift_pips > max_drift_pips:
                            logger.warning("🛡️ STALENESS BLOCKED: Price drifted %.1f pips (%.5f → %.5f) during AI inference. Skipping.", drift_pips, current_price, fresh_price)
                            return
                        else:
                            logger.info("✅ Staleness check passed: %.1f pips drift (max: %.1f). Using fresh price.", drift_pips, max_drift_pips)
                            current_price = fresh_price

                if not self._check_spread():
                    logger.warning("🛡️ SPREAD CHECK FAILED. Skipping trade execution.")
                    return

                stop_loss, take_profit = self.strategy.calculate_targets(current_price, direction, analysis_results)
                logger.info("📐 [GOLD TARGETS] TP: %.5f | SL: %.5f | Strategy: %s", take_profit, stop_loss, self.active_mode)

                signal_payload = TradeSignal(
                    decision="EXECUTE_TRADE",
                    pair=self.oanda.instrument,
                    direction=direction,
                    entry_price=current_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=ai_confidence,
                    news_catalyst=catalyst_summary,
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
                
                logger.warning("🔥 SIGNAL CONFIRMED! [%s] %s @ %.5f | SL: %.5f | TP: %.5f | Conf: %.2f | Strategy: %s",
                              direction, self.oanda.instrument, current_price, stop_loss, take_profit, ai_confidence, self.active_mode)
                self.zmq.publish_signal(signal_payload)
                self.notifier.notify_trade(self.oanda.instrument, direction, current_price, stop_loss, take_profit, ai_confidence)
                self.last_trade_time = time.time()
            elif ai_decision in ("BUY", "SELL"):
                logger.warning("⚠️ [LOW CONFIDENCE] AI suggested %s but confidence (%.2f) was below the %.2f threshold. Skipping execution.", ai_decision, ai_confidence, min_conf)
        except Exception as e:
            logger.error("AI inference task failed: %s", e)
        finally:
            self.state = TradingState.MONITORING

    def run(self):
        """Main autonomous trading loop."""
        logger.info("Bootstrapping historical candles for Technical Analysis...")
        try:
            self.oanda.fetch_historical_candles(500)
        except Exception as e:
            logger.error("Initial bootstrap failed, retrying on loop. Error: %s", e)

        logger.info("Starting OANDA live price streaming...")
        self.oanda.start_stream()

        logger.info("Starting Weekend RL Trainer thread...")
        self.weekend_trainer.start()

        logger.info("Starting main trading loop...")
        heartbeat_interval = 60  # seconds
        last_heartbeat = 0
        last_news_scrape = 0
        last_analysis_time = 0
        analysis_interval = 15

        while self._running:
            try:
                # ── Event Bus ──────────────────────────────────────────────────
                try:
                    event = self.event_bus.get(timeout=1.0)
                    if event[0] == "TICK":
                        tick_time, mid_price = event[1], event[2]
                        # In the future, we can update real-time trailing stops here!
                except queue.Empty:
                    pass

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

                # Gold v2.0: Asia session ALWAYS blocked for Gold (low liquidity = false signals)
                allow_asia = (self.active_mode == "MTF_SCALPER" and self.cfg["strategy"].get("allow_asia_scalping", False))
                is_loss_frozen = self._is_loss_streak_frozen()
                is_passive = is_halted or is_blocked or is_news_blocked or (is_asia and not allow_asia) or (not self.kaggle_is_healthy) or is_loss_frozen
                passive_reason = None
                if is_halted:
                    passive_reason = "EMERGENCY HALT"
                elif is_blocked:
                    passive_reason = "MIDNIGHT BLOCK"
                elif is_news_blocked:
                    passive_reason = "HIGH-IMPACT NEWS BLOCK"
                elif is_asia and not allow_asia:
                    passive_reason = "ASIA SESSION BLOCKED (Gold: low liquidity)"
                elif not self.kaggle_is_healthy:
                    passive_reason = "KAGGLE DISCONNECTED (EMERGENCY FREEZE)"
                elif is_loss_frozen:
                    remaining_min = int((self.loss_streak_freeze_until - time.time()) / 60)
                    passive_reason = f"LOSS-STREAK FREEZE ({self.consecutive_losses} losses, {remaining_min}min remaining)"

                if is_passive:
                    logger.debug("System operating in passive monitoring mode due to: %s", passive_reason)

                # Enforce cooldown period between trade signals
                cooldown_sec = self.cfg["strategy"].get("cooldown_sec", 120 if self.active_mode == "MTF_SCALPER" else 600)
                time_since_last = time.time() - self.last_trade_time
                if time_since_last < cooldown_sec:
                    continue
                    
                # State Machine Gate
                if self.state != TradingState.MONITORING:
                    continue

                # Throttle technical analysis to save CPU
                if time.time() - last_analysis_time < analysis_interval:
                    continue
                last_analysis_time = time.time()

                # ── Phase 1: Technical Analysis & Signal Detection ──────────
                logger.info("🔍 [TIMEFRAME CHECK] Fetching M5 candledata from OANDA...")
                df = self.oanda.get_latest_dataframe()
                if df.empty or len(df) < 30:
                    logger.warning("Waiting for OANDA historical candles to bootstrap...")
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

                    logger.info("📊 [TECHNICAL ANALYSIS] Running decoupled MTF Scalper strategy calculations...")
                    analysis_results = self.strategy.analyze(df, self.df_m15_cache, self.df_h1_cache)
                    scalp_sig = analysis_results.get("signal")
                    direction = self.strategy.detect_signal(current_price, analysis_results)

                    if scalp_sig:
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
                        if scalp_sig:
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
                    # ── Legacy Quantitative Sniper Strategy (M15 Squeeze / Breakout) using the decoupled strategy
                    analysis_results = self.strategy.analyze(df, self.df_m15_cache, self.df_h1_cache)
                    squeeze_sig = analysis_results.get("signal")
                    direction = self.strategy.detect_signal(current_price, analysis_results)

                    # Update cognitive state in database
                    import json
                    try:
                        if squeeze_sig:
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

                    # ── Phase 2.5: Cognitive Memory Pre-Flight Check ───────────
                    if not self._cognitive_pre_flight_check(direction, current_price, catalyst_summary):
                        logger.warning("🛡️ PRE-FLIGHT CHECK FAILED. Memory indicates high risk of failure. Aborting trade.")
                        self.last_trade_time = time.time()  # Enforce cooldown
                        continue

                    # ── Phase 3: AI Inference (Kaggle Mini-Debate) ─────────────
                    self.state = TradingState.AWAITING_AI
                    self.executor.submit(self._run_ai_inference_task, direction, current_price, catalyst_summary, is_passive, passive_reason, analysis_results)

            except Exception as e:
                logger.exception("Unhandled error in main loop: %s", e)
                self.notifier.notify_error(str(e))
                self.shutdown_event.wait(5)

        # Cleanup
        self.weekend_trainer.stop()
        self.oanda.stop_stream()
        self.zmq.close()
        logger.info("Quantelos AI Trader shut down gracefully.")


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    orchestrator = QuantelosOrchestrator()
    orchestrator.run()
