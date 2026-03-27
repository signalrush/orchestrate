# Remind UI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make remind() calls from orchestrate programs visible in the UI by having them POST to the same API endpoint as user messages, with a `source` field to distinguish them visually.

**Architecture:** remind() calls `POST /agents/{id}/runs` with `source=remind`. API stores source in run records. UI adds a "remind" role to ChatMessage and renders it with a distinct bubble style. UI polls for new runs to pick up remind messages in real-time.

**Tech Stack:** Python (FastAPI), TypeScript (Next.js/React), agent-ui

---

## File Structure

```
api/
  server.py                              # (MODIFY) accept source field, store in runs
src/orchestrate/
  core.py                               # (MODIFY) add api_url mode to Auto
ui/
  src/types/os.ts                        # (MODIFY) add remind role to ChatMessage
  src/components/chat/ChatArea/Messages/
    Messages.tsx                         # (MODIFY) render remind messages
    MessageItem.tsx                      # (MODIFY) add RemindMessage component
  src/hooks/useSessionLoader.tsx         # (MODIFY) pass source from run to message
  src/hooks/useAIStreamHandler.tsx       # (MODIFY) pass source from streaming events
  src/hooks/useSessionPolling.tsx        # (CREATE) poll for new runs
  src/components/chat/ChatArea/ChatArea.tsx  # (MODIFY) use polling hook
```

---

### Task 1: Copy agent-ui out of submodule

**Files:**
- Remove: `.gitmodules` entry for `ui`
- Modify: `ui/` — convert from submodule to regular directory

- [ ] **Step 1: Remove submodule and copy files**

```bash
cd /Users/tianhaowu/orchestrate
git submodule deinit -f ui
git rm -f ui
rm -rf .git/modules/ui
```

