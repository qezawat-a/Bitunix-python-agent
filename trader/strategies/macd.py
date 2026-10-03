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

        # Histogram normalised against its own recent range, not against the MACD
        # line. The line can sit near zero while the histogram is clearly
        # positive, which made the old ratio explode or collapse arbitrarily;
        # scaling by the rolling range gives a stable 0-1 reading.
        span = float(hist.abs().tail(50).max() or 0.0)

        def strength() -> float:
            if span <= 0:
                return 0.0
            return round(max(0.0, min(1.0, abs(curr_h) / span)), 3)

        meta = {"macd": round(macd_line.iloc[-1], 6),
                "signal": round(sig_line.iloc[-1], 6)}

        if cross_up:
            return Signal(Direction.BUY, strength(),
                          "MACD histogram crossed zero upward", meta=meta)
        if cross_down:
            return Signal(Direction.SELL, strength(),
                          "MACD histogram crossed zero downward", meta=meta)

        # Between zero crossings the histogram still shows which way momentum
        # leans. Firing only on the crossover bar meant this strategy sat idle
        # for long stretches and consensus rarely reached its threshold.
        if curr_h > 0 and above_zero:
            return Signal(Direction.BUY, strength(),
                          f"MACD histogram positive ({curr_h:.6f}), line above zero", meta=meta)
        if curr_h < 0 and not above_zero:
            return Signal(Direction.SELL, strength(),
                          f"MACD histogram negative ({curr_h:.6f}), line below zero", meta=meta)
        return Signal(Direction.NEUTRAL, 0.0, "MACD momentum mixed")
