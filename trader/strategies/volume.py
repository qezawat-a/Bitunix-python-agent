"""Volume surge / volume-confirming strategy."""
from __future__ import annotations

import pandas as pd
from trader import indicators as ta

from trader.strategies.base import BaseStrategy, Direction, Signal


class VolumeStrategy(BaseStrategy):
    name = "VOLUME"
    description = "High-volume candle in trending direction; uses OBV divergence as filter"

    def __init__(self, vol_multiplier: float = 2.0, lookback: int = 20):
        self.vol_multiplier = vol_multiplier
        self.lookback = lookback

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        self._validate(df, min_rows=self.lookback + 5)
        c = df["close"].astype(float)
        v = df.get("baseVol", df.get("volume", pd.Series(dtype=float))).astype(float)
        if v.empty:
            return Signal(Direction.NEUTRAL, 0.0, "No volume data")

        avg_vol = v.iloc[-self.lookback:-1].mean()
        curr_vol = v.iloc[-1]
        vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 1.0
        is_surge = vol_ratio >= self.vol_multiplier

        obv = ta.obv(c, v)
        obv_rising = obv.iloc[-1] > obv.iloc[-5]
        price_up   = c.iloc[-1] > c.iloc[-2]

        if is_surge and price_up and obv_rising:
            conf = min(1.0, (vol_ratio - 1) / self.vol_multiplier)
            return Signal(Direction.BUY, round(conf, 3),
                          f"Volume surge {vol_ratio:.1f}x avg on up candle, OBV rising",
                          meta={"vol_ratio": round(vol_ratio, 2)})
        if is_surge and not price_up and not obv_rising:
            conf = min(1.0, (vol_ratio - 1) / self.vol_multiplier)
            return Signal(Direction.SELL, round(conf, 3),
                          f"Volume surge {vol_ratio:.1f}x avg on down candle, OBV falling",
                          meta={"vol_ratio": round(vol_ratio, 2)})
        return Signal(Direction.NEUTRAL, 0.0, f"Volume {vol_ratio:.1f}x avg — no surge signal")
