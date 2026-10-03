"""
Complete Bitunix trader Telegram handler.

Every command the user requested from day one is here.
Settings are persisted via jrock.store (Neon) so they
survive restarts — no re-typing every session.

Dispatch table at the top; each sub-command is its own function.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import Config

if TYPE_CHECKING:
    from trader.engine_enhanced import TradingEngine


# ── helpers ──────────────────────────────────────────────────────────────────

def _engine(ctx) -> "TradingEngine | None":
    return ctx.bot_data.get("trader_engine")


async def _store():
    from jrock import store
    await store.connect()
    return store


async def _kv_get(key: str, default: str = "") -> str:
    s = await _store()
    return (await s.kv_get(key, default)).strip() or default


async def _kv_set(key: str, val: str) -> None:
    s = await _store()
    await s.kv_set(key, val)


def _is_owner(update: Update) -> bool:
    return Config.is_admin(update.effective_user.id)


# ── main dispatcher ───────────────────────────────────────────────────────────

DISPATCH = {
    # engine lifecycle
    "start":        "_start",
    "stop":         "_stop",
    "on":           "_engine_on",
    "off":          "_engine_off",
    "autotrade":    "_autotrade",

    # info
    "status":       "_status",
    "settings":     "_settings",

    # reporting
    "report":       "_report",

    # live signal snapshot
    "signal":       "_signal",

    # scanning
    "scan":         "_scan_cmd",

    # market data
    "balance":      "_balance",
    "positions":    "_positions",
    "orders":       "_orders",
    "history":      "_history",
    "funding":      "_funding",
    "tickers":      "_tickers",

    # trading actions
    "close":        "_close",
    "cancel":       "_cancel",

    # settings (all settable params)
    "set":          "_set",
    "symbol":       "_set_symbol",
    "symbols":      "_set_symbol",
    "leverage":     "_set_leverage",
    "margin":       "_set_margin",
    "margin_mode":  "_set_margin",
    "position_mode":"_set_posmode",
    "timeframe":    "_set_timeframes",
    "timeframes":   "_set_timeframes",
    "tf":           "_set_timeframes",
    "paper":        "_set_paper",
    "risk":         "_set_risk",

    # threshold params (accept value directly)
    "breakeven":        "_set_breakeven",
    "trailing":         "_set_trailing",
    "max":              "_set_max",
    "consensus":        "_set_consensus",
    "confidence":       "_set_confidence",
    "tf_confidence":    "_set_tf_confidence",
    "liq_distance":     "_set_liq_distance",
    "account_tp":       "_set_account_tp",
    "account_sl":       "_set_account_sl",
    "universe":         "_set_universe",
    "min_volume":       "_set_min_volume",
    "scan_interval":    "_set_scan_interval",
    "guard_interval":   "_set_guard_interval",
    "mid_interval":     "_set_mid_interval",

    # strategies
    "strategies":   "_strategies",
}


async def handle_trader(subcmd: str, args: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> str:
    fname = DISPATCH.get(subcmd.lower())
    if not fname:
        return _unknown(subcmd)
    fn = globals().get(fname)
    if not fn:
        return f"❌ Handler `{fname}` not implemented yet"
    try:
        return await fn(args.strip(), update, ctx)
    except Exception as e:
        logger.exception(f"trader/{subcmd}")
        return f"❌ `{subcmd}` error: {type(e).__name__}: {e}"


def _unknown(subcmd: str) -> str:
    cmds = sorted(DISPATCH.keys())
    half = len(cmds) // 2
    return (
        f"Unknown: `{subcmd}`\n\n"
        f"*Available commands:*\n"
        f"`{' '.join(cmds[:half])}`\n"
        f"`{' '.join(cmds[half:])}`"
    )


# ── load / save complete config from store ───────────────────────────────────

async def _load_cfg() -> dict:
    """Load all persisted settings, filling in .env defaults."""
    return {
        "symbol":           await _kv_get("t/symbol",          Config.DEFAULT_SYMBOL),
        "interval":         await _kv_get("t/interval",        "15m"),
        "timeframes":       await _kv_get("t/timeframes",      "15m"),
        "leverage":         await _kv_get("t/leverage",        str(Config.DEFAULT_LEVERAGE)),
        "margin_mode":      await _kv_get("t/margin_mode",     Config.DEFAULT_MARGIN_MODE),
        "position_mode":    await _kv_get("t/position_mode",   Config.DEFAULT_POSITION_MODE),
        "risk":             await _kv_get("t/risk",            str(Config.RISK_PERCENT)),
        "max_positions":    await _kv_get("t/max_positions",   str(Config.MAX_OPEN_POSITIONS)),
        "paper":            await _kv_get("t/paper",           str(Config.PAPER_TRADING)),
        "autotrade":        await _kv_get("t/autotrade",       "false"),
        "scan_on":          await _kv_get("t/scan_on",         "true"),
        "scan_interval":    await _kv_get("t/scan_interval",   "15"),
        "guard_interval":   await _kv_get("t/guard_interval",  "15"),
        "mid_interval":     await _kv_get("t/mid_interval",    "15"),
        "consensus":        await _kv_get("t/consensus",       "2"),
        "confidence":       await _kv_get("t/confidence",      "80"),
        "tf_confidence":    await _kv_get("t/tf_confidence",   "60"),
        "breakeven":        await _kv_get("t/breakeven",       "2.0"),
        "trailing_trigger": await _kv_get("t/trailing_trigger","5.0"),
        "trailing_stop":    await _kv_get("t/trailing_stop",   "0.5"),
        "trailing_dist":    await _kv_get("t/trailing_dist",   "0.3"),
        "liq_distance":     await _kv_get("t/liq_distance",    "5.0"),
        "account_tp":       await _kv_get("t/account_tp",      "0"),
        "account_sl":       await _kv_get("t/account_sl",      "0"),
        "universe_rank":    await _kv_get("t/universe_rank",   "VOLUME"),
        "min_volume":       await _kv_get("t/min_volume",      "1000000"),
        "tpsl_method":      await _kv_get("t/tpsl_method",     "POSITION"),
        "tp_mode":          await _kv_get("t/tp_mode",         "PARTIAL"),
        "trailing_method":  await _kv_get("t/trailing_method", "RATIO"),
        "engine_on":        await _kv_get("t/engine_on",       "false"),
        "report_on":        await _kv_get("t/report_on",       "false"),
        "report_interval":  await _kv_get("t/report_interval", "30"),
    }


def _bool(v: str) -> bool:
    return v.lower() in ("true", "1", "yes", "on")


# ── engine lifecycle ──────────────────────────────────────────────────────────

async def _start(args: str, update: Update, ctx) -> str:
    if not _is_owner(update):
        return "❌ Owner only"
    if _engine(ctx) and _engine(ctx)._running:
        return "✅ Engine already running"

    from trader.engine_enhanced import TradingEngine, EngineConfig
    c = await _load_cfg()

    tfs_raw = c["timeframes"]
    tfs = [t.strip() for t in tfs_raw.split(",") if t.strip()]

    # command-line overrides: /trader start BTCUSDT 5m
    parts = args.split()
    sym = (parts[0].upper() if parts else "") or c["symbol"]
    ivl = (parts[1] if len(parts) > 1 else "") or c["interval"]
    if not tfs or tfs == [c["interval"]]:
        tfs = [ivl]

    paper = _bool(c["paper"])
    if not paper and not Config.TRADING_ENABLED:
        return "❌ Set TRADING_ENABLED=true in .env or use `/trader paper on`"

    cfg = EngineConfig(
        symbol=sym,
        interval=ivl,
        timeframes=tfs,
        margin_coin=Config.DEFAULT_MARGIN_COIN,
        leverage=int(c["leverage"]),
        margin_mode=c["margin_mode"],
        position_mode=c["position_mode"],
        risk_pct=float(c["risk"]),
        max_positions=int(c["max_positions"]),
        paper=paper,
        consensus_threshold=int(c["consensus"]),
        min_confidence=float(c["confidence"]) / 100.0,
        breakeven_threshold_pct=float(c["breakeven"]),
        trailing_trigger_roi_pct=float(c["trailing_trigger"]),
        trailing_stop_pct=float(c["trailing_stop"]),
        trailing_distance=float(c["trailing_dist"]),
        tpsl_method=c["tpsl_method"],
        trailing_method=c["trailing_method"],
        account_tp=float(c["account_tp"]),
        account_sl=float(c["account_sl"]),
        liq_distance_pct=float(c["liq_distance"]),
        scan_interval=float(c["scan_interval"]),
        guard_interval=float(c["guard_interval"]),
        mid_interval=float(c["mid_interval"]),
    )

    # persist resolved values
    await _kv_set("t/symbol",    cfg.symbol)
    await _kv_set("t/interval",  cfg.interval)
    await _kv_set("t/timeframes", ",".join(cfg.timeframes))

    engine = TradingEngine(cfg)
    engine.account_guard.account_tp = cfg.account_tp
    engine.account_guard.account_sl = cfg.account_sl
    cid = update.effective_chat.id
    bot = ctx.bot

    async def notify(msg: str):
        try:
            await bot.send_message(cid, msg, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    engine.on_notify(lambda m: asyncio.create_task(notify(m)))
    ctx.bot_data["trader_engine"] = engine
    ctx.bot_data["trader_notify"] = notify
    # Remember we were running, so a restart can bring us back up.
    await _kv_set("t/engine_on", "true")

    async def _run():
        try:
            await engine.start()
        except Exception as e:
            logger.exception("engine start failed")
            ctx.bot_data.pop("trader_engine", None)
            await notify(f"❌ Engine failed: `{type(e).__name__}: {e}`")

    asyncio.create_task(_run())

    mode = "📄 PAPER" if paper else "🔴 LIVE"
    return (
        f"🚀 Engine starting\n"
        f"Symbol: `{cfg.symbol}` | TF: `{', '.join(cfg.timeframes)}`\n"
        f"Leverage: `{cfg.leverage}x` | Mode: {mode}\n"
        f"Margin: `{cfg.margin_mode}` | Pos: `{cfg.position_mode}`"
    )


async def _stop(args: str, update: Update, ctx) -> str:
    if not _is_owner(update):
        return "❌ Owner only"
    e = _engine(ctx)
    await _kv_set("t/engine_on", "false")
    if not e:
        return "Engine not running"
    await e.stop()
    ctx.bot_data.pop("trader_engine", None)
    return "⏹ Engine stopped"


async def _engine_on(args: str, update: Update, ctx) -> str:
    """engine on scanner|trader|autotrade"""
    sub = args.lower()
    if sub in ("scanner", "scan"):
        await _kv_set("t/scan_on", "true")
        e = _engine(ctx)
        if e:
            e._scan_enabled = True
        return "✅ Scanner ON"
    if sub in ("autotrade", "trade", "trader"):
        await _kv_set("t/autotrade", "true")
        e = _engine(ctx)
        if e:
            e._autotrade = True
        return "✅ Autotrade ON"
    # default: start engine
    return await _start(args, update, ctx)


async def _engine_off(args: str, update: Update, ctx) -> str:
    sub = args.lower()
    if sub in ("scanner", "scan"):
        await _kv_set("t/scan_on", "false")
        e = _engine(ctx)
        if e:
            e._scan_enabled = False
        return "✅ Scanner OFF"
    if sub in ("autotrade", "trade", "trader"):
        await _kv_set("t/autotrade", "false")
        e = _engine(ctx)
        if e:
            e._autotrade = False
        return "✅ Autotrade OFF"
    return await _stop(args, update, ctx)


async def _autotrade(args: str, update: Update, ctx) -> str:
    if args.lower() in ("on", "true", "1"):
        await _kv_set("t/autotrade", "true")
        e = _engine(ctx)
        if e: e._autotrade = True
        return "✅ Autotrade ON"
    if args.lower() in ("off", "false", "0"):
        await _kv_set("t/autotrade", "false")
        e = _engine(ctx)
        if e: e._autotrade = False
        return "✅ Autotrade OFF"
    val = await _kv_get("t/autotrade", "false")
    return f"Autotrade: `{'ON' if _bool(val) else 'OFF'}`"


# ── /signal ──────────────────────────────────────────────────────────────────

def _fmt_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _signal_line(sig: dict, quote_prec: int = 2) -> str:
    """One-line summary of the engine's latest signal, or why there is none."""
    direction = sig.get("direction")
    conf = sig.get("confidence")
    age = sig.get("age_s", 0)
    icon = {"BUY": "🟢", "SELL": "🔴"}.get(direction, "⚪")
    name = direction or "NO SIGNAL"
    head = f"{icon} *{name}*"
    if conf is not None:
        head += f" — confidence `{conf}%`"
    if sig.get("price"):
        head += f"\nPrice: `{sig['price']:.{quote_prec}f}`"
    head += f"\nWhen: `{_fmt_age(age)}`"
    if sig.get("timeframe"):
        head += f" on `{sig['timeframe']}`"
    if sig.get("opened") is True:
        head += "\n✅ Order was placed"
        if sig.get("order_id"):
            head += f" (`{sig['order_id']}`)"
    elif sig.get("opened") is False and sig.get("error"):
        head += f"\n❌ Order failed: `{sig['error']}`"
    if sig.get("qty"):
        head += f"\nQty: `{sig['qty']}` | SL: `{sig.get('sl') or '—'}`"
        head += f" | TP: `{sig.get('tp') or '—'}`"
    votes = sig.get("votes") or {}
    if votes:
        head += (f"\nVotes: `{votes.get('BUY', 0)}` buy / "
                 f"`{votes.get('SELL', 0)}` sell")
    if sig.get("strategies"):
        head += f"\nStrategies: `{', '.join(sig['strategies'])}`"
    return head


