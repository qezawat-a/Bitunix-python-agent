"""Simplified TPSL: POSITION mode with breakeven + trailing stop only."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TPSLAction(Enum):
    NONE = "none"
    BREAKEVEN = "breakeven"
    TRAILING_START = "trailing_start"
    TRAILING_UPDATE = "trailing_update"
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"


@dataclass
class TPSLUpdate:
    action: TPSLAction
    old_sl: Optional[float] = None
    new_sl: Optional[float] = None
    old_tp: Optional[float] = None
    new_tp: Optional[float] = None
    reason: str = ""


class PositionTPSL:
    """
    POSITION mode: single TP/SL for entire position.
    
    Lifecycle:
    1. Entry at market price
    2. SL = 1x ATR below/above entry
    3. TP = 2x ATR above/below entry
    4. When PNL >= breakeven_threshold_pct → move SL to entry (breakeven)
    5. When PNL >= trailing_trigger_ROI_pct → activate trailing stop
    6. Trailing: callback trailing_stop_pct from best price
    """
    
    def __init__(
        self,
        entry_price: float,
        side: str,  # LONG | SHORT
        atr_value: float,
        breakeven_threshold_pct: float = 2.0,
        trailing_trigger_roi_pct: float = 5.0,
        trailing_stop_pct: float = 0.5,
    ):
        self.entry_price = entry_price
        self.side = side  # LONG or SHORT
        self.atr = atr_value
        self.breakeven_threshold = breakeven_threshold_pct / 100.0
        self.trailing_trigger_roi = trailing_trigger_roi_pct / 100.0
        self.trailing_stop_pct_val = trailing_stop_pct / 100.0
        
        # Calculate initial TP/SL
        if side == "LONG":
            self.tp_price = entry_price + (atr_value * 2)
            self.sl_price = entry_price - atr_value
        else:  # SHORT
            self.tp_price = entry_price - (atr_value * 2)
            self.sl_price = entry_price + atr_value
        
        self.breakeven_set = False
        self.trailing_active = False
        self.best_price = entry_price  # Track best price for trailing
    
    def update(
        self,
        current_price: float,
        unrealized_pnl_pct: float,
    ) -> TPSLUpdate:
        """
        Update TPSL on each new price/candle.
        
        Args:
            current_price: latest price
            unrealized_pnl_pct: current PNL as percentage (0.0 to 100.0, negative OK)
        
        Returns:
            TPSLUpdate with action and new levels if changed
        """
        
        # Update best price for trailing
        if self.side == "LONG":
            if current_price > self.best_price:
                self.best_price = current_price
        else:
            if current_price < self.best_price:
                self.best_price = current_price
        
        # Check if TP/SL hit
        if self.side == "LONG":
            if current_price >= self.tp_price:
                return TPSLUpdate(TPSLAction.TP_HIT, reason="Price hit TP")
            if current_price <= self.sl_price:
                return TPSLUpdate(TPSLAction.SL_HIT, reason="Price hit SL")
        else:
            if current_price <= self.tp_price:
                return TPSLUpdate(TPSLAction.TP_HIT, reason="Price hit TP")
            if current_price >= self.sl_price:
                return TPSLUpdate(TPSLAction.SL_HIT, reason="Price hit SL")
        
        # Breakeven: when PNL >= threshold, move SL to entry
        if not self.breakeven_set and unrealized_pnl_pct >= (self.breakeven_threshold * 100):
            old_sl = self.sl_price
            self.sl_price = self.entry_price
            if self.side == "SHORT":
                self.sl_price -= 0.0001  # 1bp below entry for shorts
            self.breakeven_set = True
            return TPSLUpdate(
                TPSLAction.BREAKEVEN,
                old_sl=old_sl,
                new_sl=self.sl_price,
                reason=f"Breakeven activated at {unrealized_pnl_pct:.2f}% PNL",
            )
        
        # Trailing: activate when ROI >= trigger
        if not self.trailing_active and unrealized_pnl_pct >= (self.trailing_trigger_roi * 100):
            self.trailing_active = True
            return TPSLUpdate(
                TPSLAction.TRAILING_START,
                reason=f"Trailing activated at {unrealized_pnl_pct:.2f}% ROI",
            )
        
        # Trailing stop: move SL up/down by callback %
        if self.trailing_active:
            old_sl = self.sl_price
            if self.side == "LONG":
                # Callback from best price: best_price * (1 - trailing_stop_pct)
                new_sl = self.best_price * (1.0 - self.trailing_stop_pct_val)
                if new_sl > self.sl_price:  # Only move up
                    self.sl_price = new_sl
                    return TPSLUpdate(
                        TPSLAction.TRAILING_UPDATE,
                        old_sl=old_sl,
                        new_sl=new_sl,
                        reason=f"Trailing updated: {self.trailing_stop_pct_val*100:.2f}% below {self.best_price:.8f}",
                    )
            else:  # SHORT
                # Callback from best price: best_price * (1 + trailing_stop_pct)
                new_sl = self.best_price * (1.0 + self.trailing_stop_pct_val)
                if new_sl < self.sl_price:  # Only move down
                    self.sl_price = new_sl
                    return TPSLUpdate(
                        TPSLAction.TRAILING_UPDATE,
                        old_sl=old_sl,
                        new_sl=new_sl,
                        reason=f"Trailing updated: {self.trailing_stop_pct_val*100:.2f}% above {self.best_price:.8f}",
                    )
        
        return TPSLUpdate(TPSLAction.NONE)
    
    def to_dict(self) -> dict:
        """Serialize for storage/transmission."""
        return {
            "entry_price": self.entry_price,
            "side": self.side,
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
            "atr": self.atr,
            "breakeven_set": self.breakeven_set,
            "trailing_active": self.trailing_active,
            "best_price": self.best_price,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> PositionTPSL:
        """Deserialize from storage."""
        tpsl = cls(
            entry_price=data["entry_price"],
            side=data["side"],
            atr_value=data["atr"],
        )
        tpsl.tp_price = data.get("tp_price", tpsl.tp_price)
        tpsl.sl_price = data.get("sl_price", tpsl.sl_price)
        tpsl.breakeven_set = data.get("breakeven_set", False)
        tpsl.trailing_active = data.get("trailing_active", False)
        tpsl.best_price = data.get("best_price", data["entry_price"])
        return tpsl
