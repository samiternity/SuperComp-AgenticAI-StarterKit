"""The game's HTTP service.

Three groups of routes:
  agent-facing   /agent/*, /store/*   called by agent containers with their run token
  internal       /health, /runs*      called by the Competition API with the service token
  screen         /runs/{id}/events, /runs/{id}/ws, and the static screen page

Without a SERVICE_TOKEN the game runs in local mode (the starter kit's practice game): the
internal and screen routes are open, and it should only listen on localhost.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .runs import CaseSet, Limits, Run, RunError, RunManager
from .tools import tool_schemas


class CreateRun(BaseModel):
    kind: str = "practice"
    case_set: str
    team: str = "local"
    case_ids: list[str] | None = None
    commit: str | None = None
    detail: bool | None = None


def create_app(case_sets: dict[str, CaseSet], *, service_token: str | None = None,
               screen_dir: Path | None = None, log_dir: Path | None = None,
               limits: Limits | None = None) -> FastAPI:
    manager = RunManager(case_sets, limits=limits, log_dir=log_dir)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        ticker = asyncio.create_task(manager.tick_forever())
        yield
        ticker.cancel()

    app = FastAPI(title="SuperComp store game", lifespan=lifespan)
    app.state.manager = manager

    @app.exception_handler(RunError)
    async def run_error(_: Request, exc: RunError):
        return JSONResponse({"ok": False, "error": exc.message}, status_code=exc.status)

    def staff(authorization: str | None = Header(default=None)) -> None:
        if service_token and authorization != f"Bearer {service_token}":
            raise RunError(401, "service token required")

    def agent_run(authorization: str | None = Header(default=None)) -> Run:
        if not authorization or not authorization.startswith("Bearer "):
            raise RunError(401, "missing run token")
        return manager.by_run_token(authorization.removeprefix("Bearer ").strip())

    # -- agent-facing ------------------------------------------------------------------

    @app.get("/agent/next")
    async def agent_next(run: Run = Depends(agent_run)):
        async with run.lock:
            return run.next_turn()

    @app.post("/agent/reply")
    async def agent_reply(request: Request, run: Run = Depends(agent_run)):
        body = await _json(request)
        async with run.lock:
            return run.reply(body)

    @app.get("/agent/tools")
    async def agent_tools(run: Run = Depends(agent_run)):
        return {"tools": tool_schemas()}

    @app.post("/store/{tool}")
    async def store_tool(tool: str, request: Request, run: Run = Depends(agent_run)):
        args = await _json(request)
        async with run.lock:
            return run.call(tool, args)

    # -- internal ----------------------------------------------------------------------

    @app.get("/health")
    async def health():
        return {"ok": True, "case_sets": sorted(case_sets),
                "running": sum(r.status == "running" for r in manager.runs.values())}

    @app.post("/runs", dependencies=[Depends(staff)])
    async def create_run(body: CreateRun):
        run = manager.create(kind=body.kind, case_set=body.case_set, team=body.team,
                             case_ids=body.case_ids, commit=body.commit, detail=body.detail)
        return {"run_id": run.id, "run_token": run.token, **run.info()}

    @app.get("/runs", dependencies=[Depends(staff)])
    async def list_runs(since: float = 0.0):
        return {"runs": [r.info() for r in manager.runs.values()
                         if (r.times["finished"] or 0) >= since or since == 0.0]}

    @app.get("/runs/{run_id}", dependencies=[Depends(staff)])
    async def get_run(run_id: str):
        run = manager.get(run_id)
        info = run.info()
        info["results"] = [r.to_dict(detail=run.detail) for r in run.results]
        return info

    @app.post("/runs/{run_id}/cancel", dependencies=[Depends(staff)])
    async def cancel_run(run_id: str):
        run = manager.get(run_id)
        async with run.lock:
            run.void()
        return run.info()

    @app.post("/runs/{run_id}/abort", dependencies=[Depends(staff)])
    async def abort_run(run_id: str, reason: str = "the agent exited"):
        """Called by the run manager when the agent's container exits before the run ends."""
        run = manager.get(run_id)
        async with run.lock:
            run.abort(reason)
        return run.info()

    @app.get("/case-sets", dependencies=[Depends(staff)])
    async def list_case_sets():
        return {name: [{"id": c["id"], "category": c["category"],
                        "difficulty": c["difficulty"], "language": c.get("language", "en")}
                       for c in cs.cases]
                for name, cs in case_sets.items()}

    # -- screen ------------------------------------------------------------------------

    def logged_events(run_id: str) -> list[dict[str, Any]] | None:
        """A finished run from an earlier session, read back from its event log."""
        if not log_dir or not re.fullmatch(r"run-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", run_id):
            return None
        path = log_dir / f"{run_id}.jsonl"
        if not path.exists():
            return None
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    @app.get("/runs/{run_id}/events", dependencies=[Depends(staff)])
    async def run_events(run_id: str, since: int = 0):
        if run_id not in manager.runs:
            logged = logged_events(run_id)
            if logged is not None:
                return {"events": logged[since:], "closed": True}
        run = manager.get(run_id)
        return {"events": run.events.since(since), "closed": run.events.closed}

    @app.websocket("/runs/{run_id}/ws")
    async def run_ws(websocket: WebSocket, run_id: str, since: int = 0,
                     token: str | None = None):
        if service_token and token != service_token:
            await websocket.close(code=4401)
            return
        run = manager.runs.get(run_id)
        if run is None:
            logged = logged_events(run_id)
            if logged is None:
                await websocket.close(code=4404)
                return
            await websocket.accept()
            for event in logged[since:]:
                await websocket.send_json(event)
            await websocket.close()
            return
        await websocket.accept()
        queue = run.events.subscribe()
        try:
            for event in run.events.since(since):
                await websocket.send_json(event)
            sent = len(run.events.events)
            while True:
                event = await queue.get()
                if event is None:
                    break
                if event["seq"] > sent:
                    await websocket.send_json(event)
            await websocket.close()
        except WebSocketDisconnect:
            pass
        finally:
            run.events.unsubscribe(queue)

    @app.get("/latest-run")
    async def latest_run():
        """Local mode only: the screen follows the newest run without being told its id."""
        if service_token:
            raise RunError(404, "not available")
        if not manager.runs:
            return {"run_id": None}
        return {"run_id": list(manager.runs)[-1]}

    if screen_dir and (screen_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=screen_dir / "assets"), name="assets")
        if (screen_dir / "models").exists():
            app.mount("/models", StaticFiles(directory=screen_dir / "models"), name="models")

        @app.get("/")
        async def screen_index():
            return FileResponse(screen_dir / "index.html")

    return app


async def _json(request: Request) -> Any:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return await request.json()
    except ValueError:
        raise RunError(400, "the body must be JSON") from None


def app_from_env() -> FastAPI:
    """uvicorn entry point for the server: storegame.app:app_from_env --factory"""
    content = [Path(p) for p in os.environ["GAME_CONTENT"].split(os.pathsep) if p]
    case_sets = {}
    for folder in content:
        cs = CaseSet.load(folder)
        case_sets[cs.name] = cs
    screen = os.environ.get("GAME_SCREEN_DIR")
    logs = os.environ.get("GAME_LOG_DIR")
    return create_app(case_sets, service_token=os.environ.get("SERVICE_TOKEN"),
                      screen_dir=Path(screen) if screen else None,
                      log_dir=Path(logs) if logs else None)
