"""Fetches customers from the game, calls your run() for each one, and sends the reply.
Do not edit: on the server, this same loop drives your agent.

    python -m agent.loop          (the runtime does this; locally, use run_local.py)
"""

from __future__ import annotations

import importlib
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "agent"

from agent.model import Model, load_dotenv  # noqa: E402
from agent.tools import Tools  # noqa: E402

RunFn = Callable[[dict[str, Any], Tools, Model], dict[str, Any]]


def load_run(module: str = "agent.main") -> RunFn:
    return importlib.import_module(module).run


def play(run: RunFn, tools: Tools, model: Model, log: Callable[[str], None] = print) -> None:
    """Plays every customer turn the game hands out, until the run is done."""
    game = httpx.Client(base_url=tools.game_url, timeout=30,
                        headers={"Authorization": f"Bearer {tools.run_token}"})
    while True:
        turn = _get(game, "/agent/next")
        if turn.get("done"):
            break
        case = turn["case"]
        if case["turn"] == 1:
            model.start_case(case["case_id"])
        started = time.monotonic()
        try:
            out = run(case, tools, model)
            if isinstance(out, str):
                out = {"reply": out}
            body = {"reply": out.get("reply", "")}
            if out.get("products"):
                body["products"] = list(out["products"])
        except Exception as exc:  # a crash scores zero for this case; the run goes on
            log(f"[{case['case_id']}] run() crashed:\n{traceback.format_exc()}")
            body = {"crash": f"{type(exc).__name__}: {exc}"}
        answer = game.post("/agent/reply", json=body).json()
        if not answer.get("ok", False):
            log(f"[{case['case_id']}] reply rejected: {answer.get('error')}")
        log(f"[{case['case_id']}] turn {case['turn']} answered in "
            f"{time.monotonic() - started:.1f}s")
    usage = model.finish()
    log(f"model usage: {usage}")


def _get(game: httpx.Client, path: str) -> dict[str, Any]:
    for attempt in range(5):
        try:
            return game.get(path).json()
        except httpx.HTTPError:
            time.sleep(1 + attempt)
    raise RuntimeError("the game is not answering")


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    module = os.environ.get("AGENT_MAIN", "agent.main")
    play(load_run(module), Tools(), Model())


if __name__ == "__main__":
    main()
