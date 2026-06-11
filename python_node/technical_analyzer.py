# =============================================================================
# Quantelos AI Trader — Technical Analyzer (Squeeze Detector)
# =============================================================================
# Implements the MRD-defined Quantitative Sniper strategy:
#   Phase 1: ATR Squeeze Detection
#   Phase 2: Bollinger Band Compression
#   Phase 3: RSI Neutral Zone Confirmation
# =============================================================================
import logging
from dataclasses import dataclass

logger = logging.getLogger("quantelos.ta")

try:
    import pandas as pd
    import pandas_ta as ta
except ImportError:
    logger.error("Dependencies missing. Run: pip install pandas pandas-ta")
    raise


@dataclass
class SqueezeSignal:
    """Output of the technical squeeze detector."""
    is_squeeze: bool            # True if all 3 conditions are met on current candle
    recent_squeeze: bool        # True if squeeze was active in recent history (last 4 candles)
    atr_value: float            # Current ATR(14) in pips
    atr_is_compressed: bool     # ATR < threshold
    bb_bandwidth: float         # Current Bollinger bandwidth
    bb_is_compressed: bool      # Bandwidth < percentile
    rsi_value: float            # Current RSI(14)
    rsi_is_neutral: bool        # RSI in 40-60 zone
    support: float              # Lower Bollinger Band (breakout reference)
    bb_mid: float               # Middle Bollinger Band (exit reference)
    resistance: float           # Upper Bollinger Band (breakout reference)


@dataclass
class ScalpingSignal:
    """Output of the multi-timeframe scalping analysis."""
    is_scalp_trigger: bool
    direction: str | None
    h1_ema: float
    h1_trend: str
    m15_bb_mid: float
    m15_bb_lower: float
    m15_bb_upper: float
    m5_vwap: float
    m5_vwap_lower: float
    m5_vwap_upper: float
    m5_rsi: float
    m5_atr: float


