"""Bridge between jrock Telegram handlers and the Bitunix trading engine."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Config
from loguru import logger

if TYPE_CHECKING:
    from trader.engine import TradingEngine


def _engine(ctx: ContextTypes.DEFAULT_TYPE):
    return ctx.bot_data.get("trader_engine")


async def handle_trader_command(subcmd: str, args: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> str:
    dispatch = {
        "status":     _status,
        "start":      _start,
        "stop":       _stop,
        "strategies": _strategies,
        "set":        _set,
        "positions":  _positions,
        "balance":    _balance,
        "orders":     _orders,
        "history":    _history,
        "close":      _close,
        "paper":      _paper,
        "leverage":   _leverage,
        "symbol":     _symbol,
        "funding":    _funding,
        "tickers":    _tickers,
    }
    handler = dispatch.get(subcmd, _unknown)
    return await handler(args, update, ctx)


# ── Sub-handlers ──────────────────────────────────────────────────────────────

async def _status(args, update, ctx) -> str:
    engine = _engine(ctx)
    if not engine:
        return "⏸ Engine not running. Use `/trader start`"
    c = engine.cfg
    return (
        f"*Trader Status*\n"
        f"Symbol: `{c.symbol}`\n"
        f"Interval: `{c.interval}`\n"
        f"Mode: `{'📄 PAPER' if c.paper else '🔴 LIVE'}`\n"
        f"Leverage: `{c.leverage}x`\n"
        f"Margin mode: `{c.margin_mode}`\n"
        f"Position mode: `{c.position_mode}`\n"
        f"Risk/trade: `{c.risk_pct}%`\n"
        f"Strategies: `{', '.join(c.active_strategies)}`\n"
        f"Consensus threshold: `{c.consensus_threshold}`\n"
        f"Open positions: `{len(engine._open_positions)}`\n"
        f"Running: `{engine._running}`"
    )


async def _start(args, update, ctx) -> str:
    if not Config.is_admin(update.effective_user.id):
        return "❌ Owner only"
    if _engine(ctx) and _engine(ctx)._running:
        return "Already running."
    from trader.engine import TradingEngine, EngineConfig

    cfg = EngineConfig(
        symbol=Config.DEFAULT_SYMBOL,
        margin_coin=Config.DEFAULT_MARGIN_COIN,
        leverage=Config.DEFAULT_LEVERAGE,
        margin_mode=Config.DEFAULT_MARGIN_MODE,
        position_mode=Config.DEFAULT_POSITION_MODE,
        risk_pct=Config.RISK_PERCENT,
        max_positions=Config.MAX_OPEN_POSITIONS,
        paper=Config.PAPER_TRADING,
    )

    # parse optional override: /trader start ETHUSDT 15m
    parts = args.split()
    if len(parts) >= 1 and parts[0]:
        cfg.symbol = parts[0].upper()
    if len(parts) >= 2 and parts[1]:
        cfg.interval = parts[1]

    # Pre-validate the live gate so the failure surfaces NOW, not as a
    # task that dies silently after we already told the user "started".
    if not cfg.paper and not Config.TRADING_ENABLED:
        return ("❌ LIVE mode requires TRADING_ENABLED=true in .env "
                "(or toggle with `/trader paper on`).")

    engine = TradingEngine(cfg)
    cid = update.effective_chat.id
    bot = ctx.bot

    async def notify(msg: str):
        try:
            await bot.send_message(cid, msg, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    engine.on_notify(lambda m: asyncio.create_task(notify(m)))
    ctx.bot_data["trader_engine"] = engine

    async def _run():
        try:
            await engine.start()   # blocks on the WS loop until stop()
        except Exception as e:
            # Engine failed to come up (bad keys, ws.connect error, ...):
            # clean the slot and tell the user instead of a silent task death.
            logger.exception("trader engine start failed")
            if ctx.bot_data.get("trader_engine") is engine:
                ctx.bot_data.pop("trader_engine", None)
            await notify(f"❌ Engine failed to start: `{type(e).__name__}: {e}`")

    asyncio.create_task(_run())
    return f"🚀 Engine starting — `{cfg.symbol}` `{cfg.interval}` {'📄 PAPER' if cfg.paper else '🔴 LIVE'}"


async def _stop(args, update, ctx) -> str:
    if not Config.is_admin(update.effective_user.id):
        return "❌ Owner only"
    engine = _engine(ctx)
    if not engine:
        return "Engine not running."
    await engine.stop()
    ctx.bot_data.pop("trader_engine", None)
    return "⏹ Engine stopped."


async def _strategies(args, update, ctx) -> str:
    from trader.strategies import ALL_STRATEGIES
    engine = _engine(ctx)
    active = engine.cfg.active_strategies if engine else []
    lines = [
        f"{'✅' if name in active else '○'} `{name}` — {cls.description}"
        for name, cls in ALL_STRATEGIES.items()
    ]
    return "*Strategies:*\n" + "\n".join(lines)


async def _set(args, update, ctx) -> str:
    parts = args.split(None, 1)
    if not parts:
        return "Usage: `/trader set strategy <names>` | `leverage <n>` | `symbol <sym>` | `interval <i>` | `risk <pct>` | `consensus <n>`"
    key = parts[0].lower()
    val = parts[1].strip() if len(parts) > 1 else ""
    engine = _engine(ctx)

    if key in ("strategy", "strategies") and val:
        names = [n.strip().upper() for n in val.replace(",", " ").split() if n.strip()]
        if engine:
            engine.set_strategies(names)
        return f"✅ Strategies: `{', '.join(names)}`"
    if key == "leverage" and val.isdigit():
        lev = int(val)
        if engine:
            engine.cfg.leverage = lev
        return f"✅ Leverage: `{lev}x`"
    if key == "symbol" and val:
        if engine:
            engine.cfg.symbol = val.upper()
        return f"✅ Symbol: `{val.upper()}`"
    if key == "interval" and val:
        if engine:
            engine.cfg.interval = val
        return f"✅ Interval: `{val}`"
    if key == "risk":
        try:
            pct = float(val)
            if engine:
                engine.cfg.risk_pct = pct
            return f"✅ Risk per trade: `{pct}%`"
        except ValueError:
            return f"❌ Invalid risk value: `{val}`"
    if key == "consensus" and val.isdigit():
        n = int(val)
        if engine:
            engine.cfg.consensus_threshold = n
        return f"✅ Consensus threshold: `{n}` strategies must agree"
    return f"Unknown key `{key}`"


async def _positions(args, update, ctx) -> str:
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_pending_positions()
        positions = resp.get("data", [])
        if not positions:
            return "_No open positions._"
        lines = []
        for p in positions:
            lines.append(
                f"• `{p['symbol']}` {p['side']} qty=`{p['qty']}`\n"
                f"  Entry=`{p.get('avgOpenPrice','?')}` Liq=`{p.get('liqPrice','?')}`"
                f" uPNL=`{p.get('unrealizedPNL','?')}`"
            )
        return "*Open Positions:*\n" + "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _balance(args, update, ctx) -> str:
    from trader.api.rest import BitunixError, BitunixRestClient, account_dict
    client = BitunixRestClient()
    try:
        resp = await client.get_account(Config.DEFAULT_MARGIN_COIN)
        data = account_dict(resp)
        if not data:
            return "No account data."
        return (
            f"*Balance ({data.get('marginCoin','?')})*\n"
            f"Available: `{data.get('available','0')}`\n"
            f"Frozen: `{data.get('frozen','0')}`\n"
            f"Margin: `{data.get('margin','0')}`\n"
            f"Transferable: `{data.get('transfer','0')}`\n"
            f"Cross uPNL: `{data.get('crossUnrealizedPNL','0')}`\n"
            f"Isolated uPNL: `{data.get('isolationUnrealizedPNL','0')}`\n"
            f"Bonus: `{data.get('bonus','0')}`\n"
            f"Position mode: `{data.get('positionMode','?')}`"
        )
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _orders(args, update, ctx) -> str:
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_pending_orders()
        # `data` is an OBJECT: {total, orderList} — not an array.
        orders = resp.get("data", {}).get("orderList", [])
        if not orders:
            return "_No pending orders._"
        lines = [
            f"• `{o.get('orderId','')}` {o.get('symbol','')} {o.get('side','')} "
            f"qty=`{o.get('qty','')}` price=`{o.get('price','mkt')}`"
            for o in orders[:10]
        ]
        return "*Pending Orders:*\n" + "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _history(args, update, ctx) -> str:
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_history_positions(limit=10)
        positions = resp.get("data", {}).get("positionList", [])
        if not positions:
            return "_No position history._"
        lines = [
            f"• `{p['symbol']}` {p['side']} pnl=`{p['realizedPNL']}`"
            f" entry=`{p.get('entryPrice','?')}` close=`{p.get('closePrice','?')}`"
            for p in positions
        ]
        return "*Position History:*\n" + "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _close(args, update, ctx) -> str:
    if not Config.is_admin(update.effective_user.id):
        return "❌ Owner only"
    parts = args.split()
    if Config.PAPER_TRADING:
        return "_Paper mode — no real positions to close._"
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        if parts and parts[0].lower() == "all":
            await client.close_all_position()
            return "✅ All positions closed."
        elif parts:
            await client.flash_close_position(parts[0])
            return f"✅ Position `{parts[0]}` closed."
        else:
            return "Usage: `/trader close all` or `/trader close <positionId>`"
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _paper(args, update, ctx) -> str:
    if not Config.is_admin(update.effective_user.id):
        return "❌ Owner only"
    val = args.strip().lower()
    if val in ("on", "off"):
        enabled = val == "on"
        Config.PAPER_TRADING = enabled
        engine = _engine(ctx)
        if engine:
            engine.cfg.paper = enabled
        return f"Paper trading: `{val}`"
    return f"Paper trading: `{'on' if Config.PAPER_TRADING else 'off'}`\nToggle: `/trader paper on|off`"


async def _leverage(args, update, ctx) -> str:
    parts = args.split()
    if not parts or not parts[0].isdigit():
        return "Usage: `/trader leverage <n> [symbol]`"
    lev = int(parts[0])
    sym = parts[1].upper() if len(parts) > 1 else Config.DEFAULT_SYMBOL
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        await client.change_leverage(sym, lev, Config.DEFAULT_MARGIN_COIN)
        engine = _engine(ctx)
        if engine:
            engine.cfg.leverage = lev
        return f"✅ Leverage `{lev}x` for `{sym}`"
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _symbol(args, update, ctx) -> str:
    sym = args.strip().upper()
    if not sym:
        return "Usage: `/trader symbol <BTCUSDT>`"
    engine = _engine(ctx)
    if engine:
        engine.cfg.symbol = sym
    return f"✅ Symbol: `{sym}`"


async def _funding(args, update, ctx) -> str:
    sym = args.strip().upper() or Config.DEFAULT_SYMBOL
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_funding_rate_history(sym, limit=5)
        rates = resp.get("data", [])
        if not rates:
            return f"No funding rate history for `{sym}`"
        lines = [
            f"• `{r['fundingRate']}` at `{r['fundingTime']}`  mark=`{r.get('markPrice','?')}`"
            for r in rates
        ]
        return f"*Funding Rate History ({sym}):*\n" + "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _tickers(args, update, ctx) -> str:
    sym = args.strip().upper() or None
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_tickers(sym)
        data = resp.get("data", [])
        if not data:
            return "_No ticker data._"
        if isinstance(data, dict):
            data = [data]
        if sym:
            # Bitunix ignores the `symbol` query param and always returns the
            # full list (794 rows), so filter locally or we print 5 random pairs.
            matches = [t for t in data if str(t.get("symbol", "")).upper() == sym]
            if matches:
                data = matches
            else:
                return f"No ticker for `{sym}`"
        lines = []
        for t in data[:5]:
            # Real ticker rows carry open/baseVol/quoteVol — there is no
            # `priceChangePercent` or `volume` field, so derive the change.
            try:
                pct = (float(t["lastPrice"]) / float(t["open"]) - 1) * 100
                pct_s = f"{pct:+.2f}%"
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                pct_s = "?"
            hi = t.get("high", "?")
            lo = t.get("low", "?")
            vol = t.get("baseVol", t.get("quoteVol", "?"))
            lines.append(
                f"• `{t.get('symbol','?')}` last=`{t.get('lastPrice','?')}`"
                f" chg24h=`{pct_s}` vol=`{vol}`\n"
                f"  high=`{hi}` low=`{lo}`"
            )
        return "*Tickers:*\n" + "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _unknown(args, update, ctx) -> str:
    return (
        "Unknown trader command. Available:\n"
        "`status` `start` `stop` `strategies` `set` `positions` "
        "`balance` `orders` `history` `close` `paper` `leverage` "
        "`symbol` `funding` `tickers`"
    )
