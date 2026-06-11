# Quantelos AI Trader v1.0

> **Autonomous Multi-Agent Algorithmic Trading System for Forex (EUR/USD)**
> 
> Classification: Sangat Rahasia / Internal

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    QUANTELOS AI TRADER v1.0                        │
├─────────────────────┬───────────────────────┬───────────────────────┤
│   HP Laptop (GUI)   │  ASUS Server (Exec)   │  Kaggle Cloud (AI)   │
│   ───────────────   │  ────────────────────  │  ─────────────────   │
│   Odysseus GUI      │  Python Logic Node     │  Qwen-2.5 LLM       │
│   Price Charts      │  ├─ Technical Analyzer │  Mini-Debate Prompt  │
│   Telemetry         │  ├─ News Scraper       │  Ngrok Tunnel        │
│   Discord/TG Alerts │  ├─ Kaggle Bridge      │                      │
│                     │  ├─ ZMQ Publisher ──────┤──→ C++ Executor      │
│                     │  └─ SQLite3 WAL DB     │                      │
│                     │      ↕                  │                      │
│                     │  C++ Execution Engine   │                      │
│                     │  ├─ RiskGatekeeper      │                      │
│                     │  ├─ ZMQ Subscriber      │                      │
│                     │  └─ OANDA v20 API ──────┤──→ Forex Market      │
└─────────────────────┴───────────────────────┴───────────────────────┘
```

## Quick Start

### 1. ASUS Headless Server Setup
```bash
sudo bash scripts/setup_asus.sh
```

### 2. Configure Credentials
Edit `config/settings.toml` with your:
- OANDA v20 API token and account ID
- Discord webhook / Telegram bot token
- Kaggle ngrok token
- Supabase URL and key

### 3. Run TDD Tests
```bash
cd cpp_engine
g++ -std=c++17 -I./include tests/tdd_suite.cpp -o tdd_runner && ./tdd_runner
```

### 4. Start Python Logic Node
```bash
source .venv/bin/activate
python3 python_node/main.py
```

### 5. Start Web Dashboard
```bash
source .venv/bin/activate
python3 python_node/dashboard.py
```
Access the premium monitoring suite at `http://localhost:8050` from your browser.

### 6. Deploy Kaggle Inference
Upload `kaggle/inference_notebook.py` to Kaggle Notebooks with GPU enabled.

## Project Structure

```
quantelos/
├── config/settings.toml          # Master configuration (BRD/MRD params)
├── cpp_engine/
│   ├── include/
│   │   ├── risk_gatekeeper.hpp   # Hardcoded C++ safety gate
│   │   └── zmq_subscriber.hpp   # ZeroMQ IPC subscriber
│   ├── src/main.cpp              # C++ execution engine entry point
│   ├── tests/tdd_suite.cpp       # TDD fatal scenario tests
│   └── CMakeLists.txt            # CMake build configuration
├── python_node/
│   ├── main.py                   # Main orchestrator loop
│   ├── technical_analyzer.py     # ATR/BB/RSI squeeze detector
│   ├── news_scraper.py           # Multi-layer scraper (L1/L2/L3)
│   ├── kaggle_bridge.py          # LLM Mini-Debate inference
│   ├── zmq_publisher.py          # ZeroMQ IPC publisher
│   ├── db_manager.py             # SQLite3 WAL database manager
│   ├── backtester.py             # Historical strategy validator
│   └── notifier.py               # Discord/Telegram alerts
├── kaggle/
│   └── inference_notebook.py     # Kaggle LLM deployment script
├── database/schema.sql           # SQLite3 database schema
├── scripts/setup_asus.sh         # One-command server setup
└── requirements.txt              # Python dependencies
```

## Risk Management (Hardcoded in C++)

| Parameter | Value | Source |
|---|---|---|
| Max Risk Per Trade | 5% of equity | BRD §2 |
| Max Drawdown (MDD) | 15% (180 USC) | BRD §2 |
| Risk:Reward Ratio | 1:2 minimum | BRD §2 |
| Max Slippage | 2 pips | MRD §4 |
| Max Open Positions | 2 | BRD §2 |
| Midnight Block | 04:00-05:15 WIB | MRD §2.2 |

## KPI Targets (30-Day Demo)

- Win Rate ≥ 55%
- Profit Factor ≥ 1.5
- Execution Latency < 500ms
- RAM Usage < 2.5 GB (ASUS)

---
*Quantelos © 2026 — Muhamad Fikri*
