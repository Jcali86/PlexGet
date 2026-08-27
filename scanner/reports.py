import re
from collections import OrderedDict
from db import Database
from scanner import paths
from scanner.poster_manager import detect_orphans, ensure_poster_table

# Maps issue keys to human-readable fragments
_ISSUE_LABELS = {
    "old_video_codec:mpeg4": "XviD/MPEG-4",
    "old_video_codec:mpeg2video": "MPEG-2",
    "old_video_codec:msmpeg4v3": "MS-MPEG4",
    "old_video_codec:msmpeg4v2": "MS-MPEG4",
    "old_video_codec:msmpeg4v1": "MS-MPEG4",
    "old_video_codec:wmv1": "WMV",
    "old_video_codec:wmv2": "WMV",
    "old_video_codec:wmv3": "WMV",
    "bad_container:avi": "AVI container",
    "bad_container:wmv": "WMV container",
    "bad_container:asf": "ASF container",
    "bad_container:flv": "FLV container",
    "bad_container:mpegts": "MPEG-TS container",
}

_SE_PATTERN = re.compile(r"[Ss](\d{1,3})[Ee](\d{1,3})")


def _human_reason(issues_str, resolution, audio_channels):
    """Turn the semicolon-delimited issues string into a readable sentence."""
    if not issues_str:
        return None

    parts = []
    for issue in issues_str.split("; "):
        key = issue.split(":")[0] if ":" in issue else issue

        # Check exact match first, then prefix match
        if issue in _ISSUE_LABELS:
            parts.append(_ISSUE_LABELS[issue])
        elif key == "legacy_codec_tag":
            tag = issue.split(":", 1)[1] if ":" in issue else "unknown"
            parts.append(f"legacy {tag} encoder")
        elif key == "low_bitrate_1080p":
            mbps = issue.split(":", 1)[1] if ":" in issue else "?"
            parts.append(f"low bitrate ({mbps})")
        elif key == "low_bitrate_720p":
            mbps = issue.split(":", 1)[1] if ":" in issue else "?"
            parts.append(f"low bitrate ({mbps})")
        elif key == "stereo_only":
            ch = audio_channels or 2
            parts.append(f"{'mono' if ch <= 1 else 'stereo'} only")
        elif key == "low_resolution":
            parts.append(f"{resolution or 'sub-720p'}")
        elif key.startswith("old_video_codec"):
            codec = issue.split(":", 1)[1] if ":" in issue else "unknown"
            parts.append(codec)
        elif key.startswith("bad_container"):
            fmt = issue.split(":", 1)[1] if ":" in issue else "unknown"
            parts.append(f"{fmt.upper()} container")

    return ", ".join(parts) if parts else issues_str


def _extract_title(file_path):
    """Pull a grouping title from a file path.

    An episode is grouped by its show, which is the folder sitting directly
    inside the library - not by any folder name this code has to recognise.
    Anything else is grouped by its own filename, without extension or release
    tags.
    """
    inside = paths.below_library(file_path)
    basename = re.split(r"[\\/]", file_path)[-1]

    if len(inside) > 1 and _SE_PATTERN.search(basename):
        raw = inside[0]
        # Strip from S01 or quality tags onward
        name = re.split(r"[\.\s][Ss]\d{1,3}", raw)[0]
        name = re.split(r"[\.\s]\d{3,4}p", name)[0]
        name = re.split(r"\s*[\[\(]", name)[0]
        return name.replace(".", " ").strip()

    name = re.sub(r"\.\w{2,4}$", "", basename)
    name = re.split(
        r"[\.\s](?:\d{3,4}p|BluRay|BRRip|WEB-DL|WEBRip|REMUX|HDTV|BDRip|DVDRip|HDRip|DVDScr)",
        name, flags=re.IGNORECASE,
    )[0]
    name = name.replace(".", " ").strip()
    return name