async def _signal(args: str, update: Update, ctx) -> str:
    """Status + live price + last signal + PNL, all read off the engine.

    No exchange call: the engine already tracks all of it, so this stays one
    round trip instead of the four a hand-rolled check would need.
    """
    e = _engine(ctx)
    if not e or not e._running:
        c = await _load_cfg()
        return (
            f"⏸ *Engine stopped* — nothing to report.\n"
            f"Symbol: `{c['symbol']}` | TF: `{c['timeframes']}`\n"
            f"Start it with `/ton`, or scan once with `/scan`."
        )

    snap = e.signal_snapshot()
    c = await _load_cfg()
    quote_prec = e.cfg.quote_precision
    sig = snap.get("signal") or {}

    out = [
        f"*📡 Signal — {snap['symbol']}*",
        f"Engine: `{'✅ running' if snap['running'] else '⏸ stopped'}` | "
        f"Mode: `{'📄 PAPER' if snap['paper'] else '🔴 LIVE'}`",
        f"Timeframes: `{', '.join(snap['timeframes'])}`",
        "",
        _signal_line(sig, quote_prec),
    ]

    if snap.get("candles"):
        bars = " | ".join(f"{tf}:{n}" for tf, n in snap["candles"].items())
        out.append(f"\nCandles loaded: `{bars}`")

    positions = snap.get("positions") or []
    out.append("")
    if positions:
        out.append(f"*Open positions ({len(positions)})*")
        for p in positions:
            flags = []
            if p["breakeven"]:
                flags.append("🎯BE")
            if p["trailing"]:
                flags.append("📈Trail")
            out.append(
                f"• `{p['symbol']}` {p['side']} qty=`{p['qty']}`\n"
                f"  Entry `{p['entry']:.{quote_prec}f}` → "
                f"now `{p['price']:.{quote_prec}f}`\n"
                f"  PNL `{p['pnl_usdt']:+.4f} USDT` ({p['pnl_pct']:+.2f}%) | "
                f"SL `{p['sl'] or '—'}` | TP `{p['tp'] or '—'}` | model `{p['method']}`\n"
                f"  Liq `{p['liq'] or '—'}`"
                + (f"  {' '.join(flags)}" if flags else "")
            )
            if p.get("sl_beyond_liq"):
                out.append("  🚨 **SL is beyond liquidation — it can never fire**")
    else:
        out.append("_No open positions._")

    # Account-level PNL, in USDT and as a share of equity.
    acct = snap.get("account") or {}
    coin = acct.get("coin") or "USDT"
    upnl = snap.get("total_pnl_usdt", 0.0)
    pct = snap.get("total_pnl_pct", 0.0)
    out.append(
        f"\n*Total PNL:* `{upnl:+.4f} {coin}` ({pct:+.2f}% of equity)"
    )
    if acct:
        out.append(
            f"Equity `{acct.get('equity', 0):.2f}` | "
            f"available `{acct.get('available', 0):.2f}` | "
            f"margin used `{acct.get('margin', 0):.2f}`"
        )

    # Model 4 — say whether the account-wide guard is armed.
    atp = float(c.get("account_tp") or 0)
    asl = float(c.get("account_sl") or 0)
    if atp or asl:
        out.append(
            f"\n🛑 Account guard: TP `{atp or '—'}` | SL `{asl or '—'}` "
            f"{c['margin_coin'] if 'margin_coin' in c else 'USDT'}"
        )
    return "\n".join(out)


