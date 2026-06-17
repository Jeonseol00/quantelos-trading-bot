-- =============================================================================
-- Quantelos AI Trader — SQLite3 Physical Schema (WAL Mode)  [v1.1 FIXED]
-- =============================================================================
-- FIXES APPLIED (Audit v1.0):
--   H-09: news_events UNIQUE constraint prevents duplicate scrape insertions
--   M-03: system_state last_updated auto-triggers on UPDATE
--   H-10: heartbeat_log retention policy (7-day auto-prune trigger)
--   NEW:  active_positions reconciliation view for C++ monitor thread
--   NEW:  processed_signals table for deduplication (Audit M-04)
-- =============================================================================

-- ─── PRAGMA CONFIGURATION ────────────────────────────────────────────────────
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- ─── TABLE: system_state ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS system_state (
    config_key   TEXT     PRIMARY KEY,
    config_value TEXT     NOT NULL,
    last_updated DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- FIX M-03: Auto-update last_updated on every write
CREATE TRIGGER IF NOT EXISTS trg_system_state_update
AFTER UPDATE ON system_state
BEGIN
    UPDATE system_state
    SET last_updated = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    WHERE config_key = NEW.config_key;
END;

-- Seed configuration
INSERT OR IGNORE INTO system_state (config_key, config_value) VALUES
    ('kaggle_ngrok_url',    ''),
    ('system_mode',         'DEMO'),
    ('emergency_halt',      'FALSE'),
    ('max_drawdown_usc',    '180'),
    ('risk_per_trade_pct',  '0.05'),
    ('last_heartbeat_cpp',  ''),
    ('last_heartbeat_py',   '');

-- ─── TABLE: active_positions ─────────────────────────────────────────────────
-- Sole writer: C++ Execution Engine
CREATE TABLE IF NOT EXISTS active_positions (
    trade_id    TEXT    PRIMARY KEY,
    pair        TEXT    NOT NULL DEFAULT 'EUR_USD',
    direction   TEXT    NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    lot_size    REAL    NOT NULL,
    units       INTEGER NOT NULL,
    entry_price REAL    NOT NULL,
    stop_loss   REAL    NOT NULL,
    take_profit REAL    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'PENDING_CLOSE', 'CLOSED')),
    opened_at   DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_at   DATETIME
);

-- ─── TABLE: trade_logs_evaluation ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trade_logs_evaluation (
    log_id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    trade_id           TEXT     NOT NULL REFERENCES active_positions(trade_id) ON DELETE RESTRICT,
    usc_profit_loss    REAL     NOT NULL,
    pips_gained        REAL,
    news_trigger       TEXT,
    ai_sentiment       TEXT     CHECK (ai_sentiment IN ('BULLISH_USD', 'BEARISH_USD', 'NEUTRAL', NULL)),
    ai_confidence      REAL     CHECK (ai_confidence BETWEEN 0.0 AND 1.0),
    ai_lessons_learned TEXT,
    strategy_tag       TEXT     DEFAULT 'QUANTITATIVE_SNIPER',
    evaluated_at       DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ─── TABLE: news_events ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS news_events (
    event_id     INTEGER  PRIMARY KEY AUTOINCREMENT,
    currency     TEXT     NOT NULL CHECK (currency IN ('USD', 'EUR')),
    event_name   TEXT     NOT NULL,
    impact_level TEXT     NOT NULL CHECK (impact_level IN ('HIGH', 'MEDIUM', 'LOW')),
    forecast     TEXT,
    actual       TEXT,
    previous     TEXT,
    scheduled_at DATETIME NOT NULL,
    scraped_at   DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source       TEXT     DEFAULT 'forex_factory'
);

-- FIX H-09: Prevent duplicate scrapes of the same event
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_unique_event
    ON news_events(currency, event_name, scheduled_at);

-- ─── TABLE: heartbeat_log ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS heartbeat_log (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    node_name  TEXT     NOT NULL CHECK (node_name IN ('python_logic', 'cpp_executor', 'kaggle_brain')),
    status     TEXT     NOT NULL CHECK (status IN ('ALIVE', 'TIMEOUT', 'CRASHED')),
    ram_mb     REAL,
    cpu_pct    REAL,
    checked_at DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- FIX H-10: Auto-prune heartbeat rows older than 7 days on every new insert
CREATE TRIGGER IF NOT EXISTS trg_heartbeat_prune
AFTER INSERT ON heartbeat_log
BEGIN
    DELETE FROM heartbeat_log
    WHERE checked_at < datetime('now', '-7 days');
END;

-- ─── TABLE: processed_signals ─────────────────────────────────────────────────
-- FIX M-04: Signal deduplication table
-- C++ engine writes a record for every processed signal; checks before executing.
CREATE TABLE IF NOT EXISTS processed_signals (
    signal_id   INTEGER  PRIMARY KEY AUTOINCREMENT,
    signal_key  TEXT     NOT NULL UNIQUE,   -- pair + direction + timestamp hash
    processed_at DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Auto-prune processed signals older than 1 hour (dedup window)
CREATE TRIGGER IF NOT EXISTS trg_signals_prune
AFTER INSERT ON processed_signals
BEGIN
    DELETE FROM processed_signals
    WHERE processed_at < datetime('now', '-1 hour');
END;

-- ─── TABLE: weekend_training_logs ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS weekend_training_logs (
    log_id              INTEGER  PRIMARY KEY AUTOINCREMENT,
    simulated_at        DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    pair                TEXT     NOT NULL,
    predicted_direction TEXT     NOT NULL CHECK (predicted_direction IN ('BUY', 'SELL', 'HOLD')),
    entry_price         REAL     NOT NULL,
    exit_price          REAL     NOT NULL,
    pips_gained         REAL     NOT NULL,
    evaluation_result   TEXT     NOT NULL CHECK (evaluation_result IN ('CORRECT', 'INCORRECT', 'NEUTRAL')),
    reward_penalty      REAL     NOT NULL,
    ai_reasoning        TEXT,
    ai_lessons_learned  TEXT
);

-- ─── INDEXES ─────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_positions_status    ON active_positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_pair      ON active_positions(pair);
CREATE INDEX IF NOT EXISTS idx_logs_trade_id       ON trade_logs_evaluation(trade_id);
CREATE INDEX IF NOT EXISTS idx_news_scheduled      ON news_events(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_heartbeat_node_time ON heartbeat_log(node_name, checked_at);
CREATE INDEX IF NOT EXISTS idx_weekend_training_time ON weekend_training_logs(simulated_at);

-- ─── VIEWS ───────────────────────────────────────────────────────────────────

-- NEW A-01: Position reconciliation view — used by C++ monitor thread
-- Shows OPEN positions with time elapsed since opening (for stale position detection)
CREATE VIEW IF NOT EXISTS v_open_positions_age AS
    SELECT
        trade_id,
        pair,
        direction,
        entry_price,
        stop_loss,
        take_profit,
        opened_at,
        ROUND(
            (julianday('now') - julianday(opened_at)) * 24 * 60, 1
        ) AS age_minutes
    FROM active_positions
    WHERE status = 'OPEN';

-- Recent heartbeat status — last known status per node
CREATE VIEW IF NOT EXISTS v_node_health AS
    SELECT
        node_name,
        status,
        ram_mb,
        cpu_pct,
        checked_at,
        ROUND(
            (julianday('now') - julianday(checked_at)) * 24 * 60, 1
        ) AS minutes_since_last_beat
    FROM heartbeat_log
    GROUP BY node_name
    HAVING checked_at = MAX(checked_at);
