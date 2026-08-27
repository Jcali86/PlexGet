"""
Poster management module — coordinates with Posteria for all poster operations.

Posteria is the single poster authority: where it runs, who it logs in as, and
where it keeps its files are all config, because none of that is knowable from
here. This module NEVER writes to Plex's poster data directly. All poster
changes go through Posteria's import flow.
No overlays. None. Zero.
"""

import os
import re
import time
import shutil
import requests
import yaml
from pathlib import Path
from datetime import datetime
from plexapi.server import PlexServer
from thefuzz import fuzz
from config import config
from db import Database
from scanner import libraries

# ── Constants ────────────────────────────────────────────────────────────────

_POSTERIA = config.get("posteria") or {}
POSTERIA_URL = (_POSTERIA.get("url") or "http://localhost:1818").rstrip("/")
POSTERIA_USER = _POSTERIA.get("username") or ""
POSTERIA_PASS = _POSTERIA.get("password") or ""

# Posteria's own poster store. Alongside this checkout is where it lands when
# the two are installed together; point it elsewhere when they are not.
POSTER_DIR = Path(
    _POSTERIA.get("poster_dir")
    or Path(__file__).resolve().parent.parent / "posteria" / "posters"
)
POSTER_SUBDIRS = {
    "movie": "movies",
    "show": "tv-shows",
    "season": "tv-seasons",
    "collection": "collections",
}

_TMDB = config.get("tmdb") or {}
TMDB_BASE = _TMDB.get("base_url") or "https://api.themoviedb.org/3"
TMDB_KEY = _TMDB.get("api_key") or ""
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"
RATE_LIMIT_DELAY = 0.26

PLEX_URL = config["plex"]["url"]
PLEX_TOKEN = config["plex"]["token"]


# ── Posteria session management ──────────────────────────────────────────────

class PosteriaSession:
    """Authenticated session to Posteria's PHP endpoints."""

    def __init__(self):
        self.session = requests.Session()
        self._authenticated = False

    def login(self):
        if self._authenticated:
            return True
        # No sign-in named in config means no Posteria to talk to. Say so once,
        # quietly, rather than posting an empty login at whatever is listening
        # on that port.
        if not POSTERIA_USER:
            return False
        resp = self.session.post(
            f"{POSTERIA_URL}/src/include/login.php",
            data={"username": POSTERIA_USER, "password": POSTERIA_PASS},
            timeout=10,
        )
        # Posteria sets a PHP session cookie on successful login
        if resp.status_code == 200:
            self._authenticated = True
            return True
        return False

    def send_to_plex(self, filename, directory):
        """Tell Posteria to push a poster file to Plex.

        filename: the Posteria-convention filename (with ratingKey in brackets)
        directory: one of movies, tv-shows, tv-seasons, collections
        """
        self.login()
        resp = self.session.post(
            f"{POSTERIA_URL}/src/include/send-to-plex.php",
            data={
                "action": "send_to_plex",
                "filename": filename,
                "directory": directory,
            },
            timeout=30,
        )
        return resp.status_code == 200, resp.text

    def import_from_plex(self, filename, directory=""):
        """Tell Posteria to pull a poster from Plex into its local store."""
        self.login()
        resp = self.session.post(
            f"{POSTERIA_URL}/src/include/get-from-plex.php",
            data={
                "action": "import_from_plex",
                "filename": filename,
                "directory": directory,
            },
            timeout=30,
        )
        return resp.status_code == 200, resp.text


_posteria = None


def _get_posteria():
    global _posteria
    if _posteria is None:
        _posteria = PosteriaSession()
    return _posteria


# ── Plex helpers ─────────────────────────────────────────────────────────────

def _get_plex():
    return PlexServer(PLEX_URL, PLEX_TOKEN)


def _is_poster_locked(item):
    """Check if an item's poster (thumb) field is locked in Plex."""
    try:
        for field in item.fields:
            if field.name == "thumb" and field.locked:
                return True
    except Exception:
        pass
    return False


def _plex_type_for_item(item):
    """Return Posteria directory name for a Plex item."""
    if item.type == "movie":
        return "movies"
    elif item.type == "show":
        return "tv-shows"
    elif item.type == "season":
        return "tv-seasons"
    return "movies"


