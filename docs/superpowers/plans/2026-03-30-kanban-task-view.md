# Kanban Task View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real-time Kanban board tab to the orchestrate UI that tracks agent tasks (queued → running → done/failed) driven by 4 new SSE events.

**Architecture:** The backend threads a `task_id` through every `post_agent_message` / `post_message` queue item and emits `TaskCreated → TaskStarted → TaskCompleted | TaskFailed` lifecycle events. The frontend adds a `tasks` array to the Zustand store, a `useKanbanStream` hook that listens on `team-sse-event`, and a `KanbanView` component with 4 columns rendered behind a `TabBar`. `useTeamStream` is lifted to `page.tsx` so the SSE connection stays open regardless of which tab is active.

**Tech Stack:** Python/FastAPI (server.py), React/Next.js, TypeScript, Zustand, Tailwind CSS, `cn()` utility.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `api/server.py` | Emit 4 new task lifecycle events; thread `task_id` through queue items |
| Create | `ui/src/types/kanban.ts` | `KanbanTask` type + `TaskStatus` union |
| Modify | `ui/src/store.ts` | Add `tasks`/`setTasks` + `activeTab`/`setActiveTab` |
| Modify | `ui/src/hooks/useAIStreamHandler.tsx` | Handle `TaskCreated`/`TaskStarted` for `pendingQueue` (replaces old `MessageQueued`/`MessageDequeued`) |
| Create | `ui/src/hooks/useKanbanStream.ts` | Translate SSE task events → store updates |
| Create | `ui/src/components/kanban/TaskCard.tsx` | Single card: agent badge, title, elapsed timer |
| Create | `ui/src/components/kanban/KanbanColumn.tsx` | Column header + scrollable card list |
| Create | `ui/src/components/kanban/KanbanView.tsx` | 4-column grid; mounts `useKanbanStream` |
| Create | `ui/src/components/TabBar.tsx` | Chat / Kanban tab switcher |
| Modify | `ui/src/app/page.tsx` | Lift `useTeamStream` + `useKanbanStream` here; add `TabBar`; conditional view |
| Modify | `ui/src/components/chat/ChatArea/MessageArea.tsx` | Remove `useTeamStream()` call (now lives in page.tsx) |

---

## Task 1: Backend — task_id threading + 4 new SSE events

**Files:**
- Modify: `api/server.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api.py`:

```python
from unittest.mock import patch, AsyncMock
from api.server import AGENT_QUEUES, AGENT_WORKERS


@pytest.mark.asyncio
async def test_kanban_events_on_agent_message(client):
    """post_agent_message emits TaskCreated → TaskStarted → TaskCompleted with matching task_id."""
    import api.server as srv

    emitted: list[dict] = []
    original_emit = srv._emit
    srv._emit = lambda payload: emitted.append(dict(payload))

    # Register an agent via the API
    await client.post("/agents", json={
        "name": "kb-agent",
        "agent_id": "kb-agent",
        "model": {"name": "t", "model": "claude-3-haiku-20240307", "provider": "anthropic"},
    })
    emitted.clear()

    # Mock _process_agent_message to return immediately
    async def fake_process(msg, source, name, session_id, config, resume_id, run_id):
        return ("task done", None)

    with patch.object(srv, "_process_agent_message", fake_process):
        resp = await client.post(
            "/agents/kb-agent/message",
            data={"message": "analyze the codebase", "source": "remind"},
        )

    assert resp.status_code == 200
    event_names = [e["event"] for e in emitted]
    assert "TaskCreated" in event_names
    assert "TaskStarted" in event_names
    assert "TaskCompleted" in event_names

    created = next(e for e in emitted if e["event"] == "TaskCreated")
    completed = next(e for e in emitted if e["event"] == "TaskCompleted")
    assert "task_id" in created
    assert created["task_id"]  # non-empty string
    assert completed["task_id"] == created["task_id"]
    assert "elapsed_secs" in completed
    assert created["title"] == "analyze the codebase"

    # Cleanup
    srv._emit = original_emit
    AGENT_QUEUES.pop("kb-agent", None)
    worker = AGENT_WORKERS.pop("kb-agent", None)
    if worker and not worker.done():
        worker.cancel()
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /Users/tianhaowu/orchestrate
python -m pytest tests/test_api.py::test_kanban_events_on_agent_message -v
```

Expected: FAIL — `TaskCreated` not in event_names (events still emit `MessageQueued`)

