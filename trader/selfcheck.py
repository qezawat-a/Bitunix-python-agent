"""
Sanity checks for trader/indicators.py and the strategy layer.

These verify the indicator math against hand-computable reference values and
against pandas' own rolling/ewm implementations where the definitions coincide.
They are not a substitute for reviewing the formulas in trader/indicators.py,
but they catch sign errors, off-by-one warm-ups and column-name regressions.

Run:  .venv312/bin/python -m trader.selfcheck
"""
from __future__ import annotations

import math
import sys

import numpy as np
import pandas as pd

from trader import indicators as ta

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def approx(a: float, b: float, tol: float = 1e-9) -> bool:
    if a is None or b is None:
        return False
    if math.isnan(a) and math.isnan(b):
        return True
    if math.isnan(a) or math.isnan(b):
        return False
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def series(values) -> pd.Series:
    return pd.Series([float(v) for v in values])


def test_ema() -> None:
    print("\nEMA")
    s = series(range(1, 31))                       # 1..30
    e = ta.ema(s, 3)
    # warm-up must be NaN for the first `length-1` bars
    check("ema warm-up NaN", np.isnan(e.iloc[0]) and np.isnan(e.iloc[1]))
    check("ema first value finite", not np.isnan(e.iloc[2]))
    # alpha = 2/(3+1) = 0.5, seeded by pandas' ewm with the first observation
    check("ema recursion",
          approx(e.iloc[3], 0.5 * 4 + 0.5 * e.iloc[2]),
          f"got {e.iloc[3]}, want {0.5 * 4 + 0.5 * e.iloc[2]}")
    # matches pandas' own exponential construction
    check("ema matches pandas ewm",
          approx(e.iloc[-1], s.ewm(span=3, adjust=False,
                                  min_periods=3).mean().iloc[-1]))


def test_rsi() -> None:
    print("\nRSI")
    # Wilder's worked example from "New Concepts in Technical Trading Systems"
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    r = ta.rsi(series(closes), 14)
    # Wilder's worked example from "New Concepts in Technical Trading Systems".
    # The book prints 70.53; recomputing its seed by hand gives 70.46 (the book
    # rounds its intermediate averages), so assert against the recomputed value.
    check("rsi Wilder reference value", approx(r.iloc[-1], 70.4641, 1e-4),
          f"got {r.iloc[-1]:.4f}, want ~70.46")

    # a monotonically rising series has no down closes -> RSI 100
    up = ta.rsi(series(range(1, 40)), 14)
    check("rsi all-gains -> 100", approx(up.iloc[-1], 100.0),
          f"got {up.iloc[-1]}")

    # a monotonically falling series has no up closes -> RSI 0
    dn = ta.rsi(series(range(60, 21, -1)), 14)
    check("rsi all-losses -> 0", approx(dn.iloc[-1], 0.0),
          f"got {dn.iloc[-1]}")

    # bounded in [0, 100]
    noisy = ta.rsi(series(np.sin(np.linspace(0, 40, 200)) * 10 + 100), 14)
    finite = noisy.dropna()
    check("rsi bounded 0..100",
          finite.min() >= 0.0 and finite.max() <= 100.0,
          f"range {finite.min()}..{finite.max()}")


def test_macd() -> None:
    print("\nMACD")
    s = series(np.linspace(100, 140, 120) + np.sin(np.arange(120)) * 2)
    m = ta.macd(s, 12, 26, 9)
    expected_cols = {"MACD_12_26_9", "MACDs_12_26_9", "MACDh_12_26_9"}
    check("macd column names", set(m.columns) == expected_cols, str(list(m.columns)))

    fast = ta.ema(s, 12)
    slow = ta.ema(s, 26)
    line = fast - slow
    check("macd line = ema_fast - ema_slow",
          approx(m["MACD_12_26_9"].iloc[-1], line.iloc[-1]))

    # histogram is line minus signal, by definition
    check("macd hist = macd - signal",
          approx(m["MACDh_12_26_9"].iloc[-1],
                 m["MACD_12_26_9"].iloc[-1] - m["MACDs_12_26_9"].iloc[-1]))

    # signal is the EMA of the macd line
    check("macd signal = ema(macd line)",
          approx(m["MACDs_12_26_9"].iloc[-1], ta.ema(line, 9).iloc[-1]))


