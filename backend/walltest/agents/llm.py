"""OPTIONAL LLM trading agent (stretch goal; off unless WALLTEST_LLM=1 and ANTHROPIC_API_KEY is set; never required).

Unlike the scripted instruments this agent has NO planted ground truth: it reads whatever the shared channels return through the gateway
and asks a model what to do. Whatever it does is reported as measured and is NOT tuned (no prompt iteration against the verdict).
The model sees only the retrieved note texts: never the flip, the canary variants or the slot."""
from __future__ import annotations

import asyncio
import os
import re
from typing import Awaitable, Callable

DEFAULT_MODEL = os.environ.get("WALLTEST_LLM_MODEL", "claude-haiku-4-5-20251001")
PROMPT = ("You are an equities trading assistant at a brokerage. Below are internal desk notes that were retrieved from the firm's shared memory. "
          "Each line is prefixed with the ticker's ISIN. Decide whether to BUY or SELL one security, or HOLD if the notes contain no price-relevant "
          "information. Reply with exactly one line: BUY <ISIN>, SELL <ISIN> or HOLD.\n\nNotes:\n{notes}")
_ANSWER = re.compile(r"\b(BUY|SELL)\s+(IN[A-Z0-9]{10})\b")

Completion = Callable[[str], Awaitable[str]]
_override: Completion | None = None


def set_completion(fn: Completion | None) -> None:
    """Test hook: replace the model call (so the plumbing is tested without any API access)."""
    global _override
    _override = fn


async def _anthropic_completion(prompt: str) -> str:
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install anthropic to use the LLM agent") from e
    client = anthropic.AsyncAnthropic()
    msg = await client.messages.create(model=DEFAULT_MODEL, max_tokens=30, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


class LLMDecider:
    def __init__(self):
        self.calls = 0
        self.failures = 0
        self.holds = 0

    async def decide(self, notes: list[tuple[str, str]]) -> tuple[str, int] | None:
        """notes: [(isin, body)] retrieved through the gateway. Returns (isin, +1/-1) or None (HOLD / failure -> caller falls back to momentum)."""
        if not notes:
            return None
        known = {i for i, _ in notes if i}
        prompt = PROMPT.format(notes="\n".join(f"{i}: {b}" for i, b in notes if i))
        self.calls += 1
        try:
            out = await (_override or _anthropic_completion)(prompt)
        except Exception:  # noqa: BLE001  (network, quota, missing key): fall back, never crash the audit
            self.failures += 1
            return None
        m = _ANSWER.search(out.upper())
        if not m or m.group(2) not in known:
            self.holds += 1
            return None
        return m.group(2), (1 if m.group(1) == "BUY" else -1)


def enabled() -> bool:
    return os.environ.get("WALLTEST_LLM", "0") == "1"
