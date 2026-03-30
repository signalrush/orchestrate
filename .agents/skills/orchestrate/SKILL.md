---
name: orchestrate
description: Run yourself in a loop with programmatic control via the Agent SDK. Use for long-running tasks like optimization, research, iterative improvement, multi-agent coordination, or any multi-step workflow where you need to repeat, branch, or track progress.
---

# Orchestrate

Write a Python program that drives agent execution. The program is your body — `orch.run()` is how you think. You write the loop, it keeps you alive.

A single program can efficiently coordinate 100+ agents — fan out work with `asyncio.gather`, manage worker pools with queues, and stay in control through structured `orch.run()` decisions. The only limit is how well you design the program.

## Launch

1. Write `async def main(orch):`
2. Run: `orchestrate-run <file.py>`

**CRITICAL: After `orchestrate-run`, STOP. Do not monitor, tail logs, or wait. Your turn is done. The program handles the rest.**

## Primitives

There is one primitive. Everything else is Python.

```python
# Talk to yourself — you see results, you steer decisions
result = await orch.run("analyze the test results")

# Get structured data back to drive program logic
result = await orch.run("decide next step", schema={"action": "str", "done": "bool"})

# Dispatch to another agent
code = await orch.run("implement feature X", to="coder")
```

Concurrency is just `asyncio`:

```python
# Parallel fan-out — independent work runs simultaneously
results = await asyncio.gather(
    orch.run("research approach A", to="researcher-1"),
    orch.run("research approach B", to="researcher-2"),
)

# Work queue — just asyncio.Queue
queue = asyncio.Queue()
```

## Writing effective programs

### Stay in the loop

You are the brain. The program is the clock. `orch.run()` is the nerve. If you don't call `orch.run()`, you're blind.

Bad — fire and forget, you have no idea what happened:
```python
async def main(orch):
    for task in tasks:
        await orch.run(task, to="worker")
```

Good — you see results, you steer:
```python
async def main(orch):
    for task in tasks:
        result = await orch.run(task, to="worker")
        review = await orch.run(f"Review this result: {result}. Is it good enough?",
                                schema={"approved": "bool", "feedback": "str"})
        if not review["approved"]:
            await orch.run(f"Fix based on feedback: {review['feedback']}", to="worker")
```

Best — you control the entire loop through structured decisions:
```python
async def main(orch):
    instruction = "Survey ~/tasks/queue/ and make a plan."
    while True:
        decision = await orch.run(instruction, schema={
            "done": "bool",
            "next_action": "str",
            "delegate_to": "str | null",
        })
        if decision["done"]:
            break
        if decision["delegate_to"]:
            result = await orch.run(decision["next_action"], to=decision["delegate_to"])
            instruction = f"Agent '{decision['delegate_to']}' returned: {result}\nWhat next?"
        else:
            instruction = decision["next_action"]
```

### Parallelize what's parallelizable

If tasks are independent, run them concurrently:
```python
async def main(orch):
    # Sequential — slow (each waits for the previous)
    r1 = await orch.run("fix bug A", to="coder")
    r2 = await orch.run("fix bug B", to="coder")
    r3 = await orch.run("fix bug C", to="coder")

    # Parallel — fast (all run at once on different agents)
    r1, r2, r3 = await asyncio.gather(
        orch.run("fix bug A", to="coder-1"),
        orch.run("fix bug B", to="coder-2"),
        orch.run("fix bug C", to="coder-3"),
    )
```

### Use schema to stay structured

Without schema, you get free text — hard to parse, unreliable for program logic.
With schema, you get structured data — drives decisions cleanly.

```python
# Free text — fragile
result = await orch.run("is the build passing?")
if "yes" in result.lower():  # brittle string matching
    ...

# Structured — reliable
result = await orch.run("check build status", schema={
    "passing": "bool",
    "failures": "int",
    "summary": "str",
})
if result["passing"]:  # clean
    ...
```

### Use context to chain agent work

Every `orch.run()` returns a `ContextResult` — auto-saved to the context store with a summary and a `.md` file. Pass results between agents with `context=`:

```python
# Step 1: researcher produces findings — auto-saved
c1 = await orch.run("research X", to="researcher", schema={"findings": "str"})

# Step 2: pass findings as context to implementer
c2 = await orch.run("implement based on research", to="coder", context=[c1])

# The coder sees:
# [Context from researcher (full output: ~/.orchestrate/context/86.md)]:
# <summary of findings>
#
# implement based on research
```

`ContextResult` behaves like a dict for schema fields, and `print(c1)` shows the summary:

```python
c1 = await orch.run("analyze", to="analyst", schema={"score": "int", "reason": "str"})
print(c1)              # prints the summary
print(c1["score"])     # 87
print(c1["reason"])    # "Good coverage but..."
print(c1.id)           # context entry ID
print(c1.file)         # ~/.orchestrate/context/42.md
```

Recall past results from the context store:

```python
past = await orch.recall(tags="research", agent="researcher", limit=5)
c3 = await orch.run("build on prior work", to="builder", context=past)
```

Fetch a single context entry by ID:

```python
entry = await orch.get_context("42")   # returns ContextResult or None
```

Pass context IDs directly (auto-fetched):

```python
# If you already know the IDs from a previous recall or search
await orch.run("build on this", to="builder", context=["42", "86"])
```

Pin important context so it always appears in recall:

```python
await orch.pin(c1)     # always included in recall results
await orch.unpin(c1)   # remove pin
```

### CRITICAL: Always validate context before passing

Context is the most important input to downstream agents. **Never blindly pass recall() results without inspecting them.** Bad context = bad output.

```python
# BAD — blindly passing whatever recall returns
past = await orch.recall(agent="researcher", limit=10)
await orch.run("build on this", to="builder", context=past)

# GOOD — inspect, filter, then pass
past = await orch.recall(agent="researcher", limit=10)
for c in past:
    print(f"  [{c.id}] {c.agent}: {c.summary[:80]}")

# Filter to only relevant entries
relevant = [c for c in past if "architecture" in c.summary.lower()]
print(f"Passing {len(relevant)} of {len(past)} entries")
await orch.run("build on this", to="builder", context=relevant)
```

Why this matters:
- `recall()` searches the **persistent store across all runs** — old/stale entries accumulate
- Passing irrelevant context wastes tokens and confuses the agent
- Always check: right count? right content? right timeframe?
- When writing orchestrate programs, hardcode known-good context IDs if you've already identified them during research

### Configure agents for different roles

```python
await orch.agent("coder", cwd="/project")
await orch.agent("reviewer", cwd="/project")

code = await orch.run("implement the feature", to="coder")
review = await orch.run(f"review this:\n{code}", to="reviewer")
```

## Manage runs

```bash
orchestrate-run list              # show all runs
orchestrate-run status <id>       # details + recent log
orchestrate-run log <id>          # show last 50 lines (non-blocking)
orchestrate-run stop <id>         # stop a run
orchestrate-run stop --all        # stop all runs
```
