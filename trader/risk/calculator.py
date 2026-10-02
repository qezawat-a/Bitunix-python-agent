"""Position sizing, order unit calculator, and risk management.

Based on Bitunix order-unit docs:
https://www.bitunix.com/hub/helpcenter/article/explanation-of-the-order-units-in-futures-trading?id=170

Order qty is denominated in the BASE asset (e.g. BTC in BTCUSDT).

Formula:
  position_value = balance * risk_pct / 100 * leverage
  qty = position_value / entry_price        # in base coin

For isolated margin:
  required_margin = qty * entry_price / leverage
  liquidation price (long)  ≈ avg_open * (1 - 1/leverage + maintenance_rate)
  liquidation price (short) ≈ avg_open * (1 + 1/leverage - maintenance_rate)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class OrderUnit:
    qty: str            # formatted to precision, in base asset
    notional: float     # qty * price
    required_margin: float
    liq_price_long: float
    liq_price_short: float


def calc_order_unit(
    balance: float,
    entry_price: float,
    leverage: int,
    risk_pct: float = 1.0,
    base_precision: int = 3,
    maintenance_rate: float = 0.005,   # 0.5% default; get exact from get_position_tiers
) -> OrderUnit:
    """
    Calculate order quantity and key metrics.

    Parameters
    ----------
    balance        : available USDT balance
    entry_price    : estimated fill price
    leverage       : account leverage setting
    risk_pct       : % of balance to risk per trade (default 1%)
    base_precision : decimal places for qty (from trading_pairs basePrecision)
    maintenance_rate : from position tier table (tiered based on notional)
    """
    if entry_price <= 0 or leverage <= 0:
        raise ValueError("entry_price and leverage must be positive")

    position_value = balance * (risk_pct / 100.0) * leverage
    raw_qty = position_value / entry_price

    # Round down to exchange precision
    factor = 10 ** base_precision
    qty = math.floor(raw_qty * factor) / factor

    notional = qty * entry_price
    required_margin = notional / leverage

    # Simplified liquidation price estimates (isolated margin, cross has different calc)
    liq_long  = entry_price * (1 - 1 / leverage + maintenance_rate)
    liq_short = entry_price * (1 + 1 / leverage - maintenance_rate)

    return OrderUnit(
        qty=f"{qty:.{base_precision}f}",
        notional=notional,
        required_margin=required_margin,
        liq_price_long=liq_long,
        liq_price_short=liq_short,
    )


def atr_stop_loss(entry: float, atr: float, side: str, multiplier: float = 2.0) -> float:
    """ATR-based stop-loss price."""
    if side.upper() == "BUY":
        return entry - atr * multiplier
    return entry + atr * multiplier


def atr_take_profit(entry: float, atr: float, side: str, rr: float = 2.0, multiplier: float = 2.0) -> float:
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
    """Format price to exchange precision."""
    return f"{price:.{quote_precision}f}"
