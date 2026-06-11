# =============================================================================
# Quantelos AI Trader — Discord / Telegram Notifier
# =============================================================================
# Real-time push notifications for trade executions, errors, and heartbeats.
# BRD Section 5, Gap 4 — Remote Monitoring Protocol.
# =============================================================================
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("quantelos.notify")

try:
    import requests
except ImportError:
    raise ImportError("Run: pip install requests")


class Notifier:
    """Multi-channel notification dispatcher (Discord + Telegram)."""

    def __init__(self, discord_webhook: str = "", telegram_token: str = "",
                 telegram_chat_id: str = "", enabled: bool = True):
        self.discord_url = discord_webhook
        self.tg_token = telegram_token
        self.tg_chat = telegram_chat_id
        self.enabled = enabled

    def send(self, title: str, message: str, level: str = "INFO"):
        """Send notification to all configured channels."""
        if not self.enabled:
            return

        emoji_map = {
            "INFO": "ℹ️", "TRADE": "💰", "ERROR": "🚨",
            "HEARTBEAT": "💓", "EMERGENCY": "🔴",
        }
        emoji = emoji_map.get(level, "📌")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        if self.discord_url:
            self._send_discord(emoji, title, message, timestamp, level)
        if self.tg_token and self.tg_chat:
            self._send_telegram(emoji, title, message, timestamp)

    def _send_discord(self, emoji: str, title: str, msg: str, ts: str, level: str):
        """Send formatted Discord webhook embed."""
        color_map = {"INFO": 3447003, "TRADE": 3066993, "ERROR": 15158332,
                     "EMERGENCY": 10038562, "HEARTBEAT": 1752220}
        payload = {
            "embeds": [{
                "title": f"{emoji} {title}",
                "description": msg,
                "color": color_map.get(level, 3447003),
                "footer": {"text": f"Quantelos AI Trader | {ts}"},
            }]
        }
        try:
            requests.post(self.discord_url, json=payload, timeout=5)
        except requests.RequestException as e:
            logger.error("Discord notification failed: %s", e)

    def _send_telegram(self, emoji: str, title: str, msg: str, ts: str):
        """Send formatted Telegram message."""
        text = f"{emoji} *{title}*\n\n{msg}\n\n_{ts}_"
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                json={"chat_id": self.tg_chat, "text": text, "parse_mode": "Markdown"},
                timeout=5,
            )
        except requests.RequestException as e:
            logger.error("Telegram notification failed: %s", e)

    # ─── Convenience Methods ──────────────────────────────────────────────────

    def notify_trade(self, pair: str, direction: str, entry: float,
                     sl: float, tp: float, confidence: float, units: int = 0):
        units_str = f"Units: `{units}` | " if units > 0 else ""
        self.send("Trade Executed", (
            f"**{direction} {pair}**\n"
            f"Entry: `{entry:.5f}` | SL: `{sl:.5f}` | TP: `{tp:.5f}`\n"
            f"{units_str}Confidence: `{confidence:.0%}`"
        ), "TRADE")

    def notify_error(self, error_msg: str):
        self.send("System Error", f"```\n{error_msg}\n```", "ERROR")

    def notify_emergency(self, reason: str):
        self.send("🔴 EMERGENCY HALT", reason, "EMERGENCY")

    def notify_heartbeat(self, ram_mb: float, cpu_pct: float, open_positions: int):
        self.send("Heartbeat", (
            f"RAM: `{ram_mb:.0f} MB` | CPU: `{cpu_pct:.1f}%` | "
            f"Open Positions: `{open_positions}`"
        ), "HEARTBEAT")
