# =============================================================================
# Quantelos AI Trader — Base Strategy Interface
# =============================================================================
from abc import ABC, abstractmethod
import pandas as pd

class BaseStrategy(ABC):
    """
    Abstract Base Class for all trading strategies in Quantelos.
    Follows a modular structure similar to Freqtrade's IStrategy.
    """
    def __init__(self, config: dict):
        self.cfg = config

    @abstractmethod
    def analyze(self, df_m5: pd.DataFrame, df_m15: pd.DataFrame, df_h1: pd.DataFrame) -> dict:
        """
        Runs indicator calculations and returns a dictionary of strategy data
        used for state updating and signal detection.
        """
        pass

    @abstractmethod
    def detect_signal(self, current_price: float, analysis_results: dict) -> str | None:
        """
        Determines if a BUY, SELL, or HOLD signal is triggered.
        Returns 'BUY', 'SELL', or None.
        """
        pass

    @abstractmethod
    def calculate_targets(self, current_price: float, direction: str, analysis_results: dict) -> tuple[float, float]:
        """
        Calculates Stop Loss (SL) and Take Profit (TP) target levels.
        Returns a tuple of (stop_loss, take_profit).
        """
        pass
