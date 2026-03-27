# orchestrate

Thin wrapper over the Claude Agent SDK for coordinating multiple Claude agents.

## Install

```bash
pip install -e .
```

## Usage

```python
import asyncio
from orchestrate import Auto

async def main():
    auto = Auto()
    result = await auto.remind("Say hello")
    print(result)

asyncio.run(main())
```
