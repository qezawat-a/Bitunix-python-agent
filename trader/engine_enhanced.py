"""Enhanced trading engine with integrated TPSL management."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
from loguru import logger

from config import Config
from jrock.store import log_paper_trade as _db_log_paper_trade
from trader.api.rest import BitunixRestClient, BitunixError, account_dict
from trader.api.ws import BitunixWSClient
from trader.strategies import ALL_STRATEGIES, BaseStrategy, Direction, Signal
from trader.risk.calculator import calc_order_unit, atr_stop_loss, atr_take_profit, price_precision
from trader.risk.liquidation import calc_liquidation, is_near_liquidation, set_tiers
from trader.risk.tpsl import PositionTPSL, TPSLAction


@dataclass
class EngineConfig:
    symbol: str = "BTCUSDT"
    interval: str = "15m"
    margin_coin: str = "USDT"
    leverage: int = 10
    margin_mode: str = "ISOLATION"
    position_mode: str = "ONE_WAY"
    risk_pct: float = 1.0
    max_positions: int = 3
    paper: bool = True
    active_strategies: List[str] = field(default_factory=lambda: ["EMA", "RSI", "MACD"])
    consensus_threshold: int = 2
    min_confidence: float = 0.5
    timeframes: list = None          # e.g. ["1m","3m","5m","15m"] — scans all
    tf_min_confidence: float = 0.6   # per-timeframe min confidence
    
    def __post_init__(self):
        if self.timeframes is None:
            self.timeframes = [self.interval]
        # Input validation
        self.leverage      = max(1, min(125, self.leverage))
        self.risk_pct      = max(0.1, min(100.0, self.risk_pct))
        self.max_positions = max(1, min(50, self.max_positions))
        self.consensus_threshold = max(1, self.consensus_threshold)
        self.min_confidence = max(0.0, min(1.0, self.min_confidence))
        if self.margin_mode not in ("CROSS", "ISOLATION"):
            self.margin_mode = "CROSS"
        if self.position_mode not in ("HEDGE", "ONE_WAY"):
            self.position_mode = "HEDGE" 
    
    # TPSL config
    breakeven_threshold_pct: float = 2.0
    trailing_trigger_roi_pct: float = 5.0
    trailing_stop_pct: float = 0.5


@dataclass
class ManagedPosition:
    """Position with integrated TPSL lifecycle."""
    position_id: str
    symbol: str
    side: str                      # LONG | SHORT
    qty: str
    entry_price: float
    opened_at: int
    paper: bool
    strategy: str
    
    # TPSL management
    tpsl: Optional[PositionTPSL] = None
    current_price: float = 0.0
    unrealized_pnl_pct: float = 0.0
    best_price: float = 0.0
    
    def init_tpsl(self, atr: float, cfg=None) -> None:
        """Initialize TPSL using values from EngineConfig (not Config class)."""
        self.best_price = self.entry_price
        be  = cfg.breakeven_threshold_pct  if cfg else 2.0
        tri = cfg.trailing_trigger_roi_pct if cfg else 5.0
        trs = cfg.trailing_stop_pct        if cfg else 0.5
        self.tpsl = PositionTPSL(
            entry_price=self.entry_price,
            side=self.side,
            atr_value=atr,
            breakeven_threshold_pct=be,
            trailing_trigger_roi_pct=tri,
            trailing_stop_pct=trs,
        )
    
    def update_price(self, price: float, pnl_pct: float) -> Dict[str, Any]:
        """Update position with new price data. Returns action if TP/SL/breakeven triggered."""
        self.current_price = price
        self.unrealized_pnl_pct = pnl_pct
        
        # Update best price for trailing
        if self.side == "LONG":
            self.best_price = max(self.best_price, price)
        else:
            self.best_price = min(self.best_price, price)
        
        if not self.tpsl:
            return {"action": "none"}
        
        # Check TPSL
        update = self.tpsl.update(price, pnl_pct)
        return {
            "action": update.action.value,
            "reason": update.reason,
            "old_sl": update.old_sl,
            "new_sl": update.new_sl,
            "old_tp": update.old_tp,
            "new_tp": update.new_tp,
        }
    
    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "tp_price": self.tpsl.tp_price if self.tpsl else None,
            "sl_price": self.tpsl.sl_price if self.tpsl else None,
            "breakeven_set": self.tpsl.breakeven_set if self.tpsl else False,
            "trailing_active": self.tpsl.trailing_active if self.tpsl else False,
            "opened_at": self.opened_at,
            "strategy": self.strategy,
            "paper": self.paper,
        }


class TradingEngine:
    """Enhanced engine with TPSL-managed positions."""
    
    def __init__(self, cfg: Optional[EngineConfig] = None):
        self.cfg = cfg or EngineConfig(
            symbol=Config.DEFAULT_SYMBOL,
            interval="15m",
            margin_coin=Config.DEFAULT_MARGIN_COIN,
            leverage=Config.DEFAULT_LEVERAGE,
            margin_mode=Config.DEFAULT_MARGIN_MODE,
            position_mode=Config.DEFAULT_POSITION_MODE,
            risk_pct=Config.RISK_PERCENT,
            max_positions=Config.MAX_OPEN_POSITIONS,
            paper=Config.PAPER_TRADING,
        )
        self.rest = BitunixRestClient()
        self.ws = BitunixWSClient()
        self._strategies: List[BaseStrategy] = []
        self._klines: Dict[str, List[Dict[str, Any]]] = {}  # tf → candles
        self._open_positions: Dict[str, ManagedPosition] = {}  # Enhanced
        self._running = False
        self._notify_cb: Optional[Callable[[str], None]] = None
        self._last_candle_time = 0
        
        self._reload_strategies()
    
    # ── Public API ────────────────────────────────────────────────────
    
    def on_notify(self, callback: Callable[[str], None]) -> None:
        """Register callback for Telegram notifications."""
        self._notify_cb = callback
    
    def set_strategies(self, names: List[str]) -> None:
        self.cfg.active_strategies = names
        self._reload_strategies()
    
    def _reload_strategies(self) -> None:
        self._strategies = [
            ALL_STRATEGIES[n]() for n in self.cfg.active_strategies
            if n in ALL_STRATEGIES
        ]
    
    async def start(self) -> None:
        """Start the trading engine."""
        logger.info(f"Starting engine: {self.cfg.symbol} {self.cfg.interval}")
        self._running = True
        
        # Setup WebSocket callbacks
        self.ws.on("kline", self._on_kline)
        self.ws.on("position", self._on_position_update)
        self.ws.on("order", self._on_order_update)
        self.ws.on("tp_sl", self._on_tpsl_update)
        
        # Subscribe to streams
        # Subscribe to ALL configured timeframes
        for tf in self.cfg.timeframes:
            self.ws.subscribe_kline(self.cfg.symbol, tf)
        self.ws.subscribe_positions(self.cfg.symbol)
        self.ws.subscribe_orders(self.cfg.symbol)
        self.ws.subscribe_tpsl(self.cfg.symbol)
        
        try:
            await self.ws.start()
        except Exception as e:
            logger.error(f"Engine failed: {e}")
            raise
    
    async def stop(self) -> None:
        """Stop the trading engine."""
        logger.info("Stopping engine")
        self._running = False
        await self.ws.stop()
    
    # ── Position Management ───────────────────────────────────────────
    
    def _on_kline(self, msg: Dict[str, Any]) -> None:
        """Handle kline updates — stores per timeframe."""
        if not msg.get("data"):
            return
        
        data = msg["data"]
        tf = data.get("interval", self.cfg.interval)
        
        if tf not in self._klines:
            self._klines[tf] = []
        
        self._klines[tf].append({
            "timestamp": data.get("eventTime"),
            "open": float(data.get("o", 0)),
            "high": float(data.get("h", 0)),
            "low": float(data.get("l", 0)),
            "close": float(data.get("c", 0)),
            "baseVol": float(data.get("v", 0)),
            "quoteVol": float(data.get("q", 0)),
        })
        
        # Keep last 500 candles per timeframe
        if len(self._klines[tf]) > 500:
            self._klines[tf] = self._klines[tf][-500:]
        
        # On candle close, evaluate strategies for this timeframe
        if data.get("x"):
            asyncio.create_task(self._evaluate_strategies(tf))
    
    def _on_position_update(self, msg: Dict[str, Any]) -> None:
        """Handle position updates from WebSocket."""
        data = msg.get("data", {})
        if not data:
            return
        
        pos_id = data.get("positionId")
        qty = float(data.get("qty", 0))
        symbol = data.get("symbol", "")
        
        # Position closed
        if qty == 0 and pos_id in self._open_positions:
            pos = self._open_positions.pop(pos_id)
            pnl = data.get("realizedPNL", "?")
            self._notify(
                f"🔒 *Position Closed*\n"
                f"ID: `{pos_id}`\n"
                f"Symbol: `{symbol}`\n"
                f"Side: `{pos.side}`\n"
                f"PNL: `{pnl} USDT`"
            )
        
        # Position updated (open or modified)
        elif pos_id and symbol == self.cfg.symbol:
            price = float(data.get("markPrice", 0)) or float(data.get("avgOpenPrice", 0))
            pnl_pct = float(data.get("percentPNL", 0)) * 100
            
            if pos_id not in self._open_positions:
                # New position
                pos = ManagedPosition(
                    position_id=pos_id,
                    symbol=symbol,
                    side=data.get("side", "LONG"),
                    qty=data.get("qty", "0"),
                    entry_price=float(data.get("avgOpenPrice", 0)),
                    opened_at=int(time.time()),
                    paper=False,
                    strategy="",
                )
                
                # Init TPSL with ATR from current candles
                df = self._to_dataframe()
                if df is not None and len(df) >= 14:
                    from trader import indicators as ta
                    atr_series = ta.atr(df["high"].astype(float), 
                                       df["low"].astype(float), 
                                       df["close"].astype(float), 
                                       length=14)
                    atr_val = float(atr_series.iloc[-1])
                    pos.init_tpsl(atr_val, self.cfg)
                
                self._open_positions[pos_id] = pos
                self._notify(f"✅ *New Position*\n"
                            f"Side: `{pos.side}`\n"
                            f"Entry: `{pos.entry_price}`\n"
                            f"TP: `{pos.tpsl.tp_price if pos.tpsl else '?'}`\n"
                            f"SL: `{pos.tpsl.sl_price if pos.tpsl else '?'}`")
            else:
                # Update existing position
                pos = self._open_positions[pos_id]
                action_dict = pos.update_price(price, pnl_pct)
                
                # Handle TPSL actions
                if action_dict["action"] == "breakeven":
                    self._notify(
                        f"✅ *Breakeven Set*\n"
                        f"Position: `{pos_id}`\n"
                        f"PNL: `{pnl_pct:.2f}%`\n"
                        f"SL moved to entry: `{pos.entry_price}`"
                    )
                
                elif action_dict["action"] == "trailing_start":
                    self._notify(
                        f"📈 *Trailing Activated*\n"
                        f"Position: `{pos_id}`\n"
                        f"ROI: `{pnl_pct:.2f}%`\n"
                        f"Trailing from: `{pos.best_price}`"
                    )
                
                elif action_dict["action"] == "trailing_update":
                    # Less spam: only log to logger, don't notify every update
                    logger.info(f"Trailing update: SL {action_dict['old_sl']} → {action_dict['new_sl']}")
    
    def _on_order_update(self, msg: Dict[str, Any]) -> None:
        """Handle order updates."""
        data = msg.get("data", {})
        status = data.get("status", "")
        if status == "FILLED":
            self._notify(f"✅ Order filled: `{data.get('orderId', '')}`")
        elif status in ("CANCELLED", "REJECTED"):
            self._notify(f"⚠️ Order {status}: `{data.get('orderId', '')}`")
    
    def _on_tpsl_update(self, msg: Dict[str, Any]) -> None:
        """Handle TP/SL updates."""
        data = msg.get("data", {})
        status = data.get("status", "")
        if status == "FILLED":
            self._notify(f"🎯 TP/SL triggered: `{data.get('orderId', '')}`")
    
    # ── Strategy Evaluation ───────────────────────────────────────────
    
    async def _evaluate_strategies(self, tf: str = None) -> None:
        """Run all strategies on latest candle for a given timeframe."""
        tf = tf or self.cfg.interval
        if len(self._open_positions) >= self.cfg.max_positions:
            return  # Already at max
        
        if not self._strategies:
            return
        
        df = self._to_dataframe(tf)
        if df is None or len(df) < 20:
            return
        
        # Run strategies on this timeframe
        signals = []
        for strategy in self._strategies:
            try:
                signal = strategy.evaluate(df)
                if signal and signal.confidence >= self.cfg.min_confidence:
                    signal.timeframe = tf  # Tag signal with timeframe
                    signals.append(signal)
            except Exception as e:
                logger.warning(f"Strategy {strategy.__class__.__name__} [{tf}] error: {e}")
        
        if not signals:
            return
        
        logger.info(f"[{tf}] {len(signals)} signals: {[s.direction.value for s in signals]}")
        
        # Check consensus
        if len(signals) >= self.cfg.consensus_threshold:
            long_count = sum(1 for s in signals if s.direction == Direction.BUY)
            short_count = sum(1 for s in signals if s.direction == Direction.SELL)
            
            if long_count > short_count:
                await self._place_trade(Direction.BUY, signals, tf)
            elif short_count > long_count:
                await self._place_trade(Direction.SELL, signals, tf)
    
    async def _place_trade(self, direction: Direction, signals: List[Signal], tf: str = None) -> None:
        """Place a trade."""
        available = float((await self._get_account()).get("available", 0))
        if available <= 0:
            self._notify("❌ Insufficient balance")
            return
        
        ticker = await self._get_ticker()
        if not ticker:
            return
        
        price = float(ticker.get("lastPrice", 0))
        if price <= 0:
            return
        
        try:
            pairs = await self.rest.get_trading_pairs(self.cfg.symbol)
            pair_data = pairs.get("data", [{}])[0]
            base_prec = int(pair_data.get("basePrecision", 3))
            quote_prec = int(pair_data.get("quotePrecision", 2))
        except Exception:
            base_prec, quote_prec = 3, 2
        
        order_unit = calc_order_unit(
            balance=available,
            entry_price=price,
            leverage=self.cfg.leverage,
            risk_pct=self.cfg.risk_pct,
            base_precision=base_prec,
        )
        
        if float(order_unit.qty) <= 0:
            logger.warning("Calculated qty=0, skipping trade")
            return
        
        side = "BUY" if direction == Direction.BUY else "SELL"
        
        # ATR-based TP/SL
        df = self._to_dataframe(tf or self.cfg.interval)
        from trader import indicators as ta
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)
        atr_series = ta.atr(h, l, c, length=14)
        atr_val = float(atr_series.iloc[-1])
        sl = atr_stop_loss(price, atr_val, side)
        tp = atr_take_profit(price, atr_val, side)
        
        reasons = "; ".join(s.reason for s in signals[:3])
        msg = (
            f"{'📈' if side == 'BUY' else '📉'} *{'PAPER ' if self.cfg.paper else ''}SIGNAL*\n"
            f"Symbol: `{self.cfg.symbol}` [{tf or self.cfg.interval}]\n"
            f"Direction: `{side}`\n"
            f"Price: `{price}`\n"
            f"Qty: `{order_unit.qty}`\n"
            f"SL: `{price_precision(sl, quote_prec)}`\n"
            f"TP: `{price_precision(tp, quote_prec)}`\n"
            f"Strategies: `{', '.join(s.direction.value for s in signals)}`\n"
            f"Reason: {reasons}"
        )
        self._notify(msg)
        
        if self.cfg.paper:
            await self._log_paper_trade(side, order_unit.qty, price, sl, tp, reasons)
            return
        
        # Live order
        try:
            resp = await self.rest.place_order(
                symbol=self.cfg.symbol,
                side=side,
                order_type="MARKET",
                qty=order_unit.qty,
                trade_side="OPEN" if self.cfg.position_mode == "HEDGE" else None,
                sl_price=price_precision(sl, quote_prec),
                sl_stop_type="LAST_PRICE",
                sl_order_type="MARKET",
                tp_price=price_precision(tp, quote_prec),
                tp_stop_type="LAST_PRICE",
                tp_order_type="MARKET",
            )
            order_id = resp.get("data", {}).get("orderId", "")
            logger.info(f"Order placed: {order_id}")
            self._notify(f"✅ Order placed: `{order_id}`")
        except BitunixError as e:
            logger.error(f"Order failed: {e}")
            self._notify(f"❌ Order failed: [{e.code}] {e.msg}")
    
    async def _log_paper_trade(self, side: str, qty: str, price: float, sl: float, tp: float, reason: str) -> None:
        await _db_log_paper_trade(self.cfg.symbol, side, qty, price, reason)
    
    # ── Helpers ───────────────────────────────────────────────────────
    
    def _to_dataframe(self, tf: str = None) -> Optional[pd.DataFrame]:
        tf = tf or self.cfg.interval
        klines = self._klines.get(tf, [])
        if not klines:
            return None
        df = pd.DataFrame(klines)
        for col in ["open", "high", "low", "close", "baseVol", "quoteVol"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    
    async def _get_account(self) -> Dict[str, Any]:
        try:
            resp = await self.rest.get_account(self.cfg.margin_coin)
            return account_dict(resp)
        except Exception as e:
            logger.error(f"get_account failed: {e}")
            return {}
    
    async def _get_ticker(self) -> Optional[Dict[str, Any]]:
        try:
            resp = await self.rest.get_tickers(self.cfg.symbol)
            data = resp.get("data")
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.error(f"get_ticker failed: {e}")
        return None
    
    def _notify(self, text: str) -> None:
        if self._notify_cb:
            try:
                self._notify_cb(text)
            except Exception:
                pass
        logger.info(f"[notify] {text}")