class TechnicalAnalyzer:
    """MRD-compliant squeeze detector for EUR/USD M15 timeframe."""

    def __init__(self, atr_period: int = 14, bb_period: int = 20, bb_std: float = 2.0,
                 rsi_period: int = 14, atr_threshold_pips: float = 4.0,
                 bb_percentile: int = 10, rsi_low: int = 40, rsi_high: int = 60,
                 instrument: str = "EUR_USD", scalping_rsi_low: int = 30,
                 scalping_rsi_high: int = 70, scalping_vwap_std: float = 2.0,
                 scalping_trend_filter: bool = True):
        self.atr_period = atr_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.instrument = instrument
        self.scalping_rsi_low = scalping_rsi_low
        self.scalping_rsi_high = scalping_rsi_high
        self.scalping_vwap_std = scalping_vwap_std
        self.scalping_trend_filter = scalping_trend_filter
        
        # Define pip multiplier based on instrument
        if "JPY" in instrument:
            self.pip_multiplier = 0.01
        elif "XAU" in instrument:
            self.pip_multiplier = 0.1  # 1 pip = 0.10 USD (10 cents) for Gold
        else:
            self.pip_multiplier = 0.0001  # Default for EUR/USD, GBP/USD, etc.
            
        self.atr_threshold = atr_threshold_pips * self.pip_multiplier  # Convert pips to price
        self.bb_percentile = bb_percentile
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high

    def analyze(self, df: pd.DataFrame) -> SqueezeSignal:
        """
        Run squeeze analysis on OHLCV DataFrame.
        Expects columns: ['open', 'high', 'low', 'close', 'volume']
        """
        if len(df) < max(self.atr_period, self.bb_period) + 5:
            raise ValueError(f"Insufficient data: need {self.bb_period + 5} candles, got {len(df)}")

        # ── ATR Squeeze Detection ─────────────────────────────────────────
        atr = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        current_atr = atr.iloc[-1]
        atr_compressed_series = atr < self.atr_threshold
        atr_compressed = atr_compressed_series.iloc[-1]

        # ── Bollinger Band Compression ────────────────────────────────────
        bb = ta.bbands(df["close"], length=self.bb_period, std=self.bb_std)
        bbl_col = [c for c in bb.columns if c.startswith("BBL_")][0]
        bbm_col = [c for c in bb.columns if c.startswith("BBM_")][0]
        bbu_col = [c for c in bb.columns if c.startswith("BBU_")][0]

        bb_upper = bb[bbu_col].iloc[-1]
        bb_lower = bb[bbl_col].iloc[-1]
        bb_mid = bb[bbm_col].iloc[-1]

        # Calculate bandwidth history for percentile comparison
        bb_bandwidth_series = (bb[bbu_col] - bb[bbl_col]) / bb[bbm_col]
        current_bandwidth = bb_bandwidth_series.iloc[-1]
        
        # Calculate rolling quantile for each point in history to get bb_compressed series
        lookback = min(len(bb_bandwidth_series), 1920)
        rolling_quantile = bb_bandwidth_series.rolling(window=lookback, min_periods=1).quantile(
            self.bb_percentile / 100.0
        )
        bb_compressed_series = bb_bandwidth_series <= rolling_quantile
        bb_compressed = bb_compressed_series.iloc[-1]

        # ── RSI Neutral Zone ──────────────────────────────────────────────
        rsi = ta.rsi(df["close"], length=self.rsi_period)
        current_rsi = rsi.iloc[-1]
        rsi_neutral_series = (rsi >= self.rsi_low) & (rsi <= self.rsi_high)
        rsi_neutral = rsi_neutral_series.iloc[-1]

        # ── Combined Squeeze Detection ────────────────────────────────────
        is_squeeze = atr_compressed and bb_compressed and rsi_neutral
        
        # Squeeze series to check recent history (last 4 candles)
        squeeze_series = atr_compressed_series & bb_compressed_series & rsi_neutral_series
        recent_squeeze = bool(squeeze_series.iloc[-4:].any())

        if is_squeeze:
            logger.info(
                "🎯 SQUEEZE DETECTED — ATR: %.5f | BB Width: %.5f | RSI: %.1f",
                current_atr, current_bandwidth, current_rsi
            )
        elif recent_squeeze:
            logger.info("🎯 RECENT SQUEEZE ACTIVE IN MEMORY (within last 4 candles)")

        return SqueezeSignal(
            is_squeeze=is_squeeze,
            recent_squeeze=recent_squeeze,
            atr_value=current_atr / self.pip_multiplier,  # Convert back to pips for display
            atr_is_compressed=atr_compressed,
            bb_bandwidth=current_bandwidth,
            bb_is_compressed=bb_compressed,
            rsi_value=current_rsi,
            rsi_is_neutral=rsi_neutral,
            support=bb_lower,
            bb_mid=bb_mid,
            resistance=bb_upper,
        )

    def detect_trade_signal(self, current_price: float, signal: SqueezeSignal) -> str | None:
        """
        Detect trade signal based on active mode (Breakout if squeeze was active recently, Mean Reversion otherwise).
        """
        if signal.recent_squeeze:
            # BREAKOUT MODE
            if current_price > signal.resistance:
                logger.info("🚀 BREAKOUT UP detected at %.5f (resistance: %.5f)",
                            current_price, signal.resistance)
                return "BUY"
            elif current_price < signal.support:
                logger.info("📉 BREAKOUT DOWN detected at %.5f (support: %.5f)",
                            current_price, signal.support)
                return "SELL"
        else:
            # MEAN REVERSION MODE
            range_width = signal.resistance - signal.support
            pct = (current_price - signal.support) / range_width if range_width > 0 else 0.5

            # Buy when price is in lower 15% zone (pct <= 0.15) and RSI is oversold (< 45)
            if pct <= 0.15 and signal.rsi_value < 45:
                logger.info("🟢 MEAN REVERSION BUY triggered at %.5f (support: %.5f, pct: %.2f, RSI: %.2f)",
                            current_price, signal.support, pct, signal.rsi_value)
                return "BUY"
            # Sell when price is in upper 15% zone (pct >= 0.85) and RSI is overbought (> 55)
            elif pct >= 0.85 and signal.rsi_value > 55:
                logger.info("🔴 MEAN REVERSION SELL triggered at %.5f (resistance: %.5f, pct: %.2f, RSI: %.2f)",
                            current_price, signal.resistance, pct, signal.rsi_value)
                return "SELL"
        return None

    def detect_breakout(self, current_price: float, signal: SqueezeSignal) -> str | None:
        """Wrapper for backward compatibility."""
        return self.detect_trade_signal(current_price, signal)

    def analyze_scalping(self, df_m5: pd.DataFrame, df_m15: pd.DataFrame, df_h1: pd.DataFrame) -> ScalpingSignal:
        """
        Run Multi-Timeframe Scalping Analysis.
        df_m5: Execution dataframe (M5)
        df_m15: Intermediate structure dataframe (M15)
        df_h1: Macro trend filter dataframe (H1)
        """
        if len(df_m5) < 30 or len(df_m15) < 30 or len(df_h1) < 60:
            logger.warning("Insufficient multi-timeframe historical data to run scalper.")
            return ScalpingSignal(
                is_scalp_trigger=False, direction=None,
                h1_ema=0.0, h1_trend="NEUTRAL",
                m15_bb_mid=0.0, m15_bb_lower=0.0, m15_bb_upper=0.0,
                m5_vwap=0.0, m5_vwap_lower=0.0, m5_vwap_upper=0.0,
                m5_rsi=50.0, m5_atr=0.0
            )

        # 1. Macro H1 EMA Trend Filter
        h1_ema_series = ta.ema(df_h1["close"], length=50)
        h1_ema = h1_ema_series.iloc[-1]
        current_price = df_m5.iloc[-1]["close"]
        h1_trend = "BULLISH" if current_price > h1_ema else "BEARISH"

        # 2. Intermediate M15 Bollinger Band structural boundaries
        bb_m15 = ta.bbands(df_m15["close"], length=self.bb_period, std=self.bb_std)
        bbl_col = [c for c in bb_m15.columns if c.startswith("BBL_")][0]
        bbm_col = [c for c in bb_m15.columns if c.startswith("BBM_")][0]
        bbu_col = [c for c in bb_m15.columns if c.startswith("BBU_")][0]

        m15_bb_lower = bb_m15[bbl_col].iloc[-1]
        m15_bb_mid = bb_m15[bbm_col].iloc[-1]
        m15_bb_upper = bb_m15[bbu_col].iloc[-1]

        # 3. Micro M5 Keltner Channel (EMA20 + ATR14 envelope)
        # NOTE: Replaces cumulative VWAP which suffered from anchor-drift.
        # Keltner Channel dynamically adapts to recent volatility.
        m5_ema20 = ta.ema(df_m5["close"], length=20)
        m5_atr_envelope = ta.atr(df_m5["high"], df_m5["low"], df_m5["close"], length=14)

        m5_vwap = m5_ema20.iloc[-1]  # Center line (replaces VWAP)
        m5_vwap_lower = (m5_ema20 - self.scalping_vwap_std * m5_atr_envelope).iloc[-1]
        m5_vwap_upper = (m5_ema20 + self.scalping_vwap_std * m5_atr_envelope).iloc[-1]

        # Calculate M5 RSI
        rsi_m5_series = ta.rsi(df_m5["close"], length=self.rsi_period)
        m5_rsi = rsi_m5_series.iloc[-1]

        # Calculate M5 ATR(14)
        atr_m5_series = ta.atr(df_m5["high"], df_m5["low"], df_m5["close"], length=14)
        m5_atr = atr_m5_series.iloc[-1]

        # 4. Trigger logic
        is_scalp_trigger = False
        direction = None

        trend_allows_buy = (not self.scalping_trend_filter) or (h1_trend == "BULLISH" and current_price <= m15_bb_mid)
        trend_allows_sell = (not self.scalping_trend_filter) or (h1_trend == "BEARISH" and current_price >= m15_bb_mid)

        if trend_allows_buy:
            # Check for oversold trigger on M5
            if current_price <= m5_vwap_lower and m5_rsi < self.scalping_rsi_low:
                is_scalp_trigger = True
                direction = "BUY"
                logger.info("⚡ MTF SCALPER BUY triggered — Trend Filter: %s | M5 RSI: %.2f | M5 KC Lower: %.5f",
                            "ENABLED" if self.scalping_trend_filter else "DISABLED", m5_rsi, m5_vwap_lower)

        if not is_scalp_trigger and trend_allows_sell:
            # Check for overbought trigger on M5
            if current_price >= m5_vwap_upper and m5_rsi > self.scalping_rsi_high:
                is_scalp_trigger = True
                direction = "SELL"
                logger.info("⚡ MTF SCALPER SELL triggered — Trend Filter: %s | M5 RSI: %.2f | M5 KC Upper: %.5f",
                            "ENABLED" if self.scalping_trend_filter else "DISABLED", m5_rsi, m5_vwap_upper)

        return ScalpingSignal(
            is_scalp_trigger=is_scalp_trigger,
            direction=direction,
            h1_ema=h1_ema,
            h1_trend=h1_trend,
            m15_bb_mid=m15_bb_mid,
            m15_bb_lower=m15_bb_lower,
            m15_bb_upper=m15_bb_upper,
            m5_vwap=m5_vwap,
            m5_vwap_lower=m5_vwap_lower,
            m5_vwap_upper=m5_vwap_upper,
            m5_rsi=m5_rsi,
            m5_atr=m5_atr
        )

