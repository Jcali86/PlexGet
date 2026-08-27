#!/usr/bin/env python3
"""Seed the wanted list from a file of titles.

Each title is searched against Plex; only the misses are saved. Safe to run
more than once - titles already captured are reported, never duplicated.

One title per line, "Title (Year)" or just the title. Blank lines and lines
starting with # are ignored, so a list can carry its own headings.

    venv/bin/python scripts/seed_wanted.py titles.txt --dry-run
    venv/bin/python scripts/seed_wanted.py titles.txt
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import Database
from scanner.wanted_search import PlexUnavailable, search_and_capture


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path, help="file of titles, one per line")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    if not args.file.is_file():
        print(f"No such file: {args.file}", file=sys.stderr)
        return 2

    titles = [
        line.strip() for line in args.file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not titles:
        print(f"No titles in {args.file}.", file=sys.stderr)
        return 2

    db = Database()
    found = added = already = 0
    try:
        for title in titles:
            try:
                result = search_and_capture(db, title, capture=not args.dry_run)
            except PlexUnavailable as e:
                print(f"\nPlex is unavailable, stopping: {e}", file=sys.stderr)
                return 2

            if result["found"]:
                found += 1
                names = ", ".join(f"{m['title']} ({m['year']})" for m in result["matches"][:3])
                print(f"  in library  {title:58s} -> {names}")
            elif result["added_to_wanted"]:
                added += 1
                print(f"  ADDED       {title}")
            elif result["already_wanted"]:
                already += 1
                print(f"  on list     {title:58s} -> already tracked ({result['wanted']['status']})")
            else:
                existing = db.find_wanted(result["title"], result["year"])
                if existing is None:
                    added += 1
                    print(f"  WOULD ADD   {title}")
                else:
                    already += 1
                    print(f"  on list     {title:58s} -> already tracked ({existing['status']})")
    finally:
        db.close()

    verb = "would be added" if args.dry_run else "added"
    print(f"\n{len(titles)} titles checked: {found} already in Plex, "
          f"{added} {verb} to wanted, {already} already on the list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
