# Mempalace Trader

`D:\Mempalac_AI` is now the intended root for the new `Mempalace_trader` repo. Dexter stays separate and read-only at `D:\dexter_pro_v3_fixed\dexter_pro_v3_fixed`.

## Design goals

- Keep execution and data isolated from Dexter.
- Reuse Dexter only as an external cTrader worker boundary when needed.
- Add MemPalace-style memory organization on top of the trading journals:
  - raw verbatim journals
  - `wing / hall / room` taxonomy
  - wake-up context for the current symbol/session
- Run cleanly from this PC as a local test server.

## Repo layout

- `trading_ai/`
  Python package for the trading loop, API, memory engine, and broker integrations.
- `scripts/`
  Windows-first bootstrap and local test-server commands.
- `docs/`
  Architecture and parallel-with-Dexter notes.

## Local setup

```powershell
cd D:\Mempalac_AI
scripts\bootstrap.ps1
```

Config resolution order:

1. repo root `.env`
2. fallback `trading_ai\.env`

The current local install still uses `trading_ai\.env`, so no secret migration was forced during this redesign.

## Run on this PC

API:

```powershell
cd D:\Mempalac_AI
scripts\start-api.ps1
```

Dry-run loop:

```powershell
cd D:\Mempalac_AI
scripts\start-loop.ps1
```

Start both in background:

```powershell
cd D:\Mempalac_AI
scripts\start-test-server.ps1
```

Health check:

```powershell
cd D:\Mempalac_AI
scripts\check-status.ps1
```

## API

- `GET /status`
- `POST /memory/search`
- `GET /memory/wakeup`
- `GET /memory/taxonomy`
- `GET /memory/intelligence`
- `GET /memory/room-guard`
- `GET /memory/tunnel`
- `GET /memory/daily-brief`
- `GET /memory/analyst-packet`
- `GET /memory/notes`
- `POST /memory/notes`

## Operator tools

- `scripts\add-memory-note.ps1`
  Add research, risk, execution, or analyst notes into MemPalace halls.
- `scripts\get-daily-analyst-packet.ps1`
  Export the full daily analyst packet for MiMo review.
- `scripts\run-daily-analyst.ps1`
  Run the MiMo analyst once and store the result into `hall_advice`.
- `scripts\test-memory-intelligence.ps1`
  Run an isolated temp-data self-test for winner/danger/opportunity rooms, room guards, notes, API packet endpoints, and promotion hints.

MiMo analyst config uses Xiaomi's OpenAI-compatible endpoint:

```env
MIMO_BASE_URL=https://api.xiaomimimo.com/v1
MIMO_MODEL=mimo-v2-pro
```

## Dexter boundary

- Dexter is not imported into this package.
- Live/demo quotes and orders can still go through Dexter's one-shot worker subprocess.
- Keep separate repos, separate venvs, separate `DATA_DIR`, separate logs, and separate ports.

See [docs/ARCHITECTURE.md](/D:/Mempalac_AI/docs/ARCHITECTURE.md) and [PARALLEL_WITH_DEXTER.md](/D:/Mempalac_AI/trading_ai/docs/PARALLEL_WITH_DEXTER.md).