# ── status / settings ─────────────────────────────────────────────────────────

async def _status(args: str, update: Update, ctx) -> str:
    e = _engine(ctx)
    c = await _load_cfg()
    running = e and e._running
    open_pos = len(e._open_positions) if e else 0
    mode = "📄 PAPER" if _bool(c["paper"]) else "🔴 LIVE"
    return (
        f"*🤖 Trader Status*\n"
        f"Engine: `{'✅ Running' if running else '⏸ Stopped'}`\n"
        f"Mode: {mode}\n"
        f"Symbol: `{c['symbol']}` | TF: `{c['timeframes']}`\n"
        f"Leverage: `{c['leverage']}x` | Risk: `{c['risk']}%`\n"
        f"Open positions: `{open_pos}/{c['max_positions']}`\n"
        f"Autotrade: `{'ON' if _bool(c['autotrade']) else 'OFF'}`\n"
        f"Scanner: `{'ON' if _bool(c['scan_on']) else 'OFF'}`"
    )


async def _settings(args: str, update: Update, ctx) -> str:
    c = await _load_cfg()
    mode = "📄 PAPER" if _bool(c["paper"]) else "🔴 LIVE"
    return (
        f"*⚙️ Trade Settings*\n\n"
        f"*Market*\n"
        f"Symbol: `{c['symbol']}`\n"
        f"Timeframes: `{c['timeframes']}`\n"
        f"Mode: {mode}\n\n"
        f"*Position*\n"
        f"Leverage: `{c['leverage']}x`\n"
        f"Margin mode: `{c['margin_mode']}`\n"
        f"Position mode: `{c['position_mode']}`\n"
        f"Max positions: `{c['max_positions']}`\n\n"
        f"*Risk*\n"
        f"Risk per trade: `{c['risk']}%`\n"
        f"Min strategies agree: `{c['consensus']}`\n"
        f"Min confidence: `{c['confidence']}%`\n"
        f"TF min confidence: `{c['tf_confidence']}%`\n\n"
        f"*TPSL*\n"
        f"Method: `{c['tpsl_method']}`\n"
        f"TP mode: `{c['tp_mode']}`\n"
        f"Trailing method: `{c['trailing_method']}`\n"
        f"Breakeven at: `{c['breakeven']}%` profit\n"
        f"Trailing trigger: `{c['trailing_trigger']}%` ROI\n"
        f"Trailing stop: `{c['trailing_stop']}%`\n"
        f"Trailing distance: `{c['trailing_dist']}%`\n"
        f"Liq SL distance: `{c['liq_distance']}%`\n\n"
        f"*Account Guard*\n"
        f"Account TP: `{c['account_tp']} USDT` (0=off)\n"
        f"Account SL: `{c['account_sl']} USDT` (0=off)\n\n"
        f"*Scanner*\n"
        f"Scan: `{'ON' if _bool(c['scan_on']) else 'OFF'}`\n"
        f"Scan interval: `{c['scan_interval']}s`\n"
        f"Guard interval: `{c['guard_interval']}s`\n"
        f"Mid-management: `{c['mid_interval']}s`\n"
        f"Universe rank: `{c['universe_rank']}`\n"
        f"Min 24h volume: `${c['min_volume']}`\n\n"
        f"*Report*\n"
        f"Report: `{'ON' if _bool(c['report_on']) else 'OFF'}`\n"
        f"Interval: `{c['report_interval']}s`"
    )


