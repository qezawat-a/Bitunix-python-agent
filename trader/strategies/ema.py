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
        h = df["high"].astype(float)
        l = df["low"].astype(float)

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

        # Strength is measured in ATR units, not raw percent. As a percentage of
        # price the gap between two EMAs is almost always well under 1%, so a
        # percent-based confidence scored ~0.05 and could never clear
        # min_confidence — this strategy never contributed a vote. A separation
        # of one ATR is a strong, meaningful reading.
        try:
            atr_val = float(ta.atr(h, l, c, length=14).iloc[-1])
        except Exception:
            atr_val = 0.0
        gap = abs(curr_fast - curr_slow)
        strength = min(1.0, (gap / atr_val) / 2.0) if atr_val > 0 else 0.0
        conf = round(strength, 3)

        if golden and above_trend:
            return Signal(Direction.BUY, conf,
                          f"EMA{self.fast} crossed above EMA{self.slow}, price above EMA{self.trend}",
                          meta={"separation_atr": round(gap / atr_val, 2) if atr_val > 0 else 0})
        if death and not above_trend:
            return Signal(Direction.SELL, conf,
                          f"EMA{self.fast} crossed below EMA{self.slow}, price below EMA{self.trend}",
                          meta={"separation_atr": round(gap / atr_val, 2) if atr_val > 0 else 0})

        # Between crossings the EMAs still describe a persistent trend. Reporting
        # only the exact crossover bar meant this strategy returned NEUTRAL for
        # every candle between crossings — hours on a 15m timeframe — so it
        # contributed almost no votes. A confirmed trend is still a reading.
        if above_trend and curr_fast > curr_slow:
            return Signal(Direction.BUY, conf,
                          f"EMA{self.fast} above EMA{self.slow}, price above EMA{self.trend}",
                          meta={"separation_atr": round(gap / atr_val, 2) if atr_val > 0 else 0})
        if not above_trend and curr_fast < curr_slow:
            return Signal(Direction.SELL, conf,
                          f"EMA{self.fast} below EMA{self.slow}, price below EMA{self.trend}",
                          meta={"separation_atr": round(gap / atr_val, 2) if atr_val > 0 else 0})
        return Signal(Direction.NEUTRAL, 0.0, "EMAs mixed against the trend")
