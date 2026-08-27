#!/usr/bin/env python3
"""Walk every configured library into media_files, then run the quality audit.

This is the same work the /scan/<library> and /analyze/quality endpoints do,
run start to finish in one pass so the dashboard can be repopulated.

    venv/bin/python scripts/full_rescan.py            # scan + quality audit
    venv/bin/python scripts/full_rescan.py --scan-only
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from scanner import MediaScanner, libraries, paths
from scanner.quality_auditor import run_quality_audit


def scan_all(db, scanner):
    grand_total = 0
    for library in libraries.keys():
        started = time.time()
        files = scanner.scan_nas_path(library)
        manual = scanner.is_manual_library(library)
        # Worked out per folder rather than per file: every file in one folder
        # is on the same disk, and a library of ten thousand would otherwise
        # ask the filesystem the same question ten thousand times.
        by_folder = {}
        for f in files:
            if f.parent not in by_folder:
                by_folder[f.parent] = paths.disk_label(f.parent)
            try:
                size = f.stat().st_size
            except OSError:
                size = None
            db.upsert_media(
                str(f), library=library, volume=by_folder[f.parent],
                size_bytes=size, manual_only=manual,
            )
        db.log_scan(f"nas_{library}", len(files))
        grand_total += len(files)
        print(f"  [{library:8s}] {len(files):>6,d} files in {time.time() - started:5.1f}s"
              f"{' (manual-only library)' if manual else ''}", flush=True)
    return grand_total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-only", action="store_true",
                        help="skip the ffprobe quality audit")
    args = parser.parse_args()

    if not libraries.keys():
        print("No libraries in config.yaml - fill in the nas block first.",
              file=sys.stderr)
        return 2

    db = Database()
    # The web app reads this database while the scan writes to it.
    db.conn.execute("PRAGMA busy_timeout = 30000")
    scanner = MediaScanner()

    print("=" * 62, flush=True)
    print("FULL RESCAN", flush=True)
    print("=" * 62, flush=True)

    started = time.time()
    print("\n[1/2] Walking library paths\n", flush=True)
    total = scan_all(db, scanner)
    print(f"\n  {total:,} files catalogued in {time.time() - started:.0f}s", flush=True)

    if args.scan_only:
        print("\nSkipping quality audit (--scan-only).", flush=True)
        db.close()
        return 0

    print("\n[2/2] Quality audit (ffprobe per file - this is the slow part)\n", flush=True)
    audit_started = time.time()
    result = run_quality_audit(db)
    print(f"\n  Quality audit finished in {time.time() - audit_started:.0f}s: {result}", flush=True)

    counts = db.conn.execute(
        "SELECT COUNT(*) FROM media_files"
    ).fetchone()[0]
    scored = db.conn.execute("SELECT COUNT(*) FROM quality_scores").fetchone()[0]
    avg = db.conn.execute("SELECT ROUND(AVG(score),1) FROM quality_scores").fetchone()[0]
    db.close()

    print("=" * 62, flush=True)
    print(f"DONE in {time.time() - started:.0f}s - {counts:,} files tracked, "
          f"{scored:,} scored, average quality {avg}", flush=True)
    print("=" * 62, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