# ── reporting ─────────────────────────────────────────────────────────────────

async def _report(args: str, update: Update, ctx) -> str:
    parts = args.split()
    if not parts:
        val = await _kv_get("t/report_on", "false")
        ivl = await _kv_get("t/report_interval", "30")
        return f"Report: `{'ON' if _bool(val) else 'OFF'}` | Interval: `{ivl}s`"

    cmd = parts[0].lower()

    if cmd in ("on", "true"):
        await _kv_set("t/report_on", "true")
        await _start_reporter(update, ctx)
        return "✅ Report ON"

    if cmd in ("off", "false"):
        await _kv_set("t/report_on", "false")
        _stop_reporter(ctx)
        return "✅ Report OFF"

    if cmd == "interval" and len(parts) > 1:
        try:
            sec = int(parts[1])
            await _kv_set("t/report_interval", str(sec))
            return f"✅ Report interval: `{sec}s`"
        except ValueError:
            return "❌ Invalid interval"

    # /trader report interval 30  (without the word "interval")
    try:
        sec = int(cmd)
        await _kv_set("t/report_interval", str(sec))
        return f"✅ Report interval: `{sec}s`"
    except ValueError:
        pass

    return f"Usage: `report on|off` or `report interval <sec>`"


async def _start_reporter(update: Update, ctx) -> None:
    _stop_reporter(ctx)
    try:
        ivl = int(await _kv_get("t/report_interval", "30"))
    except ValueError:
        ivl = 30
    ivl = max(5, ivl)
    cid = update.effective_chat.id
    bot = ctx.bot

    async def _loop():
        while ctx.bot_data.get("reporter_running"):
            e = _engine(ctx)
            if e and e._running:
                # Report what the ENGINE knows — price, last signal, PNL — even
                # when nothing is open. The old loop only built a message if
                # there was at least one open position, so a signal with no fill
                # produced silence, which is exactly when you want to hear.
                try:
                    text = format_report(e)
                    if text:
                        await bot.send_message(cid, text, parse_mode=ParseMode.MARKDOWN)
                except Exception as e:
                    logger.debug(f"report tick failed: {e}")
            await asyncio.sleep(ivl)

    ctx.bot_data["reporter_running"] = True
    ctx.bot_data["reporter_task"] = asyncio.create_task(_loop())


def format_report(e) -> str:
    """Compact periodic report: price, last signal, open positions, PNL."""
    snap = e.signal_snapshot()
    qp = e.cfg.quote_precision
    sig = snap.get("signal") or {}
    lines = [f"📊 *{snap['symbol']}* — every {e.cfg.report_interval}s"
             if hasattr(e.cfg, "report_interval") else
             f"📊 *{snap['symbol']}*"]
    if snap.get("price"):
        lines.append(f"Price: `{snap['price']:.{qp}f}`")
    if sig.get("direction"):
        icon = {"BUY": "🟢", "SELL": "🔴"}.get(sig["direction"], "⚪")
        lines.append(f"{icon} Signal: `{sig['direction']}` "
                     f"`{sig.get('confidence', '?')}%` "
                     f"({_fmt_age(sig.get('age_s', 0))})")
    else:
        lines.append("⚪ Signal: none yet")

    for p in snap.get("positions") or []:
        flags = (" 🎯BE" if p["breakeven"] else "") + (" 📈Trail" if p["trailing"] else "")
        danger = " 🚨SL>LIQ" if p.get("sl_beyond_liq") else ""
        # SL/TP/entry go out at the pair's own quotePrecision. Raw floats printed
        # 17 significant digits ("0.07574929121466055") — unreadable, and it hid
        # the fact that the stop was sitting on the wrong side of entry.
        sl = f"{p['sl']:.{qp}f}" if p.get("sl") else "—"
        tp = f"{p['tp']:.{qp}f}" if p.get("tp") else "—"
        entry = f"{p['entry']:.{qp}f}" if p.get("entry") else "—"
        lines.append(f"• `{p['symbol']}` {p['side']} `{entry}` "
                     f"`{p['pnl_usdt']:+.4f}` ({p['pnl_pct']:+.2f}%) "
                     f"SL:`{sl}` TP:`{tp}`{flags}{danger}")
    # Total PNL always, positions or not — the number that matters.
    lines.append(f"Total PNL: `{snap.get('total_pnl_usdt', 0):+.4f}` "
                 f"({snap.get('total_pnl_pct', 0):+.2f}%)")
    return "\n".join(lines)


def _stop_reporter(ctx) -> None:
    ctx.bot_data["reporter_running"] = False
    task = ctx.bot_data.pop("reporter_task", None)
    if task:
        task.cancel()


async def restore_reporter(update: Update, ctx) -> None:
    """Re-arm reporting after a restart if it was on when we last stopped.

    `t/report_on` is persisted, but nothing re-armed it: the reporter only ever
    started from an explicit `/ron`, so every restart silently dropped back to
    "no news in Telegram" while the setting still read ON.
    """
    if not _bool(await _kv_get("t/report_on", "false")):
        return
    await _start_reporter(update, ctx)
    ctx.bot_data["report_restored"] = True


async def restore_engine(update: Update, ctx) -> None:
    """Restart the engine after a process restart, if it was running before."""
    if not _bool(await _kv_get("t/engine_on", "false")):
        return
    await _start("", update, ctx)
    ctx.bot_data["engine_restored"] = True


