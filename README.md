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
| `/ts` | All settings (symbol, leverage, TPSL, risk, etc.) |

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
| `/trader tpsl_method` | `POSITION` | POSITION \| PARTIAL \| ADAPTIVE \| FIXED_R |
| `/trader tp_mode` | `FIXED_R` | FIXED_R \| ADAPTIVE \| PARTIAL |
| `/trader trailing_method` | `ATR` | ATR \| RATIO \| INTERVAL |
| `/trader strategies` | `EMA,RSI,MACD` | Active strategies |

### Short Aliases

```
/ts   = /trader settings       /tp   = /trader positions
/tb   = /trader balance        /to   = /trader orders
/th   = /trader history        /tf   = /trader timeframe
/tc   = /trader close          /scan = /trader scan
/ron  = /trader report on      /roff = /trader report off
/ton  = /trader start          /toff = /trader stop
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

The bot manages TP/SL automatically per position:

| Method | Description |
|--------|-------------|
| `POSITION` | One TP/SL for the whole position |
| `PARTIAL` | Scale-out ladder (partial close at each TP level) |
| `ADAPTIVE` | Target chosen from trend strength/structure |
| `FIXED_R` | Multiple of the initial stop loss |

**Mid-management (automatic):**
- **Breakeven**: When PNL ≥ `breakeven`% → SL moves to entry
- **Trailing**: When ROI ≥ `trailing`% → trailing stop activates
- **Guard**: Checks liquidation distance every `guard_interval` seconds
- **Reversal**: If market reverses against position → early exit

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
│   │   ├── tpsl.py           # TPSL automation (breakeven + trailing)
│   │   ├── calculator.py     # Position sizing
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
# Bitunix-python-agent
