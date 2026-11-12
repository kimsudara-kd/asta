"""Fallback local PostgreSQL (embedded, dev-only) for machines where Docker is unavailable.

Usage: uv run python scripts/dev_db.py [data_dir]   (runs until Ctrl+C)
Prints a connection URI; export it as ASTA_DATABASE_URL with the scheme changed to
`postgresql+pg8000://`. Local-only, not for production.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pgserver


def main() -> None:
    data = Path(sys.argv[1] if len(sys.argv) > 1 else "infra/data/pg").resolve()
    data.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(data, cleanup_mode=None)
    print(f"embedded postgres data dir: {data}")
    print("connection URI:", server.get_uri())
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.cleanup()


if __name__ == "__main__":
    main()