def generate_upgrade_report(max_score=70, min_score=None, library=None, limit=500, issue=None):
    """Generate a prioritized upgrade report grouped by title.

    Returns a list of title groups, each containing scored files.
    """
    db = Database()
    rows = db.get_quality(
        min_score=min_score, max_score=max_score,
        library=library, limit=limit, issue=issue,
    )

    # Group by extracted title, preserving score-ascending order
    groups = OrderedDict()
    for row in rows:
        r = dict(row)
        title = _extract_title(r["file_path"])
        if title not in groups:
            groups[title] = {
                "title": title,
                "library": r["library"],
                "worst_score": r["score"],
                "file_count": 0,
                "files": [],
            }

        filename = r["file_path"].rsplit("/", 1)[-1]
        reason = _human_reason(r["issues"], r["resolution"], r["audio_channels"])

        groups[title]["files"].append({
            "file": filename,
            "path": r["file_path"],
            "score": r["score"],
            "resolution": r["resolution"],
            "video_codec": r["video_codec"],
            "audio_codec": r["audio_codec"],
            "audio_channels": r["audio_channels"],
            "issues_raw": r["issues"] or "",
            "reason": reason,
        })
        groups[title]["file_count"] += 1
        groups[title]["worst_score"] = min(groups[title]["worst_score"], r["score"])

    db.close()

    # Sort groups by worst score ascending, then by file count descending
    result = sorted(groups.values(), key=lambda g: (g["worst_score"], -g["file_count"]))
    return result


# ---------------------------------------------------------------------------
# Gap report
# ---------------------------------------------------------------------------

def generate_gap_report(gap_type=None, library=None, limit=500):
    """Generate a structured gap report organized into three sections.

    Returns a dict with keys: tv_gaps, franchise_gaps, alternate_versions.
    Each section contains grouped entries sorted by most missing first.
    """
    db = Database()

    sections = {}

    # Determine which sections to build
    types_to_run = []
    if gap_type in (None, "tv"):
        types_to_run.append("tv")
    if gap_type in (None, "franchise"):
        types_to_run.append("franchise")
    if gap_type in (None, "alternate"):
        types_to_run.append("alternate")

    if "tv" in types_to_run:
        sections["tv_gaps"] = _build_tv_gaps(db, library, limit)

    if "franchise" in types_to_run:
        sections["franchise_gaps"] = _build_franchise_gaps(db, library, limit)

    if "alternate" in types_to_run:
        sections["alternate_versions"] = _build_alternate_versions(db, library, limit)

    db.close()
    return sections


def _build_tv_gaps(db, library, limit):
    """Group missing seasons and episodes by show, sorted by most missing."""
    season_rows = db.get_gaps(gap_type="missing_season", library=library)
    episode_rows = db.get_gaps(gap_type="missing_episode", library=library)

    shows = OrderedDict()

    for row in season_rows:
        r = dict(row)
        title = r["title"]
        if title not in shows:
            shows[title] = {
                "title": title,
                "library": r["library"],
                "tmdb_id": r["tmdb_id"],
                "missing_seasons": [],
                "missing_episodes": [],
                "total_missing": 0,
            }
        shows[title]["missing_seasons"].append({
            "season": r["season_number"],
        })
        shows[title]["total_missing"] += 1

    for row in episode_rows:
        r = dict(row)
        title = r["title"]
        if title not in shows:
            shows[title] = {
                "title": title,
                "library": r["library"],
                "tmdb_id": r["tmdb_id"],
                "missing_seasons": [],
                "missing_episodes": [],
                "total_missing": 0,
            }
        shows[title]["missing_episodes"].append({
            "season": r["season_number"],
            "episode": r["episode_number"],
            "episode_title": r["episode_title"],
        })
        shows[title]["total_missing"] += 1

    result = sorted(shows.values(), key=lambda s: -s["total_missing"])
    return result[:limit]


def _build_franchise_gaps(db, library, limit):
    """Group missing franchise entries by collection."""
    rows = db.get_gaps(gap_type="missing_franchise_entry", library=library)

    collections = OrderedDict()
    for row in rows:
        r = dict(row)
        coll = r["collection_name"] or "Unknown Collection"
        if coll not in collections:
            collections[coll] = {
                "collection": coll,
                "library": r["library"],
                "missing_count": 0,
                "missing_entries": [],
            }
        collections[coll]["missing_entries"].append({
            "title": r["title"],
            "tmdb_id": r["tmdb_id"],
            "release_date": r["detail"].replace("Release: ", "") if r["detail"] else None,
        })
        collections[coll]["missing_count"] += 1

    result = sorted(collections.values(), key=lambda c: -c["missing_count"])
    return result[:limit]


