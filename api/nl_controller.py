"""
Natural language control layer for Plex-Ops.

Parses user commands via keyword matching and pattern rules,
maps them to Plex API actions and database queries.
"""

import re
from plexapi.server import PlexServer
from db import Database
from config import config


# ── Plex connection ─────────────────────────────────────────────────────────

def _get_plex():
    return PlexServer(config["plex"]["url"], config["plex"]["token"])


# ── Library name resolution ─────────────────────────────────────────────────

# Maps informal names to config keys and Plex library names
LIBRARY_ALIASES = {
    "movies": {"config_key": "movies", "plex_name": "Movies"},
    "movie": {"config_key": "movies", "plex_name": "Movies"},
    "films": {"config_key": "movies", "plex_name": "Movies"},
    "tv": {"config_key": "tv", "plex_name": "TV Shows"},
    "tv shows": {"config_key": "tv", "plex_name": "TV Shows"},
    "television": {"config_key": "tv", "plex_name": "TV Shows"},
    "anime movies": {"config_key": "anime_movies", "plex_name": "Anime Movies"},
    "anime films": {"config_key": "anime_movies", "plex_name": "Anime Movies"},
    "anime tv": {"config_key": "anime_tv", "plex_name": "Anime TV"},
    "anime shows": {"config_key": "anime_tv", "plex_name": "Anime TV"},
    "anime": {"config_key": "anime_tv", "plex_name": "Anime TV"},
    "disney": {"config_key": "disney", "plex_name": "Disney"},
    "4k": {"config_key": "4k", "plex_name": "4K"},
    "4k tv": {"config_key": "4k_tv", "plex_name": "4K TV"},
    "4k movies": {"config_key": "4k", "plex_name": "4K"},
    "music": {"config_key": "music", "plex_name": "Music"},
    "concerts": {"config_key": "concerts_and_music_videos", "plex_name": "Concerts & Music Videos"},
    "music videos": {"config_key": "concerts_and_music_videos", "plex_name": "Concerts & Music Videos"},
    "comics": {"config_key": "comics_manga", "plex_name": "Comics & Manga"},
    "manga": {"config_key": "comics_manga", "plex_name": "Comics & Manga"},
}


def _resolve_library(text):
    """Try to match a library name from free text. Returns alias dict or None."""
    text_lower = text.lower().strip()
    # Try exact match first
    if text_lower in LIBRARY_ALIASES:
        return LIBRARY_ALIASES[text_lower]
    # Try substring match (longest first to prefer "anime movies" over "anime")
    for alias in sorted(LIBRARY_ALIASES.keys(), key=len, reverse=True):
        if alias in text_lower:
            return LIBRARY_ALIASES[alias]
    return None


# ── Intent parsing ──────────────────────────────────────────────────────────

INTENT_PATTERNS = [
    # Playlist creation
    (r"create (?:a )?playlist (?:of |from |with |called |named )?(.+)",
     "create_playlist"),
    (r"make (?:a )?playlist (?:of |from |with |called |named )?(.+)",
     "create_playlist"),
    (r"build (?:a )?playlist (?:of |from |with |called |named )?(.+)",
     "create_playlist"),

    # Poster management (delegates to poster module)
    (r"apply (?:tmdb |the )?posters? (?:to |for )(.+)", "poster_apply"),
    (r"show poster status (?:for |of )(.+)", "poster_status"),
    (r"enforce poster locks?", "poster_locks"),
    (r"lock posters?", "poster_locks"),

    # Library scans
    (r"scan (.+?)(?:\s+library)?$", "scan_library"),
    (r"rescan (.+?)(?:\s+library)?$", "scan_library"),
    (r"refresh (.+?)(?:\s+library)?$", "scan_library"),

    # Gap / missing queries
    (r"what(?:'s| is) missing (?:from |in )(.+)", "query_gaps"),
    (r"show (?:me )?(?:the )?(?:gaps?|missing) (?:for |from |in )(.+)", "query_gaps"),
    (r"missing episodes? (?:for |from |in )(.+)", "query_gaps"),
    (r"what(?:'s| is) missing", "query_gaps_all"),
    (r"show (?:me )?(?:all )?gaps?", "query_gaps_all"),

    # Quality queries
    (r"(?:show|list|get) (?:me )?(?:files? |stuff )?(?:that |which )?need(?:s)? upgrad(?:e|ing)",
     "query_upgrades"),
    (r"(?:show|list|get) (?:me )?(?:the )?(?:worst|lowest) quality",
     "query_upgrades"),
    (r"(?:show|list|get) (?:me )?upgrade(?:s| priority| list)",
     "query_upgrades"),
    (r"what (?:needs?|should) (?:be )?upgrad(?:ed|ing)",
     "query_upgrades"),
    (r"quality (?:report|status|check)",
     "query_upgrades"),

    # Summary / status
    (r"(?:show|give) (?:me )?(?:a )?summary", "summary"),
    (r"status", "summary"),
    (r"overview", "summary"),
    (r"how(?:'s| is) (?:my |the )?library", "summary"),
    (r"how(?:'s| is) everything", "summary"),
]


