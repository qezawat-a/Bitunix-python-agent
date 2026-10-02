"""Bollinger Bands strategy."""
from __future__ import annotations

import pandas as pd
from trader import indicators as ta

from trader.strategies.base import BaseStrategy, Direction, Signal


class BollingerStrategy(BaseStrategy):
    name = "BOLLINGER"
    description = "Bollinger Band squeeze breakout and %B reversal"

    def __init__(self, period: int = 20, std: float = 2.0, squeeze_pct: float = 0.02):
        self.period = period
        self.std = std
        self.squeeze_pct = squeeze_pct  # band width as fraction of mid = squeeze condition

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        self._validate(df, min_rows=self.period + 10)
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)

        bb = ta.bbands(c, length=self.period, std=self.std)
        lower_col  = [col for col in bb.columns if "BBL" in col][0]
        mid_col    = [col for col in bb.columns if "BBM" in col][0]
        upper_col  = [col for col in bb.columns if "BBU" in col][0]
        width_col  = [col for col in bb.columns if "BBB" in col][0]
        pctb_col   = [col for col in bb.columns if "BBP" in col][0]

        upper = bb[upper_col].iloc[-1]
        lower = bb[lower_col].iloc[-1]
        mid   = bb[mid_col].iloc[-1]
        width = bb[width_col].iloc[-1]   # normalized bandwidth
        pctb  = bb[pctb_col].iloc[-1]    # %B: 0=lower, 0.5=mid, 1=upper
        price = c.iloc[-1]

        prev_pctb = bb[pctb_col].iloc[-2]
        prev_price = c.iloc[-2]

        # Squeeze breakout: width was low, now price breaks upper/lower
        was_squeeze = bb[width_col].iloc[-5:-1].mean() < self.squeeze_pct
        if was_squeeze and price > upper:
            conf = min(1.0, width / self.squeeze_pct)
            return Signal(Direction.BUY, round(conf, 3),
                          f"BB squeeze breakout above upper band ({upper:.2f})",
                          tp_price=upper + (upper - mid),
                          sl_price=mid,
                          meta={"bb_width": round(width, 4), "pctb": round(pctb, 3)})
        if was_squeeze and price < lower:
            conf = min(1.0, width / self.squeeze_pct)
            return Signal(Direction.SELL, round(conf, 3),
                          f"BB squeeze breakout below lower band ({lower:.2f})",
                          tp_price=lower - (mid - lower),
                          sl_price=mid,
                          meta={"bb_width": round(width, 4), "pctb": round(pctb, 3)})

        # Mean-reversion at extremes
        if prev_pctb < 0.05 and pctb > 0.05:
            return Signal(Direction.BUY, 0.65,
                          f"%B bounced off lower band ({lower:.2f})",
                          sl_price=lower * 0.995,
                          meta={"pctb": round(pctb, 3)})
        if prev_pctb > 0.95 and pctb < 0.95:
            return Signal(Direction.SELL, 0.65,
                          f"%B reversal from upper band ({upper:.2f})",
                          sl_price=upper * 1.005,
                          meta={"pctb": round(pctb, 3)})

        return Signal(Direction.NEUTRAL, 0.0, f"BB %B={pctb:.2f}, width={width:.4f}")
