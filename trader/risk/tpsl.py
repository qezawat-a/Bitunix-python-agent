"""The four Bitunix take-profit / stop-loss models.

Reference:
  https://www.bitunix.com/uk-ua/hub/helpcenter/article/bitunix-futures-position-a-guide-to-four-take-profit-and-stop-loss-methods-web?id=290

Bitunix offers exactly four TPSL methods. Each maps to a different REST call:

  1. POSITION  — one TP/SL for the WHOLE position.
                 POST /api/v1/futures/tpsl/position/place_order
                 On trigger it closes the entire position at market price using
                 the quantity held AT THAT MOMENT. One per position.

  2. PARTIAL   — several closing levels on one position.
                 POST /api/v1/futures/tpsl/place_order
                 Sized with tpQty / slQty in BASE COIN. At least one of
                 tpQty or slQty is required, and at least one of tpPrice or
                 slPrice. Levels close progressively as price is reached.

  3. TRAILING  — dynamic: activation price + retracement range + quantity.
                 There is NO dedicated REST endpoint. Bitunix exposes it in
                 the web/app order panel only, so this bot implements it
                 client-side: once price passes the activation price the stop
                 is ratcheted along the best price, and the position TP/SL
                 order is REWRITTEN via tpsl/position/modify_order.
                 Two retrace modes, matching the UI: RATIO (percentage off the
                 peak) and INTERVAL (absolute distance off the peak).

  4. ACCOUNT   — account-level TP/SL across ALL positions.
                 No dedicated endpoint either: the bot watches total account
             PnL and calls POST /api/v1/futures/trade/close_all_position
                 when the threshold is crossed. 0 disables.

On top of any of them, the two mid-management overlays Bitunix documents run
locally: breakeven (move SL to entry) and trailing (ratchet the SL). Both are
applied by modifying the position's TPSL order, never by waiting for a fill.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from trader.risk.calculator import split_ratio


class TPSLMethod(Enum):
    """The four methods Bitunix offers. Values match `tpsl_method` setting."""
    POSITION = "POSITION"   # whole-position TP/SL
    PARTIAL = "PARTIAL"     # scale-out ladder
    TRAILING = "TRAILING"   # activation + retracement
    ACCOUNT = "ACCOUNT"     # account-wide guard

    @classmethod
    def parse(cls, raw: str) -> "TPSLMethod":
        try:
            return cls(str(raw).strip().upper())
        except ValueError:
            return cls.POSITION


class TrailingMode(Enum):
    """Retrace measurement for TRAILING TP/SL (the UI's Ratio / Interval)."""
    RATIO = "RATIO"      # percent of the peak price
    INTERVAL = "INTERVAL"  # absolute price distance from the peak

    @classmethod
    def parse(cls, raw: str) -> "TrailingMode":
        try:
            return cls(str(raw).strip().upper())
        except ValueError:
            return cls.RATIO


class TPSLAction(Enum):
    NONE = "none"
    BREAKEVEN = "breakeven"
    TRAILING_START = "trailing_start"
    TRAILING_UPDATE = "trailing_update"
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    LADDER = "ladder"          # partial ladder needs placing


@dataclass
class TPSLLevel:
    """One rung of a Partial TP/SL ladder."""
    price: float
    qty: str
    kind: str = "TP"          # TP | SL
    placed: bool = False

    def payload(self, precision: int) -> dict:
        p = f"{self.price:.{precision}f}"
        return {f"{self.kind.lower()}Price": p, f"{self.kind.lower()}Qty": self.qty,
                f"{self.kind.lower()}OrderType": "MARKET",
                f"{self.kind.lower()}StopType": "LAST_PRICE"}


@dataclass
class TPSLUpdate:
    action: TPSLAction
    old_sl: Optional[float] = None
    new_sl: Optional[float] = None
    old_tp: Optional[float] = None
    new_tp: Optional[float] = None
    reason: str = ""
    payload: Optional[dict] = None    # REST body to apply, when action needs it


class PositionTPSL:
    """
    Per-position TPSL state machine for one position.

    Breakeven and trailing are overlays that work with every method; the
    method only decides how the INITIAL levels are shaped:

        POSITION  one TP, one SL, full size
        PARTIAL   a ladder of partial closes (default 30/40/30)
        TRAILING  activation price + retrace stop, no fixed TP
        ACCOUNT   handled account-wide; a position still gets its own SL
    """

    def __init__(
        self,
        entry_price: float,
        side: str,                       # LONG | SHORT
        atr_value: float,
        method: str = "POSITION",
        breakeven_threshold_pct: float = 2.0,
        trailing_trigger_roi_pct: float = 5.0,
        trailing_stop_pct: float = 0.5,
        trailing_mode: str = "RATIO",
        trailing_distance: float = 0.3,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        qty: Optional[float] = None,
        partial_ratios: Optional[List[float]] = None,
        quote_precision: int = 2,
    ):
        if entry_price <= 0:
            raise ValueError("entry_price must be positive")
        self.entry_price = entry_price
        self.side = side                  # LONG or SHORT
        self.atr = atr_value
        self.method = TPSLMethod.parse(method)
        self.trailing_mode = TrailingMode.parse(trailing_mode)
        self.trailing_distance = trailing_distance
        self.quote_precision = quote_precision
        self.qty = qty
        self.partial_ratios = partial_ratios or [30, 40, 30]
        # Base-coin precision for ladder splits, from trading_pairs
        # `basePrecision`. Set before the ladder is built so the first build
        # already splits at exchange precision rather than the 3-decimal guess.
        self._qty_precision = 3

        self.breakeven_threshold = breakeven_threshold_pct / 100.0
        self.trailing_trigger_roi = trailing_trigger_roi_pct / 100.0
        self.trailing_stop_pct_val = trailing_stop_pct / 100.0

        self.breakeven_set = False
        self.trailing_active = False
        self.best_price = entry_price

        long = side == "LONG"

        # Initial SL/TP: caller-supplied wins, else ATR-based (1x risk / 2x
        # reward, so R:R = 2 — the same shape the engine computes itself).
        self.sl_price = sl_price if sl_price is not None else (
            entry_price - atr_value if long else entry_price + atr_value
        )
        self.tp_price = tp_price if tp_price is not None else (
            entry_price + atr_value * 2 if long else entry_price - atr_value * 2
        )

        # TRAILING: activation is the price the stop starts ratcheting from,
        # which is the ROI trigger expressed as a price.
        self.activation_price = entry_price * (1 + self.trailing_trigger_roi) if long \
            else entry_price * (1 - self.trailing_trigger_roi)

        self.ladder: List[TPSLLevel] = []
        if self.method is TPSLMethod.PARTIAL:
            self.ladder = self._build_ladder()
        elif self.method is TPSLMethod.TRAILING:
            # No fixed target — the retrace stop IS the exit. Leaving tp_price
            # set here would place it BELOW entry on a long, and the position
            # would report an instant TP hit on the very first update.
            self.tp_price = None

    # ── Partial ladder ──────────────────────────────────────────────────

    def _build_ladder(self) -> List[TPSLLevel]:
        """Split the position into partial TP rungs off the initial SL risk.

        Each rung takes a third of the SL distance as profit, so the ladder
        scales out as the trade proves itself. The SL covers the whole
        position until it is hit.
        """
        if not self.qty:
            return []
        risk = abs(self.entry_price - self.sl_price) or self.atr
        long = self.side == "LONG"
        parts = split_ratio(self.qty, self.partial_ratios, self._qty_precision)
        levels: List[TPSLLevel] = []
        for i, part in enumerate(parts):
            price = (self.entry_price + risk * (i + 1)) if long \
                else (self.entry_price - risk * (i + 1))
            levels.append(TPSLLevel(price=float(price), qty=part, kind="TP"))
        # The ladder's top rung is the headline TP.
        self.tp_price = levels[-1].price
        return levels

    def set_qty_precision(self, precision: int) -> None:
        """Record basePrecision so the ladder splits at exchange precision."""
        self._qty_precision = precision
        if self.method is TPSLMethod.PARTIAL:
            self.ladder = self._build_ladder()

    # ── Trailing ────────────────────────────────────────────────────────

    def _retrace_stop(self, peak: float) -> float:
        """Stop price that sits `trailing_stop_pct` off `peak`."""
        if self.trailing_mode is TrailingMode.INTERVAL:
            offset = self.trailing_distance
            return peak - offset if self.side == "LONG" else peak + offset
        pct = self.trailing_stop_pct_val
        return peak * (1 - pct) if self.side == "LONG" else peak * (1 + pct)

    # ── Main update ──────────────────────────────────────────────────────

    def update(self, current_price: float, unrealized_pnl_pct: float) -> TPSLUpdate:
        """Fold a new price into the state machine and report any action."""
        long = self.side == "LONG"

        # Track the peak for the trailing stop.
        if long:
            if current_price > self.best_price:
                self.best_price = current_price
        elif current_price < self.best_price:
            self.best_price = current_price

        # 1. Hard exits first — a hit level is terminal for this position.
        if self.tp_price and (current_price >= self.tp_price if long
                              else current_price <= self.tp_price):
            return TPSLUpdate(TPSLAction.TP_HIT, reason=f"price hit TP {self.tp_price}")
        if current_price <= self.sl_price if long else current_price >= self.sl_price:
            return TPSLUpdate(TPSLAction.SL_HIT, reason=f"price hit SL {self.sl_price}")

        # 2. Trailing activation (RATIO and INTERVAL alike).
        if not self.trailing_active and self.method is TPSLMethod.TRAILING:
            reached = (current_price >= self.activation_price if long
                       else current_price <= self.activation_price)
            if reached:
                self.trailing_active = True
                old_sl = self.sl_price
                new_sl = self._retrace_stop(current_price)
                self.sl_price = new_sl
                # The retrace stop replaces the fixed target.
                self.tp_price = None
                return TPSLUpdate(
                    TPSLAction.TRAILING_START, old_sl=old_sl, new_sl=new_sl,
                    reason=(f"trailing active at {current_price}, retrace "
                            f"{self.trailing_stop_pct_val*100:.2f}%"
                            if self.trailing_mode is TrailingMode.RATIO
                            else f"trailing active at {current_price}, "
                                 f"distance {self.trailing_distance}"),
                    payload=self.position_payload(),
                )

        # 3. Breakeven: after the trade pays for its risk, stop risking more.
        #    Only ever a TIGHTENING. If trailing already ratcheted the stop
        #    past entry, pulling it back to entry would LOOSEN it and give back
        #    profit the trail had already locked in.
        already_past_entry = self.sl_price > self.entry_price if long \
            else self.sl_price < self.entry_price
        if not self.breakeven_set and not already_past_entry \
                and unrealized_pnl_pct >= self.breakeven_threshold * 100:
            old_sl = self.sl_price
            new_sl = self.entry_price
            if self.side == "SHORT":
                new_sl += 10 ** -self.quote_precision   # 1 tick above entry
            self.sl_price = new_sl
            self.breakeven_set = True
            return TPSLUpdate(
                TPSLAction.BREAKEVEN, old_sl=old_sl, new_sl=new_sl,
                reason=f"breakeven at {unrealized_pnl_pct:.2f}% PNL",
                payload=self.position_payload(),
            )

        # 4. ROI-triggered trailing (available on POSITION/PARTIAL too).
        if not self.trailing_active and \
                unrealized_pnl_pct >= self.trailing_trigger_roi * 100:
            self.trailing_active = True
            return TPSLUpdate(
                TPSLAction.TRAILING_START,
                reason=f"trailing activated at {unrealized_pnl_pct:.2f}% ROI",
            )

        # 5. Ratchet the stop along the peak. Only ever tighter.
        if self.trailing_active:
            old_sl = self.sl_price
            new_sl = self._retrace_stop(self.best_price)
            tighter = new_sl > old_sl if long else new_sl < old_sl
            if tighter:
                self.sl_price = new_sl
                return TPSLUpdate(
                    TPSLAction.TRAILING_UPDATE, old_sl=old_sl, new_sl=new_sl,
                    reason=(f"trailing stop -> {new_sl:.8f}"),
                    payload=self.position_payload(),
                )

        return TPSLUpdate(TPSLAction.NONE)

    # ── REST payloads ────────────────────────────────────────────────────

    def position_payload(self) -> dict:
        """Body for tpsl/position/place_order and /modify_order.

        One stop for the whole position. For PARTIAL the ladder owns every take
        profit, so the position order must carry the STOP ONLY — sending a
        position-level TP at the same price as the ladder's top rung would
        close the position twice over.
        """
        body: dict = {
            "slPrice": f"{self.sl_price:.{self.quote_precision}f}",
            "slStopType": "LAST_PRICE",
        }
        if self.tp_price and self.method is not TPSLMethod.PARTIAL:
            body["tpPrice"] = f"{self.tp_price:.{self.quote_precision}f}"
            body["tpStopType"] = "LAST_PRICE"
        return body

    def unplaced_levels(self) -> List[TPSLLevel]:
        """Ladder rungs whose orders have not been accepted yet."""
        return [lv for lv in self.ladder if not lv.placed]

    def partial_orders(self) -> List[dict]:
        """One tpsl/place_order body per unplaced ladder rung.

        Each rung is its own order because tpQty/slQty are per-order base-coin
        quantities — Bitunix has no "close 30% at X" primitive, the ladder IS
        the set of sized orders.
        """
        return [lv.payload(self.quote_precision) for lv in self.unplaced_levels()]

    def to_dict(self) -> dict:
        return {
            "entry_price": self.entry_price,
            "side": self.side,
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
            "atr": self.atr,
            "breakeven_set": self.breakeven_set,
            "trailing_active": self.trailing_active,
            "trailing_mode": self.trailing_mode.value,
            "best_price": self.best_price,
            "method": self.method.value,
            "activation_price": self.activation_price,
            "qty": self.qty,
            "ladder": [{"price": l.price, "qty": l.qty, "kind": l.kind,
                        "placed": l.placed} for l in self.ladder],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PositionTPSL":
        tpsl = cls(
            entry_price=data["entry_price"],
            side=data["side"],
            atr_value=data.get("atr", 0.0),
            method=data.get("method", "POSITION"),
            trailing_mode=data.get("trailing_mode", "RATIO"),
        )
        tpsl.tp_price = data.get("tp_price", tpsl.tp_price)
        tpsl.sl_price = data.get("sl_price", tpsl.sl_price)
        tpsl.breakeven_set = data.get("breakeven_set", False)
        tpsl.trailing_active = data.get("trailing_active", False)
        tpsl.best_price = data.get("best_price", data["entry_price"])
        tpsl.activation_price = data.get("activation_price", tpsl.activation_price)
        tpsl.qty = data.get("qty")
        tpsl.ladder = [TPSLLevel(**lv) for lv in data.get("ladder", [])]
        return tpsl


# ── Model 4: account-level guard ─────────────────────────────────────────────

@dataclass
class AccountGuard:
    """
    Account TP/SL — closes EVERY position when total account PnL crosses a
    threshold. There is no REST endpoint for this on Bitunix, so the bot
    evaluates it and calls trade/close_all_position itself.

        account_tp / account_sl : thresholds in USDT; 0 disables that side
    """

    account_tp: float = 0.0
    account_sl: float = 0.0
    _triggered: Optional[str] = field(default=None, init=False, repr=False)

    def check(self, total_pnl: float) -> Optional[str]:
        """Return 'TP' / 'SL' / None. Latches: fires once per session."""
        if self._triggered:
            return None
        if self.account_tp > 0 and total_pnl >= self.account_tp:
            self._triggered = "TP"
            return "TP"
        if self.account_sl > 0 and total_pnl <= -abs(self.account_sl):
            self._triggered = "SL"
            return "SL"
        return None

    def reset(self) -> None:
        self._triggered = None

    @property
    def armed(self) -> bool:
        return self.account_tp > 0 or self.account_sl > 0