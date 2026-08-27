"""
Export builder — generates search-engine-ready terms for media upgrades.

Produces copy-pasteable strings like "Call Me By Your Name 2017 1080p"
from the quality_scores, gaps, and poster_status tables.
"""

import re
from collections import OrderedDict
from scanner import paths
from scanner.gap_analysis import _parse_movie_title
from scanner.poster_manager import ensure_poster_table

# An episode names itself in its filename; a film does not. That is the only
# reliable way to tell the two apart without knowing what anybody calls their
# libraries.
_EPISODE_MARK = re.compile(r"[Ss]\d{1,3}[Ee]\d{1,3}")


# ── Search term formatting ───────────────────────────────────────────────────

def _target_quality(width, height, issue_hint=None):
    """Determine the target quality string based on current resolution and issue."""
    if issue_hint and "low_bitrate_1080p" in issue_hint:
        return "1080p BluRay"
    if issue_hint and "stereo_only" in issue_hint:
        return "5.1"
    if width and height:
        if height >= 1080:
            return "1080p BluRay"
        return "1080p"
    return "1080p"


def _build_term(title, year, quality_suffix):
    """Assemble a clean search term."""
    parts = [title]
    if year:
        parts.append(str(year))
    if quality_suffix:
        parts.append(quality_suffix)
    return " ".join(parts)


def _clean_release_tags(name):
    """Strip common release/scene tags from a title string."""
    # Strip from first release-quality tag onward (DVDRip, BRRip, HDRip, etc.)
    name = re.split(
        r"[\.\s\-](?:DVDScr|DVDRip|BDRip|BRRip|HDRip|WEBRip|WEB-DL|HDTV|PDTV|"
        r"BluRay|REMUX|AMZN|NF|PCOK|WEB\b|XVID|XviD|x264|x265|H\.?264|H\.?265|"
        r"AAC|AC3|DTS|DD5|MP3|HEVC|AVC|FLAC|Atmos|TrueHD|"
        r"\d{3,4}p\b)",
        name, maxsplit=1, flags=re.IGNORECASE,
    )[0]
    # Strip trailing scene group tags like "-CM8", "-EVO", "-FRAGMENT"
    name = re.sub(r"\s*-[A-Za-z0-9]+$", "", name)
    # Strip bracket/paren metadata like [SUBS], (XviD) — but keep (Year)
    name = re.sub(r"\s*\[[^\]]*\]", "", name)
    name = re.sub(r"\s*\([^)]*[A-Za-z][^)]*\)", "", name)  # parens containing letters
    return name.strip()


def _extract_title_from_path(file_path):
    """Extract title and year from a file path. Works for movies and TV.

    The folder sitting directly inside the library is the show, for anything
    that names an episode in its filename. For a film that folder is the title
    only when it carries a year - Plex's own convention - because a folder
    without one is as likely to be a shelf somebody made ("90s night") as the
    film's own.
    """
    inside = paths.below_library(file_path)
    basename = re.split(r"[\\/]", file_path)[-1]
    folder = inside[0] if len(inside) > 1 else ""

    if folder and _EPISODE_MARK.search(basename):
        name = re.split(r"[\.\s][Ss]\d{1,3}", folder)[0]
        name = re.split(r"[\.\s]\d{3,4}p", name)[0]
        name = re.split(r"\s*[\[\(]", name)[0]
        name = name.replace(".", " ").strip()
        year_match = re.search(r"\b((?:19|20)\d{2})$", name)
        year = int(year_match.group(1)) if year_match else None
        if year_match:
            name = name[:year_match.start()].strip()
        return name, year

    if folder and re.search(r"\b(19|20)\d{2}\b", folder):
        name = _clean_release_tags(folder.replace(".", " ").strip())
        year_match = re.search(r"[\s\(]((?:19|20)\d{2})\)?$", name)
        year = int(year_match.group(1)) if year_match else None
        if year_match:
            name = name[:year_match.start()].strip().rstrip("(")
        return name, year

    # Loose in the library, or in a folder that gave nothing away: the
    # filename is all there is.
    name = re.sub(r"\.\w{2,4}$", "", basename)
    name = name.replace(".", " ").strip()
    name = _clean_release_tags(name)
    year_match = re.search(r"[\s\(]((?:19|20)\d{2})\)?(?:\s|$)", name)
    year = int(year_match.group(1)) if year_match else None
    if year_match:
        name = name[:year_match.start()].strip()
    return name.rstrip("(").strip(), year


