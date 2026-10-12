# Welcome to Bitunix Trading Team

## How We Use Claude

Claude Code can help with debugging, quality improvements, feature work, and
code review. Use the setup checklist below to configure the tools needed for
this project.

## Your Setup Checklist

### Codebases
- [ ] bitunix-python-agent — https://github.com/qezawat-a/bitunix-python-agent
- [ ] bitunix-agent-cline — https://github.com/qezawat-a/bitunix-agent-cline

### MCP Servers to Activate
- [ ] railway — Deployment and service management for the bot. Run `railway login` in a terminal, then ask a teammate to add you to the Railway project.
- [ ] bitunix — Read-only market data (tickers, klines, order book, funding rates) and, with API credentials, account and order queries. Ask for the team's API key setup; never commit keys to the repo.

### Skills to Know About
- [/config] — Change Claude Code settings such as theme and permissions.
- [/effort] — Set how much reasoning effort Claude spends on a task.
- [/model] — Switch the model for the current session.
- [/mcp] — Check which MCP servers are connected and their status.
- [/code_review] — Review the current diff or a PR for correctness bugs before merging.
- [/diagnose-trailing-failures] — Diagnose the failing breakeven/trailing stop tests: stale assertions vs. live trading bugs.
- [/verify] — Check that a change works before you call it done.

## Team Tips

_TODO_

## Get Started

**Starter task (draft, please confirm):** Align our Bitunix futures code with the official API docs, then modify the order unit calculator using the order-units help article. The docs are the source of truth, so match their parameters, endpoints, and error codes exactly.

Reference docs:
- Example code: https://github.com/qezawat-a/open-api/Demo
- Liquidation mechanism: https://support.bitunix.com/hc/en-us/articles/32152530856601-Bitunix-Futures-Liquidation-Mechanism-and-Tiered-Risk-Limit
- Order units calculator: https://www.bitunix.com/hub/helpcenter/article/explanation-of-the-order-units-in-futures-trading?id=170
- Futures API index (market, account, position, TP/SL, trade, WebSocket): https://www.bitunix.com/api-docs/futures/
- Error codes: https://www.bitunix.com/api-docs/futures/ErrorCode/error_code.html
- Changelog: https://www.bitunix.com/api-docs/futures/log/change_log.html
