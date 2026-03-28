# Multi-Agent Session Visibility

**Goal:** When a program creates agents via `orch.agent("coder")`, their sessions appear in the UI sidebar as first-class chats.

## Design

No new endpoints. No UI changes. One server-side fix: when `POST /agents` registers a new agent and creates its session, set `agent_id="orchestrator"` so the sidebar's `component_id=orchestrator` filter includes it. Set `session_name` to the agent name.

## Flow

1. User chats with orchestrator
2. Program calls `orch.agent("coder")` → `POST /agents` → server registers agent, creates session named "coder" with `agent_id="orchestrator"`
3. Sidebar refreshes → "coder" appears in session list
4. User clicks "coder" → loads that session's runs
5. User types in coder's chat → `/sessions/{id}/message` → coder's queue → coder's worker responds

## What changes

- `api/server.py` `register_agent` / `_ensure_agent_worker`: when creating the session, use `agent_id="orchestrator"` and `session_name=agent_name`

## What doesn't change

- UI sidebar, chat, input — all existing
- Session loading, run history — all existing
- Queue/worker/SSE — all existing
