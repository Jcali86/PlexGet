import re
import time
import requests
from thefuzz import fuzz
from config import config
from db import Database
from scanner import paths
from scanner.libraries import keys_of_kind
from scanner.wanted_search import fold, plausible_year

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


def _year_of(release_date):
    """The year of a TMDb release date, or None for a film with no date yet."""
    try:
        return int((release_date or "")[:4])
    except ValueError:
        return None


def _title_owned(tmdb_title, tmdb_original_title, tmdb_year, owned_films):
    """Check if a TMDb film matches any owned (title, year) pair.

    The year is what tells a remake from the film it remakes. Both
    token_set_ratio and partial_ratio score 100 whenever one title is
    contained in the other, so on titles alone holding Halloween (1978) meant
    Halloween (2018) and Halloween Kills were never reported missing. A year
    more than one apart is therefore a different film whatever the title says;
    one apart is allowed because TMDb dates a film by its first release and a
    filename often by its local one.

    partial_ratio is kept for number-vs-word variants ('101 Dalmatians' vs
    'One Hundred and One Dalmatians'), but only when both years are known and
    agree, since otherwise it matches any title that contains a held one. With
    a year missing on either side the title alone decides.

    A title that ends in a believable year, 'Wonder Woman 1984.mkv', has that
    number read as its year, so the title and the year together are tried as a
    title before the years are allowed to rule anything out.
    """
    candidates = [tmdb_title]
    if tmdb_original_title:
        candidates.append(tmdb_original_title)
    for owned, owned_year in owned_films:
        if owned_year is not None and f"{owned} {owned_year}" in candidates:
            return True
        years_known = tmdb_year is not None and owned_year is not None
        if years_known and abs(tmdb_year - owned_year) > 1:
            continue
        for cand in candidates:
            if cand == owned:
                return True
            if fuzz.ratio(cand, owned) >= FUZZY_THRESHOLD:
                return True
            if fuzz.token_set_ratio(cand, owned) >= FUZZY_THRESHOLD:
                return True
            if years_known and fuzz.partial_ratio(cand, owned) >= FUZZY_THRESHOLD:
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
      <library>/Halloween (1978).mkv
    """
    basename = re.split(r"[\\/]", file_path)[-1]
    # Remove extension
    name = re.sub(r"\.\w{2,4}$", "", basename)
    # Strip common release tags from the right
    name = re.split(r"[\.\s](?:\d{3,4}p|BluRay|BRRip|WEB-DL|WEBRip|REMUX|HDTV|BDRip|AMZN|NF|PCOK|WEB\b)", name, flags=re.IGNORECASE)[0]
    name = name.replace(".", " ").strip()
    # The year in brackets is Plex's own naming, and the importer's. Without
    # it the franchise check has no year to tell a remake by.
    year_match = re.search(r"[\s\.]\(?(\d{4})\)?$", name)
    # 'Blade Runner 2049' ends in a number no film has come out in, so it is
    # the title and it is what TMDb has to be asked for.
    if year_match and not plausible_year(int(year_match.group(1))):
        year_match = None
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


def _search_movie(title, year):
    params = {"query": title}
    if year:
        params["year"] = year
    return _tmdb_get("/search/movie", params).get("results", [])


def _named(hits, title, year):
    """The id of the hit called exactly this, nearest the year; else None."""
    same = [h for h in hits
            if fold(title) in (fold(h.get("title")), fold(h.get("original_title")))]
    if year:
        apart = lambda h: abs((_year_of(h.get("release_date")) or 0) - year)
        same = sorted((h for h in same if apart(h) <= 1), key=apart)
    return same[0]["id"] if same else None


def _resolve_movie(title, year):
    """TMDb's id for a held film, or None when the search finds nothing.

    TMDb ranks by popularity and its year filter takes any release, so its
    first answer for Halloween from 2018 can be the 1978 film, re-released
    that year. A hit with the file's own title and the nearest year is taken
    over it. A title ending in a believable year, 'Class of 1999', is asked
    for whole when nothing turns up under the shorter title. Failing both,
    TMDb's first answer stands, so a bare 'Halloween.mkv' is whichever
    Halloween TMDb ranks first.
    """
    hits = _search_movie(title, year)
    found = _named(hits, title, year)
    if found is None and year:
        found = _named(_search_movie(f"{title} {year}", None), f"{title} {year}", None)
    if found is None and hits:
        found = hits[0]["id"]
    return found


def analyze_movie_franchise(movie_id, library, db, owned_ids, seen_collections,
                            unresolved=None):
    """Report the parts of this film's collection that are not held.

    A part is held when its TMDb id is one of owned_ids. Titles cannot do this
    job: a sequel's title contains the first film's, and a remake's is the
    same, so no fuzzy threshold separates Saw from Saw II or one Halloween
    from another. The title match survives only for films in unresolved, the
    held (lowercased title, year) pairs TMDb could not find, which would
    otherwise be reported missing from their own collection.

    seen_collections: collection ids already reported, added to here once the
    report is written, so each collection is reported once however many of it
    are held, and one that failed is tried again by the next film in it.
    Returns count of gaps found.
    """
    details = _tmdb_get(f"/movie/{movie_id}")
    collection = details.get("belongs_to_collection")
    if not collection or collection["id"] in seen_collections:
        return 0

    coll_data = _tmdb_get(f"/collection/{collection['id']}")
    coll_name = coll_data.get("name", "Unknown Collection")

    gap_count = 0
    for part in coll_data.get("parts", []):
        if part["id"] in owned_ids:
            continue
        if unresolved and _title_owned(part["title"].lower(),
                                       (part.get("original_title") or "").lower(),
                                       _year_of(part.get("release_date")),
                                       unresolved):
            continue
        db.insert_gap(
            title=part["title"],
            library=library,
            gap_type="missing_franchise_entry",
            tmdb_id=part["id"],
            collection_name=coll_name,
            detail=f"Release: {part.get('release_date') or 'TBA'}",
        )
        gap_count += 1

    seen_collections.add(collection["id"])
    return gap_count


def analyze_alternate_versions(title, movie_id, library, db):
    """Check for alternate versions (IMAX, Director's Cut, etc.).

    Returns count of findings.
    """
    gap_count = 0

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

    limit: if set, only analyze this many movies (for testing). Every held
    film is still looked up on TMDb first, so it saves less than it used to.
    """
    if libraries is None:
        films = set(keys_of_kind("movie"))
        libraries = [l for l in db.get_non_manual_libraries() if l in films]

    total_franchise_gaps = 0
    total_alt_versions = 0
    movies_analyzed = 0

    held = {}
    for lib in libraries:
        rows = db.get_all_media(lib)
        held[lib] = sorted({_parse_movie_title(row["file_path"]) for row in rows},
                           key=lambda x: (x[0], x[1] or 0))
        print(f"  [{lib}] {len(rows)} movies found")

    # Every held film is looked up before any collection is judged, limit or
    # no limit, because a part counts as held only if its id is among them. A
    # lookup that fails is tried once more: left failed, the film falls back
    # to the title match, which is how a held Saw III passes for Saw II.
    ids, failed = {}, set()
    for title, year in sorted(set().union(*held.values()), key=lambda x: (x[0], x[1] or 0)):
        for attempt in (1, 2):
            try:
                ids[(title, year)] = _resolve_movie(title, year)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"    {title}: ERROR - {e}")
                    failed.add((title, year))

    # Cleared only now: the lookups take minutes on a large library, and the
    # last report is better than an empty one while they run, or if they fail.
    db.clear_gaps(gap_type="missing_franchise_entry")
    db.clear_gaps(gap_type="alternate_version_available")

    for lib, parsed in held.items():
        owned_ids = {ids[f] for f in parsed if ids.get(f)}
        unresolved = {(t.lower(), y) for (t, y) in parsed if not ids.get((t, y))}
        seen_collections = set()

        for i, (title, year) in enumerate(parsed):
            if limit and i >= limit:
                break
            if (title, year) in failed:
                continue
            movie_id = ids[(title, year)]
            if not movie_id:
                movies_analyzed += 1
                continue
            try:
                fgaps = analyze_movie_franchise(movie_id, lib, db, owned_ids,
                                                seen_collections, unresolved)
                total_franchise_gaps += fgaps

                agaps = analyze_alternate_versions(title, movie_id, lib, db)
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
