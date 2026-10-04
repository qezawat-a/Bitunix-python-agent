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
                                    price_precision, OrderUnitError, OrderUnit,
                                    _floor_to)
from trader.risk.liquidation import (calc_liquidation, calc_liquidation_cross,
                                      get_mmr, is_near_liquidation, set_tiers)
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

    # TPSL config. These MUST stay above __post_init__: @dataclass only promotes
    # an annotated class attribute into a field, and an annotation that sits
    # below a method definition is still collected — but as a plain attribute
    # with no annotation it is not, so it silently fell out of __init__ and
    # /trader start raised TypeError on every TPSL keyword.
    breakeven_threshold_pct: float = 2.0
    trailing_trigger_roi_pct: float = 5.0
    trailing_stop_pct: float = 0.5
    trailing_distance: float = 0.3
    # Which of Bitunix's four TPSL methods: POSITION | PARTIAL | TRAILING | ACCOUNT
    tpsl_method: str = "POSITION"
    # Trailing retrace mode: RATIO (pct off peak) | INTERVAL (abs off peak)
    trailing_method: str = "RATIO"
    # Account-level TP/SL in USDT; 0 disables that side.
    account_tp: float = 0.0
    account_sl: float = 0.0
    # Warn when price is within this percent of the exchange's liqPrice.
    liq_distance_pct: float = 5.0
    # Periodic loops, in seconds.
    scan_interval: float = 15.0
    guard_interval: float = 15.0
    mid_interval: float = 15.0
    # How often the Telegram report fires. Lives here so format_report can name
    # its own cadence in the header; it was only ever in the kv store, so the
    # header's "every Ns" branch could never be taken.
    report_interval: int = 30
    # Cached from trading_pairs.
    base_precision: int = 3
    quote_precision: int = 2

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
        # An unknown TPSL method silently fell back to POSITION inside
        # PositionTPSL, so the bot traded a model the operator did not pick.
        self.tpsl_method = TPSLMethod.parse(self.tpsl_method).value
        self.trailing_method = str(self.trailing_method).strip().upper()
        if self.trailing_method not in ("RATIO", "INTERVAL"):
            self.trailing_method = "RATIO"
        self.account_tp = max(0.0, self.account_tp)
        self.account_sl = max(0.0, self.account_sl)
        # Clocks feed an asyncio.sleep tick, so a 0 or negative interval would
        # spin the periodic loop at full speed.
        self.scan_interval    = max(1.0, self.scan_interval)
        self.guard_interval   = max(1.0, self.guard_interval)
        self.mid_interval     = max(1.0, self.mid_interval)


# Minutes per timeframe, used to rank timeframes and to pick the "highest" one
# for the trend filter. Mirrors trader.api.ws.KLINE_INTERVALS.
_TF_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120,
    "4h": 240, "6h": 360, "8h": 480, "12h": 720, "1d": 1440, "1w": 10080,
    "1M": 43200,
}


def _norm_side(raw: Any, default: str = "LONG") -> str:
    """Normalise an exchange position side to the engine's LONG/SHORT vocabulary.

    Position state is read back from two places — the private WS `OPEN` push
    and REST `get_pending_positions` — and neither is guaranteed to use the
    same words the engine internally reasons in. Bitunix reports `side` as
    BUY/SELL on some routes and LONG/SHORT on others.

    Storing the raw string made every downstream `side == "LONG"` test false
    for a long position, so it took the short branch in ALL of them at once:
    the TP was built BELOW entry, the SL ABOVE entry, PNL was signed backwards,
    and breakeven could never fire. A position adopted at startup got an
    instantly-armed stop above its entry and was closed out on the next tick.
    This is the single function that makes both vocabularies safe.
    """
    s = str(raw or "").strip().upper()
    if s in ("BUY", "LONG"):
        return "LONG"
    if s in ("SELL", "SHORT"):
        return "SHORT"
    return default


# A price difference below this is string-representation noise, not a real move.
# `avgOpenPrice` re-rendered as "0.0821" one sync and "0.08210000000000001" the
# next is the same price; treating it as a change would rewrite the entry (and
# everything anchored to it) on every tick.
_PRICE_EPS = 1e-12


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