- [ ] **Step 2: Clone agent-ui fresh and copy into ui/**

```bash
git clone https://github.com/agno-agi/agent-ui /tmp/agent-ui-copy
cp -r /tmp/agent-ui-copy/ ui/
rm -rf ui/.git
rm -rf /tmp/agent-ui-copy
```

- [ ] **Step 3: Verify UI still works**

```bash
cd ui && npm install && npm run dev -- --port 3000 &
sleep 10
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
# Expected: 200
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: copy agent-ui from submodule to regular directory"
```

---

### Task 2: API — accept source field in runs

**Files:**
- Modify: `api/server.py:138-251`

- [ ] **Step 1: Add `source` parameter to the run endpoint**

In `api/server.py`, modify the `run_agent` function signature to accept `source`:

```python
@app.post("/agents/{agent_id}/runs")
async def run_agent(
    agent_id: str,
    message: str = Form(...),
    stream: str = Form("true"),
    session_id: str = Form(""),
    source: str = Form("user"),
):
```

- [ ] **Step 2: Include source in the run record**

Change the run record stored in `RUNS` (around line 243):

```python
        RUNS.setdefault(session_id, []).append({
            "run_input": message,
            "content": accumulated_text,
            "tools": tools_used,
            "created_at": now,
            "source": source,
        })
```

- [ ] **Step 3: Include source in streaming events**

Add `"source": source` to the RunStarted event (around line 156):

```python
        yield json.dumps({
            "event": "RunStarted",
            "session_id": session_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "content_type": "text/plain",
            "created_at": now,
            "source": source,
        })
```

- [ ] **Step 4: Test with curl**

```bash
# Normal user message
curl -s -X POST http://localhost:7777/agents/orchestrator/runs \
  -F "message=hello" -F "stream=false" -F "session_id=test1" | python3 -m json.tool

# Remind message
curl -s -X POST http://localhost:7777/agents/orchestrator/runs \
  -F "message=say hello" -F "stream=false" -F "session_id=test1" -F "source=remind" | python3 -m json.tool

# Check runs have source field
curl -s 'http://localhost:7777/sessions/test1/runs?type=agent' | python3 -m json.tool
# Expected: each run has "source": "user" or "source": "remind"
```

- [ ] **Step 5: Update test**

Add to `tests/test_api.py`:

```python
@pytest.mark.asyncio
async def test_run_stores_source_field(client):
    """Run records should include source field."""
    sid = "test-source"
    SESSIONS[sid] = {
        "session_id": sid, "session_name": "Test",
        "agent_id": "orchestrator", "created_at": 1000, "updated_at": 1000,
    }
    RUNS[sid] = [
        {"run_input": "hello", "content": "hi", "tools": [], "created_at": 1000, "source": "user"},
        {"run_input": "remind msg", "content": "ok", "tools": [], "created_at": 1001, "source": "remind"},
    ]
    resp = await client.get(f"/sessions/{sid}/runs", params={"type": "agent"})
    runs = resp.json()
    assert runs[0]["source"] == "user"
    assert runs[1]["source"] == "remind"
```

- [ ] **Step 6: Run tests and commit**

```bash
pytest tests/test_api.py -v
git add api/server.py tests/test_api.py
git commit -m "feat: accept source field in run endpoint"
```

---

### Task 3: Core library — add API mode to Auto

**Files:**
- Modify: `src/orchestrate/core.py`

- [ ] **Step 1: Add api_url and session_id params to Auto.__init__**

```python
class Auto:
    def __init__(self, cwd: str | None = None, model: str = "claude-sonnet-4-6",
                 api_url: str | None = None, session_id: str | None = None):
        self._sessions: dict[str, dict] = {}
        self._cwd = cwd or os.getcwd()
        self._model = model
        self._api_url = api_url
        self._session_id = session_id
```

- [ ] **Step 2: Add HTTP-based remind in task()**

When `api_url` is set, POST to the API instead of calling the SDK directly. Add at the top of `task()`:

```python
    async def task(self, instruction: str, to: str, schema: dict | None = None) -> str | dict:
        """Send instruction to a named agent. Accumulates session context."""
        # API mode: POST to the API endpoint (used by orchestrate-run programs)
        if self._api_url and to == "self":
            return await self._remind_via_api(instruction, schema)

        # SDK mode: direct Agent SDK call (original behavior)
        if to not in self._sessions:
            self.agent(to)
        # ... rest of existing code unchanged
```

- [ ] **Step 3: Implement _remind_via_api**

Add this method to the Auto class:

```python
    async def _remind_via_api(self, instruction: str, schema: dict | None = None) -> str | dict:
        """Send remind via HTTP POST to the API server."""
        import urllib.request
        import urllib.parse

        prompt = instruction
        if schema:
            schema_desc = json.dumps(schema, indent=2)
            prompt += f"\n\nRespond with a JSON object with these keys and types:\n{schema_desc}"

        data = urllib.parse.urlencode({
            "message": prompt,
            "stream": "false",
            "session_id": self._session_id or "",
            "source": "remind",
        }).encode()

        req = urllib.request.Request(
            f"{self._api_url}/agents/orchestrator/runs",
            data=data,
            method="POST",
        )

        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode()

        # Non-streaming response: parse the accumulated JSON events
        # Find the last RunCompleted event to get the content
        result_text = ""
        for line in body.split("}{"):
            # Reconstruct JSON objects from concatenated stream
            chunk = line
            if not chunk.startswith("{"):
                chunk = "{" + chunk
            if not chunk.endswith("}"):
                chunk = chunk + "}"
            try:
                event = json.loads(chunk)
                if event.get("event") == "RunCompleted":
                    result_text = event.get("content", "")
            except json.JSONDecodeError:
                continue

        print(f"[remind] {result_text[:200]}", flush=True)

        if schema:
            return _parse_json(result_text, schema)
        return result_text
```

- [ ] **Step 4: Update cli.py to pass api_url when available**

In `src/orchestrate/cli.py`, in the `_exec_program` function, check for an environment variable to enable API mode:

```python
def _exec_program(file_path: str, run_id: str, run_dir_path: str):
    """Execute a user program (called in background subprocess)."""
    try:
        spec = importlib.util.spec_from_file_location("user_program", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        main_fn = getattr(module, "main", None)
        if main_fn is None:
            raise RuntimeError(f"No main() function found in {file_path}")

        if not inspect.iscoroutinefunction(main_fn):
            raise RuntimeError("main() must be async (async def main)")

        # Use API mode if ORCHESTRATE_API_URL is set
        api_url = os.environ.get("ORCHESTRATE_API_URL")
        session_id = os.environ.get("ORCHESTRATE_SESSION_ID")
        auto = Auto(api_url=api_url, session_id=session_id)
        asyncio.run(main_fn(auto))
```

- [ ] **Step 5: Commit**

```bash
git add src/orchestrate/core.py src/orchestrate/cli.py
git commit -m "feat: add API mode to Auto for remind-via-HTTP"
```

---

### Task 4: UI — add remind role and bubble

**Files:**
- Modify: `ui/src/types/os.ts:197-198`
- Modify: `ui/src/components/chat/ChatArea/Messages/MessageItem.tsx`
- Modify: `ui/src/components/chat/ChatArea/Messages/Messages.tsx`

- [ ] **Step 1: Add remind role to ChatMessage type**

In `ui/src/types/os.ts`, change the role union:

```typescript
export interface ChatMessage {
  role: 'user' | 'agent' | 'system' | 'tool' | 'remind'
  content: string
  // ... rest unchanged
}
```

- [ ] **Step 2: Add RemindMessage component**

In `ui/src/components/chat/ChatArea/Messages/MessageItem.tsx`, add after UserMessage:

```typescript
const RemindMessage = memo(({ message }: MessageProps) => {
  return (
    <div className="flex items-start gap-4 pt-4 text-start max-md:break-words">
      <div className="flex-shrink-0">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent/20 text-xs font-bold text-accent">
          R
        </div>
      </div>
      <div className="text-md rounded-lg font-geist text-secondary italic">
        {message.content}
      </div>
    </div>
  )
})
RemindMessage.displayName = 'RemindMessage'
```

Export it alongside the others:

```typescript
export { AgentMessage, UserMessage, RemindMessage }
```

- [ ] **Step 3: Render remind messages in Messages.tsx**

In `ui/src/components/chat/ChatArea/Messages/Messages.tsx`, update the import:

```typescript
import { AgentMessage, UserMessage, RemindMessage } from './MessageItem'
```

Update the render logic in the Messages component:

```typescript
      {messages.map((message, index) => {
        const key = `${message.role}-${message.created_at}-${index}`
        const isLastMessage = index === messages.length - 1

        if (message.role === 'agent') {
          return (
            <AgentMessageWrapper
              key={key}
              message={message}
              isLastMessage={isLastMessage}
            />
          )
        }
        if (message.role === 'remind') {
          return <RemindMessage key={key} message={message} />
        }
        return <UserMessage key={key} message={message} />
      })}
```

- [ ] **Step 4: Verify UI renders correctly**

Start the UI, open a session that has remind messages, verify the remind bubble shows with the "R" icon and italic text.

- [ ] **Step 5: Commit**

```bash
git add ui/src/types/os.ts ui/src/components/chat/ChatArea/Messages/
git commit -m "feat: add remind message bubble to UI"
```

---

### Task 5: UI — session loader passes source to messages

**Files:**
- Modify: `ui/src/hooks/useSessionLoader.tsx:84-133`

- [ ] **Step 1: Use source from run data to set role**

In `useSessionLoader.tsx`, update the message construction in the `flatMap` callback. Change the user message creation (around line 89):

```typescript
            const messagesFor = response.flatMap((run) => {
              const filteredMessages: ChatMessage[] = []

              if (run) {
                filteredMessages.push({
                  role: run.source === 'remind' ? 'remind' : 'user',
                  content: run.run_input ?? '',
                  created_at: run.created_at
                })
              }
```

This is the only change needed — when loading session history, runs with `source: "remind"` create remind bubbles instead of user bubbles.

- [ ] **Step 2: Commit**

```bash
git add ui/src/hooks/useSessionLoader.tsx
git commit -m "feat: session loader uses source field for remind role"
```

---

### Task 6: UI — poll for new runs

**Files:**
- Create: `ui/src/hooks/useSessionPolling.tsx`
- Modify: `ui/src/components/chat/ChatArea/ChatArea.tsx`

- [ ] **Step 1: Find ChatArea.tsx**

```bash
find ui/src -name "ChatArea.tsx" -path "*/ChatArea/*"
```

- [ ] **Step 2: Create polling hook**

Create `ui/src/hooks/useSessionPolling.tsx`:

```typescript
import { useCallback, useEffect, useRef } from 'react'
import { useQueryState } from 'nuqs'
import { getSessionAPI } from '@/api/os'
import { useStore } from '@/store'
import { ChatMessage, ToolCall, ReasoningMessage } from '@/types/os'
import { constructEndpointUrl } from '@/lib/constructEndpointUrl'
import { getJsonMarkdown } from '@/lib/utils'

const POLL_INTERVAL = 3000

const useSessionPolling = () => {
  const [sessionId] = useQueryState('session')
  const [dbId] = useQueryState('db_id')
  const selectedEndpoint = useStore((state) => state.selectedEndpoint)
  const authToken = useStore((state) => state.authToken)
  const messages = useStore((state) => state.messages)
  const setMessages = useStore((state) => state.setMessages)
  const isStreaming = useStore((state) => state.isStreaming)
  const lastRunCount = useRef(0)

  const poll = useCallback(async () => {
    if (!selectedEndpoint || !sessionId || isStreaming) return

    try {
      const endpointUrl = constructEndpointUrl(selectedEndpoint)
      const response = await getSessionAPI(
        endpointUrl,
        'agent',
        sessionId,
        dbId ?? undefined,
        authToken ?? undefined
      )

      if (!Array.isArray(response)) return

      const currentRunCount = response.length
      if (currentRunCount <= lastRunCount.current) return

      // New runs appeared — rebuild messages from all runs
      const newMessages: ChatMessage[] = response.flatMap((run: any) => {
        const msgs: ChatMessage[] = []

        if (run) {
          msgs.push({
            role: run.source === 'remind' ? 'remind' : 'user',
            content: run.run_input ?? '',
            created_at: run.created_at
          })
        }

        if (run) {
          const toolCalls = [
            ...(run.tools ?? []),
            ...(run.extra_data?.reasoning_messages ?? []).reduce(
              (acc: ToolCall[], msg: ReasoningMessage) => {
                if (msg.role === 'tool') {
                  acc.push({
                    role: msg.role,
                    content: msg.content,
                    tool_call_id: msg.tool_call_id ?? '',
                    tool_name: msg.tool_name ?? '',
                    tool_args: msg.tool_args ?? {},
                    tool_call_error: msg.tool_call_error ?? false,
                    metrics: msg.metrics ?? { time: 0 },
                    created_at: msg.created_at ?? Math.floor(Date.now() / 1000)
                  })
                }
                return acc
              },
              []
            )
          ]

          msgs.push({
            role: 'agent',
            content: typeof run.content === 'string'
              ? run.content
              : (typeof run.content !== 'undefined' ? getJsonMarkdown(run.content) : ''),
            tool_calls: toolCalls.length > 0 ? toolCalls : undefined,
            extra_data: run.extra_data,
            images: run.images,
            videos: run.videos,
            audio: run.audio,
            response_audio: run.response_audio,
            created_at: run.created_at
          })
        }

        return msgs
      })

      lastRunCount.current = currentRunCount
      setMessages(newMessages)
    } catch {
      // Silently ignore polling errors
    }
  }, [selectedEndpoint, sessionId, dbId, authToken, isStreaming, setMessages])

  useEffect(() => {
    lastRunCount.current = Math.floor(messages.length / 2)
  }, [sessionId])

  useEffect(() => {
    const interval = setInterval(poll, POLL_INTERVAL)
    return () => clearInterval(interval)
  }, [poll])
}

export default useSessionPolling
```

- [ ] **Step 3: Use polling hook in ChatArea**

Find the main ChatArea component and add the hook. The exact file path needs to be found in step 1, but add:

```typescript
import useSessionPolling from '@/hooks/useSessionPolling'

// Inside the component:
useSessionPolling()
```

- [ ] **Step 4: Commit**

```bash
git add ui/src/hooks/useSessionPolling.tsx ui/src/components/chat/ChatArea/
git commit -m "feat: poll for new runs to show remind messages in real-time"
```

---

### Task 7: End-to-end test

- [ ] **Step 1: Start API and UI**

```bash
pkill -f "uvicorn api.server" 2>/dev/null
pkill -f "next dev" 2>/dev/null
sleep 1
PYTHONPATH=/Users/tianhaowu/orchestrate uvicorn api.server:app --port 7777 --host 0.0.0.0 &
cd ui && npm run dev -- --port 3000 &
sleep 10
```

- [ ] **Step 2: Send a user message to create a session**

```bash
SESSION_ID=$(uuidgen)
curl -s -X POST http://localhost:7777/agents/orchestrator/runs \
  -F "message=hello" -F "stream=true" -F "session_id=$SESSION_ID" > /dev/null
echo "Session: $SESSION_ID"
```

- [ ] **Step 3: Send a remind message to the same session**

```bash
curl -s -X POST http://localhost:7777/agents/orchestrator/runs \
  -F "message=say hello back" -F "stream=true" -F "session_id=$SESSION_ID" -F "source=remind" > /dev/null
```

- [ ] **Step 4: Verify runs have correct source**

```bash
curl -s "http://localhost:7777/sessions/$SESSION_ID/runs?type=agent" | python3 -m json.tool
# Expected: first run has source "user", second has source "remind"
```

- [ ] **Step 5: Open UI and verify visual rendering**

Open `http://localhost:3000`, select the session. Verify:
- First message shows with user icon
- Remind message shows with "R" icon and italic text
- Agent responses show normally after each

- [ ] **Step 6: Test with orchestrate-run program**

```bash
cat > /tmp/test_remind_ui.py << 'EOF'
import asyncio

async def main(auto):
    for i in range(3):
        await asyncio.sleep(2)
        await auto.remind(f"Iteration {i+1}: say hello")
EOF

ORCHESTRATE_API_URL=http://localhost:7777 ORCHESTRATE_SESSION_ID=$SESSION_ID orchestrate-run /tmp/test_remind_ui.py
sleep 15
orchestrate-run status
```

Verify: the UI session now shows 3 remind bubbles with agent responses.

- [ ] **Step 7: Commit any fixes**

```bash
git add -A
git commit -m "test: verify remind UI integration end-to-end"
```