- [ ] **Step 3: Thread task_id through post_agent_message**

In `api/server.py`, find the `post_agent_message` function (around line 425). Replace the `_emit({"event": "MessageQueued", ...})` block and the `AGENT_QUEUES[agent_name].put(...)` call:

```python
    # OLD — remove this block:
    # _emit({
    #     "event": "MessageQueued",
    #     "content": message,
    #     "source": source,
    #     "agent_name": agent_name,
    #     "session_id": session_id,
    #     "created_at": int(time.time()),
    # })
    # await AGENT_QUEUES[agent_name].put({
    #     "message": message,
    #     "source": source,
    #     "future": future,
    #     "session_id": session_id,
    # })

    # NEW — replace with:
    task_id = str(uuid.uuid4())
    _emit({
        "event": "TaskCreated",
        "task_id": task_id,
        "agent_name": agent_name,
        "session_id": session_id,
        "title": message[:80],
        "source": source,
        "created_at": int(time.time()),
    })
    await AGENT_QUEUES[agent_name].put({
        "message": message,
        "source": source,
        "future": future,
        "session_id": session_id,
        "task_id": task_id,
    })
```

- [ ] **Step 4: Thread task_id through post_message (session-based compat route)**

In `api/server.py`, find the `post_message` function (around line 1012). Apply the same replacement:

```python
    # OLD — remove:
    # _emit({
    #     "event": "MessageQueued",
    #     "content": message,
    #     "source": source,
    #     "agent_name": agent_name,
    #     "session_id": session_id,
    #     "created_at": int(time.time()),
    # })
    # await AGENT_QUEUES[agent_name].put({
    #     "message": message,
    #     "source": source,
    #     "future": future,
    #     "session_id": session_id,
    # })

    # NEW:
    task_id = str(uuid.uuid4())
    _emit({
        "event": "TaskCreated",
        "task_id": task_id,
        "agent_name": agent_name,
        "session_id": session_id,
        "title": message[:80],
        "source": source,
        "created_at": int(time.time()),
    })
    await AGENT_QUEUES[agent_name].put({
        "message": message,
        "source": source,
        "future": future,
        "session_id": session_id,
        "task_id": task_id,
    })
```

- [ ] **Step 5: Update _agent_worker to emit TaskStarted / TaskCompleted / TaskFailed**

In `api/server.py`, find `_agent_worker` (around line 253). After the line `session_id = item.get("session_id") or config.get("session_id", agent_name)`, add:

```python
            item_task_id = item.get("task_id")
            item_started_at = int(time.time())
```

Then replace the `_emit({"event": "MessageDequeued", ...})` block with:

```python
            # Emit TaskStarted for queued tasks; MessageDequeued for direct UI runs
            if item_task_id:
                _emit({
                    "event": "TaskStarted",
                    "task_id": item_task_id,
                    "run_id": item_run_id,
                    "agent_name": agent_name,
                    "session_id": session_id,
                    "title": item_message[:80],
                    "started_at": item_started_at,
                })
            else:
                _emit({
                    "event": "MessageDequeued",
                    "content": item_message,
                    "source": item_source,
                    "agent_name": agent_name,
                    "session_id": session_id,
                    "created_at": int(time.time()),
                })
```

Then after `item_future.set_result(response_text)`, add:

```python
                if item_task_id:
                    _emit({
                        "event": "TaskCompleted",
                        "task_id": item_task_id,
                        "run_id": item_run_id,
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "title": item_message[:80],
                        "summary": response_text[:200] if isinstance(response_text, str) else "",
                        "elapsed_secs": int(time.time()) - item_started_at,
                        "completed_at": int(time.time()),
                    })
```

Then in the `except Exception as e:` block, after the existing `_emit({"event": "RunError", ...})`, add:

```python
                if item_task_id:
                    _emit({
                        "event": "TaskFailed",
                        "task_id": item_task_id,
                        "run_id": item_run_id,
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "title": item_message[:80],
                        "error": str(e),
                        "failed_at": int(time.time()),
                    })
```

- [ ] **Step 6: Run the test — must pass**

```bash
cd /Users/tianhaowu/orchestrate
python -m pytest tests/test_api.py::test_kanban_events_on_agent_message -v
```

Expected: PASS

- [ ] **Step 7: Run full test suite to verify no regressions**

