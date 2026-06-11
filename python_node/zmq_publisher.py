# =============================================================================
# Quantelos AI Trader — ZeroMQ IPC Publisher (Python → C++ Bridge)
# =============================================================================
# Publishes validated trade signals to the C++ Execution Engine via
# UNIX Domain Sockets (ipc://) for sub-millisecond latency.
# =============================================================================
import json
import logging
import time
from dataclasses import dataclass, asdict

logger = logging.getLogger("quantelos.zmq")

try:
    import zmq
except ImportError:
    logger.error("pyzmq not installed. Run: pip install pyzmq")
    raise


@dataclass
class TradeSignal:
    """Trade signal payload sent to C++ Execution Engine."""
    decision: str           # "EXECUTE_TRADE" | "CLOSE_TRADE" | "HEARTBEAT"
    pair: str               # "EUR_USD" | "GBP_USD"
    direction: str          # "BUY" | "SELL"
    entry_price: float      # Target entry price
    stop_loss: float        # Mandatory SL coordinate
    take_profit: float      # Mandatory TP coordinate
    confidence: float       # AI confidence score [0.0 - 1.0]
    news_catalyst: str      # Description of triggering news event
    timestamp: str          # ISO 8601 timestamp


class ZMQPublisher:
    """ZeroMQ IPC publisher for fire-and-forget signal transmission."""

    def __init__(self, ipc_path: str = "/tmp/quantelos_ipc.sock",
                 protocol: str = "ipc"):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)

        if protocol == "ipc":
            self.address = f"ipc://{ipc_path}"
        else:
            self.address = "tcp://127.0.0.1:5555"

        self.socket.bind(self.address)
        # Allow subscribers to connect before first message
        time.sleep(0.5)
        logger.info("ZMQ Publisher bound to %s", self.address)

    def publish_signal(self, signal: TradeSignal) -> bool:
        """Publish a trade signal to the C++ subscriber."""
        try:
            payload = json.dumps(asdict(signal), ensure_ascii=False)
            self.socket.send_string(payload, zmq.NOBLOCK)
            logger.info("Published signal: %s %s %s @ %.5f (conf: %.2f)",
                        signal.decision, signal.direction, signal.pair,
                        signal.entry_price, signal.confidence)
            return True
        except zmq.ZMQError as e:
            logger.error("ZMQ publish failed: %s", e)
            return False

    def publish_heartbeat(self):
        """Send a heartbeat ping to C++ node."""
        heartbeat = TradeSignal(
            decision="HEARTBEAT",
            pair="", direction="", entry_price=0, stop_loss=0,
            take_profit=0, confidence=0, news_catalyst="",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        return self.publish_signal(heartbeat)

    def close(self):
        """Clean shutdown of ZMQ resources."""
        self.socket.close()
        self.context.term()
        logger.info("ZMQ Publisher closed.")
