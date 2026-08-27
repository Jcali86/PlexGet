#!/usr/bin/env python3
"""Run TMDb gap analysis: missing episodes/seasons, franchise gaps, alternate versions.

Requires a TMDb API key in config.yaml. Self-throttles to ~4 requests/second.

    venv/bin/python scripts/run_gap_analysis.py            # TV + movies
    venv/bin/python scripts/run_gap_analysis.py --tv
    venv/bin/python scripts/run_gap_analysis.py --movies
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import config
from db import Database
from scanner.gap_analysis import run_full_analysis, run_movie_analysis, run_tv_analysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tv", action="store_true", help="TV gaps only")
    parser.add_argument("--movies", action="store_true", help="movie gaps only")
    parser.add_argument("--limit", type=int, help="only analyse this many (for testing)")
    args = parser.parse_args()

    key = ((config.get("tmdb") or {}).get("api_key") or "").strip()
    # The example file ships a placeholder; treating it as unset saves a
    # stranger a run that would only collect four hundred 401s.
    if not key or key.startswith("YOUR_") or key == "REPLACE_ME":
        print("No TMDb API key in config.yaml - nothing to do.", file=sys.stderr)
        return 2

    db = Database()
    # The web app and other jobs read this database while we write to it.
    db.conn.execute("PRAGMA busy_timeout = 30000")

    started = time.time()
    print("=" * 62, flush=True)
    print("GAP ANALYSIS", flush=True)
    print("=" * 62, flush=True)

    try:
        if args.tv:
            result = {"tv": run_tv_analysis(db, limit=args.limit)}
        elif args.movies:
            result = {"movies": run_movie_analysis(db, limit=args.limit)}
        else:
            result = run_full_analysis(db, limit=args.limit)
    finally:
        counts = {
            row["gap_type"]: row["n"]
            for row in db.conn.execute(
                "SELECT gap_type, COUNT(*) AS n FROM gaps GROUP BY gap_type"
            )
        }
        db.close()

    print("=" * 62, flush=True)
    print(f"DONE in {time.time() - started:.0f}s - {result}", flush=True)
    print(f"gaps by type: {counts}", flush=True)
    print("=" * 62, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
