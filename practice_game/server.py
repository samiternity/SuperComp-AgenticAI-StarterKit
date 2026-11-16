"""Runs the practice game alone: python practice_game/server.py, then open http://localhost:8000"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from storegame.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.argv[1:] = ["serve", "--content", str(HERE / "content" / "practice"),
                    "--screen", str(HERE / "screen"), *sys.argv[1:]]
    main()
