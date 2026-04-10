# Mempalace Trader Handoff State

Last updated: 2026-04-10 19:53 Asia/Bangkok

Read this file first when continuing the project in a new chat/session. It is intentionally written without API keys, tokens, passwords, account logins, private keys, or local export data.

## Restart prompt for a new chat

```text
Continue Mempalace Trader from D:\Mempalac_AI. Read docs/HANDOFF_STATE.md first, then read README.md, docs/ARCHITECTURE.md, docs/TRADING_MEMORY_V2.md, and trading_ai/docs/PARALLEL_WITH_DEXTER.md as needed. Check git status/log, local .env without printing secrets, runtime_state, logs, current processes, and dashboard status. Do not modify or stop Dexter. Preserve secret hygiene. Current design uses local Ollama qwen2.5:1.5b primary, qwen2.5:0.5b and gemma3:1b-it-qat fallback, DRY_RUN=false demo trading, Dexter worker execution, pre-LLM hard filters, MemPalace-style memory wings/halls/rooms, and optional daily MiMo analyst. Continue safely from current state.
```

## Current repository state

- Repo path: `D:\Mempalac_AI`
- Remote: `https://github.com/Geowahaha/Mempalace_trader.git`
- Branch: `main`
- Latest pushed commits:
  - `2196a60 Add read-only Dexter edge auditor`
  - `0ffe6d3 Add Mempalace handoff state`
  - `aa3750f Add safe Mempalac stop script`
  - `30aceed Initial Mempalace production trading engine`
- The local untracked file `start_stop ระบบ Mempalace ai.txt` is user-local and should not be staged unless explicitly requested.
- Git ignore rules intentionally exclude `.env`, `.env.*`, `.venv`, `data`, `logs`, `reports`, Chroma/db files, account exports, ssh keys, and local notes.

## Current runtime state

- Mempalace is running on this PC as normal PowerShell/Python processes, not as a Windows service.
- Current API process command: `python.exe -m trading_ai.api`
- Current loop process command: `python.exe -m trading_ai --interval 30 --no-dry-run`
- Current dashboard: `http://127.0.0.1:8091/dashboard`
- Current status from `/status`:
  - `instance_name=mempalac`
  - `symbol=XAUUSD`
  - `llm_provider=LOCAL`
  - `dry_run_default=false`
  - `live_execution_enabled=true`
  - `memory_count=107`
  - `trade_memory_count=0`
  - `note_memory_count=107`
- Current `runtime_state.json`:
  - `open_position=null`
  - `trades_executed=0`
  - `consecutive_losses=0`
  - `halted=false`

Process IDs change after restart. Do not rely on old PIDs except for immediate same-session debugging.

## Start, stop, and monitor commands

Start the full local demo-live stack:

```powershell
cd D:\Mempalac_AI
.\scripts\start-demo-live-stack.ps1 -Interval 30
```

Stop Mempalace API and trading loop only. This does not stop Dexter and does not stop Ollama:

```powershell
cd D:\Mempalac_AI
.\scripts\stop-demo-live-stack.ps1
```

Stop only the trading loop and keep the API/dashboard if needed:

```powershell
cd D:\Mempalac_AI
.\scripts\stop-demo-live-stack.ps1 -KeepApi
```

Check dashboard/API status:

```powershell
cd D:\Mempalac_AI
.\scripts\check-status.ps1
```

Tail logs:

```powershell
cd D:\Mempalac_AI
.\scripts\tail-logs.ps1
```

Direct status check:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8091/status" -TimeoutSec 10
```

Direct Ollama health check:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
```

## Reboot behavior

If the PC is shut down or restarted, the current Mempalace PowerShell/Python processes stop. They will not automatically resume unless a Windows Scheduled Task or service wrapper is created later.

After reboot:

1. Make sure Ollama background server is running.
2. Confirm `Invoke-RestMethod http://127.0.0.1:11434/api/tags` returns local models.
3. Run `.\scripts\start-demo-live-stack.ps1 -Interval 30`.
4. Check dashboard and logs.

The Ollama desktop app window can be closed. Mempalace needs `ollama.exe serve`, not the UI window.

## LLM design

- Primary local model: `qwen2.5:1.5b`
- Fallback local models: `qwen2.5:0.5b`, `gemma3:1b-it-qat`
- Local endpoint: `http://127.0.0.1:11434/v1`
- Reasoning from tests:
  - `qwen2.5:1.5b` passed controlled BUY/SELL/HOLD probe after prompt cleanup.
  - `qwen2.5:0.5b` was faster but failed 2/3 controlled cases, so it is fallback only.
  - Gemma local was slower on this PC and sometimes produced schema/action issues, so it is last fallback.

