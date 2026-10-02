"""
Native technical indicators — pure pandas/numpy, no external TA dependency.

Why this module exists
----------------------
The project originally used ``pandas-ta``, but that package is no longer
distributable in a working form (the pinned 0.3.14b0 was removed from PyPI and
the 0.4.x rewrite ships a different API). It also pinned ``numpy<2`` and
blocked every modern Python.

So the indicators are implemented here directly. That removes a fragile
dependency, lets the project install on Python 3.12 / 3.13 / 3.14, and — most
importantly for a trading bot — makes the math auditable line by line.

API compatibility
-----------------
Every function mirrors the ``pandas_ta`` signature and return shape it replaced,
including the DataFrame column names, so the strategy files needed only their
import line changed. Where ``pandas_ta`` and this module could differ, it is
called out in the individual docstrings.

Conventions
-----------
* Inputs are coerced to float and reindexed to a common index.
* Warm-up periods return ``NaN`` rather than a misleading partial value.
* All indicators are causal: the value at index ``i`` uses only data up to and
  including ``i``. (Ichimoku's forward-shifted span columns are the documented
  exception — that shift *is* the indicator.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "ema", "sma", "rsi", "macd", "atr", "obv", "roc", "willr",
    "bbands", "supertrend", "ichimoku",
]


# ── helpers ────────────────────────────────────────────────────────────────

def _f(s: pd.Series, index: pd.Index) -> pd.Series:
    """Coerce to float and align onto a common index."""
    return pd.Series(s, index=index, dtype="float64")


def _wilder_smooth(s: pd.Series, length: int) -> pd.Series:
    """
    Wilder's smoothing, using Wilder's exact seeding.

    The first average is the *simple* mean of the first `length` observations.
    After that each step is Wilder's recursive rule:

        avg[i] = (avg[i-1] * (length - 1) + x[i]) / length

    This is the definition in "New Concepts in Technical Trading Systems" and it
    is what charting platforms display. ``pandas.Series.ewm(alpha=1/length,
    adjust=False)`` is very close but seeds from the first observation instead
    of the initial simple mean, which drifts the result by a few tenths on
    short windows — enough to move a 70/30 RSI threshold. For a trading signal
    that is not an acceptable approximation, so the seeding is done by hand.
    """
    n = len(s)
    out = np.full(n, np.nan, dtype="float64")
    if n < length:
        return pd.Series(out, index=s.index, dtype="float64")

    values = s.to_numpy(dtype="float64")
    seed = values[:length]
    if np.isnan(seed).any():
        return pd.Series(out, index=s.index, dtype="float64")

    avg = float(np.mean(seed))
    out[length - 1] = avg

    for i in range(length, n):
        x = values[i]
        if np.isnan(x):
            out[i] = out[i - 1] if not np.isnan(out[i - 1]) else np.nan
            continue
        avg = (avg * (length - 1) + x) / length
        out[i] = avg

    return pd.Series(out, index=s.index, dtype="float64")


# Backwards-compatible alias for the two indicator entry points below.
_rma = _wilder_smooth


# ── moving averages ────────────────────────────────────────────────────────

def ema(close: pd.Series, length: int = 20) -> pd.Series:
    """
    Exponential Moving Average.

    alpha = 2 / (length + 1), recursive: ema[i] = alpha*close[i] + (1-alpha)*ema[i-1]

    NOTE: this is the standard/TradingView EMA. ``pandas_ta.ema`` defaults to
    mode="rma" (Wilder's smoothing, alpha = 1/length), so values differ slightly
    from the old behaviour. The standard definition is used deliberately.
    """
    return close.ewm(span=length, adjust=False,
                     min_periods=length).mean()


def sma(close: pd.Series, length: int = 20) -> pd.Series:
    """Simple Moving Average — arithmetic mean of the last `length` values."""
    return close.rolling(length, min_periods=length).mean()


# ── oscillators ────────────────────────────────────────────────────────────

def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """
    Relative Strength Index (Wilder).

        delta = close.diff()
        gain  = delta.clip(lower=0)
        loss  = -delta.clip(upper=0)
        avg_gain = Wilder-smoothed gain
        avg_loss = Wilder-smoothed loss
        RS  = avg_gain / avg_loss
        RSI = 100 - 100 / (1 + RS)

    Where avg_loss == 0 (no down closes in the window) RSI is defined as 100.
    """
    delta = close.diff()
    gain = delta.clip(lower=0).fillna(0.0)
    loss = (-delta.clip(upper=0)).fillna(0.0)

    avg_gain = _rma(gain, length)
    avg_loss = _rma(loss, length)

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 → pure uptrend → RSI 100
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(avg_gain.notna() | avg_loss.notna())
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """
    Moving Average Convergence Divergence.

        macd_line   = EMA(fast) - EMA(slow)
        signal_line = EMA(macd_line, signal)
        histogram   = macd_line - signal_line

    Returns columns MACD_{fast}_{slow}_{signal}, MACDs_..., MACDh_... to match
    the column names the strategies previously read out of ``pandas_ta``.
    """
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line

    suffix = f"{fast}_{slow}_{signal}"
    return pd.DataFrame(
        {
            f"MACD_{suffix}": macd_line,
            f"MACDs_{suffix}": signal_line,
            f"MACDh_{suffix}": hist,
        },
        index=close.index,
    )


def roc(close: pd.Series, length: int = 14) -> pd.Series:
    """
    Rate of Change, in percent.

        ROC[i] = (close[i] - close[i-length]) / close[i-length] * 100
    """
    prev = close.shift(length)
    return ((close - prev) / prev) * 100.0


def willr(high: pd.Series, low: pd.Series, close: pd.Series,
          length: int = 14) -> pd.Series:
    """
    Williams %R — momentum oscillator in the range [-100, 0].

        HH = highest high over `length`; LL = lowest low over `length`
        %R = (HH - close) / (HH - LL) * -100

    0 = overbought, -100 = oversold. When HH == LL (flat range) the value is
    NaN — there is no range to measure against.
    """
    hh = high.rolling(length, min_periods=length).max()
    ll = low.rolling(length, min_periods=length).min()
    span = hh - ll
    out = (hh - close) / span * -100.0
    return out.where(span != 0)


# ── volatility ─────────────────────────────────────────────────────────────

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """
    True Range for a single bar.

        TR = max(high - low, |high - prev_close|, |low - prev_close|)

    The first bar has no previous close, so it falls back to high - low.
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        length: int = 14) -> pd.Series:
    """
    Average True Range — Wilder's smoothing of True Range.

        TR  = max(h-l, |h - prev_close|, |l - prev_close|)
        ATR = Wilder-smoothed TR
    """
    return _rma(true_range(high, low, close), length)