def test_atr() -> None:
    print("\nATR / True Range")
    h = series([10, 11, 12, 11, 10])
    l = series([9, 10, 11, 10, 9])
    c = series([9.5, 10.5, 11.5, 10.5, 9.5])
    tr = ta.true_range(h, l, c)
    # bar 0 has no previous close -> high - low = 1
    check("tr bar0 = high-low", approx(tr.iloc[0], 1.0))
    # bar 1: h-l=1, |h-prev_c|=|11-9.5|=1.5, |l-prev_c|=|10-9.5|=0.5 -> 1.5
    check("tr bar1 uses prev close", approx(tr.iloc[1], 1.5),
          f"got {tr.iloc[1]}")
    a = ta.atr(h, l, c, 3)
    check("atr warm-up NaN", np.isnan(a.iloc[0]))
    check("atr positive", a.dropna().iloc[-1] > 0)

    # Wilder ATR must exceed simple mean TR when ranges expand
    h2 = series(list(np.arange(50, 150)))
    l2 = h2 - 5
    c2 = (h2 + l2) / 2
    a2 = ta.atr(h2, l2, c2, 14)
    check("atr equals constant TR when range is constant",
          approx(a2.dropna().iloc[-1], 5.0, 1e-6),
          f"got {a2.dropna().iloc[-1]}")


def test_bollinger() -> None:
    print("\nBollinger Bands")
    s = series([10, 12, 14, 16, 18, 20, 22, 24, 26, 28,
                10, 12, 14, 16, 18, 20, 22, 24, 26, 28])
    bb = ta.bbands(s, 10, 2.0)
    check("bbands column names",
          set(bb.columns) == {f"{k}_10_2.0" for k in ("BBL", "BBM", "BBU", "BBB", "BBP")},
          str(list(bb.columns)))

    mid = s.rolling(10).mean().iloc[-1]
    sd = s.rolling(10).std(ddof=0).iloc[-1]
    check("bb mid = SMA", approx(bb["BBM_10_2.0"].iloc[-1], mid))
    check("bb upper = mid + 2sd", approx(bb["BBU_10_2.0"].iloc[-1], mid + 2 * sd))
    check("bb lower = mid - 2sd", approx(bb["BBL_10_2.0"].iloc[-1], mid - 2 * sd))

    pctb = bb["BBP_10_2.0"].iloc[-1]
    lo, up = bb["BBL_10_2.0"].iloc[-1], bb["BBU_10_2.0"].iloc[-1]
    check("bb %B formula", approx(pctb, (s.iloc[-1] - lo) / (up - lo)))
    check("bb bandwidth formula",
          approx(bb["BBB_10_2.0"].iloc[-1], (up - lo) / mid))

    # %B is the position of close within the band: above the mid band -> >0.5,
    # below -> <0.5. (It only reaches exactly 1.0 when close sits on the
    # upper band itself, which cannot be arranged in a self-referential way
    # because appending the value changes the band.)
    rising = series([10, 12, 14, 16, 18, 20, 22])
    rb = ta.bbands(rising, 7, 2.0)
    check("bb %B above mid when close is high",
          rb["BBP_7_2.0"].iloc[-1] > 0.5, f"got {rb['BBP_7_2.0'].iloc[-1]}")
    falling = series([22, 20, 18, 16, 14, 12, 10])
    fb = ta.bbands(falling, 7, 2.0)
    check("bb %B below mid when close is low",
          fb["BBP_7_2.0"].iloc[-1] < 0.5, f"got {fb['BBP_7_2.0'].iloc[-1]}")


def test_obv() -> None:
    print("\nOBV")
    c = series([10, 11, 12, 11, 12])
    v = series([100, 200, 300, 400, 500])
    o = ta.obv(c, v)
    # 0, +200, +300, -400, +500
    expected = [0, 200, 500, 100, 600]
    check("obv running total", [approx(x, y) for x, y in zip(o, expected)] == [True] * 5,
          f"got {list(o)}")


def test_roc() -> None:
    print("\nROC")
    s = series([100, 110, 121, 133.1])
    r = ta.roc(s, 2)
    check("roc warm-up NaN", np.isnan(r.iloc[1]))
    # shift(2) at bar 3 references bar 1 (110), so 133.1/110 - 1 = 21%
    check("roc value", approx(r.iloc[3], (133.1 - 110) / 110 * 100),
          f"got {r.iloc[3]}")