```bash
cd /Users/tianhaowu/orchestrate
python -m pytest tests/ -v --timeout=30
```

Expected: all previously passing tests still pass.

- [ ] **Step 8: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add api/server.py tests/test_api.py
git commit -m "feat: add task_id threading and TaskCreated/TaskStarted/TaskCompleted/TaskFailed SSE events"
```

---

## Task 2: Kanban types

**Files:**
- Create: `ui/src/types/kanban.ts`
- Test: `pnpm run typecheck` (no errors)

- [ ] **Step 1: Create ui/src/types/kanban.ts**

```typescript
export type TaskStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface KanbanTask {
  task_id: string
  agent_name: string
  /** Truncated task instruction (≤80 chars from server) */
  title: string
  source: string
  status: TaskStatus
  session_id: string
  created_at: number       // unix timestamp
  started_at?: number      // set on TaskStarted
  completed_at?: number    // set on TaskCompleted
  failed_at?: number       // set on TaskFailed
  elapsed_secs?: number    // stored elapsed from TaskCompleted
  summary?: string         // first 200 chars of agent response
  error?: string           // error message from TaskFailed
  run_id?: string
}
```

- [ ] **Step 2: Run typecheck**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add ui/src/types/kanban.ts
git commit -m "feat: add KanbanTask type"
```

---

## Task 3: Store additions (tasks + activeTab)

**Files:**
- Modify: `ui/src/store.ts`

- [ ] **Step 1: Add imports and new fields to the Store interface**

At the top of `ui/src/store.ts`, add the import:

```typescript
import { KanbanTask } from '@/types/kanban'
```

In the `Store` interface, add after `agentStatus: string` / `setAgentStatus`:

```typescript
  tasks: KanbanTask[]
  setTasks: (tasks: KanbanTask[] | ((prev: KanbanTask[]) => KanbanTask[])) => void
  activeTab: 'chat' | 'kanban'
  setActiveTab: (tab: 'chat' | 'kanban') => void
```

Also update the `pendingQueue` type to include optional `task_id` (used for matching on `TaskStarted`):

```typescript
  pendingQueue: { content: string; source: string; created_at: number; task_id?: string }[]
  setPendingQueue: (
    queue:
      | { content: string; source: string; created_at: number; task_id?: string }[]
      | ((prev: { content: string; source: string; created_at: number; task_id?: string }[]) => { content: string; source: string; created_at: number; task_id?: string }[])
  ) => void
```

- [ ] **Step 2: Add implementations inside create()**

Inside the `persist((set) => ({...}))` body, add after `setAgentStatus`:

```typescript
      tasks: [],
      setTasks: (tasks) =>
        set((state) => ({
          tasks: typeof tasks === 'function' ? tasks(state.tasks) : tasks,
        })),
      activeTab: 'chat',
      setActiveTab: (activeTab) => set(() => ({ activeTab })),
```

