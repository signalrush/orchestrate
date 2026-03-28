# Team SSE Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-agent SSE queues with a single team stream. All events tagged with `agent_name`, one SSE connection per browser.

**Architecture:** One `TEAM_SSE` asyncio.Queue replaces `AGENT_SSE` dict. All workers push to it. Frontend opens one persistent SSE connection on page load, filters events by currently viewed agent.

**Tech Stack:** Python (FastAPI, asyncio), TypeScript (React)

---

## File Structure

```
api/
  server.py                              # (MODIFY) TEAM_SSE replaces AGENT_SSE
ui/src/
  hooks/useTeamStream.tsx                 # (CREATE) persistent SSE connection hook
  hooks/useAIStreamHandler.tsx            # (MODIFY) receive events from team stream
  components/chat/ChatArea/ChatInput/
    ChatInput.tsx                         # (MODIFY) fire-and-forget POST
```

---

### Task 1: Server — replace AGENT_SSE with TEAM_SSE

**Files:**
- Modify: `api/server.py`

- [ ] **Step 1: Replace AGENT_SSE with TEAM_SSE**

Change:
```python
AGENT_SSE: dict[str, asyncio.Queue] = {}    # name → output Queue
```
To:
```python
TEAM_SSE: asyncio.Queue = asyncio.Queue()   # single team stream
```

- [ ] **Step 2: Update `_emit_agent` to push to TEAM_SSE**

Change:
```python
def _emit_agent(agent_name: str, event: dict):
    """Push event to the agent's SSE output channel."""
    sse = AGENT_SSE.get(agent_name)
    if sse:
        sse.put_nowait(json.dumps(event))
```
To:
```python
def _emit(event: dict):
    """Push event to the team SSE stream."""
    TEAM_SSE.put_nowait(json.dumps(event))
```

- [ ] **Step 3: Update all `_emit_agent(agent_name, {...})` calls to `_emit({..., "agent_name": agent_name})`**

Every call to `_emit_agent` in the file needs to:
1. Change function name to `_emit`
2. Remove `agent_name` as first argument
3. Add `"agent_name": agent_name` inside the event dict

There are calls in: `_process_agent_message` (RunContent, ToolCallStarted), `_agent_worker` (MessageDequeued, RunContent source marker, RunError), `post_agent_message` (MessageQueued), `register_agent` (AgentRegistered), `post_message` (MessageQueued).

- [ ] **Step 4: Update `_ensure_agent_worker` — remove AGENT_SSE creation**

Remove:
```python
        if agent_name not in AGENT_SSE:
            AGENT_SSE[agent_name] = asyncio.Queue()
```

- [ ] **Step 5: Add `GET /teams/default/events` endpoint**

```python
@app.get("/teams/default/events")
async def team_events():
    """Persistent SSE stream for the team. All agent events flow here."""
    async def generate():
        while True:
            event_str = await TEAM_SSE.get()
            yield event_str
    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 6: Update `POST /agents/{agent_name}/runs` — return team stream instead of agent stream**

Change the `generate()` function to read from `TEAM_SSE` instead of `AGENT_SSE.get(agent_name)`:

```python
    async def generate():
        yield json.dumps({
            "event": "RunStarted",
            "session_id": session_id,
            "run_id": run_id,
            "agent_name": agent_name,
            "content_type": "text/plain",
            "created_at": now,
        })

        while True:
            event_str = await TEAM_SSE.get()
            yield event_str
    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 7: Update `GET /agents/{agent_name}/events` — return team stream**

```python
@app.get("/agents/{agent_name}/events")
async def agent_events(agent_name: str):
    """SSE stream — returns team stream (all events, frontend filters)."""
    async def generate():
        while True:
            event_str = await TEAM_SSE.get()
            yield event_str
    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 8: Update `delete_agent` — remove AGENT_SSE cleanup**

Remove:
```python
    AGENT_SSE.pop(agent_name, None)
```

- [ ] **Step 9: Commit**

```bash
git add -f api/server.py
git commit -m "feat: replace per-agent SSE with single team stream"
```

---

### Task 2: Frontend — persistent team SSE connection

**Files:**
- Create: `ui/src/hooks/useTeamStream.tsx`
- Modify: `ui/src/hooks/useAIStreamHandler.tsx`
- Modify: `ui/src/components/chat/ChatArea/ChatInput/ChatInput.tsx`

- [ ] **Step 1: Create `useTeamStream` hook**

This hook opens a persistent SSE connection on page load and dispatches events to the store.

```typescript
// ui/src/hooks/useTeamStream.tsx
import { useEffect, useRef } from 'react'
import { useStore } from '@/store'
import { constructEndpointUrl } from '@/lib/constructEndpointUrl'
import { useQueryState } from 'nuqs'

