"""EMA crossover strategy (fast EMA crosses slow EMA)."""
from __future__ import annotations

import pandas as pd
from trader import indicators as ta

from trader.strategies.base import BaseStrategy, Direction, Signal


class EMAStrategy(BaseStrategy):
    name = "EMA"
    description = "Fast/slow EMA crossover — BUY on golden cross, SELL on death cross"

    def __init__(self, fast: int = 9, slow: int = 21, trend: int = 50):
        self.fast = fast
        self.slow = slow
        self.trend = trend

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        self._validate(df, min_rows=self.trend + 10)
        c = df["close"].astype(float)

        ema_fast = ta.ema(c, length=self.fast)
        ema_slow = ta.ema(c, length=self.slow)
        ema_trend = ta.ema(c, length=self.trend)

        prev_fast, curr_fast = ema_fast.iloc[-2], ema_fast.iloc[-1]
        prev_slow, curr_slow = ema_slow.iloc[-2], ema_slow.iloc[-1]
        trend_val = ema_trend.iloc[-1]
        price = c.iloc[-1]

        golden = prev_fast < prev_slow and curr_fast > curr_slow
        death  = prev_fast > prev_slow and curr_fast < curr_slow
        above_trend = price > trend_val

        if golden and above_trend:
            conf = min(1.0, abs(curr_fast - curr_slow) / curr_slow * 100)
            return Signal(Direction.BUY, round(conf, 3),
                          f"EMA{self.fast} crossed above EMA{self.slow}, price above EMA{self.trend}")
        if death and not above_trend:
            conf = min(1.0, abs(curr_fast - curr_slow) / curr_slow * 100)
            return Signal(Direction.SELL, round(conf, 3),
                          f"EMA{self.fast} crossed below EMA{self.slow}, price below EMA{self.trend}")
        return Signal(Direction.NEUTRAL, 0.0, "No EMA crossover signal")
