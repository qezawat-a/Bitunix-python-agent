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
from trader.api.ws import BitunixWSClient, interval_from_channel
from trader.strategies import ALL_STRATEGIES, BaseStrategy, Direction, Signal
from trader.risk.calculator import (calc_order_unit, atr_stop_loss, atr_take_profit,
                                    price_precision, OrderUnitError, OrderUnit)
from trader.risk.liquidation import calc_liquidation, is_near_liquidation, set_tiers
from trader.risk.tpsl import (AccountGuard, PositionTPSL, TPSLAction,
                              TPSLMethod, TPSLUpdate)


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
    trailing_distance: float = 0.3
    # Which of Bitunix's four TPSL methods: POSITION | PARTIAL | TRAILING
    tpsl_method: str = "POSITION"
    # Trailing retrace mode: RATIO (pct off peak) | INTERVAL (abs off peak)
    trailing_method: str = "RATIO"
    # Account-level TP/SL in USDT; 0 disables that side.
    account_tp: float = 0.0
    account_sl: float = 0.0
    # Periodic loops, in seconds.
    scan_interval: float = 15.0
    guard_interval: float = 15.0
    mid_interval: float = 15.0
    # Cached from trading_pairs.
    base_precision: int = 3
    quote_precision: int = 2


def _opt_float(value: Any) -> Optional[float]:
    """Parse a REST numeric field that arrives as a string, or may be absent.

    Bitunix returns every numeric field as a string, and uses "" or "0" for
    "no value" — liqPrice <= 0 means the position is not near liquidation.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class ManagedPosition:
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
    # Live fields the position WS channel does carry.
    margin: float = 0.0
    leverage: float = 0.0
    unrealized_pnl: float = 0.0
    # Exchange-reported liquidation price (REST only; 0 = no liq risk).
    liq_price: float = 0.0

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
            method=getattr(cfg, "tpsl_method", "POSITION") if cfg else "POSITION",
            breakeven_threshold_pct=be,
            trailing_trigger_roi_pct=tri,
            trailing_stop_pct=trs,
            trailing_mode=getattr(cfg, "trailing_method", "RATIO") if cfg else "RATIO",
            trailing_distance=getattr(cfg, "trailing_distance", 0.3) if cfg else 0.3,
            qty=float(self.qty) if self.qty else None,
            quote_precision=getattr(cfg, "quote_precision", 2) if cfg else 2,
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
        # Live price snapshot, refreshed by the ticker WS channel; used by
        # /signal and by position PNL when the position channel is sparse.
        self._last_price: float = 0.0
        # Last signal the engine produced — what /signal reports.
        self._latest_signal: dict = {}
        # Whether report must be re-armed on boot (persisted by store).
        self._restore_report_on: bool = False
        # Last REST view of open positions, keyed by positionId. The WS push
        # omits avgOpenPrice/liqPrice, so this is where entry price comes from.
        self._position_snapshot: Dict[str, Dict[str, Any]] = {}
        # Account-level TP/SL (model 4 of Bitunix's four TPSL methods).
        self.account_guard = AccountGuard()
        # Exchange limits + precision for the traded symbol, from trading_pairs.
        self._pair: Dict[str, Any] = {}
        # Clock-driven loop: scan, account guard, position resync.
        self._periodic_task: Optional[asyncio.Task] = None

        self._reload_strategies()
    
    # ── Public API ────────────────────────────────────────────────────
    
    def on_notify(self, callback: Callable[[str], None]) -> None:
        """Register callback for Telegram notifications."""
        self._notify_cb = callback

    def signal_snapshot(self) -> dict:
        """Everything /signal needs: price, last signal, open positions, PNL.

        Read straight off the engine so the command never has to call the
        exchange or re-run strategies itself.
        """
        sig = dict(self._latest_signal)
        if sig.get("at"):
            sig["age_s"] = int(time.time()) - sig["at"]
        if not sig.get("price"):
            sig["price"] = self._last_price

        positions = []
        total_u = 0.0
        for pos in self._open_positions.values():
            tpsl = pos.tpsl
            positions.append({
                "id": pos.position_id,
                "symbol": pos.symbol,
                "side": pos.side,
                "qty": pos.qty,
                "entry": pos.entry_price,
                "price": pos.current_price or self._last_price,
                "pnl_pct": pos.unrealized_pnl_pct,
                "tp": tpsl.tp_price if tpsl else None,
                "sl": tpsl.sl_price if tpsl else None,
                "liq": pos.liq_price,
                "method": tpsl.method.value if tpsl else None,
                "breakeven": bool(tpsl and tpsl.breakeven_set),
                "trailing": bool(tpsl and tpsl.trailing_active),
                "margin": pos.margin,
            })
            total_u += pos.unrealized_pnl_pct

        return {
            "running": self._running,
            "paper": self.cfg.paper,
            "symbol": self.cfg.symbol,
            "timeframes": list(self.cfg.timeframes),
            "price": self._last_price,
            "signal": sig,
            "positions": positions,
            "total_pnl_pct": round(total_u, 2),
            "candles": {tf: len(v) for tf, v in self._klines.items()},
        }
    
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
        self.ws.on("ticker", self._on_ticker)
        self.ws.on("position", self._on_position_update)
        self.ws.on("order", self._on_order_update)
        self.ws.on("tpsl", self._on_tpsl_update)

        # Subscribe to streams
        # Subscribe to ALL configured timeframes
        for tf in self.cfg.timeframes:
            self.ws.subscribe_kline(self.cfg.symbol, tf)
        # Live price for position PNL between position-channel pushes.
        self.ws.subscribe_ticker(self.cfg.symbol)
        self.ws.subscribe_positions()
        self.ws.subscribe_orders()
        self.ws.subscribe_tpsl()

        # Backfill before the socket opens: the strategies need ~20 candles and
        # a 15m chart would otherwise take five hours of streaming to get them.
        await self._backfill_klines()

        self._periodic_task = asyncio.create_task(self._periodic())

        try:
            await self.ws.start()
        except Exception as e:
            logger.error(f"Engine failed: {e}")
            raise
        finally:
            # ws.start() only returns when the socket dies; the loop still has
            # to be told to stop rather than keep scanning on a dead engine.
            self._running = False
            if self._periodic_task:
                self._periodic_task.cancel()
                self._periodic_task = None

    async def _periodic(self) -> None:
        """Heartbeat loop — the half of the merge that never got wired up.

        scan_interval / guard_interval / mid_interval were saved in the kv
        config and read back by the handler, but nothing ever looped on them, so
        the account guard, position resync and signal scan only ran when a
        websocket message happened to arrive. This runs all three on a clock.

        Each clock is independent: the loop wakes on the FASTEST of the three
        and runs whatever is due, so a 5s guard is not silently stretched to a
        60s scan, and vice versa.
        """
        scan = max(1.0, self.cfg.scan_interval)
        guard = max(1.0, self.cfg.guard_interval)
        mid = max(1.0, self.cfg.mid_interval)
        tick = min(scan, guard, mid)

        next_scan = next_guard = next_mid = 0.0
        while self._running:
            try:
                await asyncio.sleep(tick)
                if not self._running:
                    break
                now = time.monotonic()

                if now >= next_scan:
                    next_scan = now + scan
                    for tf in self.cfg.timeframes:
                        await self._evaluate_strategies(tf)

                if now >= next_mid:
                    next_mid = now + mid
                    # Re-read positions from REST: the position WS push omits
                    # avgOpenPrice/liqPrice, so entry price and liquidation
                    # price only ever come from here.
                    await self.sync_positions()

                if now >= next_guard:
                    next_guard = now + guard
                    # Account TP/SL (model 4) is only ever evaluated on this tick.
                    await self.check_account_guard()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"periodic tick failed: {type(e).__name__}: {e}")

    async def _backfill_klines(self, limit: int = 200) -> None:
        """Seed each timeframe's candle buffer from REST.

        The kline WS pushes only the CURRENT candle, so a fresh process has an
        empty buffer and every strategy returns nothing. get_kline returns
        oldest-first, and we keep the newest `limit` per timeframe.
        """
        for tf in self.cfg.timeframes:
            try:
                resp = await self.rest.get_kline(self.cfg.symbol, interval=tf, limit=limit)
            except Exception as e:
                logger.warning(f"kline backfill [{tf}] failed: {e}")
                continue
            rows = resp.get("data") or []
            if not rows:
                logger.warning(f"kline backfill [{tf}] returned no data")
                continue
            candles = [{
                "timestamp": int(float(r.get("time", 0))),
                "open": float(r.get("open", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0)),
                "close": float(r.get("close", 0)),
                "baseVol": float(r.get("baseVol", 0)),
                "quoteVol": float(r.get("quoteVol", 0)),
            } for r in rows if r.get("close")]
            # Drop the still-forming last candle — an indicator reading a
            # partial bar differs from the closed bar and misfires the signal.
            if len(candles) > 1:
                candles = candles[:-1]
            self._klines[tf] = candles[-limit:]
            logger.info(f"kline backfill [{tf}]: {len(candles)} candles")
    
    async def stop(self) -> None:
        """Stop the trading engine."""
        logger.info("Stopping engine")
        self._running = False
        if self._periodic_task:
            self._periodic_task.cancel()
            self._periodic_task = None
        await self.ws.stop()
    
    # ── Position Management ───────────────────────────────────────────
    
    def _on_kline(self, msg: Dict[str, Any]) -> None:
        """Handle kline updates — stores per timeframe.

        The push shape is {ch, symbol, ts, data:{o,c,h,l,b,q}}:
          * the timeframe comes from the CHANNEL NAME (`market_kline_15min`),
            not from a field on the message;
          * there is no `x`/"isFinal" field and no `eventTime` — `ts` is the
            candle's open time;
          * it arrives every ~500ms as a snapshot of the CURRENT candle, so the
            same candle is delivered many times. We upsert on `ts` rather than
            appending, or one 15m bar would land 1800 times.
        """
        data = msg.get("data")
        if not data:
            return

        tf = interval_from_channel(msg.get("ch", ""))
        if tf is None:
            return

        ts = int(msg.get("ts") or 0)
        candle = {
            "timestamp": ts,
            "open": float(data.get("o", 0)),
            "high": float(data.get("h", 0)),
            "low": float(data.get("l", 0)),
            "close": float(data.get("c", 0)),
            "baseVol": float(data.get("b", 0)),
            "quoteVol": float(data.get("q", 0)),
        }
        if candle["close"] <= 0:
            return

        series = self._klines.setdefault(tf, [])
        if series and series[-1]["timestamp"] == ts:
            series[-1] = candle          # same candle, fresher values
        else:
            series.append(candle)
            if len(series) > 500:
                del series[:-500]
            # A new candle means the previous one closed. That is the only
            # reliable close signal Bitunix gives us, so evaluate here.
            if len(series) > 1:
                asyncio.create_task(self._evaluate_strategies(tf))

    def _on_ticker(self, msg: Dict[str, Any]) -> None:
        """Live price — keeps /signal and position PNL fresh."""
        data = msg.get("data")
        if not data:
            return
        try:
            price = float(data.get("lastPrice") or data.get("c") or 0)
        except (TypeError, ValueError):
            return
        if price <= 0:
            return
        self._last_price = price
        # Refresh PNL for open positions off the live mark.
        for pos in self._open_positions.values():
            pnl_pct = ((price - pos.entry_price) / pos.entry_price) * 100 \
                if pos.side == "LONG" else \
                ((pos.entry_price - price) / pos.entry_price) * 100
            pos.update_price(price, pnl_pct)
    
    def _on_position_update(self, msg: Dict[str, Any]) -> None:
        """Handle position updates from WebSocket.

        The push carries: event (OPEN/UPDATE/CLOSE), positionId, side, qty,
        unrealizedPNL, margin, leverage, ctime. It does NOT carry markPrice,
        avgOpenPrice or percentPNL — the old code read those three and got 0
        for every position, which zeroed the entry price and the PNL.

        Entry price therefore comes from avgOpenPrice on the REST snapshot
        (seeded in sync_positions) or, for a position we have never seen, from
        the live ticker.
        """
        data = msg.get("data")
        if not data:
            return

        pos_id = str(data.get("positionId", ""))
        event = str(data.get("event", "")).upper()
        if not pos_id:
            return

        # A close is signalled by event=CLOSE, not by qty going to zero.
        if event == "CLOSE" or float(data.get("qty", 0) or 0) == 0:
            if pos_id in self._open_positions:
                pos = self._open_positions.pop(pos_id)
                self._notify(
                    f"🔒 *Position Closed*\n"
                    f"ID: `{pos_id}`\n"
                    f"Symbol: `{pos.symbol}`\n"
                    f"Side: `{pos.side}`\n"
                    f"Entry: `{pos.entry_price}`\n"
                    f"Realized PNL: `{data.get('realizedPNL', '?')}` USDT"
                )
            return

        if event == "OPEN" and pos_id not in self._open_positions:
            entry = self._entry_price_for(data)
            if entry <= 0:
                # Without an entry price every PNL and TP/SL level is wrong.
                logger.warning(f"position {pos_id} open but no entry price known; "
                               f"deferring to sync_positions")
                asyncio.create_task(self.sync_positions())
                return
            pos = ManagedPosition(
                position_id=pos_id,
                symbol=str(data.get("symbol", self.cfg.symbol)),
                side=str(data.get("side", "LONG")),
                qty=str(data.get("qty", "0")),
                entry_price=entry,
                opened_at=int(data.get("ctime", 0) or time.time() * 1000),
                paper=False,
                strategy="ws",
            )
            pos.init_tpsl(self._atr(), self.cfg)
            self._open_positions[pos_id] = pos
            self._notify(
                f"✅ *New Position*\n"
                f"ID: `{pos_id}`\n"
                f"Side: `{pos.side}`\n"
                f"Qty: `{pos.qty}`\n"
                f"Entry: `{pos.entry_price}`\n"
                f"TP: `{pos.tpsl.tp_price if pos.tpsl else '—'}`\n"
                f"SL: `{pos.tpsl.sl_price if pos.tpsl else '—'}`"
            )
            return

        pos = self._open_positions.get(pos_id)
        if not pos:
            return

        # Refresh the live fields the position channel does carry.
        if data.get("qty"):
            pos.qty = str(data["qty"])
        if data.get("margin"):
            pos.margin = float(data["margin"])
        if data.get("leverage"):
            pos.leverage = float(data["leverage"])

        price = self._last_price or pos.current_price
        upnl = float(data.get("unrealizedPNL", 0) or 0)
        # PNL as a percentage of the margin committed — the percentPNL the old
        # code wanted. Margin is 0 on a CROSS position, so fall back to price.
        if pos.margin > 0 and self.cfg.leverage:
            pnl_pct = upnl / pos.margin * 100
        elif pos.entry_price > 0 and price > 0:
            pnl_pct = ((price - pos.entry_price) / pos.entry_price) * 100 \
                if pos.side == "LONG" else \
                ((pos.entry_price - price) / pos.entry_price) * 100
        else:
            pnl_pct = pos.unrealized_pnl_pct

        result = pos.update_price(price, pnl_pct)
        self._apply_tpsl_action(pos_id, pos, result)

    def _apply_tpsl_action(self, pos_id: str, pos: "ManagedPosition",
                           result: dict) -> None:
        """Push a TPSL state change to the exchange, then report it."""
        action = result.get("action", "none")
        if action == "none":
            return

        if action in ("breakeven", "trailing_start", "trailing_update"):
            asyncio.create_task(self._push_position_tpsl(pos))
        if action == "trailing_update":
            logger.info(f"trailing {pos_id}: SL {result.get('old_sl')} -> "
                        f"{result.get('new_sl')}")

        notes = {
            "breakeven": f"🎯 *Breakeven Set*\nID: `{pos_id}`\n"
                         f"PNL: `{pos.unrealized_pnl_pct:+.2f}%`\n"
                         f"SL moved to entry: `{result.get('new_sl')}`",
            "trailing_start": f"📈 *Trailing Activated*\nID: `{pos_id}`\n"
                              f"PNL: `{pos.unrealized_pnl_pct:+.2f}%`\n"
                              f"SL: `{result.get('new_sl')}`",
        }
        if action in notes:
            self._notify(notes[action])

    def _entry_price_for(self, data: Dict[str, Any]) -> float:
        """Best available entry price: the REST snapshot's, else the live price."""
        row = self._position_snapshot.get(str(data.get("positionId", "")))
        if row:
            try:
                avg = float(row.get("avgOpenPrice", 0) or 0)
                if avg > 0:
                    return avg
            except (TypeError, ValueError):
                pass
        return self._last_price

    def _atr(self, tf: str = None) -> float:
        """Latest ATR, or a small fraction of price when unavailable."""
        from trader import indicators as ta
        df = self._to_dataframe(tf or self.cfg.interval)
        if df is not None and len(df) >= 14:
            try:
                return float(ta.atr(df["high"].astype(float), df["low"].astype(float),
                                    df["close"].astype(float), length=14).iloc[-1])
            except Exception:
                pass
        return (self._last_price or 0) * 0.01
    
    def _on_order_update(self, msg: Dict[str, Any]) -> None:
        """Handle order updates."""
        data = msg.get("data", {})
        if isinstance(data, list):
            data = data[0] if data else {}
        status = str(data.get("status", ""))
        if status == "FILLED":
            self._notify(f"✅ Order filled: `{data.get('orderId', '')}`")
        elif status in ("CANCELED", "CANCELLED", "REJECTED", "EXPIRED"):
            self._notify(f"⚠️ Order {status}: `{data.get('orderId', '')}`")

    def _on_tpsl_update(self, msg: Dict[str, Any]) -> None:
        """Handle TP/SL updates.

        Bitunix says to read `event` TOGETHER WITH `status` — event alone is not
        final. FILLED means the trigger fired and the child order was placed,
        NOT that the position is closed, so we do not report a close here.
        """
        data = msg.get("data", {})
        if isinstance(data, list):
            data = data[0] if data else {}
        event = str(data.get("event", "")).upper()
        status = str(data.get("status", "")).upper()
        order_id = data.get("orderId", "")

        if event == "CLOSE" and status == "FILLED":
            self._notify(
                f"🎯 *TP/SL Triggered*\n"
                f"Order: `{order_id}`\n"
                f"Symbol: `{data.get('symbol', '')}`\n"
                f"Type: `{data.get('type', '')}`\n"
                f"TP: `{data.get('tpPrice', '—')}` | SL: `{data.get('slPrice', '—')}`"
            )
        elif event == "CLOSE" and status in ("CANCELED", "SYSTEM_CANCELED"):
            self._notify(f"🗑 TP/SL {status}: `{order_id}`")
        elif event == "CLOSE" and status == "FAILED":
            self._notify(f"❌ TP/SL trigger FAILED: `{order_id}` — check it manually")
    
    # ── Strategy Evaluation ───────────────────────────────────────────
    
    async def _evaluate_strategies(self, tf: str = None) -> None:
        """Run all strategies on latest candle for a given timeframe."""
        tf = tf or self.cfg.interval
        if len(self._open_positions) >= self.cfg.max_positions:
            return  # Already at max
        
        if not self._strategies:
            return
        
        df = self._to_dataframe(tf)
        if df is None or len(df) < 60:
            return

        # Run strategies on this timeframe
        signals = []
        for strategy in self._strategies:
            try:
                # Strategies implement generate(); evaluate() never existed, so
                # calling it raised AttributeError on every strategy, every
                # candle — and the except below swallowed it as a warning.
                signal = strategy.generate(df)
                if signal and signal.confidence >= self.cfg.min_confidence:
                    signal.timeframe = tf        # tag for /signal and the report
                    signal.strategy = strategy.name
                    signals.append(signal)
            except Exception as e:
                logger.warning(f"Strategy {strategy.__class__.__name__} [{tf}] error: {e}")
        
        if not signals:
            return
        
        logger.info(f"[{tf}] {len(signals)} signals: {[s.direction.value for s in signals]}")
        
        # Consensus — signals must out-vote, not merely exist.
        long_count = sum(1 for s in signals if s.direction == Direction.BUY)
        short_count = sum(1 for s in signals if s.direction == Direction.SELL)
        direction = Direction.NEUTRAL
        if len(signals) >= self.cfg.consensus_threshold:
            if long_count > short_count:
                direction = Direction.BUY
            elif short_count > long_count:
                direction = Direction.SELL

        # Confidence is the strongest backer's score, boosted by agreement —
        # what /signal shows and what the threshold gate applies to.
        confidence = self._confidence(signals, direction)
        self._latest_signal = {
            "symbol": self.cfg.symbol,
            "timeframe": tf,
            "direction": direction.value,
            "confidence": round(confidence * 100, 1),
            "price": self._last_price,
            "votes": {"BUY": long_count, "SELL": short_count,
                      "NEUTRAL": len(signals) - long_count - short_count},
            "strategies": [s.strategy for s in signals],
            "reason": "; ".join(s.reason for s in signals[:3]),
            "at": int(time.time()),
            "age_s": 0,
        }

        if direction is Direction.NEUTRAL:
            logger.info(f"[{tf}] no consensus: {long_count}B/{short_count}S")
            return

        await self._place_trade(direction, signals, tf)

    @staticmethod
    def _confidence(signals: List[Signal], direction: Direction) -> float:
        """Blend the winning side's best score with how strongly it agreed."""
        if not signals:
            return 0.0
        agree = [s for s in signals if s.direction is direction]
        if not agree:
            return 0.0
        best = max(s.confidence for s in agree)
        breadth = len(agree) / len(signals)
        return min(1.0, best * (0.7 + 0.3 * breadth))

    async def _place_trade(self, direction: Direction, signals: List[Signal], tf: str = None) -> None:
        """Size the order, place it, then attach TP/SL per the chosen model."""
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
        self._last_price = price

        pair = await self._load_pair()
        base_prec = self.cfg.base_precision
        quote_prec = self.cfg.quote_precision

        order_unit = calc_order_unit(
            balance=available,
            entry_price=price,
            leverage=self.cfg.leverage,
            risk_pct=self.cfg.risk_pct,
            base_precision=base_prec,
            min_trade_volume=_opt_float(pair.get("minTradeVolume")),
            max_market_order_volume=_opt_float(pair.get("maxMarketOrderVolume")),
        )

        side = "BUY" if direction == Direction.BUY else "SELL"

        atr_val = self._atr(tf)
        sl = atr_stop_loss(price, atr_val, side)
        tp = atr_take_profit(price, atr_val, side)

        # Build the TPSL state machine first: it decides whether we send a fixed
        # TP at all (TRAILING has none until it activates) and whether the
        # ladder needs its own sized orders.
        tpsl = PositionTPSL(
            entry_price=price,
            side="LONG" if direction is Direction.BUY else "SHORT",
            atr_value=atr_val,
            method=self.cfg.tpsl_method,
            breakeven_threshold_pct=self.cfg.breakeven_threshold_pct,
            trailing_trigger_roi_pct=self.cfg.trailing_trigger_roi_pct,
            trailing_stop_pct=self.cfg.trailing_stop_pct,
            trailing_mode=self.cfg.trailing_method,
            trailing_distance=self.cfg.trailing_distance,
            sl_price=sl,
            tp_price=tp,
            qty=order_unit.qty_float,
            quote_precision=quote_prec,
        )
        tpsl.set_qty_precision(base_prec)

        reasons = "; ".join(s.reason for s in signals[:3])
        confidence = self._latest_signal.get("confidence", 0)
        self._latest_signal.update({
            "qty": order_unit.qty,
            "sl": price_precision(tpsl.sl_price, quote_prec),
            "tp": price_precision(tpsl.tp_price, quote_prec) if tpsl.tp_price else None,
            "tpsl_method": self.cfg.tpsl_method,
            "opened": False,
        })

        msg = (
            f"{'📈' if side == 'BUY' else '📉'} *{'PAPER ' if self.cfg.paper else ''}SIGNAL*\n"
            f"Symbol: `{self.cfg.symbol}` [{tf or self.cfg.interval}]\n"
            f"Direction: `{side}`\n"
            f"Confidence: `{confidence}%`\n"
            f"Price: `{price}`\n"
            f"Qty: `{order_unit.qty}`\n"
            f"Notional: `{round(order_unit.notional, 2)} {self.cfg.margin_coin}`\n"
            f"SL: `{price_precision(tpsl.sl_price, quote_prec)}`\n"
            f"TP: `{price_precision(tpsl.tp_price, quote_prec) if tpsl.tp_price else '— (trailing)'}`\n"
            f"TPSL model: `{self.cfg.tpsl_method}`\n"
            f"Strategies: `{', '.join(s.strategy for s in signals)}`\n"
            f"Reason: {reasons}"
        )
        if order_unit.clamped:
            msg += f"\n⚠️ clamped to exchange limit: `{order_unit.clamped}`"
        self._notify(msg)

        if self.cfg.paper:
            await self._log_paper_trade(side, order_unit.qty, price, sl, tp, reasons)
            return

        # Live order. POSITION attaches both legs to the fill itself; PARTIAL
        # needs the ladder placed as separate sized orders once we have a
        # positionId, which only arrives after the market fill.
        try:
            body = {
                "symbol": self.cfg.symbol,
                "side": side,
                "orderType": "MARKET",
                "qty": order_unit.qty,
                "reduceOnly": False,
            }
            if self.cfg.position_mode == "HEDGE":
                body["tradeSide"] = "OPEN"
            if self.cfg.tpsl_method != TPSLMethod.PARTIAL.value:
                body["slPrice"] = price_precision(tpsl.sl_price, quote_prec)
                body["slStopType"] = "LAST_PRICE"
                body["slOrderType"] = "MARKET"
                if tpsl.tp_price:
                    body["tpPrice"] = price_precision(tpsl.tp_price, quote_prec)
                    body["tpStopType"] = "LAST_PRICE"
                    body["tpOrderType"] = "MARKET"

            resp = await self.rest._post("/api/v1/futures/trade/place_order", body)
            order_id = resp.get("data", {}).get("orderId", "")
            logger.info(f"order placed: {order_id}")
            self._notify(f"✅ Order placed: `{order_id}` qty=`{order_unit.qty}`")
            self._latest_signal["opened"] = True
            self._latest_signal["order_id"] = order_id
            self._latest_signal["sl"] = price_precision(tpsl.sl_price, quote_prec)
            self._latest_signal["tp"] = (price_precision(tpsl.tp_price, quote_prec)
                                         if tpsl.tp_price else None)
        except BitunixError as e:
            logger.error(f"order failed: {e}")
            self._notify(f"❌ Order failed: [{e.code}] {e.msg}")
            self._latest_signal["opened"] = False
            self._latest_signal["error"] = f"[{e.code}] {e.msg}"

    async def _load_pair(self) -> Dict[str, Any]:
        """Fetch and cache the symbol's trading_pairs row (precision + limits)."""
        if self._pair:
            return self._pair
        try:
            resp = await self.rest.get_trading_pairs(self.cfg.symbol)
            rows = resp.get("data") or []
            row = next((r for r in rows if r.get("symbol") == self.cfg.symbol), None) \
                or (rows[0] if rows else {})
        except Exception as e:
            logger.warning(f"trading_pairs failed: {e}")
            row = {}
        if row:
            self._pair = row
            try:
                self.cfg.base_precision = int(row.get("basePrecision", 3))
                self.cfg.quote_precision = int(row.get("quotePrecision", 2))
            except (TypeError, ValueError):
                pass
        return self._pair

    async def _push_position_tpsl(self, pos: "ManagedPosition") -> None:
        """Rewrite the position's TP/SL after a local level change.

        Uses tpsl/position/modify_order — keyed by symbol + positionId, not by
        orderId. Skipped in paper mode: there is nothing on the exchange to move.
        """
        if self.cfg.paper or not pos.tpsl:
            return
        body = {"symbol": pos.symbol, "positionId": pos.position_id,
                **pos.tpsl.position_payload()}
        try:
            await self.rest.modify_position_tpsl(
                symbol=pos.symbol, position_id=pos.position_id,
                tp_price=body.get("tpPrice"), sl_price=body.get("slPrice"),
                tp_stop_type=body.get("tpStopType"), sl_stop_type=body.get("slStopType"),
            )
            logger.info(f"position {pos.position_id} TPSL updated: {body}")
        except BitunixError as e:
            logger.error(f"modify_position_tpsl failed for {pos.position_id}: {e}")
            self._notify(f"⚠️ Could not update TP/SL on `{pos.position_id}`: "
                         f"[{e.code}] {e.msg}")
        except Exception as e:
            logger.error(f"modify_position_tpsl error for {pos.position_id}: {e}")

    async def _place_partial_ladder(self, pos: "ManagedPosition") -> None:
        """Place the Partial TP/SL ladder as sized tpsl/place_order calls."""
        if self.cfg.paper or not pos.tpsl:
            return
        for body in pos.tpsl.partial_orders():
            try:
                await self.rest.place_tpsl_order(
                    symbol=pos.symbol, position_id=pos.position_id, **body
                )
            except BitunixError as e:
                logger.error(f"partial TPSL failed for {pos.position_id}: {e}")
                self._notify(f"⚠️ Partial TP/SL failed on `{pos.position_id}`: "
                             f"[{e.code}] {e.msg}")
                return
        self._notify(
            f"🪜 *Partial TP/SL ladder placed*\n"
            f"`{pos.symbol}` {pos.side} {len(pos.tpsl.ladder)} levels"
        )

    async def sync_positions(self) -> None:
        """Rebuild position state from REST.

        The private WS only pushes on events, so a bot that starts after a
        position is opened knows nothing until something happens. This seeds
        `_open_positions` and `_position_snapshot` (which carries avgOpenPrice
        and liqPrice, absent from the WS push).
        """
        try:
            resp = await self.rest.get_pending_positions()
            rows = resp.get("data") or []
        except Exception as e:
            logger.error(f"get_pending_positions failed: {e}")
            return

        self._position_snapshot = {str(r.get("positionId")): r for r in rows}
        live = set()
        atr = self._atr()
        for row in rows:
            pid = str(row.get("positionId", ""))
            if not pid:
                continue
            live.add(pid)
            try:
                entry = float(row.get("avgOpenPrice", 0) or 0)
            except (TypeError, ValueError):
                entry = 0.0
            if entry <= 0:
                logger.warning(f"position {pid} has no avgOpenPrice; skipping")
                continue
            pos = self._open_positions.get(pid)
            if pos is None:
                pos = ManagedPosition(
                    position_id=pid,
                    symbol=str(row.get("symbol", self.cfg.symbol)),
                    side=str(row.get("side", "LONG")),
                    qty=str(row.get("qty", "0")),
                    entry_price=entry,
                    opened_at=int(row.get("ctime", 0) or time.time() * 1000),
                    paper=self.cfg.paper,
                    strategy="sync",
                )
                pos.margin = _opt_float(row.get("margin")) or 0.0
                pos.leverage = _opt_float(row.get("leverage")) or float(self.cfg.leverage)
                pos.init_tpsl(atr, self.cfg)
                self._open_positions[pid] = pos
            else:
                pos.qty = str(row.get("qty", pos.qty))
                pos.margin = _opt_float(row.get("margin")) or pos.margin
            pos.liq_price = _opt_float(row.get("liqPrice")) or 0.0

        # Anything the REST no longer lists is closed.
        for pid in list(self._open_positions):
            if pid not in live:
                self._open_positions.pop(pid, None)

        if rows:
            total = sum(_opt_float(r.get("unrealizedPNL")) or 0.0 for r in rows)
            self._notify(f"🔄 Synced {len(rows)} open position(s) | "
                         f"uPNL: `{round(total, 2)} {self.cfg.margin_coin}`")
        else:
            self._notify("🔄 Synced — no open positions")

    async def check_account_guard(self) -> None:
        """Account TP/SL (model 4): close everything when total PNL crosses."""
        if not self.account_guard.armed:
            return
        try:
            resp = await self.rest.get_pending_positions()
            rows = resp.get("data") or []
        except Exception as e:
            logger.error(f"account guard: {e}")
            return
        if not rows:
            return
        total = sum(_opt_float(r.get("unrealizedPNL")) or 0.0 for r in rows)
        hit = self.account_guard.check(total)
        if not hit:
            return
        try:
            await self.rest.close_all_position()
            self._notify(
                f"🛑 *ACCOUNT {hit} HIT*\n"
                f"Total PNL: `{round(total, 2)} {self.cfg.margin_coin}`\n"
                f"TP: `{self.account_guard.account_tp}` | "
                f"SL: `{self.account_guard.account_sl}`\n"
                f"All positions closed."
            )
        except BitunixError as e:
            self._notify(f"❌ Account {hit} hit but close_all_position failed: "
                         f"[{e.code}] {e.msg}")
    
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
