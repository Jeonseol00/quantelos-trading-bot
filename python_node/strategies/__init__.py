# =============================================================================
# Quantelos AI Trader — Strategy Package Interface
# =============================================================================
from .base_strategy import BaseStrategy
from .mtf_scalper import MtfScalperStrategy
from .quantitative_sniper import QuantitativeSniperStrategy

def get_strategy(strategy_name: str, config: dict) -> BaseStrategy:
    """Factory function to instantiate strategies dynamically by name."""
    if strategy_name == "MTF_SCALPER":
        return MtfScalperStrategy(config)
    elif strategy_name == "QUANTITATIVE_SNIPER":
        return QuantitativeSniperStrategy(config)
    else:
        raise ValueError(f"Unknown strategy mode: {strategy_name}")
