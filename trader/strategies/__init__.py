# trader/strategies/__init__.py
from trader.strategies.base import BaseStrategy, Direction, Signal
from trader.strategies.ema import EMAStrategy
from trader.strategies.rsi import RSIStrategy
from trader.strategies.macd import MACDStrategy
from trader.strategies.volume import VolumeStrategy
from trader.strategies.momentum import MomentumStrategy
from trader.strategies.atr_breakout import ATRBreakoutStrategy
from trader.strategies.funding import FundingRateStrategy
from trader.strategies.supertrend import SuperTrendStrategy
from trader.strategies.bollinger import BollingerStrategy
from trader.strategies.ichimoku import IchimokuStrategy

ALL_STRATEGIES = {
    "EMA":          EMAStrategy,
    "RSI":          RSIStrategy,
    "MACD":         MACDStrategy,
    "VOLUME":       VolumeStrategy,
    "MOMENTUM":     MomentumStrategy,
    "ATR_BREAKOUT": ATRBreakoutStrategy,
    "FUNDING_RATE": FundingRateStrategy,
    "SUPERTREND":   SuperTrendStrategy,
    "BOLLINGER":    BollingerStrategy,
    "ICHIMOKU":     IchimokuStrategy,
}

__all__ = [
    "BaseStrategy", "Direction", "Signal",
    "EMAStrategy", "RSIStrategy", "MACDStrategy", "VolumeStrategy",
    "MomentumStrategy", "ATRBreakoutStrategy", "FundingRateStrategy",
    "SuperTrendStrategy", "BollingerStrategy", "IchimokuStrategy",
    "ALL_STRATEGIES",
]
