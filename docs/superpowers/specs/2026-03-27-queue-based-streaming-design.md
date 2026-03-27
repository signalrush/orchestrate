# Design: Queue-based streaming

## Overview

One queue per session. One open stream per session. Everything — user messages, remind calls, program signals — goes through the queue in FIFO order. The user is never blocked from typing.

## Architecture

```
UI ←── stream (yields events) ←── queue processor ←── session queue
                                                        ↑         ↑
                                              remind() POST   user POST
                                         /sessions/{id}/message
```

## Components

### 1. Session queue

An `asyncio.Queue` per session, created when the first message opens a stream. Each item is a dict:

```python
{"message": "...", "source": "user"|"remind", "type": "message"}
{"type": "program-done"}
```

### 2. Stream endpoint (existing, modified)

`POST /agents/{id}/runs` — creates the stream and the queue. The generator:
1. Emits RunStarted
2. Processes the initial message (SDK query, yields events)
3. Loops: pulls next item from queue, processes it, yields events
4. On "program-done" item or timeout: emits RunCompleted, closes stream

### 3. Message endpoint (new)

`POST /sessions/{id}/message` — lightweight. Accepts `message` and `source` (user or remind). Pushes to the session queue. Returns immediately with `{"status": "queued"}`.

Used by:
- `Auto._remind_via_api()` with `source=remind`
- UI when user sends a message during an active stream

### 4. Queue processor

Inside the stream generator. For each queue item:
- If `source == "remind"`: yield a RunContent event with `source: "remind"` (creates remind bubble in UI), then run SDK query and yield response events
- If `source == "user"`: yield a RunContent event with `source: "user"` (creates user bubble in UI), then run SDK query and yield response events
- If `type == "program-done"`: break the loop

### 5. Stream handler (UI)

When a RunContent event arrives:
- If `source == "remind"`: create a remind bubble + new agent bubble
- If `source == "user"`: create a user bubble + new agent bubble
- If no source (normal): append to current agent bubble (existing behavior)

### 6. Input handling (UI)

- Input is **never disabled** — remove `isStreaming` check from send button
- When user sends a message:
  - If an active stream exists for the session: POST to `/sessions/{id}/message` with `source=user`
  - If no active stream: POST to `/agents/{id}/runs` (creates new stream)

## API changes

### Modified: `POST /agents/{id}/runs`

Same signature. But the generator now:
1. Creates session queue
2. Processes initial message
3. Loops on queue until done signal or timeout
4. Cleans up queue on exit

### New: `POST /sessions/{id}/message`

```
POST /sessions/{id}/message
FormData:
  message: string
  source: "user" | "remind"

Response: {"status": "queued"}
```

Pushes to session queue. Returns immediately.

### Modified: `POST /sessions/{id}/program-done`

Pushes `{"type": "program-done"}` to the queue instead of directly putting None.

### Removed: `POST /sessions/{id}/program-start`

No longer needed — the queue processor loops until done or timeout. No registration required.

## Core library changes

### `Auto._remind_via_api()`

Change from POSTing to `/agents/{id}/runs` to POSTing to `/sessions/{id}/message` with `source=remind`. Since the response comes through the stream (not the POST response), `_remind_via_api` needs to wait for the result.

Options:
- POST includes a `response_event` asyncio.Event that the queue processor sets when done
- Or: the message endpoint blocks until the queue processor finishes handling it (sync response)

Simplest: the `/sessions/{id}/message` endpoint blocks until processing is complete and returns the response content. The queue processor signals completion via a per-message future.

```python
# Queue item includes a future for the response
item = {"message": msg, "source": "remind", "future": asyncio.Future()}
queue.put(item)
result = await item["future"]  # blocks until processor is done
return {"content": result}
```

### CLI changes

- Remove `program-start` call (no longer needed)
- Keep `program-done` call

## UI changes

### Stream handler (`useAIStreamHandler.tsx`)

On RunContent with `source == "remind"`:
- Push a remind message bubble
- Push an empty agent bubble
- Subsequent RunContent (no source) appends to the agent bubble

On RunContent with `source == "user"`:
- Push a user message bubble
- Push an empty agent bubble

### Input (`ChatInput` or `useAIStreamHandler`)

- Never disable input based on `isStreaming`
- When submitting during active stream: POST to `/sessions/{id}/message` with `source=user`
- When submitting without active stream: POST to `/agents/{id}/runs` (existing behavior)

## What this does NOT include

- Cancellation — user can't cancel a running program from the UI
- Priority — messages are strictly FIFO
- Multiple concurrent programs per session

## Example timeline

```
[user]   "run optimization loop"     ← POST /agents/{id}/runs (opens stream)
[agent]  writes program, runs it     ← SDK query events streamed
[remind] "iteration 1: try lr=0.01"  ← POST /sessions/{id}/message (from program)
[agent]  "Edited train.py, loss=0.5" ← SDK query events streamed
[user]   "what's the best so far?"   ← POST /sessions/{id}/message (from UI)
[agent]  "Best loss is 0.5 from..."  ← SDK query events streamed
[remind] "iteration 2: try lr=0.001" ← from program
[agent]  "Loss improved to 0.3!"     ← streamed
         program-done               ← stream closes
```
