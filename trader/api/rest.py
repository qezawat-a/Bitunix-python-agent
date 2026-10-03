"""All Bitunix Futures REST endpoints.

Paths, parameter names, enums and required/optional flags here were transcribed
from the official docs at https://www.bitunix.com/api-docs/futures/.

Documented quirks that are reproduced deliberately:
  * `tp_sl` appears only in the doc URLs; every real path segment is `tpsl`.
  * get_funding_rate_history's start param is really spelled `starTime`.
  * get_leverage_margin_mode has no "and" in the path.
  * data is an ARRAY for some endpoints and an OBJECT for others — see each
    method's docstring rather than assuming a shape.
  * Trigger-type enums are documented as MARK_PRICE/LAST_PRICE but the site's
    own examples send the short forms "MARK" / "LAST".
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import Config
from trader.api.auth import canonical_body, make_headers, wire_params


_BASE = Config.BITUNIX_BASE_URL
_TIMEOUT = 15.0


def _headers(params: Dict[str, Any] | None = None,
             body: Dict[str, Any] | None = None) -> Dict[str, str]:
    """
    Sign the exact params/body that are about to be transmitted.

    REST signing hashes nonce + timestamp + apiKey + query + body, so a
    mismatch between what is signed and what is sent yields error 10007.
    """
    return make_headers(
        Config.BITUNIX_API_KEY,
        Config.BITUNIX_SECRET_KEY,
        params=params,
        body=body,
    )


def _ok(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Raise on non-zero code."""
    if resp.get("code", -1) != 0:
        raise BitunixError(resp.get("code"), resp.get("msg", "unknown error"))
    return resp


class BitunixError(Exception):
    def __init__(self, code: int | None, msg: str):
        super().__init__(f"[{code}] {msg}")
        self.code = code
        self.msg = msg