def _build_alternate_versions(db, library, limit):
    """Group alternate version findings by movie title."""
    rows = db.get_gaps(gap_type="alternate_version_available", library=library)

    movies = OrderedDict()
    for row in rows:
        r = dict(row)
        title = r["title"]
        if title not in movies:
            movies[title] = {
                "title": title,
                "library": r["library"],
                "tmdb_id": r["tmdb_id"],
                "versions": [],
            }
        movies[title]["versions"].append({
            "detail": r["detail"],
        })

    result = sorted(movies.values(), key=lambda m: -len(m["versions"]))
    return result[:limit]


# ---------------------------------------------------------------------------
# Poster report
# ---------------------------------------------------------------------------

def generate_poster_report(library=None, queue_limit=100):
    """Generate a poster coverage report.

    Returns:
        - coverage: per-library breakdown of curated / tmdb_default / uncurated
        - queue: most recently added uncurated items
        - orphans: poster files without matching Plex items
        - relocks: placeholder count (from last enforce_locks run)
    """
    db = Database()
    ensure_poster_table(db)

    # ── Per-library breakdown ────────────────────────────────────────────
    lib_filter = ""
    params = []
    if library:
        lib_filter = " AND library = ?"
        params.append(library)

    by_library_rows = db.conn.execute(f"""
        SELECT
            library,
            COUNT(*) as total,
            COUNT(CASE WHEN poster_state = 'curated' THEN 1 END) as curated,
            COUNT(CASE WHEN poster_state = 'tmdb_default' THEN 1 END) as tmdb_default,
            COUNT(CASE WHEN poster_state = 'uncurated' THEN 1 END) as uncurated,
            COUNT(CASE WHEN locked = 1 THEN 1 END) as locked
        FROM poster_status
        WHERE 1=1 {lib_filter}
        GROUP BY library
        ORDER BY total DESC
    """, params).fetchall()

    coverage = []
    total_all = 0
    covered_all = 0
    for row in by_library_rows:
        r = dict(row)
        total = r["total"]
        covered = r["curated"] + r["tmdb_default"]
        pct = round(covered / total * 100, 1) if total else 0
        r["covered"] = covered
        r["coverage_pct"] = pct
        coverage.append(r)
        total_all += total
        covered_all += covered

    overall_pct = round(covered_all / total_all * 100, 1) if total_all else 0

    # ── Uncurated queue (newest first) ───────────────────────────────────
    queue_params = []
    queue_filter = ""
    if library:
        queue_filter = " AND library = ?"
        queue_params.append(library)

    queue_rows = db.conn.execute(f"""
        SELECT plex_rating_key, title, library, last_updated
        FROM poster_status
        WHERE poster_state = 'uncurated' {queue_filter}
        ORDER BY last_updated DESC
        LIMIT ?
    """, queue_params + [queue_limit]).fetchall()

    queue = [dict(r) for r in queue_rows]

    total_uncurated = db.conn.execute(f"""
        SELECT COUNT(*) FROM poster_status
        WHERE poster_state = 'uncurated' {queue_filter}
    """, queue_params).fetchone()[0]

    # ── Orphaned poster files ────────────────────────────────────────────
    orphan_result = detect_orphans()

    # ── Re-lock count (items that are curated/tmdb_default but unlocked) ─
    relock_params = []
    relock_filter = ""
    if library:
        relock_filter = " AND library = ?"
        relock_params.append(library)

    needs_relock = db.conn.execute(f"""
        SELECT COUNT(*) FROM poster_status
        WHERE poster_state IN ('curated', 'tmdb_default')
          AND locked = 0 {relock_filter}
    """, relock_params).fetchone()[0]

    db.close()

    return {
        "coverage": coverage,
        "overall": {
            "total": total_all,
            "covered": covered_all,
            "coverage_pct": overall_pct,
        },
        "queue": queue,
        "total_uncurated": total_uncurated,
        "queue_truncated": total_uncurated > queue_limit,
        "orphans": orphan_result["orphans"],
        "orphan_count": orphan_result["total"],
        "needs_relock": needs_relock,
    }