def _parse_intent(command):
    """Parse a natural language command into (intent, captured_text)."""
    text = command.strip()
    for pattern, intent in INTENT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            captured = m.group(1).strip() if m.lastindex else None
            return intent, captured
    return "unknown", text


# ── Command handlers ────────────────────────────────────────────────────────

def _handle_create_playlist(plex, db, captured):
    """Create a Plex playlist from natural language criteria."""
    text = captured.lower()

    # Parse optional criteria from the captured text
    max_score = None
    library_filter = None
    genre_filter = None
    year_filter = None
    decade_filter = None
    codec_filter = None
    name_override = None

    # Check for "called X" / "named X" at the end
    name_match = re.search(r'(?:called|named)\s+"?([^"]+)"?\s*$', text, re.IGNORECASE)
    if name_match:
        name_override = name_match.group(1).strip()
        text = text[:name_match.start()].strip()

    # Library filter
    lib = _resolve_library(text)
    if lib:
        library_filter = lib["plex_name"]

    # Score-based: "low quality", "score under X", "score below X"
    score_match = re.search(r'(?:score )?(?:under|below|less than|<=?)\s*(\d+)', text)
    if score_match:
        max_score = int(score_match.group(1))
    elif "low quality" in text or "worst" in text or "bad quality" in text:
        max_score = 50
    elif "needs upgrade" in text or "need upgrading" in text:
        max_score = 70

    # Genre
    genre_match = re.search(
        r'\b(action|comedy|drama|horror|thriller|sci-fi|science fiction|romance|'
        r'documentary|animation|animated|fantasy|mystery|crime|western|war|musical)\b',
        text
    )
    if genre_match:
        genre_filter = genre_match.group(1).title()
        if genre_filter == "Sci-Fi":
            genre_filter = "Science Fiction"
        elif genre_filter == "Animated":
            genre_filter = "Animation"

    # Year
    year_match = re.search(r'\bfrom (\d{4})\b', text)
    if year_match:
        year_filter = int(year_match.group(1))

    # Decade
    decade_match = re.search(r'\b(\d{2})(?:s|\'s)\b', text)
    if not decade_match:
        decade_match = re.search(r'\b((?:19|20)\d{2})s\b', text)
    if decade_match:
        val = decade_match.group(1)
        if len(val) == 2:
            decade_filter = int(val) + (1900 if int(val) >= 50 else 2000)
        else:
            decade_filter = int(val)

    # Codec
    if "hevc" in text or "h.265" in text or "h265" in text or "x265" in text:
        codec_filter = "hevc"
    elif "h.264" in text or "h264" in text or "x264" in text:
        codec_filter = "h264"

    # Build the playlist name
    parts = []
    if library_filter:
        parts.append(library_filter)
    if genre_filter:
        parts.append(genre_filter)
    if decade_filter:
        parts.append(f"{decade_filter}s")
    if year_filter:
        parts.append(str(year_filter))
    if max_score:
        parts.append(f"Score≤{max_score}")
    if codec_filter:
        parts.append(codec_filter.upper())

    playlist_name = name_override or ("Plex-Ops: " + " - ".join(parts) if parts else f"Plex-Ops: {captured}")

    # Query the database for matching files
    query = "SELECT file_path FROM quality_scores WHERE 1=1"
    params = []
    if max_score is not None:
        query += " AND score <= ?"
        params.append(max_score)
    if library_filter:
        # Map Plex name back to config key for DB lookup
        config_key = None
        for alias_info in LIBRARY_ALIASES.values():
            if alias_info["plex_name"] == library_filter:
                config_key = alias_info["config_key"]
                break
        if config_key:
            query += " AND library = ?"
            params.append(config_key)
    if codec_filter:
        query += " AND video_codec LIKE ?"
        params.append(f"%{codec_filter}%")
    query += " ORDER BY score ASC LIMIT 500"

    rows = db.conn.execute(query, params).fetchall()
    if not rows:
        return {
            "action": "create_playlist",
            "success": False,
            "message": f"No files matched the criteria: {captured}",
            "criteria": {
                "max_score": max_score,
                "library": library_filter,
                "genre": genre_filter,
                "codec": codec_filter,
            },
        }

    file_paths = [r["file_path"] for r in rows]

    # Find matching items in Plex
    plex_items = []
    sections = plex.library.sections()
    for section in sections:
        if library_filter and section.title != library_filter:
            continue
        try:
            for item in section.all():
                for media in item.media:
                    for part in media.parts:
                        if part.file in file_paths:
                            plex_items.append(item)
        except Exception:
            continue

    if not plex_items:
        return {
            "action": "create_playlist",
            "success": False,
            "message": f"Found {len(file_paths)} files in DB but couldn't match them in Plex. "
                       "Files may not be in a scanned Plex library.",
            "db_matches": len(file_paths),
        }

    # Deduplicate (same Plex item can appear via multiple parts)
    seen = set()
    unique_items = []
    for item in plex_items:
        if item.ratingKey not in seen:
            seen.add(item.ratingKey)
            unique_items.append(item)

    # Create or update the playlist
    try:
        existing = plex.playlist(playlist_name)
        existing.delete()
    except Exception:
        pass

    playlist = plex.createPlaylist(playlist_name, items=unique_items)
    return {
        "action": "create_playlist",
        "success": True,
        "playlist_name": playlist_name,
        "items_added": len(unique_items),
        "message": f"Created playlist '{playlist_name}' with {len(unique_items)} items",
        "criteria": {
            "max_score": max_score,
            "library": library_filter,
            "genre": genre_filter,
            "decade": decade_filter,
            "year": year_filter,
            "codec": codec_filter,
        },
    }


