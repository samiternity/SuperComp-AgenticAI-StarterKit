"""Runs and the case runner.

A run is one play-through of a list of cases by one team's agent. The agent pulls the next
customer turn, calls tools against a private copy of the store, and replies. The runner resets
the store before every case, enforces the per-case limits, scores each case as it ends and
reports every step to the run's event log.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import EventLog
from .scorer import CaseResult, case_points, score_case, total_score
from .tools import TOOLS, ToolContext, ToolError, call_tool
from .world import Dataset, World

RUN_KINDS = ("check", "practice", "checkpoint", "final", "live")
DETAILED_KINDS = ("check", "practice")
FINISHED = ("scored", "failed", "void")
MAX_REPLY_CHARS = 2000


@dataclass(frozen=True)
class Limits:
    seconds_per_turn: float = 60.0
    tool_calls_per_case: int = 15
    idle_seconds: float = 120.0  # no agent call at all for this long ends the run


@dataclass
class CaseSet:
    name: str
    dataset: Dataset
    cases: list[dict[str, Any]]

    @classmethod
    def load(cls, folder: str | Path, name: str | None = None) -> "CaseSet":
        folder = Path(folder)
        cases = json.loads((folder / "cases.json").read_text(encoding="utf-8"))
        return cls(name or folder.name, Dataset.load(folder / "store"), cases)


class RunError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Run:
    def __init__(self, *, run_id: str, token: str, kind: str, case_set: CaseSet,
                 cases: list[dict[str, Any]], team: str, detail: bool, limits: Limits,
                 log_dir: Path | None, commit: str | None = None):
        self.id = run_id
        self.token = token
        self.kind = kind
        self.case_set = case_set
        self.cases = cases
        self.team = team
        self.detail = detail
        self.limits = limits
        self.commit = commit
        self.status = "queued"
        self.failure: str | None = None
        self.times: dict[str, float | None] = {"queued": time.time(), "started": None,
                                                "finished": None}
        self.events = EventLog(run_id, log_dir / f"{run_id}.jsonl" if log_dir else None)
        self.results: list[CaseResult] = []
        self.summary: dict[str, Any] | None = None
        self.lock = asyncio.Lock()
        self.last_activity = time.monotonic()
        # the case in play
        self.index = -1
        self.world: World | None = None
        self.turns: list[dict[str, Any]] = []
        self.pending = False
        self.deadline = 0.0
        self.tool_calls = 0
        self.case_invalid: str | None = None

    # -- views -------------------------------------------------------------------------

    @property
    def case(self) -> dict[str, Any]:
        return self.cases[self.index]

    def public_case_id(self) -> str:
        return self.case["id"] if self.detail else f"case-{self.index + 1:02d}"

    def info(self) -> dict[str, Any]:
        return {
            "run_id": self.id,
            "kind": self.kind,
            "team": self.team,
            "case_set": self.case_set.name,
            "commit": self.commit,
            "status": self.status,
            "failure": self.failure,
            "cases": len(self.cases),
            "cases_done": len(self.results),
            "times": self.times,
            "summary": self.summary,
        }

    # -- agent calls -------------------------------------------------------------------

    def next_turn(self) -> dict[str, Any]:
        self._touch()
        if self.status in FINISHED:
            return {"done": True}
        if self.status in ("queued", "preparing"):
            self._start()
        if not self.pending:
            if self.world is None or len(self._customer_turns()) >= len(self.case["messages"]):
                if self.world is not None:
                    self._finish_case()  # defensive: a case whose last reply never came
                self.index += 1
                if self.index >= len(self.cases):
                    self._finish_run()
                    return {"done": True}
                self._open_case()
            self._deliver_message()
        return self._turn_payload()

    def call(self, name: str, args: Any) -> dict[str, Any]:
        self._touch()
        self._require_turn()
        self.tool_calls += 1
        if self.tool_calls > self.limits.tool_calls_per_case:
            self.case_invalid = (f"more than {self.limits.tool_calls_per_case} tool calls "
                                 "in one case")
            return {"ok": False, "error": "tool call limit reached for this case; reply now"}
        assert self.world is not None
        ctx = ToolContext(self.world, self.case["customer_id"])
        try:
            result = call_tool(ctx, name, args)
            out = {"ok": True, "result": result}
        except ToolError as exc:
            out = {"ok": False, "error": str(exc)}
        if self.detail:
            self.events.emit("tool_called", case_id=self.public_case_id(), tool=name,
                             kind=TOOLS[name].kind if name in TOOLS else "unknown",
                             args=args, **out)
        else:
            self.events.emit("tool_called", case_id=self.public_case_id(), tool=name,
                             ok=out["ok"])
        return out

    def reply(self, body: Any) -> dict[str, Any]:
        self._touch()
        self._require_turn()
        if isinstance(body, dict) and "crash" in body:
            reason = f"crash: {str(body['crash'])[:300]}"
            self._finish_case(invalid=reason)
            return {"ok": True, "case_finished": True}
        problem = _reply_problem(body)
        if problem:
            self._finish_case(invalid=f"invalid reply: {problem}")
            return {"ok": False, "error": f"invalid reply: {problem}", "case_finished": True}
        products = [str(p) for p in body.get("products") or []]
        self.turns.append({"role": "agent", "text": body["reply"], "products": products})
        self.pending = False
        if self.detail:
            self.events.emit("reply_sent", case_id=self.public_case_id(), text=body["reply"],
                             products=products)
        else:
            self.events.emit("reply_sent", case_id=self.public_case_id())
        finished = len(self._customer_turns()) >= len(self.case["messages"])
        if finished:
            self._finish_case()
        return {"ok": True, "case_finished": finished}

    # -- run manager calls -------------------------------------------------------------

    def tick(self) -> None:
        """Called about once a second: enforces the turn deadline and the idle limit."""
        if self.status != "running":
            return
        now = time.monotonic()
        if self.pending and now > self.deadline:
            self._finish_case(invalid=f"timeout: no reply within "
                                      f"{self.limits.seconds_per_turn:.0f} seconds")
        elif now - self.last_activity > self.limits.idle_seconds:
            self.abort("the agent stopped calling the game")

    def abort(self, reason: str) -> None:
        """The agent is gone: the case in play and every case left score zero."""
        if self.status in FINISHED:
            return
        if self.status != "running":
            self._start()
        if self.world is not None:
            self._finish_case(invalid=reason)
        for index in range(self.index + 1, len(self.cases)):
            self.index = index
            self._open_case()
            self._finish_case(invalid=reason)
        self.index = len(self.cases)
        self._finish_run()

    def fail(self, reason: str) -> None:
        """The code did not build or start. No score."""
        if self.status in FINISHED:
            return
        self.status, self.failure = "failed", reason
        self.times["finished"] = time.time()
        self.events.emit("run_failed", reason=reason)
        self.events.close()

    def void(self, reason: str = "cancelled by staff") -> None:
        if self.status in FINISHED:
            return
        self.status, self.failure = "void", reason
        self.times["finished"] = time.time()
        self.events.emit("run_void", reason=reason)
        self.events.close()

    # -- internals ---------------------------------------------------------------------

    def _touch(self) -> None:
        self.last_activity = time.monotonic()
        if self.status == "running" and self.pending and time.monotonic() > self.deadline:
            self._finish_case(invalid=f"timeout: no reply within "
                                      f"{self.limits.seconds_per_turn:.0f} seconds")

    def _require_turn(self) -> None:
        if self.status in FINISHED:
            raise RunError(409, f"the run is {self.status}")
        if not self.pending:
            raise RunError(409, "no customer is waiting; call GET /agent/next first")

    def _start(self) -> None:
        self.status = "running"
        self.times["started"] = time.time()
        self.events.emit("run_started", kind=self.kind, team=self.team,
                         store=self.case_set.dataset.name, cases=len(self.cases),
                         max_points=sum(case_points(c) for c in self.cases))

    def _customer_turns(self) -> list[dict[str, Any]]:
        return [t for t in self.turns if t["role"] == "customer"]

    def _open_case(self) -> None:
        setup = self.case.get("setup", {})
        self.world = World(self.case_set.dataset, flags=setup.get("flags"))
        self.turns = []
        self.tool_calls = 0
        self.case_invalid = None
        self.pending = False

    def _deliver_message(self) -> None:
        number = len(self._customer_turns())
        text = self.case["messages"][number]
        self.turns.append({"role": "customer", "text": text})
        self.pending = True
        self.deadline = time.monotonic() + self.limits.seconds_per_turn
        assert self.world is not None
        customer = self.world.customers.get(self.case["customer_id"], {})
        if self.detail:
            self.events.emit("customer_arrived", case_id=self.public_case_id(), turn=number + 1,
                             customer={"id": self.case["customer_id"],
                                       "name": customer.get("name", "")},
                             language=self.case.get("language", "en"), text=text,
                             category=self.case["category"],
                             difficulty=int(self.case["difficulty"]))
        else:
            self.events.emit("customer_arrived", case_id=self.public_case_id(), turn=number + 1)

    def _turn_payload(self) -> dict[str, Any]:
        return {
            "done": False,
            "case": {
                "case_id": self.public_case_id(),
                "turn": len(self._customer_turns()),
                "customer_id": self.case["customer_id"],
                "language": self.case.get("language", "en"),
                "today": self.case_set.dataset.store["today"],
                "store": self.case_set.dataset.store.get("name"),
                "currency": self.case_set.dataset.store.get("currency", "PKR"),
                "messages": [{"role": t["role"], "text": t["text"]} for t in self.turns],
            },
            "limits": {
                "seconds_left": max(0.0, round(self.deadline - time.monotonic(), 1)),
                "tool_calls_left": max(0, self.limits.tool_calls_per_case - self.tool_calls),
            },
        }

    def _finish_case(self, invalid: str | None = None) -> None:
        assert self.world is not None
        result = score_case(self.case, self.world, self.turns, invalid or self.case_invalid)
        self.results.append(result)
        for violation in result.violations:
            kind = violation.split(":", 1)[0]
            if self.detail:
                self.events.emit("violation", case_id=self.public_case_id(), kind=kind,
                                 detail=violation)
            else:
                self.events.emit("violation", case_id=self.public_case_id(), kind=kind)
        scored = result.to_dict(detail=self.detail)
        if not self.detail:
            scored["case_id"] = self.public_case_id()
            if result.outcome == "invalid":
                scored["failure"] = result.reason.split(":", 1)[0]  # crash, timeout, invalid reply
        self.events.emit("case_scored", **scored)
        self.world = None
        self.pending = False

    def _finish_run(self) -> None:
        self.summary = total_score(self.results, self.cases)
        self.status = "scored"
        self.times["finished"] = time.time()
        self.events.emit("run_finished", **self.summary)
        self.events.close()


def _reply_problem(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "the reply must be a JSON object"
    text = body.get("reply")
    if not isinstance(text, str) or not text.strip():
        return "'reply' must be a non-empty string"
    if len(text) > MAX_REPLY_CHARS:
        return f"'reply' is longer than {MAX_REPLY_CHARS} characters"
    products = body.get("products")
    if products is not None and (not isinstance(products, list) or len(products) > 10
                                 or not all(isinstance(p, str) for p in products)):
        return "'products' must be a list of at most 10 product ids"
    extra = set(body) - {"reply", "products"}
    if extra:
        return f"unknown field(s) {sorted(extra)}"
    return None


class RunManager:
    def __init__(self, case_sets: dict[str, CaseSet], *, limits: Limits | None = None,
                 log_dir: Path | None = None):
        self.case_sets = case_sets
        self.limits = limits or Limits()
        self.log_dir = log_dir
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
        self.runs: dict[str, Run] = {}
        self.by_token: dict[str, Run] = {}
        self._ticker: asyncio.Task | None = None

    def create(self, *, kind: str, case_set: str, team: str = "local",
               case_ids: list[str] | None = None, detail: bool | None = None,
               commit: str | None = None) -> Run:
        if kind not in RUN_KINDS:
            raise RunError(400, f"kind must be one of {RUN_KINDS}")
        cs = self.case_sets.get(case_set)
        if cs is None:
            raise RunError(404, f"no case set '{case_set}'; sets are {sorted(self.case_sets)}")
        cases = cs.cases
        if case_ids:
            by_id = {c["id"]: c for c in cs.cases}
            missing = [cid for cid in case_ids if cid not in by_id]
            if missing:
                raise RunError(404, f"unknown case id(s) {missing}")
            cases = [by_id[cid] for cid in case_ids]
        run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        run = Run(run_id=run_id, token=secrets.token_urlsafe(24), kind=kind, case_set=cs,
                  cases=cases, team=team,
                  detail=kind in DETAILED_KINDS if detail is None else detail,
                  limits=self.limits, log_dir=self.log_dir, commit=commit)
        self.runs[run.id] = run
        self.by_token[run.token] = run
        return run

    def get(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise RunError(404, f"no run '{run_id}'")
        return run

    def by_run_token(self, token: str) -> Run:
        run = self.by_token.get(token)
        if run is None:
            raise RunError(401, "unknown run token")
        return run

    async def tick_forever(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            for run in list(self.runs.values()):
                if run.status == "running":
                    async with run.lock:
                        run.tick()
