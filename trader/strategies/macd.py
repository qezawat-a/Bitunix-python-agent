"""MACD signal-line crossover strategy."""
from __future__ import annotations

import pandas as pd
from trader import indicators as ta

from trader.strategies.base import BaseStrategy, Direction, Signal


class MACDStrategy(BaseStrategy):
    name = "MACD"
    description = "MACD histogram + signal crossover"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        self._validate(df, min_rows=self.slow + self.signal + 5)
        c = df["close"].astype(float)
        macd_df = ta.macd(c, fast=self.fast, slow=self.slow, signal=self.signal)
        hist_col = f"MACDh_{self.fast}_{self.slow}_{self.signal}"
        sig_col  = f"MACDs_{self.fast}_{self.slow}_{self.signal}"
        macd_col = f"MACD_{self.fast}_{self.slow}_{self.signal}"

        hist  = macd_df[hist_col]
        macd_line = macd_df[macd_col]
        sig_line  = macd_df[sig_col]

        prev_h, curr_h = hist.iloc[-2], hist.iloc[-1]
        above_zero = macd_line.iloc[-1] > 0
        cross_up   = prev_h < 0 and curr_h >= 0
        cross_down = prev_h > 0 and curr_h <= 0

        if cross_up:
            conf = min(1.0, abs(curr_h) / (abs(macd_line.iloc[-1]) + 1e-9))
            return Signal(Direction.BUY, round(conf, 3),
                          f"MACD histogram crossed zero upward",
                          meta={"macd": round(macd_line.iloc[-1], 6),
                                "signal": round(sig_line.iloc[-1], 6)})
        if cross_down:
            conf = min(1.0, abs(curr_h) / (abs(macd_line.iloc[-1]) + 1e-9))
            return Signal(Direction.SELL, round(conf, 3),
                          f"MACD histogram crossed zero downward",
                          meta={"macd": round(macd_line.iloc[-1], 6),
                                "signal": round(sig_line.iloc[-1], 6)})
        return Signal(Direction.NEUTRAL, 0.0, "MACD no crossover")
