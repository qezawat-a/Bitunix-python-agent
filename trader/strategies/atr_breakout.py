"""ATR Breakout strategy."""
from __future__ import annotations

import pandas as pd
from trader import indicators as ta

from trader.strategies.base import BaseStrategy, Direction, Signal


class ATRBreakoutStrategy(BaseStrategy):
    name = "ATR_BREAKOUT"
    description = "Price breaks above/below ATR-based channel around a rolling high/low"

    def __init__(self, atr_period: int = 14, atr_mult: float = 1.5, lookback: int = 20):
        self.atr_period = atr_period
        self.atr_mult = atr_mult
        self.lookback = lookback

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        self._validate(df, min_rows=self.atr_period + self.lookback + 5)
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)

        atr = ta.atr(h, l, c, length=self.atr_period)
        atr_val = atr.iloc[-1]

        # Resistance = highest high over lookback; support = lowest low
        resistance = h.iloc[-self.lookback:-1].max()
        support    = l.iloc[-self.lookback:-1].min()

        price = c.iloc[-1]
        prev  = c.iloc[-2]

        breakout_up   = prev <= resistance and price > resistance + atr_val * self.atr_mult
        breakout_down = prev >= support    and price < support    - atr_val * self.atr_mult

        if breakout_up:
            conf = min(1.0, (price - resistance) / (atr_val * self.atr_mult))
            sl   = price - atr_val * self.atr_mult
            tp   = price + atr_val * self.atr_mult * 2
            return Signal(Direction.BUY, round(conf, 3),
                          f"ATR breakout above resistance {resistance:.2f}",
                          sl_price=sl, tp_price=tp,
                          meta={"atr": round(atr_val, 4), "resistance": round(resistance, 2)})
        if breakout_down:
            conf = min(1.0, (support - price) / (atr_val * self.atr_mult))
            sl   = price + atr_val * self.atr_mult
            tp   = price - atr_val * self.atr_mult * 2
            return Signal(Direction.SELL, round(conf, 3),
                          f"ATR breakdown below support {support:.2f}",
                          sl_price=sl, tp_price=tp,
                          meta={"atr": round(atr_val, 4), "support": round(support, 2)})
        return Signal(Direction.NEUTRAL, 0.0, f"No ATR breakout (ATR={atr_val:.4f})")
