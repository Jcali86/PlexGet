"""Plex filename auditor and rename suggestion engine.

Audits TV files against Plex's recommended naming convention:
  Show Name (Year)/Season XX/Show Name (Year) - sXXeXX - Episode Title.ext

Generates rename suggestions but never renames automatically.
"""

import os
import re
import time
import requests
from pathlib import Path
from db import Database
from config import config
from scanner import paths
from scanner.libraries import keys_of_kind

_TMDB = config.get("tmdb") or {}
TMDB_BASE = _TMDB.get("base_url") or "https://api.themoviedb.org/3"
TMDB_KEY = _TMDB.get("api_key") or ""
RATE_LIMIT_DELAY = 0.26

# ---------------------------------------------------------------------------
# Known problem shows — TVDB/TMDb numbering conflicts
# ---------------------------------------------------------------------------

KNOWN_ISSUES = {
    "american dad": {
        "description": "TBS seasons are offset from Fox seasons. TVDB uses broadcast order which differs from DVD/streaming season numbering.",
        "tmdb_id": 1433,
        "tvdb_id": 73141,
        "fix": "plexmatch",
        "plexmatch": {
            "title": "American Dad!",
            "year": 2005,
            "tvdbid": 73141,
            "note": "Season numbering follows TBS broadcast order. Fox S1-11 map to TVDB S1-11, TBS S12+ may need offset.",
        },
    },
    "family guy": {
        "description": "First aired 1999, often mislabeled as 1998. Seasons split across Fox cancellation/revival. TVDB has different special numbering.",
        "tmdb_id": 1434,
        "tvdb_id": 75978,
        "fix": "plexmatch",
        "plexmatch": {
            "title": "Family Guy",
            "year": 1999,
            "tvdbid": 75978,
            "note": "Year must be 1999 (not 1998). Season numbering consistent but specials need Season 00.",
        },
    },
    "south park": {
        "description": "Some releases use absolute episode numbering instead of seasonal. TVDB uses seasonal numbering consistently.",
        "tmdb_id": 2190,
        "tvdb_id": 75897,
        "fix": "plexmatch",
        "plexmatch": {
            "title": "South Park",
            "year": 1997,
            "tvdbid": 75897,
            "note": "Must use seasonal numbering (S01E01), not absolute (E001). Some releases mix both.",
        },
    },
    "mythbusters": {
        "description": "TVDB uses date-based seasons (2003, 2004, etc.) instead of sequential. Specials interspersed with regular episodes.",
        "tmdb_id": 1091,
        "tvdb_id": 73388,
        "fix": "plexmatch",
        "plexmatch": {
            "title": "MythBusters",
            "year": 2003,
            "tvdbid": 73388,
            "note": "TVDB uses year-based seasons (2003=S1, 2004=S2...). Files with YEARxEP format need mapping to SxxExx.",
        },
    },
    "doctor who": {
        "description": "Classic (1963) and modern (2005) are separate TVDB entries. Mixing them causes mismatches.",
        "tmdb_id": 57243,
        "tvdb_id": 78804,
        "fix": "plexmatch",
        "plexmatch": {
            "title": "Doctor Who",
            "year": 2005,
            "tvdbid": 78804,
            "note": "Modern series (2005 reboot) only. Classic Who (1963-1989) is TVDB 76107. Must be separate libraries.",
        },
    },
    "futurama": {
        "description": "Broadcast order differs from DVD order. TVDB follows broadcast order. Movies split into episodes on TVDB.",
        "tmdb_id": 615,
        "tvdb_id": 73871,
        "fix": "plexmatch",
        "plexmatch": {
            "title": "Futurama",
            "year": 1999,
            "tvdbid": 73871,
            "note": "DVD order differs from broadcast. TVDB uses broadcast order. S5 movies are split into S5 episodes on TVDB.",
        },
    },
}

# ---------------------------------------------------------------------------
# Filename parsing regexes
# ---------------------------------------------------------------------------

# Standard SxxExx
_SXXEXX = re.compile(r"[Ss](\d{1,3})[Ee](\d{1,3})")
# NxNN format (e.g. 1x01, 24x11)
_NXNN = re.compile(r"(?<![x\d])(\d{1,3})x(\d{2,3})(?![\d])", re.IGNORECASE)
# Multi-episode: E01E02, E01-E02, E01-02
_MULTI_EP = re.compile(r"[Ee](\d{2,3})[-\s]?[Ee]?(\d{2,3})")
# Year in path
_YEAR = re.compile(r"[\(\s\.](\d{4})[\)\s\.\-]")
# Season folder: Season XX, Season 0, Season 00
_SEASON_FOLDER = re.compile(r"[Ss]eason\s*(\d{1,3})")
# Plex ideal: Show Name (Year) - SxxExx - Episode Title
_IDEAL = re.compile(
    r"^(.+?)\s*\((\d{4})\)\s*-\s*[Ss](\d{2,})[Ee](\d{2,})\s*-\s*(.+)\.\w{2,4}$"
)