def test_willr() -> None:
    print("\nWilliams %R")
    h = series([10, 12, 14])
    l = series([8, 10, 12])
    c = series([9, 11, 13])
    w = ta.willr(h, l, c, 3)
    # HH=14, LL=8, close=13 -> (14-13)/(14-8)*-100 = -16.666...
    check("willr value", approx(w.iloc[-1], -100 / 6), f"got {w.iloc[-1]}")
    check("willr bounded -100..0",
          approx(max(w.iloc[-1], -100.0), w.iloc[-1]) and w.iloc[-1] <= 0)

    # close at the highest high -> 0; close at the lowest low -> -100
    top = ta.willr(series([10, 12, 14]), series([8, 10, 12]), series([10, 12, 14]), 3)
    check("willr at high -> 0", approx(top.iloc[-1], 0.0), f"got {top.iloc[-1]}")
    # %R is -100 only when close is the lowest low of the lookback window.
    # With h=10,12,14 and l=8,10,12 the window low is 8, so close must be 8.
    bot = ta.willr(series([10, 12, 14]), series([8, 10, 12]), series([9, 10, 8]), 3)
    check("willr at low -> -100", approx(bot.iloc[-1], -100.0), f"got {bot.iloc[-1]}")


def test_supertrend() -> None:
    print("\nSuperTrend")
    n = 200
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 0.6, n))
    high = close + rng.uniform(0.1, 0.8, n)
    low = close - rng.uniform(0.1, 0.8, n)
    c, h, l = series(close), series(high), series(low)

    st = ta.supertrend(h, l, c, 10, 3.0)
    check("supertrend column names",
          set(st.columns) == {"SUPERT", "SUPERTd", "SUPERTl", "SUPERTs"},
          str(list(st.columns)))
    check("supertrend warm-up NaN", np.isnan(st["SUPERTd"].iloc[0]))
    check("supertrend line finite later", not np.isnan(st["SUPERT"].iloc[-1]))

    # the SuperTrend line must equal the band for the current trend
    d = st["SUPERTd"].iloc[-1]
    expected_line = st["SUPERTl"].iloc[-1] if d == 1 else st["SUPERTs"].iloc[-1]
    check("supertrend line matches trend band",
          approx(st["SUPERT"].iloc[-1], expected_line))

    # trend can only be +1 or -1 once warm
    tvals = st["SUPERTd"].dropna().unique()
    check("supertrend trend in {-1,1}", set(tvals) <= {-1.0, 1.0}, str(tvals))

    # in an uptrend the line is below price; in a downtrend it is above
    in_uptrend = st[st["SUPERTd"] == 1]
    check("uptrend: line under price", (in_uptrend["SUPERT"] < c.reindex(in_uptrend.index)).mean() > 0.9)
    in_downtrend = st[st["SUPERTd"] == -1]
    if len(in_downtrend):
        check("downtrend: line over price",
              (in_downtrend["SUPERT"] > c.reindex(in_downtrend.index)).mean() > 0.9)

    # a flat market must not invent swings
    flat_c = series([50.0] * 120)
    flat = ta.supertrend(flat_c, flat_c + 1, flat_c - 1, 10, 3.0)
    check("flat market -> single trend state",
          flat["SUPERTd"].dropna().nunique() == 1,
          f"states {flat['SUPERTd'].dropna().unique()}")


def test_ichimoku() -> None:
    print("\nIchimoku")
    n = 200
    rng = np.random.default_rng(11)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + rng.uniform(0.2, 1.0, n)
    low = close - rng.uniform(0.2, 1.0, n)
    h, l, c = series(high), series(low), series(close)

    ic = ta.ichimoku(h, l, c)
    check("ichimoku column names",
          set(ic.columns) == {f"{k}_9_26_52" for k in ("ITS", "IKS", "ISA", "ISB", "ICS")},
          str(list(ic.columns)))

    # tenkan = (max9 + min9) / 2
    exp_tenkan = (h.rolling(9).max().iloc[-1] + l.rolling(9).min().iloc[-1]) / 2
    check("tenkan formula", approx(ic["ITS_9_26_52"].iloc[-1], exp_tenkan))

    exp_kijun = (h.rolling(26).max().iloc[-1] + l.rolling(26).min().iloc[-1]) / 2
    check("kijun formula", approx(ic["IKS_9_26_52"].iloc[-1], exp_kijun))

    # span columns are shifted forward by kijun bars
    check("span A warm-up NaN (shifted forward)",
          np.isnan(ic["ISA_9_26_52"].iloc[25]))
    raw_a = ((h.rolling(9).max() + l.rolling(9).min()) / 2
             + (h.rolling(26).max() + l.rolling(26).min()) / 2) / 2
    check("span A shifted by kijun",
          approx(ic["ISA_9_26_52"].iloc[-1], raw_a.shift(26).iloc[-1]))

    # chikou is close shifted back kijun bars
    check("chikou = close shifted back",
          approx(ic["ICS_9_26_52"].iloc[-27], c.iloc[-1]))

    # the cloud should be finite well past warm-up
    check("span A finite late", not np.isnan(ic["ISA_9_26_52"].iloc[-1]))
    check("span B finite late", not np.isnan(ic["ISB_9_26_52"].iloc[-1]))


