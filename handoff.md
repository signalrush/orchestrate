# Handoff: orchestrate

## What is this

`orchestrate` is a Python library that wraps the Claude Agent SDK to coordinate multiple Claude agents. It replaces the fragile stop-hook IPC system used by `auto` with direct in-process SDK calls.

Three layers:
```
ui/ (agent-ui, React)  ←→  api/server.py (FastAPI :7777)  ←→  orchestrate/ (Auto class → Agent SDK)
```

## Current state

### Done
- **orchestrate library** (`orchestrate/core.py`, `orchestrate/state.py`) — fully working, 18/18 tests pass
  - `Auto` class with `remind()`, `task()`, `agent()`
  - Each agent accumulates its own SDK session
  - `remind()` = `task(to="self")` — same implementation
  - Schema support (appends JSON instructions to prompt, parses response)
  - State persistence (`from orchestrate import state`)
- **REST API** (`api/server.py`) — fully working, tested via curl
  - `GET /health`, `GET /agents`, `GET /sessions`, `GET /sessions/{id}/runs`
  - `POST /agents/{id}/runs` — streaming JSON (RunStarted → RunContent → RunCompleted)
  - `DELETE /sessions/{id}`
  - Auto-loads OAuth token from `~/.claude/.credentials.json`
  - CORS enabled
- **agent-ui** (`ui/`) — git submodule of https://github.com/agno-agi/agent-ui
- **E2E tests passed** — 7/7 with OAuth token (hello, schema, named agent, session accumulation, concurrent agents, state, multi-agent flow)

### NOT done — your job
1. **Launch the full stack and verify visually**
   ```bash
   # Terminal 1: API
   cd ~/orchestrate && uvicorn api.server:app --port 7777 --host 0.0.0.0

   # Terminal 2: UI
   cd ~/orchestrate/ui && npm install && npm run dev -- --port 3000
   ```
   Then open http://localhost:3000, set endpoint to `http://localhost:7777`, select the "Orchestrate Agent", and send a message.

2. **Fix any UI↔API issues** — the streaming format may need tweaks:
   - agent-ui expects concatenated JSON chunks (no newlines between them)
   - Content in `RunContent` is cumulative (full text so far, not delta)
   - `session_id` is returned in `RunStarted` event
   - Requests use `FormData` not JSON

3. **Visual verification** — take a screenshot or use a headless browser to confirm the chat works

## Architecture

```python
# orchestrate/core.py — the whole library
class Auto:
    _sessions: dict[str, dict]  # agent_name -> {session_id, cwd}

    async def remind(instruction, schema=None):  # = task(to="self")
    async def task(instruction, to, schema=None):  # sends to named agent
    def agent(name, cwd=None):  # declare agent (optional)
```

```python
# api/server.py — REST bridge
POST /agents/{id}/runs  →  query(prompt=message, options=..., resume=session_id)
                        →  stream RunStarted/RunContent/RunCompleted JSON
```

## Auth

OAuth token loaded automatically from `~/.claude/.credentials.json`:
```python
os.environ["ANTHROPIC_API_KEY"] = creds["claudeAiOauth"]["accessToken"]
```

If no credentials file, set `ANTHROPIC_API_KEY` env var manually.

## Key files

| File | Purpose |
|------|---------|
| `orchestrate/core.py` | Auto class (~125 lines) |
| `orchestrate/state.py` | Persistent key-value store |
| `orchestrate/__init__.py` | Exports Auto, state |
| `api/server.py` | FastAPI REST server (~200 lines) |
| `ui/` | agent-ui (git submodule) |
| `tests/` | 18 unit tests |
| `examples/` | Smoke tests (hello, multi_agent, schema, state, e2e_oauth) |
| `docs/superpowers/specs/` | Design spec |
| `docs/superpowers/plans/` | Implementation plan |

## Run tests

```bash
cd ~/orchestrate
pip install -e ".[dev]"
pytest tests/ -v  # 18 tests, no SDK needed (mocked)

# Integration (needs ANTHROPIC_API_KEY or OAuth creds):
python examples/e2e_oauth.py  # 7 end-to-end tests
```

## Known issues

- `pyproject.toml` editable install may fail — install deps directly: `pip install claude-agent-sdk fastapi uvicorn python-multipart`
- No Chrome/headless browser on current machine for visual testing
- API streams full cumulative text in RunContent — agent-ui deduplicates by stripping already-seen prefix
- Tool call events (`ToolCallStarted`/`ToolCallCompleted`) emit but may need field name adjustments for agent-ui's exact expected format
