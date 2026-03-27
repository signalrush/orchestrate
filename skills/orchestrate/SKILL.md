---
name: orchestrate
description: Run yourself in a loop with programmatic control via the Agent SDK. Use for long-running tasks like optimization, research, iterative improvement, multi-agent coordination, or any multi-step workflow where you need to repeat, branch, or track progress.
---

# Orchestrate

Write a Python program that drives agent execution. The program is your body — `auto.run()` is how you think. You write the loop, it keeps you alive.

A single program can efficiently coordinate 100+ agents — fan out work with `asyncio.gather`, manage worker pools with queues, and stay in control through structured `auto.run()` decisions. The only limit is how well you design the program.

## Launch

1. Write `async def main(auto):`
2. Run: `orchestrate-run <file.py>`

**CRITICAL: After `orchestrate-run`, STOP. Do not monitor, tail logs, or wait. Your turn is done. The program handles the rest.**

## Primitives

There is one primitive. Everything else is Python.

```python
# Talk to yourself — you see results, you steer decisions
result = await auto.run("analyze the test results")

# Get structured data back to drive program logic
result = await auto.run("decide next step", schema={"action": "str", "done": "bool"})

# Dispatch to another agent
code = await auto.run("implement feature X", to="coder")
```

Concurrency is just `asyncio`:

```python
# Parallel fan-out — independent work runs simultaneously
results = await asyncio.gather(
    auto.run("research approach A", to="researcher-1"),
    auto.run("research approach B", to="researcher-2"),
)

# Work queue — just asyncio.Queue
queue = asyncio.Queue()
```

## Writing effective programs

### Stay in the loop

You are the brain. The program is the clock. `auto.run()` is the nerve. If you don't call `auto.run()`, you're blind.

Bad — fire and forget, you have no idea what happened:
```python
async def main(auto):
    for task in tasks:
        await auto.run(task, to="worker")
```

Good — you see results, you steer:
```python
async def main(auto):
    for task in tasks:
        result = await auto.run(task, to="worker")
        review = await auto.run(f"Review this result: {result}. Is it good enough?",
                                schema={"approved": "bool", "feedback": "str"})
        if not review["approved"]:
            await auto.run(f"Fix based on feedback: {review['feedback']}", to="worker")
```

Best — you control the entire loop through structured decisions:
```python
async def main(auto):
    instruction = "Survey ~/tasks/queue/ and make a plan."
    while True:
        decision = await auto.run(instruction, schema={
            "done": "bool",
            "next_action": "str",
            "delegate_to": "str | null",
        })
        if decision["done"]:
            break
        if decision["delegate_to"]:
            result = await auto.run(decision["next_action"], to=decision["delegate_to"])
            instruction = f"Agent '{decision['delegate_to']}' returned: {result}\nWhat next?"
        else:
            instruction = decision["next_action"]
```

### Parallelize what's parallelizable

If tasks are independent, run them concurrently:
```python
async def main(auto):
    # Sequential — slow (each waits for the previous)
    r1 = await auto.run("fix bug A", to="coder")
    r2 = await auto.run("fix bug B", to="coder")
    r3 = await auto.run("fix bug C", to="coder")

    # Parallel — fast (all run at once on different agents)
    r1, r2, r3 = await asyncio.gather(
        auto.run("fix bug A", to="coder-1"),
        auto.run("fix bug B", to="coder-2"),
        auto.run("fix bug C", to="coder-3"),
    )
```

### Use schema to stay structured

Without schema, you get free text — hard to parse, unreliable for program logic.
With schema, you get structured data — drives decisions cleanly.

```python
# Free text — fragile
result = await auto.run("is the build passing?")
if "yes" in result.lower():  # brittle string matching
    ...

# Structured — reliable
result = await auto.run("check build status", schema={
    "passing": "bool",
    "failures": "int",
    "summary": "str",
})
if result["passing"]:  # clean
    ...
```

### Configure agents for different roles

```python
auto.agent("coder", cwd="/project")
auto.agent("reviewer", cwd="/project")

code = await auto.run("implement the feature", to="coder")
review = await auto.run(f"review this:\n{code}", to="reviewer")
```

## Manage runs

```bash
orchestrate-run list              # show all runs
orchestrate-run status <id>       # details + recent log
orchestrate-run log <id>          # show last 50 lines (non-blocking)
orchestrate-run stop <id>         # stop a run
orchestrate-run stop --all        # stop all runs
```