Cost note:

- Local Ollama inference costs no OpenAI/MiMo/API credits.
- cTrader API calls do not spend LLM credits.
- MiMo Pro should remain optional and limited to the daily analyst job unless explicitly enabled for live decisions.
- Exact Codex/chat credit usage is not visible from this repo. Creating/reading this handoff file is small, but the platform/account billing page is the only source of exact credit cost.

## Trading/execution design

- Current runtime is demo-live, not dry run:
  - `DRY_RUN=false`
  - `LIVE_EXECUTION_ENABLED=true`
- Execution path is via the Dexter worker integration from Mempalace, but Dexter repo itself must not be modified by Mempalace work.
- Market quote source currently uses the Dexter-reference path instead of the failing native cTrader capture route.
- Native cTrader route still has unresolved support issue:
  - observed `app_auth_failed`
  - observed `CANT_ROUTE_REQUEST`
  - support/debug bundle should be sent to cTrader/OpenAPI support separately if needed.
- Do not print or commit `.env` contents, OpenAPI tokens, refresh tokens, passwords, or account identifiers.

## Risk and decision rules

Current safety posture is intentionally conservative:

- `pre_llm_hard_filter` runs before the LLM for obvious no-trade regimes.
- Guaranteed HOLD before LLM when:
  - volatility is `LOW`
  - trend is `RANGE`
  - structure is consolidation
  - risk loss streak block is active
- After LLM, hard filter still blocks:
  - BUY against DOWN trend
  - SELL against UP trend
  - LOW volatility
  - RANGE trend
  - consolidation
  - configured loss streak block
- The loop does not force trades just because it is demo. The goal is to avoid poisoning memory with low-quality entries.

## MemPalace memory design

The project uses MemPalace ideas as the trading memory architecture:

- Raw verbatim journals should be preserved, not compressed into summaries too early.
- Memory is structured with wings, halls, and rooms.
- Memory is used as wake-up context before decisions, not only as an archive.
- First-class memory areas:
  - `symbol:*`
  - `execution`
  - `research`
  - `risk`
- Important hall types:
  - `hall_events`
  - `hall_discoveries`
  - `hall_advice`
  - `hall_facts`
  - `hall_preferences`
- Important intelligence outputs:
  - winner rooms
  - danger rooms
  - confidence calibration
  - lane scoreboard
  - shadow to live promotion hints

## Daily MiMo analyst design

MiMo Pro is intended as a once-per-day analyst, not the hot trading-loop policy head.

Expected role:

- Read the daily brief.
- Summarize winner rooms and danger rooms.
- Detect confidence drift.
- Suggest promote/demote actions.
- Store advice into memory.
- Do not directly mutate live policy without review.

Relevant scripts:

```powershell
.\scripts\get-daily-analyst-packet.ps1
.\scripts\run-daily-analyst.ps1
```

## Dexter edge audit state

Mempalace now includes a read-only Dexter auditor. It is designed to help Dexter without interfering with it:

- Code: `D:\Mempalac_AI\trading_ai\dexter_edge_audit.py`
- Runner: `D:\Mempalac_AI\scripts\run-dexter-edge-audit.ps1`
- Console entry point: `mempalace-dexter-edge-audit`
- Local report path: `D:\Mempalac_AI\reports\DEXTER_EDGE_AUDIT.md`
- `reports/` is ignored and should not be pushed because it contains private trading performance and edge evidence.

Run the current audit again:

```powershell
cd D:\Mempalac_AI
.\scripts\run-dexter-edge-audit.ps1 -TradeExport "D:\path\to\ctrader_trade_export.xlsx" -MaxEnvKeys 360
```

The auditor reads:

- Dexter cTrader SQLite DB in read-only mode
- Dexter backtest SQLite DB in read-only mode
- Optional cTrader XLSX trade export
- Dexter `.env.local` in sanitized mode only

Sanitization rules:

- Do not print tokens, passwords, client secrets, account identifiers, private keys, Telegram IDs, webhook URLs, or raw API payloads.
- Only trading behavior keys are shown, such as risk, lot/volume, fibo, XAU, BTC, canary, gates, thresholds, sessions, and live/demo flags.
- Do not copy full `.env.local` into chat or commits.

Latest audit generated locally at `D:\Mempalac_AI\reports\DEXTER_EDGE_AUDIT.md`.

Important findings from the latest audit:

