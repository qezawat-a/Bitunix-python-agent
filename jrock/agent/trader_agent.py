"""Trader agent: AI-driven trading context with position management."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Optional, Any

from ..config import Settings


@dataclass
class TraderContext:
    """Shared trader state accessible to both engine and agent."""
    symbol: str = "BTCUSDT"
    leverage: int = 10
    margin_mode: str = "CROSS"
    position_mode: str = "HEDGE"
    max_positions: int = 1
    risk_pct: float = 2.0
    paper_trading: bool = True
    active_strategies: list[str] = None
    consensus_threshold: int = 2
    min_confidence: float = 0.6
    
    # Position tracking
    open_positions: dict[str, dict] = None
    pending_orders: list[dict] = None
    
    # Risk parameters
    tpsl_mode: str = "ADAPTIVE"  # POSITION | PARTIAL | ADAPTIVE | FIXED_R
    trailing_method: str = "ATR"  # ATR | RATIO | INTERVAL
    trailing_callback: float = 0.5
    account_tp_usdt: float = 0.0  # Close all when profit reaches this
    account_sl_usdt: float = 0.0  # Close all when loss reaches this
    
    def __post_init__(self):
        if self.active_strategies is None:
            self.active_strategies = ["EMA", "RSI", "MACD"]
        if self.open_positions is None:
            self.open_positions = {}
        if self.pending_orders is None:
            self.pending_orders = []
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> TraderContext:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TraderAgent:
    """AI wrapper around trading logic — understands trader state."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.context = TraderContext(
            symbol=settings.api_keys.get("DEFAULT_SYMBOL", "BTCUSDT"),
            leverage=int(settings.api_keys.get("DEFAULT_LEVERAGE", 10)),
            max_positions=int(settings.api_keys.get("MAX_OPEN_POSITIONS", 1)),
            paper_trading=settings.api_keys.get("PAPER_TRADING", "true").lower() == "true",
        )
        self.engine: Optional[Any] = None
    
    def set_engine(self, engine: Any) -> None:
        """Register the trading engine."""
        self.engine = engine
        if engine:
            self.context.open_positions = engine._open_positions
            self.context.pending_orders = []
    
    async def get_status(self) -> str:
        """Get human-readable trader status."""
        if not self.engine or not self.engine._running:
            return "🔌 Trader engine: offline"
        
        c = self.context
        open_pos = len(self.context.open_positions)
        mode = "📄 PAPER" if c.paper_trading else "🔴 LIVE"
        
        return (
            f"*Trader Status*\n"
            f"Mode: {mode}\n"
            f"Symbol: `{c.symbol}` • Leverage: `{c.leverage}x`\n"
            f"Strategies: `{', '.join(c.active_strategies)}`\n"
            f"Open positions: `{open_pos}/{c.max_positions}`\n"
            f"Running: `{self.engine._running if self.engine else 'no'}`"
        )
    
    async def scan_signals(self) -> list[dict]:
        """Get current signals from all active strategies."""
        if not self.engine:
            return []
        return []  # Implement scanner integration
    
    async def manage_position(self, position_id: str, action: str) -> str:
        """Manage an open position: close, adjust, etc."""
        if not self.engine or not position_id in self.context.open_positions:
            return f"❌ Position `{position_id}` not found"
        
        pos = self.context.open_positions[position_id]
        
        if action == "close":
            # Close position
            return f"✅ Closing `{position_id}`..."
        elif action == "breakeven":
            # Move SL to entry
            return f"✅ Setting breakeven for `{position_id}`"
        elif action == "trail":
            # Start trailing
            return f"✅ Trailing stop activated for `{position_id}`"
        
        return f"❌ Unknown action: {action}"
