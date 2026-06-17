# =============================================================================
# Quantelos AI Trader — Weekend Self-Training (Reinforcement Learning Loop)
# =============================================================================
# Simulates historical trade setups during market close to keep training the LLM
# agent. Runs predictions, evaluates outcomes against subsequent history,
# applies rewards/penalties, and prompts self-reflection.
# =============================================================================
import time
import logging
import random
import json
import re
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

logger = logging.getLogger("quantelos.weekend_trainer")

class WeekendRLTrainer:
    def __init__(self, db_manager, oanda_client, kaggle_bridge, config: dict):
        self.db = db_manager
        self.oanda = oanda_client
        self.ai = kaggle_bridge
        self.cfg = config
        self._running = False
        self._thread = None

        # Determine instrument and pip size
        self.instrument = self.cfg["oanda"]["instruments"][0]
        self.pip_size = 0.1 if "XAU" in self.instrument else (0.01 if "JPY" in self.instrument else 0.0001)

        # Import strategy
        from strategies import get_strategy
        self.active_mode = self.cfg["strategy"].get("active_mode", "QUANTITATIVE_SNIPER")
        self.strategy = get_strategy(self.active_mode, self.cfg)

        logger.info("Weekend RL Trainer initialized. Instrument: %s, Strategy: %s", 
                    self.instrument, self.active_mode)

    def is_training_window(self) -> bool:
        """Check if we are in the training window (weekend or forced mode)."""
        force_mode = self.cfg.get("kaggle", {}).get("force_weekend_training", False)
        if force_mode:
            return True
        # Saturday = 5, Sunday = 6
        weekday = datetime.now().weekday()
        return weekday in (5, 6)

    def generate_synthetic_candles(self, count: int = 500) -> pd.DataFrame:
        """Generate a realistic synthetic candle series if OANDA API is offline."""
        logger.warning("Generating synthetic candle dataset for weekend training...")
        base_price = 2330.0 if "XAU" in self.instrument else (1.0850 if "EUR" in self.instrument else 1.2500)
        
        candles = []
        current_time = datetime.now(timezone.utc) - timedelta(minutes=count * 5)
        price = base_price

        # Drift and volatility parameters
        dt = 1.0
        mu = 0.00001  # small upward drift
        sigma = 0.0008 if "XAU" in self.instrument else 0.00015
        
        # Merton Jump-Diffusion Parameters (Hell-Mode for XAU/USD)
        # Gold has 'fat tails' — sudden liquidity sweeps and news spikes
        lambda_jump = 0.05 if "XAU" in self.instrument else 0.01  # 5% chance of a jump per M5 candle
        mu_jump = 0.0                                             # Symmetrical jump probability
        sigma_jump = 0.004 if "XAU" in self.instrument else 0.0015 # Jump intensity (~$9-$10 sudden spikes for Gold)

        for i in range(count):
            price_prev = price
            # 1. Baseline Geometric Brownian Motion
            shock = np.random.normal(0, 1)
            
            # 2. Jump Component (Poisson Process)
            jump_occurred = np.random.poisson(lambda_jump * dt)
            jump_multiplier = 1.0
            if jump_occurred > 0:
                jump_size = np.random.normal(mu_jump, sigma_jump)
                jump_multiplier = np.exp(jump_size)
                
            price = price_prev * np.exp((mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * shock) * jump_multiplier
            
            # Form OHLC (Ensure shadows cover the jump)
            low = min(price_prev, price) - abs(np.random.normal(0, sigma * 0.3)) * price_prev
            high = max(price_prev, price) + abs(np.random.normal(0, sigma * 0.3)) * price_prev
            open_p = price_prev
            close_p = price
            
            candles.append({
                "time": current_time,
                "open": round(open_p, 5),
                "high": round(high, 5),
                "low": round(low, 5),
                "close": round(close_p, 5),
                "volume": int(np.random.poisson(100) + 10)
            })
            current_time += timedelta(minutes=5)

        return pd.DataFrame(candles)

    def fetch_historical_dataset(self) -> pd.DataFrame:
        """Fetch real historical candles from OANDA or fallback to synthetic."""
        try:
            # Attempt to fetch M5 candles from OANDA REST API (usually available on weekends)
            logger.info("Fetching M5 candles from OANDA for weekend training setup...")
            df = self.oanda.fetch_historical_candles(count=3000, granularity="M5")
            if not df.empty and len(df) >= 2500:
                return df
        except Exception as e:
            logger.error("Failed to fetch historical candles from OANDA: %s", e)
        
        return self.generate_synthetic_candles(count=3000)

    def select_random_news_event(self) -> str:
        """Fetch a random economic calendar event for simulation context."""
        major_events = [
            "CPI y/y inflation rose to 3.2% (Forecast 3.1%, previous 3.1%). Hawk USD sentiment.",
            "Non-Farm Payrolls reported +215k jobs (Forecast +180k, Unemployment steady at 3.9%). bullish USD.",
            "FOMC Meeting Minutes release: Members discuss holding rates higher for longer to combat sticky inflation.",
            "Retail Sales m/m registered 0.4% increase (Forecast 0.1%). Strong retail spending indicators.",
            "US Unemployment Claims drop to 201k (Forecast 215k), signaling robust labor market tightness.",
            "Eurozone CPI y/y falls to 2.4% (Forecast 2.5%), raising expectations of an ECB interest rate cut.",
            "Flash Manufacturing PMI prints 51.3 (Forecast 50.5), pointing to continuing economic expansion.",
            "No high-impact economic news scheduled for the current session. Technical breakout/pullback setup."
        ]
        
        try:
            with self.db._connect() as conn:
                rows = conn.execute(
                    "SELECT event_name, impact_level, currency, forecast, actual, previous "
                    "FROM news_events ORDER BY RANDOM() LIMIT 1"
                ).fetchone()
                if rows:
                    return f"Economic Event: {rows['event_name']} ({rows['currency']}) | Impact: {rows['impact_level']} | Forecast: {rows['forecast']} | Actual: {rows['actual']} | Previous: {rows['previous']}"
        except Exception as e:
            logger.warning("Could not load news catalyst from database: %s", e)
            
        return random.choice(major_events)

    def run_training_episode(self) -> bool:
        """Executes a single reinforcement learning training episode."""
        logger.info("🎬 Starting weekend training episode...")
        
        # 1. Load data
        df = self.fetch_historical_dataset()
        if df.empty or len(df) < 2500:
            logger.error("Insufficient candles for training.")
            return False

        # 2. Find a technical signal index T
        # We search from index 2500 up to len(df) - 50 to see if any index T triggers a technical setup
        # This focuses training on actionable charts rather than empty ranges.
        target_index = None
        direction = None
        analysis_results = {}
        
        logger.info("Searching historical candles for technical triggers...")
        indices = list(range(2500, len(df) - 50))
        random.shuffle(indices)
        
        for idx in indices[:100]:
            df_slice = df.iloc[:idx].copy()
            
            # Resample timeframes to match strategy requirements (M15, H1)
            # In simulated environment, resample M5 to higher timeframes
            df_m15 = df_slice.resample('15min', on='time').agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
            }).dropna().reset_index()
            
            df_h1 = df_slice.resample('1h', on='time').agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
            }).dropna().reset_index()

            if self.active_mode == "MTF_SCALPER":
                res = self.strategy.analyze(df_slice, df_m15, df_h1)
            else:
                res = self.strategy.analyze(df_slice, None, None)
                
            price = df_slice.iloc[-1]["close"]
            sig = self.strategy.detect_signal(price, res)
            
            if sig:
                target_index = idx
                direction = sig
                analysis_results = res
                logger.info("Found technical trigger: %s at candle index %d (price: %.5f)", direction, idx, price)
                break

        # Fallback to random index T if no technical signal is triggered
        if target_index is None:
            target_index = random.randint(2500, len(df) - 55)
            df_slice = df.iloc[:target_index].copy()
            # Default directional bias
            direction = random.choice(["BUY", "SELL"])
            # Re-run strategy to get signal context
            df_m15 = df_slice.resample('15min', on='time').agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
            }).dropna().reset_index()
            df_h1 = df_slice.resample('1h', on='time').agg({
                'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
            }).dropna().reset_index()
            if self.active_mode == "MTF_SCALPER":
                analysis_results = self.strategy.analyze(df_slice, df_m15, df_h1)
            else:
                analysis_results = self.strategy.analyze(df_slice, None, None)
            logger.info("No technical trigger found. Using random index %d (price: %.5f)", 
                        target_index, df_slice.iloc[-1]["close"])

        # 3. Setup Trade parameters
        entry_price = df.iloc[target_index]["close"]
        stop_loss, take_profit = self.strategy.calculate_targets(entry_price, direction, analysis_results)
        
        # Enforce minimum risk:reward bounds
        if stop_loss == entry_price:
            stop_loss = entry_price - 10 * self.pip_size if direction == "BUY" else entry_price + 10 * self.pip_size
            take_profit = entry_price + 20 * self.pip_size if direction == "BUY" else entry_price - 20 * self.pip_size

        news_catalyst = self.select_random_news_event()

        # 4. Enforce Fail-Fast Circuit Breaker: Verify AI Brain is ALIVE
        try:
            with self.db._connect() as conn:
                hb = conn.execute("SELECT status FROM heartbeat_log WHERE node_name='kaggle_brain' ORDER BY checked_at DESC LIMIT 1").fetchone()
                if not hb or hb['status'] != 'ALIVE':
                    logger.error("🚫 FAIL-FAST: Kaggle AI Brain is %s. Aborting weekend RL episode to prevent local VADER fallback from polluting RL strategy weights.", hb['status'] if hb else 'MISSING')
                    return False
        except Exception as e:
            logger.error("Failed to verify Kaggle heartbeat: %s", e)
            return False

        # 5. Generate AI Swarm Decision
        past_failures = self.db.get_recent_failures(limit=3)
        prompt = self.ai.build_debate_prompt(
            pair=self.instrument,
            direction=direction,
            entry=entry_price,
            catalyst=news_catalyst,
            past_failures=past_failures
        )
        
        logger.info("Querying AI Swarm for training decision...")
        decision = self.ai.get_decision(prompt, broadcast_ui=False)
        ai_action = decision.get("decision", "HOLD")
        confidence = decision.get("confidence", 0.5)
        reasoning = f"Retail: {decision.get('retail_trader_sentiment')}\nWhale: {decision.get('institutional_whale_outlook')}\nPolicy: {decision.get('policymaker_fundamental_view')}\nRisk Assessment: {decision.get('risk_assessment')}"

        # 5. Simulate Market Outcome (up to next 50 candles)
        df_future = df.iloc[target_index + 1 : target_index + 51]
        exit_price = entry_price
        pips_gained = 0.0
        outcome = "NEUTRAL"
        
        hit_tp = False
        hit_sl = False

        for f_idx, row in df_future.iterrows():
            high = row["high"]
            low = row["low"]
            close = row["close"]

            if ai_action == "BUY":
                if low <= stop_loss:
                    hit_sl = True
                    exit_price = stop_loss
                    break
                if high >= take_profit:
                    hit_tp = True
                    exit_price = take_profit
                    break
            elif ai_action == "SELL":
                if high <= stop_loss: # inverted for sell, high price hits stop loss
                    pass
                if high >= stop_loss:
                    hit_sl = True
                    exit_price = stop_loss
                    break
                if low <= take_profit:
                    hit_tp = True
                    exit_price = take_profit
                    break
            exit_price = close

        # Calculate Pips
        if ai_action == "BUY":
            pips_gained = (exit_price - entry_price) / self.pip_size
            if hit_tp:
                outcome = "CORRECT"
            elif hit_sl:
                outcome = "INCORRECT"
            else:
                outcome = "CORRECT" if pips_gained > 0 else "INCORRECT"
        elif ai_action == "SELL":
            pips_gained = (entry_price - exit_price) / self.pip_size
            if hit_tp:
                outcome = "CORRECT"
            elif hit_sl:
                outcome = "INCORRECT"
            else:
                outcome = "CORRECT" if pips_gained > 0 else "INCORRECT"
        else: # HOLD action
            # If the trade would have won, HOLD was incorrect (missed profit).
            # If it would have hit SL, HOLD was correct (saved capital).
            # If it was ranging/flat, HOLD was neutral.
            would_have_won = False
            would_have_lost = False
            
            for f_idx, row in df_future.iterrows():
                high = row["high"]
                low = row["low"]
                # Evaluate based on the strategy direction
                if direction == "BUY":
                    if low <= stop_loss:
                        would_have_lost = True
                        break
                    if high >= take_profit:
                        would_have_won = True
                        break
                elif direction == "SELL":
                    if high >= stop_loss:
                        would_have_lost = True
                        break
                    if low <= take_profit:
                        would_have_won = True
                        break
            
            if would_have_lost:
                outcome = "CORRECT" # saved us from SL
                pips_gained = abs(entry_price - stop_loss) / self.pip_size # positive "saved" value
            elif would_have_won:
                outcome = "INCORRECT" # missed TP
                pips_gained = -abs(take_profit - entry_price) / self.pip_size # negative "missed" value
            else:
                outcome = "NEUTRAL"
                pips_gained = 0.0

        # Calculate Reward/Penalty Metrics
        if outcome == "CORRECT":
            reward_penalty = 1.0 if ai_action != "HOLD" else 0.5
        elif outcome == "INCORRECT":
            reward_penalty = -1.0 if ai_action != "HOLD" else -0.5
        else:
            reward_penalty = 0.2

        logger.info("Result: %s | Net Pips: %.1f | Reward: %.1f", outcome, pips_gained, reward_penalty)

        # 6. Self-Reflection Prompting
        reflection_prompt = f"""You are the Chief Quantitative Strategist conducting a post-trade debrief for an elite algorithmic trading syndicate.
You analyzed a {self.instrument} market structure with a proposed technical entry direction '{direction}' at price {entry_price}.
Macro Catalyst: {news_catalyst}

Your synthesized AI Swarm decision was: {ai_action} (Conviction: {confidence:.2f})
Target Parameters: Take Profit = {take_profit:.5f}, Stop Loss = {stop_loss:.5f}

--- EXECUTION OUTCOME ---
Actual subsequent market price drifted to {exit_price:.5f}.
The simulated execution resulted in: {outcome}
Net PnL: {pips_gained:.1f} pips.
Systemic Reinforcement Reward/Penalty: {reward_penalty:.1f}

Please generate an elite, hyper-analytical quantitative self-reflection detailing:
1. Did the simulated market actors (Retail, Whale, Policymaker) fail or succeed in predicting the liquidity sweep and order-flow? Explain the failure vectors or success factors.
2. Formulate a definitive 'LESSON_LEARNED' for this specific microstructure anomaly (ATR volatility, Bollinger standard deviation, news shock).
3. State precise systemic adjustments required for future filters to maximize Sharpe ratio and mitigate max-drawdown.
4. (AUTORESEARCH) If you quantitatively prove a configuration parameter must be permanently mutated to optimize the algorithm, output EXACTLY: [MUTATE: parameter_name = new_value] (e.g., [MUTATE: scalping_rsi_low = 25]). Only mutate strategy parameters.

Provide your reflection in clean, professional institutional formatting."""

        if outcome == "INCORRECT":
            logger.info("Engaging MiroFish-Inspired ReAct Post-Mortem Agent for deep failure analysis...")
            from react_post_mortem import ReActPostMortemAgent
            context = {
                "instrument": self.instrument,
                "direction": direction,
                "outcome": outcome,
                "pips_gained": pips_gained,
                "ai_reasoning": reasoning,
            }
            try:
                react_agent = ReActPostMortemAgent(self.ai, self.db, context)
                reflection_text = react_agent.run_analysis()
            except Exception as e:
                logger.error(f"ReAct Agent failed: {e}")
                reflection_text = f"Default fallback reflection due to error."
            
            # Extract succinct lesson
            lessons_learned = reflection_text
            lesson_match = re.search(r"LESSON_LEARNED:\s*(.*)", reflection_text, re.IGNORECASE)
            if lesson_match:
                lessons_learned = lesson_match.group(1).strip()
            elif len(lessons_learned) > 300:
                lessons_learned = lessons_learned[:300] + "..."
        else:
            logger.info("Requesting Self-Reflection from remote LLM...")
            reflection_text = self.ai.query_llm(reflection_prompt, temperature=0.7, broadcast_ui=False)
            if not reflection_text:
                reflection_text = f"Default reflection: Simulated trade resulted in {outcome} with {pips_gained:.1f} pips."
                
            lessons_learned = ""
            lines = reflection_text.split("\n")
            lessons_start = False
            for line in lines:
                if "Lesson" in line or "LESSON" in line or "2." in line:
                    lessons_start = True
                if lessons_start:
                    lessons_learned += line + "\n"
            
            if not lessons_learned.strip():
                lessons_learned = reflection_text[:300] + "..."

        # Parse Autoresearch Mutations
        mutation_match = re.search(r"\[MUTATE:\s*([a-zA-Z0-9_]+)\s*=\s*([^\]]+)\]", reflection_text)
        if mutation_match:
            mut_key = mutation_match.group(1).strip()
            mut_val = mutation_match.group(2).strip()
            logger.info("🧬 AUTORESEARCH MUTATION REQUESTED: %s = %s", mut_key, mut_val)
            self._apply_git_mutation(mut_key, mut_val, lessons_learned)

        # 7. Write to SQLite
        try:
            with self.db._connect() as conn:
                conn.execute(
                    """INSERT INTO weekend_training_logs
                       (pair, predicted_direction, entry_price, exit_price, pips_gained,
                        evaluation_result, reward_penalty, ai_reasoning, ai_lessons_learned)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.instrument, ai_action, entry_price, exit_price, pips_gained,
                     outcome, reward_penalty, reasoning, reflection_text)
                )
            logger.info("✓ Weekend training session recorded to database successfully!")
            
            # Apply dynamic RL parameter adaptation
            try:
                # Load current values from DB or config
                db_rsi_low = self.db.get_config("strategy_scalping_rsi_low")
                db_rsi_high = self.db.get_config("strategy_scalping_rsi_high")
                db_vwap_std = self.db.get_config("strategy_scalping_vwap_std")

                current_rsi_low = int(db_rsi_low) if db_rsi_low else self.cfg.get("strategy", {}).get("scalping_rsi_low", 40)
                current_rsi_high = int(db_rsi_high) if db_rsi_high else self.cfg.get("strategy", {}).get("scalping_rsi_high", 60)
                current_vwap_std = float(db_vwap_std) if db_vwap_std else self.cfg.get("strategy", {}).get("scalping_vwap_std", 1.3)

                orig_rsi_low = self.cfg.get("strategy", {}).get("scalping_rsi_low", 30)  # Gold v2.0 defaults
                orig_rsi_high = self.cfg.get("strategy", {}).get("scalping_rsi_high", 70)
                orig_vwap_std = self.cfg.get("strategy", {}).get("scalping_vwap_std", 2.0)

                # Gold v2.0: RL LIVE PARAMETER MODIFICATION DISABLED
                # The RL trainer was corrupting live parameters by modifying them based on
                # random historical index simulations, not real market conditions.
                # Parameters are now locked to config/settings.toml values.
                # This block is preserved for future re-enablement with proper safeguards.
                logger.info("🔒 [RL ADAPTATION BLOCKED] Live parameter modification disabled. "
                           "Outcome: %s | Direction: %s | Current RSI: %d/%d | VWAP Std: %.2f",
                           outcome, direction, current_rsi_low, current_rsi_high, current_vwap_std)
                logger.info("🔒 Parameters locked to config values: RSI [%d, %d] | VWAP Std: %.2f",
                           orig_rsi_low, orig_rsi_high, orig_vwap_std)
                # DISABLED: The following block previously modified live parameters via db.set_config()
                # To re-enable: remove this comment block and restore the if/elif logic below.
                # if outcome == "INCORRECT":
                #     ... (see git history for original implementation)
            except Exception as e:
                logger.error("Failed to apply RL parameter adaptation: %s", e)
            
            # Send Notification
            emoji = "🟢 WIN" if outcome == "CORRECT" else ("🔴 LOSS" if outcome == "INCORRECT" else "🟡 HOLD")
            # Truncate reflection for notification
            refl_summary = reflection_text[:200] + "..." if len(reflection_text) > 200 else reflection_text
            self.send_trainer_notification(ai_action, emoji, pips_gained, reward_penalty, refl_summary)
            return True
        except Exception as e:
            logger.error("Failed to save weekend training log: %s", e)
            return False

    def _apply_git_mutation(self, key: str, value: str, reason: str):
        """Autonomously rewrite settings.toml and perform a Git commit."""
        try:
            config_path = "./config/settings.toml"
            with open(config_path, "r") as f:
                lines = f.readlines()
            
            mutated = False
            for i, line in enumerate(lines):
                # Simple line replacement for TOML
                if line.strip().startswith(f"{key} ") or line.strip().startswith(f"{key}="):
                    # Retain original comment if any
                    comment_part = ""
                    if "#" in line:
                        comment_part = "  #" + line.split("#", 1)[1]
                    lines[i] = f"{key} = {value}{comment_part}\n"
                    mutated = True
                    break
            
            if mutated:
                with open(config_path, "w") as f:
                    f.writelines(lines)
                logger.warning("🧬 Config MUTATED: %s = %s", key, value)
                
                # Commit to Git
                import subprocess
                subprocess.run(["git", "add", config_path], check=True)
                commit_msg = f"Auto-Mutate: {key} = {value}\n\nReason: {reason[:100]}..."
                subprocess.run(["git", "commit", "-m", commit_msg], check=True)
                logger.warning("🧬 Git Commit Successful for %s", key)
                
                self.notifier.send(
                    "🧬 Autoresearch Evolution",
                    f"**Mutated Parameter**: `{key} = {value}`\n**Reason**: {reason[:150]}",
                    "INFO"
                )
            else:
                logger.error("🧬 Mutation failed: Key '%s' not found in settings.toml", key)
        except Exception as e:
            logger.error("🧬 Failed to apply Git mutation: %s", e)

    def send_trainer_notification(self, action: str, emoji: str, pips: float, score: float, reflection: str):
        """Sends a notification to active channels on training run."""
        try:
            # Import notifier
            from notifier import Notifier
            notifier = Notifier(
                discord_webhook=self.cfg["notifications"]["discord_webhook"],
                telegram_token=self.cfg["notifications"]["telegram_bot_token"],
                telegram_chat_id=self.cfg["notifications"]["telegram_chat_id"],
                enabled=self.cfg["notifications"]["enabled"],
            )
            title = f"🧠 AI Weekend Self-Training Run — {emoji}"
            message = (
                f"**Instrument**: `{self.instrument}`\n"
                f"**AI Action**: `{action}`\n"
                f"**Pips Gained**: `{pips:.1f}`\n"
                f"**RL Reward Score**: `{score:.1f}`\n\n"
                f"**AI Reflection Summary**:\n{reflection}"
            )
            notifier.send(title, message, "INFO")
        except Exception as e:
            logger.error("Failed to send trainer notification: %s", e)

    def start(self):
        """Starts the weekend trainer in a background thread."""
        if self._running:
            return
        self._running = True
        import threading
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Weekend Trainer thread started.")

    def stop(self):
        """Stops the weekend trainer background thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            logger.info("Weekend Trainer thread stopped.")

    def _loop(self):
        # Run every 60 seconds (or 30 if forced)
        interval = 60
        while self._running:
            try:
                if self.is_training_window():
                    # Set system mode to weekend training in DB
                    self.db.set_config("system_mode", "WEEKEND_OFFLINE_TRAINING")
                    # Run episode
                    self.run_training_episode()
                else:
                    # Restore default system mode from configuration (DEMO or LIVE)
                    default_mode = self.cfg.get("project", {}).get("mode", "DEMO")
                    self.db.set_config("system_mode", default_mode)
                    logger.debug("Outside weekend training window. Sleeping...")
            except Exception as e:
                logger.error("Error in weekend trainer loop: %s", e)
            
            # Sleep in small increments to respond to shutdown signals
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)
