"""SuperTrend strategy."""
from __future__ import annotations

import pandas as pd
from trader import indicators as ta

from trader.strategies.base import BaseStrategy, Direction, Signal


class SuperTrendStrategy(BaseStrategy):
    name = "SUPERTREND"
    description = "SuperTrend indicator — trend-following with dynamic stop"

    def __init__(self, period: int = 10, multiplier: float = 3.0):
        self.period = period
        self.multiplier = multiplier

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        self._validate(df, min_rows=self.period + 5)
        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)

        # pandas_ta supertrend returns a DataFrame with SUPERT, SUPERTd, SUPERTl, SUPERTs cols
        st = ta.supertrend(h, l, c, length=self.period, multiplier=self.multiplier)
        direction_col = [col for col in st.columns if col.startswith("SUPERTd")]
        if not direction_col:
            return Signal(Direction.NEUTRAL, 0.0, "SuperTrend calculation failed")

        d_col = direction_col[0]
        trend_dir = st[d_col].iloc[-1]
        prev_dir  = st[d_col].iloc[-2]

        # 1 = uptrend, -1 = downtrend
        price = c.iloc[-1]
        st_val = st[st.columns[0]].iloc[-1]   # SUPERT level
        # Do not round the level to a fixed number of decimals here: every
        # symbol has its own quotePrecision (STRKUSDT is 5, BTCUSDT is 2).
        # Formatting with a fixed width in the reason string misleads the
        # operator and the sl_price would be rounded to the wrong precision
        # for the exchange. Round to 6 significant figures so the number is
        # readable but not aggressively rounded.
        st_val_rounded = float(f"{st_val:.6g}")

        if trend_dir == 1 and prev_dir == -1:
            return Signal(Direction.BUY, 0.85,
                          f"SuperTrend flipped to uptrend, ST level={st_val_rounded}",
                          sl_price=st_val_rounded,
                          meta={"supertrend": st_val_rounded})
        if trend_dir == -1 and prev_dir == 1:
            return Signal(Direction.SELL, 0.85,
                          f"SuperTrend flipped to downtrend, ST level={st_val_rounded}",
                          sl_price=st_val_rounded,
                          meta={"supertrend": st_val_rounded})
        # Continuation
        if trend_dir == 1:
            return Signal(Direction.BUY, 0.5, f"SuperTrend continues uptrend")
        return Signal(Direction.SELL, 0.5, f"SuperTrend continues downtrend")