async def restore_after_boot(bot, bot_data: dict) -> None:
    """Called from post_init: bring back the engine and reporter if they were on.

    There is no incoming message at boot, so this builds the same minimal
    update/ctx objects the handlers expect. News goes to the first configured
    admin — the one who ran `/ton` last time.
    """
    from types import SimpleNamespace
    admins = Config.ADMIN_IDS
    if not admins:
        return
    chat_id = admins[0]
    user_id = chat_id
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id),
    )
    ctx = SimpleNamespace(bot=bot, bot_data=bot_data)

    # Reporter first: it reports the engine's sync, so it must be listening.
    try:
        await restore_reporter(update, ctx)
    except Exception as e:
        logger.exception(f"restore_reporter failed: {e}")
    try:
        await restore_engine(update, ctx)
    except Exception as e:
        logger.exception(f"restore_engine failed: {e}")


# ── scan commands ─────────────────────────────────────────────────────────────

async def _scan_cmd(args: str, update: Update, ctx) -> str:
    cmd = args.lower()

    if cmd in ("on",):
        await _kv_set("t/scan_on", "true")
        e = _engine(ctx)
        if e: e._scan_enabled = True
        return "✅ Scanner ON"

    if cmd in ("off",):
        await _kv_set("t/scan_on", "false")
        e = _engine(ctx)
        if e: e._scan_enabled = False
        return "✅ Scanner OFF"

    if cmd.startswith("interval"):
        parts = args.split()
        if len(parts) > 1:
            try:
                sec = int(parts[1])
                await _kv_set("t/scan_interval", str(sec))
                return f"✅ Scan interval: `{sec}s`"
            except ValueError:
                pass

    # manual scan now
    e = _engine(ctx)
    if not e:
        return "❌ Engine not running"
    await e._evaluate_strategies()
    return "✅ Scan triggered"


# ── market data ───────────────────────────────────────────────────────────────

async def _balance(args: str, update: Update, ctx) -> str:
    from trader.api.rest import BitunixRestClient, BitunixError, account_dict
    client = BitunixRestClient()
    try:
        resp = await client.get_account(Config.DEFAULT_MARGIN_COIN)
        d = account_dict(resp)
        if not d:
            return "No account data"
        return (
            f"*💰 Balance ({d.get('marginCoin', '?')})*\n"
            f"Available: `{d.get('available', '0')}`\n"
            f"Frozen: `{d.get('frozen', '0')}`\n"
            f"Margin: `{d.get('margin', '0')}`\n"
            f"Cross uPNL: `{d.get('crossUnrealizedPNL', '0')}`\n"
            f"Iso uPNL: `{d.get('isolationUnrealizedPNL', '0')}`\n"
            f"Bonus: `{d.get('bonus', '0')}`"
        )
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _positions(args: str, update: Update, ctx) -> str:
    e = _engine(ctx)
    if e and e._open_positions:
        lines = ["*📋 Positions (engine):*"]
        for pid, pos in e._open_positions.items():
            tp = pos.tpsl.tp_price if pos.tpsl else "?"
            sl = pos.tpsl.sl_price if pos.tpsl else "?"
            lines.append(
                f"• `{pos.symbol}` {pos.side} qty=`{pos.qty}`\n"
                f"  Entry: `{pos.entry_price}` | Price: `{pos.current_price:.6f}`\n"
                f"  PNL: `{pos.unrealized_pnl_pct:+.2f}%` | TP:`{tp}` SL:`{sl}`\n"
                f"  {'🎯 Breakeven' if pos.tpsl and pos.tpsl.breakeven_set else ''}"
                f"{'  📈 Trailing' if pos.tpsl and pos.tpsl.trailing_active else ''}"
            )
        return "\n".join(lines)

    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_pending_positions()
        positions = resp.get("data", [])
        if not positions:
            return "_No open positions_"
        lines = ["*📋 Open Positions:*"]
        for p in positions[:10]:
            lines.append(
                f"• `{p['symbol']}` {p['side']} qty=`{p['qty']}`\n"
                f"  Entry:`{p.get('avgOpenPrice','?')}` "
                f"Liq:`{p.get('liqPrice','?')}` "
                f"uPNL:`{p.get('unrealizedPNL','?')}`"
            )
        return "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _orders(args: str, update: Update, ctx) -> str:
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_pending_orders()
        orders = resp.get("data", {}).get("orderList", [])
        if not orders:
            return "_No pending orders_"
        lines = ["*📑 Pending Orders:*"]
        for o in orders[:10]:
            lines.append(
                f"• `{o.get('orderId','')}` {o.get('symbol','')} "
                f"{o.get('side','')} qty=`{o.get('qty','')}` "
                f"price=`{o.get('price','mkt')}`"
            )
        return "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _history(args: str, update: Update, ctx) -> str:
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_history_positions(limit=10)
        positions = resp.get("data", {}).get("positionList", [])
        if not positions:
            return "_No position history_"
        lines = ["*📜 Position History:*"]
        for p in positions:
            lines.append(
                f"• `{p['symbol']}` {p['side']}"
                f" pnl=`{p['realizedPNL']}`"
                f" entry=`{p.get('entryPrice','?')}`"
                f" close=`{p.get('closePrice','?')}`"
            )
        return "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _funding(args: str, update: Update, ctx) -> str:
    sym = args.strip().upper() or await _kv_get("t/symbol", Config.DEFAULT_SYMBOL)
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_funding_rate_history(sym, limit=5)
        rates = resp.get("data", [])
        if not rates:
            return f"No funding history for `{sym}`"
        lines = [f"*📈 Funding ({sym}):*"]
        for r in rates:
            lines.append(f"• `{r['fundingRate']}` @ `{r['fundingTime']}`")
        return "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _tickers(args: str, update: Update, ctx) -> str:
    sym = args.strip().upper() or None
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        resp = await client.get_tickers(sym)
        data = resp.get("data", [])
        if not data:
            return "_No tickers_"
        if isinstance(data, dict):
            data = [data]
        if sym:
            data = [t for t in data if str(t.get("symbol", "")).upper() == sym] or data[:5]
        else:
            data = data[:5]
        lines = ["*🏷️ Tickers:*"]
        for t in data:
            try:
                chg = (float(t["lastPrice"]) / float(t["open"]) - 1) * 100
                chg_s = f"{chg:+.2f}%"
            except Exception:
                chg_s = "?"
            lines.append(
                f"• `{t.get('symbol','?')}` "
                f"price=`{t.get('lastPrice','?')}` "
                f"chg=`{chg_s}` "
                f"vol=`{t.get('baseVol','?')}`"
            )
        return "\n".join(lines)
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


