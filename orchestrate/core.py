"""orchestrate core — Auto class and helpers."""

import json
import re
from typing import Any


def _parse_json(text: str, schema: dict) -> dict:
    """Extract JSON object from response text. Lenient parsing.

    Tries in order:
    1. Direct json.loads on stripped text
    2. Extract from markdown ```json ... ``` fences
    3. Find first {...} substring

    Raises ValueError if no valid JSON found.
    """
    text = text.strip()
    if not text:
        raise ValueError("No valid JSON found in empty response")

    # Try 1: direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # Try 2: markdown fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            obj = json.loads(fence_match.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # Try 3: find first { ... }
    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[brace_start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    break

    raise ValueError(f"No valid JSON found in response: {text[:200]}")


class Auto:
    """Stub Auto class — full implementation in a later task."""
    pass