def _posteria_filename(item, library_title):
    """Build a Posteria-convention filename for a Plex item.

    Format: Title [ratingKey] (Atimestamp) [[Library]] --Plex--.jpg
    """
    title = re.sub(r'[<>:"/\\|?*]', '', item.title)
    timestamp = int(time.time())
    return f"{title} [{item.ratingKey}] (A{timestamp}) [[{library_title}]] --Plex--.jpg"


# ── TMDb helpers ─────────────────────────────────────────────────────────────

def _tmdb_get(path, params=None):
    time.sleep(RATE_LIMIT_DELAY)
    url = f"{TMDB_BASE}{path}"
    p = {"api_key": TMDB_KEY}
    if params:
        p.update(params)
    resp = requests.get(url, params=p, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _download_image(url, dest_path):
    """Download an image file to a local path."""
    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(8192):
            f.write(chunk)
    return True


# ── Database table ───────────────────────────────────────────────────────────

def ensure_poster_table(db):
    """Create the poster_status table if it doesn't exist."""
    db.conn.execute("""
        CREATE TABLE IF NOT EXISTS poster_status (
            plex_rating_key INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            library TEXT,
            poster_state TEXT NOT NULL DEFAULT 'uncurated',
            source TEXT NOT NULL DEFAULT 'unknown',
            locked INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            posteria_synced INTEGER DEFAULT 0
        )
    """)
    db.conn.commit()


def _upsert_poster_status(db, rating_key, title, library, poster_state="uncurated",
                           source="unknown", locked=False, posteria_synced=False):
    """Insert or update a poster_status record."""
    db.conn.execute("""
        INSERT INTO poster_status (plex_rating_key, title, library, poster_state, source, locked, last_updated, posteria_synced)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
        ON CONFLICT(plex_rating_key) DO UPDATE SET
            title = excluded.title,
            library = excluded.library,
            poster_state = excluded.poster_state,
            source = excluded.source,
            locked = excluded.locked,
            last_updated = CURRENT_TIMESTAMP,
            posteria_synced = excluded.posteria_synced
    """, (rating_key, title, library, poster_state, source, int(locked), int(posteria_synced)))
    db.conn.commit()


# ── Core functions ───────────────────────────────────────────────────────────

def scan_poster_status(db, libraries=None):
    """Populate/refresh poster_status by cross-referencing Plex library items.

    Locked posters → curated, unlocked → uncurated.
    Preserves existing state for items already marked curated/tmdb_default.
    """
    ensure_poster_table(db)
    plex = _get_plex()

    sections = plex.library.sections()
    if libraries:
        sections = [s for s in sections if s.title.lower() in [l.lower() for l in libraries]]

    total_scanned = 0
    total_locked = 0
    total_new = 0

    for section in sections:
        if section.type not in ("movie", "show"):
            continue

        items = section.all()
        for item in items:
            rating_key = item.ratingKey
            locked = _is_poster_locked(item)

            # Check if we already track this item
            existing = db.conn.execute(
                "SELECT poster_state, source FROM poster_status WHERE plex_rating_key = ?",
                (rating_key,),
            ).fetchone()

            if existing:
                # Don't downgrade curated/tmdb_default items
                if existing["poster_state"] in ("curated", "tmdb_default"):
                    # Just update lock status
                    db.conn.execute(
                        "UPDATE poster_status SET locked = ?, last_updated = CURRENT_TIMESTAMP WHERE plex_rating_key = ?",
                        (int(locked), rating_key),
                    )
                    db.conn.commit()
                else:
                    # Uncurated — update based on lock state
                    state = "curated" if locked else "uncurated"
                    source = existing["source"] if existing["source"] != "unknown" else ("unknown" if not locked else "unknown")
                    db.conn.execute(
                        "UPDATE poster_status SET poster_state = ?, locked = ?, last_updated = CURRENT_TIMESTAMP WHERE plex_rating_key = ?",
                        (state, int(locked), rating_key),
                    )
                    db.conn.commit()
            else:
                # New item
                state = "curated" if locked else "uncurated"
                _upsert_poster_status(
                    db, rating_key, item.title, section.title,
                    poster_state=state, source="unknown", locked=locked,
                )
                total_new += 1

            if locked:
                total_locked += 1
            total_scanned += 1

    return {
        "total_scanned": total_scanned,
        "total_locked": total_locked,
        "total_new": total_new,
        "message": f"Scanned {total_scanned} items, {total_locked} locked, {total_new} new",
    }


def _show_folder_map(section):
    """Map each folder Plex knows for a show section → (ratingKey, title).

    Show objects carry no .media — only their episodes do — so files are matched to
    the show whose folder contains them. A show can span several drives, hence the
    list of locations per show.
    """
    folder_map = {}
    for show in section.all():
        for loc in getattr(show, "locations", None) or []:
            folder_map[os.path.normpath(loc)] = (show.ratingKey, show.title)
    return folder_map


def _show_for_file(folder_map, file_path):
    """Walk up from a file to the innermost show folder containing it, or None."""
    current = os.path.dirname(os.path.normpath(file_path))
    while True:
        hit = folder_map.get(current)
        if hit:
            return hit
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def hook_new_media(db, library_key, file_paths):
    """Hook called after scan_nas_path inserts files into media_files.

    Adds any files not yet in poster_status as uncurated. Movie sections resolve one
    item per file; show sections resolve one item per *show*, since a poster in Plex
    belongs to the show, not to each episode file.
    Does NOT modify scanning logic — just appends poster tracking.
    """
    ensure_poster_table(db)
    plex = _get_plex()

    # Which Plex section owns this library comes from Plex itself, matched on
    # the folders both were pointed at.
    plex_name = libraries.section_name(library_key)
    if not plex_name:
        return 0

    try:
        section = plex.library.section(plex_name)
    except Exception:
        return 0

    # Resolve scanned files to distinct Plex items, preserving scan order
    resolved = []
    seen = set()

    if section.type == "show":
        folder_map = _show_folder_map(section)
        for fp in file_paths:
            hit = _show_for_file(folder_map, str(fp))
            if hit and hit[0] not in seen:
                seen.add(hit[0])
                resolved.append(hit)
    elif section.type == "movie":
        plex_file_map = {}
        for item in section.all():
            for media in item.media:
                for part in media.parts:
                    plex_file_map[os.path.normpath(part.file)] = (item.ratingKey, item.title)
        for fp in file_paths:
            hit = plex_file_map.get(os.path.normpath(str(fp)))
            if hit and hit[0] not in seen:
                seen.add(hit[0])
                resolved.append(hit)
    else:
        return 0

    added = 0
    for rating_key, title in resolved:
        # Check if already tracked
        existing = db.conn.execute(
            "SELECT 1 FROM poster_status WHERE plex_rating_key = ?",
            (rating_key,),
        ).fetchone()
        if existing:
            continue

        _upsert_poster_status(
            db, rating_key, title, plex_name,
            poster_state="uncurated", source="unknown", locked=False,
        )
        added += 1

    return added


def apply_tmdb_poster(db, plex_rating_key):
    """Fetch TMDB primary poster → Posteria directory → trigger Posteria import.

    Returns (success, message).
    """
    ensure_poster_table(db)
    plex = _get_plex()
    posteria = _get_posteria()

    # Get the Plex item
    try:
        item = plex.fetchItem(plex_rating_key)
    except Exception as e:
        return False, f"Plex item {plex_rating_key} not found: {e}"

    # Determine media type and search TMDb
    if item.type == "movie":
        search_type = "movie"
        tmdb_path = "/search/movie"
        params = {"query": item.title}
        if hasattr(item, "year") and item.year:
            params["year"] = item.year
    elif item.type in ("show", "episode"):
        search_type = "tv"
        tmdb_path = "/search/tv"
        params = {"query": item.title}
    else:
        return False, f"Unsupported item type: {item.type}"

    # Search TMDb
    results = _tmdb_get(tmdb_path, params)
    hits = results.get("results", [])
    if not hits:
        return False, f"No TMDb results for '{item.title}'"

    tmdb_item = hits[0]
    poster_path = tmdb_item.get("poster_path")
    if not poster_path:
        return False, f"No poster available on TMDb for '{item.title}'"

    # Download poster to Posteria's directory
    subdir = _plex_type_for_item(item)
    poster_dir = POSTER_DIR / subdir
    poster_dir.mkdir(parents=True, exist_ok=True)

    library_title = item.section().title if hasattr(item, "section") else "Movies"
    filename = _posteria_filename(item, library_title)
    dest = poster_dir / filename

    image_url = f"{TMDB_IMAGE_BASE}{poster_path}"
    try:
        _download_image(image_url, str(dest))
    except Exception as e:
        return False, f"Failed to download poster: {e}"

    # Trigger Posteria to push it to Plex
    success, resp = posteria.send_to_plex(filename, subdir)

    # Update poster_status
    _upsert_poster_status(
        db, plex_rating_key, item.title, library_title,
        poster_state="tmdb_default", source="tmdb",
        locked=True, posteria_synced=True,
    )

    if success:
        return True, f"Applied TMDb poster to '{item.title}' via Posteria"
    else:
        return True, f"Poster saved for '{item.title}' — Posteria sync returned: {resp}"


def apply_tmdb_bulk(db, library=None):
    """Apply TMDb posters to all uncurated items in a library (or all).

    Returns summary dict.
    """
    ensure_poster_table(db)
    query = "SELECT plex_rating_key, title FROM poster_status WHERE poster_state = 'uncurated'"
    params = []
    if library:
        query += " AND library = ?"
        params.append(library)

    rows = db.conn.execute(query, params).fetchall()
    results = {"total": len(rows), "success": 0, "failed": 0, "errors": []}

    for row in rows:
        ok, msg = apply_tmdb_poster(db, row["plex_rating_key"])
        if ok:
            results["success"] += 1
        else:
            results["failed"] += 1
            results["errors"].append({"title": row["title"], "error": msg})

    results["message"] = (
        f"Applied TMDb posters: {results['success']}/{results['total']} succeeded, "
        f"{results['failed']} failed"
    )
    return results


def import_mediux_set(db, yaml_path):
    """Import a Mediux YAML poster set.

    Reads the YAML, matches entries to Plex items by title/year,
    places poster files in Posteria's directory, triggers import.
    """
    ensure_poster_table(db)
    plex = _get_plex()
    posteria = _get_posteria()

    yaml_path = Path(yaml_path)
    if not yaml_path.exists():
        return {"success": False, "message": f"YAML file not found: {yaml_path}"}

    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)

    if not data:
        return {"success": False, "message": "Empty or invalid YAML file"}

    # Mediux YAMLs can have different structures — handle common formats
    # Typically: list of entries with title, year, and a poster image path/url
    entries = []
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        # Could be keyed by title or have an "entries"/"posters" key
        for key in ("entries", "posters", "items", "movies", "shows"):
            if key in data and isinstance(data[key], list):
                entries = data[key]
                break
        if not entries:
            # Assume dict keyed by title
            for title, info in data.items():
                if isinstance(info, dict):
                    info["title"] = title
                    entries.append(info)

    if not entries:
        return {"success": False, "message": "Could not parse entries from YAML"}

    # Build Plex title → item lookup
    plex_items = {}
    for section in plex.library.sections():
        if section.type not in ("movie", "show"):
            continue
        for item in section.all():
            key = item.title.lower()
            if hasattr(item, "year") and item.year:
                key_with_year = f"{item.title.lower()} ({item.year})"
                plex_items[key_with_year] = (item, section.title)
            plex_items[key] = (item, section.title)

    imported = 0
    skipped = 0
    unmatched = []

    for entry in entries:
        title = entry.get("title", "")
        year = entry.get("year")
        poster_source = entry.get("poster") or entry.get("url") or entry.get("image") or entry.get("file")

        if not title or not poster_source:
            skipped += 1
            continue

        # Match to Plex
        lookup_key = f"{title.lower()} ({year})" if year else title.lower()
        match = plex_items.get(lookup_key)

        # Try fuzzy match if exact fails
        if not match:
            for plex_key, plex_val in plex_items.items():
                if fuzz.ratio(lookup_key, plex_key) >= 85:
                    match = plex_val
                    break

        if not match:
            unmatched.append({"title": title, "year": year})
            continue

        plex_item, library_title = match
        subdir = _plex_type_for_item(plex_item)
        poster_dir = POSTER_DIR / subdir
        poster_dir.mkdir(parents=True, exist_ok=True)

        filename = _posteria_filename(plex_item, library_title)
        dest = poster_dir / filename

        # Download or copy the poster
        try:
            if poster_source.startswith(("http://", "https://")):
                _download_image(poster_source, str(dest))
            else:
                # Local file path
                src = Path(poster_source)
                if not src.is_absolute():
                    src = yaml_path.parent / src
                if src.exists():
                    shutil.copy2(str(src), str(dest))
                else:
                    unmatched.append({"title": title, "error": f"File not found: {src}"})
                    continue
        except Exception as e:
            unmatched.append({"title": title, "error": str(e)})
            continue

        # Trigger Posteria import
        posteria.send_to_plex(filename, subdir)

        # Update poster_status
        _upsert_poster_status(
            db, plex_item.ratingKey, plex_item.title, library_title,
            poster_state="curated", source="mediux",
            locked=True, posteria_synced=True,
        )
        imported += 1

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "unmatched": unmatched,
        "message": f"Imported {imported} posters from Mediux set, {len(unmatched)} unmatched",
    }