- [ ] **Step 3: Run typecheck**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run typecheck
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add ui/src/store.ts
git commit -m "feat: add tasks and activeTab to Zustand store"
```

---

## Task 4: Update useAIStreamHandler — handle TaskCreated/TaskStarted

**Files:**
- Modify: `ui/src/hooks/useAIStreamHandler.tsx`

The `pendingQueue` in the chat view is now driven by `TaskCreated` (queued) and `TaskStarted` (dequeued) instead of `MessageQueued`/`MessageDequeued`.

- [ ] **Step 1: Replace the MessageQueued handler with TaskCreated**

In `ui/src/hooks/useAIStreamHandler.tsx`, find the block:

```typescript
      } else if (
        (chunk.event as string) === 'MessageQueued'
      ) {
        // Server says a message entered the queue — add to pending display
        setPendingQueue((prev) => [...prev, {
          content: typeof chunk.content === 'string' ? chunk.content : '',
          source: (chunk as any).source || 'user',
          created_at: chunk.created_at ?? Math.floor(Date.now() / 1000),
        }])
```

Replace with:

```typescript
      } else if (
        (chunk.event as string) === 'TaskCreated'
      ) {
        // Agent task entered the queue — show in pending display
        setPendingQueue((prev) => [...prev, {
          content: (chunk as any).title || (typeof chunk.content === 'string' ? chunk.content : ''),
          source: (chunk as any).source || 'user',
          created_at: chunk.created_at ?? Math.floor(Date.now() / 1000),
          task_id: (chunk as any).task_id,
        }])
```

- [ ] **Step 2: Replace the MessageDequeued handler with TaskStarted**

Find:

```typescript
      } else if (
        (chunk.event as string) === 'MessageDequeued'
      ) {
        // Server says a message left the queue — remove from pending display
        const content = typeof chunk.content === 'string' ? chunk.content : ''
        const source = (chunk as any).source || 'user'
        setPendingQueue((prev) => {
          const idx = prev.findIndex((p) => p.content === content && p.source === source)
          if (idx >= 0) return [...prev.slice(0, idx), ...prev.slice(idx + 1)]
          return prev
        })
```

Replace with:

```typescript
      } else if (
        (chunk.event as string) === 'TaskStarted'
      ) {
        // Agent task left the queue — remove from pending display (match by task_id)
        const chunkTaskId = (chunk as any).task_id
        setPendingQueue((prev) => {
          if (chunkTaskId) {
            const idx = prev.findIndex((p) => p.task_id === chunkTaskId)
            if (idx >= 0) return [...prev.slice(0, idx), ...prev.slice(idx + 1)]
          }
          return prev
        })
```

- [ ] **Step 3: Run typecheck**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run typecheck
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add ui/src/hooks/useAIStreamHandler.tsx
git commit -m "feat: handle TaskCreated/TaskStarted for pendingQueue (replaces MessageQueued/MessageDequeued)"
```

---

## Task 5: useKanbanStream hook

**Files:**
- Create: `ui/src/hooks/useKanbanStream.ts`

This hook subscribes to the global `team-sse-event` DOM event (no session filter — Kanban shows all tasks across all agents) and updates the `tasks` store.

- [ ] **Step 1: Create ui/src/hooks/useKanbanStream.ts**

```typescript
import { useEffect } from 'react'
import { useStore } from '@/store'
import type { KanbanTask } from '@/types/kanban'

export default function useKanbanStream() {
  const setTasks = useStore((state) => state.setTasks)

  useEffect(() => {
    const handler = (e: Event) => {
      const chunk = (e as CustomEvent).detail
      const event = chunk.event as string

      if (event === 'TaskCreated') {
        const task: KanbanTask = {
          task_id: chunk.task_id,
          agent_name: chunk.agent_name,
          title: chunk.title,
          source: chunk.source,
          status: 'queued',
          session_id: chunk.session_id,
          created_at: chunk.created_at,
        }
        setTasks((prev) => [task, ...prev])
      } else if (event === 'TaskStarted') {
        setTasks((prev) =>
          prev.map((t) =>
            t.task_id === chunk.task_id
              ? { ...t, status: 'running', started_at: chunk.started_at, run_id: chunk.run_id }
              : t
          )
        )
      } else if (event === 'TaskCompleted') {
        setTasks((prev) =>
          prev.map((t) =>
            t.task_id === chunk.task_id
              ? {
                  ...t,
                  status: 'completed',
                  completed_at: chunk.completed_at,
                  elapsed_secs: chunk.elapsed_secs,
                  summary: chunk.summary,
                  run_id: chunk.run_id,
                }
              : t
          )
        )
      } else if (event === 'TaskFailed') {
        setTasks((prev) =>
          prev.map((t) =>
            t.task_id === chunk.task_id
              ? {
                  ...t,
                  status: 'failed',
                  failed_at: chunk.failed_at,
                  error: chunk.error,
                  run_id: chunk.run_id,
                }
              : t
          )
        )
      }
    }

    window.addEventListener('team-sse-event', handler)
    return () => window.removeEventListener('team-sse-event', handler)
  }, [setTasks])
}
```

- [ ] **Step 2: Run typecheck**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add ui/src/hooks/useKanbanStream.ts
git commit -m "feat: useKanbanStream hook — translates task SSE events to store updates"
```

---

## Task 6: TaskCard component

**Files:**
- Create: `ui/src/components/kanban/TaskCard.tsx`

Displays: agent name badge, task title (2-line truncation), elapsed timer (live for running, static for others), error text for failed tasks.

- [ ] **Step 1: Create ui/src/components/kanban/TaskCard.tsx**

```tsx
'use client'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import type { KanbanTask } from '@/types/kanban'

function formatElapsed(secs: number): string {
  if (secs < 60) return `${secs}s`
  return `${Math.floor(secs / 60)}m ${secs % 60}s`
}

const STATUS_PILL: Record<string, string> = {
  queued: 'bg-muted text-muted-foreground',
  running: 'bg-blue-500/15 text-blue-600 dark:text-blue-400',
  completed: 'bg-green-500/15 text-green-600 dark:text-green-400',
  failed: 'bg-destructive/15 text-destructive',
}

export default function TaskCard({ task }: { task: KanbanTask }) {
  const [elapsed, setElapsed] = useState<number>(() =>
    task.started_at ? Math.floor(Date.now() / 1000) - task.started_at : 0
  )

  useEffect(() => {
    if (task.status !== 'running' || !task.started_at) return
    const id = setInterval(() => {
      setElapsed(Math.floor(Date.now() / 1000) - task.started_at!)
    }, 1000)
    return () => clearInterval(id)
  }, [task.status, task.started_at])

  const elapsedDisplay =
    task.status === 'running'
      ? formatElapsed(elapsed)
      : task.elapsed_secs !== undefined
      ? formatElapsed(task.elapsed_secs)
      : null

  return (
    <div className="bg-card border rounded-lg p-3 space-y-2 text-sm shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono truncate max-w-[120px]">
          {task.agent_name}
        </span>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {task.status === 'running' && (
            <span className="block h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" />
          )}
          {elapsedDisplay && (
            <span className="text-xs text-muted-foreground tabular-nums">{elapsedDisplay}</span>
          )}
        </div>
      </div>

      <p className={cn('text-sm leading-snug line-clamp-2', task.status === 'failed' && 'text-muted-foreground')}>
        {task.title}
      </p>

      {task.status === 'failed' && task.error && (
        <p className="text-xs text-destructive line-clamp-2 break-words">{task.error}</p>
      )}

      <div className="flex items-center justify-between">
        <span className={cn('text-[10px] px-1.5 py-0.5 rounded-full font-medium', STATUS_PILL[task.status])}>
          {task.status}
        </span>
        <span className="text-[10px] text-muted-foreground">
          {task.source}
        </span>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Run typecheck**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add ui/src/components/kanban/TaskCard.tsx
git commit -m "feat: TaskCard component with live elapsed timer"
```

---

## Task 7: KanbanColumn component

**Files:**
- Create: `ui/src/components/kanban/KanbanColumn.tsx`

- [ ] **Step 1: Create ui/src/components/kanban/KanbanColumn.tsx**

```tsx
import { cn } from '@/lib/utils'
import type { KanbanTask, TaskStatus } from '@/types/kanban'
import TaskCard from './TaskCard'

const HEADER_STYLE: Record<TaskStatus, string> = {
  queued: 'text-muted-foreground',
  running: 'text-blue-600 dark:text-blue-400',
  completed: 'text-green-600 dark:text-green-400',
  failed: 'text-amber-600 dark:text-amber-400',
}

const HEADER_DOT: Record<TaskStatus, string> = {
  queued: 'bg-muted-foreground/40',
  running: 'bg-blue-500',
  completed: 'bg-green-500',
  failed: 'bg-amber-500',
}

interface KanbanColumnProps {
  title: string
  status: TaskStatus
  tasks: KanbanTask[]
}

export default function KanbanColumn({ title, status, tasks }: KanbanColumnProps) {
  return (
    <div className="flex flex-col min-w-[220px] max-w-[280px] flex-1">
      {/* Header */}
      <div className="flex items-center gap-2 px-1 pb-2 mb-1">
        <span className={cn('block h-2 w-2 rounded-full flex-shrink-0', HEADER_DOT[status])} />
        <span className={cn('text-xs font-semibold uppercase tracking-wide', HEADER_STYLE[status])}>
          {title}
        </span>
        <span className="ml-auto text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded-full">
          {tasks.length}
        </span>
      </div>

      {/* Card list */}
      <div className="flex-1 overflow-y-auto space-y-2 pr-0.5">
        {tasks.length === 0 ? (
          <div className="border border-dashed rounded-lg py-6 text-center text-xs text-muted-foreground">
            Empty
          </div>
        ) : (
          tasks.map((task) => <TaskCard key={task.task_id} task={task} />)
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Run typecheck**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add ui/src/components/kanban/KanbanColumn.tsx
git commit -m "feat: KanbanColumn component"
```

---

## Task 8: KanbanView component

**Files:**
- Create: `ui/src/components/kanban/KanbanView.tsx`

Renders all 4 columns, filters tasks from the store by status.

- [ ] **Step 1: Create ui/src/components/kanban/KanbanView.tsx**

```tsx
'use client'
import { useStore } from '@/store'
import KanbanColumn from './KanbanColumn'

export default function KanbanView() {
  const tasks = useStore((state) => state.tasks)

  const queued = tasks.filter((t) => t.status === 'queued')
  const running = tasks.filter((t) => t.status === 'running')
  const failed = tasks.filter((t) => t.status === 'failed')
  const completed = tasks.filter((t) => t.status === 'completed')

  return (
    <div className="flex flex-1 gap-4 p-4 overflow-x-auto overflow-y-hidden h-full">
      <KanbanColumn title="Backlog" status="queued" tasks={queued} />
      <KanbanColumn title="In Progress" status="running" tasks={running} />
      <KanbanColumn title="Review" status="failed" tasks={failed} />
      <KanbanColumn title="Done" status="completed" tasks={completed} />
    </div>
  )
}
```

- [ ] **Step 2: Run typecheck**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add ui/src/components/kanban/KanbanView.tsx
git commit -m "feat: KanbanView with 4 columns — Backlog, In Progress, Review, Done"
```

---

## Task 9: TabBar component

**Files:**
- Create: `ui/src/components/TabBar.tsx`

- [ ] **Step 1: Create ui/src/components/TabBar.tsx**

```tsx
'use client'
import { cn } from '@/lib/utils'
import { useStore } from '@/store'

export default function TabBar() {
  const activeTab = useStore((state) => state.activeTab)
  const setActiveTab = useStore((state) => state.setActiveTab)
  const tasks = useStore((state) => state.tasks)

  const tabs = [
    { id: 'chat' as const, label: 'Chat' },
    { id: 'kanban' as const, label: 'Kanban', badge: tasks.length || null },
  ]

  return (
    <div className="flex border-b border-border bg-background flex-shrink-0">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => setActiveTab(tab.id)}
          className={cn(
            'flex items-center gap-1.5 px-4 py-2 text-sm transition-colors',
            activeTab === tab.id
              ? 'border-b-2 border-foreground font-medium text-foreground'
              : 'text-muted-foreground hover:text-foreground'
          )}
        >
          {tab.label}
          {tab.badge !== null && (
            <span className="text-[10px] bg-muted px-1.5 py-0.5 rounded-full tabular-nums">
              {tab.badge}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Run typecheck**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add ui/src/components/TabBar.tsx
git commit -m "feat: TabBar component — Chat / Kanban switcher"
```

---

## Task 10: Wire page.tsx + remove useTeamStream from MessageArea

**Files:**
- Modify: `ui/src/app/page.tsx`
- Modify: `ui/src/components/chat/ChatArea/MessageArea.tsx`

`useTeamStream` must live in `page.tsx` so the SSE connection is maintained regardless of which tab is active. `useKanbanStream` also mounts here so tasks accumulate even when the Chat tab is shown.

- [ ] **Step 1: Lift useTeamStream out of MessageArea.tsx**

In `ui/src/components/chat/ChatArea/MessageArea.tsx`, remove the import and the call:

```tsx
// Remove this import:
// import useTeamStream from '@/hooks/useTeamStream'

// Remove this line from inside MessageArea():
// useTeamStream()
```

The file after the change should start with:

```tsx
'use client'

import { useStore } from '@/store'
import Messages from './Messages'
import ScrollToBottom from '@/components/chat/ChatArea/ScrollToBottom'
import { StickToBottom } from 'use-stick-to-bottom'
import useAIChatStreamHandler from '@/hooks/useAIStreamHandler'

const MessageArea = () => {
  useAIChatStreamHandler()
  // ... rest unchanged
```

- [ ] **Step 2: Update page.tsx to mount hooks + add TabBar + conditional view**

Replace the entire content of `ui/src/app/page.tsx` with:

```tsx
'use client'
import Sidebar from '@/components/chat/Sidebar/Sidebar'
import { ChatArea } from '@/components/chat/ChatArea'
import KanbanView from '@/components/kanban/KanbanView'
import TabBar from '@/components/TabBar'
import { Suspense } from 'react'
import { useStore } from '@/store'
import useTeamStream from '@/hooks/useTeamStream'
import useKanbanStream from '@/hooks/useKanbanStream'

function AppContent({ hasEnvToken, envToken }: { hasEnvToken: boolean; envToken: string }) {
  // Lift SSE connection here — stays open on both tabs
  useTeamStream()
  // Accumulate kanban tasks even while on Chat tab
  useKanbanStream()

  const activeTab = useStore((state) => state.activeTab)

  return (
    <div className="flex h-screen bg-background/80">
      <Sidebar hasEnvToken={hasEnvToken} envToken={envToken} />
      <div className="flex flex-col flex-1 min-w-0">
        <TabBar />
        {activeTab === 'chat' ? <ChatArea /> : <KanbanView />}
      </div>
    </div>
  )
}

export default function Home() {
  const hasEnvToken = !!process.env.NEXT_PUBLIC_OS_SECURITY_KEY
  const envToken = process.env.NEXT_PUBLIC_OS_SECURITY_KEY || ''
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <AppContent hasEnvToken={hasEnvToken} envToken={envToken} />
    </Suspense>
  )
}
```

> **Why `AppContent`?** Hooks can't be called at the top level of a server component boundary. The `'use client'` directive already makes this a client component, but extracting `AppContent` keeps the `Suspense` wrapper clean.

- [ ] **Step 3: Run typecheck**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run typecheck
```

Expected: 0 errors.

- [ ] **Step 4: Run lint**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run lint
```

Expected: 0 errors.

- [ ] **Step 5: Start the dev server and manually verify**

```bash
cd /Users/tianhaowu/orchestrate/ui
pnpm run dev
```

Open http://localhost:3000. Verify:
- Chat tab shows normal conversation UI (no regression)
- Kanban tab shows 4 empty columns: Backlog, In Progress, Review, Done
- Switching tabs doesn't reconnect SSE (network tab should show one persistent connection)

Then run a test program:
```python
# quick_test.py (in /Users/tianhaowu/orchestrate)
from orchestrate import Orchestrate

orch = Orchestrate()
orch.register_agent("test-worker", "You are a helpful agent.")
result = orch.run("test-worker", "Say hello in 3 words.")
print(result)
```
```bash
python quick_test.py
```

Verify in the browser:
1. Kanban badge on "Kanban" tab increments to 1
2. Card appears in Backlog immediately
3. Card moves to In Progress when worker starts
4. Card moves to Done with elapsed time when complete
5. Chat tab still receives messages normally

- [ ] **Step 6: Commit**

```bash
cd /Users/tianhaowu/orchestrate
git add ui/src/app/page.tsx ui/src/components/chat/ChatArea/MessageArea.tsx
git commit -m "feat: wire Kanban tab into page — lift useTeamStream, add TabBar, conditional KanbanView"
```

---

## Self-Review

**Spec coverage:**

| Requirement | Task |
|---|---|
| 4 columns: Backlog, In Progress, Review, Done | Task 8 (KanbanView) |
| Cards represent `orch.run()` tasks | Task 1 (TaskCreated via `post_agent_message`) |
| Cards show: agent name, instruction, status, elapsed | Task 6 (TaskCard) |
| Backlog when queued | Task 5 (useKanbanStream: `TaskCreated → 'queued'`) |
| In Progress when agent starts | Task 5 (useKanbanStream: `TaskStarted → 'running'`) |
| Done when complete | Task 5 (useKanbanStream: `TaskCompleted → 'completed'`) |
| Review column for errors | Task 5 (useKanbanStream: `TaskFailed → 'failed'`) |
| Real-time via SSE | Tasks 1+5 (new events + listener on `team-sse-event`) |
| Tab/view toggle | Tasks 9+10 (TabBar + conditional render) |
| No regression to chat | Task 10 (useTeamStream lifted; `useAIStreamHandler` unchanged) |

**Placeholder scan:** No TODOs or TBDs. All code blocks are complete.

**Type consistency:**
- `KanbanTask` defined in Task 2, used consistently in Tasks 5, 6, 7, 8
- `TaskStatus` union `'queued' | 'running' | 'completed' | 'failed'` used in `STATUS_PILL`, `HEADER_STYLE`, `HEADER_DOT`, `KanbanColumn` props — all match
- `setTasks` signature in store matches usage in `useKanbanStream`
- `task_id` field from backend matches `chunk.task_id` in `useKanbanStream` matches `KanbanTask.task_id` used as React `key`
- `activeTab: 'chat' | 'kanban'` in store matches `TabBar` and `page.tsx` comparisons
