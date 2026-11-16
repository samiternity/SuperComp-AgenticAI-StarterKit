"""Plays your agent against the practice game on your laptop and prints every result.

    python run_local.py                          all practice cases
    python run_local.py --list                   list the practice cases and exit
    python run_local.py --cases p-order-01,p-cancellation-02
    python run_local.py --category returns       one category (combine with --cases)
    python run_local.py --sample 5               5 random cases: a cheap smoke test
    python run_local.py --oracle                 replay the reference solutions, no model key
    python run_local.py --trace                  print what happened in every failed case
    python run_local.py --show-log last          re-print the traces of your last run
    python run_local.py --agent examples.tool_loop_agent   play another agent module
    python run_local.py --view                   also open the shop at http://localhost:8000

The model comes from a .env file next to this script (copy .env.example). The practice game
applies the same limits as the real one: 60 seconds per message, 15 tool calls per customer,
and the client allows 10 model calls per customer with at most 150 output tokens each.
Every run is logged to runs/<run_id>.jsonl.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.exit(f"the kit needs Python 3.11 or newer; this is {sys.version.split()[0]}")

import argparse  # noqa: E402
import json  # noqa: E402
import random  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import webbrowser  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def find(*candidates: Path) -> Path | None:
    return next((c for c in candidates if c.exists()), None)


# Inside the kit, the practice game sits next to agent/. In the organizers' repo, the kit is
# assembled from ../game, ../content and ../screen/dist.
GAME_SRC = find(HERE / "practice_game", HERE.parent / "game")
CONTENT = find(HERE / "practice_game" / "content", HERE.parent / "content")
SCREEN = find(HERE / "practice_game" / "screen", HERE.parent / "screen" / "dist")
if GAME_SRC is None or CONTENT is None:
    sys.exit("could not find the practice game next to run_local.py")
sys.path.insert(0, str(GAME_SRC))

try:
    import httpx  # noqa: E402
    import uvicorn  # noqa: E402
except ImportError as exc:
    sys.exit(f"missing package ({exc.name}). Activate your virtual environment and run:\n"
             "    pip install -r requirements.txt")

from agent.loop import load_run, play  # noqa: E402
from agent.model import (MAX_CALLS_PER_CASE, PRICE_CACHED_INPUT, PRICE_INPUT,  # noqa: E402
                         PRICE_OUTPUT, Model, load_dotenv)
from agent.tools import Tools  # noqa: E402
from storegame.app import create_app  # noqa: E402
from storegame.runs import CaseSet, Limits  # noqa: E402

BUDGET_FINAL = 0.075  # US dollars for the 40-case final run
RUNS_DIR = HERE / "runs"
RESET = "\033[0m"
DIM = "\033[90m"
COLOURS = {"passed": "\033[32m", "partial": "\033[33m", "violation": "\033[31m"}


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


# -- choosing cases -------------------------------------------------------------------------

def select_cases(case_set: CaseSet, args: argparse.Namespace) -> list[str] | None:
    """Returns the chosen case ids in case-file order, or None for every case.
    Exits with a helpful message on an unknown id or category."""
    by_id = {c["id"]: c for c in case_set.cases}
    categories = sorted({c["category"] for c in case_set.cases})
    chosen: set[str] = set()

    ids = [c.strip() for c in args.cases.split(",") if c.strip()]
    unknown = [c for c in ids if c not in by_id]
    if unknown:
        sys.exit(f"unknown case id(s): {', '.join(unknown)}\n"
                 "run `python run_local.py --list` to see every case")
    chosen.update(ids)

    if args.category:
        wanted = [c.strip() for c in args.category.split(",") if c.strip()]
        bad = [c for c in wanted if c not in categories]
        if bad:
            sys.exit(f"unknown category: {', '.join(bad)}\n"
                     f"categories are: {', '.join(categories)}")
        chosen.update(c["id"] for c in case_set.cases if c["category"] in wanted)

    pool = [c["id"] for c in case_set.cases if not chosen or c["id"] in chosen]
    if args.sample:
        if args.sample < 1:
            sys.exit("--sample needs a positive number")
        rng = random.Random(args.seed)
        picked = set(rng.sample(pool, min(args.sample, len(pool))))
        pool = [c for c in pool if c in picked]
    if not chosen and not args.sample:
        return None
    return pool


def list_cases(case_set: CaseSet) -> None:
    print(f"{len(case_set.cases)} cases in {case_set.name}\n")
    print(f"{'id':22s} {'category':15s} {'pts':>3s}  {'lang':10s} turns  first message")
    points = {1: 5, 2: 10, 3: 15}
    for c in case_set.cases:
        first = c["messages"][0]
        first = first if len(first) <= 60 else first[:57] + "..."
        print(f"{c['id']:22s} {c['category']:15s} {points.get(int(c['difficulty']), '?'):>3}  "
              f"{c.get('language', 'en'):10s} {len(c['messages']):>5}  {first}")


# -- the oracle agent ---------------------------------------------------------------------

def oracle_run(case_set: CaseSet):
    """An agent that replays each case's reference solution. It needs no model, so you can
    see the whole pipeline, the shop view and a perfect score before you get a key. It only
    works on practice cases, which carry their solutions."""
    by_id = {c["id"]: c for c in case_set.cases}

    def run(case: dict[str, Any], tools: Tools, model: Model) -> dict[str, Any]:
        solution = by_id[case["case_id"]]["solution"]
        turn = solution["turns"][case["turn"] - 1] if "turns" in solution else solution
        for call in turn.get("calls", []):
            tools.call(call["tool"], call.get("args", {}))
        out: dict[str, Any] = {"reply": turn["reply"]}
        if turn.get("products"):
            out["products"] = turn["products"]
        return out

    return run


class _NoModel:
    """Stands in for the model client in --oracle mode; it is never called."""

    class chat:  # noqa: N801  (mimics client.chat.completions)
        class completions:  # noqa: N801
            @staticmethod
            def create(**_: Any):
                raise RuntimeError("--oracle runs without a model")


# -- traces ---------------------------------------------------------------------------------

def short(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def print_traces(events: list[dict[str, Any]], which: str) -> None:
    """Prints, case by case, the customer's messages, every tool call, the agent's replies
    and the scorer's checks. which: 'failed' (default) or 'all'."""
    cases: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        if "case_id" in e:
            cases.setdefault(e["case_id"], []).append(e)
    shown = 0
    for case_id, evs in cases.items():
        scored = next((e for e in evs if e["type"] == "case_scored"), None)
        if which != "all" and scored and scored.get("outcome") == "passed":
            continue
        shown += 1
        outcome = scored.get("outcome", "?") if scored else "unscored"
        print(f"\n{COLOURS.get(outcome, DIM)}=== {case_id}: {outcome}{RESET}")
        for e in evs:
            kind = e["type"]
            if kind == "customer_arrived":
                who = e.get("customer", {}).get("id", "")
                print(f"  customer {who} (turn {e.get('turn')}): {e.get('text', '')}")
            elif kind == "tool_called":
                args = short(e.get("args", {}), 80)
                if e.get("ok"):
                    print(f"    {DIM}tool{RESET} {e['tool']}({args}) -> "
                          f"{short(e.get('result'), 140)}")
                else:
                    print(f"    \033[31mtool {e['tool']}({args}) failed:{RESET} "
                          f"{e.get('error')}")
            elif kind == "reply_sent":
                products = f"  products={e['products']}" if e.get("products") else ""
                print(f"  agent: {e.get('text', '')}{products}")
            elif kind == "violation":
                print(f"  \033[31mviolation: {e.get('kind')} {e.get('detail', '')}{RESET}")
        if scored:
            for check in scored.get("checks", []):
                mark = "\033[32m ok \033[0m" if check["ok"] else "\033[31mFAIL\033[0m"
                detail = f": {check['detail']}" if check.get("detail") else ""
                print(f"  [{mark}] {check['name']}{detail}")
            if scored.get("reason"):
                print(f"  reason: {scored['reason']}")
    if not shown:
        print("every case passed; use --trace all to see them anyway")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def show_log(target: str, which: str) -> None:
    if target == "last":
        logs = sorted(RUNS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not logs:
            sys.exit(f"no run logs in {RUNS_DIR}")
        path = logs[-1]
    else:
        path = Path(target)
        if not path.exists():
            path = RUNS_DIR / f"{target}.jsonl"
        if not path.exists():
            sys.exit(f"no run log at {target}")
    print(f"traces from {path}")
    print_traces(read_log(path), which)


# -- main ------------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--case-set", default="practice",
                    help="a folder under practice_game/content (default: practice)")
    ap.add_argument("--cases", default="", help="comma-separated case ids")
    ap.add_argument("--category", default="", help="product_advice, order_tracking, "
                                                   "cancellation, returns or privacy "
                                                   "(comma-separated for several)")
    ap.add_argument("--sample", type=int, default=0,
                    help="play only N random cases from the selection (a cheap smoke test)")
    ap.add_argument("--seed", type=int, default=0, help="seed for --sample (default 0)")
    ap.add_argument("--list", action="store_true", help="list the cases and exit")
    ap.add_argument("--agent", default="agent.main",
                    help="module whose run() to play (default agent.main), "
                         "e.g. examples.tool_loop_agent")
    ap.add_argument("--oracle", action="store_true",
                    help="replay the reference solutions instead of your agent (no model key)")
    ap.add_argument("--trace", nargs="?", const="failed", choices=("failed", "all"),
                    help="after the run, print what happened in each failed case (or 'all')")
    ap.add_argument("--show-log", metavar="RUN",
                    help="print the traces of a past run ('last', a run id or a .jsonl path) "
                         "and exit; combine with --trace all to include passed cases")
    ap.add_argument("--quiet", action="store_true",
                    help="hide the per-turn progress lines (crashes are always shown)")
    ap.add_argument("--view", action="store_true", help="serve the shop page and keep it open")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--turn-seconds", type=float, default=60,
                    help="time per customer message (60 on event day); raise it if a free "
                         "tier's rate limit makes your model wait, e.g. 300 on Groq's free tier")
    args = ap.parse_args()

    if args.show_log:
        show_log(args.show_log, args.trace or "failed")
        return

    load_dotenv(HERE / ".env")
    folder = CONTENT / args.case_set
    if not folder.exists():
        found = ", ".join(sorted(p.name for p in CONTENT.iterdir() if p.is_dir()))
        sys.exit(f"no case set at {folder}; available: {found}")
    case_set = CaseSet.load(folder)
    if args.list:
        list_cases(case_set)
        return
    ids = select_cases(case_set, args)

    if args.oracle:
        if any("solution" not in c for c in case_set.cases):
            sys.exit(f"--oracle needs cases with solutions; {case_set.name} has none")
        model = Model(client=_NoModel(), name="oracle (reference solutions, no model)")
        run_fn = oracle_run(case_set)
    else:
        try:
            model = Model()
        except RuntimeError as exc:
            sys.exit(f"{exc}.\n"
                     "  1. cp .env.example .env\n"
                     "  2. put your model key in .env (a free one: https://console.groq.com/keys)\n"
                     "Or try the game with no key first: python run_local.py --oracle")
        run_fn = load_run(args.agent)

    port = args.port if port_free(args.port) else 0
    app = create_app({case_set.name: case_set}, screen_dir=SCREEN if args.view else None,
                     log_dir=RUNS_DIR,
                     limits=Limits(seconds_per_turn=args.turn_seconds, idle_seconds=900))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    created = httpx.post(f"{base}/runs", json={"case_set": case_set.name,
                                               "case_ids": ids}).json()
    if "run_id" not in created:
        sys.exit(created.get("error", created))
    if args.view:
        url = f"{base}/?run={created['run_id']}"
        print(f"shop view: {url}")
        webbrowser.open(url)
        time.sleep(2)

    tools = Tools(base, created["run_token"])
    print(f"playing {created['cases']} case(s) from {case_set.name} with {model.name}\n")

    def log(line: str) -> None:
        if not args.quiet or "crashed" in line or "rejected" in line:
            print(f"{DIM}{line}{RESET}" if "answered in" in line else line, flush=True)

    try:
        play(run_fn, tools, model, log=log)
    except KeyboardInterrupt:
        print("\nstopped; results so far:")

    run = httpx.get(f"{base}/runs/{created['run_id']}").json()
    log_path = RUNS_DIR / f"{created['run_id']}.jsonl"
    events = read_log(log_path) if log_path.exists() else []
    tool_counts: dict[str, int] = {}
    for e in events:
        if e["type"] == "tool_called":
            tool_counts[e["case_id"]] = tool_counts.get(e["case_id"], 0) + 1

    print()
    for r in run.get("results", []):
        colour = COLOURS.get(r["outcome"], DIM)
        net = r["earned"] - r["penalty"]
        usage = model.per_case.get(r["case_id"], {})
        calls = f"{usage.get('calls', 0):>2}m {tool_counts.get(r['case_id'], 0):>2}t"
        print(f"{colour}{r['outcome']:11s}{RESET} {net:+5.1f}/{r['points']:<2} "
              f"{r['case_id']:20s} {calls}  ${usage.get('cost_usd', 0):.5f}  "
              f"{r.get('reason', '')}")
    s = run.get("summary")
    if not s:
        print("\nthe run did not finish, so there is no total score")
        return
    total = model.total.to_dict()
    n = max(1, len(run["results"]))
    print(f"\nscore {s['score']} / 100   violations {s['safety_violations']}   "
          f"outcomes {s['outcomes']}")
    for cat, v in s["by_category"].items():
        print(f"  {cat:15s} {v['earned'] - v['penalty']:6.1f} / {v['max']}")
    if not args.oracle:
        print(f"\nmodel: {total['calls']} calls, {total['input_tokens']} input tokens "
              f"({total['cached_tokens']} cached), {total['output_tokens']} output tokens")
        projected = total["cost_usd"] / n * 40
        print(f"at event-day prices (${PRICE_INPUT}/${PRICE_CACHED_INPUT}/${PRICE_OUTPUT} per "
              f"million input/cached/output): ${total['cost_usd']:.4f} here, about "
              f"${projected:.4f} for the 40-case final, whose budget is ${BUDGET_FINAL} "
              f"({projected / BUDGET_FINAL:.0%})")
    print(f"limits per customer: {MAX_CALLS_PER_CASE} model calls (m), 15 tool calls (t), "
          "150 output tokens per model call")
    if args.trace:
        print_traces(events, args.trace)
    elif any(r["outcome"] != "passed" for r in run.get("results", [])):
        print(f"see why cases failed: python run_local.py --show-log {created['run_id']}")
    if args.view:
        print(f"\nshop view still open at {base}/?run={created['run_id']}&replay=1 — Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
