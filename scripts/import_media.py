#!/usr/bin/env python3
"""File finished downloads from a staging folder into the Plex libraries.

Shows a plan by default and changes nothing. Add --go to carry it out.

    venv/bin/python scripts/import_media.py                    # plan only
    venv/bin/python scripts/import_media.py --go               # do it
    venv/bin/python scripts/import_media.py --only "Cool Run"  # one item
    venv/bin/python scripts/import_media.py --library 4k --go

The staging folder is `staging` in config.yaml, or --staging. Each item is
copied with rsync, checked for size, handed to Plex as a single-folder scan,
and its wanted-list entry marked acquired. Nothing in the staging folder is
deleted - items already on the shelves are reported so you can clear them
yourself.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import config
from db import Database
from scanner import libraries
from scanner.importer import (
    choose_destination, close_wanted, copy_file, inspect,
    notify_plex, plex_state, target_filename,
)
from scanner.wanted_search import PlexUnavailable


def library_for(item, override):
    """Which library an item belongs in.

    An episode goes to a TV library and everything else to a movie one, which
    Plex decides by the type of the section. Where there is more than one of
    either, the first in config is a guess - say which with --library.
    """
    if override:
        return override
    keys = libraries.keys_of_kind("show" if item["kind"] == "episode" else "movie")
    return keys[0] if keys else None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--staging", type=Path, default=config.get("staging"),
                        help="folder to import from")
    parser.add_argument("--library", help="force a library key, one of the names under nas")
    parser.add_argument("--only", help="only items whose folder name contains this")
    parser.add_argument("--go", action="store_true", help="carry out the plan")
    args = parser.parse_args()

    if not args.staging:
        print("No staging folder. Set `staging` in config.yaml or pass --staging.",
              file=sys.stderr)
        return 2
    staging = Path(args.staging).expanduser()
    if not staging.is_dir():
        print(f"No such folder: {staging}", file=sys.stderr)
        return 2

    entries = sorted(p for p in staging.iterdir() if not p.name.startswith("."))
    if args.only:
        entries = [p for p in entries if args.only.lower() in p.name.lower()]

    db = Database()
    db.conn.execute("PRAGMA busy_timeout = 30000")
    to_copy, already, skipped = [], [], []

    print(f"Reading {staging}\n")
    for path in entries:
        item = inspect(str(path))
        if not item["video"]:
            skipped.append((item, item.get("problem", "no video")))
            continue
        try:
            state = plex_state(item)
        except PlexUnavailable as e:
            print(f"Plex is unavailable, stopping: {e}", file=sys.stderr)
            db.close()
            return 2
        if state["in_plex"]:
            already.append((item, state))
            continue
        library = library_for(item, args.library)
        if not library:
            skipped.append((item, "no library in config to put it in"))
            continue
        folder, why = choose_destination(item, library)
        if folder is None:
            skipped.append((item, why))
            continue
        to_copy.append((item, library, folder, why))

    if already:
        print(f"Already in Plex — staging copy can be deleted ({len(already)}):")
        for item, state in already:
            m = state["matches"][0]
            print(f"  {item['title'][:36]:38s} {item['size']/1024**3:5.1f} GB  "
                  f"-> {m['title']} ({m['year']}) in {', '.join(m['libraries'])}")
        reclaim = sum(i["size"] for i, _ in already) / 1024**3
        print(f"  {reclaim:.1f} GB of staging space is duplicated on the shelves\n")

    if skipped:
        print(f"Skipped ({len(skipped)}):")
        for item, why in skipped:
            print(f"  {item['name'][:52]:54s} {why}")
        print()

    if not to_copy:
        print("Nothing to import.")
        db.close()
        return 0

    print(f"{'Would copy' if not args.go else 'Copying'} ({len(to_copy)}):")
    for item, library, folder, why in to_copy:
        dest = os.path.join(folder, target_filename(item))
        print(f"  {item['title'][:34]:36s} {item['size']/1024**3:5.1f} GB  [{library}]")
        print(f"      -> {dest}")
        print(f"         ({why})")

    if not args.go:
        print("\nPlan only. Re-run with --go to carry it out.")
        db.close()
        return 0

    print()
    done = failed = 0
    for item, library, folder, _why in to_copy:
        dest = os.path.join(folder, target_filename(item))
        print(f"  copying {item['title']} ({item['size']/1024**3:.1f} GB)...", flush=True)
        ok, error = copy_file(item["video"], dest)
        if not ok:
            print(f"    FAILED: {error}", flush=True)
            failed += 1
            continue
        done += 1
        scanned, detail = notify_plex(library, folder)
        print(f"    copied; Plex scan of {detail}" if scanned else f"    copied; scan failed: {detail}",
              flush=True)
        closed = close_wanted(db, item)
        if closed:
            print(f"    wanted entry '{closed}' marked acquired", flush=True)

    db.close()
    print(f"\n{done} imported, {failed} failed.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
