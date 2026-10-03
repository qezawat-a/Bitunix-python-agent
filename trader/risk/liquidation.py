"""Liquidation mechanics based on Bitunix tiered risk limit model.

Reference: https://support.bitunix.com/hc/en-us/articles/32152530856601

Key concepts:
  - Maintenance Margin Rate (MMR) is tiered by position notional value
  - Isolated margin: liq triggered when margin_balance < maintenance_margin
  - Cross margin:    uses entire account balance as buffer

Liquidation price formulas (isolated, ONE_WAY mode):
  Long:  liq = avg_entry * (1 - IMR + MMR)
         where IMR = 1/leverage
  Short: liq = avg_entry * (1 + IMR - MMR)

In HEDGE mode each direction is treated independently.

This module provides:
  - LiquidationCalc: compute liq price and margin ratio
  - is_near_liquidation: warn when marginRate crosses threshold
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Real BTC-USDT perpetual tiers, transcribed from Bitunix's published tiered
# risk-limit table:
#   https://support.bitunix.com/hc/en-us/articles/32152530856601
#
# IMPORTANT: tiers are PER SYMBOL and they change over time. The table below is
# a BTCUSDT snapshot used only as a fallback — always prefer live values from
# GET /api/v1/futures/position/get_position_tiers, which is the authoritative
# source. See `set_tiers` and `get_mmr`.
#
# Format: (max_notional_usd, mmr, maintenance_margin_usd)
BTC_TIERS = [
    (150_000,      0.004, 0),
    (800_000,      0.005, 600),
    (2_500_000,    0.010, 4_100),
    (10_000_000,   0.025, 29_100),
    (20_000_000,   0.050, 154_100),
    (40_000_000,   0.100, 654_100),
    (60_000_000,   0.250, 1_654_100),
    (100_000_000,  0.500, 6_654_100),
]

# Mutable module-level tier table, so a live fetch can replace the fallback.
DEFAULT_TIERS = list(BTC_TIERS)


def set_tiers(rows) -> None:
    """
    Replace the tier table with live values from get_position_tiers.

    `rows` is the endpoint's data array:
        [{symbol, level, startValue, endValue, leverage, maintenanceMarginRate}, ...]
    Rows must already be filtered to a single symbol and ordered by level.
    Malformed rows are skipped; an empty result leaves the current table.
    """
    global DEFAULT_TIERS
    parsed = []
    for r in rows:
        try:
            end = float(r["endValue"])
            mmr = float(r["maintenanceMarginRate"])
        except (TypeError, ValueError, KeyError):
            continue
        parsed.append((end, mmr, mmr * end))
    if not parsed:
        return
    # The last published tier is open-ended in practice — but only when we
    # have a previous tier to compare against (a single-row table would
    # otherwise IndexError on parsed[-2]).
    if len(parsed) >= 2 and parsed[-1][0] <= parsed[-2][0]:
        parsed[-1] = (float("inf"), parsed[-1][1], parsed[-1][2])
    DEFAULT_TIERS = parsed


def get_mmr(notional: float) -> float:
    """Return the maintenance margin rate for the given notional."""
    for max_notional, mmr, _ in DEFAULT_TIERS:
        if notional <= max_notional:
            return mmr
    return DEFAULT_TIERS[-1][1]


@dataclass
class LiqResult:
    liq_price: float
    margin_ratio: float       # current margin / maintenance margin
    safe: bool                # False when within 10% of liquidation


def calc_liquidation(
    avg_entry: float,
    leverage: int,
    side: str,                # 'LONG' | 'SHORT'
    qty: float,
    current_price: float,
    margin: float,            # isolated margin amount
    notional: Optional[float] = None,
) -> LiqResult:
    """
    Calculate liquidation price for an isolated-margin position.

    The maintenance margin is tiered; we use the simplified formula
    matching Bitunix's published docs.
    """
    notional_val = notional or (avg_entry * qty)
    mmr = get_mmr(notional_val)
    imr = 1.0 / leverage

    if side.upper() == "LONG":
        liq_price = avg_entry * (1 - imr + mmr)
        unrealized_pnl = (current_price - avg_entry) * qty
    else:
        liq_price = avg_entry * (1 + imr - mmr)
        unrealized_pnl = (avg_entry - current_price) * qty

    maintenance_margin = notional_val * mmr
    margin_ratio = (margin + unrealized_pnl) / maintenance_margin if maintenance_margin else 999

    return LiqResult(
        liq_price=liq_price,
        margin_ratio=margin_ratio,
        safe=margin_ratio > 1.1,   # warn when < 110% of maintenance
    )


def is_near_liquidation(margin_ratio: float, threshold: float = 1.15) -> bool:
    """Return True if position is approaching liquidation."""
    return margin_ratio < threshold


def calc_liquidation_cross(
    avg_entry: float,
    side: str,                 # 'LONG' | 'SHORT'
    qty: float,
    balance: float,
    notional: Optional[float] = None,
) -> float:
    """Liquidation price for a CROSS-margin position.

    Under CROSS the whole account balance backs every position, so the isolated
    formula UNDERSTATES the real liquidation distance — badly so at high
    leverage on a small account, which is exactly how a stop-loss ends up
    parked beyond the liquidation price and never fires.

    Cross equity is `balance + unrealised PnL`, and liquidation happens when
    that falls to the maintenance margin:

        LONG   balance + (p - entry)*qty = entry*qty*mmr
               => p = entry*(1 + mmr) - balance/qty
        SHORT  balance + (entry - p)*qty = entry*qty*mmr
               => p = entry*(1 - mmr) + balance/qty

    Note the `balance/qty` term: the smaller the position, the further away
    liquidation sits. Sizing and liquidation have to be solved together.

    `balance` should be the balance actually at risk. Passing `available`
    rather than total equity deliberately UNDER-states the real cross buffer,
    which pulls the computed liq price closer to entry. That errs toward a
    tighter stop, which is the safe direction to be wrong in.
    """
    if qty <= 0:
        return 0.0
    notional_val = notional or (avg_entry * qty)
    mmr = get_mmr(notional_val)
    # Accept the exchange's own vocabulary too. Anything that is not a long
    # falls into the short branch, so a "BUY" reaching here silently returned a
    # liquidation price ABOVE entry — the exact inversion this guards against.
    if str(side).strip().upper() in ("LONG", "BUY"):
        return avg_entry * (1 + mmr) - balance / qty
    return avg_entry * (1 - mmr) + balance / qty