def _handle_scan_library(plex, captured):
    """Trigger a Plex library scan."""
    lib = _resolve_library(captured)
    if not lib:
        # Try to find by Plex section name directly
        sections = plex.library.sections()
        for section in sections:
            if captured.lower() in section.title.lower():
                section.update()
                return {
                    "action": "scan_library",
                    "success": True,
                    "library": section.title,
                    "message": f"Triggered scan for Plex library '{section.title}'",
                }
        return {
            "action": "scan_library",
            "success": False,
            "message": f"Could not find a library matching '{captured}'",
            "available": [s.title for s in sections],
        }

    # Try to match the Plex library
    plex_name = lib["plex_name"]
    try:
        section = plex.library.section(plex_name)
        section.update()
        return {
            "action": "scan_library",
            "success": True,
            "library": plex_name,
            "message": f"Triggered scan for Plex library '{plex_name}'",
        }
    except Exception as e:
        return {
            "action": "scan_library",
            "success": False,
            "library": plex_name,
            "message": f"Failed to scan '{plex_name}': {e}",
        }


def _handle_query_gaps(db, captured):
    """Query gaps for a specific show, franchise, or library."""
    text = captured.lower().strip()

    # Check if it's a library name
    lib = _resolve_library(text)
    if lib:
        rows = db.get_gaps(library=lib["config_key"])
        gaps = [dict(r) for r in rows]

        # Group by type
        by_type = {}
        for g in gaps:
            t = g["gap_type"]
            by_type.setdefault(t, []).append(g)

        return {
            "action": "query_gaps",
            "success": True,
            "query": captured,
            "library": lib["config_key"],
            "total_gaps": len(gaps),
            "by_type": {k: len(v) for k, v in by_type.items()},
            "gaps": gaps[:100],
            "truncated": len(gaps) > 100,
        }

    # Otherwise search by title
    rows = db.conn.execute(
        "SELECT * FROM gaps WHERE LOWER(title) LIKE ? ORDER BY title, season_number, episode_number",
        (f"%{text}%",),
    ).fetchall()
    gaps = [dict(r) for r in rows]

    if not gaps:
        return {
            "action": "query_gaps",
            "success": True,
            "query": captured,
            "total_gaps": 0,
            "message": f"No gaps found matching '{captured}'",
        }

    by_type = {}
    for g in gaps:
        t = g["gap_type"]
        by_type.setdefault(t, []).append(g)

    return {
        "action": "query_gaps",
        "success": True,
        "query": captured,
        "total_gaps": len(gaps),
        "by_type": {k: len(v) for k, v in by_type.items()},
        "gaps": gaps[:100],
        "truncated": len(gaps) > 100,
    }


