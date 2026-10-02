"""RSI overbought/oversold strategy."""
from __future__ import annotations

import pandas as pd
from trader import indicators as ta

from trader.strategies.base import BaseStrategy, Direction, Signal


class RSIStrategy(BaseStrategy):
    name = "RSI"
    description = "RSI reversal: buy on oversold, sell on overbought with confirmation"

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        self._validate(df, min_rows=self.period + 5)
        c = df["close"].astype(float)
        rsi = ta.rsi(c, length=self.period)
        cur = rsi.iloc[-1]
        prev = rsi.iloc[-2]

        if prev <= self.oversold and cur > self.oversold:
            conf = round((self.oversold - rsi.iloc[-3]) / self.oversold, 3)
            return Signal(Direction.BUY, min(1.0, conf),
                          f"RSI crossed above {self.oversold} (was {prev:.1f})",
                          meta={"rsi": round(cur, 2)})
        if prev >= self.overbought and cur < self.overbought:
            conf = round((rsi.iloc[-3] - self.overbought) / (100 - self.overbought), 3)
            return Signal(Direction.SELL, min(1.0, conf),
                          f"RSI crossed below {self.overbought} (was {prev:.1f})",
                          meta={"rsi": round(cur, 2)})
        return Signal(Direction.NEUTRAL, 0.0, f"RSI neutral at {cur:.1f}")