def enforce_locks(db):
    """Re-lock any curated/tmdb_default posters that Plex has unlocked."""
    ensure_poster_table(db)
    plex = _get_plex()

    rows = db.conn.execute("""
        SELECT plex_rating_key, title, library, poster_state
        FROM poster_status
        WHERE poster_state IN ('curated', 'tmdb_default')
    """).fetchall()

    relocked = 0
    checked = 0
    errors = []

    for row in rows:
        checked += 1
        try:
            item = plex.fetchItem(row["plex_rating_key"])
        except Exception:
            errors.append({"title": row["title"], "error": "Item not found in Plex"})
            continue

        if _is_poster_locked(item):
            continue

        # Poster was unlocked — re-lock it via Plex API
        try:
            section = item.section()
            # Determine Plex type ID for the lock call
            type_id = {"movie": 1, "show": 2, "season": 3, "episode": 4}.get(item.type, 1)

            requests.put(
                f"{PLEX_URL}/library/sections/{section.key}/all",
                params={
                    "type": type_id,
                    "id": row["plex_rating_key"],
                    "thumb.locked": 1,
                    "X-Plex-Token": PLEX_TOKEN,
                },
                timeout=10,
            )

            # Update DB
            db.conn.execute(
                "UPDATE poster_status SET locked = 1, last_updated = CURRENT_TIMESTAMP WHERE plex_rating_key = ?",
                (row["plex_rating_key"],),
            )
            db.conn.commit()
            relocked += 1
            print(f"  Re-locked: {row['title']}")
        except Exception as e:
            errors.append({"title": row["title"], "error": str(e)})

    return {
        "checked": checked,
        "relocked": relocked,
        "errors": errors,
        "message": f"Checked {checked} items, re-locked {relocked}",
    }


