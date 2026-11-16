"""The model client every agent uses. Do not edit.

It talks to any OpenAI-compatible chat API. On event day the runtime points it at the
competition's gateway (MODEL_URL, MODEL_KEY, MODEL_NAME), which serves DeepSeek V4.1 Flash.
For practice, put your own provider in a .env file next to run_local.py, for example Groq:

    MODEL_URL=https://api.groq.com/openai/v1
    MODEL_KEY=gsk_...
    MODEL_NAME=<a Groq model id>

The client applies the event-day settings everywhere, so practice behaves like the real
thing: temperature 0, at most 150 output tokens per call, and at most 10 calls per case.
It also counts tokens and prices them at the event-day rates, so you can see what your agent
would cost against the budget.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

MAX_OUTPUT_TOKENS = 150
MAX_CALLS_PER_CASE = 10

# Event-day prices, US dollars per million tokens (DeepSeek V4.1 Flash on DeepInfra).
PRICE_INPUT = 0.14
PRICE_CACHED_INPUT = 0.004
PRICE_OUTPUT = 0.42


class ModelLimitError(Exception):
    """This case already used its 10 model calls."""


def load_dotenv(path: Path) -> None:
    """Reads KEY=VALUE lines into the environment, without overriding what is already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    seconds: float = 0.0

    @property
    def cost(self) -> float:
        uncached = self.input_tokens - self.cached_tokens
        return (uncached * PRICE_INPUT + self.cached_tokens * PRICE_CACHED_INPUT
                + self.output_tokens * PRICE_OUTPUT) / 1_000_000

    def add(self, other: "Usage") -> None:
        for name in ("calls", "input_tokens", "cached_tokens", "output_tokens",
                     "reasoning_tokens", "seconds"):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def to_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "input_tokens": self.input_tokens,
                "cached_tokens": self.cached_tokens, "output_tokens": self.output_tokens,
                "reasoning_tokens": self.reasoning_tokens, "seconds": round(self.seconds, 2),
                "cost_usd": round(self.cost, 6)}


@dataclass
class Model:
    url: str | None = None
    key: str | None = None
    name: str | None = None
    extra_body: dict[str, Any] | None = None
    client: Any = None  # anything with .chat.completions.create, e.g. a fake for tests
    total: Usage = field(default_factory=Usage)
    case: Usage = field(default_factory=Usage)
    per_case: dict[str, dict[str, Any]] = field(default_factory=dict)
    _case_id: str | None = None

    def __post_init__(self) -> None:
        self.url = self.url or os.environ.get("MODEL_URL")
        self.key = self.key or os.environ.get("MODEL_KEY")
        self.name = self.name or os.environ.get("MODEL_NAME")
        if self.extra_body is None and os.environ.get("MODEL_EXTRA_BODY"):
            self.extra_body = json.loads(os.environ["MODEL_EXTRA_BODY"])
        if self.client is None:
            if not (self.url and self.key and self.name):
                raise RuntimeError("set MODEL_URL, MODEL_KEY and MODEL_NAME (see .env.example)")
            # more retries help on rate-limited free tiers (Groq allows 8,000 tokens a minute)
            retries = int(os.environ.get("MODEL_MAX_RETRIES", "6"))
            self.client = OpenAI(base_url=self.url, api_key=self.key, max_retries=retries,
                                 timeout=40)

    # called by loop.py
    def start_case(self, case_id: str) -> None:
        self._end_case()
        self._case_id = case_id
        self.case = Usage()

    def _end_case(self) -> None:
        if self._case_id is not None:
            self.per_case[self._case_id] = self.case.to_dict()

    def finish(self) -> dict[str, Any]:
        self._end_case()
        self._case_id = None
        return self.total.to_dict()

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
             max_tokens: int = MAX_OUTPUT_TOKENS, **kwargs: Any):
        """One chat completion. Returns the assistant message (with .content and
        .tool_calls). Raises ModelLimitError after 10 calls in one case."""
        if self.case.calls >= MAX_CALLS_PER_CASE:
            raise ModelLimitError(f"no more than {MAX_CALLS_PER_CASE} model calls per case")
        kwargs.pop("temperature", None)
        body = dict(model=self.name, messages=messages, temperature=0,
                    max_tokens=min(max_tokens, MAX_OUTPUT_TOKENS), **kwargs)
        if tools:
            body["tools"] = tools
        if self.extra_body:
            body["extra_body"] = self.extra_body
        started = time.monotonic()
        response = self.client.chat.completions.create(**body)
        used = Usage(calls=1, seconds=time.monotonic() - started)
        u = response.usage
        if u is not None:
            raw = u.model_dump() if hasattr(u, "model_dump") else dict(u)
            used.input_tokens = raw.get("prompt_tokens") or 0
            used.output_tokens = raw.get("completion_tokens") or 0
            details = raw.get("prompt_tokens_details") or {}
            used.cached_tokens = (details.get("cached_tokens")
                                  or raw.get("prompt_cache_hit_tokens") or 0)
            out_details = raw.get("completion_tokens_details") or {}
            used.reasoning_tokens = out_details.get("reasoning_tokens") or 0
        self.case.add(used)
        self.total.add(used)
        return response.choices[0].message
