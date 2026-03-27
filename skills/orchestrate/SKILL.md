---
name: orchestrate
description: Run yourself in a loop with programmatic control via the Agent SDK.
---

# Orchestrate

## How to launch

1. Write `async def main(auto):`
2. Run: `orchestrate-run <file.py>`

**CRITICAL: After running `orchestrate-run`, STOP IMMEDIATELY. Do not monitor, check status, tail logs, or wait. The program runs in the background and sends remind messages through the queue. Your turn is done once the program starts. If you keep making tool calls after launch, you block the remind queue.**

## API

```python
result = await auto.remind(instruction)              # returns str
result = await auto.remind(instruction, schema={})   # returns dict
result = await auto.task(instruction, to="agent")    # dispatch to another agent
```

## Manage runs

```bash
orchestrate-run list
orchestrate-run status <id>
orchestrate-run log <id>
orchestrate-run stop <id>
```