def account_dict(resp: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalise a `/futures/account` payload to a single balance object.

    `data` is an OBJECT in the current API, but a one-element ARRAY on some
    deployments. `data[0]` on the object form raises `KeyError: 0`, so unwrap
    both shapes in one place.
    """
    data = resp.get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    return data if isinstance(data, dict) else {}


class BitunixRestClient:
    def __init__(self):
        self._client = httpx.AsyncClient(base_url=_BASE, timeout=_TIMEOUT)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        reraise=True,
    )
    async def _get(self, path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        # Normalise once and use the SAME dict for both the signature and the
        # request, so the two can never disagree (a bool signs as "true" here
        # and must go out as "true", not Python's "True" -> error 10007).
        wire = wire_params(params)
        resp = await self._client.get(path, params=wire or None,
                                      headers=_headers(wire or None, None))
        resp.raise_for_status()
        return _ok(resp.json())

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        reraise=True,
    )
    async def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        # Send the exact compact bytes that auth.canonical_body signs. Do NOT
        # use httpx's json= here: it re-serialises with default json.dumps
        # separators (", ", ": " → spaces), which no longer match the signed
        # string and would make every signed POST fail with error 10007.
        content = canonical_body(body).encode("utf-8")
        resp = await self._client.post(path, content=content, headers=_headers(None, body))
        resp.raise_for_status()
        return _ok(resp.json())

    # ─────────────────────────────────────────────────────────────────
    # MARKET DATA  (public, but still use auth headers for consistency)
    # ─────────────────────────────────────────────────────────────────

    async def get_tickers(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        GET /api/v1/futures/market/tickers

        Official demo uses `symbols` (plural, comma-separated).
        e.g. get_tickers("BTCUSDT,ETHUSDT") or get_tickers() for all.
        """
        params: Dict[str, Any] = {}
        if symbol:
            params["symbols"] = symbol   # official param is plural
        return await self._get("/api/v1/futures/market/tickers", params or None)

    async def get_depth(self, symbol: str, limit: str = "20") -> Dict[str, Any]:
        """
        GET /api/v1/futures/market/depth
        limit: '1' | '5' | '15' | '50' | 'max'
        """
        return await self._get("/api/v1/futures/market/depth", {"symbol": symbol, "limit": limit})

    async def get_kline(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200,
        price_type: str = "LAST_PRICE",
    ) -> Dict[str, Any]:
        """
        GET /api/v1/futures/market/kline
        interval: 1m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M
        type: LAST_PRICE | MARK_PRICE
        limit max: 200
        """
        params: Dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 200),
            "type": price_type,
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._get("/api/v1/futures/market/kline", params)

    async def get_trading_pairs(self, symbols: Optional[str] = None) -> Dict[str, Any]:
        """
        GET /api/v1/futures/market/trading_pairs
        symbols: comma-separated, e.g. 'BTCUSDT,ETHUSDT' — omit for all
        """
        params: Dict[str, Any] = {}
        if symbols:
            params["symbols"] = symbols
        return await self._get("/api/v1/futures/market/trading_pairs", params or None)

    async def get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """
        GET /api/v1/futures/market/funding_rate — current rate for one symbol.

        `symbol` is required. NOTE the response is a one-element ARRAY, not an
        object, even for a single symbol.

        data[]: symbol, markPrice, lastPrice, indexPrice, fundingRate,
                nextFundingTime, fundingInterval (hours), maxFundingRate,
                minFundingRate
        Several numerics come back as strings (e.g. nextFundingTime) — parse
        defensively.
        """
        return await self._get("/api/v1/futures/market/funding_rate", {"symbol": symbol})

    async def get_funding_rate_batch(self) -> Dict[str, Any]:
        """
        GET /api/v1/futures/market/funding_rate/batch — current rate for ALL symbols.

        Takes NO request parameters (verified against the docs: the page has no
        request-parameter section and the example curl passes no query string).
        Response `data` has the same 9 fields as get_funding_rate().
        """
        return await self._get("/api/v1/futures/market/funding_rate/batch")

    async def get_funding_rate_history(
        self,
        symbol: str,
        start_time: Optional[int] = None,  # ⚠ note: docs spell it 'starTime'
        end_time: Optional[int] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        GET /api/v1/futures/market/get_funding_rate_history
        NOTE: docs typo — request param is 'starTime' (one 't'), preserved here.
        limit max: 200
        """
        params: Dict[str, Any] = {"symbol": symbol, "limit": min(limit, 200)}
        if start_time:
            params["starTime"] = start_time   # intentional typo matching docs
        if end_time:
            params["endTime"] = end_time
        return await self._get("/api/v1/futures/market/get_funding_rate_history", params)

    # ─────────────────────────────────────────────────────────────────
    # ACCOUNT
    # ─────────────────────────────────────────────────────────────────

    async def get_account(self, margin_coin: str = "USDT") -> Dict[str, Any]:
        """
        GET /api/v1/futures/account — single account balance.

        data (object): marginCoin, available, frozen, margin, transfer,
        positionMode, crossUnrealizedPNL, isolationUnrealizedPNL, bonus.
        Use `account_dict()` to unwrap it safely.
        """
        return await self._get("/api/v1/futures/account", {"marginCoin": margin_coin})

    async def get_leverage_and_margin_mode(self, symbol: str, margin_coin: str = "USDT") -> Dict[str, Any]:
        """
        GET /api/v1/futures/account/get_leverage_margin_mode

        NOTE the path segment is `get_leverage_margin_mode` (no "and") even
        though the doc page is titled "Get Leverage and Margin Mode".

        data (object): symbol, marginCoin, leverage (int), marginMode
        """
        return await self._get(
            "/api/v1/futures/account/get_leverage_margin_mode",
            {"symbol": symbol, "marginCoin": margin_coin},
        )

    async def change_leverage(self, symbol: str, leverage: int, margin_coin: str = "USDT") -> Dict[str, Any]:
        """POST /api/v1/futures/account/change_leverage"""
        return await self._post(
            "/api/v1/futures/account/change_leverage",
            {"symbol": symbol, "leverage": leverage, "marginCoin": margin_coin},
        )

    async def change_margin_mode(self, symbol: str, margin_mode: str, margin_coin: str = "USDT") -> Dict[str, Any]:
        """
        POST /api/v1/futures/account/change_margin_mode
        marginMode: ISOLATION | CROSS
        """
        return await self._post(
            "/api/v1/futures/account/change_margin_mode",
            {"symbol": symbol, "marginMode": margin_mode, "marginCoin": margin_coin},
        )

    async def change_position_mode(self, position_mode: str) -> Dict[str, Any]:
        """
        POST /api/v1/futures/account/change_position_mode
        positionMode: ONE_WAY | HEDGE
        """
        return await self._post(
            "/api/v1/futures/account/change_position_mode",
            {"positionMode": position_mode},
        )

    async def adjust_position_margin(
        self,
        symbol: str,
        amount: str,
        margin_coin: str = "USDT",
        side: Optional[str] = None,
        position_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /api/v1/futures/account/adjust_position_margin
        amount: positive = add, negative = remove
        Supply at least one of side (LONG|SHORT) or positionId.
        """
        body: Dict[str, Any] = {"symbol": symbol, "amount": amount, "marginCoin": margin_coin}
        if side:
            body["side"] = side
        if position_id:
            body["positionId"] = position_id
        return await self._post("/api/v1/futures/account/adjust_position_margin", body)

    # ─────────────────────────────────────────────────────────────────
    # POSITIONS
    # ─────────────────────────────────────────────────────────────────

    async def get_pending_positions(
        self,
        symbol: Optional[str] = None,
        position_id: Optional[str] = None,
        sub_account_id: Optional[int] = None,
        include_sub_accounts: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """GET /api/v1/futures/position/get_pending_positions"""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        if position_id:
            params["positionId"] = position_id
        if sub_account_id is not None:
            params["subAccountId"] = sub_account_id
        if include_sub_accounts is not None:
            params["includeSubAccounts"] = include_sub_accounts
        return await self._get("/api/v1/futures/position/get_pending_positions", params or None)

    async def get_history_positions(
        self,
        symbol: Optional[str] = None,
        position_id: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        skip: int = 0,
        limit: int = 10,
        sub_account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """GET /api/v1/futures/position/get_history_positions (limit max 100)"""
        params: Dict[str, Any] = {"skip": skip, "limit": min(limit, 100)}
        if symbol:
            params["symbol"] = symbol
        if position_id:
            params["positionId"] = position_id
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        if sub_account_id is not None:
            params["subAccountId"] = sub_account_id
        return await self._get("/api/v1/futures/position/get_history_positions", params)

    async def get_position_tiers(self, symbol: str) -> Dict[str, Any]:
        """GET /api/v1/futures/position/get_position_tiers — risk limit tiers."""
        return await self._get("/api/v1/futures/position/get_position_tiers", {"symbol": symbol})

    # ─────────────────────────────────────────────────────────────────
    # ORDERS  ⚠ place_order parameters inferred from batch_order schema
    # ─────────────────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        qty: str,
        price: Optional[str] = None,
        trade_side: Optional[str] = None,  # OPEN | CLOSE  (hedge mode)
        position_id: Optional[str] = None,
        effect: Optional[str] = None,      # GTC | IOC | FOK | POST_ONLY
        client_id: Optional[str] = None,
        reduce_only: Optional[bool] = None,
        tp_price: Optional[str] = None,
        tp_stop_type: Optional[str] = None,
        tp_order_type: Optional[str] = None,
        tp_order_price: Optional[str] = None,
        sl_price: Optional[str] = None,
        sl_stop_type: Optional[str] = None,
        sl_order_type: Optional[str] = None,
        sl_order_price: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /api/v1/futures/trade/place_order  ⚠ INFERRED from batch_order schema
        side: BUY | SELL
        orderType: LIMIT | MARKET
        effect (TIF): GTC | IOC | FOK | POST_ONLY  (required for LIMIT)
        tradeSide: OPEN | CLOSE  (hedge mode only)
        Verify params at: https://www.bitunix.com/api-docs/futures/trade/place_order.html
        """
        body: Dict[str, Any] = {"symbol": symbol, "side": side, "orderType": order_type, "qty": qty}
        if price:
            body["price"] = price
        if trade_side:
            body["tradeSide"] = trade_side
        if position_id:
            body["positionId"] = position_id
        if effect:
            body["effect"] = effect
        if client_id:
            body["clientId"] = client_id
        if reduce_only is not None:
            body["reduceOnly"] = reduce_only
        if tp_price:
            body["tpPrice"] = tp_price
        if tp_stop_type:
            body["tpStopType"] = tp_stop_type
        if tp_order_type:
            body["tpOrderType"] = tp_order_type
        if tp_order_price:
            body["tpOrderPrice"] = tp_order_price
        if sl_price:
            body["slPrice"] = sl_price
        if sl_stop_type:
            body["slStopType"] = sl_stop_type
        if sl_order_type:
            body["slOrderType"] = sl_order_type
        if sl_order_price:
            body["slOrderPrice"] = sl_order_price
        return await self._post("/api/v1/futures/trade/place_order", body)

    async def batch_order(self, symbol: str, order_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        POST /api/v1/futures/trade/batch_order — max 5 orders, rate limit 1 req/s
        Each item in order_list shares the same schema as place_order fields.
        """
        if len(order_list) > 5:
            raise ValueError("Bitunix batch_order limit is 5 orders per call")
        return await self._post("/api/v1/futures/trade/batch_order", {"symbol": symbol, "orderList": order_list})

    async def cancel_orders(self, symbol: str, order_list: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        POST /api/v1/futures/trade/cancel_orders

        `orderList` items are {"orderId": ...} or {"clientId": ...}; supply at
        least one per item, and orderId wins if both are present.

        CAUTION (verbatim from the docs): "Successful interface response is not
        necessarily equal to the success of the operation, please use the
        websocket push message as an accurate judgment of the success of the
        operation."
        """
        return await self._post(
            "/api/v1/futures/trade/cancel_orders",
            {"symbol": symbol, "orderList": order_list},
        )

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """POST /api/v1/futures/trade/cancel_all_orders"""
        body: Dict[str, Any] = {}
        if symbol:
            body["symbol"] = symbol
        return await self._post("/api/v1/futures/trade/cancel_all_orders", body)

    async def close_all_position(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """POST /api/v1/futures/trade/close_all_position"""
        body: Dict[str, Any] = {}
        if symbol:
            body["symbol"] = symbol
        return await self._post("/api/v1/futures/trade/close_all_position", body)

    async def flash_close_position(self, position_id: str) -> Dict[str, Any]:
        """POST /api/v1/futures/trade/flash_close_position — market-price close."""
        return await self._post("/api/v1/futures/trade/flash_close_position", {"positionId": position_id})

    async def modify_order(
        self,
        qty: str,
        price: str,
        order_id: Optional[str] = None,
        client_id: Optional[str] = None,
        tp_price: Optional[str] = None,
        tp_stop_type: Optional[str] = None,
        tp_order_type: Optional[str] = None,
        tp_order_price: Optional[str] = None,
        sl_price: Optional[str] = None,
        sl_stop_type: Optional[str] = None,
        sl_order_type: Optional[str] = None,
        sl_order_price: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /api/v1/futures/trade/modify_order
        orderId OR clientId required; orderId takes precedence.
        NOTE: docs show symbol in example but not in parameter table — included anyway.
        """
        if not order_id and not client_id:
            raise ValueError("order_id or client_id required")
        body: Dict[str, Any] = {"qty": qty, "price": price}
        if order_id:
            body["orderId"] = order_id
        if client_id:
            body["clientId"] = client_id
        if tp_price:
            body["tpPrice"] = tp_price
        if tp_stop_type:
            body["tpStopType"] = tp_stop_type
        if tp_order_type:
            body["tpOrderType"] = tp_order_type
        if tp_order_price:
            body["tpOrderPrice"] = tp_order_price
        if sl_price:
            body["slPrice"] = sl_price
        if sl_stop_type:
            body["slStopType"] = sl_stop_type
        if sl_order_type:
            body["slOrderType"] = sl_order_type
        if sl_order_price:
            body["slOrderPrice"] = sl_order_price
        return await self._post("/api/v1/futures/trade/modify_order", body)

    async def get_pending_orders(
        self,
        symbol: Optional[str] = None,
        order_id: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /api/v1/futures/trade/get_pending_orders"""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        if order_id:
            params["orderId"] = order_id
        if client_id:
            params["clientId"] = client_id
        return await self._get("/api/v1/futures/trade/get_pending_orders", params or None)

    async def get_order_detail(self, order_id: Optional[str] = None, client_id: Optional[str] = None) -> Dict[str, Any]:
        """GET /api/v1/futures/trade/get_order_detail"""
        if not order_id and not client_id:
            raise ValueError("order_id or client_id required")
        params: Dict[str, Any] = {}
        if order_id:
            params["orderId"] = order_id
        if client_id:
            params["clientId"] = client_id
        return await self._get("/api/v1/futures/trade/get_order_detail", params)

    async def get_history_orders(
        self,
        symbol: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """GET /api/v1/futures/trade/get_history_orders"""
        params: Dict[str, Any] = {"skip": skip, "limit": min(limit, 100)}
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._get("/api/v1/futures/trade/get_history_orders", params)

    async def get_history_trades(
        self,
        symbol: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """GET /api/v1/futures/trade/get_history_trades"""
        params: Dict[str, Any] = {"skip": skip, "limit": min(limit, 100)}
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._get("/api/v1/futures/trade/get_history_trades", params)

    # ─────────────────────────────────────────────────────────────────
    # TP / SL
    # ─────────────────────────────────────────────────────────────────

    async def place_tpsl_order(
        self,
        symbol: str,
        position_id: str,
        tp_price: Optional[str] = None,
        tp_stop_type: Optional[str] = None,   # LAST_PRICE | MARK_PRICE
        tp_order_type: Optional[str] = None,   # LIMIT | MARKET
        tp_order_price: Optional[str] = None,
        tp_qty: Optional[str] = None,
        sl_price: Optional[str] = None,
        sl_stop_type: Optional[str] = None,
        sl_order_type: Optional[str] = None,
        sl_order_price: Optional[str] = None,
        sl_qty: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /api/v1/futures/tpsl/place_order
        At least one of tpPrice or slPrice required.
        At least one of tpQty or slQty required.
        """
        body: Dict[str, Any] = {"symbol": symbol, "positionId": position_id}
        if tp_price:
            body["tpPrice"] = tp_price
        if tp_stop_type:
            body["tpStopType"] = tp_stop_type
        if tp_order_type:
            body["tpOrderType"] = tp_order_type
        if tp_order_price:
            body["tpOrderPrice"] = tp_order_price
        if tp_qty:
            body["tpQty"] = tp_qty
        if sl_price:
            body["slPrice"] = sl_price
        if sl_stop_type:
            body["slStopType"] = sl_stop_type
        if sl_order_type:
            body["slOrderType"] = sl_order_type
        if sl_order_price:
            body["slOrderPrice"] = sl_order_price
        if sl_qty:
            body["slQty"] = sl_qty
        return await self._post("/api/v1/futures/tpsl/place_order", body)

    async def place_position_tpsl(
        self,
        symbol: str,
        position_id: str,
        tp_price: Optional[str] = None,
        tp_stop_type: Optional[str] = None,
        sl_price: Optional[str] = None,
        sl_stop_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /api/v1/futures/tpsl/position/place_order — full-position TP/SL.

        This is the "Position Take-Profit & Stop-Loss" method: on trigger the
        WHOLE position closes at market using the position size at that moment,
        so it takes no quantity, order-type or order-price fields. Each position
        may have only one Position TP/SL order.

        At least one of tpPrice / slPrice is required.
        """
        body: Dict[str, Any] = {"symbol": symbol, "positionId": position_id}
        if tp_price:
            body["tpPrice"] = tp_price
        if tp_stop_type:
            body["tpStopType"] = tp_stop_type
        if sl_price:
            body["slPrice"] = sl_price
        if sl_stop_type:
            body["slStopType"] = sl_stop_type
        return await self._post("/api/v1/futures/tpsl/position/place_order", body)

    async def modify_tpsl_order(
        self,
        order_id: str,
        tp_price: Optional[str] = None,
        tp_stop_type: Optional[str] = None,
        tp_order_type: Optional[str] = None,
        tp_order_price: Optional[str] = None,
        tp_qty: Optional[str] = None,
        sl_price: Optional[str] = None,
        sl_stop_type: Optional[str] = None,
        sl_order_type: Optional[str] = None,
        sl_order_price: Optional[str] = None,
        sl_qty: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/v1/futures/tpsl/modify_order"""
        body: Dict[str, Any] = {"orderId": order_id}
        if tp_price:
            body["tpPrice"] = tp_price
        if tp_stop_type:
            body["tpStopType"] = tp_stop_type
        if tp_order_type:
            body["tpOrderType"] = tp_order_type
        if tp_order_price:
            body["tpOrderPrice"] = tp_order_price
        if tp_qty:
            body["tpQty"] = tp_qty
        if sl_price:
            body["slPrice"] = sl_price
        if sl_stop_type:
            body["slStopType"] = sl_stop_type
        if sl_order_type:
            body["slOrderType"] = sl_order_type
        if sl_order_price:
            body["slOrderPrice"] = sl_order_price
        if sl_qty:
            body["slQty"] = sl_qty
        return await self._post("/api/v1/futures/tpsl/modify_order", body)

    async def modify_position_tpsl(
        self,
        symbol: str,
        position_id: str,
        tp_price: Optional[str] = None,
        tp_stop_type: Optional[str] = None,
        sl_price: Optional[str] = None,
        sl_stop_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        POST /api/v1/futures/tpsl/position/modify_order

        Keyed by symbol + positionId (NOT orderId, unlike modify_tpsl_order).
        Like its place counterpart it takes no qty/order-type fields.

        At least one of tpPrice / slPrice is required.
        """
        body: Dict[str, Any] = {"symbol": symbol, "positionId": position_id}
        if tp_price:
            body["tpPrice"] = tp_price
        if tp_stop_type:
            body["tpStopType"] = tp_stop_type
        if sl_price:
            body["slPrice"] = sl_price
        if sl_stop_type:
            body["slStopType"] = sl_stop_type
        return await self._post("/api/v1/futures/tpsl/position/modify_order", body)

    async def cancel_tpsl_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        POST /api/v1/futures/tpsl/cancel_order — cancel ONE TP/SL order.

        Both `symbol` and `orderId` are required. This endpoint cancels a
        single order; it does not take a list.

        CAUTION (verbatim from the docs): "Successful interface response is not
        necessarily equal to the success of the operation, please use the
        websocket push message as an accurate judgment of the success of the
        operation."
        """
        return await self._post(
            "/api/v1/futures/tpsl/cancel_order",
            {"symbol": symbol, "orderId": order_id},
        )

    async def get_pending_tpsl_orders(
        self,
        symbol: Optional[str] = None,
        position_id: Optional[str] = None,
        side: Optional[int] = None,        # int32; the docs give no enum values
        position_mode: Optional[int] = None,  # int32; the docs give no enum values
        skip: int = 0,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        GET /api/v1/futures/tpsl/get_pending_orders

        All params optional. limit: default 10, max 100.
        `side` and `positionMode` are accepted as filters but the docs do not
        document their enum values, and they are absent from the response.
        """
        params: Dict[str, Any] = {"skip": skip, "limit": min(limit, 100)}
        if symbol:
            params["symbol"] = symbol
        if position_id:
            params["positionId"] = position_id
        if side is not None:
            params["side"] = side
        if position_mode is not None:
            params["positionMode"] = position_mode
        return await self._get("/api/v1/futures/tpsl/get_pending_orders", params)

    async def get_history_tpsl_orders(
        self,
        symbol: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """GET /api/v1/futures/tpsl/get_history_orders"""
        params: Dict[str, Any] = {"skip": skip, "limit": min(limit, 100)}
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._get("/api/v1/futures/tpsl/get_history_orders", params)

    # ─────────────────────────────────────────────────────────────────
    # COPY TRADING
    # ─────────────────────────────────────────────────────────────────

    async def copy_trading_asset_query(self) -> Dict[str, Any]:
        """GET /api/v1/futures/copyTrading/asset/asset_query"""
        return await self._get("/api/v1/futures/copyTrading/asset/asset_query")

    async def close(self) -> None:
        await self._client.aclose()