- Broker-side export had 277 closed trades, total PnL about `+112.16 USD`, win rate about `38.6%`, and max drawdown about `-155.20 USD`.
- `XAUUSD buy` in the export was weak: 181 trades, about `-53.83 USD`, win rate about `34.3%`.
- `XAUUSD sell` in the export was strong: 17 trades, about `+89.96 USD`, win rate about `58.8%`.
- `BTCUSD buy` in the export was strong: 27 trades, about `+72.29 USD`, win rate about `66.7%`.
- Bad export days: `2026-04-07` about `-132.16 USD`, `2026-04-09` about `-55.82 USD`.
- Good export day: `2026-04-08` about `+110.78 USD`.
- Dexter DB shows the strongest immediate red flag is `fibo_xauusd` short: 22 trades, about `-111.25 USD`, win rate `0.0%`.
- Dexter DB shows `fibo_xauusd` long was positive, so do not kill the whole fibo family blindly. Quarantine/review the short side first.
- BTC quantity `0.05` appears explained by Dexter `.env.local` value `CTRADER_DEFAULT_VOLUME_SYMBOL_OVERRIDES=BTCUSD=5`; the worker uses `fixed_volume` before risk-based sizing.
- This means BTC volume increase was not proven to be AI auto-learning. It was likely a fixed symbol override that happened to perform well in that sample.

Safe Dexter improvement plan:

- Keep Dexter as executor and proven strategy host.
- Use Mempalace as read-only memory/evidence/governance first.
- Protect winner lanes before removing bad lanes.
- Candidate protect lanes include `xauusd_scheduled:canary` short/long, `scalp_xauusd:fss:canary` long, and `scalp_btcusd:canary` short.
- Candidate quarantine lanes include `fibo_xauusd` short, weak XAU canary base lanes, and selected `pb/bs/td` lanes only after human approval.
- Do not patch Dexter live logic until the user explicitly approves a branch/rollback plan.

## Current behavior observed

Recent live-demo loop behavior:

- The system is running and repeatedly evaluating XAUUSD.
- It has not opened a current position.
- Recent decisions are HOLD.
- Many HOLD decisions are correct because of `RANGE`, `LOW`, or consolidation hard filters.
- Some directional `UP_MEDIUM` or `DOWN_MEDIUM` regimes reached the LLM, but qwen still chose HOLD because pattern/memory evidence was thin.
- Current memory has notes but no closed trade rows in the current Chroma store, so PatternBook has not yet learned real win/loss statistics.

## Unresolved issues / next engineering tasks

1. Local qwen sometimes writes reasons like `risk_state can_trade is false` even when runtime risk is not halted. Add stricter decision validation or include compact risk JSON in a harder-to-misread format.
2. The model often chooses HOLD in clear UP/DOWN MEDIUM regimes because memory/pattern sample size is thin. Decide whether bootstrap exploration should allow very small demo orders after structure and risk gates pass.
3. Native cTrader quote/capture route still needs cTrader support response for `app_auth_failed / CANT_ROUTE_REQUEST`.
4. Build a Windows Scheduled Task or service wrapper if this PC should auto-resume Mempalace after reboot.
5. For VM deployment, keep Mempalace and Dexter as separate repos/processes/config paths. Do not share `data`, logs, runtime state, or strategy memory.
6. Add a dashboard panel showing:
   - current pre-LLM veto reason
   - whether LLM was called this cycle
   - model used
   - memory room guard outcome
   - current open position state
7. Add explicit broker position reconciliation so startup can detect externally-opened demo positions, not only positions opened and persisted by Mempalace.
8. Consider a shadow-trade mode for candidate lanes: record would-have-traded signals without broker execution until enough memory exists.
9. Add a Mempalace daily Dexter audit summary that stores `winner rooms`, `danger rooms`, and BTC/XAU sizing observations into MemPalace memory without changing Dexter.
10. If approved later, implement a Dexter-side hard gate for `fibo_xauusd` short only, preserving `fibo_xauusd` long and all currently profitable lanes.

## Safety rules for future agents

- Do not modify, reset, stop, or deploy Dexter unless explicitly requested.
- Do not read secrets aloud or paste `.env` contents into chat.
- Do not commit `.env`, token files, logs, Chroma DBs, account export spreadsheets, private keys, or local runtime data.
- Before commit, run:

```powershell
git status --short --ignored
git diff --cached --check
```

- Before push, scan staged files for known secrets and account identifiers.
- If changing execution logic, verify with demo first and check `runtime_state.json` for open positions.