# ── trade actions ─────────────────────────────────────────────────────────────

async def _close(args: str, update: Update, ctx) -> str:
    if not _is_owner(update):
        return "❌ Owner only"
    if _bool(await _kv_get("t/paper", "true")):
        return "_Paper mode — no real positions_"
    parts = args.split()
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        if not parts or parts[0].lower() == "all":
            await client.close_all_position()
            return "✅ All positions closed"
        pos_id = parts[0]
        await client.flash_close_position(pos_id)
        return f"✅ Closed: `{pos_id}`"
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


async def _cancel(args: str, update: Update, ctx) -> str:
    if not _is_owner(update):
        return "❌ Owner only"
    parts = args.split()
    if not parts:
        return "Usage: `/trader cancel <orderId>` or `/trader cancel tpsl <orderId>`"
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    sym = await _kv_get("t/symbol", Config.DEFAULT_SYMBOL)
    try:
        if parts[0].lower() == "tpsl" and len(parts) > 1:
            await client.cancel_tpsl_order(parts[1], sym)
            return f"✅ TPSL cancelled: `{parts[1]}`"
        elif parts[0].lower() == "all":
            await client.cancel_all_orders(sym)
            return "✅ All orders cancelled"
        else:
            await client.cancel_orders(sym, [{"orderId": parts[0]}])
            return f"✅ Order cancelled: `{parts[0]}`"
    except BitunixError as e:
        return f"❌ [{e.code}] {e.msg}"
    finally:
        await client.close()


# ── strategies ────────────────────────────────────────────────────────────────

async def _strategies(args: str, update: Update, ctx) -> str:
    from trader.strategies import ALL_STRATEGIES
    e = _engine(ctx)
    active = e.cfg.active_strategies if e else []
    lines = ["*📈 Strategies:*"]
    for name, cls in ALL_STRATEGIES.items():
        mark = "✅" if name in active else "○"
        desc = getattr(cls, "description", "")
        lines.append(f"{mark} `{name}` — {desc}")
    lines.append("\nSet: `/trader set strategies EMA,RSI,MACD`")
    return "\n".join(lines)


# ── general /trader set <key> <value> ────────────────────────────────────────

async def _set(args: str, update: Update, ctx) -> str:
    parts = args.split(None, 1)
    if len(parts) < 2:
        return (
            "*Usage:* `/trader set <key> <value>`\n\n"
            "*Keys:* `symbol` `timeframes` `leverage` `risk` `margin_mode`\n"
            "`position_mode` `max_positions` `consensus` `confidence`\n"
            "`tf_confidence` `breakeven` `trailing_trigger` `trailing_stop`\n"
            "`trailing_dist` `liq_distance` `account_tp` `account_sl`\n"
            "`universe_rank` `min_volume` `scan_interval` `guard_interval`\n"
            "`mid_interval` `tpsl_method` `tp_mode` `trailing_method`\n"
            "`strategies` `paper`"
        )
    key, val = parts[0].lower(), parts[1].strip()
    # Route to specific setter
    routed = {
        "symbol":           _set_symbol,
        "symbols":          _set_symbol,
        "timeframe":        _set_timeframes,
        "timeframes":       _set_timeframes,
        "tf":               _set_timeframes,
        "leverage":         _set_leverage,
        "margin_mode":      _set_margin,
        "margin":           _set_margin,
        "position_mode":    _set_posmode,
        "risk":             _set_risk,
        "max_positions":    _set_max,
        "max":              _set_max,
        "consensus":        _set_consensus,
        "confidence":       _set_confidence,
        "tf_confidence":    _set_tf_confidence,
        "breakeven":        _set_breakeven,
        "trailing_trigger": _set_trailing,
        "trailing_stop":    _set_trailing_stop,
        "trailing_dist":    _set_trailing_dist,
        "liq_distance":     _set_liq_distance,
        "account_tp":       _set_account_tp,
        "account_sl":       _set_account_sl,
        "universe_rank":    _set_universe,
        "universe":         _set_universe,
        "min_volume":       _set_min_volume,
        "scan_interval":    _set_scan_interval,
        "guard_interval":   _set_guard_interval,
        "mid_interval":     _set_mid_interval,
        "paper":            _set_paper,
        "strategies":       _set_strategies,
        "strategy":         _set_strategies,
        "tpsl_method":      _set_tpsl_method,
        "tp_mode":          _set_tp_mode,
        "trailing_method":  _set_trail_method,
    }
    fn = routed.get(key)
    if fn:
        return await fn(val, update, ctx)
    return f"❌ Unknown key `{key}`"


# ── individual setters ────────────────────────────────────────────────────────

async def _set_symbol(args: str, update: Update, ctx) -> str:
    sym = args.strip().upper()
    if not sym:
        return f"Current symbol: `{await _kv_get('t/symbol', Config.DEFAULT_SYMBOL)}`"
    await _kv_set("t/symbol", sym)
    e = _engine(ctx)
    if e:
        e.cfg.symbol = sym
        # Reload the pair spec. quotePrecision/basePrecision are PER PAIR —
        # SANDUSDT trades at 5 decimals, a BTC-priced symbol at 2 — so without
        # this the new symbol keeps the old symbol's precision, or the 2-decimal
        # default, and every price, SL and TP prints truncated ("0.08" for
        # 0.08351). The klines for the new symbol must be pulled too, otherwise
        # the engine keeps evaluating strategies on the previous symbol's bars.
        try:
            await e._load_pair(force=True)
            await e._backfill_klines()
        except Exception as ex:
            return f"⚠️ Symbol set to `{sym}` but pair info failed: `{ex}`"
    return f"✅ Symbol: `{sym}`"


async def _set_timeframes(args: str, update: Update, ctx) -> str:
    if not args:
        tfs = await _kv_get("t/timeframes", "15m")
        return (
            f"Timeframes: `{tfs}`\n"
            f"Valid: `1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d`\n"
            f"Set: `/trader timeframes 1m,3m,5m,15m`"
        )
    tfs = [t.strip() for t in args.replace(" ", ",").split(",") if t.strip()]
    await _kv_set("t/timeframes", ",".join(tfs))
    await _kv_set("t/interval", tfs[0])
    e = _engine(ctx)
    if e:
        e.cfg.timeframes = tfs
        e.cfg.interval = tfs[0]
    return f"✅ Timeframes: `{', '.join(tfs)}`"


