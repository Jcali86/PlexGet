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
    without asking Plex at all. Once Plex has failed, it is not asked again in
    the same check - each try can wait out a 20-second timeout, and a list of
    thirty would hold the check for ten minutes - so only entries the index
    can answer by id are looked at. The failures are counted and returned so
    the caller can try again; if every entry failed, Plex is down, and that is
    raised as it always was.

    Each entry is claimed before anybody is told (Database.claim_arrival), so
    of two checks that both see it arrive, only one sends "it's here". A check
    that got through every entry tells the index so, whoever made it, and the
    worker does not repeat it.
    """
    from scanner import library_index

    with _lock:
        epoch = library_index.arrivals_epoch()
        entries = db.get_wanted("wanted")
        settled, failed, first_error, plex_down = [], 0, None, False
        for entry in entries:
            try:
                matches = _by_id_only(entry) if plex_down else ws.arrived(entry)
            except ws.PlexUnavailable as e:
                plex_down = True
                matches = None
                first_error = first_error or e
            except Exception as e:
                matches = None
                first_error = first_error or e
            if matches is None:
                failed += 1
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
    if not failed:
        library_index.arrivals_checked(epoch)
    return {"checked": len(entries), "settled": settled, "failed": failed}


def _by_id_only(entry):
    """What the index alone says has arrived for an entry; None when it cannot say without Plex."""
    keys = entry.keys()
    if (entry["kind"] if "kind" in keys else "film") == "show":
        return None
    tmdb_id = entry["tmdb_id"] if "tmdb_id" in keys else None
    return ws.films_filed_as(tmdb_id, entry["year"]) or None


def settle_now():
    """settle() on a database of its own, for callers with none open - the index worker, the script."""
    from db import Database

    db = Database()
    try:
        return settle(db)
    finally:
        db.close()
