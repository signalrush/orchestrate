# Kanban UI — Architecture Reference

**Date**: 2026-03-30
**Status**: Implemented

---

## Data flow

```
POST /agents/{name}/message  (source=remind)
         │
         ▼
  server emits SSE events to /teams/default/events
         │
         ▼
  useAIStreamHandler  →  dispatches 'team-sse-event' CustomEvents
         │
         ▼
  useKanbanStream  (listens on window)
         │
         ├─ TaskCreated  → prepend to tasks[]
         ├─ TaskStarted  → update status to 'running', set started_at, run_id
         ├─ TaskCompleted → update status to 'completed', set summary, elapsed_secs
         └─ TaskFailed   → update status to 'failed', set error
         │
         ▼
  Zustand store (tasks[], selectedTask, activeTab)
         │
         ▼
  KanbanView → KanbanColumn → TaskCard
```

`useKanbanStream` is mounted in the root layout and runs for the lifetime of the page.

---

## SSE events

Only messages with `source=remind` generate Kanban events. UI-originated messages (`source=ui`) are excluded.

| Event | Trigger | Key fields |
|---|---|---|
| `TaskCreated` | `POST /agents/{name}/message` with `source=remind` | `task_id`, `agent_name`, `title` (≤80 chars), `source`, `session_id`, `created_at` |
| `TaskStarted` | Task dequeued and execution begins | `task_id`, `run_id`, `started_at` |
| `TaskCompleted` | Agent returns a result | `task_id`, `run_id`, `summary` (first 200 chars), `elapsed_secs`, `completed_at` |
| `TaskFailed` | Exception during agent execution | `task_id`, `run_id`, `error`, `failed_at` |

`task_id` is a UUID assigned at enqueue time. For non-`remind` messages, `task_id` is `None` and no Kanban events are emitted.

---

## Zustand store (kanban fields)

```typescript
tasks: KanbanTask[]                                        // all tasks, newest first
setTasks: (tasks | (prev) => tasks) => void               // supports functional updater
activeTab: 'chat' | 'kanban'                              // which tab is visible
setActiveTab: (tab) => void
selectedTask: KanbanTask | null                            // card currently open in chat panel
setSelectedTask: (task | null) => void
```

State is persisted via `zustand/middleware` (persist) — tasks survive page refresh.

---

## Columns

`KanbanView` partitions `tasks[]` into four filtered lists:

| Column | Status filter | Header colour | Empty state |
|---|---|---|---|
| Backlog | `queued` | muted | "No tasks queued" |
| In Progress | `running` | blue | "Waiting for agents…" (spinning icon) |
| Failed | `failed` | red | "No failures" |
| Done | `completed` | green | "No completed work yet" |

Each column shows a task count badge. Columns have a fixed minimum width (180px) and flex-grow to fill available space.

---

## TaskCard

### Display

| Row | Content | Condition |
|---|---|---|
| Top | Agent name pill (primary/10) + elapsed timer + chevron | always |
| Middle | Task title (2-line clamp by default) | always |
| Summary | Coloured dot + monospace truncated text | `running` or `completed` and `summary` present |
| Error | Red error text (2-line clamp) | `failed` and `error` present |
| Expanded footer | `id`, `created`, `run` in monofont | when expanded |
| Bottom | Status pill + source label | always |

The running timer counts up live (1s interval). On completion the server-computed `elapsed_secs` replaces it.

Cards animate in with a fade + translateY on mount, staggered by 30ms × index.

### Click behaviour

Click toggles `expanded` (local state) **and** calls `onSelect(task)`. These are independent — expand controls the card detail view; select controls the chat panel.

Selected card: `border-primary` ring. Deselected: `border-border`.

---

## Chat panel (right side of KanbanView)

Fixed-width panel, always visible alongside the columns.

**Default width**: 320px
**Resize range**: 240px – 600px (drag handle on left edge)

### Header

- No task selected: "CHAT" label (muted uppercase)
- Task selected: agent name pill + task title truncated + × close button

### Agent switching on card click

When `selectedTask` changes in the store, `KanbanView` fires a `useEffect` that:

1. Saves current `agentId`/`teamId`/`dbId` query params (only on first selection per session)
2. Resolves `db_id` from `agents[]` by matching `agent_name`
3. Updates URL query params: `agent=`, `team=null`, `db_id=`, `session=selectedTask.session_id`
4. Clears messages (`setMessages([])`) and calls `getSession(...)` to reload the agent's conversation

Clicking × (deselect) restores the saved params and clears the session.

The `ChatArea` component reads the current query params to determine which agent/session to display — the kanban view reuses it without modification.

---

## Tab bar

Two tabs: **Chat** and **Kanban**.

The Kanban tab shows a badge with `tasks.length` when non-zero (total across all statuses). Badge disappears when the list is empty.

Active tab: bottom border + foreground text. Inactive: muted text.

`activeTab` is stored in Zustand. `page.tsx` renders `<ChatArea />` or `<KanbanView />` based on the active tab, with `<TabBar />` above both.