async def _set_leverage(args: str, update: Update, ctx) -> str:
    if not args or not args.strip().isdigit():
        return f"Current: `{await _kv_get('t/leverage', str(Config.DEFAULT_LEVERAGE))}x`"
    lev = int(args.strip())
    await _kv_set("t/leverage", str(lev))
    e = _engine(ctx)
    if e: e.cfg.leverage = lev
    # also push to exchange
    sym = await _kv_get("t/symbol", Config.DEFAULT_SYMBOL)
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        await client.change_leverage(sym, lev, Config.DEFAULT_MARGIN_COIN)
        return f"✅ Leverage: `{lev}x` (updated on exchange)"
    except BitunixError as ex:
        return f"✅ Leverage saved: `{lev}x` (exchange error: {ex.msg})"
    finally:
        await client.close()


async def _set_margin(args: str, update: Update, ctx) -> str:
    mode = args.strip().upper()
    if mode not in ("CROSS", "ISOLATION"):
        cur = await _kv_get("t/margin_mode", Config.DEFAULT_MARGIN_MODE)
        return f"Current: `{cur}`\nSet: `/trader margin CROSS` or `/trader margin ISOLATION`"
    await _kv_set("t/margin_mode", mode)
    e = _engine(ctx)
    if e: e.cfg.margin_mode = mode
    sym = await _kv_get("t/symbol", Config.DEFAULT_SYMBOL)
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        await client.change_margin_mode(sym, mode, Config.DEFAULT_MARGIN_COIN)
        return f"✅ Margin mode: `{mode}` (updated on exchange)"
    except BitunixError as ex:
        return f"✅ Margin mode saved: `{mode}` (exchange: {ex.msg})"
    finally:
        await client.close()


async def _set_posmode(args: str, update: Update, ctx) -> str:
    mode = args.strip().upper()
    if mode not in ("HEDGE", "ONE_WAY"):
        cur = await _kv_get("t/position_mode", Config.DEFAULT_POSITION_MODE)
        return f"Current: `{cur}`\nSet: `/trader position_mode HEDGE` or `ONE_WAY`"
    await _kv_set("t/position_mode", mode)
    e = _engine(ctx)
    if e: e.cfg.position_mode = mode
    from trader.api.rest import BitunixRestClient, BitunixError
    client = BitunixRestClient()
    try:
        await client.change_position_mode(mode)
        return f"✅ Position mode: `{mode}` (updated on exchange)"
    except BitunixError as ex:
        return f"✅ Position mode saved: `{mode}` (exchange: {ex.msg})"
    finally:
        await client.close()


async def _set_risk(args: str, update: Update, ctx) -> str:
    try:
        pct = float(args.strip())
    except ValueError:
        return f"Current risk: `{await _kv_get('t/risk', str(Config.RISK_PERCENT))}%`"
    await _kv_set("t/risk", str(pct))
    e = _engine(ctx)
    if e: e.cfg.risk_pct = pct
    return f"✅ Risk per trade: `{pct}%`"


async def _set_max(args: str, update: Update, ctx) -> str:
    try:
        n = int(args.strip())
    except ValueError:
        return f"Current max positions: `{await _kv_get('t/max_positions', str(Config.MAX_OPEN_POSITIONS))}`"
    await _kv_set("t/max_positions", str(n))
    e = _engine(ctx)
    if e: e.cfg.max_positions = n
    return f"✅ Max positions: `{n}`"


async def _set_consensus(args: str, update: Update, ctx) -> str:
    try:
        n = int(args.strip())
    except ValueError:
        return f"Current: `{await _kv_get('t/consensus', '2')}` strategies must agree"
    await _kv_set("t/consensus", str(n))
    e = _engine(ctx)
    if e: e.cfg.consensus_threshold = n
    return f"✅ Min agreeing strategies: `{n}`"


async def _set_confidence(args: str, update: Update, ctx) -> str:
    try:
        pct = float(args.strip())
    except ValueError:
        return f"Current min confidence: `{await _kv_get('t/confidence', '80')}%`"
    await _kv_set("t/confidence", str(pct))
    e = _engine(ctx)
    if e: e.cfg.min_confidence = pct / 100.0
    return f"✅ Min confidence: `{pct}%`"


async def _set_tf_confidence(args: str, update: Update, ctx) -> str:
    try:
        pct = float(args.strip())
    except ValueError:
        return f"Current TF min confidence: `{await _kv_get('t/tf_confidence', '60')}%`"
    await _kv_set("t/tf_confidence", str(pct))
    return f"✅ TF min confidence: `{pct}%`"


async def _set_breakeven(args: str, update: Update, ctx) -> str:
    try:
        pct = float(args.strip())
    except ValueError:
        return f"Current breakeven threshold: `{await _kv_get('t/breakeven', '2.0')}%`"
    await _kv_set("t/breakeven", str(pct))
    e = _engine(ctx)
    if e: e.cfg.breakeven_threshold_pct = pct
    return f"✅ Breakeven at: `{pct}%` profit"


async def _set_trailing(args: str, update: Update, ctx) -> str:
    try:
        pct = float(args.strip())
    except ValueError:
        return f"Current trailing trigger ROI: `{await _kv_get('t/trailing_trigger', '5.0')}%`"
    await _kv_set("t/trailing_trigger", str(pct))
    e = _engine(ctx)
    if e: e.cfg.trailing_trigger_roi_pct = pct
    return f"✅ Trailing trigger ROI: `{pct}%`"


async def _set_trailing_stop(args: str, update: Update, ctx) -> str:
    try:
        pct = float(args.strip())
    except ValueError:
        return f"Current trailing stop: `{await _kv_get('t/trailing_stop', '0.5')}%`"
    await _kv_set("t/trailing_stop", str(pct))
    e = _engine(ctx)
    if e: e.cfg.trailing_stop_pct = pct
    return f"✅ Trailing stop: `{pct}%`"


async def _set_trailing_dist(args: str, update: Update, ctx) -> str:
    try:
        pct = float(args.strip())
    except ValueError:
        return f"Current trailing distance: `{await _kv_get('t/trailing_dist', '0.3')}%`"
    await _kv_set("t/trailing_dist", str(pct))
    return f"✅ Trailing distance: `{pct}%`"