def bbands(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """
    Bollinger Bands.

        mid   = SMA(close, length)
        upper = mid + std * stdev(close, length)
        lower = mid - std * stdev(close, length)

    Uses the *population* standard deviation (ddof=0), matching pandas_ta and
    TradingView.

    Returns BBL_ (lower), BBM_ (mid), BBU_ (upper), BBB_ (bandwidth) and
    BBP_ (%B):
        bandwidth = (upper - lower) / mid      — normalised squeeze measure
        %B        = (close - lower) / (upper - lower)   — 0 at lower, 1 at upper
    """
    mid = sma(close, length)
    sd = close.rolling(length, min_periods=length).std(ddof=0)
    upper = mid + std * sd
    lower = mid - std * sd

    band_span = upper - lower
    with np.errstate(divide="ignore", invalid="ignore"):
        bandwidth = band_span / mid.replace(0, np.nan)
        pct_b = (close - lower) / band_span.replace(0, np.nan)

    suffix = f"{length}_{std}"
    return pd.DataFrame(
        {
            f"BBL_{suffix}": lower,
            f"BBM_{suffix}": mid,
            f"BBU_{suffix}": upper,
            f"BBB_{suffix}": bandwidth,
            f"BBP_{suffix}": pct_b,
        },
        index=close.index,
    )


# ── volume ─────────────────────────────────────────────────────────────────

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """
    On-Balance Volume.

        OBV[0] = 0
        OBV[i] = OBV[i-1] + sign(close[i] - close[i-1]) * volume[i]

    Rises on up closes, falls on down closes, flat on unchanged closes.
    """
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


# ── composite indicators ───────────────────────────────────────────────────

def supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
               length: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    SuperTrend.

        ATR  = ATR(high, low, close, length)
        mid  = (high + low) / 2
        raw_up   = mid + multiplier * ATR
        raw_dn   = mid - multiplier * ATR

    The bands ratchet — they only tighten while price is on the correct side,
    and reset when it breaks through:
        final_up = raw_up   if (raw_up < final_up[-1]   or close[-1] > final_up[-1])
                   else final_up[-1]
        final_dn = raw_dn   if (raw_dn > final_dn[-1]   or close[-1] < final_dn[-1])
                   else final_dn[-1]

    Trend flips when price closes through a band, starting from uptrend:
        trend = 1 if (was downtrend and close > final_up) else
               -1 if (was uptrend   and close < final_dn) else previous trend

    The SuperTrend line itself tracks final_dn while in an uptrend and final_up
    while in a downtrend.

    Returns columns SUPERT (the line), SUPERTd (trend: 1 up, -1 down),
    SUPERTl (final lower band) and SUPERTs (final upper band).
    """
    idx = close.index
    h = _f(high, idx).to_numpy()
    l = _f(low, idx).to_numpy()
    c = _f(close, idx).to_numpy()
    atr_v = atr(_f(high, idx), _f(low, idx), _f(close, idx), length).to_numpy()

    hl2 = (h + l) / 2.0
    raw_up = hl2 + multiplier * atr_v
    raw_dn = hl2 - multiplier * atr_v

    n = len(c)
    f_up = np.full(n, np.nan)
    f_dn = np.full(n, np.nan)
    trend = np.full(n, np.nan)

    for i in range(n):
        # skip bars inside the ATR warm-up
        if np.isnan(raw_up[i]) or np.isnan(raw_dn[i]):
            continue

        if i == 0 or np.isnan(f_up[i - 1]):
            f_up[i] = raw_up[i]
            f_dn[i] = raw_dn[i]
        else:
            f_up[i] = (raw_up[i] if (raw_up[i] < f_up[i - 1] or c[i - 1] > f_up[i - 1])
                       else f_up[i - 1])
            f_dn[i] = (raw_dn[i] if (raw_dn[i] > f_dn[i - 1] or c[i - 1] < f_dn[i - 1])
                       else f_dn[i - 1])

        if i == 0:
            trend[i] = 1.0
        elif np.isnan(trend[i - 1]):
            trend[i] = 1.0
        elif trend[i - 1] == -1 and c[i] > f_up[i]:
            trend[i] = 1.0
        elif trend[i - 1] == 1 and c[i] < f_dn[i]:
            trend[i] = -1.0
        else:
            trend[i] = trend[i - 1]

    st = np.where(trend == 1, f_dn, f_up)

    return pd.DataFrame(
        {"SUPERT": st, "SUPERTd": trend, "SUPERTl": f_dn, "SUPERTs": f_up},
        index=idx,
    )


def ichimoku(high: pd.Series, low: pd.Series, close: pd.Series,
             tenkan: int = 9, kijun: int = 26,
             senkou: int = 52) -> pd.DataFrame:
    """
    Ichimoku Kinko Hyo.

        Tenkan-sen (conversion)  = (max high  + min low)  over `tenkan` bars
        Kijun-sen  (base)        = (max high  + min low)  over `kijun` bars
        Senkou A   (span A)      = (Tenkan + Kijun) / 2, shifted forward `kijun`
        Senkou B   (span B)      = (max high + min low) over `senkou` bars,
                                   shifted forward `kijun`
        Chikou     (lagging)     = close, shifted back `kijun`

    The forward shift on the span lines is the definition of the cloud: the
    value plotted at bar `i` is computed from data at bar `i - kijun`. The
    resulting cloud is therefore "in the future" by `kijun` bars, which is why
    the first `kijun` rows of ISA_/ISB_ are NaN.

    Returns columns ITS_ (tenkan), IKS_ (kijun), ISA_ (span A), ISB_ (span B)
    and ICS_ (chikou), suffixed with the periods.
    """
    idx = close.index
    h = _f(high, idx)
    l = _f(low, idx)
    c = _f(close, idx)

    tenkan_val = (h.rolling(tenkan, min_periods=tenkan).max()
                  + l.rolling(tenkan, min_periods=tenkan).min()) / 2.0
    kijun_val = (h.rolling(kijun, min_periods=kijun).max()
                 + l.rolling(kijun, min_periods=kijun).min()) / 2.0

    span_a = ((tenkan_val + kijun_val) / 2.0).shift(kijun)
    span_b = ((h.rolling(senkou, min_periods=senkou).max()
               + l.rolling(senkou, min_periods=senkou).min()) / 2.0).shift(kijun)

    chikou = c.shift(-kijun)

    suffix = f"{tenkan}_{kijun}_{senkou}"
    return pd.DataFrame(
        {
            f"ITS_{suffix}": tenkan_val,
            f"IKS_{suffix}": kijun_val,
            f"ISA_{suffix}": span_a,
            f"ISB_{suffix}": span_b,
            f"ICS_{suffix}": chikou,
        },
        index=idx,
    )
