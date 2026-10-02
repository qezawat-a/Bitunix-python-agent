"""Position sizing — Bitunix's three order units, plus TP/SL price helpers.

Order units (https://www.bitunix.com/hub/helpcenter/article/explanation-of-the-order-units-in-futures-trading?id=170)

Bitunix lets you express an order in three different units. They are three
views of the SAME order, and the exchange ultimately wants a base-coin
quantity (`qty`), so every path here ends at the same number:

    Nominal Value  — the contract's market value, denominated in USDT.
        qty  = nominal / price
        cost = nominal / leverage

    Cost Value     — what you actually put in: initial margin (+ fees).
        qty  = cost * leverage / price
        cost = cost

    Quantity Unit  — size in the BASE asset (BTC in BTCUSDT). The base unit of
        the trading system, and the only one place_order/batch_order accept.
        qty  = qty
        nominal = qty * price
        cost   = qty * price / leverage

The bot sizes by Cost Value: `risk_pct` of the balance is margin committed,
and leverage turns that into notional. See calc_order_unit().
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


class OrderUnitError(ValueError):
    """Raised when an order cannot be sized from the given inputs."""


@dataclass
class OrderUnit:
    """One order, expressed in all three Bitunix units at once."""
    qty: str            # base coin, formatted to basePrecision — what we send
    notional: float     # Nominal Value  (USDT) = qty * price
    required_margin: float   # Cost Value   (USDT) = notional / leverage
    liq_price_long: float
    liq_price_short: float
    unit: str = "COST"      # which unit the caller sized from
    clamped: str = ""       # non-empty when exchange limits forced a resize
    min_qty: float = 0.0
    max_qty: float = 0.0

    @property
    def qty_float(self) -> float:
        return float(self.qty)


def _floor_to(value: float, precision: int) -> float:
    """Round DOWN to the exchange's decimal precision.

    Flooring (never rounding up) keeps the order inside the risk budget; a
    rounded-up qty would quietly exceed it.
    """
    factor = 10 ** precision
    return math.floor(value * factor) / factor


def _ceil_to(value: float, precision: int) -> float:
    factor = 10 ** precision
    return math.ceil(value * factor) / factor


def calc_order_unit(
    balance: float,
    entry_price: float,
    leverage: int,
    risk_pct: float = 1.0,
    base_precision: int = 3,
    maintenance_rate: float = 0.005,   # 0.5% default; get exact from get_position_tiers
    min_trade_volume: Optional[float] = None,
    max_market_order_volume: Optional[float] = None,
) -> OrderUnit:
    """
    Size an order from a balance, priced in Bitunix's Cost Value unit.

    Parameters
    ----------
    balance        : available margin coin balance (USDT)
    entry_price    : estimated fill price
    leverage       : account leverage
    risk_pct       : % of balance committed as margin for this trade
    base_precision : decimals for qty (trading_pairs `basePrecision`)
    maintenance_rate : tier maintenance margin rate (get_position_tiers)
    min_trade_volume / max_market_order_volume : exchange limits from
        trading_pairs (`minTradeVolume` / `maxMarketOrderVolume`), in base coin

    Cost Value sizing:  margin = balance * risk_pct/100
                        notional = margin * leverage
                        qty      = notional / entry_price
    """
    if entry_price <= 0:
        raise OrderUnitError("entry_price must be positive")
    if leverage <= 0:
        raise OrderUnitError("leverage must be positive")
    if balance <= 0:
        raise OrderUnitError("balance must be positive")

    cost_value = balance * (risk_pct / 100.0)
    notional = cost_value * leverage
    raw_qty = notional / entry_price

    # Exchange limits come first, then precision — a limit clamp is a business
    # rule, precision is formatting. Flooring after clamping keeps us under.
    clamped = ""
    if min_trade_volume is not None and raw_qty < min_trade_volume:
        raw_qty = min_trade_volume
        clamped = f"min_qty({min_trade_volume})"
    if max_market_order_volume is not None and raw_qty > max_market_order_volume:
        raw_qty = max_market_order_volume
        clamped = f"max_qty({max_market_order_volume})"

    qty = _floor_to(raw_qty, base_precision)
    if qty <= 0:
        raise OrderUnitError(
            f"sized order rounds to 0 {entry_price} (balance={balance}, "
            f"risk={risk_pct}%, leverage={leverage}x) — raise risk or use a "
            f"cheaper symbol"
        )

    final_notional = qty * entry_price
    required_margin = final_notional / leverage

    # Isolated-margin liquidation estimates. Cross margin backs these with the
    # whole balance, so these are the WORST case, not the real thing —
    # get_pending_positions returns the exchange's own liqPrice; prefer it.
    liq_long = entry_price * (1 - 1 / leverage + maintenance_rate)
    liq_short = entry_price * (1 + 1 / leverage - maintenance_rate)

    return OrderUnit(
        qty=f"{qty:.{base_precision}f}",
        notional=final_notional,
        required_margin=required_margin,
        liq_price_long=liq_long,
        liq_price_short=liq_short,
        unit="COST",
        clamped=clamped,
        min_qty=min_trade_volume or 0.0,
        max_qty=max_market_order_volume or 0.0,
    )


# ── The other two units ──────────────────────────────────────────────────────

def qty_from_nominal(nominal: float, price: float, leverage: int,
                     base_precision: int = 3) -> OrderUnit:
    """Size from Nominal Value (USDT contract size).

    qty = nominal / price;  cost = nominal / leverage.
    """
    if price <= 0 or nominal <= 0:
        raise OrderUnitError("nominal and price must be positive")
    qty = _floor_to(nominal / price, base_precision)
    if qty <= 0:
        raise OrderUnitError("nominal too small for this price at this precision")
    return OrderUnit(
        qty=f"{qty:.{base_precision}f}",
        notional=qty * price,
        required_margin=nominal / leverage,
        liq_price_long=price * (1 - 1 / leverage),
        liq_price_short=price * (1 + 1 / leverage),
        unit="NOMINAL",
    )


def describe_qty_unit(qty: float, price: float, leverage: int) -> dict:
    """Price out a Quantity Unit (base coin) order in the other two units.

        nominal = qty * price
        cost    = qty * price / leverage
    """
    if price <= 0 or leverage <= 0:
        raise OrderUnitError("price and leverage must be positive")
    notional = qty * price
    return {
        "nominal": notional,
        "cost": notional / leverage,
        "margin": notional / leverage,
    }


def split_qty(qty: float, parts: int, base_precision: int = 3) -> list[str]:
    """Split a position size into N parts for a Partial TP/SL ladder.

    `tpQty`/`slQty` on tpsl/place_order are base-coin quantities, so the
    ladder is computed here. The remainder from flooring is folded into the
    LAST part so the parts always sum back to the original size — otherwise
    the final tranche silently rounds the position down and leaves dust open.
    """
    if parts < 1:
        raise OrderUnitError("parts must be >= 1")
    step = _floor_to(qty / parts, base_precision)
    if step <= 0:
        raise OrderUnitError(
            f"qty {qty} is too small to split into {parts} parts at "
            f"{base_precision} decimals"
        )
    out = [step] * (parts - 1)
    out.append(_floor_to(qty - step * (parts - 1), base_precision))
    return [f"{q:.{base_precision}f}" for q in out]


def split_ratio(qty: float, ratios: list[float], base_precision: int = 3) -> list[str]:
    """Split a position by weight ratios, e.g. [0.3, 0.4, 0.3].

    Weights are normalized to their own sum, so [30, 40, 30] and [3, 4, 3]
    behave identically. Remainder goes to the last part.
    """
    if not ratios or any(r <= 0 for r in ratios):
        raise OrderUnitError("ratios must be non-empty and positive")
    total_w = sum(ratios)
    parts = [_floor_to(qty * (r / total_w), base_precision) for r in ratios[:-1]]
    placed = sum(parts)
    last = _floor_to(qty - placed, base_precision)
    if last <= 0:
        raise OrderUnitError(
            f"qty {qty} is too small to split by {ratios} at {base_precision} decimals"
        )
    return [f"{p:.{base_precision}f}" for p in parts + [last]]


# ── TP/SL price helpers ──────────────────────────────────────────────────────

def atr_stop_loss(entry: float, atr: float, side: str, multiplier: float = 2.0) -> float:
    """ATR-based stop-loss price."""
    if side.upper() == "BUY":
        return entry - atr * multiplier
    return entry + atr * multiplier


def atr_take_profit(entry: float, atr: float, side: str, rr: float = 2.0,
                    multiplier: float = 2.0) -> float:
    """ATR-based take-profit using risk:reward ratio."""
    risk = atr * multiplier
    if side.upper() == "BUY":
        return entry + risk * rr
    return entry - risk * rr


def pct_stop_loss(entry: float, pct: float, side: str) -> float:
    """Percentage-based stop loss."""
    if side.upper() == "BUY":
        return entry * (1 - pct / 100)
    return entry * (1 + pct / 100)


def pct_take_profit(entry: float, pct: float, side: str) -> float:
    if side.upper() == "BUY":
        return entry * (1 + pct / 100)
    return entry * (1 - pct / 100)


def price_precision(price: float, quote_precision: int = 2) -> str:
    """Format price to exchange precision (`quotePrecision`)."""
    return f"{price:.{quote_precision}f}"


def placeable(price: float, precision: int, side: str) -> bool:
    """Reject TP/SL prices the exchange would refuse.

    trigger prices are off by 1 unit) can be rejected at placement time.
    """
    if price <= 0:
        return False
    step = 10 ** -precision
    return abs(price / step - round(price / step)) < 1e-6
