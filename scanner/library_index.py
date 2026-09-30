"""What the library holds, kept on this machine, so a search knows before it asks.

Every search used to start by asking Plex, library by library, and the worst
case was a film the library does not have: nothing came back, so the next way
of looking was tried, and the next - the person search, the model, a run of
looser Plex searches, TMDb - one after another, most of it spent establishing a
negative. The discovery lists were worse still: every suggestion was checked by
a full title search of every library in turn.

This keeps a catalogue of every film and series on the server in a SQLite file
of its own, refreshed in the background, so "is it here?" is a lookup rather
than a round of searches. What the catalogue can answer on its own is "no". It
is read with the owner's token, which sees every library, so a title it does
not hold is a title nobody in the household can have. A "yes" is still
confirmed against Plex with the searcher's own token before anything is shown,
because Plex also decides who may see what: a share can leave out a library, or
hide a film by its rating, and the catalogue cannot know that.

It is a cache and never the only copy of anything. Delete the file and it is
rebuilt; if it cannot be opened, every search carries on asking Plex directly,
exactly as before, only slower.
"""

import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from rapidfuzz import fuzz, process

from config import config
from scanner import wanted_search as ws

ROOT = Path(__file__).resolve().parent.parent

# Bumped whenever the tables change shape or the way names are folded changes.
# A catalogue written under another version is thrown away and read again.
SCHEMA_VERSION = "1"

DEFAULTS = {
    "enabled": True,
    "background": True,
    "path": "library_index.db",
    "full_sync_hours": 6,
    "recent_sync_minutes": 5,
    "file_check_minutes": 60,
    "max_age_hours": 24,
}

# A miss is only believed if the look at newly added items is at least this
# recent; older than that and it is looked at again before answering, which
# costs one small request per library.
CONFIRM_SECONDS = 60
# How many of the newest items each library is asked for on that look.
RECENT_WINDOW = 60
# Page size for a full read. plexapi's own default of 100 meant a hundred round
# trips for ten thousand films.
PAGE = 500
# A full sync is held against other processes for at most this long, so one
# that died half way cannot lock the catalogue for good.
LEASE_SECONDS = 1800
# More candidates than this for one query and the catalogue declines to judge,
# rather than risk calling something missing on a truncated list.
CANDIDATE_LIMIT = 400
# Plex's numeric type for each kind of thing, as its listing URLs take them.
PLEX_TYPES = {"movie": 1, "show": 2, "season": 3, "episode": 4}
# Sorts after every word that starts with a given prefix.
_HIGH = "\U0010ffff"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sections (
    section_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    items INTEGER NOT NULL DEFAULT 0,
    full_synced_at REAL,
    recent_checked_at REAL,
    last_error TEXT
);