# ── Quality issue exporters ──────────────────────────────────────────────────

def _export_quality_category(db, issue_filter, label, quality_override=None, library=None):
    """Generic exporter for quality-based categories.

    Deduplicates by title+year so each movie/show appears once.
    """
    rows = db.get_quality(issue=issue_filter, library=library, limit=5000)

    seen = OrderedDict()
    for row in rows:
        r = dict(row)
        title, year = _extract_title_from_path(r["file_path"])
        key = f"{title}|{year}"

        if key in seen:
            seen[key]["file_count"] += 1
            continue

        quality = quality_override or _target_quality(
            r.get("width"), r.get("height"), r.get("issues")
        )
        term = _build_term(title, year, quality)

        seen[key] = {
            "term": term,
            "detail": f"Score: {r['score']} | {r.get('resolution', '?')} | {r.get('video_codec', '?')}",
            "score": r["score"],
            "library": r.get("library", ""),
            "file_count": 1,
        }

    # Add file count to detail for multi-file entries
    terms = []
    for item in seen.values():
        if item["file_count"] > 1:
            item["detail"] += f" | {item['file_count']} files"
        terms.append(item)

    return {
        "label": label,
        "count": len(terms),
        "terms": terms,
    }


def export_avi_files(db, library=None):
    return _export_quality_category(db, "bad_container:avi", "AVI Container", "1080p", library)


def export_low_resolution(db, library=None):
    return _export_quality_category(db, "low_resolution", "Low Resolution", "1080p", library)


def export_bad_codecs(db, library=None):
    return _export_quality_category(db, "old_video_codec", "Bad Codecs (MPEG-4/XVID/WMV)", "1080p", library)


def export_low_bitrate_1080p(db, library=None):
    return _export_quality_category(db, "low_bitrate_1080p", "Low Bitrate 1080p", "1080p BluRay", library)


def export_low_bitrate_720p(db, library=None):
    return _export_quality_category(db, "low_bitrate_720p", "Low Bitrate 720p", "1080p", library)


def export_stereo_only(db, library=None):
    return _export_quality_category(db, "stereo_only", "Stereo Only (No Surround)", "5.1", library)


# ── Gap exporters ────────────────────────────────────────────────────────────

def export_missing_episodes(db, library=None):
    """Export missing TV episodes as search terms."""
    rows = db.get_gaps(gap_type="missing_episode", library=library)
    terms = []
    for row in rows:
        r = dict(row)
        ep = f"S{r['season_number']:02d}E{r['episode_number']:02d}" if r["episode_number"] else f"S{r['season_number']:02d}"
        term = f"{r['title']} {ep}"
        detail = r.get("episode_title") or ""
        if r.get("tmdb_id"):
            detail += f" | tmdb:{r['tmdb_id']}"
        terms.append({"term": term, "detail": detail.strip(" |"), "library": r.get("library", "")})

    return {
        "label": "Missing Episodes",
        "count": len(terms),
        "terms": terms,
    }


def export_missing_seasons(db, library=None):
    """Export missing TV seasons as search terms."""
    rows = db.get_gaps(gap_type="missing_season", library=library)
    terms = []
    for row in rows:
        r = dict(row)
        term = f"{r['title']} Season {r['season_number']}"
        detail = ""
        if r.get("tmdb_id"):
            detail = f"tmdb:{r['tmdb_id']}"
        terms.append({"term": term, "detail": detail, "library": r.get("library", "")})

    return {
        "label": "Missing Seasons",
        "count": len(terms),
        "terms": terms,
    }


def export_franchise_gaps(db, library=None):
    """Export missing franchise/collection entries."""
    rows = db.get_gaps(gap_type="missing_franchise_entry", library=library)
    terms = []
    for row in rows:
        r = dict(row)
        # Extract year from detail field ("Release: 2019-05-24")
        year = None
        if r.get("detail"):
            year_match = re.search(r"(\d{4})", r["detail"])
            if year_match:
                year = year_match.group(1)
        term = _build_term(r["title"], year, None)
        detail = r.get("collection_name") or ""
        if r.get("tmdb_id"):
            detail += f" | tmdb:{r['tmdb_id']}"
        terms.append({"term": term, "detail": detail.strip(" |"), "library": r.get("library", "")})

    return {
        "label": "Missing Franchise Entries",
        "count": len(terms),
        "terms": terms,
    }