def detect_orphans():
    """Find poster files in Posteria's directory that don't match any Plex item.

    Returns list of orphaned files but does NOT delete them.
    """
    plex = _get_plex()

    # Collect all Plex ratingKeys
    plex_keys = set()
    for section in plex.library.sections():
        if section.type not in ("movie", "show"):
            continue
        for item in section.all():
            plex_keys.add(str(item.ratingKey))

    orphans = []
    rating_key_pattern = re.compile(r"\[(\d+)\]")

    for subdir_name in POSTER_SUBDIRS.values():
        subdir = POSTER_DIR / subdir_name
        if not subdir.exists():
            continue
        for poster_file in subdir.iterdir():
            if not poster_file.is_file():
                continue
            match = rating_key_pattern.search(poster_file.name)
            if not match:
                orphans.append({
                    "file": str(poster_file),
                    "reason": "No ratingKey found in filename",
                })
                continue
            if match.group(1) not in plex_keys:
                orphans.append({
                    "file": str(poster_file),
                    "rating_key": match.group(1),
                    "reason": "ratingKey not found in any Plex library",
                })

    return {
        "orphans": orphans,
        "total": len(orphans),
        "message": f"Found {len(orphans)} orphaned poster files",
    }


# ── Query helpers ────────────────────────────────────────────────────────────

def get_poster_status(db, library=None, state=None, limit=500):
    """Query the poster_status table with optional filters."""
    ensure_poster_table(db)
    query = "SELECT * FROM poster_status WHERE 1=1"
    params = []
    if library:
        query += " AND library = ?"
        params.append(library)
    if state:
        query += " AND poster_state = ?"
        params.append(state)
    query += " ORDER BY last_updated DESC LIMIT ?"
    params.append(limit)
    return db.conn.execute(query, params).fetchall()
