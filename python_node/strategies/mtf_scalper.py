# =============================================================================
# Quantelos AI Trader — MTF Scalper Strategy Implementation
# =============================================================================
import logging
import pandas as pd
from .base_strategy import BaseStrategy
from technical_analyzer import TechnicalAnalyzer

logger = logging.getLogger("quantelos.strategy.mtf_scalper")

class MtfScalperStrategy(BaseStrategy):
    """
    Multi-Timeframe Scalper Strategy.
    Uses H1 trend, M15 Bollinger Bands, and M5 Keltner Channels + RSI.
    """
    def __init__(self, config: dict):
        super().__init__(config)
        self.ta = TechnicalAnalyzer(
            atr_period=self.cfg["strategy"]["atr_period"],
            bb_period=self.cfg["strategy"]["bollinger_period"],
            bb_std=self.cfg["strategy"]["bollinger_std"],
            rsi_period=self.cfg["strategy"]["rsi_period"],
            atr_threshold_pips=self.cfg["strategy"]["atr_squeeze_threshold_pips"],
            bb_percentile=self.cfg["strategy"]["bollinger_bandwidth_percentile"],
            rsi_low=self.cfg["strategy"]["rsi_neutral_low"],
            rsi_high=self.cfg["strategy"]["rsi_neutral_high"],
            instrument=self.cfg["oanda"]["instruments"][0],
            scalping_rsi_low=self.cfg["strategy"].get("scalping_rsi_low", 30),
            scalping_rsi_high=self.cfg["strategy"].get("scalping_rsi_high", 70),
            scalping_vwap_std=self.cfg["strategy"].get("scalping_vwap_std", 2.0),
            scalping_trend_filter=self.cfg["strategy"].get("scalping_trend_filter", True),
        )

    def analyze(self, df_m5: pd.DataFrame, df_m15: pd.DataFrame, df_h1: pd.DataFrame) -> dict:
        # Gold v2.0: RL parameter adaptation DISABLED.
        # Always use config values as source of truth to prevent stale DB corruption.
        # The TechnicalAnalyzer already has config-driven defaults loaded in __init__.
        self.ta.scalping_rsi_low = self.cfg["strategy"].get("scalping_rsi_low", 30)
        self.ta.scalping_rsi_high = self.cfg["strategy"].get("scalping_rsi_high", 70)
        self.ta.scalping_vwap_std = self.cfg["strategy"].get("scalping_vwap_std", 2.0)

        signal = self.ta.analyze_scalping(df_m5, df_m15, df_h1)
        return {"signal": signal}

    def detect_signal(self, current_price: float, analysis_results: dict) -> str | None:
        signal = analysis_results.get("signal")
        if signal and signal.is_scalp_trigger:
            return signal.direction
        return None

    def calculate_targets(self, current_price: float, direction: str, analysis_results: dict) -> tuple[float, float]:
        """Calculate Gold-tuned SL/TP targets with minimum distance guards.
        
        Gold v2.0 targets:
        - ATR-based: SL = 2.0x ATR, TP = 3.0x ATR (R:R = 1:1.5 minimum)
        - Hard minimums: SL >= $5.00, TP >= $8.00 (prevents noise-level stops)
        - Fallback: 60 pip SL / 120 pip TP when ATR unavailable
        """
        signal = analysis_results.get("signal")
        instrument = self.cfg["oanda"]["instruments"][0]
        pip_size = 0.1 if "XAU" in instrument else (0.01 if "JPY" in instrument else 0.0001)
        
        tp_mult = self.cfg["strategy"].get("scalping_atr_tp_mult", 3.0)
        sl_mult = self.cfg["strategy"].get("scalping_atr_sl_mult", 2.0)
        
        # Gold-specific minimum distances in price (not pips)
        min_sl_price = self.cfg["strategy"].get("scalping_min_sl_price", 5.0)
        min_tp_price = self.cfg["strategy"].get("scalping_min_tp_price", 8.0)
        
        m5_atr = signal.m5_atr if (signal and hasattr(signal, "m5_atr")) else 0.0
        
        if m5_atr > 0:
            # ATR-based calculation with Gold minimum guards
            tp_distance = max(tp_mult * m5_atr, min_tp_price)
            sl_distance = max(sl_mult * m5_atr, min_sl_price)
        else:
            # Fallback: use configured pip distances
            sl_pips = self.cfg["strategy"].get("scalping_stop_pips", 60.0)
            tp_pips = self.cfg["strategy"].get("scalping_target_pips", 120.0)
            tp_distance = max(tp_pips * pip_size, min_tp_price)
            sl_distance = max(sl_pips * pip_size, min_sl_price)
        
        # Enforce minimum R:R ratio of 1:1.5 (TP must be >= 1.5x SL)
        if tp_distance < sl_distance * 1.5:
            tp_distance = sl_distance * 1.5
            logger.info("📐 R:R floor enforced: TP adjusted to %.5f (1.5x SL %.5f)", tp_distance, sl_distance)

        if direction == "BUY":
            stop_loss = current_price - sl_distance
            take_profit = current_price + tp_distance
        else:
            stop_loss = current_price + sl_distance
            take_profit = current_price - tp_distance
        
        sl_pips_display = sl_distance / pip_size
        tp_pips_display = tp_distance / pip_size
        rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0
        logger.info("📐 [GOLD TARGETS] SL: %.5f (%.0f pips) | TP: %.5f (%.0f pips) | R:R = 1:%.1f",
                    sl_distance, sl_pips_display, tp_distance, tp_pips_display, rr_ratio)
            
        return stop_loss, take_profit
