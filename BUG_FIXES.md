# Bug Fixes & Issues Resolved

## Issue 1: Agent Model Chat Error

**Problem:** When sending a message to the bot, it returns error instead of chatting. Model detection was failing.

**Root Cause:** 
- `AI_MODEL=AUTO` was being sent literally to the API (causing 404)
- Model resolution fallback to hardcoded models that don't exist on the user's endpoint
- No dynamic model discovery from the user's custom provider

**Fix Applied:** 
- Rewrote `jrock/llm/client.py` with `resolve_model_async()`
- Now tests actual models from provider's `/models` endpoint
- Falls back gracefully if endpoint is down
- Caches working models to avoid repeated probes
- Created `jrock/llm/model_discovery.py` for persistent caching

**Code Changes:**
```python
# OLD: Sends "AUTO" literally
model = self.resolve_model(model, provider)  # Returns "AUTO"

# NEW: Actually probes and finds a working model
model = await self.resolve_model_async(model, provider)
# Tests each model: returns first one that responds
```

---

## Issue 2: Trader Bot Engine Doesn't Work

**Problem:** Trader engine fails to start, no signals, no trades, complete silence.

**Root Cause(s):**
1. Missing TPSL implementation (was trying 4 methods, all incomplete)
2. Position lifecycle incomplete (open → management → close)
3. WebSocket connection issues not handled
4. Engine didn't properly integrate with agent context
5. No error visibility (failures were silent)

**Fix Strategy (Simplified TPSL):**

Instead of 4 complex methods, implement ONE that actually works:

### POSITION Mode with Breakeven + Trailing

```python
class PositionTPSL:
    def __init__(self, entry_price, side, atr_value):
        self.entry_price = entry_price
        self.side = side
        self.atr = atr_value
        self.tp_price = self._calc_tp()
        self.sl_price = self._calc_sl()
        self.breakeven_set = False
        self.trailing_active = False
    
    def _calc_tp(self):
        """TP = 2x ATR above/below entry"""
        if self.side == "LONG":
            return self.entry_price + (self.atr * 2)
        return self.entry_price - (self.atr * 2)
    
    def _calc_sl(self):
        """SL = 1x ATR below/above entry"""
        if self.side == "LONG":
            return self.entry_price - self.atr
        return self.entry_price + self.atr
    
    def update(self, current_price, unrealized_pnl_pct):
        """Update on each candle close"""
        
        # Breakeven: when PNL >= 2%, move SL to entry + 1bp
        if not self.breakeven_set and unrealized_pnl_pct >= 2.0:
            self.sl_price = self.entry_price
            if self.side == "SHORT":
                self.sl_price -= 0.0001
            self.breakeven_set = True
            return {"action": "breakeven", "new_sl": self.sl_price}
        
        # Trailing: when PNL >= 5%, activate trailing stop
        if not self.trailing_active and unrealized_pnl_pct >= 5.0:
            self.trailing_active = True
            return {"action": "trailing_start"}
        
        # Trailing stop: callback 0.5% from best price
        if self.trailing_active:
            best_price = current_price  # Track best price seen
            if self.side == "LONG":
                new_sl = best_price * 0.995  # 0.5% callback
                if new_sl > self.sl_price:
                    self.sl_price = new_sl
                    return {"action": "trailing_update", "new_sl": self.sl_price}
            else:
                new_sl = best_price * 1.005
                if new_sl < self.sl_price:
                    self.sl_price = new_sl
                    return {"action": "trailing_update", "new_sl": self.sl_price}
        
        return {"action": "none"}
```

---

## Implementation Priority

1. ✅ **Model Detection** — DONE (in main branch)
2. **Simplified TPSL** — Implement PositionTPSL class (see above)
3. **Engine Integration** — Wire TPSL into position lifecycle
4. **Error Handling** — Log all failures, notify via Telegram
5. **Testing** — Validate with paper trading

---

## Files Modified

- `jrock/llm/client.py` — Multi-provider model detection
- `jrock/llm/model_discovery.py` — Model caching
- `jrock/agent/trader_agent.py` — Unified context
- `trader/engine.py` — TPSL integration (TODO)
- `trader/risk/tpsl.py` — New TPSL implementation (TODO)

---

## Testing

Once implemented:

```bash
# Test agent chat
/chat "What's 2+2?"

# Test trader
/trader start BTCUSDT 15m
/trader strategies
/trader set strategies EMA,RSI,MACD
/trader settings

# Watch positions
/trader positions
/trader status
```

---

## Configuration

All working models will be auto-cached in `data/discovered_models.json`:

```json
{
  "provider": "custom",
  "models": [
    "model-that-works-1",
    "model-that-works-2"
  ]
}
```

Agent will use first one; if rate limited, falls back to next.