export default function useTeamStream() {
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const setMessages = useStore((state) => state.setMessages)
  const setPendingQueue = useStore((state) => state.setPendingQueue)
  const setSessionsData = useStore((state) => state.setSessionsData)
  const [sessionId, setSessionId] = useQueryState('session')
  const sessionIdRef = useRef(sessionId)
  sessionIdRef.current = sessionId
  const readerRef = useRef<ReadableStreamDefaultReader | null>(null)

  useEffect(() => {
    if (!selectedEndpoint) return

    const endpointUrl = constructEndpointUrl(selectedEndpoint)
    let cancelled = false

    async function connect() {
      try {
        const resp = await fetch(`${endpointUrl}/teams/default/events`)
        if (!resp.body) return
        const reader = resp.body.getReader()
        readerRef.current = reader
        const decoder = new TextDecoder()
        let buffer = ''

        while (!cancelled) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })

          // Extract complete JSON objects from buffer
          let startIdx = buffer.indexOf('{')
          while (startIdx >= 0) {
            let depth = 0
            let endIdx = -1
            for (let i = startIdx; i < buffer.length; i++) {
              if (buffer[i] === '{') depth++
              else if (buffer[i] === '}') {
                depth--
                if (depth === 0) { endIdx = i; break }
              }
            }
            if (endIdx === -1) break
            const jsonStr = buffer.slice(startIdx, endIdx + 1)
            buffer = buffer.slice(endIdx + 1)
            try {
              const event = JSON.parse(jsonStr)
              // Dispatch event to the appropriate handler
              window.dispatchEvent(new CustomEvent('team-sse-event', { detail: event }))
            } catch {}
            startIdx = buffer.indexOf('{')
          }
        }
      } catch {
        // Reconnect after delay
        if (!cancelled) setTimeout(connect, 3000)
      }
    }

    connect()

    return () => {
      cancelled = true
      readerRef.current?.cancel()
    }
  }, [selectedEndpoint])
}
```

- [ ] **Step 2: Wire up `useTeamStream` in the app**

In `ui/src/components/chat/ChatArea/MessageArea.tsx` (or a top-level layout component), add:

```typescript
import useTeamStream from '@/hooks/useTeamStream'

const MessageArea = () => {
  useTeamStream()  // Opens persistent SSE on mount
  // ... rest of component
```

- [ ] **Step 3: Update `useAIStreamHandler` to listen for team SSE events**

The current `useAIStreamHandler` processes events from `streamResponse` (a per-message fetch stream). Change it to also listen for `team-sse-event` custom events from the window.

Add a `useEffect` that listens for `team-sse-event` and processes each event through the existing `onChunk` logic. The `onChunk` handler should filter events by `agent_name` matching the currently viewed session's agent.

In the `handleStreamResponse` callback, instead of opening a stream via `streamResponse`, just fire-and-forget the POST to `/agents/{name}/message`. The team SSE delivers events.

- [ ] **Step 4: Update `ChatInput` — fire-and-forget POST**

The current `ChatInput` always calls `handleStreamResponse`. Change it to:

```typescript
const handleSubmit = async () => {
    if (!inputMessage.trim()) return
    const currentMessage = inputMessage
    setInputMessage('')

    try {
      const endpointUrl = constructEndpointUrl(selectedEndpoint)
      if (sessionId) {
        // Existing session: fire-and-forget to agent message endpoint
        const formData = new FormData()
        formData.append('message', currentMessage)
        formData.append('source', 'user')
        fetch(`${endpointUrl}/agents/orchestrator/message`, {
          method: 'POST',
          body: formData,
        }).catch(() => {})
      } else {
        // New session: POST to /agents/orchestrator/runs (creates session + pushes message)
        // But DON'T read the stream — team SSE handles events
        const formData = new FormData()
        formData.append('message', currentMessage)
        formData.append('stream', 'false')
        const resp = await fetch(`${endpointUrl}/agents/orchestrator/runs`, {
          method: 'POST',
          body: formData,
        })
        // The response includes session_id — set it
        // Actually: team SSE will deliver RunStarted with session_id
        // So we just fire and let the team stream handle it
      }
    } catch (error) {
      toast.error(...)
    }
}
```

- [ ] **Step 5: Commit**

```bash
git add -f ui/src/hooks/useTeamStream.tsx ui/src/hooks/useAIStreamHandler.tsx ui/src/components/chat/ChatArea/ChatInput/ChatInput.tsx ui/src/components/chat/ChatArea/MessageArea.tsx
git commit -m "feat: frontend uses persistent team SSE stream"
```

---

### Task 3: Test end-to-end

- [ ] **Step 1: Reinstall and restart**

```bash
pip install -e .
kill $(lsof -ti:7777); sleep 1
PYTHONPATH=/path/to/orchestrate uvicorn api.server:app --port 7777 --host 0.0.0.0
```

- [ ] **Step 2: API test — team stream delivers events**

```bash
# In terminal 1: open team stream
curl -s http://localhost:7777/teams/default/events &

# In terminal 2: send a message
curl -s -X POST http://localhost:7777/agents/orchestrator/message -F 'message=hi' -F 'source=user'

# Terminal 1 should show: MessageQueued, MessageDequeued, RunContent (source marker), RunContent (response)
```

- [ ] **Step 3: UI test — send message, verify bubbles appear**

Navigate to `http://localhost:3000/?agent=orchestrator`, send a message, verify response appears.

- [ ] **Step 4: Orchestrate test — spawn agents, verify events on team stream**

Send orchestrate program that spawns agents. Verify all events (from orchestrator + sub-agents) come through the single team stream.

- [ ] **Step 5: Session switch test**

Click different sessions in sidebar. Verify history loads and new events filter correctly.

- [ ] **Step 6: Commit fixes**

```bash
git add -A
git commit -m "test: verify team SSE stream end-to-end"
```
