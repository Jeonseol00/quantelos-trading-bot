# =============================================================================
# Quantelos AI Trader — Quantitative Sniper Strategy Implementation
# =============================================================================
import logging
import pandas as pd
from .base_strategy import BaseStrategy
from technical_analyzer import TechnicalAnalyzer

logger = logging.getLogger("quantelos.strategy.quantitative_sniper")

class QuantitativeSniperStrategy(BaseStrategy):
    """
    Quantitative Sniper Strategy (M15 Squeeze / Breakout).
    Detects market consolidation periods (ATR squeeze + BB compression + RSI neutrality)
    and executes breakout or mean reversion trades.
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
            instrument=self.cfg["oanda"]["instruments"][0]
        )

    def analyze(self, df_m5: pd.DataFrame, df_m15: pd.DataFrame, df_h1: pd.DataFrame) -> dict:
        # Sniper runs primarily on the default timeframe (M15)
        # Note: df_m5 represents the main execution candles when configured for M15, 
        # but to be fully safe, we pass df_m5 to technical analyzer.
        # Let's inspect df_m5 or whichever dataframe is appropriate.
        # In main.py, it calls: squeeze_sig = self.ta.analyze(df) where df is get_latest_dataframe().
        # Since df in main.py is df (fetched as get_latest_dataframe()), let's use df_m5 as the main df.
        signal = self.ta.analyze(df_m5)
        return {"signal": signal}

    def detect_signal(self, current_price: float, analysis_results: dict) -> str | None:
        signal = analysis_results.get("signal")
        if signal:
            return self.ta.detect_breakout(current_price, signal)
        return None

    def calculate_targets(self, current_price: float, direction: str, analysis_results: dict) -> tuple[float, float]:
        signal = analysis_results.get("signal")
        is_breakout = signal.recent_squeeze if signal else False
        instrument = self.cfg["oanda"]["instruments"][0]
        
        if is_breakout and signal:
            stop_loss = signal.support if direction == "BUY" else signal.resistance
            risk = abs(current_price - stop_loss)
            take_profit = current_price + (risk * 2.0) if direction == "BUY" else current_price - (risk * 2.0)
        elif signal:
            # Mean Reversion fallback targets
            pip_size = 0.1 if "XAU" in instrument else (0.01 if "JPY" in instrument else 0.0001)
            atr_price_offset = signal.atr_value * pip_size
            if direction == "BUY":
                stop_loss = current_price - (1.0 * atr_price_offset)
                take_profit = current_price + (2.0 * atr_price_offset)
            else:
                stop_loss = current_price + (1.0 * atr_price_offset)
                take_profit = current_price - (2.0 * atr_price_offset)
        else:
            # Absolute fallback
            stop_loss = current_price
            take_profit = current_price
            
        return stop_loss, take_profit
