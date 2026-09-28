"""Settling the wanted list: marking what has arrived and telling whoever asked.

Kept out of create_app so everything that can notice an arrival settles the
same way - the index worker, Plex's webhook, the owner's button, and the
sync script run on a schedule when the app's own worker is switched off.
"""

import threading

from api import push as web_push
from scanner import wanted_search as ws

# One check at a time in this process; two at once only repeat each other's
# searches. Across processes the claim below is what keeps it to one "it's here".
_lock = threading.Lock()


def label(row):
    """How an entry is named in "it's here": title, year, and series if it is one."""
    keys = row.keys()
    name = row["title"] + (f" ({row['year']})" if row["year"] else "")
    if "season" in keys and row["season"]:
        name += f", series {row['season']}"
    return name


def settle(db):
    """Check every wanted entry against the shelves; mark and tell for each that arrived.

    One entry that cannot be checked - Plex timing out on its search - does not
    stop the rest: an entry known by its TMDb id is answered from the index
    without asking Plex at all. The failures are counted and returned so the
    caller can try again; if every entry failed, Plex is down, and that is
    raised as it always was.

    Each entry is claimed before anybody is told (Database.claim_arrival), so
    of two checks that both see it arrive, only one sends "it's here".
    """
    with _lock:
        entries = db.get_wanted("wanted")
        settled, failed, first_error = [], 0, None
        for entry in entries:
            try:
                matches = ws.arrived(entry)
            except Exception as e:
                failed += 1
                first_error = first_error or e
                continue
            if not matches:
                continue
            row = db.claim_arrival(entry["id"])
            if row is None:
                continue    # somebody else's check got there first, and told them
            name = label(row)
            settled.append(name)
            # Everybody who asked hears about it; the owner if none of them does.
            web_push.tell_arrived(db, row, name)
    if entries and failed == len(entries) and isinstance(first_error, ws.PlexUnavailable):
        raise first_error
    return {"checked": len(entries), "settled": settled, "failed": failed}


def settle_now():
    """settle() on a database of its own, for callers with none open - the index worker, the script."""
    from db import Database

    db = Database()
    try:
        return settle(db)
    finally:
        db.close()
