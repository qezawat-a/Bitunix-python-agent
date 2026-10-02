"""Ichimoku Cloud strategy."""
from __future__ import annotations

import pandas as pd
from trader import indicators as ta

from trader.strategies.base import BaseStrategy, Direction, Signal


class IchimokuStrategy(BaseStrategy):
    name = "ICHIMOKU"
    description = "Ichimoku Cloud — tenkan/kijun cross above/below cloud"

    def __init__(self, tenkan: int = 9, kijun: int = 26, senkou: int = 52):
        self.tenkan = tenkan
        self.kijun = kijun
        self.senkou = senkou

    def generate(self, df: pd.DataFrame, **kwargs) -> Signal:
        self._validate(df, min_rows=self.senkou + 30)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)

        ichi = ta.ichimoku(h, l, c,
                           tenkan=self.tenkan,
                           kijun=self.kijun,
                           senkou=self.senkou)
        # pandas_ta returns a tuple: (ichimoku_df, span_df)
        if isinstance(ichi, tuple):
            ichi_df = ichi[0]
        else:
            ichi_df = ichi

        tenkan_col = [col for col in ichi_df.columns if "ITS" in col]
        kijun_col  = [col for col in ichi_df.columns if "IKS" in col]
        spa_col    = [col for col in ichi_df.columns if "ISA" in col]
        spb_col    = [col for col in ichi_df.columns if "ISB" in col]
        chikou_col = [col for col in ichi_df.columns if "ICS" in col]

        if not all([tenkan_col, kijun_col, spa_col, spb_col]):
            return Signal(Direction.NEUTRAL, 0.0, "Ichimoku calculation incomplete")

        tenkan_val = ichi_df[tenkan_col[0]].iloc[-1]
        kijun_val  = ichi_df[kijun_col[0]].iloc[-1]
        spa_val    = ichi_df[spa_col[0]].dropna().iloc[-1]
        spb_val    = ichi_df[spb_col[0]].dropna().iloc[-1]
        price      = c.iloc[-1]

        cloud_top    = max(spa_val, spb_val)
        cloud_bottom = min(spa_val, spb_val)

        prev_tenkan = ichi_df[tenkan_col[0]].iloc[-2]
        prev_kijun  = ichi_df[kijun_col[0]].iloc[-2]

        tk_cross_up   = prev_tenkan <= prev_kijun and tenkan_val > kijun_val
        tk_cross_down = prev_tenkan >= prev_kijun and tenkan_val < kijun_val
        above_cloud   = price > cloud_top
        below_cloud   = price < cloud_bottom

        if tk_cross_up and above_cloud:
            return Signal(Direction.BUY, 0.9,
                          f"Tenkan crossed above Kijun, price above cloud ({cloud_top:.2f})",
                          meta={"cloud_top": round(cloud_top, 2),
                                "tenkan": round(tenkan_val, 2),
                                "kijun": round(kijun_val, 2)})
        if tk_cross_down and below_cloud:
            return Signal(Direction.SELL, 0.9,
                          f"Tenkan crossed below Kijun, price below cloud ({cloud_bottom:.2f})",
                          meta={"cloud_bottom": round(cloud_bottom, 2),
                                "tenkan": round(tenkan_val, 2),
                                "kijun": round(kijun_val, 2)})
        if tk_cross_up:
            return Signal(Direction.BUY, 0.55,
                          f"Tenkan/Kijun cross up (inside cloud)",
                          meta={"in_cloud": True})
        if tk_cross_down:
            return Signal(Direction.SELL, 0.55,
                          f"Tenkan/Kijun cross down (inside cloud)",
                          meta={"in_cloud": True})

        return Signal(Direction.NEUTRAL, 0.0,
                      f"Ichimoku neutral — tenkan={tenkan_val:.2f}, kijun={kijun_val:.2f}")
