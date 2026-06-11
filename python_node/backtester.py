# =============================================================================
# Quantelos AI Trader — Historical Backtester
# =============================================================================
# Validates the Quantitative Sniper strategy against historical EUR/USD data
# before forward-testing on OANDA Demo. Gap 3 mitigation from SOTA audit.
# =============================================================================
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("quantelos.backtest")

try:
    import pandas as pd
    import pandas_ta as ta
except ImportError:
    raise ImportError("Run: pip install pandas pandas-ta")

try:
    from technical_analyzer import TechnicalAnalyzer, SqueezeSignal
except ImportError:
    from python_node.technical_analyzer import TechnicalAnalyzer, SqueezeSignal


@dataclass
class BacktestResult:
    """Aggregated backtest performance metrics."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_pips: float = 0.0
    max_drawdown_pips: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win_pips: float = 0.0
    avg_loss_pips: float = 0.0
    trades: list = field(default_factory=list)


@dataclass
class BacktestTrade:
    """Individual simulated trade record."""
    entry_time: str
    exit_time: str
    direction: str
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    pnl_pips: float
    result: str  # "WIN" | "LOSS"


class Backtester:
    """
    Offline strategy backtester for Quantitative Sniper.
    Uses historical OHLCV data to simulate squeeze → breakout trades.
    """

    def __init__(self, sl_pips: float = 15.0, tp_pips: float = 30.0,
                 risk_reward: float = 2.0):
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips
        self.rr = risk_reward
        self.analyzer = TechnicalAnalyzer()

    def run(self, df: pd.DataFrame) -> BacktestResult:
        """
        Execute backtest on historical M15 OHLCV DataFrame.
        Expects columns: ['open', 'high', 'low', 'close', 'volume']
        with a DatetimeIndex.
        """
        result = BacktestResult()
        min_bars = 30  # Minimum lookback for indicators
        in_trade = False
        trade_entry = None
        trade_dir = None
        trade_sl = 0.0
        trade_tp = 0.0
        entry_time = None
        peak_equity = 0.0
        running_pnl = 0.0

        for i in range(min_bars, len(df)):
            window = df.iloc[:i+1]
            current = df.iloc[i]

            if not in_trade:
                # Check for squeeze
                try:
                    signal = self.analyzer.analyze(window)
                except (ValueError, KeyError):
                    continue

                if not signal.is_squeeze:
                    continue

                # Check for breakout
                direction = self.analyzer.detect_breakout(current["close"], signal)
                if direction is None:
                    continue

                # Enter trade
                in_trade = True
                trade_entry = current["close"]
                trade_dir = direction
                entry_time = df.index[i] if hasattr(df.index[i], 'isoformat') else str(df.index[i])

                pip = 0.0001
                if direction == "BUY":
                    trade_sl = trade_entry - (self.sl_pips * pip)
                    trade_tp = trade_entry + (self.tp_pips * pip)
                else:
                    trade_sl = trade_entry + (self.sl_pips * pip)
                    trade_tp = trade_entry - (self.tp_pips * pip)

            else:
                # Check SL/TP hit
                high = current["high"]
                low = current["low"]
                pnl = 0.0
                hit = False
                exit_time = df.index[i] if hasattr(df.index[i], 'isoformat') else str(df.index[i])

                if trade_dir == "BUY":
                    if low <= trade_sl:
                        pnl = -self.sl_pips
                        hit = True
                    elif high >= trade_tp:
                        pnl = self.tp_pips
                        hit = True
                else:
                    if high >= trade_sl:
                        pnl = -self.sl_pips
                        hit = True
                    elif low <= trade_tp:
                        pnl = self.tp_pips
                        hit = True

                if hit:
                    trade = BacktestTrade(
                        entry_time=str(entry_time),
                        exit_time=str(exit_time),
                        direction=trade_dir,
                        entry_price=trade_entry,
                        exit_price=trade_tp if pnl > 0 else trade_sl,
                        sl=trade_sl, tp=trade_tp,
                        pnl_pips=pnl,
                        result="WIN" if pnl > 0 else "LOSS",
                    )
                    result.trades.append(trade)
                    result.total_trades += 1
                    if pnl > 0:
                        result.wins += 1
                    else:
                        result.losses += 1
                    result.total_pnl_pips += pnl

                    running_pnl += pnl
                    if running_pnl > peak_equity:
                        peak_equity = running_pnl
                    dd = peak_equity - running_pnl
                    if dd > result.max_drawdown_pips:
                        result.max_drawdown_pips = dd

                    in_trade = False

        # Compute summary stats
        if result.total_trades > 0:
            result.win_rate = (result.wins / result.total_trades) * 100
            total_wins = sum(t.pnl_pips for t in result.trades if t.pnl_pips > 0)
            total_losses = abs(sum(t.pnl_pips for t in result.trades if t.pnl_pips < 0))
            result.profit_factor = (total_wins / total_losses) if total_losses > 0 else float('inf')
            result.avg_win_pips = total_wins / result.wins if result.wins > 0 else 0
            result.avg_loss_pips = total_losses / result.losses if result.losses > 0 else 0

        logger.info("Backtest: %d trades | WR: %.1f%% | PF: %.2f | PnL: %.1f pips",
                     result.total_trades, result.win_rate,
                     result.profit_factor, result.total_pnl_pips)
        return result

    def print_report(self, result: BacktestResult):
        """Print formatted backtest report to console."""
        print("\n" + "═" * 60)
        print("  QUANTITATIVE SNIPER — BACKTEST REPORT")
        print("═" * 60)
        print(f"  Total Trades     : {result.total_trades}")
        print(f"  Wins / Losses    : {result.wins} / {result.losses}")
        print(f"  Win Rate         : {result.win_rate:.1f}%")
        print(f"  Profit Factor    : {result.profit_factor:.2f}")
        print(f"  Total P/L        : {result.total_pnl_pips:+.1f} pips")
        print(f"  Max Drawdown     : {result.max_drawdown_pips:.1f} pips")
        print(f"  Avg Win          : {result.avg_win_pips:.1f} pips")
        print(f"  Avg Loss         : {result.avg_loss_pips:.1f} pips")
        print("═" * 60)

        # KPI validation
        kpi_wr = result.win_rate >= 55.0
        kpi_pf = result.profit_factor >= 1.5
        print(f"  KPI Win Rate ≥55% : {'✓ PASS' if kpi_wr else '✗ FAIL'}")
        print(f"  KPI PF ≥1.5       : {'✓ PASS' if kpi_pf else '✗ FAIL'}")
        print("═" * 60 + "\n")


if __name__ == "__main__":
    import os
    import sys
    
    # Simple logging config for main execution
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    config_path = "./config/settings.toml"
    if not os.path.exists(config_path):
        print(f"[ERROR] Config file not found at {config_path}. Run from project root.")
        sys.exit(1)
        
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
        
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
        
    count = 500
    if len(sys.argv) > 1:
        try:
            count = int(sys.argv[1])
            if count <= 0 or count > 5000:
                raise ValueError("Count must be between 1 and 5000.")
        except ValueError as e:
            print(f"[WARN] Invalid candle count. Using default 500. Detail: {e}")
            count = 500

    print(f"[INFO] Fetching {count} historical candles from OANDA for backtest...")
    from oanda_client import OANDAClient
    
    client = OANDAClient(
        api_url=cfg["oanda"]["api_url"],
        stream_url=cfg["oanda"]["stream_url"],
        account_id=cfg["oanda"]["account_id"],
        api_token=cfg["oanda"]["api_token"],
        instrument="EUR_USD",
        granularity="M15"
    )
    
    try:
        df = client.fetch_historical_candles(count=count)
        backtester = Backtester(
            sl_pips=cfg["risk"].get("max_slippage_pips", 2.0) * 10,  # dynamic SL
            tp_pips=cfg["risk"].get("max_slippage_pips", 2.0) * 20,  # dynamic TP (1:2 Ratio)
        )
        result = backtester.run(df)
        backtester.print_report(result)
    except Exception as e:
        print(f"[ERROR] Failed to execute backtest: {e}")

