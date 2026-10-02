"""Funding rate strategy — fade extreme funding or follow funding trend."""
from __future__ import annotations

from typing import List

from trader.strategies.base import BaseStrategy, Direction, Signal


class FundingRateStrategy(BaseStrategy):
    name = "FUNDING_RATE"
    description = "Trade against extreme funding rates (contrarian) or with persistent trend"

    # Extreme threshold: ±0.1% per 8h is considered extreme
    EXTREME_LONG  =  0.001   # extreme positive → longs paying → fade longs → SELL
    EXTREME_SHORT = -0.001   # extreme negative → shorts paying → fade shorts → BUY

    def __init__(self, lookback: int = 3, mode: str = "contrarian"):
        """
        mode: 'contrarian' — trade against extreme rates
              'trend'      — trade with persistent direction
        """
        self.lookback = lookback
        self.mode = mode

    def generate_from_rates(self, rates: List[float]) -> Signal:
        """
        Parameters
        ----------
        rates : list of recent funding rates, newest last
        """
        if not rates:
            return Signal(Direction.NEUTRAL, 0.0, "No funding rate data")

        current = rates[-1]
        avg = sum(rates[-self.lookback:]) / min(len(rates), self.lookback)

        if self.mode == "contrarian":
            if current >= self.EXTREME_LONG:
                conf = min(1.0, current / (self.EXTREME_LONG * 3))
                return Signal(Direction.SELL, round(conf, 3),
                              f"Extreme positive funding {current*100:.4f}% — fade longs",
                              meta={"funding": current, "avg": avg})
            if current <= self.EXTREME_SHORT:
                conf = min(1.0, abs(current) / (abs(self.EXTREME_SHORT) * 3))
                return Signal(Direction.BUY, round(conf, 3),
                              f"Extreme negative funding {current*100:.4f}% — fade shorts",
                              meta={"funding": current, "avg": avg})
        else:  # trend
            if all(r > 0 for r in rates[-self.lookback:]):
                return Signal(Direction.BUY, 0.6,
                              f"Persistent positive funding — bulls in control",
                              meta={"funding": current})
            if all(r < 0 for r in rates[-self.lookback:]):
                return Signal(Direction.SELL, 0.6,
                              f"Persistent negative funding — bears in control",
                              meta={"funding": current})

        return Signal(Direction.NEUTRAL, 0.0, f"Funding rate neutral: {current*100:.4f}%")

    def generate(self, df, **kwargs) -> Signal:
        """Expects kwargs['funding_rates'] = list of floats."""
        rates = kwargs.get("funding_rates", [])
        return self.generate_from_rates(rates)
