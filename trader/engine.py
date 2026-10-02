"""Core trading engine — manages the trade lifecycle.

Flow:
  1. Fetch kline history via REST on startup
  2. Subscribe to kline WebSocket for live updates
  3. On each closed candle: run active strategies, compute consensus signal
  4. If BUY/SELL signal AND within open-position limit: place order (or log if paper)
  5. Monitor positions via WebSocket (balance / position / order channels)
  6. On TP/SL hit or exit signal: close position
  7. Emit events → TelegramBridge for notifications

Paper mode: all logic runs but no real orders are placed; trades logged to DB.
"""
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
    consensus_threshold: int = 2     # how many strategies must agree
    min_confidence: float = 0.5


@dataclass
class OpenPosition:
    position_id: str
    symbol: str
    side: str                  # LONG | SHORT
    qty: str
    entry_price: float
    sl_price: Optional[float]
    tp_price: Optional[float]
    strategy: str
    opened_at: int
    paper: bool


class TradingEngine:
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
        self.ws   = BitunixWSClient()
        self._strategies: List[BaseStrategy] = []
        self._klines: List[Dict[str, Any]] = []
        self._open_positions: Dict[str, OpenPosition] = {}
        self._running = False
        self._notify_cb: Optional[Callable[[str], None]] = None

        # Apply active strategies from config
        self._reload_strategies()

    # ── Public API ────────────────────────────────────────────────────

    def on_notify(self, callback: Callable[[str], None]) -> None:
        """Register a callback to send Telegram notifications."""
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
        if not Config.TRADING_ENABLED and not self.cfg.paper:
            raise RuntimeError("Set TRADING_ENABLED=true in .env to enable live trading")

        self._running = True
        logger.info(f"Engine starting — symbol={self.cfg.symbol} paper={self.cfg.paper}")

        await self._setup_account()
        await self._load_tiers()
        await self._load_kline_history()

        # Subscribe to WS streams
        self.ws.subscribe_kline(self.cfg.symbol, self.cfg.interval)
        self.ws.subscribe_positions(self.cfg.symbol)
        self.ws.subscribe_orders(self.cfg.symbol)
        self.ws.subscribe_balance()

        self.ws.on(f"market_kline_{self.cfg.interval}:{self.cfg.symbol}", self._on_kline)
        self.ws.on(f"position:{self.cfg.symbol}", self._on_position_update)
        self.ws.on(f"order:{self.cfg.symbol}", self._on_order_update)

        await self.ws.start()

    async def stop(self) -> None:
        self._running = False
        await self.ws.stop()
        await self.rest.close()

    # ── Account setup ─────────────────────────────────────────────────

    async def _setup_account(self) -> None:
        if self.cfg.paper:
            return
        try:
            await self.rest.change_leverage(self.cfg.symbol, self.cfg.leverage, self.cfg.margin_coin)
            await self.rest.change_margin_mode(self.cfg.symbol, self.cfg.margin_mode, self.cfg.margin_coin)
            await self.rest.change_position_mode(self.cfg.position_mode)
            logger.info("Account configured successfully")
        except BitunixError as e:
            # 20006 = can't change with open orders/positions — that's ok
            if e.code != 20006:
                raise

    async def _load_tiers(self) -> None:
        """Fetch per-symbol risk tiers into the (global) tier table.

        Runs in BOTH paper and live mode: liquidation math is used for paper
        positions too, and the module-level tier table otherwise keeps the
        BTC fallback for a different symbol.
        """
        try:
            resp = await self.rest.get_position_tiers(self.cfg.symbol)
            rows = resp.get("data", [])
            if rows:
                set_tiers(rows)
                logger.info(f"Loaded {len(rows)} position tiers for {self.cfg.symbol}")
        except Exception as exc:
            logger.warning(f"get_position_tiers failed — using fallback BTC tiers: {exc}")

    # ── Kline management ──────────────────────────────────────────────

    async def _load_kline_history(self) -> None:
        resp = await self.rest.get_kline(self.cfg.symbol, self.cfg.interval, limit=200)
        self._klines = resp.get("data", [])
        logger.info(f"Loaded {len(self._klines)} historical candles")

    def _on_kline(self, msg: Dict[str, Any]) -> None:
        """Called for each WS kline update."""
        candle = msg.get("data")
        if not candle:
            return
        # Append if closed candle (new timestamp)
        if self._klines and candle.get("time") == self._klines[-1].get("time"):
            self._klines[-1] = candle   # update in-progress
        else:
            self._klines.append(candle)
            if len(self._klines) > 500:
                self._klines = self._klines[-500:]
            # Only run analysis on a freshly closed candle
            asyncio.create_task(self._analyze())

    # ── Analysis & signal ─────────────────────────────────────────────

    async def _analyze(self) -> None:
        df = self._to_dataframe()
        if df is None or len(df) < 60:
            return

        # Fetch funding rate for strategies that need it
        try:
            fr_resp = await self.rest.get_funding_rate_history(self.cfg.symbol, limit=10)
            funding_rates = [float(r["fundingRate"]) for r in fr_resp.get("data", [])]
        except Exception:
            funding_rates = []

        signals: List[Signal] = []
        for strat in self._strategies:
            try:
                sig = strat.generate(df, funding_rates=funding_rates)
                if sig.direction != Direction.NEUTRAL and sig.confidence >= self.cfg.min_confidence:
                    signals.append(sig)
                    logger.debug(f"{strat.name}: {sig.direction} conf={sig.confidence:.2f} — {sig.reason}")
            except Exception as e:
                logger.warning(f"Strategy {strat.name} error: {e}")

        await self._evaluate_signals(signals)

    async def _evaluate_signals(self, signals: List[Signal]) -> None:
        if not signals:
            return

        buys  = [s for s in signals if s.direction == Direction.BUY]
        sells = [s for s in signals if s.direction == Direction.SELL]

        direction: Optional[Direction] = None
        best_signals: List[Signal] = []

        if len(buys) >= self.cfg.consensus_threshold:
            direction = Direction.BUY
            best_signals = sorted(buys, key=lambda s: s.confidence, reverse=True)
        elif len(sells) >= self.cfg.consensus_threshold:
            direction = Direction.SELL
            best_signals = sorted(sells, key=lambda s: s.confidence, reverse=True)

        if not direction:
            return

        open_count = len(self._open_positions)
        if open_count >= self.cfg.max_positions:
            logger.info(f"Signal {direction} ignored — max positions ({self.cfg.max_positions}) reached")
            return

        await self._execute(direction, best_signals)

    # ── Execution ─────────────────────────────────────────────────────

    async def _execute(self, direction: Direction, signals: List[Signal]) -> None:
        try:
            account = await self.rest.get_account(self.cfg.margin_coin)
            balance = account_dict(account)
            if not balance:
                return
            available = float(balance["available"])
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
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
            base_prec  = int(pair_data.get("basePrecision", 3))
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

        # SL / TP from best signal or ATR-based fallback
        best = signals[0]
        sl = best.sl_price
        tp = best.tp_price
        if not sl:
            df = self._to_dataframe()
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
            f"Symbol: `{self.cfg.symbol}`\n"
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

    async def _log_paper_trade(
        self, side: str, qty: str, price: float, sl: float, tp: float, reason: str
    ) -> None:
        await _db_log_paper_trade(self.cfg.symbol, side, qty, price, reason)

    # ── Position / order event handlers ───────────────────────────────

    def _on_position_update(self, msg: Dict[str, Any]) -> None:
        data = msg.get("data", {})
        if not data:
            return
        pos_id = data.get("positionId")
        qty    = float(data.get("qty", 0))
        if qty == 0 and pos_id in self._open_positions:
            pos = self._open_positions.pop(pos_id)
            pnl = data.get("realizedPNL", "?")
            self._notify(f"🔒 Position closed: `{pos_id}`\nSide: `{pos.side}`\nPNL: `{pnl}`")

    def _on_order_update(self, msg: Dict[str, Any]) -> None:
        data = msg.get("data", {})
        status = data.get("status", "")
        if status == "FILLED":
            self._notify(f"✅ Order filled: `{data.get('orderId', '')}`")
        elif status in ("CANCELLED", "REJECTED"):
            self._notify(f"⚠️ Order {status}: `{data.get('orderId', '')}`")

    # ── Helpers ───────────────────────────────────────────────────────

    def _to_dataframe(self) -> Optional[pd.DataFrame]:
        if not self._klines:
            return None
        df = pd.DataFrame(self._klines)
        for col in ["open", "high", "low", "close", "baseVol", "quoteVol"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

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