async def _set_liq_distance(args: str, update: Update, ctx) -> str:
    try:
        pct = float(args.strip())
    except ValueError:
        return f"Current liq SL distance: `{await _kv_get('t/liq_distance', '5.0')}%`"
    await _kv_set("t/liq_distance", str(pct))
    e = _engine(ctx)
    if e: e.cfg.liq_distance_pct = pct
    return f"✅ Liq SL distance: `{pct}%`"


async def _set_account_tp(args: str, update: Update, ctx) -> str:
    try:
        usdt = float(args.strip())
    except ValueError:
        return f"Current account TP: `{await _kv_get('t/account_tp', '0')} USDT`"
    await _kv_set("t/account_tp", str(usdt))
    return f"✅ Account TP: `{usdt} USDT`"


async def _set_account_sl(args: str, update: Update, ctx) -> str:
    try:
        usdt = float(args.strip())
    except ValueError:
        return f"Current account SL: `{await _kv_get('t/account_sl', '0')} USDT`"
    await _kv_set("t/account_sl", str(usdt))
    return f"✅ Account SL: `{usdt} USDT`"


async def _set_universe(args: str, update: Update, ctx) -> str:
    rank = args.strip().upper()
    valid = ("VOLUME", "GAINERS", "LOSERS", "MOVERS")
    if rank not in valid:
        cur = await _kv_get("t/universe_rank", "VOLUME")
        return f"Current: `{cur}`\nValid: `{' | '.join(valid)}`"
    await _kv_set("t/universe_rank", rank)
    return f"✅ Universe rank: `{rank}`"


async def _set_min_volume(args: str, update: Update, ctx) -> str:
    try:
        vol = float(args.strip())
    except ValueError:
        return f"Current min 24h volume: `${await _kv_get('t/min_volume', '1000000')}`"
    await _kv_set("t/min_volume", str(vol))
    return f"✅ Min 24h volume: `${vol:,.0f}`"


async def _set_scan_interval(args: str, update: Update, ctx) -> str:
    try:
        sec = int(args.strip())
    except ValueError:
        return f"Current scan interval: `{await _kv_get('t/scan_interval', '15')}s`"
    await _kv_set("t/scan_interval", str(sec))
    e = _engine(ctx)
    if e:
        e.cfg.scan_interval = sec
    return f"✅ Scan interval: `{sec}s`"


async def _set_guard_interval(args: str, update: Update, ctx) -> str:
    try:
        sec = int(args.strip())
    except ValueError:
        return f"Current guard interval: `{await _kv_get('t/guard_interval', '15')}s`"
    await _kv_set("t/guard_interval", str(sec))
    e = _engine(ctx)
    if e:
        e.cfg.guard_interval = sec
    return f"✅ Guard interval: `{sec}s`"


async def _set_mid_interval(args: str, update: Update, ctx) -> str:
    try:
        sec = int(args.strip())
    except ValueError:
        return (f"Current mid-management interval: "
                f"`{await _kv_get('t/mid_interval', '15')}s`")
    await _kv_set("t/mid_interval", str(sec))
    e = _engine(ctx)
    if e:
        e.cfg.mid_interval = sec
    return f"✅ Mid-management interval: `{sec}s`"


async def _set_paper(args: str, update: Update, ctx) -> str:
    if not _is_owner(update):
        return "❌ Owner only"
    val = args.strip().lower()
    if val in ("on", "true", "yes", "1"):
        await _kv_set("t/paper", "true")
        e = _engine(ctx)
        if e: e.cfg.paper = True
        return "✅ Paper trading: ON (no real orders)"
    if val in ("off", "false", "no", "0"):
        await _kv_set("t/paper", "false")
        e = _engine(ctx)
        if e: e.cfg.paper = False
        return "✅ Paper trading: OFF — ⚠️ LIVE TRADING"
    cur = await _kv_get("t/paper", "true")
    return f"Paper: `{'ON' if _bool(cur) else 'OFF'}`\nToggle: `/trader paper on|off`"


async def _set_strategies(args: str, update: Update, ctx) -> str:
    names = [n.strip().upper() for n in args.replace(",", " ").split() if n.strip()]
    if not names:
        from trader.strategies import ALL_STRATEGIES
        return (
            f"Current: `{await _kv_get('t/strategies', 'EMA,RSI,MACD')}`\n"
            f"Available: `{', '.join(ALL_STRATEGIES.keys())}`"
        )
    await _kv_set("t/strategies", ",".join(names))
    e = _engine(ctx)
    if e:
        e.set_strategies(names)
    return f"✅ Strategies: `{', '.join(names)}`"


async def _set_tpsl_method(args: str, update: Update, ctx) -> str:
    """Pick one of Bitunix's four TP/SL models.

    These are the four Bitunix actually offers — the previous ADAPTIVE/FIXED_R
    values were ours, not theirs, and had no matching behaviour.
    """
    from trader.risk.tpsl import TPSLMethod
    valid = [m.value for m in TPSLMethod]
    method = args.strip().upper()
    if method not in valid:
        cur = await _kv_get("t/tpsl_method", "POSITION")
        return (
            f"Current: `{cur}`\n"
            f"Valid (Bitunix's four models):\n"
            f"`{' | '.join(valid)}`"
        )
    await _kv_set("t/tpsl_method", method)
    e = _engine(ctx)
    if e:
        e.cfg.tpsl_method = method
    return f"✅ TPSL method: `{method}`"


async def _set_tp_mode(args: str, update: Update, ctx) -> str:
    mode = args.strip().upper()
    valid = ("POSITION", "PARTIAL", "TRAILING")
    if mode not in valid:
        cur = await _kv_get("t/tp_mode", "PARTIAL")
        return f"Current: `{cur}`\nValid: `{' | '.join(valid)}`"
    await _kv_set("t/tp_mode", mode)
    e = _engine(ctx)
    if e:
        e.cfg.tp_mode = mode
    return f"✅ TP mode: `{mode}`"


async def _set_trail_method(args: str, update: Update, ctx) -> str:
    """Retrace mode for TRAILING TP/SL — matches the UI's two choices."""
    method = args.strip().upper()
    valid = ("RATIO", "INTERVAL")
    if method not in valid:
        cur = await _kv_get("t/trailing_method", "RATIO")
        return (
            f"Current: `{cur}`\n"
            f"Valid: `{' | '.join(valid)}`\n"
            f"`RATIO` = % off the peak (`trailing_stop`)\n"
            f"`INTERVAL` = absolute distance off the peak (`trailing_dist`)"
        )
    await _kv_set("t/trailing_method", method)
    e = _engine(ctx)
    if e:
        e.cfg.trailing_method = method
    return f"✅ Trailing mode: `{method}`"
