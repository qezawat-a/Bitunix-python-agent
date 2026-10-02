"""Momentum strategy using ROC and Williams %R."""
from __future__ import annotations

import pandas as pd
from trader import indicators as ta

from trader.strategies.base import BaseStrategy, Direction, Signal


class MomentumStrategy(BaseStrategy):
    name = "MOMENTUM"
    description = "Rate of Change + Williams %R momentum confirmation"

    def __init__(self, roc_period: int = 14, willr_period: int = 14):
        self.roc_period = roc_period
        self.willr_period = willr_period

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        self._validate(df, min_rows=max(self.roc_period, self.willr_period) + 5)
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)

        roc = ta.roc(c, length=self.roc_period)
        willr = ta.willr(h, l, c, length=self.willr_period)

        roc_val   = roc.iloc[-1]
        willr_val = willr.iloc[-1]

        strong_bull = roc_val > 1.0 and willr_val > -20     # approaching overbought with strength
        strong_bear = roc_val < -1.0 and willr_val < -80    # approaching oversold with weakness

        if strong_bull:
            conf = min(1.0, roc_val / 5.0)
            return Signal(Direction.BUY, round(conf, 3),
                          f"ROC={roc_val:.2f}%, Williams%R={willr_val:.1f} — strong bullish momentum",
                          meta={"roc": round(roc_val, 3), "willr": round(willr_val, 2)})
        if strong_bear:
            conf = min(1.0, abs(roc_val) / 5.0)
            return Signal(Direction.SELL, round(conf, 3),
                          f"ROC={roc_val:.2f}%, Williams%R={willr_val:.1f} — strong bearish momentum",
                          meta={"roc": round(roc_val, 3), "willr": round(willr_val, 2)})
        return Signal(Direction.NEUTRAL, 0.0, f"Momentum neutral: ROC={roc_val:.2f}%")
