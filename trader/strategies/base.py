"""Base strategy interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

import pandas as pd


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"


@dataclass
class Signal:
    direction: Direction
    confidence: float          # 0.0 → 1.0
    reason: str
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy:
    """All strategies inherit this and implement generate()."""
    name: str = "base"
    description: str = ""

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        """
        Parameters
        ----------
        df : OHLCV DataFrame with columns: open, high, low, close, baseVol (volume)
             sorted ascending by time, at least 200 rows recommended.
        """
        raise NotImplementedError

    def _validate(self, df: pd.DataFrame, min_rows: int = 30) -> None:
        if len(df) < min_rows:
            raise ValueError(f"{self.name} needs at least {min_rows} candles, got {len(df)}")
        required = {"open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")