-- One row per film or series, as Plex lists it.
CREATE TABLE IF NOT EXISTS items (
    rating_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    section_key TEXT NOT NULL,
    title TEXT NOT NULL,
    original_title TEXT,
    year INTEGER,
    tmdb_id TEXT,
    imdb_id TEXT,
    tvdb_id TEXT,
    summary TEXT,
    thumb TEXT,
    art TEXT,
    content_rating TEXT,
    duration_ms INTEGER,
    audience_rating REAL,
    rating REAL,
    genres TEXT NOT NULL DEFAULT '[]',
    seasons INTEGER,
    episodes INTEGER,
    added_at INTEGER,
    updated_at INTEGER,
    files TEXT NOT NULL DEFAULT '[]',
    -- availability() of the files above, as JSON, with a one-word summary
    -- beside it for counting. Cleared whenever the files change.
    file_state TEXT,
    file_verdict TEXT,
    file_checked_at REAL,
    generation INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_section ON items (section_key, kind);
CREATE INDEX IF NOT EXISTS idx_items_tmdb ON items (tmdb_id);
CREATE INDEX IF NOT EXISTS idx_items_imdb ON items (imdb_id);

-- Every folded word of every title and original title, for finding candidates
-- by word prefix the way Plex's own title search matches.
CREATE TABLE IF NOT EXISTS words (
    word TEXT NOT NULL,
    rating_key TEXT NOT NULL,
    PRIMARY KEY (word, rating_key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_words_item ON words (rating_key, word);

CREATE TABLE IF NOT EXISTS seasons (
    rating_key TEXT NOT NULL,
    season INTEGER NOT NULL,
    episodes INTEGER,
    PRIMARY KEY (rating_key, season)
) WITHOUT ROWID;

-- The directors and actors each film library files things under, so naming a
-- person is not a fresh listing of every actor in every library.
CREATE TABLE IF NOT EXISTS people (
    section_key TEXT NOT NULL,
    role TEXT NOT NULL,
    folded TEXT NOT NULL,
    spelled TEXT NOT NULL,
    PRIMARY KEY (section_key, role, folded, spelled)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS people_sync (
    section_key TEXT NOT NULL,
    role TEXT NOT NULL,
    synced_at REAL NOT NULL,
    PRIMARY KEY (section_key, role)
) WITHOUT ROWID;
"""


class IndexUnavailable(RuntimeError):
    """The catalogue file cannot be used here. Searches carry on without it."""


# ---- settings and the file ------------------------------------------------

def settings():
    merged = dict(DEFAULTS)
    for key, value in (config.get("index") or {}).items():
        if value is not None:
            merged[key] = value
    return merged


def _number(key):
    try:
        return float(settings()[key])
    except (TypeError, ValueError, KeyError):
        return float(DEFAULTS[key])


def db_path():
    raw = str(settings().get("path") or DEFAULTS["path"]).strip()
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        path = ROOT / path
    return path


_availability = {"at": 0.0, "verdict": (False, "")}
_said = set()


def _say(message):
    """Tell the console once, not once per search."""
    if message not in _said:
        _said.add(message)
        print(f"library index: {message}")


def available():
    """Can the catalogue be used at all, and if not, why not.

    The folder the file lives in must already exist; it is never created. The
    catalogue is meant to live on a drive of the owner's choosing, and on a Mac
    a drive that is not mounted leaves nothing at /Volumes/<name> - creating the
    folder would quietly put the catalogue on the internal disk under a name
    that then shadows the real drive when it comes back.
    """
    now = time.time()
    if now - _availability["at"] < 5:
        return _availability["verdict"]
    if not settings().get("enabled", True):
        verdict = (False, "switched off in config.yaml (index.enabled)")
    else:
        folder = db_path().parent
        if folder.is_dir():
            verdict = (True, "")
        else:
            verdict = (False, f"the folder {folder} does not exist")
            _say(f"not in use: {verdict[1]}. Searches ask Plex directly until it does.")
    _availability.update(at=now, verdict=verdict)
    return verdict


_schema_ready = set()
_schema_lock = threading.Lock()


@contextmanager
def _connect():
    ok, why = available()
    if not ok:
        raise IndexUnavailable(why)
    path = str(db_path())
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 15000")
        if path not in _schema_ready:
            with _schema_lock:
                if path not in _schema_ready:
                    _prepare(conn)
                    _schema_ready.add(path)
        yield conn
    finally:
        conn.close()


def _prepare(conn):
    """Create the tables, or start again if they were written by another version."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    version = _meta(conn, "schema")
    if version is not None and version != SCHEMA_VERSION:
        for table in ("items", "words", "seasons", "people", "people_sync", "sections", "meta"):
            conn.execute(f"DELETE FROM {table}")
    _set_meta(conn, "schema", SCHEMA_VERSION)
    if _meta(conn, "arrivals_epoch") is None:
        # One check of the wanted list the first time this file is used, for
        # anything that arrived while nothing was watching for it.
        _set_meta(conn, "arrivals_epoch", 1)
    conn.commit()


def _meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(conn, key, value):
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def _meta_float(conn, key):
    try:
        return float(_meta(conn, key))
    except (TypeError, ValueError):
        return None


def _bump(conn):
    """Note that the catalogue changed, so in-memory copies of it are refreshed."""
    _set_meta(conn, "epoch", int(_meta(conn, "epoch") or 0) + 1)


def _note_arrivals(conn):
    """Note, inside the write that caused it, that the wanted list is worth checking again.

    Kept in the file rather than in memory, so it survives a restart and is
    seen by whichever process does the checking - a sync run by the script,
    or by a search, is otherwise invisible to the app's own worker - and it
    stays noted until a check of the whole list succeeds.
    """
    _set_meta(conn, "arrivals_epoch", int(_meta(conn, "arrivals_epoch") or 0) + 1)


def _marks(values):
    return ",".join("?" * len(values))


# ---- reading Plex's listings -----------------------------------------------

def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row(element, kind, section_key, generation):
    """One catalogue row from Plex's own XML for an item.

    Read from the raw listing rather than through plexapi's attributes: reading
    an attribute that came back empty makes plexapi fetch the whole item again,
    and ten thousand films have a lot of empty original titles.
    """
    attrib = element.attrib
    ids = {}
    for guid in element.findall("Guid"):
        scheme, _, value = (guid.attrib.get("id") or "").partition("://")
        if scheme in ("tmdb", "imdb", "tvdb") and value:
            ids.setdefault(scheme, value)
    files = [part.attrib["file"] for part in element.findall("Media/Part") if part.attrib.get("file")]
    genres = [tag.attrib["tag"] for tag in element.findall("Genre") if tag.attrib.get("tag")]
    return {
        "rating_key": str(attrib.get("ratingKey")),
        "kind": kind,
        "section_key": str(section_key),
        "title": (attrib.get("title") or "").strip(),
        "original_title": (attrib.get("originalTitle") or "").strip() or None,
        "year": _int(attrib.get("year")),
        "tmdb_id": ids.get("tmdb"),
        "imdb_id": ids.get("imdb"),
        "tvdb_id": ids.get("tvdb"),
        "summary": (attrib.get("summary") or "").strip(),
        "thumb": attrib.get("thumb"),
        "art": attrib.get("art"),
        "content_rating": attrib.get("contentRating") or "",
        "duration_ms": _int(attrib.get("duration")),
        "audience_rating": _float(attrib.get("audienceRating")),
        "rating": _float(attrib.get("rating")),
        "genres": json.dumps(genres),
        "seasons": _int(attrib.get("childCount")),
        "episodes": _int(attrib.get("leafCount")),
        "added_at": _int(attrib.get("addedAt")),
        "updated_at": _int(attrib.get("updatedAt")),
        "files": json.dumps(files),
        "generation": generation,
    }


ITEM_COLUMNS = ("rating_key", "kind", "section_key", "title", "original_title", "year",
                "tmdb_id", "imdb_id", "tvdb_id", "summary", "thumb", "art", "content_rating",
                "duration_ms", "audience_rating", "rating", "genres", "seasons", "episodes",
                "added_at", "updated_at", "files", "generation")

_UPSERT = (
    f"INSERT INTO items ({', '.join(ITEM_COLUMNS)}) VALUES ({_marks(ITEM_COLUMNS)}) "
    "ON CONFLICT(rating_key) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in ITEM_COLUMNS if c != "rating_key")
    # A file check is only good for the files it looked at.
    + ", file_state = CASE WHEN items.files = excluded.files THEN items.file_state END"
    + ", file_verdict = CASE WHEN items.files = excluded.files THEN items.file_verdict END"
    + ", file_checked_at = CASE WHEN items.files = excluded.files THEN items.file_checked_at END"
)


def _words(*names):
    found = set()
    for name in names:
        found.update(word for word in ws.fold(name).split() if word)
    return found


def _store(conn, rows):
    """Write rows, and their words wherever a name changed.

    Returns the keys that could now answer a wanted entry when they could not
    before: new ones, and ones whose name, year or TMDb id changed (a Fix
    Match turns an item into another film under the same key) or whose files
    changed (a copy put back).
    """
    if not rows:
        return []
    keys = [row["rating_key"] for row in rows]
    before = {}
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        for old in conn.execute(
                f"SELECT rating_key, title, original_title, year, tmdb_id, files FROM items "
                f"WHERE rating_key IN ({_marks(chunk)})", chunk):
            before[old["rating_key"]] = tuple(old[c] for c in _IDENTITY)
    conn.executemany(_UPSERT, [tuple(row[c] for c in ITEM_COLUMNS) for row in rows])
    renamed = [row for row in rows
               if before.get(row["rating_key"], (None, None))[:2] != (row["title"], row["original_title"])]
    conn.executemany("DELETE FROM words WHERE rating_key = ?",
                     [(row["rating_key"],) for row in renamed])
    conn.executemany("INSERT OR IGNORE INTO words (word, rating_key) VALUES (?, ?)",
                     [(word, row["rating_key"]) for row in renamed
                      for word in _words(row["title"], row["original_title"])])
    return [row["rating_key"] for row in rows
            if before.get(row["rating_key"]) != tuple(row[c] for c in _IDENTITY)]


# What an item is, as far as answering a wanted entry goes.
_IDENTITY = ("title", "original_title", "year", "tmdb_id", "files")


def _drop(conn, where, params):
    """Delete items matching a condition, with everything hanging off them."""
    doomed = [r["rating_key"] for r in conn.execute(f"SELECT rating_key FROM items WHERE {where}", params)]
    for table in ("words", "seasons", "items"):
        conn.executemany(f"DELETE FROM {table} WHERE rating_key = ?", [(key,) for key in doomed])
    return len(doomed)


def _owner_server():
    return ws.get_server()


def _sections_now(server):
    """Plex's film and series libraries as they are now.

    plexapi remembers the list from the first time it is asked for the life of
    the server object, so a library added after this app started would never
    be read.
    """
    library = server.library
    library.__dict__.pop("_loadSections", None)
    return [s for s in library.sections() if s.type in ("movie", "show")]


def _listing(section, kind, sort=None, limit=None):
    """Everything of one kind in a library, as plexapi objects built from the listing."""
    key = f"/library/sections/{section.key}/all?type={PLEX_TYPES[kind]}&includeGuids=1"
    if sort:
        key += f"&sort={sort}"
    if limit:
        return section.fetchItems(key, container_size=limit, maxresults=limit)
    return section.fetchItems(key, container_size=PAGE)


# ---- keeping it up to date --------------------------------------------------

@contextmanager
def _lease():
    """Hold the catalogue for a full sync, across processes; yields whether it was got."""
    holder = f"{os.getpid()}:{threading.get_ident()}"
    now = time.time()
    got = False
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _meta(conn, "lease_holder")
        until = _meta_float(conn, "lease_until") or 0
        if not current or until < now or current == holder:
            _set_meta(conn, "lease_holder", holder)
            _set_meta(conn, "lease_until", now + LEASE_SECONDS)
            got = True
        conn.commit()
    try:
        yield got
    finally:
        if got:
            with _connect() as conn:
                if _meta(conn, "lease_holder") == holder:
                    conn.execute("DELETE FROM meta WHERE key IN ('lease_holder', 'lease_until')")
                    conn.commit()


def full_sync(progress=None):
    """Read every film and series library and make the catalogue match Plex.

    A library that fails part way keeps what it had: only a library read to
    the end has anything removed, so an outage never empties the catalogue.
    """
    say = progress or (lambda message: None)
    server = _owner_server()
    with _lease() as held:
        if not held:
            return {"skipped": "another sync is already running"}
        started = time.time()
        with _connect() as conn:
            machine = str(getattr(server, "machineIdentifier", "") or "")
            known = _meta(conn, "machine")
            if known and machine and known != machine:
                # A different server entirely: nothing here is true of it.
                for table in ("items", "words", "seasons", "people", "people_sync", "sections"):
                    conn.execute(f"DELETE FROM {table}")
            if machine:
                _set_meta(conn, "machine", machine)
            generation = int(_meta(conn, "generation") or 0) + 1
            _set_meta(conn, "generation", generation)
            conn.commit()

        sections = _sections_now(server)
        report = {}
        for section in sections:
            try:
                count = _sync_section(section, generation)
                report[section.title] = count
                say(f"{section.title}: {count:,} {'films' if section.type == 'movie' else 'series'}")
            except Exception as e:
                report[section.title] = f"failed: {e}"
                say(f"{section.title}: failed ({e}); keeping what the catalogue had")
                with _connect() as conn:
                    conn.execute("UPDATE sections SET last_error = ? WHERE section_key = ?",
                                 (str(e)[:300], str(section.key)))
                    conn.commit()

        with _connect() as conn:
            live = {str(s.key) for s in sections}
            for row in conn.execute("SELECT section_key FROM sections").fetchall():
                if row["section_key"] not in live:
                    _drop(conn, "section_key = ?", (row["section_key"],))
                    for table in ("people", "people_sync", "sections"):
                        conn.execute(f"DELETE FROM {table} WHERE section_key = ?", (row["section_key"],))
            _set_meta(conn, "full_synced_at", time.time())
            _set_meta(conn, "full_sync_seconds", round(time.time() - started, 1))
            _bump(conn)
            conn.commit()
        return report


def _sync_section(section, generation):
    kind = section.type
    rows = [_row(item._data, kind, section.key, generation)
            for item in _listing(section, kind) if item._data.attrib.get("ratingKey")]
    seasons = []
    if kind == "show":
        for season in _listing(section, "season"):
            attrib = season._data.attrib
            show, number = attrib.get("parentRatingKey"), _int(attrib.get("index"))
            if show and number is not None:
                seasons.append((str(show), number, _int(attrib.get("leafCount"))))
    people = _people_of(section) if kind == "movie" else []

    now = time.time()
    key = str(section.key)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        had = {(r["rating_key"], r["season"]) for r in conn.execute(
            "SELECT rating_key, season FROM seasons WHERE rating_key IN "
            "(SELECT rating_key FROM items WHERE section_key = ?)", (key,))}
        changed = _store(conn, rows)
        # A new season of a series already here is an arrival too, though the
        # series itself is not new.
        if changed or any((show, number) not in had for show, number, _count in seasons):
            _note_arrivals(conn)
        _drop(conn, "section_key = ? AND generation < ?", (key, generation))
        if kind == "show":
            conn.execute("DELETE FROM seasons WHERE rating_key IN "
                         "(SELECT rating_key FROM items WHERE section_key = ?)", (key,))
            conn.executemany("INSERT OR REPLACE INTO seasons (rating_key, season, episodes) "
                             "VALUES (?, ?, ?)", seasons)
        for role, names in people:
            conn.execute("DELETE FROM people WHERE section_key = ? AND role = ?", (key, role))
            conn.executemany("INSERT OR IGNORE INTO people (section_key, role, folded, spelled) "
                             "VALUES (?, ?, ?, ?)", [(key, role, f, s) for f, s in names])
            conn.execute("INSERT OR REPLACE INTO people_sync (section_key, role, synced_at) "
                         "VALUES (?, ?, ?)", (key, role, now))
        conn.execute(
            "INSERT INTO sections (section_key, title, kind, items, full_synced_at, recent_checked_at, "
            "last_error) VALUES (?, ?, ?, ?, ?, ?, NULL) ON CONFLICT(section_key) DO UPDATE SET "
            "title = excluded.title, kind = excluded.kind, items = excluded.items, "
            "full_synced_at = excluded.full_synced_at, recent_checked_at = excluded.recent_checked_at, "
            "last_error = NULL",
            (key, section.title, kind, len(rows), now, now))
        _bump(conn)
        conn.commit()
    return len(rows)


def _people_of(section):
    """Each role's names in one film library, folded for matching and as spelled."""
    found = []
    for role in ("director", "actor"):
        try:
            choices = section.listFilterChoices(role)
        except Exception:
            continue    # this library does not offer the filter; searches ask Plex instead
        found.append((role, sorted({(ws.fold(c.title), c.title) for c in choices if c.title})))
    return found


_recent_lock = threading.Lock()


def recent_sync(section_keys=None):
    """Look at what each library added most recently and bring it in.

    One small request per library, sorted newest first, so a film added a
    minute ago is known to the catalogue without a full read. Returns the keys
    of the libraries it managed to look at.
    """
    with _connect() as conn:
        generation = int(_meta(conn, "generation") or 0)
        known = {r["section_key"]: r["kind"] for r in conn.execute("SELECT section_key, kind FROM sections")}
    if not generation or not known:
        return set()
    wanted = {str(k) for k in section_keys} if section_keys else set(known)
    server = _owner_server()
    targets = [s for s in server.library.sections() if str(s.key) in wanted and str(s.key) in known]

    def newest(section):
        kind = known[str(section.key)]
        return [_row(item._data, kind, section.key, generation)
                for item in _listing(section, kind, sort="addedAt:desc", limit=RECENT_WINDOW)
                if item._data.attrib.get("ratingKey")]

    def newest_episodes(section):
        # A new season of a series already here does not make the series
        # itself recent; its newest episodes do.
        pairs = []
        for episode in _listing(section, "episode", sort="addedAt:desc", limit=RECENT_WINDOW):
            attrib = episode._data.attrib
            show, number = attrib.get("grandparentRatingKey"), _int(attrib.get("parentIndex"))
            if show and number is not None:
                pairs.append((str(show), number))
        return pairs

    # Every listing is a request of its own, so all of them go at once. A TV
    # library's two used to follow one another, and a search that found the
    # look stale waited for both before it could say anything.
    jobs = [(section, False) for section in targets]
    jobs += [(section, True) for section in targets if known[str(section.key)] == "show"]

    def look(job):
        section, episodes = job
        return newest_episodes(section) if episodes else newest(section)

    with ThreadPoolExecutor(max_workers=max(1, min(len(jobs), 8))) as pool:
        answers = dict(zip([(str(section.key), episodes) for section, episodes in jobs],
                           pool.map(_quietly(look), jobs)))
    looked = []
    for section in targets:
        key = str(section.key)
        rows = answers.get((key, False))
        pairs = answers.get((key, True)) if known[key] == "show" else []
        # Half a look is no look: a library counts only when every listing
        # asked of it came back.
        if rows is not None and pairs is not None:
            looked.append((key, rows, pairs))

    now = time.time()
    arrived = False
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for key, rows, pairs in looked:
            arrived = bool(_store(conn, rows)) or arrived
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO seasons (rating_key, season) VALUES (?, ?)", pairs)
            arrived = arrived or conn.total_changes > before
            conn.execute("UPDATE sections SET recent_checked_at = ? WHERE section_key = ?", (now, key))
        if arrived:
            _note_arrivals(conn)
        if looked:
            _bump(conn)
        conn.commit()
    if arrived:
        _worker.poke()
    return {key for key, _rows, _pairs in looked}


def _quietly(fn):
    """A version of fn that returns None instead of raising, for pool.map."""
    def wrapped(*args):
        try:
            return fn(*args)
        except Exception:
            return None
    return wrapped


def forget_recent():
    """Make the next miss look at new additions again before it is believed."""
    try:
        with _connect() as conn:
            conn.execute("UPDATE sections SET recent_checked_at = NULL")
            conn.commit()
    except (IndexUnavailable, sqlite3.Error):
        pass


def add_or_refresh(rating_key):
    """Bring one item into the catalogue now - for Plex's library.new webhook.

    An episode or a season brings in its whole series, since that is what the
    catalogue holds. Returns the title brought in, or None.
    """
    server = _owner_server()
    item = server.fetchItem(int(rating_key))
    attrib = item._data.attrib
    kind = attrib.get("type")
    if kind in ("episode", "season"):
        parent = attrib.get("grandparentRatingKey") if kind == "episode" else attrib.get("parentRatingKey")
        if not parent:
            return None
        item = server.fetchItem(int(parent))
        attrib, kind = item._data.attrib, "show"
    if kind not in ("movie", "show"):
        return None
    section_key = str(attrib.get("librarySectionID") or getattr(item, "librarySectionID", "") or "")
    with _connect() as conn:
        section = conn.execute("SELECT kind FROM sections WHERE section_key = ?", (section_key,)).fetchone()
        generation = int(_meta(conn, "generation") or 0)
    if section is None or not generation:
        return None     # a library the catalogue has not read yet; the next full sync will
    row = _row(item._data, kind, section_key, generation)
    seasons = []
    if kind == "show":
        for season in server.fetchItems(f"/library/metadata/{row['rating_key']}/children"):
            number = _int(season._data.attrib.get("index"))
            if number is not None:
                seasons.append((row["rating_key"], number, _int(season._data.attrib.get("leafCount"))))
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        had = {r["season"] for r in conn.execute(
            "SELECT season FROM seasons WHERE rating_key = ?", (row["rating_key"],))}
        if _store(conn, [row]) or any(number not in had for _key, number, _count in seasons):
            _note_arrivals(conn)
        if kind == "show":
            conn.execute("DELETE FROM seasons WHERE rating_key = ?", (row["rating_key"],))
            conn.executemany("INSERT OR REPLACE INTO seasons (rating_key, season, episodes) "
                             "VALUES (?, ?, ?)", seasons)
        _bump(conn)
        conn.commit()
    return row["title"]


def check_files(progress=None):
    """Look at the files behind every film, on this thread, and remember the answers.

    This is the disk check a search used to do for itself, film by film, while
    somebody waited. Done here it can take as long as the disks need.
    """
    say = progress or (lambda message: None)
    with _connect() as conn:
        films = conn.execute("SELECT rating_key, files FROM items WHERE kind = 'movie'").fetchall()
    verdicts = {"ok": 0, "missing": 0, "unknown": 0}
    results = []
    for film in films:
        state = ws.files_state(json.loads(film["files"]))
        verdict = ("unknown" if state["store_offline"]
                   else "missing" if state["all_missing"] else "ok")
        verdicts[verdict] += 1
        results.append((json.dumps(state), verdict, time.time(), film["rating_key"], film["files"]))
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        was = {(r["rating_key"], r["files"]): r["file_verdict"] for r in conn.execute(
            "SELECT rating_key, files, file_verdict FROM items WHERE kind = 'movie'")}
        # Only where the files are still the ones that were looked at.
        conn.executemany("UPDATE items SET file_state = ?, file_verdict = ?, file_checked_at = ? "
                         "WHERE rating_key = ? AND files = ?", results)
        # A file put back, or a drive back online, can be the arrival of
        # something on the wanted list - "Plex lists this but the file is
        # missing" is settled by exactly that.
        if any(verdict == "ok" and (key, files) in was and was[(key, files)] != "ok"
               for _state, verdict, _at, key, files in results):
            _note_arrivals(conn)
        _set_meta(conn, "files_checked_at", time.time())
        conn.commit()
    say(f"files: {verdicts['ok']:,} fine, {verdicts['missing']:,} missing, "
        f"{verdicts['unknown']:,} on drives that could not be read")
    return verdicts


# ---- answering questions ----------------------------------------------------

def trusted(section_keys, kind):
    """Can a miss in these libraries be believed?

    Only when every one of them has been read in full recently enough, and the
    look at what was added since is fresh. If that look is stale it is done now,
    before answering - one small request per library - and if Plex cannot be
    asked, the answer is no and the search asks Plex itself, as it always did.
    """
    keys = sorted({str(k) for k in section_keys})
    if not keys:
        return False
    try:
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT section_key, kind, full_synced_at, recent_checked_at FROM sections "
                f"WHERE section_key IN ({_marks(keys)})", keys).fetchall()
    except (IndexUnavailable, sqlite3.Error):
        return False
    now = time.time()
    oldest = _number("max_age_hours") * 3600
    if (len(rows) != len(keys) or any(r["kind"] != kind for r in rows)
            or any(not r["full_synced_at"] or now - r["full_synced_at"] > oldest for r in rows)):
        _worker.poke(full=True)
        return False
    stale = [r["section_key"] for r in rows
             if not r["recent_checked_at"] or now - r["recent_checked_at"] > CONFIRM_SECONDS]
    if not stale:
        return True
    # One look serves everybody waiting: a burst of searches must not each
    # send their own round of requests to Plex.
    if not _recent_lock.acquire(timeout=10):
        return False
    try:
        cutoff = time.time() - CONFIRM_SECONDS
        with _connect() as conn:
            fresh = {r["section_key"] for r in conn.execute(
                f"SELECT section_key FROM sections WHERE section_key IN ({_marks(stale)}) "
                f"AND recent_checked_at > ?", stale + [cutoff])}
            # The other kind's libraries come along when theirs is as old. A
            # title search asks about series and then about films, and each
            # found the look stale in turn: two waits on Plex, one after the
            # other, before either had been searched.
            also = [r["section_key"] for r in conn.execute(
                "SELECT section_key FROM sections WHERE recent_checked_at IS NULL "
                "OR recent_checked_at <= ?", (cutoff,)) if r["section_key"] not in stale]
        remaining = [key for key in stale if key not in fresh]
        if remaining:
            looked = recent_sync(remaining + also)
            if set(remaining) - looked:
                return False
        return True
    except Exception:
        return False
    finally:
        _recent_lock.release()


def _candidate_rows(conn, query, kind, section_keys):
    """Items whose names contain every word of the query as a word prefix.

    Returns (rows, complete); complete is False when there were too many to
    judge, and a caller must not treat that as knowing anything.
    """
    words = sorted(set(ws.fold(query).split()), key=len, reverse=True)
    if not words:
        return [], False
    first, rest = words[0], words[1:]
    # CROSS JOIN fixes the order: start from the rows for the query's longest
    # word and look each item up from there. Left to choose, SQLite walked
    # every item in the libraries and checked its words - 4ms a lookup on ten
    # thousand films rather than a few microseconds.
    sql = ["SELECT DISTINCT i.* FROM words w0 CROSS JOIN items i ON i.rating_key = w0.rating_key",
           "WHERE w0.word >= ? AND w0.word < ? AND i.kind = ?"]
    params = [first, first + _HIGH, kind]
    if section_keys is not None:
        sql.append(f"AND i.section_key IN ({_marks(section_keys)})")
        params.extend(section_keys)
    for word in rest:
        sql.append("AND EXISTS (SELECT 1 FROM words w WHERE w.rating_key = i.rating_key "
                   "AND w.word >= ? AND w.word < ?)")
        params.extend([word, word + _HIGH])
    sql.append(f"LIMIT {CANDIDATE_LIMIT + 1}")
    rows = conn.execute(" ".join(sql), params).fetchall()
    if len(rows) > CANDIDATE_LIMIT:
        return rows[:CANDIDATE_LIMIT], False
    return rows, True


def candidates(query, kind, section_keys):
    """[(confidence, row)] for everything in these libraries that could be the title typed.

    None when the catalogue cannot say.
    """
    keys = sorted({str(k) for k in section_keys})
    try:
        with _connect() as conn:
            rows, complete = _candidate_rows(conn, query, kind, keys)
    except (IndexUnavailable, sqlite3.Error):
        return None
    if not complete:
        return None
    scored = []
    for row in rows:
        confidence = ws.best_confidence(query, row["title"], row["original_title"])
        if confidence:
            scored.append((confidence, row))
    return scored


def lookup(query, kind, sections):
    """[(section_key, rating_key, title)] held under this name in these libraries.

    None when the catalogue cannot say. An empty list is the useful answer:
    nothing anywhere could match, so there is no need to ask Plex at all.
    """
    if not sections:
        return []
    keys = [str(s.key) for s in sections]
    try:
        if not trusted(keys, kind):
            return None
        found = candidates(query, kind, keys)
    except Exception:
        return None
    if found is None:
        return None
    return [(row["section_key"], row["rating_key"], row["title"]) for _confidence, row in found]


def owned(titles, kind, section_keys):
    """For each suggestion, whether these libraries already hold it; None when unknown.

    Matched by TMDb id first, which is exact, and otherwise by name and year the
    way a title search would call it certain. A film whose every file is gone
    does not count as held - that is the one Plex lists but cannot play.
    """
    keys = sorted({str(k) for k in section_keys})
    if not keys:
        return [False] * len(titles)
    if not trusted(keys, kind):
        return None
    verdicts = []
    try:
        with _connect() as conn:
            for suggestion in titles:
                verdicts.append(_holds(conn, suggestion, kind, keys))
    except (IndexUnavailable, sqlite3.Error):
        return None
    return verdicts


def _holds(conn, suggestion, kind, keys):
    rows = []
    tmdb = suggestion.get("tmdb_id")
    if tmdb:
        rows = conn.execute(
            f"SELECT * FROM items WHERE tmdb_id = ? AND kind = ? AND section_key IN ({_marks(keys)})",
            [str(tmdb), kind] + keys).fetchall()
    if not rows:
        # By name: the same rule as answering a request (ws.answers) - the year
        # asked for, or one either side for exactly the same name, and never
        # something filed under a different TMDb id. Wicked: For Good (2025)
        # does not hold Wicked (2024).
        title, year = suggestion.get("title") or "", suggestion.get("year")
        found, _complete = _candidate_rows(conn, title, kind, keys)
        named = [dict(r, confidence=ws.best_confidence(title, r["title"], r["original_title"]))
                 for r in found if not tmdb or r["tmdb_id"] in (None, str(tmdb))]
        held = {m["rating_key"] for m in ws.answers(named, title, year)}
        rows = [r for r in found if r["rating_key"] in held]
    if kind == "movie":
        rows = [r for r in rows if r["file_verdict"] != "missing"]
    return bool(rows)


def file_state(rating_key, files):
    """The last disk check of exactly these files, if it is recent enough to trust.

    None sends the caller to the disk itself: a check of other files, or one
    older than a few rounds of the background checker, says nothing about now.
    """
    if rating_key is None:
        return None
    try:
        with _connect() as conn:
            row = conn.execute("SELECT files, file_state, file_checked_at FROM items WHERE rating_key = ?",
                               (str(rating_key),)).fetchone()
    except (IndexUnavailable, sqlite3.Error):
        return None
    if not row or not row["file_state"] or not row["file_checked_at"]:
        return None
    if json.loads(row["files"]) != list(files or []):
        return None
    oldest = max(3 * _number("file_check_minutes") * 60, 900)
    if time.time() - row["file_checked_at"] > oldest:
        return None
    return json.loads(row["file_state"])


def _recent_enough(conn):
    synced = _meta_float(conn, "full_synced_at")
    return bool(synced) and time.time() - synced <= _number("max_age_hours") * 3600


def seasons_of(rating_key):
    """The season numbers held of a series, or None when the catalogue does not know."""
    try:
        with _connect() as conn:
            if not _recent_enough(conn):
                return None
            show = conn.execute("SELECT kind, seasons FROM items WHERE rating_key = ?",
                                (str(rating_key),)).fetchone()
            if not show or show["kind"] != "show":
                return None
            rows = conn.execute("SELECT season FROM seasons WHERE rating_key = ? ORDER BY season",
                                (str(rating_key),)).fetchall()
    except (IndexUnavailable, sqlite3.Error):
        return None
    if not rows and (show["seasons"] or 0) > 0:
        return None
    return [r["season"] for r in rows]


def people_named(folded_name, section_keys, role):
    """How these libraries spell a person whose name contains these words.

    The same rule as the live lookup it stands in for: an exact match, or the
    name appearing as whole words inside a longer one. None when any of the
    libraries has not had its people read.
    """
    keys = sorted({str(k) for k in section_keys})
    if not keys or not folded_name:
        return None
    escaped = folded_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    try:
        with _connect() as conn:
            if not _recent_enough(conn):
                return None
            synced = conn.execute(
                f"SELECT COUNT(*) FROM people_sync WHERE role = ? AND section_key IN ({_marks(keys)})",
                [role] + keys).fetchone()[0]
            if synced != len(keys):
                return None
            rows = conn.execute(
                f"SELECT folded, spelled FROM people WHERE role = ? AND section_key IN ({_marks(keys)}) "
                "AND (folded = ? OR folded LIKE ? ESCAPE '\\' OR folded LIKE ? ESCAPE '\\' "
                "OR folded LIKE ? ESCAPE '\\')",
                [role] + keys + [folded_name, f"{escaped} %", f"% {escaped}", f"% {escaped} %"]).fetchall()
    except (IndexUnavailable, sqlite3.Error):
        return None
    found = {}
    for row in rows:
        found[row["folded"]] = row["spelled"]
    return list(found.values())


_names = {"key": None, "choices": {}}
_SUBTITLE = re.compile(r"\s*:\s*|\s+[-\u2013\u2014]\s+")


def near_misses(query, kind, section_keys, limit=3, cutoff=85):
    """Titles spelled nearly like the query: "anchroman" is probably Anchorman.

    Returns [(rating_key, title, section_key)], best first; never certain, only
    ever offered as a maybe. Uses the whole catalogue of names, kept in memory
    and refreshed when the catalogue changes.
    """
    wanted = ws.fold(query)
    if len(wanted) < 4:
        return []
    keys = {str(k) for k in section_keys}
    try:
        with _connect() as conn:
            epoch = (str(db_path()), kind, _meta(conn, "epoch"))
            if _names["key"] != epoch:
                choices = {}
                for row in conn.execute("SELECT rating_key, section_key, title, original_title "
                                        "FROM items WHERE kind = ?", (kind,)):
                    for name in (row["title"], row["original_title"]):
                        if not name:
                            continue
                        # The name before any subtitle too: "anchroman" is
                        # nearly "Anchorman", nowhere near all of "Anchorman:
                        # The Legend of Ron Burgundy".
                        for variant in {name, _SUBTITLE.split(name, maxsplit=1)[0]}:
                            folded = ws.fold(ws.without_article(variant))
                            if folded:
                                choices[(row["rating_key"], variant)] = (folded, row["section_key"], row["title"])
                _names.update(key=epoch, choices=choices)
    except (IndexUnavailable, sqlite3.Error):
        return []
    usable = {ident: folded for ident, (folded, section, _t) in _names["choices"].items() if section in keys}
    ranked = process.extract(ws.fold(ws.without_article(query)), usable,
                             scorer=fuzz.ratio, score_cutoff=cutoff, limit=limit * 3)
    picked, seen = [], set()
    for _choice, _score, ident in ranked:
        rating_key = ident[0]
        if rating_key in seen:
            continue
        seen.add(rating_key)
        _folded, section, title = _names["choices"][ident]
        picked.append((rating_key, title, section))
        if len(picked) >= limit:
            break
    return picked


def catalogue(kind, section_keys):
    """Every item of a kind in these libraries, or None when the catalogue cannot vouch for it."""
    keys = sorted({str(k) for k in section_keys})
    if not trusted(keys, kind):
        return None
    try:
        with _connect() as conn:
            return conn.execute(
                f"SELECT * FROM items WHERE kind = ? AND section_key IN ({_marks(keys)})",
                [kind] + keys).fetchall()
    except (IndexUnavailable, sqlite3.Error):
        return None


def status():
    """Everything the console shows about the catalogue."""
    ok, why = available()
    report = {
        "enabled": bool(settings().get("enabled", True)),
        "available": ok,
        "reason": why or None,
        "path": str(db_path()),
        "working_on": _worker.busy,
        "background": bool(_worker.thread and _worker.thread.is_alive()),
        "last_error": _worker.last_error,
        "settle_error": _worker.settle_error,
    }
    if not ok:
        return report
    try:
        with _connect() as conn:
            counts = {r["kind"]: r["n"] for r in conn.execute(
                "SELECT kind, COUNT(*) AS n FROM items GROUP BY kind")}
            files = {r["file_verdict"] or "unchecked": r["n"] for r in conn.execute(
                "SELECT file_verdict, COUNT(*) AS n FROM items WHERE kind = 'movie' GROUP BY file_verdict")}
            report.update({
                "films": counts.get("movie", 0),
                "series": counts.get("show", 0),
                "seasons": conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0],
                "people": conn.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                "files": {name: files.get(name, 0) for name in ("ok", "missing", "unknown", "unchecked")},
                "full_synced_at": _meta_float(conn, "full_synced_at"),
                "full_sync_seconds": _meta_float(conn, "full_sync_seconds"),
                "files_checked_at": _meta_float(conn, "files_checked_at"),
                "arrivals_waiting": int(_meta(conn, "arrivals_epoch") or 0)
                                    > int(_meta(conn, "arrivals_settled") or 0),
                "sections": [dict(r) for r in conn.execute(
                    "SELECT section_key, title, kind, items, full_synced_at, recent_checked_at, last_error "
                    "FROM sections ORDER BY title")],
            })
    except sqlite3.Error as e:
        report.update(available=False, reason=f"the catalogue file could not be read: {e}")
        return report
    size = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            size += Path(f"{db_path()}{suffix}").stat().st_size
        except OSError:
            pass
    report["size_bytes"] = size
    report["ready"] = bool(report.get("full_synced_at")) and \
        time.time() - report["full_synced_at"] <= _number("max_age_hours") * 3600
    return report


def films_by_tmdb(tmdb_id):
    """Films on the shelves filed under this TMDb id, for the arrival check.

    Exact where a name and year are not: the film asked for by its TMDb id is
    the film Plex filed under that id, whatever either of them calls it. Only
    a film known to play counts - its files checked and found, or, not yet
    checked, found now. One whose files are gone, or on a drive that cannot
    be read just now, does not. None when the index cannot say.
    """
    try:
        with _connect() as conn:
            if not _recent_enough(conn):
                return None
            rows = conn.execute(
                "SELECT i.*, s.title AS library FROM items i "
                "LEFT JOIN sections s ON s.section_key = i.section_key "
                "WHERE i.tmdb_id = ? AND i.kind = 'movie'", (str(tmdb_id),)).fetchall()
    except (IndexUnavailable, sqlite3.Error):
        return None
    found = {}
    for r in rows:
        if r["file_verdict"] is None:
            state = ws.availability_of(json.loads(r["files"] or "[]"))
            if state["all_missing"] or ws._dropped(state):
                continue
        elif r["file_verdict"] == "unknown":
            # Unreadable from here. Only a drive that has dropped out stops
            # it counting; a store this app has never been able to read is
            # Plex's word against nobody's.
            if ws._dropped(json.loads(r["file_state"] or "{}") or {"store_offline": True}):
                continue
        elif r["file_verdict"] != "ok":
            continue
        # One film in two libraries is one answer, as search_plex gives it.
        key = ((r["title"] or "").lower(), r["year"])
        if key in found:
            if r["library"] and r["library"] not in found[key]["libraries"]:
                found[key]["libraries"].append(r["library"])
            continue
        # The same shape search_plex gives (Film 2a), so a caller cannot tell
        # which of them answered.
        found[key] = {"title": r["title"], "year": r["year"], "rating_key": r["rating_key"],
                      "original_title": r["original_title"] or "", "thumb": r["thumb"],
                      "summary": r["summary"] or "", "content_rating": r["content_rating"] or "",
                      "duration_ms": r["duration_ms"], "libraries": [r["library"]] if r["library"] else [],
                      "file_missing": False, "store_offline": False, "missing_files": [],
                      "added_at": r["added_at"] or 0, "confidence": 2}
    return list(found.values())


def tmdb_of(rating_keys):
    """{rating_key: TMDb id} for those of these items the index files under one."""
    keys = [str(k) for k in rating_keys if k]
    if not keys:
        return {}
    try:
        with _connect() as conn:
            return {r["rating_key"]: r["tmdb_id"] for r in conn.execute(
                f"SELECT rating_key, tmdb_id FROM items WHERE rating_key IN ({_marks(keys)}) "
                f"AND tmdb_id IS NOT NULL", keys)}
    except (IndexUnavailable, sqlite3.Error):
        return {}


# ---- telling the app about new arrivals ---------------------------------------

_arrivals = {"callback": None, "failed_epoch": None, "retry_at": 0.0, "failures": 0}
_deliver_lock = threading.Lock()
# After a check of the wanted list fails, how long before it is tried again:
# doubling from a minute, never longer than half an hour. Anything new
# arriving meanwhile is checked at once.
RETRY_FIRST, RETRY_MOST = 60, 1800


class SettleIncomplete(RuntimeError):
    """Part of the wanted list could not be checked, so it will be checked again."""


def on_arrivals(callback):
    """callback() checks the wanted list; it runs whenever the index has seen something new.

    One callback: the app registers its check of the list here. Without the
    webhook, which needs Plex Pass, this is how a film arriving is noticed.
    It should raise, or return a dict whose "failed" is non-zero, when it
    could not check everything; the same check is then made again later.
    """
    _arrivals["callback"] = callback


def arrivals_waiting():
    """Has anything been seen that the wanted list has not yet been checked against? None if unknown."""
    try:
        with _connect() as conn:
            return int(_meta(conn, "arrivals_epoch") or 0) > int(_meta(conn, "arrivals_settled") or 0)
    except (IndexUnavailable, sqlite3.Error):
        return None


def arrivals_epoch():
    """How many arrivals the index has noted so far, for a check to say it covered them. None if unknown."""
    try:
        with _connect() as conn:
            return int(_meta(conn, "arrivals_epoch") or 0)
    except (IndexUnavailable, sqlite3.Error):
        return None


def arrivals_checked(epoch):
    """A check of the whole wanted list, begun when the count was epoch, got through.

    Whoever made it - the worker, the webhook, the owner's button, the script
    - so none of the others repeats it, and a failure noted earlier is over.
    """
    if epoch is None:
        return
    _arrivals.update(failures=0, failed_epoch=None, retry_at=0.0)
    _worker.settle_error = None
    try:
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Only forward: another process may have checked a later round.
            if int(_meta(conn, "arrivals_settled") or 0) < epoch:
                _set_meta(conn, "arrivals_settled", epoch)
            conn.commit()
    except (IndexUnavailable, sqlite3.Error):
        pass


def deliver_arrivals(force=False):
    """Check the wanted list if anything arrived since the last complete check.

    True if it was checked, False if there was nothing to check, None if a
    failed check is waiting out its pause before the next try. Raises what
    the check raised; nothing is marked done until a check gets through the
    whole list, so an arrival is never dropped by a check that failed.
    """
    callback = _arrivals["callback"]
    if callback is None:
        return False
    with _deliver_lock:
        try:
            with _connect() as conn:
                epoch = int(_meta(conn, "arrivals_epoch") or 0)
                settled = int(_meta(conn, "arrivals_settled") or 0)
        except (IndexUnavailable, sqlite3.Error):
            return False
        if epoch <= settled:
            return False
        if (not force and _arrivals["failed_epoch"] == epoch
                and time.time() < _arrivals["retry_at"]):
            return None
        try:
            outcome = callback()
            if isinstance(outcome, dict) and outcome.get("failed"):
                raise SettleIncomplete(
                    f"{outcome['failed']} of {outcome.get('checked', '?')} could not be checked")
        except Exception:
            _arrivals["failures"] += 1
            _arrivals["failed_epoch"] = epoch
            _arrivals["retry_at"] = time.time() + min(
                RETRY_FIRST * 2 ** (_arrivals["failures"] - 1), RETRY_MOST)
            raise
        arrivals_checked(epoch)
        return True


# ---- the background worker ---------------------------------------------------

def _describe(error):
    """A failed sync, in words the owner can act on."""
    if isinstance(error, ws.PlexUnavailable):
        return ("Plex could not be reached, so the index kept what it had. "
                "It tries again on its own; searches ask Plex directly meanwhile.")
    if isinstance(error, IndexUnavailable):
        return f"The index file cannot be used: {error}."
    return str(error)[:300]


def _describe_settle(error):
    """A failed check of the wanted list, in words the owner can act on."""
    if isinstance(error, ws.PlexUnavailable):
        why = "Plex could not be reached"
    elif isinstance(error, SettleIncomplete):
        why = f"part of it could not be checked ({error})"
    else:
        why = str(error)[:200]
    return (f"The wanted list was not fully checked against new arrivals: {why}. "
            f"Nothing is lost - it is checked again shortly.")


class _Worker:
    """One thread that keeps the catalogue fresh on its own schedule."""

    def __init__(self):
        self.wake = threading.Event()
        self.full_wanted = False
        self.thread = None
        self.busy = None
        self.last_error = None
        self.settle_error = None
        # Set when the owner asks for a sync: a check that failed is tried
        # again then, rather than after its pause.
        self.settle_now = False

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="library-index", daemon=True)
        self.thread.start()

    def poke(self, full=False):
        if not (self.thread and self.thread.is_alive()):
            return False
        if full:
            self.full_wanted = True
        self.wake.set()
        return True

    def _loop(self):
        while True:
            self.round()
            self.wake.wait(60)
            self.wake.clear()

    def round(self):
        """One pass: keep the index fresh, then check the wanted list if anything arrived.

        The two fail apart. A sync that fails still leaves earlier arrivals to
        check, and a check that fails is not reported as the index failing.
        """
        try:
            self.tick()
            self.last_error = None
        except Exception as e:
            self.last_error = _describe(e)
        finally:
            self.busy = None
        self.settle()

    def settle(self):
        force, self.settle_now = self.settle_now, False
        try:
            self.busy = "checking the wanted list against new arrivals"
            if deliver_arrivals(force=force) is not None:
                self.settle_error = None
        except Exception as e:
            self.settle_error = _describe_settle(e)
        finally:
            self.busy = None

    def tick(self):
        if not available()[0]:
            return
        with _connect() as conn:
            full_at = _meta_float(conn, "full_synced_at")
            files_at = _meta_float(conn, "files_checked_at")
            recent = [r["recent_checked_at"] for r in conn.execute("SELECT recent_checked_at FROM sections")]
        now = time.time()
        if (self.full_wanted or not full_at
                or now - full_at > _number("full_sync_hours") * 3600):
            self.full_wanted = False
            self.busy = "reading every library"
            full_sync()
            # Searches ask for a full read while there is none yet; a read that
            # just finished answers them, so they must not queue another.
            self.full_wanted = False
            self.busy = "checking files"
            check_files()
            return
        oldest_look = min((r or 0 for r in recent), default=0)
        if now - oldest_look > _number("recent_sync_minutes") * 60:
            self.busy = "looking for new additions"
            recent_sync()
        if not files_at or now - files_at > _number("file_check_minutes") * 60:
            self.busy = "checking files"
            check_files()


_worker = _Worker()


def start_background():
    """Start keeping the catalogue fresh, unless config says not to.

    Not in the half of Flask's debug reloader that only watches files: that
    process never serves a request, and a second worker there would read every
    library twice.
    """
    chosen = settings()
    if not chosen.get("enabled", True) or not chosen.get("background", True):
        return False
    if (config.get("flask") or {}).get("debug") and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False
    _worker.start()
    return True


def request_sync(full=False):
    """Ask for a sync now: on the background worker if it is running, else on a thread of its own."""
    _worker.settle_now = True
    if _worker.poke(full=full):
        return "queued"

    def once():
        try:
            _worker.busy = "reading every library" if full else "looking for new additions"
            if full:
                full_sync()
                _worker.busy = "checking files"
                check_files()
            else:
                recent_sync()
            _worker.last_error = None
        except Exception as e:
            _worker.last_error = _describe(e)
        finally:
            _worker.busy = None
        _worker.settle()

    threading.Thread(target=once, name="library-index-once", daemon=True).start()
    return "started"
