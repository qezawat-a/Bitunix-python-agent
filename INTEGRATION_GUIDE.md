# Integration Guide: Agent + Trader + TPSL

## What Was Fixed

### 1. Agent Model Detection ✅
- **File:** `jrock/llm/client.py`
- **Change:** Added `resolve_model_async()` that tests providers in real-time
- **Result:** Agent now finds and uses working models automatically
- **No more errors:** "AUTO" is resolved to an actual working model before sending to API

### 2. Simplified TPSL ✅
- **File:** `trader/risk/tpsl.py` (NEW)
- **Features:** 
  - Breakeven: Move SL to entry when PNL >= 2%
  - Trailing: Activate at ROI >= 5%, trail by 0.5% from best price
  - Simple and reliable (no complex 4-method logic)

### 3. Trader Agent Context ✅
- **File:** `jrock/agent/trader_agent.py`
- **Purpose:** Shared state between AI agent and trading engine
- **Integrates:** Position tracking, signal generation, risk management

---

## How to Use

### Step 1: Test Agent Chat (Model Fix)

```bash
# Send a message to the bot
/chat "Hello, what can you do?"

# Should respond without error
# If error: check your API_BASE_URL and API_KEY in .env
```

### Step 2: Start Trader with TPSL

```bash
# Start the engine with auto-TPSL
/trader start BTCUSDT 15m

# Check settings
/trader settings

# View positions (will show TP/SL and breakeven status)
/trader positions
```

### Step 3: Monitor Mid-Management

```bash
# Watch breakeven and trailing activate as PNL increases
/trader report on          # Get signal updates
/trader report interval 30 # Report every 30 seconds

# Output will show:
# ✅ Breakeven activated at 2.5% PNL
# ✅ Trailing activated at 5.2% ROI
# ✅ Trailing updated: SL moved to 23450.50
```

---

## Configuration (.env)

```env
# Agent
AI_BASE_URL=http://127.0.0.1:20128/v1
AI_API_KEY=sk-xxx
AI_MODEL=AUTO  # Will auto-detect working models

# Trader TPSL
DEFAULT_SYMBOL=BTCUSDT
DEFAULT_LEVERAGE=10
RISK_PERCENT=2.0

# TPSL Thresholds
BREAKEVEN_THRESHOLD_PCT=2.0      # Activate at 2% profit
TRAILING_TRIGGER_ROI_PCT=5.0     # Activate trailing at 5% ROI
TRAILING_STOP_PCT=0.5            # Trail 0.5% from best price
```

---

## Integration Points

### Agent → Trader
```python
# Agent can query trader status
trader_agent.get_status()
trader_agent.scan_signals()

# Agent can manage positions
trader_agent.manage_position(position_id, "close")
trader_agent.manage_position(position_id, "breakeven")
trader_agent.manage_position(position_id, "trail")
```

### Trader → Agent
```python
# Trader can request signals from agent strategies
strategies = await agent.query_strategies(symbol, timeframe)

# Trader can ask agent to generate trading plan
plan = await agent.plan_trade(signal)
```

---

## Error Handling

### If Agent Chat Still Errors
1. Check `.env`: `AI_BASE_URL` must be reachable
2. Check API key is valid
3. Look in `data/discovered_models.json` to see if models were found
4. If empty → endpoint not responding or no auth

### If Trader Engine Fails
1. Check `logs/agent.log` for errors
2. Verify `BITUNIX_API_KEY` and `BITUNIX_SECRET_KEY` are set
3. Check Bitunix API is not rate-limited
4. Verify network can reach `fapi.bitunix.com`

### If TPSL Doesn't Trigger
1. Check position is actually LONG/SHORT (not closed)
2. Verify PNL percentages are being calculated
3. Check breakeven/trailing thresholds aren't too high
4. Review position update logs

---

## Next Steps

1. **Test agent chat** → Verify model detection works
2. **Paper trade** → Test trader with PAPER_TRADING=true
3. **Monitor TPSL** → Watch breakeven and trailing activate
4. **Go live** → Switch PAPER_TRADING=false when confident
5. **Add more strategies** → EMA, RSI, MACD, Bollinger, etc.

---

## Support

All code is in:
- Agent: `jrock/` (LLM, skills, memory, sessions)
- Trader: `trader/` (engine, strategies, TPSL, risk)
- Integration: `jrock/agent/trader_agent.py`

Telegram commands handle everything else.
