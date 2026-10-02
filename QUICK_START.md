# Quick Start — 5 Minutes

## 1. Install

```bash
git clone https://github.com/qezawat-a/Bitunix-python-agent.git
cd Bitunix-python-agent
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure `.env`

```bash
cp .env.example .env
# Edit .env with your keys:
# - TELEGRAM_BOT_TOKEN
# - AI_BASE_URL & AI_API_KEY
# - BITUNIX_API_KEY & BITUNIX_SECRET_KEY
```

## 3. Run

```bash
python3 run.py
```

## 4. Test in Telegram

**Agent:**
```
/chat "Hello!"
```

**Trader (Paper Mode):**
```
/trader start BTCUSDT 5m
/trader positions
/trader settings
```

## Key Commands

```
/chat <msg>              Talk to AI
/trader start            Begin trading
/trader settings         View config
/trader positions        Open trades
/trader set risk 2.5     Update risk %
/trader report on        Get updates
```

## How It Works

1. **Agent receives /chat** → Auto-detects working LLM model → Responds
2. **Trader starts** → Subscribes to kline WebSocket → Loads strategies
3. **Each candle closes** → Evaluates all strategies → Consensus reached?
4. **Trade placed** → Auto TPSL set (entry ± ATR)
5. **Price moves** → Breakeven/Trailing auto-trigger
6. **Position closes** → TP/SL hit → Notification sent

## Paper → Live

```
/trader paper off        # NOW TRADING REAL
```

## Debug

```bash
tail -f logs/agent.log   # Watch all activity
cat data/discovered_models.json  # See which models work
```

---

**Start with paper mode. Test 2-3 trades. Then go live when confident.**
