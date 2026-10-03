# J-Rock — Telegram AI Agent + Bitunix Futures Trader

One bot. Full AI agent merged with a complete crypto trading engine.

---

## Quick Start

```bash
git clone https://github.com/qezawat-a/Bitunix-python-agent.git
cd Bitunix-python-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your keys
python3 run.py
```

---

## AI Agent Commands

| Command | What it does |
|---------|-------------|
| `/chat <message>` | Talk to the AI |
| `/harness` | Show runtime status (model, provider, thinking) |
| `/thinking off\|low\|medium\|high` | Set reasoning depth |
| `/models` | List available models for current provider |
| `/set model <name>` | Switch model |
| `/provider [name]` | Show or switch AI provider |
| `/set_api_key <provider> <key>` | Update API key |
| `/soul` | Show active soul/persona |
| `/soul add <name>` | Add a soul from file or text |
| `/skills` | List skills |
| `/skills add <path\|url>` | Add a skill |
| `/mcp` | List MCP servers |
| `/mcp add <name> <cmd>` | Add MCP server |
| `/memory [query]` | Search memory |
| `/dream` | Consolidate memories |
| `/learning on\|off` | Enable/disable learning from feedback |
| `/deepsearch <query>` | Deep web research |
| `/bugfixes <desc>` | AI-assisted bug fixing |
| `/code_review [path]` | Review code changes |
| `/agents build <task>` | Build agent — implements a task |
| `/agents plan <task>` | Plan agent — research and design |
| `/team` | Show sub-agent team |
| `/tools` | List available tools |
| `/generator image\|tts\|video\|files\|codes\|docs <prompt>` | Generate content |
| `/app_connector <name> <url>` | Connect external app |
| `/sessions` | List sessions |
| `/resume_session <id\|last>` | Resume a session |
| `/config` | Show current settings |
| `/auto_approve_on_edit on\|off` | Skip edit approval prompts |
| `/auto_compat on\|off` | Auto compatibility layer |
| `/compat` | Show compatibility report |
| `/quit` | Stop current task |

---

## Trader Bot Commands

### Engine Control

| Command | What it does |
|---------|-------------|
| `/ton` | Start trading engine |
| `/toff` | Stop trading engine |
| `/trader autotrade on\|off` | Enable/disable auto-trading |
| `/trader on scanner\|autotrade` | Turn scanner or autotrade on |
| `/trader off scanner\|autotrade` | Turn scanner or autotrade off |

### Status & Info

| Command | What it does |
|---------|-------------|
| `/trader status` | Engine state, open positions, mode |
| `/signal` (`/sig`) | Live signal — status, report, price + confidence + PNL, all in one message |
| `/ts` | All settings (symbol, leverage, TPSL, risk, etc.) |

`/signal` is the "give me everything at once" command. It reads the engine's
in-memory snapshot, so it costs **zero** exchange API calls and answers
instantly — status, the periodic report, current price, the last signal with its
confidence and which strategies voted, and per-position PNL for anything open.

It also always reports **total account PNL** — in USDT *and* as a percentage of
equity — plus equity, available balance and margin used. If any open position
has its stop-loss sitting beyond its liquidation price, it flags that with
`🚨 SL is beyond liquidation`, because the exchange would liquidate you before
the stop could ever fire.

If a signal was rejected (higher-timeframe trend veto, or max positions
reached), the reason is shown next to the signal — the strategies still run and
the signal is still reported, so you can see what the market was doing even
when no trade was taken.

### Reporting

| Command | What it does |
|---------|-------------|
| `/ron` | Report ON — auto-sends signals + price + PNL |
| `/roff` | Report OFF |
| `/trader report interval 30` | Report every 30 seconds |

### Market Data

| Command | What it does |
|---------|-------------|
| `/tb` | Account balance |
| `/tp` | Open positions with TPSL status |
| `/to` | Pending orders |
| `/th` | Position history |
| `/trader funding [SYMBOL]` | Funding rate history |
| `/trader tickers [SYMBOL]` | Market tickers |

### Trade Actions

