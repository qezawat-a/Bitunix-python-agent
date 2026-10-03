"""Bitunix WebSocket client — public and private streams."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed
from loguru import logger

from config import Config
from trader.api.auth import make_ws_login_args


Callback = Callable[[Dict[str, Any]], None]


# Kline intervals are encoded in the CHANNEL NAME, not a separate argument —
# there is no `interval` field on the subscribe args (see websocket/public/kline
# channel). `{price_type}_kline_{interval}`, e.g. market_kline_15min.
KLINE_INTERVALS = {
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
    "30m": "30min", "1h": "60min", "2h": "2h", "4h": "4h",
    "6h": "6h", "8h": "8h", "12h": "12h",
    "1d": "1day", "1w": "1week", "1M": "1month",
}
# REST kline uses "1M" for the monthly interval; the channel uses "1month".
_CHANNEL_TO_REST = {v: k for k, v in KLINE_INTERVALS.items()}


def kline_channel(interval: str, price_type: str = "market") -> str:
    """Build the kline channel name for a REST-style interval like `15m`."""
    unit = KLINE_INTERVALS.get(interval, interval)
    return f"{price_type}_kline_{unit}"


def interval_from_channel(ch: str) -> str | None:
    """Reverse of kline_channel: `market_kline_15min` -> `15m`, else None."""
    if not ch or "_kline_" not in ch:
        return None
    suffix = ch.rsplit("_kline_", 1)[1]
    return _CHANNEL_TO_REST.get(suffix, suffix)


def channel_family(ch: str) -> str:
    """Collapse a concrete channel name to the family key callbacks register on.

    Bitunix encodes the symbol and the kline interval into the channel name
    itself (`market_kline_15min`, `market_ticker_BTCUSDT`), while callers
    register a bare family key such as "kline". Dispatch used to compare the
    raw string, so the engine's kline handler was registered under "kline"
    while every push arrived as "market_kline_15min": the two never matched,
    the candle buffer froze at its backfill, and the bot traded on data that
    could be hours stale. Falling back to the family makes the handler match
    whatever exact channel string the gateway uses.
    """
    if not ch:
        return ""
    name = ch
    for prefix in ("market_", "mark_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    if "_kline_" in name:
        name = name.split("_kline_", 1)[0]
    # A trailing _SYMBOL segment is not part of the family.
    return name.split("_", 1)[0] or name


class BitunixWSClient:
    """
    Manages one public and one private WebSocket connection.

    Public  channels: trade, ticker, depth_book1, {market|mark}_kline_{interval}
    Private channels: balance, order, position, tpsl

    Subscription message format:
        {"op": "subscribe", "args": [{"ch": "<channel>", "symbol": "<BTCUSDT>"}]}

    Klines are the exception: the interval is part of the channel name and no
    `interval` argument exists. Switching intervals requires unsubscribing the
    old channel first — see kline_channel().
    """

    PING_INTERVAL = 3   # seconds between pings
    RECONNECT_DELAY = 5  # seconds before reconnect attempt

    def __init__(self):
        self._public_url = Config.BITUNIX_WS_PUBLIC
        self._private_url = Config.BITUNIX_WS_PRIVATE
        self._api_key = Config.BITUNIX_API_KEY
        self._secret_key = Config.BITUNIX_SECRET_KEY

        # channel → list of callbacks
        self._callbacks: Dict[str, List[Callback]] = {}
        self._public_subscriptions: List[Dict[str, str]] = []
        self._private_subscriptions: List[Dict[str, str]] = []
        self._pending_unsubscribes: List[Dict[str, str]] = []

        self._pub_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._priv_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False

    # ── Public API ────────────────────────────────────────────────────

    def on(self, channel: str, callback: Callback) -> None:
        """Register a callback for a channel key (e.g. 'kline:BTCUSDT:1m')."""
        self._callbacks.setdefault(channel, []).append(callback)

    def subscribe_kline(self, symbol: str, interval: str) -> None:
        """Public kline channel. The interval lives in the channel name."""
        self._public_subscriptions.append(
            {"ch": kline_channel(interval), "symbol": symbol}
        )

    def unsubscribe_kline(self, symbol: str, interval: str) -> None:
        """Drop a kline subscription. Required before re-subscribing the same
        symbol at a different interval — Bitunix keeps both otherwise."""
        sub = {"ch": kline_channel(interval), "symbol": symbol}
        self._public_subscriptions = [s for s in self._public_subscriptions if s != sub]
        self._pending_unsubscribes.append(sub)

    def set_kline_subscriptions(self, symbol: str, intervals: List[str]) -> None:
        """Replace this symbol's kline channels with exactly `intervals`.

        The caller should `await apply_subscriptions()` on a live connection so
        the removals actually reach the gateway.
        """
        keep = [s for s in self._public_subscriptions
                if interval_from_channel(s.get("ch", "")) is None]
        dropped = [s for s in self._public_subscriptions
                   if s not in keep and s.get("symbol") == symbol]
        wanted = [{"ch": kline_channel(tf), "symbol": symbol} for tf in intervals]
        self._public_subscriptions = keep + wanted
        self._pending_unsubscribes.extend(dropped)

    async def apply_subscriptions(self) -> None:
        """Flush pending unsubscribe messages to the live public socket."""
        if not self._pending_unsubscribes:
            return
        subs, self._pending_unsubscribes = self._pending_unsubscribes, []
        if self._pub_ws:
            await self._send_subscribe(self._pub_ws, subs, op="unsubscribe")

    def subscribe_depth(self, symbol: str) -> None:
        self._public_subscriptions.append({"ch": "depth_book1", "symbol": symbol})

    def subscribe_ticker(self, symbol: str) -> None:
        self._public_subscriptions.append({"ch": "ticker", "symbol": symbol})

    def subscribe_tickers(self) -> None:
        """All tickers (no symbol filter)."""
        self._public_subscriptions.append({"ch": "ticker"})

    def subscribe_mark_price(self, symbol: str) -> None:
        self._public_subscriptions.append({"ch": "ticker", "symbol": symbol})

    def subscribe_trades(self, symbol: str) -> None:
        self._public_subscriptions.append({"ch": "trade", "symbol": symbol})

    def subscribe_balance(self) -> None:
        self._private_subscriptions.append({"ch": "balance"})

    def subscribe_orders(self, symbol: Optional[str] = None) -> None:
        sub: Dict[str, str] = {"ch": "order"}
        if symbol:
            sub["symbol"] = symbol
        self._private_subscriptions.append(sub)

    def subscribe_positions(self, symbol: Optional[str] = None) -> None:
        sub: Dict[str, str] = {"ch": "position"}
        if symbol:
            sub["symbol"] = symbol
        self._private_subscriptions.append(sub)

    def subscribe_tpsl(self, symbol: Optional[str] = None) -> None:
        sub: Dict[str, str] = {"ch": "tpsl"}
        if symbol:
            sub["symbol"] = symbol
        self._private_subscriptions.append(sub)

    async def start(self) -> None:
        self._running = True
        tasks = []
        if self._public_subscriptions:
            tasks.append(asyncio.create_task(self._run_public()))
        if self._private_subscriptions:
            tasks.append(asyncio.create_task(self._run_private()))
        if tasks:
            await asyncio.gather(*tasks)

    async def stop(self) -> None:
        self._running = False
        for ws in (self._pub_ws, self._priv_ws):
            if ws:
                await ws.close()

    # ── Internal ──────────────────────────────────────────────────────

    async def _run_public(self) -> None:
        while self._running:
            try:
                async with websockets.connect(self._public_url) as ws:
                    self._pub_ws = ws
                    self._connected_pub = True
                    logger.info("WS public connected")
                    # Re-subscribe on every connect (handles reconnects too)
                    await self._send_subscribe(ws, self._public_subscriptions)
                    await self._message_loop(ws)
                    self._connected_pub = False
            except (ConnectionClosed, OSError) as e:
                logger.warning(f"WS public disconnected: {e}, reconnecting in {self.RECONNECT_DELAY}s")
                await asyncio.sleep(self.RECONNECT_DELAY)

    async def _run_private(self) -> None:
        while self._running:
            try:
                async with websockets.connect(self._private_url) as ws:
                    self._priv_ws = ws
                    self._connected_priv = True
                    logger.info("WS private connected")
                    await self._login(ws)
                    # Re-subscribe on every connect (handles reconnects too)
                    await self._send_subscribe(ws, self._private_subscriptions)
                    await self._message_loop(ws)
                    self._connected_priv = False
            except (ConnectionClosed, OSError) as e:
                logger.warning(f"WS private disconnected: {e}, reconnecting in {self.RECONNECT_DELAY}s")
                await asyncio.sleep(self.RECONNECT_DELAY)

    async def _login(self, ws: websockets.WebSocketClientProtocol) -> None:
        args = make_ws_login_args(self._api_key, self._secret_key)
        await ws.send(json.dumps({"op": "login", "args": [args]}))
        # Bitunix first sends {"op":"connect",...}; the login ack comes after it.
        deadline = time.time() + 10
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                logger.warning("WS login: no login response within 10s")
                return
            resp = await asyncio.wait_for(ws.recv(), timeout=remaining)
            try:
                data = json.loads(resp)
            except json.JSONDecodeError:
                continue
            op = data.get("op")
            if op == "connect":
                continue
            if op == "login":
                body = data.get("data")
                ok = body.get("result") if isinstance(body, dict) else None
                if ok is False or data.get("code") not in (None, 0, "0"):
                    logger.error(f"WS login rejected (check API key permissions): {data}")
                else:
                    logger.info("WS private login OK")
                return
            logger.debug(f"WS login: ignoring pre-login message {data}")

    async def _send_subscribe(self, ws: websockets.WebSocketClientProtocol,
                              subs: List[Dict], op: str = "subscribe") -> None:
        if subs:
            await ws.send(json.dumps({"op": op, "args": subs}))

    async def _message_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        ping_task = asyncio.create_task(self._ping_loop(ws))
        try:
            async for raw in ws:
                try:
                    msg: Dict[str, Any] = json.loads(raw)
                    await self._dispatch(msg)
                except json.JSONDecodeError:
                    pass
        finally:
            ping_task.cancel()

    async def _ping_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            await asyncio.sleep(self.PING_INTERVAL)
            try:
                await ws.send(json.dumps({"op": "ping", "ping": int(time.time())}))
            except Exception:
                break

    async def _dispatch(self, msg: Dict[str, Any]) -> None:
        ch = msg.get("ch") or msg.get("op", "")
        symbol = msg.get("symbol", "")
        # Build lookup keys from most-specific to least. The family key comes
        # before the bare catch-all so a "kline" registration still sees
        # market_kline_15min, and a "" (catch-all) registration still sees
        # everything. Dedupe so one callback cannot fire twice per message.
        keys: List[str] = []
        for key in (f"{ch}:{symbol}", ch, channel_family(ch), ""):
            if key and key not in keys:
                keys.append(key)
        for key in keys:
            for cb in self._callbacks.get(key, []):
                try:
                    cb(msg)
                except Exception as e:
                    logger.error(f"WS callback error on {key}: {e}")
