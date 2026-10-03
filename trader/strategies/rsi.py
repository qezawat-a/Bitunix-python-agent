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

        # Confidence measures how deep RSI went past the threshold, over the
        # remaining distance to the scale's floor. The previous version read
        # rsi.iloc[-3] — three bars back, after the bounce had already started
        # — and divided by the threshold itself, so even a genuine oversold
        # touch scored ~0.17 and fell under min_confidence. Scaling against the
        # half-range makes a real extreme approach 1.0.
        def depth(value: float) -> float:
            return max(0.0, min(1.0, (self.oversold - value) / (self.oversold / 2.0)))

        def depth_hi(value: float) -> float:
            return max(0.0, min(1.0, (value - self.overbought) / ((100 - self.overbought) / 2.0)))

        if prev <= self.oversold and cur > self.oversold:
            return Signal(Direction.BUY, round(depth(min(prev, cur)), 3),
                          f"RSI crossed above {self.oversold} (was {prev:.1f})",
                          meta={"rsi": round(cur, 2)})
        if prev >= self.overbought and cur < self.overbought:
            return Signal(Direction.SELL, round(depth_hi(max(prev, cur)), 3),
                          f"RSI crossed below {self.overbought} (was {prev:.1f})",
                          meta={"rsi": round(cur, 2)})

        # Between crossings RSI still reads the state of the market. Returning
        # NEUTRAL here meant this strategy only ever voted on the exact
        # reversal bar, which is most of the reason consensus never formed.
        if cur <= self.oversold:
            return Signal(Direction.BUY, round(depth(cur), 3),
                          f"RSI oversold at {cur:.1f}",
                          meta={"rsi": round(cur, 2)})
        if cur >= self.overbought:
            return Signal(Direction.SELL, round(depth_hi(cur), 3),
                          f"RSI overbought at {cur:.1f}",
                          meta={"rsi": round(cur, 2)})
        return Signal(Direction.NEUTRAL, 0.0, f"RSI neutral at {cur:.1f}")