| Command | What it does |
|---------|-------------|
| `/tc all` | Close all positions |
| `/tc <positionId>` | Close specific position |
| `/trader cancel <orderId>` | Cancel an order |
| `/trader cancel tpsl <orderId>` | Cancel a TPSL order |
| `/trader cancel all` | Cancel all orders |

### Scanner

| Command | What it does |
|---------|-------------|
| `/scan` | Run signal scan now |
| `/trader scan on\|off` | Enable/disable scanner |
| `/trader scan_interval 15` | Scan every 15 seconds |
| `/trader guard_interval 15` | Position guard every 15 seconds |
| `/trader mid_interval 15` | Mid-management every 15 seconds |

### Settings (all persist — no re-typing after restart)

| Command | Example | What it does |
|---------|---------|-------------|
| `/trader symbol` | `MOVRUSDT` | Trading symbol |
| `/trader symbols` | `BTCUSDT,ETHUSDT` | Multiple symbols |
| `/trader timeframes` | `1m,3m,5m,15m` | Scan timeframes |
| `/trader leverage` | `25` | Leverage (pushes to exchange) |
| `/trader margin` | `CROSS` | Margin mode (CROSS \| ISOLATION) |
| `/trader position_mode` | `HEDGE` | Position mode (HEDGE \| ONE_WAY) |
| `/trader risk` | `2.5` | Risk % per trade |
| `/trader max` | `3` | Max open positions |
| `/trader paper on\|off` | — | Paper mode toggle |
| `/trader consensus` | `2` | Min strategies that must agree |
| `/trader confidence` | `80` | Min signal confidence % |
| `/trader tf_confidence` | `60` | Min per-timeframe confidence % |
| `/trader breakeven` | `2.0` | Activate breakeven at % profit |
| `/trader trailing` | `5.0` | Activate trailing at % ROI |
| `/trader trailing_stop` | `0.5` | Trailing stop callback % |
| `/trader trailing_dist` | `0.3` | Trailing distance % |
| `/trader liq_distance` | `5.0` | Liquidation SL distance % |
| `/trader account_tp` | `200` | Close all at +200 USDT |
| `/trader account_sl` | `100` | Close all at -100 USDT |
| `/trader universe` | `VOLUME` | VOLUME \| GAINERS \| LOSERS \| MOVERS |
| `/trader min_volume` | `1000000` | Min 24h volume USD |
| `/trader scan_interval` | `15` | Scan interval seconds |
| `/trader guard_interval` | `15` | Guard interval seconds |
| `/trader mid_interval` | `15` | Mid-management interval seconds |
| `/trader tpsl_method` | `POSITION` | POSITION \| PARTIAL \| TRAILING \| ACCOUNT |
| `/trader tp_mode` | `PARTIAL` | POSITION \| PARTIAL \| TRAILING (initial TP shape) |
| `/trader trailing_method` | `RATIO` | RATIO (pct off peak) \| INTERVAL (abs off peak) |
| `/trader strategies` | `EMA,RSI,MACD` | Active strategies |

### Take-Profit / Stop-Loss

The bot implements the **four TP/SL models Bitunix actually offers**, not an
invented set. Two have dedicated REST endpoints; two exist only in the web/app
order panel, so the bot implements them client-side:

| Model | REST call | Shape |
|-------|-----------|-------|
| `POSITION` | `POST /api/v1/futures/tpsl/position/place_order` | One TP and one SL for the whole position. On trigger it closes everything at market. |
| `PARTIAL` | `POST /api/v1/futures/tpsl/place_order` | A ladder of partial closes, sized with `tpQty`/`slQty` in base coin (default 30/40/30). |
| `TRAILING` | *(no endpoint — client-side)* | Activation price + retracement. The bot rewrites the position's TP/SL order as the peak moves. Retrace is measured by `trailing_method`: `RATIO` = percent off peak, `INTERVAL` = absolute distance off peak. |
| `ACCOUNT` | *(no endpoint — client-side)* | Account-level TP/SL in USDT across **all** positions. The bot watches total account PnL and calls `POST /api/v1/futures/trade/close_all_position` when crossed. Set with `/trader account_tp` and `/trader account_sl`; `0` disables that side. |

