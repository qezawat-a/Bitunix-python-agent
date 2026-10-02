# J-Rock Telegram AI Agent + Bitunix Trader Bot — Complete Setup

## Prerequisites

- Python 3.10+
- Telegram Bot Token (from BotFather)
- LLM API Key (OpenAI, DeepSeek, local endpoint, etc.)
- Bitunix Futures API Key + Secret

## Installation

### 1. Clone & Install

```bash
git clone https://github.com/qezawat-a/Bitunix-python-agent.git
cd Bitunix-python-agent
python3 -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
# ── Telegram ─────────────────────
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN_HERE
TELEGRAM_ADMIN_IDS=YOUR_USER_ID

# ── AI Provider ──────────────────
# Use ANY provider: OpenAI, DeepSeek, Anthropic, OpenRouter, local, etc.
# The agent will auto-detect working models
AI_BASE_URL=http://127.0.0.1:20128/v1  # or https://api.openai.com/v1
AI_API_KEY=sk-xxxxxx                   # Your actual API key
AI_MODEL=AUTO                          # Auto-detects best model for your key

# Optional: multiple provider keys
# OPENAI_API_KEY=sk-xxxxx
# DEEPSEEK_API_KEY=sk-xxxxx
# ANTHROPIC_API_KEY=sk-xxxxx

# ── Trading ──────────────────────
DEFAULT_SYMBOL=BTCUSDT
DEFAULT_LEVERAGE=10
DEFAULT_MARGIN_MODE=CROSS
DEFAULT_POSITION_MODE=HEDGE
RISK_PERCENT=2.0
MAX_OPEN_POSITIONS=1
PAPER_TRADING=true  # Start with paper, switch to false when ready

# ── Bitunix API ──────────────────
BITUNIX_API_KEY=your_api_key_here
BITUNIX_SECRET_KEY=your_secret_key_here
BITUNIX_BASE_URL=https://fapi.bitunix.com
BITUNIX_WS_PUBLIC=wss://fapi.bitunix.com/public/
BITUNIX_WS_PRIVATE=wss://fapi.bitunix.com/private/

# ── Database & Logging ───────────
NEON_DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
LOG_LEVEL=INFO
LOG_FILE=./logs/agent.log
```

### 3. Run the Bot

```bash
python3 run.py
```

You should see:
```
[jrock] starting polling…
[jrock] ready as YOUR_BOT_NAME | custom/auto-detected-model | soul=fable-5.1
```

---

## Features

### 🤖 AI Agent

- **Multi-provider auto-detection**: Tests all configured LLM providers, finds which one works right now
- **Smart model probing**: Tests each available model, uses the first that responds
- **Rate limit fallback**: If a model hits rate limit, automatically switches to next working model
- **Skills system**: Load custom skills for specialized tasks
- **Memory**: Remembers user preferences, past conversations
- **Sessions**: Multi-turn conversations with context
- **Tools**: Terminal access, file operations, web search, code generation

### 🤖 Trader Bot

- **13 Technical Indicators**: EMA, RSI, MACD, Bollinger Bands, ATR, Funding Rate, Ichimoku, Momentum, Supertrend, Volume, ATR Breakout, and more
- **Consensus Signals**: Requires minimum N strategies to agree before trading
- **Automated TPSL**:
  - Entry at market price
  - TP = 2x ATR above/below entry
  - SL = 1x ATR below/above entry
  - **Breakeven**: Automatically moves SL to entry when profit ≥ 2%
  - **Trailing Stop**: Activates when ROI ≥ 5%, trails 0.5% from best price
- **Paper Trading**: Test strategies risk-free
- **Position Tracking**: Real-time PNL, WebSocket updates
- **Risk Management**: Position sizing based on account %, max position limits

---

## Commands

### Agent Commands

```
/start              Show help
/chat [message]     Chat with the AI
/harness            Runtime status
/thinking off|low|medium|high
/models             List available models
/set model <name>   Switch model
/provider [name]    Show/switch provider
/soul               Show active soul / /soul add <name> <text>
/skills             List skills
/memory [query]     Search memories
/dream              Consolidate memories
/learning on|off    Enable learning from feedback
```

### Trader Commands

```
/trader status           Current engine status
/trader start [SYM] [TF] Start trading engine
/trader stop             Stop engine
/trader settings         View all settings
/trader strategies       List available strategies
/trader set <key> <val>  Update settings:
                         - strategies STRAT1,STRAT2
                         - leverage 20
                         - symbol ETHUSDT
                         - risk 2.5
                         - breakeven 1.5 (activate at % profit)
                         - trailing 0.75 (callback %)

/trader positions        Open positions
/trader balance          Account balance
/trader orders           Pending orders
/trader history          Closed positions
/trader close all|ID     Close position(s)
/trader scan             Run signal scanner
/trader report on|off    Enable/disable updates
/trader paper on|off     Toggle paper mode
/trader funding [SYM]    Funding rate history
/trader tickers [SYM]    Market tickers
```

---

## Workflow: Paper Trading

### Step 1: Test Agent Chat

```
You: /chat What's the current Bitcoin price?
Bot: (connects to your LLM, returns answer)
```

If it errors, check:
- `.env`: AI_BASE_URL must be reachable
- API key is valid
- Check `logs/agent.log` for details

### Step 2: Start Trading Engine

```
You: /trader start BTCUSDT 5m
Bot: 🚀 Engine starting: `BTCUSDT` `5m` 📄 PAPER
```

### Step 3: Configure Strategy & Risk

```
You: /trader set strategies EMA,RSI,MACD
You: /trader set risk 2.5
You: /trader settings
Bot: Shows all current settings
```

### Step 4: Monitor Signals

```
You: /trader report on
Bot: Will notify you of signals, entries, exits
```

