#!/usr/bin/env python3
"""Build or refresh the library index: every film and series, kept on this machine.

The running app keeps the index fresh by itself. This is for the first build,
which is worth watching, and for a machine where the app's own worker is
switched off (index.background: false) and a schedule does the job instead.

    venv/bin/python scripts/sync_library.py            # read every library, then check the files
    venv/bin/python scripts/sync_library.py --recent   # only what was added lately
    venv/bin/python scripts/sync_library.py --files    # only re-check the files on disk
    venv/bin/python scripts/sync_library.py --status   # what the index holds, and where
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner import library_index
from scanner.wanted_search import PlexUnavailable


def ago(stamp):
    if not stamp:
        return "never"
    seconds = int(time.time() - stamp)
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'} ago"
    return "just now"


def show_status():
    state = library_index.status()
    print(f"Index file: {state['path']}")
    if not state["available"]:
        print(f"Not in use: {state['reason']}")
        return 1
    print(f"Films: {state['films']:,}   Series: {state['series']:,}   "
          f"Seasons: {state['seasons']:,}   People: {state['people']:,}")
    files = state["files"]
    print(f"Files: {files['ok']:,} fine, {files['missing']:,} missing, "
          f"{files['unknown']:,} unreadable, {files['unchecked']:,} not yet checked")
    print(f"Last full read: {ago(state.get('full_synced_at'))}"
          + (f" (took {state['full_sync_seconds']:.0f}s)" if state.get("full_sync_seconds") else ""))
    print(f"Files last checked: {ago(state.get('files_checked_at'))}")
    print(f"Size on disk: {state.get('size_bytes', 0) / 1_048_576:.1f} MB")
    for section in state.get("sections", []):
        problem = f"  - last attempt failed: {section['last_error']}" if section.get("last_error") else ""
        print(f"  {section['title']}: {section['items']:,} ({section['kind']}), "
              f"read {ago(section['full_synced_at'])}{problem}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    what = parser.add_mutually_exclusive_group()
    what.add_argument("--recent", action="store_true", help="only look at what was added lately")
    what.add_argument("--files", action="store_true", help="only re-check the files on disk")
    what.add_argument("--status", action="store_true", help="print what the index holds and stop")
    args = parser.parse_args()

    if args.status:
        return show_status()

    ok, why = library_index.available()
    if not ok:
        print(f"The library index is not in use: {why}.", file=sys.stderr)
        print("Check index.path in config.yaml, and that its folder exists.", file=sys.stderr)
        return 2

    started = time.time()
    try:
        if args.recent:
            looked = library_index.recent_sync()
            print(f"Looked at the newest additions in {len(looked)} "
                  f"librar{'y' if len(looked) == 1 else 'ies'}.")
        elif args.files:
            library_index.check_files(progress=print)
        else:
            print(f"Reading every library into {library_index.db_path()} ...")
            report = library_index.full_sync(progress=lambda line: print(f"  {line}"))
            if "skipped" in report:
                print(f"Skipped: {report['skipped']}.")
                return 0
            library_index.check_files(progress=lambda line: print(f"  {line}"))
    except PlexUnavailable as e:
        print(f"Plex could not be reached: {e}", file=sys.stderr)
        return 3
    print(f"Done in {time.time() - started:.1f}s.")
    return show_status()


if __name__ == "__main__":
    raise SystemExit(main())