On top of any model, breakeven and trailing run as local overlays, applied by
modifying the position's TP/SL order rather than waiting for a fill.

### Short Aliases

```
/ts   = /trader settings       /tp   = /trader positions
/tb   = /trader balance        /to   = /trader orders
/th   = /trader history        /tf   = /trader timeframe
/tc   = /trader close          /scan = /trader scan
/ron  = /trader report on      /roff = /trader report off
/ton  = /trader start          /toff = /trader stop
/sig  = /signal
/mem  = /memory
```

---

## Trading Strategies

| Strategy | Description |
|----------|-------------|
| `EMA` | Exponential Moving Average crossover |
| `RSI` | Relative Strength Index overbought/oversold |
| `MACD` | MACD line crossover with signal |
| `VOLUME` | Volume-based momentum |
| `MOMENTUM` | Price momentum indicator |
| `ATR_BREAKOUT` | ATR-based breakout detection |
| `FUNDING` | Funding rate sentiment |
| `SUPERTREND` | SuperTrend trend follower |
| `BOLLINGER` | Bollinger Bands squeeze/expansion |
| `ICHIMOKU` | Ichimoku cloud signals |

---

## TPSL System

The bot manages TP/SL automatically per position, using the four models Bitunix
documents (see [Take-Profit / Stop-Loss](#take-profit--stop-loss) above for the
endpoints behind each):

| Model | Description |
|-------|-------------|
| `POSITION` | One TP/SL for the whole position |
| `PARTIAL` | Scale-out ladder (partial close at each TP level), sized in base coin |
| `TRAILING` | Activation price + retracement stop; no fixed target |
| `ACCOUNT` | Account-wide TP/SL across all positions |

**Mid-management (automatic):**
- **Breakeven**: When PNL ≥ `breakeven`% → SL moves to entry (only ever tightens — it will never drag a trailed stop back down to entry)
- **Trailing**: When ROI ≥ `trailing`% → trailing stop activates and ratchets along the peak
- **Guard**: Re-reads positions from REST every `guard_interval` seconds; the position WS push omits `avgOpenPrice`/`liqPrice`, so entry and liquidation price only come from here
- **Account TP/SL**: total account PnL is checked every `guard_interval` and closes everything when crossed

---

## Order Sizing

`trader/risk/calculator.py` implements Bitunix's three documented order units.
All three resolve to the same base-coin quantity — they differ only in what you
ask for:

| Unit | You specify | The bot computes |
|------|-------------|-------------------|
| Nominal Value | `notional` in USDT | size at leverage: `qty = notional / entry_price` |
| **Cost Value** (default) | `cost` in USDT of margin | `notional = cost × leverage`, then `qty = notional / entry_price` |
| Quantity Unit | `qty` in base coin | used as-is |

Cost Value is the default because it maps directly to the `risk_pct` setting:
the bot spends `balance × risk%` of margin, so the loss at the stop is the
number you chose. Quantities are floored to the pair's `basePrecision` so
rounding can never push a trade past its risk budget, then clamped to
`minTradeVolume` / `maxMarketOrderVolume`. If the result rounds to zero the trade
is rejected rather than sent as dust.

The calculator also returns the resulting notional, required margin, and both
long/short liquidation prices, using the tiered maintenance margin rate from
`GET /api/v1/futures/account/get_position_tiers`.

---

## Risk: Sizing and Liquidation

Position size and stop-loss are **solved together**, not one after the other.
This matters more than it sounds, because under CROSS the liquidation price
carries a `balance / qty` term:

```
LONG   liq = entry × (1 + MMR) − balance / qty
SHORT  liq = entry × (1 − MMR) + balance / qty
```

The isolated formula (`entry × (1 ∓ 1/leverage ± MMR)`) does **not** apply in
cross — it ignores that the whole account backs the position, and it
understates the real liquidation distance. At 40x on a small account the two
disagree enough to matter.

Because `liq` depends on `qty` and the stop must stay *inside* `liq`, the engine
iterates: pick the ATR stop → pull it inside the liquidation price with a
safety buffer → re-solve the quantity so the loss at that stop equals
`balance × risk%` → repeat until stable. Two consequences:

- **The stop can never sit beyond liquidation.** A stop out past the liq price
  is dead code — the exchange closes you first, so it can never fire. This was
  a real bug: an ATR-derived stop at 40x landed 10.5% from entry while
  liquidation was 8.8% away.
- **`liq_distance` is a real setting, not a warning.** `/trader liq_distance 0.5`
  keeps the stop 0.5% of the way *inside* the liquidation price, so a tighter
  value means a tighter stop. The trade is rejected rather than sent if the
  risk budget cannot afford the exchange's minimum quantity.

Take-profit is then set at 2R against the stop actually placed, not the ATR
stop that was originally wanted.

The liquidation numbers computed here are estimates from the tier table; the
engine prefers the `liqPrice` that comes back from REST when one is available.

---

## Signal Filtering

A signal only becomes a trade if it survives two filters:

- **Higher-timeframe trend veto.** The highest configured timeframe above the
  signal's own (EMA-20 vs EMA-50, with a 0.05% noise gate) sets the permitted
  direction. A 1m short into a 15m uptrend is blocked. The signal is still
  computed and reported — only the trade is skipped — so `/signal` keeps
  showing what the market is doing. Without this the bot will happily take
  counter-trend entries all day.
- **Max open positions.** Signals are still evaluated and recorded at the
  limit, for the same reason.

Confidence is capped at 95% — a 100% reading from a handful of agreeing
indicators is a statement about the sample, not certainty.

---

## Model Auto-Detection

Set `AI_MODEL=` (blank) in `.env`. On startup the agent:

1. Queries `/models` on your configured endpoint
2. Tests each model with a minimal call
3. Picks the first one that responds
4. Falls back to next if rate-limited

Tested providers: OpenAI, Anthropic, DeepSeek, Groq, OpenRouter, Google, local (Ollama/vLLM), any custom endpoint.

---

## Persistent Storage

All settings changed via Telegram commands are saved to:
- **Neon Postgres only** — set `NEON_DATABASE_URL` in `.env` (required)

With Neon, agent memory + trader history + settings survive across machines (GitHub Actions, VPS, etc.).

---

## Deploy on GitHub Actions (Free)

1. Go to repo → **Settings** → **Secrets and variables** → **Actions**
2. Add secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS`, `AI_BASE_URL`, `AI_API_KEY`, `BITUNIX_API_KEY`, `BITUNIX_SECRET_KEY`, `NEON_DATABASE_URL`
3. Go to **Actions** tab → **Run Bot (Manual)** → **Run workflow**
4. Bot runs for up to 6 hours (free tier: 2000 min/month)

For 24/7: see `DEPLOY.md` (VPS + systemd + GitHub Actions auto-deploy).

---

## File Structure

```
├── jrock/                    # AI Agent
│   ├── llm/                  # Model detection + LLM client
│   ├── agent/                # Core loop, memory, sessions, tools
│   ├── bot/                  # Telegram handlers
│   └── trader_handler.py     # All trader Telegram commands
│
├── trader/                   # Trading Engine
│   ├── api/
│   │   ├── rest.py           # All Bitunix REST endpoints
│   │   └── ws.py             # WebSocket (public + private)
│   ├── strategies/           # 10 indicator strategies
│   ├── risk/
│   │   ├── tpsl.py           # Bitunix's four TP/SL models + account guard
│   │   ├── calculator.py     # Order units (nominal / cost / quantity) + sizing
│   │   └── liquidation.py    # Liquidation price calculation
│   └── engine_enhanced.py    # Main trading engine
│
├── .env.example              # Configuration template
├── .github/workflows/        # GitHub Actions (test + deploy + run-bot)
├── j-rock-bot.service        # Systemd service for VPS
└── run.py                    # Entry point
```

---

## Environment Variables

See `.env.example` for full reference. Minimum required:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_IDS=...
AI_BASE_URL=...
AI_API_KEY=...
BITUNIX_API_KEY=...
BITUNIX_SECRET_KEY=...
```

Everything else has sensible defaults.
