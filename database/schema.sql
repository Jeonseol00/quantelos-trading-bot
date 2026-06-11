-- =============================================================================
-- Quantelos AI Trader — SQLite3 Physical Schema (WAL Mode)
-- =============================================================================
-- Author  : Muhamad Fikri (Quantelos)
-- Engine  : SQLite3 3.40+ with WAL journaling
-- Host    : ASUS Headless Server (Ubuntu 24.04 LTS)
-- Purpose : Persistent state retention for fault-tolerant autonomous trading
-- =============================================================================

-- ─── PRAGMA CONFIGURATION ────────────────────────────────────────────────────
-- WAL mode allows concurrent reads from Python while C++ writes live ticks.
-- busy_timeout prevents SQLITE_BUSY errors under high-frequency IPC loads.
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- ─── TABLE: system_state ─────────────────────────────────────────────────────
-- Dynamic runtime configuration store (Kaggle URLs, feature flags, etc.)
CREATE TABLE IF NOT EXISTS system_state (
    config_key   TEXT     PRIMARY KEY,
    config_value TEXT     NOT NULL,
    last_updated DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Seed essential configuration keys
INSERT OR IGNORE INTO system_state (config_key, config_value) VALUES
    ('kaggle_ngrok_url',    ''),
    ('system_mode',         'DEMO'),
    ('emergency_halt',      'FALSE'),
    ('max_drawdown_usc',    '180'),
    ('risk_per_trade_pct',  '0.05'),
    ('last_heartbeat_cpp',  ''),
    ('last_heartbeat_py',   '');

-- ─── TABLE: active_positions ─────────────────────────────────────────────────
-- Live position tracking synchronized with OANDA v20 API.
-- The C++ Execution Engine is the SOLE writer to this table.
CREATE TABLE IF NOT EXISTS active_positions (
    trade_id    TEXT    PRIMARY KEY,                -- OANDA Official Trade ID
    pair        TEXT    NOT NULL DEFAULT 'EUR_USD',  -- Instrument identifier
    direction   TEXT    NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    lot_size    REAL    NOT NULL,                    -- Decimal cent lot (e.g., 0.1)
    units       INTEGER NOT NULL,                    -- OANDA native units
    entry_price REAL    NOT NULL,                    -- Actual fill price
    stop_loss   REAL    NOT NULL,                    -- Static SL coordinate
    take_profit REAL    NOT NULL,                    -- Static TP coordinate (RR 1:2)
    status      TEXT    NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'PENDING_CLOSE', 'CLOSED')),
    opened_at   DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_at   DATETIME
);

-- ─── TABLE: trade_logs_evaluation ────────────────────────────────────────────
-- Post-trade cognitive evaluation and P/L recording for AI learning loop.
CREATE TABLE IF NOT EXISTS trade_logs_evaluation (
    log_id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    trade_id           TEXT     NOT NULL REFERENCES active_positions(trade_id),
    usc_profit_loss    REAL     NOT NULL,               -- Realized P/L in US Cents
    pips_gained        REAL,                             -- Net pips movement
    news_trigger       TEXT,                             -- JSON payload of news catalyst
    ai_sentiment       TEXT     CHECK (ai_sentiment IN ('BULLISH_USD', 'BEARISH_USD', 'NEUTRAL', NULL)),
    ai_confidence      REAL     CHECK (ai_confidence BETWEEN 0.0 AND 1.0),
    ai_lessons_learned TEXT,                             -- LLM evaluation extraction
    strategy_tag       TEXT     DEFAULT 'QUANTITATIVE_SNIPER',
    evaluated_at       DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ─── TABLE: news_events ──────────────────────────────────────────────────────
-- Scraped economic calendar events for fundamental catalyst detection.
CREATE TABLE IF NOT EXISTS news_events (
    event_id     INTEGER  PRIMARY KEY AUTOINCREMENT,
    currency     TEXT     NOT NULL CHECK (currency IN ('USD', 'EUR')),
    event_name   TEXT     NOT NULL,                     -- e.g., 'Non-Farm Payrolls'
    impact_level TEXT     NOT NULL CHECK (impact_level IN ('HIGH', 'MEDIUM', 'LOW')),
    forecast     TEXT,
    actual       TEXT,
    previous     TEXT,
    scheduled_at DATETIME NOT NULL,
    scraped_at   DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source       TEXT     DEFAULT 'forex_factory'
);

-- ─── TABLE: heartbeat_log ────────────────────────────────────────────────────
-- Process health monitoring for fault isolation between Python and C++ nodes.
CREATE TABLE IF NOT EXISTS heartbeat_log (
    id         INTEGER  PRIMARY KEY AUTOINCREMENT,
    node_name  TEXT     NOT NULL CHECK (node_name IN ('python_logic', 'cpp_executor', 'kaggle_brain')),
    status     TEXT     NOT NULL CHECK (status IN ('ALIVE', 'TIMEOUT', 'CRASHED')),
    ram_mb     REAL,                                   -- Current RAM usage in MB
    cpu_pct    REAL,                                   -- Current CPU percentage
    checked_at DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ─── INDEXES ─────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_positions_status    ON active_positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_pair      ON active_positions(pair);
CREATE INDEX IF NOT EXISTS idx_logs_trade_id       ON trade_logs_evaluation(trade_id);
CREATE INDEX IF NOT EXISTS idx_news_scheduled      ON news_events(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_heartbeat_node_time ON heartbeat_log(node_name, checked_at);