Watch for:
- **📈 SIGNAL** — Strategy consensus reached, entry placed
- **✅ Breakeven Set** — Position now at break-even
- **📈 Trailing Activated** — Trailing stop started
- **✅ Position Closed** — Exit with PNL

### Step 5: Go Live (When Ready)

```
You: /trader paper off
You: /trader settings
Bot: Shows Mode: 🔴 LIVE (now trading real)
```

---

## How TPSL Works (Automated)

### Entry
```
Signal detected → Place order at market price
TP = entry + (2 × ATR)    [2x risk-reward]
SL = entry - (1 × ATR)    [1x risk]
```

### Breakeven (Auto)
```
When PNL ≥ 2% (configurable):
  SL moves to entry + 1bp
  Risk now = 0 (breakeven)
  Notification: "✅ Breakeven Set"
```

### Trailing (Auto)
```
When ROI ≥ 5% (configurable):
  Trailing activates
  SL follows best price by 0.5% (configurable)
  Updates on each price movement
  Locks in profits as price moves favorably
  Notification: "📈 Trailing Activated"
          then: "📈 Trailing Updated"
```

### Exit
```
TP hit → Position closed with profit ✅
SL hit → Position closed with loss ⚠️
Manual close → /trader close <positionId>
```

---

## Configuration Tips

### For Maximum Profitability
```
Breakeven threshold: 1-2%     (lock profits early)
Trailing ROI trigger: 3-5%    (activate sooner)
Trailing callback: 0.5-1%     (wider trail = more room)
Min confidence: 70-80%        (quality signals)
Consensus: 2-3 strategies     (redundancy)
Risk %: 1-3%                  (safe sizing)
```

### For Aggressive Trading
```
Breakeven threshold: 3-5%     (let winners run)
Trailing ROI trigger: 8-10%   (wait for big moves)
Trailing callback: 0.25-0.5%  (tight trail)
Min confidence: 60-70%        (more signals)
Consensus: 1-2 strategies     (faster entries)
Risk %: 3-5%                  (larger positions)
```

### For Conservative Trading
```
Breakeven threshold: 0.5-1%   (protect immediately)
Trailing ROI trigger: 2-3%    (early activation)
Trailing callback: 1-2%       (loose trail)
Min confidence: 85-90%        (only best signals)
Consensus: 3 strategies       (strong agreement)
Risk %: 0.5-1.5%              (tiny positions)
```

---

## Troubleshooting

### Agent Chat Returns Error

**Problem**: `/chat "hi"` returns API error

**Solutions**:
1. Check `.env` AI_BASE_URL is correct
2. Verify API key is valid
3. Check endpoint is running (if local)
4. View `logs/agent.log` for details
5. Check `data/discovered_models.json` — if empty, endpoint not responding

### Trader Engine Won't Start

**Problem**: `/trader start` hangs or fails silently

**Solutions**:
1. Check logs: `tail -f logs/agent.log`
2. Verify Bitunix API keys in `.env`
3. Ensure network can reach `fapi.bitunix.com`
4. Try paper mode first: `/trader paper on`
5. Check database permissions: `ls -la data/`

### TPSL Not Triggering

**Problem**: Position open but breakeven/trailing not activating

**Solutions**:
1. Check position PNL: `/trader positions` shows `unrealizedPNL %`
2. Verify thresholds: `/trader settings` shows current breakeven %
3. View logs: `grep "TPSL\|breakeven\|trailing" logs/agent.log`
4. Ensure position exists and is not closed

### No Signals Generated

**Problem**: Strategies run but no trades

**Solutions**:
1. Check strategies are loaded: `/trader strategies`
2. Lower min confidence: `/trader set consensus 1`
3. Run scanner: `/trader scan`
4. Check candle data: Need at least 20 candles for indicators
5. Verify signal logic in `trader/strategies/`

---

## File Structure

```
Bitunix-python-agent/
├── jrock/                   # AI Agent core
│   ├── llm/                 # Model detection & LLM client
│   │   ├── client.py       # Multi-provider async resolution
│   │   ├── model_discovery.py
│   │   └── providers.py
│   ├── agent/               # Agent core
│   │   ├── core.py         # Main agent loop
│   │   ├── trader_agent.py # Trader context bridge
│   │   └── ...
│   ├── bot/                 # Telegram handlers
│   │   └── handlers.py
│   └── trader_handler.py    # 👈 New comprehensive trader commands
│
├── trader/                  # Trading engine
│   ├── api/
│   │   ├── rest.py         # All Bitunix REST endpoints
│   │   └── ws.py           # WebSocket streams
│   ├── strategies/          # 13 technical indicators
│   ├── risk/
│   │   └── tpsl.py         # 👈 New TPSL implementation
│   ├── engine.py           # Old engine
│   └── engine_enhanced.py   # 👈 New enhanced engine with TPSL
│
├── data/                    # Runtime data
│   ├── discovered_models.json  # Cached working models
│   ├── agent.db            # Sessions, memory
│   └── ...
├── logs/                    # Log files
├── .env                     # Your configuration
└── run.py                   # Entry point
```

---

## Next Steps

1. **Test everything in paper mode first**
2. **Monitor logs**: `tail -f logs/agent.log`
3. **Start small**: 1 symbol, low leverage
4. **Watch first trades**: Verify TPSL activates correctly
5. **Gradually increase risk** as you gain confidence
6. **Enable learning**: `/learning on` — agent improves from your feedback

---

## Support

- Check `logs/agent.log` for all errors
- Review `.env` for missing/invalid keys
- Run `/trader settings` to verify configuration
- Run `/trader scan` to test signals manually
- Test with paper mode: `/trader paper on`

All code is open-source. Issues? Check the GitHub repo or review the code directly.