def _handle_query_gaps_all(db):
    """Show all gaps summary."""
    tv_count = db.conn.execute(
        "SELECT COUNT(*) FROM gaps WHERE gap_type IN ('missing_season','missing_episode')"
    ).fetchone()[0]
    franchise_count = db.conn.execute(
        "SELECT COUNT(*) FROM gaps WHERE gap_type = 'missing_franchise_entry'"
    ).fetchone()[0]
    alt_count = db.conn.execute(
        "SELECT COUNT(*) FROM gaps WHERE gap_type = 'alternate_version_available'"
    ).fetchone()[0]

    # Top shows with most gaps
    top_shows = [dict(r) for r in db.conn.execute("""
        SELECT title, COUNT(*) as gap_count
        FROM gaps
        WHERE gap_type IN ('missing_season', 'missing_episode')
        GROUP BY title ORDER BY gap_count DESC LIMIT 10
    """).fetchall()]

    return {
        "action": "query_gaps",
        "success": True,
        "total_gaps": tv_count + franchise_count + alt_count,
        "tv_gaps": tv_count,
        "franchise_gaps": franchise_count,
        "alternate_versions": alt_count,
        "top_shows_with_gaps": top_shows,
        "message": f"{tv_count} TV gaps, {franchise_count} franchise gaps, {alt_count} alternate versions available",
    }


def _handle_query_upgrades(db, captured=None):
    """Show files that need upgrading."""
    # Parse optional filters from captured text
    max_score = 70
    library_filter = None
    limit = 50

    if captured:
        text = captured.lower()
        lib = _resolve_library(text)
        if lib:
            library_filter = lib["config_key"]

        score_match = re.search(r'(?:under|below|less than|<=?)\s*(\d+)', text)
        if score_match:
            max_score = int(score_match.group(1))

        limit_match = re.search(r'(?:top|first|limit)\s*(\d+)', text)
        if limit_match:
            limit = int(limit_match.group(1))

    query = """
        SELECT file_path, library, score, issues, upgrade_recommendation, video_codec, resolution
        FROM quality_scores
        WHERE score <= ?
    """
    params = [max_score]
    if library_filter:
        query += " AND library = ?"
        params.append(library_filter)
    query += " ORDER BY score ASC LIMIT ?"
    params.append(limit)

    rows = db.conn.execute(query, params).fetchall()
    files = [dict(r) for r in rows]

    # Summary stats
    total_needing = db.conn.execute(
        "SELECT COUNT(*) FROM quality_scores WHERE score <= ?", (max_score,)
    ).fetchone()[0]

    by_library = [dict(r) for r in db.conn.execute("""
        SELECT library, COUNT(*) as count, ROUND(AVG(score), 1) as avg_score
        FROM quality_scores WHERE score <= ?
        GROUP BY library ORDER BY count DESC
    """, (max_score,)).fetchall()]

    return {
        "action": "query_upgrades",
        "success": True,
        "max_score_threshold": max_score,
        "total_needing_upgrade": total_needing,
        "showing": len(files),
        "by_library": by_library,
        "files": files,
        "message": f"{total_needing} files scoring ≤{max_score} need upgrading",
    }


