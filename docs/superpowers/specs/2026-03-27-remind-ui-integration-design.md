# Design: remind() UI Integration

## Overview

Make `remind()` calls from orchestrate programs visible in the UI. A remind() is equivalent to a user sending a message — it calls the same API endpoint, the agent responds normally, and the UI displays it. The only difference is the UI renders remind messages with a distinct bubble style instead of the user avatar.

## How it works

1. Program runs in background via `orchestrate-run`
2. Program calls `auto.remind("say hello")`
3. `remind()` makes `POST /agents/{agent_id}/runs` with `message="say hello"`, `session_id=<current session>`, and `source=remind`
4. API processes it like any user message — same agent session, same streaming
5. Agent responds with text, tool calls, etc. — all stored as a run record
6. UI polls or reloads session, sees the remind message + agent response
7. UI renders the remind message with a distinct "remind" bubble style

## Changes

### Core library (`src/orchestrate/core.py`)

`Auto.remind()` / `Auto.task()` needs to know the API URL and session ID so it can POST to the API endpoint instead of making direct SDK calls. Add optional `api_url` and `session_id` params to `Auto.__init__()`.

When `api_url` is set, `remind()` sends an HTTP POST to `{api_url}/agents/orchestrator/runs` with FormData `{message, session_id, source: "remind", stream: "false"}`. Returns the response content.

When `api_url` is not set, falls back to direct SDK calls (current behavior).

### API server (`api/server.py`)

- Accept optional `source` field in `POST /agents/{id}/runs` (default: `"user"`)
- Store `source` in the run record alongside `run_input` and `content`
- Return `source` in `GET /sessions/{id}/runs` response

### UI — copy from submodule

- Remove `ui/` git submodule
- Copy agent-ui source files directly into `ui/`
- Now we can modify the UI code

### UI — remind bubble

- Add `source?: string` to the `ChatMessage` type
- In session loader (`useSessionLoader.tsx`), pass `source` from run data to the user message
- In Messages rendering, if `message.source === "remind"`, render with a "remind" style (different icon/color) instead of the user avatar
- During streaming (`useAIStreamHandler.tsx`), no changes needed — remind messages appear on session reload/poll, not during the original stream

### UI — polling for new messages

- When a session is active, poll `GET /sessions/{id}/runs` periodically (every 3 seconds)
- If new runs appear that aren't in the current messages array, inject them (remind bubble + agent response)
- Stop polling when no programs are running (can check via a simple flag or just always poll)

## What this does NOT change

- `orchestrate-run` background execution — stays as-is
- Streaming protocol — no new events
- Agent SDK usage — remind() in non-API mode still uses direct SDK calls
- The `POST /agents/{id}/runs` endpoint logic — same handler, just accepts `source` field

## Example flow in UI

```
[user]     "run optimization loop 5 times"
[agent]    "I'll write the program and run it." + tool calls (Write, Bash)
           "Started run a3f1."

  ... program runs in background ...

[remind]   "Iteration 1: say hello"
[agent]    "Hello! 👋"
[remind]   "Iteration 2: say hello"
[agent]    "Hello again! 👋"
...
```
