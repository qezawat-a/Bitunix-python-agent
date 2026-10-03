"""Bitunix API authentication — REST and WebSocket request signing.

Two DIFFERENT schemes. Do not reuse one for the other.

REST  (https://www.bitunix.com/api-docs/futures/common/sign.html)
-------------------------------------------------------------
    All query params are sorted ascending by ASCII key and concatenated
    with no separator, and the JSON body is compacted (all whitespace
    removed). The signed string must be byte-identical to what is sent.

        queryParams = "id1uid200"          # sorted, concatenated
        body        = '{"uid":"2899"}'     # spaces removed
        digest      = SHA256(nonce + timestamp + apiKey + queryParams + body)
        sign        = SHA256(digest + secretKey)

    `timestamp` is Unix time in MILLISECONDS (string) for REST, versus
    seconds for the WebSocket login.

WebSocket login (websocket/prepare/WebSocket.html)
--------------------------------------------------
        digest = SHA256(nonce + str(timestamp) + apiKey)
        sign   = SHA256(digest + secretKey)

    `timestamp` is Unix time in SECONDS (int). No body or query is hashed.

Headers required on every REST call: api-key, nonce, timestamp, sign.
`language` is optional (the docs show en-US).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import urlencode

__all__ = ["make_headers", "make_ws_login_args", "sign_rest", "canonical_query",
           "canonical_body", "wire_params"]


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def wire_params(params: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Coerce query params to the exact strings httpx puts on the wire.

    The signature covers the query, so the signed string has to be byte-equal
    to what is transmitted. Python's str(True) is "True", while httpx
    serialises a bool as "true" — signing the dict and sending it unchanged
    therefore produced two different strings and every request carrying a bool
    (e.g. includeSubAccounts) failed with error 10007. Normalising once and
    passing the SAME dict to httpx closes the gap.

    None values are dropped, matching httpx, which omits them entirely.
    """
    if not params:
        return {}
    out: Dict[str, str] = {}
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out[str(k)] = "true" if v else "false"
        elif isinstance(v, str):
            out[str(k)] = v
        else:
            out[str(k)] = str(v)
    return out


def canonical_query(params: Optional[Dict[str, Any]]) -> str:
    """
    Build the signed query string: params sorted ascending by key, values
    concatenated with no separator, no URL-encoding.

        {"symbol": "BTCUSDT", "limit": 5}
        -> "limit5symbolBTCUSDT"

    Values are stringified by wire_params(), so a bool is hashed as "true" and
    None is skipped. Sort is on the raw string key (ASCII order).
    """
    if not params:
        return ""
    wire = wire_params(params)
    return "".join(f"{k}{wire[k]}" for k in sorted(wire, key=str))


def canonical_body(body: Optional[Any]) -> str:
    """
    Compact JSON for signing: no spaces, keys in insertion order.

    Must match the bytes actually sent on the wire, so the REST client
    serialises the body exactly once and reuses that string.
    """
    if body is None:
        return ""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def sign_rest(
    api_key: str,
    secret_key: str,
    nonce: str,
    timestamp_ms: str,
    query_params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
) -> str:
    """
    Compute the REST signature.

    query_params: the request's query dict (signed, not sent as such)
    body:         the request's JSON body dict, or None for GET
    """
    qp = canonical_query(query_params)
    bd = canonical_body(body)
    digest = _sha256(f"{nonce}{timestamp_ms}{api_key}{qp}{bd}")
    return _sha256(digest + secret_key)


def make_headers(
    api_key: str,
    secret_key: str,
    language: str = "en-US",
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    nonce: Optional[str] = None,
    timestamp_ms: Optional[str] = None,
) -> Dict[str, str]:
    """
    Build signed REST headers.

    `params` and `body` MUST be the exact values being sent — the signature
    covers them, so any serialisation difference between what is signed and
    what is transmitted produces error 10007 (sign signature error).
    """
    nonce = nonce or uuid.uuid4().hex
    ts = timestamp_ms or str(int(time.time() * 1000))
    return {
        "api-key": api_key,
        "sign": sign_rest(api_key, secret_key, nonce, ts, params, body),
        "nonce": nonce,
        "timestamp": ts,
        "language": language,
        "Content-Type": "application/json",
    }


def make_ws_login_args(api_key: str, secret_key: str) -> Dict[str, Any]:
    """
    Auth args for the WebSocket login frame.

    NOTE this is NOT the REST scheme: it hashes only
    nonce + timestamp + apiKey, and the timestamp is in seconds.
    """
    nonce = uuid.uuid4().hex
    timestamp = int(time.time())
    digest = _sha256(f"{nonce}{timestamp}{api_key}")
    return {
        "apiKey": api_key,
        "timestamp": timestamp,
        "sign": _sha256(digest + secret_key),
        "nonce": nonce,
    }