def _handle_summary(db):
    """Return overall system summary."""
    total_files = db.conn.execute("SELECT COUNT(*) FROM media_files").fetchone()[0]
    total_scored = db.conn.execute("SELECT COUNT(*) FROM quality_scores").fetchone()[0]
    avg_score = db.conn.execute("SELECT ROUND(AVG(score),1) FROM quality_scores").fetchone()[0]
    files_with_issues = db.conn.execute(
        "SELECT COUNT(*) FROM quality_scores WHERE issues IS NOT NULL"
    ).fetchone()[0]
    total_gaps = db.conn.execute("SELECT COUNT(*) FROM gaps").fetchone()[0]
    pending_renames = db.conn.execute(
        "SELECT COUNT(*) FROM rename_suggestions WHERE applied = 0"
    ).fetchone()[0]

    last_scan = db.conn.execute(
        "SELECT scan_type, files_found, completed_at FROM scan_history ORDER BY completed_at DESC LIMIT 1"
    ).fetchone()

    return {
        "action": "summary",
        "success": True,
        "total_media_files": total_files,
        "total_quality_scored": total_scored,
        "average_quality_score": avg_score,
        "files_with_issues": files_with_issues,
        "total_gaps": total_gaps,
        "pending_renames": pending_renames,
        "last_scan": dict(last_scan) if last_scan else None,
        "message": (
            f"{total_files} files indexed, avg quality {avg_score}, "
            f"{total_gaps} gaps, {pending_renames} pending renames"
        ),
    }


def _handle_poster(db, captured, intent):
    """Delegates poster operations to scanner/poster_manager.py via Posteria."""
    from scanner.poster_manager import scan_poster_status, apply_tmdb_bulk, enforce_locks, get_poster_status

    lib = _resolve_library(captured) if captured else None
    library_name = lib["plex_name"] if lib else None

    if intent == "poster_apply":
        if not library_name:
            return {"action": intent, "success": False, "message": f"Could not resolve library: {captured}"}
        result = apply_tmdb_bulk(db, library=library_name)
        result["action"] = "poster_apply"
        return result

    elif intent == "poster_status":
        rows = get_poster_status(db, library=library_name, limit=100)
        items = [dict(r) for r in rows]
        by_state = {}
        for item in items:
            s = item["poster_state"]
            by_state[s] = by_state.get(s, 0) + 1
        return {
            "action": "poster_status",
            "success": True,
            "library": library_name or "all",
            "total": len(items),
            "by_state": by_state,
            "items": items,
        }

    elif intent == "poster_locks":
        result = enforce_locks(db)
        result["action"] = "poster_locks"
        return result

    return {"action": intent, "success": False, "message": "Unknown poster action"}


# ── Main dispatcher ─────────────────────────────────────────────────────────

def execute_command(command):
    """Parse and execute a natural language command. Returns a result dict."""
    intent, captured = _parse_intent(command)

    db = Database()
    plex = None

    try:
        if intent == "create_playlist":
            plex = _get_plex()
            return _handle_create_playlist(plex, db, captured)

        elif intent == "scan_library":
            plex = _get_plex()
            return _handle_scan_library(plex, captured)

        elif intent == "query_gaps":
            return _handle_query_gaps(db, captured)

        elif intent == "query_gaps_all":
            return _handle_query_gaps_all(db)

        elif intent == "query_upgrades":
            return _handle_query_upgrades(db, captured)

        elif intent == "summary":
            return _handle_summary(db)

        elif intent in ("poster_apply", "poster_status", "poster_locks"):
            return _handle_poster(db, captured, intent)

        else:
            return {
                "action": "unknown",
                "success": False,
                "command": command,
                "message": "I didn't understand that command.",
                "supported_commands": [
                    "create a playlist of [criteria]",
                    "scan [library name]",
                    "what's missing from [show/franchise]",
                    "show me files that need upgrading",
                    "show me gaps",
                    "status / summary / overview",
                    "apply posters to [library] (coming soon)",
                ],
            }
    except Exception as e:
        return {
            "action": intent,
            "success": False,
            "error": str(e),
            "message": f"Command failed: {e}",
        }
    finally:
        db.close()
