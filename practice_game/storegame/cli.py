"""storegame serve --content content/practice [--content content/event-day] [--port 8000]"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .app import create_app
from .runs import CaseSet


def main() -> None:
    parser = argparse.ArgumentParser(prog="storegame")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the game service")
    serve.add_argument("--content", action="append", required=True,
                       help="a case set folder (store/ and cases.json); repeatable")
    serve.add_argument("--screen", help="folder of the built screen page")
    serve.add_argument("--logs", default="runs", help="folder for run event logs")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    case_sets = {}
    for folder in args.content:
        cs = CaseSet.load(folder)
        case_sets[cs.name] = cs
    app = create_app(case_sets, service_token=os.environ.get("SERVICE_TOKEN"),
                     screen_dir=Path(args.screen) if args.screen else None,
                     log_dir=Path(args.logs))
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