# ---------------------------------------------------------------------------
# TMDb lookups (cached per session)
# ---------------------------------------------------------------------------

_tmdb_cache = {}


def _tmdb_get(path, params=None):
    """Rate-limited TMDb API call with caching."""
    cache_key = (path, str(params))
    if cache_key in _tmdb_cache:
        return _tmdb_cache[cache_key]
    time.sleep(RATE_LIMIT_DELAY)
    url = f"{TMDB_BASE}{path}"
    p = {"api_key": TMDB_KEY}
    if params:
        p.update(params)
    resp = requests.get(url, params=p, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    _tmdb_cache[cache_key] = data
    return data


def _search_show(name):
    """Search TMDb for a TV show, return (id, name, year) or None."""
    results = _tmdb_get("/search/tv", {"query": name})
    hits = results.get("results", [])
    if not hits:
        return None
    best = hits[0]
    year = best.get("first_air_date", "")[:4]
    return best["id"], best["name"], year


def _get_episode_title(tmdb_id, season, episode):
    """Get episode title from TMDb."""
    try:
        data = _tmdb_get(f"/tv/{tmdb_id}/season/{season}/episode/{episode}")
        return data.get("name")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Path parsing helpers
# ---------------------------------------------------------------------------

def _parse_tv_path(file_path):
    """Parse a TV file path into components.

    Returns dict with: show_name, year, season, episode, episode_title,
    format_type, folder_show, season_folder, ext, issues[]
    """
    parts = re.split(r"[\\/]", file_path)
    basename = parts[-1]
    ext = Path(basename).suffix

    result = {
        "show_name": None,
        "year": None,
        "season": None,
        "episode": None,
        "episode_title": None,
        "multi_episode": None,
        "format_type": None,  # sxxexx, nxnn, absolute, unknown
        "season_folder": None,
        "ext": ext,
        "issues": [],
    }

    # The show folder is whatever sits directly inside the library, so this
    # works whatever anybody has called theirs. A file loose in the library
    # root has no show folder, and the issues below say so.
    inside = paths.below_library(file_path)
    show_folder = inside[0] if len(inside) > 1 else None

    # Extract show name from folder
    if show_folder:
        name = re.split(r"[\.\s][Ss]\d{1,3}", show_folder)[0]
        name = re.split(r"[\.\s]\d{3,4}p", name)[0]
        name = re.split(r"\s*[\[\(]", name)[0]
        name = re.split(r"\s+(?:COMPLETE|Complete|complete)", name)[0]
        result["show_name"] = name.replace(".", " ").strip()

    # Extract year from full path
    years = [int(y) for y in _YEAR.findall(file_path) if 1950 <= int(y) <= 2030]
    if years:
        result["year"] = str(min(years))

    # Detect season folder
    for p in parts[:-1]:
        sm = _SEASON_FOLDER.search(p)
        if sm:
            result["season_folder"] = int(sm.group(1))
            break

    # Parse episode info from filename
    sxxexx = _SXXEXX.search(basename)
    nxnn = _NXNN.search(basename)

    if sxxexx:
        result["season"] = int(sxxexx.group(1))
        result["episode"] = int(sxxexx.group(2))
        result["format_type"] = "sxxexx"
    elif nxnn:
        result["season"] = int(nxnn.group(1))
        result["episode"] = int(nxnn.group(2))
        result["format_type"] = "nxnn"
        result["issues"].append("nxnn_format")
    else:
        result["format_type"] = "unknown"
        result["issues"].append("no_episode_pattern")

    # Check for multi-episode
    multi = _MULTI_EP.findall(basename)
    if multi and len(multi) > 0:
        result["multi_episode"] = [(int(m[0]), int(m[1])) for m in multi]

    # Check ideal Plex format
    ideal = _IDEAL.match(basename)
    if ideal:
        result["episode_title"] = ideal.group(5).rsplit(".", 1)[0] if "." in ideal.group(5) else ideal.group(5)

    return result


# ---------------------------------------------------------------------------
# Issue detection
# ---------------------------------------------------------------------------

def _audit_file(file_path, parsed):
    """Detect naming issues. Returns list of (issue_type, description) tuples."""
    issues = []
    basename = Path(file_path).name

    # 1. No year in show folder or filename
    if not parsed["year"]:
        issues.append(("missing_year", "No year found in path"))

    # 2. NxNN format instead of SxxExx
    if parsed["format_type"] == "nxnn":
        issues.append(("nxnn_format", f"Uses NxNN format ({parsed['season']}x{parsed['episode']:02d}) instead of SxxExx"))

    # 3. Unpadded season/episode
    if parsed["season"] is not None and parsed["episode"] is not None:
        if parsed["format_type"] == "sxxexx":
            # Check for S1E1 instead of S01E01 in the actual filename
            unpadded = re.search(r"[Ss](\d)[Ee](\d)(?!\d)", basename)
            if unpadded:
                issues.append(("unpadded_numbers", f"Unpadded S{unpadded.group(1)}E{unpadded.group(2)}"))

    # 4. Multi-episode with wrong delimiter
    if parsed["multi_episode"]:
        # Check for E01-02 instead of E01E02 or E01-E02
        bad_multi = re.search(r"[Ee](\d{2,3})-(\d{2,3})(?![Ee\d])", basename)
        if bad_multi:
            issues.append(("bad_multi_episode", f"Multi-episode uses E{bad_multi.group(1)}-{bad_multi.group(2)} instead of E{bad_multi.group(1)}E{bad_multi.group(2)}"))

    # 5. Specials not in Season 00 folder
    if parsed["season"] == 0 and parsed["season_folder"] is not None and parsed["season_folder"] != 0:
        issues.append(("special_wrong_folder", f"Special (S00) in Season {parsed['season_folder']:02d} folder instead of Season 00"))

    # 6. Dots instead of spaces in show-relevant parts of filename
    if parsed["format_type"] == "sxxexx":
        pre_se = re.split(r"[Ss]\d{1,3}[Ee]\d{1,3}", basename)[0]
        if "." in pre_se and " " not in pre_se and len(pre_se) > 3:
            issues.append(("dots_in_name", "Dots used instead of spaces in show name"))

    # 7. No episode title in filename
    if parsed["format_type"] in ("sxxexx", "nxnn") and parsed["season"] is not None:
        # Check if there's anything meaningful after the episode number
        if parsed["format_type"] == "sxxexx":
            after = re.split(r"[Ss]\d{1,3}[Ee]\d{1,3}", basename, maxsplit=1)
        else:
            after = re.split(r"\d{1,3}x\d{2,3}", basename, maxsplit=1)
        if len(after) > 1:
            rest = after[1]
            # Strip quality/codec tags to see if there's a title
            rest = re.sub(r"[\.\s]?\d{3,4}p.*", "", rest)
            rest = re.sub(r"[\.\s]?(WEB|BluRay|BRRip|HDTV|AMZN|REMUX).*", "", rest, flags=re.IGNORECASE)
            rest = rest.strip(". -")
            if not rest:
                issues.append(("missing_episode_title", "No episode title in filename"))

    return issues


# ---------------------------------------------------------------------------
# Rename suggestion generator
# ---------------------------------------------------------------------------

def _suggest_rename(file_path, parsed, issues, tmdb_info=None):
    """Generate a corrected path based on detected issues.

    Returns (suggested_path, confidence) or (None, None) if no fix needed.
    """
    if not issues:
        return None, None

    basename = re.split(r"[\\/]", file_path)[-1]
    ext = parsed["ext"]

    show_name = parsed["show_name"] or "Unknown"
    season = parsed["season"]
    episode = parsed["episode"]
    year = parsed["year"]
    ep_title = parsed["episode_title"]

    confidence = "high"

    # Use TMDb data if available
    if tmdb_info:
        show_name = tmdb_info.get("name", show_name)
        year = year or tmdb_info.get("year")
        if not ep_title and tmdb_info.get("episode_title"):
            ep_title = tmdb_info["episode_title"]
            confidence = "medium"  # TMDb title could be wrong for renumbered shows

    if season is None or episode is None:
        return None, None

    # Clean show name for filesystem
    clean_name = re.sub(r'[<>:"/\\|?*]', "", show_name)

    # Build ideal filename
    if ep_title:
        # Clean episode title
        clean_title = re.sub(r'[<>:"/\\|?*]', "", ep_title)
        new_basename = f"{clean_name}" + (f" ({year})" if year else "") + f" - S{season:02d}E{episode:02d} - {clean_title}{ext}"
    else:
        new_basename = f"{clean_name}" + (f" ({year})" if year else "") + f" - S{season:02d}E{episode:02d}{ext}"
        if not year:
            confidence = "low"

    # A rename stays inside the library the file is already in, on the same
    # drive: moving files between drives is not a rename and is not this
    # tool's business. A file outside every configured library has nowhere to
    # be put, so nothing is suggested for it.
    library_root = paths.library_root_for(file_path)
    if library_root is None:
        return None, None

    show_folder = f"{clean_name}" + (f" ({year})" if year else "")
    season_folder = f"Season {season:02d}"

    new_path = os.path.join(library_root, show_folder, season_folder, new_basename)

    # Don't suggest if it's already correct
    if new_path == file_path:
        return None, None

    # Lower confidence for files without year
    if not year:
        confidence = "low"

    # Issue types that are just cosmetic get medium confidence
    issue_types = {i[0] for i in issues}
    if issue_types == {"dots_in_name"} or issue_types == {"missing_episode_title"}:
        confidence = "medium"

    return new_path, confidence


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_audit(db, limit=None, libraries=None):
    """Audit TV files for Plex naming issues.

    Returns summary dict.
    """
    if libraries is None:
        tv = set(keys_of_kind("show"))
        libraries = [l for l in db.get_non_manual_libraries() if l in tv]

    db.clear_suggestions()

    total_audited = 0
    total_issues = 0
    total_suggestions = 0
    issue_counts = {}

    for lib in libraries:
        rows = db.get_all_media(lib)
        print(f"  [{lib}] {len(rows)} files to audit")

        # Group files by show name for TMDb efficiency
        shows = {}
        for row in rows:
            parsed = _parse_tv_path(row["file_path"])
            sn = (parsed["show_name"] or "").lower()
            if sn not in shows:
                shows[sn] = []
            shows[sn].append((row["file_path"], parsed))

        shows_processed = 0
        for show_key, files in sorted(shows.items()):
            if limit and total_audited >= limit:
                break

            # Look up TMDb once per show
            tmdb_info = None
            if show_key and show_key not in ("unknown", ""):
                try:
                    result = _search_show(show_key)
                    if result:
                        tmdb_info = {"id": result[0], "name": result[1], "year": result[2]}
                except Exception:
                    pass

            for file_path, parsed in files:
                if limit and total_audited >= limit:
                    break

                issues = _audit_file(file_path, parsed)
                total_audited += 1

                if not issues:
                    continue

                total_issues += 1
                for issue_type, _desc in issues:
                    issue_counts[issue_type] = issue_counts.get(issue_type, 0) + 1

                # Get episode title from TMDb if we have the info
                file_tmdb = dict(tmdb_info) if tmdb_info else None
                if file_tmdb and parsed["season"] and parsed["episode"]:
                    try:
                        et = _get_episode_title(file_tmdb["id"], parsed["season"], parsed["episode"])
                        if et:
                            file_tmdb["episode_title"] = et
                    except Exception:
                        pass

                suggested, confidence = _suggest_rename(file_path, parsed, issues, file_tmdb)

                if suggested:
                    primary_issue = issues[0][0]
                    db.insert_rename(
                        file_path, suggested, primary_issue, confidence, lib,
                    )
                    total_suggestions += 1
                    if total_suggestions <= 5:
                        print(f"    [{confidence}] {Path(file_path).name}")
                        print(f"       -> {Path(suggested).name}")
                        print(f"       Issues: {', '.join(i[0] for i in issues)}")

            shows_processed += 1

    return {
        "files_audited": total_audited,
        "files_with_issues": total_issues,
        "suggestions_generated": total_suggestions,
        "issue_breakdown": issue_counts,
    }


def apply_rename(db, suggestion_id):
    """Apply a single rename suggestion. Returns (success, message)."""
    row = db.conn.execute(
        "SELECT * FROM rename_suggestions WHERE id = ?", (suggestion_id,)
    ).fetchone()
    if not row:
        return False, f"Suggestion {suggestion_id} not found"

    r = dict(row)
    if r["applied"]:
        return False, f"Suggestion {suggestion_id} already applied"

    original = Path(r["original_path"])
    suggested = Path(r["suggested_path"])

    if not original.exists():
        return False, f"Original file not found: {original}"

    # Create target directory if needed
    suggested.parent.mkdir(parents=True, exist_ok=True)

    # Perform rename
    try:
        original.rename(suggested)
    except OSError as e:
        return False, f"Rename failed: {e}"

    db.mark_applied(suggestion_id)

    # Update media_files table
    db.conn.execute(
        "UPDATE media_files SET file_path = ? WHERE file_path = ?",
        (str(suggested), str(original)),
    )
    db.conn.commit()

    return True, f"Renamed: {original.name} -> {suggested.name}"


def apply_batch(db, confidence="high"):
    """Apply all suggestions at a given confidence level. Returns summary."""
    rows = db.get_suggestions(confidence=confidence, applied=False)
    applied = 0
    errors = []
    for row in rows:
        success, msg = apply_rename(db, row["id"])
        if success:
            applied += 1
        else:
            errors.append(msg)
    return {"applied": applied, "errors": errors}


def get_known_issues():
    """Return the known problem shows dict."""
    return {
        name: {
            "description": info["description"],
            "tmdb_id": info["tmdb_id"],
            "tvdb_id": info["tvdb_id"],
            "fix_type": info["fix"],
            "plexmatch": info.get("plexmatch"),
        }
        for name, info in KNOWN_ISSUES.items()
    }