def test_strategies() -> None:
    print("\nStrategies (all 11)")
    from trader.strategies import ALL_STRATEGIES
    from trader.strategies.base import Direction

    n_candles = 300
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 0.7, n_candles))
    df = pd.DataFrame({
        "open": close + rng.normal(0, 0.2, n_candles),
        "high": close + rng.uniform(0.3, 1.2, n_candles),
        "low": close - rng.uniform(0.3, 1.2, n_candles),
        "close": close,
        "baseVol": rng.uniform(1000, 9000, n_candles),
    })
    rates = [0.0002, -0.0001, 0.0003, 0.0004, -0.0002]

    n = len(ALL_STRATEGIES)
    check("all 10 strategies registered", n == 10, f"got {n}")
    check("10 names unique", len(set(ALL_STRATEGIES)) == n, str(list(ALL_STRATEGIES)))
    check("FUNDING_RATE present", "FUNDING_RATE" in ALL_STRATEGIES)

    for key, cls in ALL_STRATEGIES.items():
        strat = cls()
        check(f"{key} uses its registry name", strat.name == key,
              f"class reports {strat.name!r}")
        if key == "FUNDING_RATE":
            sig = strat.generate(df, funding_rates=rates)
        else:
            sig = strat.generate(df)
        ok = (sig.direction in (Direction.BUY, Direction.SELL, Direction.NEUTRAL)
              and 0.0 <= sig.confidence <= 1.0
              and isinstance(sig.reason, str) and len(sig.reason) > 0)
        check(f"{key} returns a valid Signal", ok,
              f"dir={sig.direction} conf={sig.confidence} reason={sig.reason!r}")

    # strategies must tolerate a flat market without raising
    flat = pd.DataFrame({
        "open": [50.0] * 120, "high": [51.0] * 120, "low": [49.0] * 120,
        "close": [50.0] * 120, "baseVol": [1000.0] * 120,
    })
    for key, cls in ALL_STRATEGIES.items():
        if key == "FUNDING_RATE":
            continue
        try:
            sig = cls().generate(flat)
            check(f"{key} survives a flat market",
                  sig.direction in (Direction.BUY, Direction.SELL, Direction.NEUTRAL))
        except Exception as exc:
            check(f"{key} survives a flat market", False, f"raised {exc!r}")

    # strategies must reject a too-short DataFrame rather than silently proceeding
    short = df.head(5)
    for key, cls in ALL_STRATEGIES.items():
        if key == "FUNDING_RATE":
            continue
        try:
            cls().generate(short)
            check(f"{key} rejects too-few candles", False, "no error raised")
        except ValueError:
            check(f"{key} rejects too-few candles", True)
        except Exception as exc:
            check(f"{key} rejects too-few candles", False, f"raised {exc!r}")

    # a rising market should produce at least one bullish signal across the set
    bullish = 0
    for key, cls in ALL_STRATEGIES.items():
        if key == "FUNDING_RATE":
            continue
        up = df.copy()
        up["close"] = pd.Series(close).cumsum() + 5
        if cls().generate(up).direction == Direction.BUY:
            bullish += 1
    check("some strategies fire BUY on a rising tape", bullish > 0,
          f"{bullish} of 9 fired")


def test_no_pandas_ta() -> None:
    print("\nDependency hygiene")
    try:
        import pandas_ta  # noqa: F401
        check("pandas-ta is no longer imported", False, "still installed/importable")
    except ImportError:
        check("pandas-ta is no longer imported", True)

    import trader.strategies.ema as m
    check("strategies import trader.indicators",
          getattr(m.ta, "__name__", "") == "trader.indicators",
          str(getattr(m.ta, "__name__", "?")))


def main() -> int:
    print("=" * 62)
    print("trader/indicators.py + strategies self-check")
    print("=" * 62)
    print(f"python {sys.version.split()[0]} | numpy {np.__version__} | "
          f"pandas {pd.__version__}")

    for fn in (test_ema, test_rsi, test_macd, test_atr, test_bollinger,
               test_obv, test_roc, test_willr, test_supertrend,
               test_ichimoku, test_strategies, test_no_pandas_ta):
        fn()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
