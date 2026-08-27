import re
import time
import requests
from thefuzz import fuzz
from config import config
from db import Database
from scanner import paths
from scanner.libraries import keys_of_kind

_TMDB = config.get("tmdb") or {}
TMDB_BASE = _TMDB.get("base_url") or "https://api.themoviedb.org/3"
TMDB_KEY = _TMDB.get("api_key") or ""
RATE_LIMIT_DELAY = 0.26  # ~4 req/sec, well within TMDb's 40/10s limit

ALTERNATE_KEYWORDS = [
    "IMAX", "Director's Cut", "Extended", "Uncut",
    "Black & White", "Theatrical", "Unrated",
]

# TMDb release_dates type codes
RELEASE_TYPE_NAMES = {
    1: "Premiere",
    2: "Theatrical (limited)",
    3: "Theatrical",
    4: "Digital",
    5: "Physical",
    6: "TV",
}


def _tmdb_get(path, params=None):
    """Make a rate-limited TMDb API call."""
    time.sleep(RATE_LIMIT_DELAY)
    url = f"{TMDB_BASE}{path}"
    p = {"api_key": TMDB_KEY}
    if params:
        p.update(params)
    resp = requests.get(url, params=p, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD = 85


def _title_owned(tmdb_title, tmdb_original_title, owned_titles):
    """Check if a TMDb title matches any owned title (exact or fuzzy).

    Uses multiple fuzz strategies to catch number-vs-word variants
    (e.g. '101 Dalmatians' vs 'One Hundred and One Dalmatians').
    """
    candidates = [tmdb_title]
    if tmdb_original_title:
        candidates.append(tmdb_original_title)
    for owned in owned_titles:
        for cand in candidates:
            if cand == owned:
                return True
            if fuzz.ratio(cand, owned) >= FUZZY_THRESHOLD:
                return True
            if fuzz.token_set_ratio(cand, owned) >= FUZZY_THRESHOLD:
                return True
            if fuzz.partial_ratio(cand, owned) >= FUZZY_THRESHOLD:
                return True
    return False


# Matches S01E02, S01E02E03, etc. in filenames
_SE_PATTERN = re.compile(r"[Ss](\d{1,3})[Ee](\d{1,3})")
# Matches season-only folder names like "S01" or "Season 1"
_SEASON_PATTERN = re.compile(r"[Ss](\d{1,3})(?![Ee])")


def _parse_show_name(file_path):
    """Extract a human-readable show name from the top-level show folder.

    The show is whatever folder sits directly inside the library, so nothing
    here needs to know what anybody calls their libraries:
      <library>/Atlanta.S01.1080p.AMZN/Atlanta.S01E01.mkv
      <library>/Blue Eye Samurai/Season 1/...mkv
    A file outside every configured library, or loose in the library root, has
    only its own name to go on.
    """
    inside = paths.below_library(file_path)
    raw = inside[0] if len(inside) > 1 else re.split(r"[\\/]", file_path)[-1]

    # Strip release-group junk: everything from S01 onward, resolution tags, etc.
    name = re.split(r"[\.\s][Ss]\d{1,3}", raw)[0]
    name = re.split(r"[\.\s]\d{3,4}p", name)[0]
    name = re.split(r"\s*[\[\(]", name)[0]
    name = name.replace(".", " ").strip()
    return name


def _parse_episodes(file_path):
    """Return list of (season, episode) tuples found in a filename."""
    basename = re.split(r"[\\/]", file_path)[-1]
    matches = _SE_PATTERN.findall(basename)
    return [(int(s), int(e)) for s, e in matches]


def _parse_movie_title(file_path):
    """Extract a clean movie title from a file path.

    Handles patterns like:
      <library>/Batman v. Superman-Dawn of Justice 2016 ...mkv
      <library>/500 Days of Summer.mkv
    """
    basename = re.split(r"[\\/]", file_path)[-1]
    # Remove extension
    name = re.sub(r"\.\w{2,4}$", "", basename)
    # Strip common release tags from the right
    name = re.split(r"[\.\s](?:\d{3,4}p|BluRay|BRRip|WEB-DL|WEBRip|REMUX|HDTV|BDRip|AMZN|NF|PCOK|WEB\b)", name, flags=re.IGNORECASE)[0]
    name = name.replace(".", " ").strip()
    # Try to pull out year
    year_match = re.search(r"[\s\.](\d{4})$", name)
    year = int(year_match.group(1)) if year_match else None
    if year_match:
        name = name[:year_match.start()].strip()
    return name, year


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyze_tv_show(show_name, owned_episodes, library, db, tmdb_id=None):
    """Analyze a single TV show for missing seasons/episodes.

    owned_episodes: set of (season, episode) tuples
    Returns count of gaps found.
    """
    gap_count = 0

    # Search TMDb if we don't have an ID
    if not tmdb_id:
        results = _tmdb_get("/search/tv", {"query": show_name})
        hits = results.get("results", [])
        if not hits:
            return 0
        tmdb_id = hits[0]["id"]

    # Get show details for season count
    show = _tmdb_get(f"/tv/{tmdb_id}")
    owned_seasons = {s for s, _e in owned_episodes}

    for season_info in show.get("seasons", []):
        snum = season_info["season_number"]
        if snum == 0:
            continue  # skip specials

        if snum not in owned_seasons:
            # Entire season missing
            db.insert_gap(
                title=show_name,
                library=library,
                gap_type="missing_season",
                season_number=snum,
                tmdb_id=tmdb_id,
            )
            gap_count += 1
            continue

        # Check individual episodes
        season_detail = _tmdb_get(f"/tv/{tmdb_id}/season/{snum}")
        for ep in season_detail.get("episodes", []):
            enum = ep["episode_number"]
            if (snum, enum) not in owned_episodes:
                db.insert_gap(
                    title=show_name,
                    library=library,
                    gap_type="missing_episode",
                    season_number=snum,
                    episode_number=enum,
                    episode_title=ep.get("name"),
                    tmdb_id=tmdb_id,
                )
                gap_count += 1

    return gap_count


def analyze_movie_franchise(title, year, library, db, owned_titles=None):
    """Check if a movie belongs to a collection and find missing entries.

    Returns count of gaps found.
    """
    gap_count = 0
    params = {"query": title}
    if year:
        params["year"] = year
    results = _tmdb_get("/search/movie", params)
    hits = results.get("results", [])
    if not hits:
        return 0

    movie = hits[0]
    movie_id = movie["id"]

    # Get full details for collection info
    details = _tmdb_get(f"/movie/{movie_id}")
    collection = details.get("belongs_to_collection")
    if not collection:
        return 0

    # Fetch collection
    coll_data = _tmdb_get(f"/collection/{collection['id']}")
    coll_name = coll_data.get("name", "Unknown Collection")

    for part in coll_data.get("parts", []):
        part_title = part["title"].lower()
        part_orig = (part.get("original_title") or "").lower()
        # Check exact match or fuzzy match (>= 85) against owned titles
        if owned_titles and _title_owned(part_title, part_orig, owned_titles):
            continue
        # Not in our collection of owned titles
        db.insert_gap(
            title=part["title"],
            library=library,
            gap_type="missing_franchise_entry",
            tmdb_id=part["id"],
            collection_name=coll_name,
            detail=f"Release: {part.get('release_date', 'TBA')}",
        )
        gap_count += 1

    return gap_count


def analyze_alternate_versions(title, year, library, db):
    """Check for alternate versions (IMAX, Director's Cut, etc.).

    Returns count of findings.
    """
    gap_count = 0
    params = {"query": title}
    if year:
        params["year"] = year
    results = _tmdb_get("/search/movie", params)
    hits = results.get("results", [])
    if not hits:
        return 0

    movie_id = hits[0]["id"]

    # Check release_dates for different release types.
    #
    # TMDb lists a release per country, so one alternate cut appears dozens of
    # times - Blade Runner's Director's Cut was reported 21 times, once per
    # territory. What matters is that the cut exists, not where it played, so
    # the countries are gathered up and reported as a single finding.
    rel_data = _tmdb_get(f"/movie/{movie_id}/release_dates")
    by_cut = {}
    for country in rel_data.get("results", []):
        for rel in country.get("release_dates", []):
            rtype = rel.get("type")
            note = rel.get("note", "")
            for kw in ALTERNATE_KEYWORDS:
                if kw.lower() in note.lower():
                    entry = by_cut.setdefault(kw, {"countries": set(), "types": set(), "note": note})
                    entry["countries"].add(country["iso_3166_1"])
                    entry["types"].add(RELEASE_TYPE_NAMES.get(rtype, "Unknown"))

    for kw, entry in sorted(by_cut.items()):
        places = ", ".join(sorted(entry["countries"])[:6])
        if len(entry["countries"]) > 6:
            places += f" +{len(entry['countries']) - 6} more"
        db.insert_gap(
            title=title,
            library=library,
            gap_type="alternate_version_available",
            tmdb_id=movie_id,
            detail=f"{kw} — {'/'.join(sorted(entry['types']))} ({places})",
        )
        gap_count += 1

    # Also search TMDb for keyword-tagged alternate versions
    keywords_data = _tmdb_get(f"/movie/{movie_id}/keywords")
    for kw_obj in keywords_data.get("keywords", []):
        kw_name = kw_obj["name"].lower()
        for alt_kw in ALTERNATE_KEYWORDS:
            if alt_kw.lower() in kw_name:
                db.insert_gap(
                    title=title,
                    library=library,
                    gap_type="alternate_version_available",
                    tmdb_id=movie_id,
                    detail=f"TMDb keyword: {kw_obj['name']}",
                )
                gap_count += 1

    return gap_count


# ---------------------------------------------------------------------------
# Full analysis orchestrators
# ---------------------------------------------------------------------------

def _show_key(name):
    """Group key for a show name.

    Folder spellings vary across volumes - "south park" and "South Park" are
    the same show - so shows are grouped case- and spacing-insensitively.
    Without this each spelling is analysed separately and every season the
    other copy holds is reported missing.
    """
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def _preferred_display_name(a, b):
    """Pick the tidier of two spellings of the same show name."""
    if not a:
        return b
    if not b:
        return a
    # More capitalised words reads as the properly-cased variant.
    caps = lambda n: sum(1 for w in n.split() if w[:1].isupper())
    if caps(a) != caps(b):
        return a if caps(a) > caps(b) else b
    return a if len(a) >= len(b) else b


def run_tv_analysis(db, libraries=None, limit=None):
    """Analyze TV show gaps across libraries.

    limit: if set, only analyze this many shows (for testing).
    """
    if libraries is None:
        tv = set(keys_of_kind("show"))
        libraries = [l for l in db.get_non_manual_libraries() if l in tv]

    db.clear_gaps(gap_type="missing_season")
    db.clear_gaps(gap_type="missing_episode")

    total_gaps = 0
    shows_analyzed = 0

    for lib in libraries:
        rows = db.get_all_media(lib)
        # Group files by show name, keyed case-insensitively so that the same
        # show spelled differently on two volumes is analysed once.
        shows = {}
        for row in rows:
            eps = _parse_episodes(row["file_path"])
            if not eps:
                continue
            name = _parse_show_name(row["file_path"])
            key = _show_key(name)
            if key not in shows:
                shows[key] = {"name": name, "episodes": set()}
            else:
                shows[key]["name"] = _preferred_display_name(shows[key]["name"], name)
            shows[key]["episodes"].update(eps)

        print(f"  [{lib}] {len(shows)} unique shows found")

        for i, (show_name, owned_eps) in enumerate(
            (entry["name"], entry["episodes"])
            for _, entry in sorted(shows.items())
        ):
            if limit and i >= limit:
                break
            try:
                gaps = analyze_tv_show(show_name, owned_eps, lib, db)
                total_gaps += gaps
                shows_analyzed += 1
                if gaps:
                    print(f"    {show_name}: {gaps} gaps")
            except Exception as e:
                print(f"    {show_name}: ERROR - {e}")

    return {"shows_analyzed": shows_analyzed, "gaps_found": total_gaps}


def run_movie_analysis(db, libraries=None, limit=None):
    """Analyze movie franchise gaps and alternate versions.

    limit: if set, only analyze this many movies (for testing).
    """
    if libraries is None:
        films = set(keys_of_kind("movie"))
        libraries = [l for l in db.get_non_manual_libraries() if l in films]

    db.clear_gaps(gap_type="missing_franchise_entry")
    db.clear_gaps(gap_type="alternate_version_available")

    total_franchise_gaps = 0
    total_alt_versions = 0
    movies_analyzed = 0

    for lib in libraries:
        rows = db.get_all_media(lib)

        # Build set of owned titles (lowercased) for franchise matching
        owned_titles = set()
        parsed = []
        for row in rows:
            title, year = _parse_movie_title(row["file_path"])
            owned_titles.add(title.lower())
            parsed.append((title, year))

        print(f"  [{lib}] {len(parsed)} movies found")

        for i, (title, year) in enumerate(sorted(set(parsed), key=lambda x: (x[0], x[1] or 0))):
            if limit and i >= limit:
                break
            try:
                fgaps = analyze_movie_franchise(title, year, lib, db, owned_titles)
                total_franchise_gaps += fgaps

                agaps = analyze_alternate_versions(title, year, lib, db)
                total_alt_versions += agaps

                movies_analyzed += 1
                if fgaps or agaps:
                    print(f"    {title}: {fgaps} franchise gaps, {agaps} alt versions")
            except Exception as e:
                print(f"    {title}: ERROR - {e}")

    return {
        "movies_analyzed": movies_analyzed,
        "franchise_gaps": total_franchise_gaps,
        "alternate_versions": total_alt_versions,
    }


def run_full_analysis(db, limit=None):
    """Run all gap analyses. Set limit for testing."""
    print("=== TV Show Gap Analysis ===")
    tv_result = run_tv_analysis(db, limit=limit)
    print(f"  TV totals: {tv_result}")

    print("\n=== Movie Franchise & Alternate Version Analysis ===")
    movie_result = run_movie_analysis(db, limit=limit)
    print(f"  Movie totals: {movie_result}")

    return {"tv": tv_result, "movies": movie_result}
