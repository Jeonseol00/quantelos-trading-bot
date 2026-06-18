# =============================================================================
# Quantelos AI Trader — Web Monitoring Dashboard Server
# =============================================================================
# A lightweight, zero-dependency Python http.server that connects to the local
# SQLite3 database and serves a premium, real-time dark mode web interface.
# =============================================================================
import http.server
import socketserver
import json
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

PORT = 8050
DB_PATH = Path("./data/quantelos.db")
LOG_PATH = Path("./logs/quantelos.log")

# Reader connections must tolerate brief WAL-checkpoint locks taken by the
# orchestrator's high-frequency writes. Without busy_timeout, a concurrent
# write can raise OperationalError and bubble up as HTTP 500, breaking the
# 1s dashboard polling loop.
DB_BUSY_TIMEOUT_MS = 5000


def _open_db() -> sqlite3.Connection:
    """Open a read-only SQLite connection tuned for safe concurrent reads."""
    conn = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro", uri=True, timeout=DB_BUSY_TIMEOUT_MS / 1000
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
    return conn

# Load HTML template dynamically
try:
    TEMPLATE_PATH = Path(__file__).parent / "dashboard_template.html"
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        HTML_CONTENT = f.read()
except Exception as e:
    HTML_CONTENT = f"<h1>Failed to load dashboard_template.html: {str(e)}</h1>"

class DashboardRequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default request logs to avoid cluttering the main console
        return

    def do_GET(self):
        if self.path == '/':
            try:
                with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                content = f"<h1>Failed to load dashboard_template.html: {str(e)}</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        elif self.path == '/api/data':
            self._send_json(self._safe(self._get_dashboard_data, {}))
        elif self.path == '/api/weekend_training_logs':
            self._send_json(self._safe(self._get_weekend_training_logs, []))
        elif self.path == '/api/ai_thinking':
            self._send_json(self._safe(self._get_ai_thinking_state, self._default_thinking_state()))
        else:
            self.send_response(404)
            self.end_headers()

    def _safe(self, fn, default):
        """Run a data fetcher, always returning a JSON-serializable value.

        Guarantees the API never raises and surfaces as HTTP 500, which would
        break the frontend polling loop. On any error (e.g. SQLite lock) the
        provided default is returned instead.
        """
        try:
            return fn()
        except Exception:
            return default

    def _send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _default_thinking_state() -> dict:
        return {"active": False, "phase": "idle", "model": "", "tokens_generated": 0,
                "thinking_text": "", "output_text": "", "current_agent": None,
                "agents_completed": [], "elapsed_seconds": 0, "error": None,
                "decision_json": None}

    def do_POST(self):
        if self.path == '/api/halt':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            params = urllib.parse.parse_qs(post_data)
            target_state = params.get('state', ['FALSE'])[0]
            
            self._update_halt_state(target_state)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "new_state": target_state}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def _get_dashboard_data(self) -> dict:
        conn = _open_db()
        cursor = conn.cursor()
        
        # 1. Fetch system states
        states = {}
        try:
            rows = cursor.execute("SELECT config_key, config_value FROM system_state").fetchall()
            for r in rows:
                states[r["config_key"]] = r["config_value"]
        except Exception:
            pass
            
        # 2. Fetch metrics
        metrics = {"total_trades": 0, "wins": 0, "losses": 0, "total_pnl_usc": 0.0, "win_rate": 0.0}
        try:
            row = cursor.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN usc_profit_loss > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN usc_profit_loss <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(usc_profit_loss) as total_pnl_usc
                FROM trade_logs_evaluation
            """).fetchone()
            if row and row["total_trades"] > 0:
                metrics = dict(row)
                metrics["win_rate"] = (metrics["wins"] / metrics["total_trades"] * 100)
        except Exception:
            pass

        # 3. Active positions
        active_positions = []
        try:
            rows = cursor.execute("SELECT * FROM active_positions WHERE status = 'OPEN' ORDER BY opened_at DESC").fetchall()
            active_positions = [dict(r) for r in rows]
        except Exception:
            pass

        # 4. Recent completed trades
        recent_trades = []
        try:
            rows = cursor.execute("""
                SELECT t.*, p.direction, p.pair, p.entry_price 
                FROM trade_logs_evaluation t
                JOIN active_positions p ON t.trade_id = p.trade_id
                ORDER BY t.evaluated_at DESC LIMIT 10
            """).fetchall()
            recent_trades = [dict(r) for r in rows]
        except Exception:
            pass

        # 5. High impact news
        news_events = []
        try:
            rows = cursor.execute("""
                SELECT * FROM news_events 
                WHERE impact_level IN ('HIGH', 'MEDIUM') 
                  AND scheduled_at > datetime('now', '-2 hours')
                ORDER BY scheduled_at ASC LIMIT 6
            """).fetchall()
            news_events = [dict(r) for r in rows]
        except Exception:
            pass

        # 6. Check node status (alive if heartbeat recorded in last 35 seconds)
        node_status = {
            "python_logic": {"alive": False, "ram_mb": 0, "cpu_pct": 0},
            "cpp_executor": {"alive": False, "ram_mb": 0, "cpu_pct": 0},
            "kaggle_brain": {"alive": False, "ram_mb": 0, "cpu_pct": 0}
        }
        try:
            for node in node_status.keys():
                row = cursor.execute("""
                    SELECT status, ram_mb, cpu_pct, checked_at 
                    FROM heartbeat_log 
                    WHERE node_name = ? 
                    ORDER BY checked_at DESC LIMIT 1
                """, (node,)).fetchone()
                if row:
                    # Calculate time difference
                    checked_at = datetime.fromisoformat(row["checked_at"].replace("Z", "+00:00"))
                    diff = (datetime.now(timezone.utc) - checked_at).total_seconds()
                    if diff < 90: # Alive threshold
                        node_status[node] = {
                            "alive": True,
                            "ram_mb": round(row["ram_mb"], 1) if row["ram_mb"] else 0,
                            "cpu_pct": round(row["cpu_pct"], 1) if row["cpu_pct"] else 0
                        }
        except Exception:
            pass

        # Fallback direct health check for Kaggle Swarm from Dashboard
        if not node_status["kaggle_brain"]["alive"]:
            kaggle_url = states.get("kaggle_ngrok_url")
            if kaggle_url:
                import urllib.request
                try:
                    req = urllib.request.Request(
                        f"{kaggle_url}/api/tags",
                        headers={'User-Agent': 'QuantelosDashboard/1.0'}
                    )
                    with urllib.request.urlopen(req, timeout=1.5) as response:
                        if response.status == 200:
                            node_status["kaggle_brain"] = {
                                "alive": True,
                                "ram_mb": 0.0,
                                "cpu_pct": 0.0
                            }
                except Exception:
                    try:
                        req = urllib.request.Request(
                            kaggle_url,
                            headers={'User-Agent': 'QuantelosDashboard/1.0'}
                        )
                        with urllib.request.urlopen(req, timeout=1.5) as response:
                            if response.status == 200:
                                node_status["kaggle_brain"] = {
                                    "alive": True,
                                    "ram_mb": 0.0,
                                    "cpu_pct": 0.0
                                }
                    except Exception:
                        pass

        # 6.5. Weekend training stats
        weekend_stats = {"total_runs": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_reward": 0.0, "avg_pips": 0.0}
        try:
            row = cursor.execute("""
                SELECT 
                    COUNT(*) as total_runs,
                    SUM(CASE WHEN evaluation_result = 'CORRECT' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN evaluation_result = 'INCORRECT' THEN 1 ELSE 0 END) as losses,
                    SUM(reward_penalty) as total_reward,
                    AVG(pips_gained) as avg_pips
                FROM weekend_training_logs
            """).fetchone()
            if row and row["total_runs"] > 0:
                weekend_stats = dict(row)
                weekend_stats["win_rate"] = (weekend_stats["wins"] / weekend_stats["total_runs"] * 100)
                weekend_stats["total_reward"] = round(weekend_stats["total_reward"] or 0.0, 1)
                weekend_stats["avg_pips"] = round(weekend_stats["avg_pips"] or 0.0, 1)
        except Exception:
            pass

        conn.close()

        # 7. Last 50 lines of log file
        system_logs = []
        if LOG_PATH.exists():
            try:
                with open(LOG_PATH, "r", errors="ignore") as f:
                    lines = f.readlines()
                    system_logs = [line.strip() for line in lines[-50:]]
            except Exception:
                pass

        return {
            "emergency_halt": states.get("emergency_halt", "FALSE"),
            "gpu_url": states.get("kaggle_ngrok_url", ""),
            "system_mode": states.get("system_mode", "DEMO"),
            "account_balance": states.get("account_balance", "0.00"),
            "account_equity": states.get("account_equity", "0.00"),
            "account_unrealized_pl": states.get("account_unrealized_pl", "0.00"),
            "account_currency": states.get("account_currency", "USD"),
            "ai_cognitive_state": json.loads(states.get("ai_cognitive_state", "{}")) if states.get("ai_cognitive_state") else {},
            "latest_market_debate": json.loads(states.get("latest_market_debate", "{}")) if states.get("latest_market_debate") else {},
            "metrics": metrics,
            "active_positions": active_positions,
            "recent_trades": recent_trades,
            "news_events": news_events,
            "node_status": node_status,
            "system_logs": system_logs,
            "weekend_stats": weekend_stats
        }

    def _get_weekend_training_logs(self) -> list[dict]:
        conn = _open_db()
        cursor = conn.cursor()
        logs = []
        try:
            rows = cursor.execute("""
                SELECT * FROM weekend_training_logs 
                ORDER BY simulated_at DESC LIMIT 30
            """).fetchall()
            logs = [dict(r) for r in rows]
        except Exception:
            pass
        finally:
            conn.close()
        return logs

    def _get_ai_thinking_state(self) -> dict:
        """Read the live AI thinking state from SQLite system_state table."""
        conn = _open_db()
        default = self._default_thinking_state()
        try:
            row = conn.execute(
                "SELECT config_value FROM system_state WHERE config_key = 'ai_thinking_state'"
            ).fetchone()
            if row:
                state = json.loads(row["config_value"])
                # Gold v2.0: If current state is connecting/idle, show last complete state
                if state.get("phase") in ("connecting", "idle") and state.get("tokens_generated", 0) == 0:
                    last_row = conn.execute(
                        "SELECT config_value FROM system_state WHERE config_key = 'last_complete_thinking'"
                    ).fetchone()
                    if last_row:
                        last_state = json.loads(last_row["config_value"])
                        last_state["phase"] = "complete (previous)"
                        last_state["_note"] = "Showing last complete analysis while new inference runs..."
                        conn.close()
                        return last_state
                conn.close()
                return state
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return default

    def _update_halt_state(self, state: str):
        conn = sqlite3.connect(str(DB_PATH))
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO system_state (config_key, config_value, last_updated)
                VALUES ('emergency_halt', ?, datetime('now'))
                ON CONFLICT(config_key) DO UPDATE SET
                    config_value = excluded.config_value,
                    last_updated = excluded.last_updated
            """, (state,))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

def run_server():
    handler = DashboardRequestHandler
    # Allow port reuse to prevent address already in use errors on rapid restarts
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"[SUCCESS] Quantelos Web Dashboard running at: http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("[INFO] Shutting down Dashboard server.")

if __name__ == "__main__":
    run_server()