def _entry_price_of(row: Dict[str, Any]) -> float:
    """Entry/average-open price from a position row, whichever name it uses.

    Bitunix is not consistent across the two position endpoints. The docs for
    get_pending_positions document `avgOpenPrice`, while get_history_positions
    documents `entryPrice` — and the official demo's real history response uses
    `entryPrice`. Reading only one of them means the other endpoint silently
    yields 0, which zeroes the entry price and every PNL and TP/SL level
    derived from it. Both are accepted, first non-positive wins nothing.
    """
    for key in ("avgOpenPrice", "entryPrice"):
        val = _opt_float(row.get(key))
        if val:
            return val
    return 0.0


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
    # Whether the protective orders already exist on the exchange. Guards
    # against re-placing them on every resync.
    tpsl_attached: bool = False
    current_price: float = 0.0
    unrealized_pnl_pct: float = 0.0
    best_price: float = 0.0
    # Live fields the position WS channel does carry.
    margin: float = 0.0
    leverage: float = 0.0
    unrealized_pnl: float = 0.0
    # When the exchange last reported unrealized_pnl. The position channel only
    # pushes on events, so without a timestamp there is no way to tell a PNL
    # that is genuinely unchanged from one that stopped being fed.
    unrealized_pnl_at: int = 0
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
        # Build the ladder at the pair's real qty precision, not the 3-decimal
        # guess, or the rung quantities miss what the exchange accepts.
        self.tpsl.set_qty_precision(getattr(cfg, "base_precision", 3) if cfg else 3)
    
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
        # When that price was last refreshed. The ticker channel is the only
        # live price feed; if the socket drops, `_last_price` keeps its last
        # value indefinitely, and a report that prints it without an age looks
        # identical to one printing a real current price.
        self._last_price_at: int = 0
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
        # Last REST account view, so /signal can show account-level PNL free.
        # Mixed value types: floats, the coin string, a bool and an int stamp.
        self._last_account: Dict[str, Any] = {}
        # TPSL decided at signal time, keyed by (symbol, side, qty), waiting
        # for the fill to hand back a positionId. Without this the levels are
        # re-derived from ATR after the fill and can differ from the ones the
        # operator was just told about.
        self._pending_tpsl: Dict[tuple, "PositionTPSL"] = {}
        # Newest-waiting order per (symbol, trade_side). `_pending_tpsl` alone is
        # keyed by the exact qty string we planned, and Bitunix echoes the filled
        # qty back in its own formatting ("455" vs "455.000"), so that key does
        # not reliably match the fill. Adoption used to fall through to the
        # unbounded ATR defaults when it missed, which silently replaced the
        # liquidation-bounded stop we had announced with one that had no
        # liquidation awareness at all. This index makes the match structural.
        self._pending_tpsl_side: Dict[tuple, "PositionTPSL"] = {}
        # Last warned liquidation distance per position, to avoid re-warning
        # on every guard tick while a position sits near its liquidation.
        self._liq_warned: Dict[str, float] = {}
        # Risk-limit tiers are fetched once; `_pair` caching would otherwise
        # skip the fetch entirely.
        self._tiers_loaded = False

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
        total_upnl = 0.0
        total_margin = 0.0
        total_mixed = False
        for pos in self._open_positions.values():
            tpsl = pos.tpsl
            price = pos.current_price or self._last_price
            # Prefer the exchange's own uPNL; fall back to deriving it from
            # price movement so the number is never silently zero. Say which
            # one it is — a derived figure and an exchange figure are not the
            # same claim, and the report should not present one as the other.
            upnl = pos.unrealized_pnl
            source = "exchange"
            # How old the exchange figure is. The position channel pushes only
            # on events, so uPNL legitimately goes quiet between them; past a
            # couple of sync cycles it is more likely to have stopped being fed
            # than to be genuinely unchanged, and quoting it as a live exchange
            # number would be a claim we cannot support.
            if pos.unrealized_pnl_at and upnl:
                stale_pnl = int(time.time()) - pos.unrealized_pnl_at > 120
            else:
                stale_pnl = False
            if not upnl and pos.entry_price and price:
                d = price - pos.entry_price
                upnl = d * float(pos.qty or 0) * (1 if pos.side == "LONG" else -1)
                source = "derived"
            elif stale_pnl:
                # Show the fresh figure instead of the frozen one.
                if pos.entry_price and price:
                    d = price - pos.entry_price
                    upnl = d * float(pos.qty or 0) * (1 if pos.side == "LONG" else -1)
                    source = "derived"
            if not upnl and not pos.entry_price:
                source = "unknown"
            positions.append({
                "id": pos.position_id,
                "symbol": pos.symbol,
                "side": pos.side,
                "qty": pos.qty,
                "entry": pos.entry_price,
                "price": price,
                "pnl_pct": pos.unrealized_pnl_pct,
                "pnl_usdt": upnl,
                "pnl_source": source,
                "tp": tpsl.tp_price if tpsl else None,
                "sl": tpsl.sl_price if tpsl else None,
                "liq": pos.liq_price,
                "method": tpsl.method.value if tpsl else None,
                "breakeven": bool(tpsl and tpsl.breakeven_set),
                "trailing": bool(tpsl and tpsl.trailing_active),
                "margin": pos.margin,
                # True when the stop sits on the far side of liquidation and so
                # can never fire — the exchange closes the position first.
                "sl_beyond_liq": bool(
                    pos.liq_price and tpsl
                    and (tpsl.sl_price - pos.entry_price) * (pos.liq_price - pos.entry_price) > 0
                    and abs(tpsl.sl_price - pos.entry_price) > abs(pos.liq_price - pos.entry_price)
                ),
            })
            total_upnl += upnl
            total_margin += pos.margin or 0.0
            # The total is a sum of values that may come from different places.
            # Track that, so the sum is not presented as a single exchange
            # figure when part of it is a local estimate.
            if source != "exchange":
                total_mixed = True

        acct = self._last_account or {}
        # A negative equity would flip the sign of the displayed percentage and
        # report a losing position as a +100% gain, which is exactly what
        # happened: equity came back negative while the PNL was a real loss.
        # Fall back to the margin actually committed, and finally to zero, so
        # the ratio can never invert.
        equity = _opt_float(acct.get("equity")) or 0.0
        if equity <= 0:
            equity = total_margin or max(
                _opt_float(acct.get("available")) or 0.0, 0.0)
        pct = 0.0
        if equity > 0:
            pct = total_upnl / equity * 100.0
            # Last line of defence: PNL and its percentage must never disagree
            # in sign. If they do, one of the two inputs is wrong, so show the
            # percentage as unknown rather than as a confident wrong number.
            if total_upnl and (pct > 0) != (total_upnl > 0):
                pct = 0.0
        # Age of the price the whole report is built on. `False` means it is
        # fresh enough to quote as current; anything else tells the caller to
        # label it instead of passing it off as live.
        price_age = int(time.time()) - self._last_price_at if self._last_price_at else None
        acct_age = int(time.time()) - acct["at"] if acct.get("at") else None
        # A clock that steps backwards yields a negative age, which every
        # `> threshold` test treats as fresh. Clamp at zero so a backwards step
        # reports "just updated" rather than hiding an arbitrarily old price.
        if price_age is not None:
            price_age = max(0, price_age)
        if acct_age is not None:
            acct_age = max(0, acct_age)
        return {
            "running": self._running,
            "paper": self.cfg.paper,
            "symbol": self.cfg.symbol,
            "timeframes": list(self.cfg.timeframes),
            "price": self._last_price,
            "price_age_s": price_age,
            "price_stale": price_age is None or price_age > 60,
            "acct_age_s": acct_age,
            "signal": sig,
            "positions": positions,
            "total_pnl_usdt": round(total_upnl, 4),
            "total_pnl_pct": round(pct, 2),
            "total_pnl_source": ("exchange" if positions and not total_mixed
                                 else "derived" if positions else "unknown"),
            "account": acct,
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
                    # The account snapshot feeds the reported equity and the
                    # "% of equity" denominator. It used to be refreshed only
                    # inside _place_trade, so between trades — which is most of
                    # the time — /signal and the periodic report showed equity,
                    # available and margin exactly as they were when the last
                    # order was sent. Hours-old balance next to a live PNL is
                    # how a losing position gets reported as a healthy account.
                    await self._get_account()

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

    def _pnl_pct(self, pos: "ManagedPosition", price: float,
                unrealized: Optional[float] = None) -> float:
        """Unrealized return as a percentage of committed MARGIN (ROI).

        The TPSL thresholds are expressed as ROI (`breakeven 2%`, `trailing
        trigger 5%`), and ROI is margin-relative — at 10x a 2% ROI is a 0.2%
        price move. Returning the raw price move instead made every threshold
        trigger ten times too late at 10x leverage.

        Prefer the exchange's own unrealizedPNL over margin when both are known;
        fall back to the price move scaled by leverage, which is the correct
        ROI for an isolated position and a close proxy under cross margin
        (where `margin` is reported as 0).
        """
        if unrealized is not None and pos.margin > 0:
            return unrealized / pos.margin * 100.0
        if price <= 0 or pos.entry_price <= 0:
            return pos.unrealized_pnl_pct
        move = ((price - pos.entry_price) if pos.side == "LONG"
                else (pos.entry_price - price)) / pos.entry_price * 100.0
        return move * max(1.0, pos.leverage or self.cfg.leverage)

    def _on_ticker(self, msg: Dict[str, Any]) -> None:
        """Live price — keeps /signal and position PNL fresh.

        This is also where trailing and breakeven actually get to move: the
        position channel only pushes on position events, so evaluating TPSL
        only there left the trailing stop frozen at its activation level for
        the whole trade.
        """
        data = msg.get("data")
        if not data:
            return
        # The ticker push carries SHORT field names: `la` is the last traded
        # price (`s` symbol, `o` open, `h`/`l` high/low, `b`/`q` volumes).
        # Reading `lastPrice` here — a REST field name — always missed, so the
        # guard below rejected every tick and the live price feed was dead:
        # /signal showed a stale price and trailing never ratcheted.
        raw = data.get("la") or data.get("lastPrice") or data.get("c")
        try:
            price = float(raw)
        except (TypeError, ValueError):
            return
        if price <= 0:
            return
        self._last_price = price
        self._last_price_at = int(time.time())
        for pos_id, pos in list(self._open_positions.items()):
            # Pass the stored exchange uPNL on EVERY path. The position channel
            # only pushes on events, so between pushes `pos.unrealized_pnl` is
            # the last thing the exchange said — passing it here means the
            # ticker and the position push compute ROI the same way instead of
            # each picking a different branch on message ordering. It is the
            # price-move fallback below that keeps the value fresh between
            # pushes, not a second formula.
            pnl_pct = self._pnl_pct(pos, price, pos.unrealized_pnl or None)
            result = pos.update_price(price, pnl_pct)
            self._apply_tpsl_action(pos_id, pos, result)
    
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
                side=_norm_side(data.get("side"), "LONG"),
                qty=str(data.get("qty", "0")),
                entry_price=entry,
                opened_at=int(data.get("ctime", 0) or time.time() * 1000),
                paper=False,
                strategy="ws",
            )
            pos.init_tpsl(self._atr(), self.cfg)
            self._adopt_pending_tpsl(pos)
            self._open_positions[pos_id] = pos
            # Protection first, notification second: a position must never be
            # reported as opened while it is still naked on the exchange.
            asyncio.create_task(self._attach_position_tpsl(pos))
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
        # Store the exchange's own uPNL. It was read into a local and handed to
        # _pnl_pct, then dropped — `unrealized_pnl` stayed at its 0.0 default for
        # the life of the process, so signal_snapshot's "prefer the exchange"
        # branch never actually preferred anything and every position fell
        # through to the locally derived figure.
        if upnl:
            pos.unrealized_pnl = upnl
            pos.unrealized_pnl_at = int(time.time())
        pnl_pct = self._pnl_pct(pos, price, upnl if upnl else None)

        result = pos.update_price(price, pnl_pct)
        self._apply_tpsl_action(pos_id, pos, result)

    def _plan_risk(self, side: str, price: float, atr_val: float,
                   balance: float, pair: Dict[str, Any]) -> dict:
        """Solve position size and stop together, both bounded by liquidation.

        Sizing and liquidation are not independent here. Under CROSS the
        liquidation price carries a `balance / qty` term, so shrinking the
        position moves the liquidation price further away — and conversely, a
        stop placed from ATR alone can land beyond the liquidation price, where
        it can never fire because the exchange closes you first. That is the
        exact failure that produced a SHORT with SL 0.00128 ABOVE its liq.

        So: pick the ATR stop, pull it inside the liquidation price with a
        safety buffer, then re-solve the quantity so the loss at that stop is
        the configured risk percentage of the balance. Both move together, so
        this iterates until the pair is stable.
        """
        long = side == "BUY"
        # The risk helpers speak LONG/SHORT; the engine speaks BUY/SELL. Passing
        # the wrong vocabulary makes every LONG fall into the short branch.
        tside = "LONG" if long else "SHORT"
        base_prec = self.cfg.base_precision
        min_qty = _opt_float(pair.get("minTradeVolume")) or 0.0
        max_qty = _opt_float(pair.get("maxMarketOrderVolume"))

        risk_usdt = balance * (self.cfg.risk_pct / 100.0)
        mmr = get_mmr(price * (min_qty or 1.0))  # tier depends on notional

        def liq_price_for(q: float) -> float:
            if q <= 0:
                return 0.0
            if self.cfg.margin_mode == "CROSS":
                return calc_liquidation_cross(price, tside, q, balance, price * q)
            # ISOLATION: only this position's own margin is at risk.
            return calc_liquidation(price, self.cfg.leverage, tside, q, price,
                                    margin=price * q / self.cfg.leverage,
                                    notional=price * q).liq_price

        qty = 0.0
        sl = tp = liq = 0.0
        for _ in range(4):                 # converges in 2; 4 is a safety net
            liq = liq_price_for(qty)
            # Distance from entry to liquidation, which bounds the stop.
            room = abs(liq - price)
            # Keep the stop a buffer inside liquidation. liq_distance_pct is the
            # buffer as a share of that room, so 0.5% leaves the stop 0.5% of
            # the way inside liq rather than 0.5% of price.
            buffer = max(0.0, min(50.0, self.cfg.liq_distance_pct)) / 100.0
            d_sl = min(atr_val, room * (1.0 - buffer))
            # A stop so wide it cannot fit any position inside the risk budget
            # would round the size to zero and silently kill every trade; cap it
            # at the widest stop that still affords the exchange minimum.
            if min_qty > 0:
                d_sl = min(d_sl, risk_usdt / min_qty)
            # And never tighter than a rounding error, or the stop is inside the
            # spread and gets taken out by noise.
            d_sl = max(d_sl, price * 0.001)
            sl = price - d_sl if long else price + d_sl

            # Quantity whose loss AT THIS STOP equals the risk budget.
            qty = risk_usdt / d_sl if d_sl > 0 else 0.0
            if max_qty:
                qty = min(qty, max_qty)

        qty = _floor_to(qty, base_prec)
        if qty <= 0 or (min_qty and qty < min_qty):
            raise OrderUnitError(
                f"risk {self.cfg.risk_pct}% of {balance:.2f} {self.cfg.margin_coin} "
                f"cannot afford the exchange minimum {min_qty} at {price}"
            )
        if max_qty:
            qty = min(qty, _floor_to(max_qty, base_prec))
        # Final liquidation price for the quantity actually being sent.
        liq = liq_price_for(qty)
        # R:R = 2 against the stop we really got, not the ATR one we wanted.
        d_sl = abs(sl - price)
        tp = price + 2 * d_sl if long else price - 2 * d_sl
        return {"qty": qty, "sl": sl, "tp": tp, "liq": liq, "d_sl": d_sl,
                "risk_usdt": risk_usdt, "max_loss_usdt": qty * d_sl}

    def _apply_tpsl_action(self, pos_id: str, pos: "ManagedPosition",
                           result: dict) -> None:
        """Push a TPSL state change to the exchange, then report it."""
        action = result.get("action", "none")
        if action == "none":
            return

        if action in ("breakeven", "trailing_start", "trailing_update"):
            # Before the first attach there is no order to modify, and a
            # modify would bounce with "order not found". Attach instead.
            if pos.tpsl_attached:
                asyncio.create_task(self._push_position_tpsl(pos))
            else:
                asyncio.create_task(self._attach_position_tpsl(pos))
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
            entry = _entry_price_of(row)
            if entry > 0:
                return entry
        return self._last_price

    def _to_dataframe(self, tf: str = None) -> Optional[pd.DataFrame]:
        """Turn a timeframe's candle buffer into the DataFrame strategies expect.

        The contract is in BaseStrategy.generate: columns open/high/low/close
        plus baseVol, sorted ASCENDING by time, oldest first. Indicators read the
        last row as "now", so a reversed frame would make every signal look at
        the oldest bar instead of the newest.
        """
        tf = tf or self.cfg.interval
        rows = self._klines.get(tf) or []
        if not rows:
            return None
        # Sort by candle open time rather than trusting arrival order: the WS
        # upserts in place, but a backfill followed by a live push for an older
        # bucket can otherwise land out of sequence.
        rows = sorted(rows, key=lambda r: r.get("timestamp", 0))
        df = pd.DataFrame(rows, columns=[
            "timestamp", "open", "high", "low", "close", "baseVol", "quoteVol",
        ])
        for col in ("open", "high", "low", "close", "baseVol", "quoteVol"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"])
        # A DatetimeIndex lets callers reason about bar spacing; the timestamp
        # column stays so nothing has to guess it from the index.
        try:
            df.index = pd.to_datetime(df["timestamp"], unit="ms")
        except Exception:
            df = df.reset_index(drop=True)
        return df

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
    
    def _higher_tf_trend(self) -> Optional[str]:
        """Direction of the highest configured timeframe, or None if unknown.

        Used as a veto: a 1m signal that disagrees with the 15m trend is noise,
        and taking it is how the bot ended up short three times into a rising
        market.
        """
        frames = [tf for tf in (self.cfg.timeframes or []) if tf != self.cfg.interval]
        if not frames:
            return None
        # Highest timeframe = the one with the most minutes per bar.
        top = max(frames, key=lambda tf: _TF_MINUTES.get(tf, 0))
        if _TF_MINUTES.get(top, 0) <= _TF_MINUTES.get(self.cfg.interval, 0):
            return None
        df = self._to_dataframe(top)
        if df is None or len(df) < 60:
            return None
        try:
            from trader import indicators as ta
            close = df["close"].astype(float)
            fast = ta.ema(close, 20)
            slow = ta.ema(close, 50)
            f, s = float(fast.iloc[-1]), float(slow.iloc[-1])
        except Exception as e:
            logger.warning(f"trend filter failed on {top}: {e}")
            return None
        if not (f and s):
            return None
        # Require a real separation, not noise: 0.05% between the two EMAs.
        sep = abs(f - s) / s if s else 0.0
        if sep < 0.0005:
            return None
        return "UP" if f > s else "DOWN"

    async def _evaluate_strategies(self, tf: str = None) -> None:
        """Run all strategies on latest candle for a given timeframe."""
        tf = tf or self.cfg.interval
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
        # what /signal shows and what the threshold gate applies to. Capped at
        # 0.95: three 1m strategies agreeing is still three readings of the same
        # noisy timeframe, and reporting it as 100% certainty overstated it.
        confidence = min(0.95, self._confidence(signals, direction))
        # The periodic loop sweeps every configured timeframe, so a 1m read would
        # otherwise overwrite a 15m read purely by being evaluated last. Keep the
        # signal from the slowest timeframe that produced one: a higher-timeframe
        # view is the more meaningful one to show and to act on.
        prev = self._latest_signal
        prev_tf = prev.get("timeframe") if prev else None
        try:
            prev_rank = _TF_MINUTES.get(prev_tf or "", 0)
            new_rank = _TF_MINUTES.get(tf or "", 0)
        except Exception:
            prev_rank = new_rank = 0
        if prev_tf and prev.get("direction") and prev.get("direction") != "NEUTRAL" \
                and prev_rank > new_rank:
            self._latest_signal["note"] = (
                f"showing {prev_tf} signal (higher timeframe confirmed)"
            )
            return

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

        # Trend veto: never open against the higher timeframe. A 1m signal into
        # a 15m uptrend is the exact failure mode that produced three losing
        # shorts in a rising market. The signal is still reported — only the
        # trade is blocked — so /signal keeps showing what the market is doing.
        trend = self._higher_tf_trend()
        if trend:
            against = ((direction is Direction.SELL and trend == "UP") or
                       (direction is Direction.BUY and trend == "DOWN"))
            if against:
                self._latest_signal["blocked"] = (
                    f"against higher-timeframe trend ({trend})"
                )
                self._notify(
                    f"🚫 *SIGNAL BLOCKED*\n"
                    f"`{self.cfg.symbol}` [{tf}] wanted `{direction.value}` but the "
                    f"higher timeframe is trending `{trend}`.\n"
                    f"No trade opened."
                )
                logger.info(f"[{tf}] vetoed: {direction.value} vs trend {trend}")
                return
        self._latest_signal.pop("blocked", None)

        # Strategies still have to run at max exposure so /signal reports what
        # the market is doing. Bailing out before the scan left the command
        # showing a signal from whenever the last slot freed up.
        if len(self._open_positions) >= self.cfg.max_positions:
            self._latest_signal["blocked"] = (
                f"max positions reached ({len(self._open_positions)}"
                f"/{self.cfg.max_positions})"
            )
            return
        self._latest_signal.pop("blocked", None)

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
        self._last_price_at = int(time.time())

        pair = await self._load_pair()
        base_prec = self.cfg.base_precision
        quote_prec = self.cfg.quote_precision
        side = "BUY" if direction == Direction.BUY else "SELL"
        atr_val = self._atr(tf)

        # Size and stop are solved together: the stop is pulled inside the
        # liquidation price, then the size is re-solved so that hitting that
        # stop costs the configured risk percentage of the balance.
        try:
            plan = self._plan_risk(side, price, atr_val, available, pair)
        except OrderUnitError as e:
            logger.warning(f"risk plan failed: {e}")
            self._notify(f"⚠️ Skipping trade: {e}")
            self._latest_signal["opened"] = False
            self._latest_signal["error"] = str(e)
            return

        sl, tp = plan["sl"], plan["tp"]

        # Build the TPSL state machine first: it decides whether we send a fixed
        # TP at all (TRAILING has none until it activates) and whether the
        # ladder needs its own sized orders.
        tpsl = PositionTPSL(
            entry_price=price,
            side="LONG" if direction == Direction.BUY else "SHORT",
            atr_value=atr_val,
            method=self.cfg.tpsl_method,
            breakeven_threshold_pct=self.cfg.breakeven_threshold_pct,
            trailing_trigger_roi_pct=self.cfg.trailing_trigger_roi_pct,
            trailing_stop_pct=self.cfg.trailing_stop_pct,
            trailing_mode=self.cfg.trailing_method,
            trailing_distance=self.cfg.trailing_distance,
            sl_price=sl,
            tp_price=tp,
            qty=plan["qty"],
            quote_precision=quote_prec,
        )
        tpsl.set_qty_precision(base_prec)

        reasons = "; ".join(s.reason for s in signals[:3])
        confidence = self._latest_signal.get("confidence", 0)
        qty_str = price_precision(plan["qty"], base_prec)
        notional = plan["qty"] * price
        self._latest_signal.update({
            "qty": qty_str,
            "sl": price_precision(tpsl.sl_price, quote_prec),
            "tp": price_precision(tpsl.tp_price, quote_prec) if tpsl.tp_price else None,
            "tpsl_method": self.cfg.tpsl_method,
            "liq": plan["liq"],
            "opened": False,
        })

        msg = (
            f"{'📈' if side == 'BUY' else '📉'} *{'PAPER ' if self.cfg.paper else ''}SIGNAL*\n"
            f"Symbol: `{self.cfg.symbol}` [{tf or self.cfg.interval}]\n"
            f"Direction: `{side}`\n"
            f"Confidence: `{confidence}%`\n"
            f"Price: `{price}`\n"
            f"Qty: `{qty_str}`\n"
            f"Notional: `{round(notional, 2)} {self.cfg.margin_coin}`\n"
            f"SL: `{price_precision(tpsl.sl_price, quote_prec)}`\n"
            f"TP: `{price_precision(tpsl.tp_price, quote_prec) if tpsl.tp_price else '— (trailing)'}`\n"
            f"Liq: `{price_precision(plan['liq'], quote_prec)}` "
            f"(stop is {'inside' if (plan['sl'] - price) * (plan['liq'] - price) > 0 else 'OUTSIDE'} it)\n"
            f"Max loss: `{round(plan['max_loss_usdt'], 4)} {self.cfg.margin_coin}` "
            f"({self.cfg.risk_pct}% of balance)\n"
            f"TPSL model: `{self.cfg.tpsl_method}`\n"
            f"Strategies: `{', '.join(s.strategy for s in signals)}`\n"
            f"Reason: {reasons}"
        )
        self._notify(msg)

        if self.cfg.paper:
            await self._log_paper_trade(side, qty_str, price, sl, tp, reasons)
            return

        # Live order. TP/SL rides along on the order itself (place_order accepts
        # tpPrice/slPrice), so protection exists from the instant of the fill
        # rather than one websocket push later. _attach_position_tpsl still
        # runs afterwards as a reconciliation pass, and is what places the
        # PARTIAL ladder, which cannot be expressed on the order.
        self._pending_tpsl[(self.cfg.symbol, side, qty_str)] = tpsl
        self._pending_tpsl_side[(self.cfg.symbol, side)] = tpsl
        try:
            # PARTIAL owns its profit targets via the ladder, so the order
            # carries the position-wide STOP only — sending a position-level TP
            # as well would close the position twice at the same price.
            attach_tp = tpsl.tp_price is not None and \
                tpsl.method is not TPSLMethod.PARTIAL
            resp = await self.rest.place_order(
                symbol=self.cfg.symbol, side=side, order_type="MARKET",
                qty=qty_str,
                trade_side="OPEN" if self.cfg.position_mode == "HEDGE" else None,
                reduce_only=False,
                sl_price=price_precision(tpsl.sl_price, quote_prec),
                sl_stop_type="LAST_PRICE",
                sl_order_type="MARKET",
                tp_price=price_precision(tpsl.tp_price, quote_prec) if attach_tp else None,
                tp_stop_type="LAST_PRICE" if attach_tp else None,
                tp_order_type="MARKET" if attach_tp else None,
            )
            order_id = (resp.get("data") or {}).get("orderId", "")
            logger.info(f"order placed: {order_id}")
            self._notify(f"✅ Order placed: `{order_id}` qty=`{qty_str}`")
            self._latest_signal["opened"] = True
            self._latest_signal["order_id"] = order_id
            self._latest_signal["sl"] = price_precision(tpsl.sl_price, quote_prec)
            self._latest_signal["tp"] = (price_precision(tpsl.tp_price, quote_prec)
                                         if tpsl.tp_price else None)
        except BitunixError as e:
            self._pending_tpsl.pop((self.cfg.symbol, side, qty_str), None)
            self._pending_tpsl_side.pop((self.cfg.symbol, side), None)
            logger.error(f"order failed: {e}")
            self._notify(f"❌ Order failed: [{e.code}] {e.msg}")
            self._latest_signal["opened"] = False
            self._latest_signal["error"] = f"[{e.code}] {e.msg}"

    async def _log_paper_trade(self, side: str, qty: str, price: float,
                               sl: float, tp: float, reason: str) -> None:
        """Record a simulated fill so /trader history has something to show."""
        await _db_log_paper_trade(self.cfg.symbol, side, qty, price, reason)

    async def _load_pair(self, force: bool = False) -> Dict[str, Any]:
        """Fetch and cache the symbol's trading_pairs row (precision + limits).

        `force` re-reads the exchange even when a row is already cached. The
        cache is per-engine, but precision is per-PAIR, so switching symbols has
        to bust it — otherwise the new symbol silently keeps the old symbol's
        quotePrecision and every price renders truncated.
        """
        if self._pair and not force:
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
        await self._load_risk_tiers()
        return self._pair

    async def _load_risk_tiers(self) -> None:
        """Refresh the maintenance-margin tier table for this symbol.

        The liquidation math falls back to a hardcoded BTCUSDT snapshot, which
        is wrong for any other symbol and drifts over time. get_position_tiers
        is the authoritative source, so load it once at startup and hand it to
        the liquidation module.
        """
        if self._tiers_loaded:
            return
        self._tiers_loaded = True
        try:
            resp = await self.rest.get_position_tiers(self.cfg.symbol)
            rows = resp.get("data") or []
            if not rows:
                return
            rows = sorted(rows, key=lambda r: float(r.get("level", 0) or 0))
            set_tiers(rows)
            logger.info(f"risk tiers loaded for {self.cfg.symbol}: {len(rows)} levels")
        except Exception as e:
            logger.warning(f"get_position_tiers failed, using default tiers: {e}")

    async def _push_position_tpsl(self, pos: "ManagedPosition") -> None:
        """Rewrite the position's TP/SL after a local level change.

        Uses tpsl/position/modify_order — keyed by symbol + positionId, not by
        orderId. Skipped in paper mode: there is nothing on the exchange to move.
        """
        if self.cfg.paper or not pos.tpsl:
            return
        body = pos.tpsl.position_payload()
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

    def _adopt_pending_tpsl(self, pos: "ManagedPosition") -> None:
        """Hand the fill the exact levels chosen when the signal fired.

        The position only exists after the market fill, and by then the ATR has
        moved on. Re-deriving the levels here would put stops on the exchange
        that differ from the ones just announced in Telegram, so match the
        waiting order and adopt its state machine.

        Matching is by (symbol, trade_side) first and qty only as a tiebreaker.
        An exact qty-string match is unreliable: we floor the planned quantity to
        basePrecision ourselves, while the exchange echoes the fill back in its
        own formatting, so "455" and "455.000" are the same position and the
        exact key missed. When that happened the position silently fell back to
        the ATR defaults from init_tpsl — stops with no liquidation bound, which
        is how a reported SL of 0.08171060558526559 replaced the planned
        0.08186 and ended up flagged SL>LIQ.
        """
        sym = pos.symbol
        side = "LONG" if pos.side == "LONG" else "SHORT"
        trade_side = "BUY" if side == "LONG" else "SELL"
        side_key = (sym, trade_side)

        pending = self._pending_tpsl.pop((sym, trade_side, str(pos.qty)), None)
        indexed = self._pending_tpsl_side.get(side_key)
        if indexed is not None:
            # An exact-qty match wins; otherwise the newest waiting order for
            # this symbol and direction is the fill we are looking at.
            if pending is None or indexed is pending:
                pending = indexed
        if pending is not None:
            self._pending_tpsl_side.pop(side_key, None)
            # Drop any other stale key for this side so it cannot be adopted by
            # a later, unrelated fill.
            for key in [k for k in self._pending_tpsl
                        if len(k) == 3 and k[0] == sym and k[1] == trade_side]:
                self._pending_tpsl.pop(key, None)
            pos.tpsl = pending
            pos.tpsl.entry_price = pos.entry_price or pending.entry_price
            pos.tpsl.best_price = pos.entry_price
            # The fill can differ from the plan by a tick; re-check the geometry
            # against the real entry so an adopted stop is never left on the
            # wrong side of it.
            pos.tpsl._sanitise_levels()
            return

        # Nothing waiting (position predates this process): keep the ATR-derived
        # levels init_tpsl built, but split the ladder at exchange precision and
        # bound the stop inside liquidation, because these levels were never
        # through _plan_risk and carry no liquidation awareness of their own.
        if pos.tpsl:
            pos.tpsl.set_qty_precision(self.cfg.base_precision)
            if pos.liq_price:
                self._bound_sl_inside_liq(pos)

    def _bound_sl_inside_liq(self, pos: "ManagedPosition") -> bool:
        """Pull the stop inside the liquidation price, if it is not already.

        Only _plan_risk's levels are liquidation-bounded. Anything built outside
        it — an ATR default for an adopted position, a fill that slipped — can
        sit on the far side of liquidation, where the exchange closes the
        position before the stop can ever fill. A stop there is not a stop.
        """
        tpsl = pos.tpsl
        liq = pos.liq_price
        entry = pos.entry_price
        if not tpsl or not liq or liq <= 0 or not entry or tpsl.sl_price is None:
            return False
        long = pos.side == "LONG"
        # Liquidation must be on the losing side of entry, or there is no
        # meaningful room to bound against.
        if (long and liq >= entry) or (not long and liq <= entry):
            return False
        inside = tpsl.sl_price > liq if long else tpsl.sl_price < liq
        if inside:
            return False

        room = abs(liq - entry)
        buffer = max(0.0, min(50.0, self.cfg.liq_distance_pct)) / 100.0
        tick = 10 ** -self.cfg.quote_precision
        if long:
            new_sl = liq + max(room * buffer, tick)
            new_sl = min(new_sl, entry - tick)      # stay off entry
        else:
            new_sl = liq - max(room * buffer, tick)
            new_sl = max(new_sl, entry + tick)
        tpsl.sl_price = new_sl
        tpsl._sanitise_levels()
        logger.warning(f"position {pos.position_id} SL {new_sl} pulled inside "
                       f"liq {liq} (was outside)")
        return True

    async def _attach_position_tpsl(self, pos: "ManagedPosition") -> None:
        """Reconcile the position's protective orders, and place the ladder.

        The market order already carries the position TP/SL, so normally this
        finds them in place and only fills the gaps: a position that predates
        this process (restart) has none, and PARTIAL additionally needs its
        sized take-profit rungs, which place_order cannot express.

        Guarded by pos.tpsl_attached so a resync can never stack duplicate
        protective orders on one position, and it checks the exchange first
        because Bitunix permits only ONE position TP/SL per position.
        """
        if self.cfg.paper or not pos.tpsl or pos.tpsl_attached:
            return

        # Last gate before anything reaches the exchange. A stop on the far side
        # of liquidation can never fill, so if the levels came from anywhere
        # other than _plan_risk — an adopted position, a slipped fill — bound
        # them here rather than trusting the caller.
        moved_sl = self._bound_sl_inside_liq(pos)

        already_there = False
        try:
            resp = await self.rest.get_pending_tpsl_orders(
                symbol=pos.symbol, position_id=pos.position_id, limit=100)
            existing = resp.get("data") or []
            if isinstance(existing, dict):
                existing = existing.get("list") or []
            already_there = bool(existing)
        except Exception as e:
            logger.warning(f"pending tpsl check failed for {pos.position_id}: {e}")

        if not already_there:
            pos.tpsl_attached = True
            body = pos.tpsl.position_payload()
            try:
                await self.rest.place_position_tpsl(
                    symbol=pos.symbol, position_id=pos.position_id,
                    tp_price=body.get("tpPrice"), sl_price=body.get("slPrice"),
                    tp_stop_type=body.get("tpStopType"), sl_stop_type=body.get("slStopType"),
                )
                logger.info(f"position {pos.position_id} TPSL placed: {body}")
            except BitunixError as e:
                pos.tpsl_attached = False   # let the next sync retry
                logger.error(f"place_position_tpsl failed for {pos.position_id}: {e}")
                self._notify(f"⚠️ Could not attach TP/SL to `{pos.position_id}`: "
                             f"[{e.code}] {e.msg}")
                return
            self._notify(
                f"🛡 *TP/SL attached* `{pos.position_id}`\n"
                f"SL: `{body.get('slPrice')}`"
                + (f" | TP: `{body.get('tpPrice')}`" if body.get("tpPrice") else "")
            )
        else:
            # Order-level TP/SL is already live (or predates this process).
            pos.tpsl_attached = True
            logger.info(f"position {pos.position_id} already has "
                        f"{len(existing)} TP/SL order(s); leaving them alone")
            if moved_sl:
                # The stop that is already on the exchange sits beyond
                # liquidation, so it can never fill. Under CROSS the liquidation
                # price moves as the account balance moves, which is how a stop
                # placed correctly at entry drifts outside it later. Rewrite it
                # rather than just reporting SL>LIQ every cycle.
                logger.warning(f"rewriting out-of-bounds SL for {pos.position_id}")
                self._notify(
                    f"🛠 *Stop rescued*\n"
                    f"`{pos.symbol}` {pos.side} `{pos.position_id}` — SL was "
                    f"beyond liquidation and could never fill. Moved inside."
                )
                await self._push_position_tpsl(pos)

        if pos.tpsl.method is TPSLMethod.PARTIAL:
            await self._place_partial_ladder(pos)

    async def _place_partial_ladder(self, pos: "ManagedPosition") -> None:
        """Place the Partial TP/SL ladder as sized tpsl/place_order calls."""
        if self.cfg.paper or not pos.tpsl:
            return
        levels = pos.tpsl.unplaced_levels()
        orders = pos.tpsl.partial_orders()

        if not levels:
            # Silent no-op otherwise. _build_ladder yields nothing when it has
            # no qty to split, and this position then carried a stop with NO
            # take-profit anywhere on the exchange: the bot reported a large
            # open profit, price ran through every ladder rung, none of them
            # existed, and the position round-tripped back into a loss. A
            # target that may silently not exist is worse than no ladder.
            logger.error(f"partial ladder empty for {pos.position_id} "
                         f"(qty={pos.qty!r}) — falling back to a single TP")
            await self._ensure_take_profit(pos, "ladder had no rungs to place")
            return

        placed = 0
        failure = ""
        for lv, body in zip(levels, orders):
            try:
                await self.rest.place_tpsl_order(
                    symbol=pos.symbol, position_id=pos.position_id, **body
                )
                lv.placed = True       # so a resync cannot re-send this rung
                placed += 1
            except BitunixError as e:
                failure = f"[{e.code}] {e.msg}"
                logger.error(f"partial TPSL failed for {pos.position_id}: {e}")
                break
            except Exception as e:
                # A non-Bitunix error (a parse failure, a timeout) used to kill
                # this task with nothing logged and nothing notified, so the
                # position stayed permanently untargeted and nobody knew.
                failure = f"{type(e).__name__}: {e}"
                logger.exception(f"partial TPSL crashed for {pos.position_id}")
                break

        if placed:
            self._notify(
                f"🪜 *Partial TP/SL ladder placed*\n"
                f"`{pos.symbol}` {pos.side} — {placed}/{len(levels)} level(s)"
            )
        if placed < len(levels):
            self._notify(f"⚠️ Partial TP/SL incomplete on `{pos.position_id}` — "
                         f"{placed}/{len(levels)} rungs placed ({failure})")
            await self._ensure_take_profit(pos, f"only {placed}/{len(levels)} rungs placed")

    async def _ensure_take_profit(self, pos: "ManagedPosition", reason: str) -> None:
        """Guarantee at least one take-profit order exists for this position.

        Invariant, not optimisation: a position may never sit on the exchange
        with a stop and no way to take profit. Sized with the POSITION's full
        remaining quantity as a single partial-order TP — deliberately not a
        position-level TP/SL, because Bitunix permits only one position TP/SL
        per position and the entry order already owns that slot with its stop.
        """
        tp = pos.tpsl.tp_price if pos.tpsl else None
        qty = pos.qty
        if not tp or not qty:
            self._notify(
                f"🛑 *NO TAKE-PROFIT on `{pos.symbol}`*\n"
                f"`{pos.position_id}` {pos.side} qty `{qty}` has a stop but no "
                f"target ({reason}). It can only exit via the stop.\n"
                f"Close it manually or set `tpsl_method` to POSITION."
            )
            return
        try:
            await self.rest.place_tpsl_order(
                symbol=pos.symbol,
                position_id=pos.position_id,
                tp_price=price_precision(tp, self.cfg.quote_precision),
                tp_qty=str(qty),
                tp_order_type="MARKET",
                tp_stop_type="LAST_PRICE",
            )
            logger.info(f"fallback TP placed for {pos.position_id} @ {tp} qty {qty}")
            self._notify(
                f"🛟 *Fallback TP placed*\n"
                f"`{pos.symbol}` {pos.side} — full `{qty}` at "
                f"`{price_precision(tp, self.cfg.quote_precision)}`\n"
                f"({reason})"
            )
        except Exception as e:
            logger.exception(f"fallback TP failed for {pos.position_id}")
            self._notify(
                f"🛑 *FAILED to place any TP on `{pos.symbol}`*\n"
                f"`{pos.position_id}` {pos.side} qty `{qty}` has a STOP and "
                f"NO TARGET ({reason}; {e})."
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
                entry = _entry_price_of(row)
            except (TypeError, ValueError):
                entry = 0.0
            if entry <= 0:
                logger.warning(f"position {pid} has no avgOpenPrice/entryPrice; skipping")
                continue
            pos = self._open_positions.get(pid)
            if pos is None:
                pos = ManagedPosition(
                    position_id=pid,
                    symbol=str(row.get("symbol", self.cfg.symbol)),
                    side=_norm_side(row.get("side"), "LONG"),
                    qty=str(row.get("qty", "0")),
                    entry_price=entry,
                    opened_at=int(row.get("ctime", 0) or time.time() * 1000),
                    paper=self.cfg.paper,
                    strategy="sync",
                )
                pos.margin = _opt_float(row.get("margin")) or 0.0
                pos.leverage = _opt_float(row.get("leverage")) or float(self.cfg.leverage)
                pos.init_tpsl(atr, self.cfg)
                self._adopt_pending_tpsl(pos)
                self._open_positions[pid] = pos
                # A position that exists only because the process restarted
                # still needs its stop; attach it once, guarded per position.
                if not self.cfg.paper:
                    asyncio.create_task(self._attach_position_tpsl(pos))
            else:
                new_qty = str(row.get("qty", pos.qty))
                if new_qty != pos.qty:
                    pos.qty = new_qty
                    # Keep the ladder sized against the live position, or a
                    # partial close leaves the rungs closing more than is left.
                    if pos.tpsl:
                        try:
                            pos.tpsl.qty = float(new_qty)
                        except (TypeError, ValueError):
                            pass
                pos.margin = _opt_float(row.get("margin")) or pos.margin
                # Entry price and leverage must track the exchange on EVERY
                # sync, not only when the position is first discovered. A
                # partial close or an add-to-position moves the true average
                # entry, and keeping the old one reports PNL — and every
                # TP/SL level anchored to it — against an entry price the
                # position no longer has.
                entry = _entry_price_of(row)
                if entry > 0 and abs(entry - pos.entry_price) > _PRICE_EPS:
                    logger.info(f"position {pid} entry {pos.entry_price} -> {entry}")
                    pos.entry_price = entry
                    if pos.tpsl:
                        pos.tpsl.entry_price = entry
                        pos.tpsl._sanitise_levels()
                        # Deliberately NOT resetting best_price. The trailing
                        # stop ratchets from the highest price seen since entry;
                        # rewinding it to the new average entry throws away the
                        # peak the trail has been tracking and leaves the stop
                        # looser than it already was.
                pos.leverage = _opt_float(row.get("leverage")) or pos.leverage
            pos.liq_price = _opt_float(row.get("liqPrice")) or 0.0
            # Keep the exchange's own uPNL on the position itself, not just in
            # the log total below.
            upnl = _opt_float(row.get("unrealizedPNL"))
            if upnl is not None:
                pos.unrealized_pnl = upnl
                pos.unrealized_pnl_at = int(time.time())

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
        # The guard runs on the same tick as sync_positions, which has just
        # fetched these very rows and left them in _position_snapshot. Reuse
        # them; only go back to REST if the snapshot is missing.
        rows = list(self._position_snapshot.values())
        if not rows:
            try:
                resp = await self.rest.get_pending_positions()
                rows = resp.get("data") or []
            except Exception as e:
                logger.error(f"account guard: {e}")
                return
        if not rows:
            # Flat again: re-arm, so a later session can hit the same
            # threshold instead of the latch holding until restart.
            self.account_guard.reset()
            return
        self._warn_near_liquidation(rows)
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
    
    def _warn_near_liquidation(self, rows: List[Dict[str, Any]]) -> None:
        """Warn when a position is close to its liquidation price.

        Bitunix tiers the maintenance margin rate by notional, so a fixed
        percentage is not enough on its own. Prefer the exchange's own
        `liqPrice` from get_pending_positions (it already applies the tier in
        force for that symbol); fall back to the tier table for the margin-ratio
        check, which is what decides whether liquidation is actually imminent.

        Warns at most once per position per 5% of remaining distance so a
        15-second guard interval does not spam the channel.
        """
        price = self._last_price
        if price <= 0:
            return
        for row in rows:
            pid = str(row.get("positionId", ""))
            liq = _opt_float(row.get("liqPrice")) or 0.0
            if liq <= 0:
                continue
            side = _norm_side(row.get("side"), "LONG")
            entry = _entry_price_of(row)
            qty = _opt_float(row.get("qty")) or 0.0
            margin = _opt_float(row.get("margin")) or 0.0
            leverage = _opt_float(row.get("leverage")) or float(self.cfg.leverage)

            # How far price still has to travel to reach liquidation.
            room = (liq - price) / price * 100 if side == "LONG" \
                else (price - liq) / price * 100
            near_price = room <= self.cfg.liq_distance_pct

            near_margin = False
            liq_res = None
            # The position snapshot carries the exchange's own `marginRate`
            # (margin over maintenance margin) — authoritative, and already
            # tier-aware. Only compute it ourselves when it is absent.
            margin_rate = _opt_float(row.get("marginRate"))
            if margin_rate is not None and margin_rate > 0:
                near_margin = is_near_liquidation(margin_rate)
                ratio_text = f"{margin_rate:.3f}"
            elif margin > 0 and qty > 0 and entry > 0:
                liq_res = calc_liquidation(
                    avg_entry=entry, leverage=int(leverage or 1), side=side,
                    qty=qty, current_price=price, margin=margin,
                )
                near_margin = is_near_liquidation(liq_res.margin_ratio)
                ratio_text = f"{liq_res.margin_ratio:.3f}"
            else:
                ratio_text = None

            last = self._liq_warned.get(pid, 1e9)
            if (near_price or near_margin) and (last - room) >= 5.0:
                self._liq_warned[pid] = room
                self._notify(
                    f"⚠️ *NEAR LIQUIDATION* `{row.get('symbol', '')}`\n"
                    f"ID: `{pid}` | Side: `{side}`\n"
                    f"Price: `{price}` | Liq: `{liq}`\n"
                    f"Distance: `{room:.2f}%`"
                    + (f"\nMargin rate: `{ratio_text}`" if ratio_text else "")
                    + "\nReduce size or add margin now."
                )
            elif room > (self._liq_warned.get(pid, 1e9) - 5.0) + 5.0:
                # Back to safety — allow the warning to fire again later.
                self._liq_warned.pop(pid, None)

    async def _get_account(self) -> Dict[str, Any]:
        """Fetch the account and cache the fields the reporter renders.

        Field names come straight from get_single_account's documented schema:
        `marginCoin`, `available`, `frozen`, `margin`, `transfer`, `positionMode`,
        `crossUnrealizedPNL`, `isolationUnrealizedPNL`, `bonus`.

        There is deliberately no `equity` and no bare `unrealizedPNL` on that
        endpoint. Reading either one returned None, so equity stayed 0 and the
        cached account uPNL stayed 0 — the report fell back to margin/available
        for its denominator and showed the account PNL as flat while positions
        were open and losing. Equity is therefore DERIVED here, and explicitly
        labelled as derived in the snapshot so nothing downstream treats it as
        a field the exchange reported.
        """
        try:
            resp = await self.rest.get_account(self.cfg.margin_coin)
            acct = account_dict(resp)
            # Cached so /signal and the periodic report can show account-level
            # PNL without spending an API call on every tick.
            if acct:
                cross = _opt_float(acct.get("crossUnrealizedPNL")) or 0.0
                iso = _opt_float(acct.get("isolationUnrealizedPNL")) or 0.0
                upnl = cross + iso
                available = _opt_float(acct.get("available")) or 0.0
                margin = _opt_float(acct.get("margin")) or 0.0
                frozen = _opt_float(acct.get("frozen")) or 0.0
                # Equity = free balance + committed margin + open PnL. `frozen`
                # is margin reserved by resting orders; it is already part of
                # the account's committed capital, so it is added once here and
                # never double-counted against `margin`.
                self._last_account = {
                    "coin": acct.get("marginCoin") or self.cfg.margin_coin,
                    "available": available,
                    "frozen": frozen,
                    "margin": margin,
                    "unrealized_pnl": upnl,
                    "equity": available + margin + frozen + upnl,
                    # Bitunix's get_single_account has no equity field, so this
                    # figure is always derived here. Flagged so the reporters
                    # can label it rather than present it as an exchange read.
                    "equity_derived": "equity" not in acct,
                    "at": int(time.time()),
                }
            return acct
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
