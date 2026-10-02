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


class BitunixWSClient:
    """
    Manages one public and one private WebSocket connection.

    Public  channels: kline, depth, ticker, tickers, markPrice, trade
    Private channels: balance, order, position, tpsl

    Subscription message format:
        {"op": "subscribe", "args": [{"ch": "<channel>", "symbol": "<BTCUSDT>"}]}
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

        self._pub_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._priv_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False

    # ── Public API ────────────────────────────────────────────────────

    def on(self, channel: str, callback: Callback) -> None:
        """Register a callback for a channel key (e.g. 'kline:BTCUSDT:1m')."""
        self._callbacks.setdefault(channel, []).append(callback)

    def subscribe_kline(self, symbol: str, interval: str) -> None:
        """Public kline channel — interval e.g. '1m', '5m', '1h'."""
        self._public_subscriptions.append({"ch": "kline", "symbol": symbol, "interval": interval})

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
        # Wait for login response
        resp = await asyncio.wait_for(ws.recv(), timeout=10)
        data = json.loads(resp)
        if data.get("op") != "login":
            logger.warning(f"Unexpected WS login response: {data}")

    async def _send_subscribe(self, ws: websockets.WebSocketClientProtocol, subs: List[Dict]) -> None:
        if subs:
            await ws.send(json.dumps({"op": "subscribe", "args": subs}))

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
        # Build lookup keys from most-specific to least
        keys = [f"{ch}:{symbol}", ch, ""]
        for key in keys:
            for cb in self._callbacks.get(key, []):
                try:
                    cb(msg)
                except Exception as e:
                    logger.error(f"WS callback error on {key}: {e}")