def export_alternate_versions(db, library=None):
    """Export alternate versions available (IMAX, Director's Cut, etc.)."""
    rows = db.get_gaps(gap_type="alternate_version_available", library=library)

    # Group by title to avoid duplicates
    seen = OrderedDict()
    for row in rows:
        r = dict(row)
        title = r["title"]
        detail_str = r.get("detail", "")

        # Extract version keyword from detail
        version = ""
        for kw in ["IMAX", "Director's Cut", "Extended", "Uncut", "Black & White", "Theatrical", "Unrated"]:
            if kw.lower() in detail_str.lower():
                version = kw
                break

        key = f"{title}|{version}"
        if key in seen:
            continue

        term = f"{title} {version}".strip() if version else title
        seen[key] = {"term": term, "detail": detail_str, "library": r.get("library", "")}

    terms = list(seen.values())
    return {
        "label": "Alternate Versions Available",
        "count": len(terms),
        "terms": terms,
    }


# ── Poster exporter ─────────────────────────────────────────────────────────

def export_uncurated_posters(db, library=None):
    """Export uncurated poster items."""
    ensure_poster_table(db)
    query = "SELECT title, library FROM poster_status WHERE poster_state = 'uncurated'"
    params = []
    if library:
        query += " AND library = ?"
        params.append(library)
    query += " ORDER BY title"
    rows = db.conn.execute(query, params).fetchall()

    terms = []
    for row in rows:
        r = dict(row)
        terms.append({
            "term": f"{r['title']} poster",
            "detail": r.get("library", ""),
            "library": r.get("library", ""),
        })

    return {
        "label": "Uncurated Posters",
        "count": len(terms),
        "terms": terms,
    }


# ── Category registry ────────────────────────────────────────────────────────

CATEGORIES = OrderedDict([
    ("avi_files",           {"fn": export_avi_files,           "section": "Quality Issues"}),
    ("low_resolution",      {"fn": export_low_resolution,      "section": "Quality Issues"}),
    ("bad_codecs",          {"fn": export_bad_codecs,          "section": "Quality Issues"}),
    ("low_bitrate_1080p",   {"fn": export_low_bitrate_1080p,   "section": "Quality Issues"}),
    ("low_bitrate_720p",    {"fn": export_low_bitrate_720p,    "section": "Quality Issues"}),
    ("stereo_only",         {"fn": export_stereo_only,         "section": "Quality Issues"}),
    ("missing_episodes",    {"fn": export_missing_episodes,    "section": "Missing Content"}),
    ("missing_seasons",     {"fn": export_missing_seasons,     "section": "Missing Content"}),
    ("franchise_gaps",      {"fn": export_franchise_gaps,      "section": "Missing Content"}),
    ("alternate_versions",  {"fn": export_alternate_versions,  "section": "Missing Content"}),
    ("uncurated_posters",   {"fn": export_uncurated_posters,   "section": "Poster Curation"}),
])


# ── Orchestrator ─────────────────────────────────────────────────────────────

def generate_export(db, categories=None, library=None):
    """Generate export data for the requested categories.

    categories: list of category keys, or None for all.
    library: optional library filter.
    Returns: {"categories": [...], "total_terms": int}
    """
    keys = categories if categories else list(CATEGORIES.keys())
    results = []
    total = 0

    for key in keys:
        if key not in CATEGORIES:
            continue
        cat = CATEGORIES[key]
        data = cat["fn"](db, library=library)
        data["key"] = key
        data["section"] = cat["section"]
        results.append(data)
        total += data["count"]

    return {"categories": results, "total_terms": total}


def generate_export_text(db, categories=None, library=None):
    """Generate a plain-text export file content."""
    export = generate_export(db, categories=categories, library=library)
    lines = []
    for cat in export["categories"]:
        if not cat["terms"]:
            continue
        lines.append(f"# {cat['label']} ({cat['count']})")
        for item in cat["terms"]:
            lines.append(item["term"])
        lines.append("")  # blank line between sections

    return "\n".join(lines)
