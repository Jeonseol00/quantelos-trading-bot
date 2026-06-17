# =============================================================================
# Quantelos AI Trader — Python Logic Node (Database Manager)
# =============================================================================
# SQLite3 WAL mode manager for persistent state retention.
# This module is the SOLE interface for Python-side database operations.
# =============================================================================
import sqlite3
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger("quantelos.db")


class DatabaseManager:
    """Thread-safe SQLite3 WAL database manager for Quantelos."""

    def __init__(self, db_path: str = "./data/quantelos.db", schema_path: str = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.schema_path = schema_path
        self._init_db()

    def _init_db(self):
        """Initialize database with WAL mode and apply schema if needed."""
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = NORMAL")

            if self.schema_path and Path(self.schema_path).exists():
                schema_sql = Path(self.schema_path).read_text()
                conn.executescript(schema_sql)
                logger.info("Database schema applied from %s", self.schema_path)

    @contextmanager
    def _connect(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── System State Operations ──────────────────────────────────────────────

    def get_config(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT config_value FROM system_state WHERE config_key = ?", (key,)
            ).fetchone()
            return row["config_value"] if row else None

    def set_config(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO system_state (config_key, config_value, last_updated)
                   VALUES (?, ?, ?)
                   ON CONFLICT(config_key) DO UPDATE SET
                     config_value = excluded.config_value,
                     last_updated = excluded.last_updated""",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )

    def is_emergency_halt(self) -> bool:
        return self.get_config("emergency_halt") == "TRUE"

    def trigger_emergency_halt(self):
        self.set_config("emergency_halt", "TRUE")
        logger.critical("EMERGENCY HALT TRIGGERED")

    def clear_stale_rl_params(self):
        """Gold v2.0: Clear any stale RL-adapted parameters from the database.
        Since RL live parameter modification is now disabled, this ensures the
        strategy always uses config/settings.toml as the source of truth."""
        stale_keys = [
            "strategy_scalping_rsi_low",
            "strategy_scalping_rsi_high",
            "strategy_scalping_vwap_std",
        ]
        with self._connect() as conn:
            for key in stale_keys:
                conn.execute("DELETE FROM system_state WHERE config_key = ?", (key,))
                logger.info("🧹 Cleared stale RL param: %s", key)
        logger.info("🧹 All stale RL parameters cleared. Strategy will use config values.")

    # ─── Heartbeat Operations ─────────────────────────────────────────────────

    def record_heartbeat(self, node_name: str, status: str, ram_mb: float = 0, cpu_pct: float = 0):
        # Map statuses to SQLite-compliant CHECK constraint values ('ALIVE', 'TIMEOUT', 'CRASHED')
        status_upper = status.upper()
        if status_upper == "OFFLINE":
            mapped_status = "TIMEOUT"
        elif status_upper in ("ALIVE", "TIMEOUT", "CRASHED"):
            mapped_status = status_upper
        else:
            mapped_status = "TIMEOUT"

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO heartbeat_log (node_name, status, ram_mb, cpu_pct)
                   VALUES (?, ?, ?, ?)""",
                (node_name, mapped_status, ram_mb, cpu_pct),
            )
        if node_name == "python_logic":
            self.set_config("last_heartbeat_py", datetime.now(timezone.utc).isoformat())

    # ─── News Events Operations ───────────────────────────────────────────────

    def insert_news_event(self, currency: str, event_name: str, impact: str,
                          forecast: str, actual: str, previous: str,
                          scheduled_at: str, source: str = "forex_factory"):
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO news_events
                   (currency, event_name, impact_level, forecast, actual, previous, scheduled_at, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (currency, event_name, impact, forecast, actual, previous, scheduled_at, source),
            )

    def get_upcoming_high_impact(self, hours_ahead: int = 4) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM news_events
                   WHERE impact_level = 'HIGH'
                     AND scheduled_at > datetime('now')
                     AND scheduled_at < datetime('now', '+' || ? || ' hours')
                   ORDER BY scheduled_at ASC""",
                (hours_ahead,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Trade Log Operations ─────────────────────────────────────────────────

    def log_trade_evaluation(self, trade_id: str, pnl_usc: float, pips: float,
                             news_trigger: str, sentiment: str, confidence: float,
                             lessons: str):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO trade_logs_evaluation
                   (trade_id, usc_profit_loss, pips_gained, news_trigger,
                    ai_sentiment, ai_confidence, ai_lessons_learned)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (trade_id, pnl_usc, pips, news_trigger, sentiment, confidence, lessons),
            )

    def get_performance_summary(self) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT
                     COUNT(*) as total_trades,
                     SUM(CASE WHEN usc_profit_loss > 0 THEN 1 ELSE 0 END) as wins,
                     SUM(CASE WHEN usc_profit_loss <= 0 THEN 1 ELSE 0 END) as losses,
                     SUM(usc_profit_loss) as total_pnl_usc,
                     AVG(usc_profit_loss) as avg_pnl_usc,
                     MAX(usc_profit_loss) as best_trade,
                     MIN(usc_profit_loss) as worst_trade
                   FROM trade_logs_evaluation"""
            ).fetchone()
            d = dict(row)
            total = d["total_trades"] or 0
            wins = d["wins"] or 0
            d["win_rate"] = (wins / total * 100) if total > 0 else 0.0
            return d

    def get_recent_failures(self, limit: int = 3) -> list[dict]:
        """Fetch historical trading and training failures to provide as negative reinforcement context."""
        with self._connect() as conn:
            try:
                rows = conn.execute("""
                    SELECT 
                        pair, 
                        pips_gained, 
                        ai_lessons_learned,
                        simulated_at as timestamp,
                        'SIMULATION' as type
                    FROM weekend_training_logs
                    WHERE evaluation_result = 'INCORRECT' AND ai_lessons_learned IS NOT NULL AND ai_lessons_learned != ''
                    
                    UNION ALL
                    
                    SELECT 
                        news_trigger as pair, 
                        pips_gained, 
                        ai_lessons_learned,
                        evaluated_at as timestamp,
                        'LIVE_TRADE' as type
                    FROM trade_logs_evaluation
                    WHERE usc_profit_loss < 0 AND ai_lessons_learned IS NOT NULL AND ai_lessons_learned != ''
                    
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,)).fetchall()
                return [dict(r) for r in rows]
            except Exception as e:
                logger.error("Failed to query recent failures: %s", e)
                return []
