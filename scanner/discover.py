"""Find films worth having that the library does not hold.

The gap analysis starts from what is owned and looks outward: the rest of a
collection, the seasons a show is short of. This starts from the other end.
TMDb is asked for every film matching a mood - horror and comedy, rated six or
better, enough votes to trust the number - and whatever Plex already lists is
taken away. What is left is a shopping list for a genre, rather than a hole in
a franchise.

The rating is TMDb's own user score, not IMDb's. TMDb does not carry IMDb
ratings, and getting them would mean either a second API key or a per-film
lookup against the IMDb datasets. The two track each other closely enough that
a floor of six means the same thing on either.

Nothing here fetches anything. A pick lands on the wanted list, as ever.
"""

import datetime
import time

import requests

from config import config
from scanner.wanted_search import PlexUnavailable, fold, movie_sections

POSTER = "https://image.tmdb.org/t/p/w185"
RATE_LIMIT_DELAY = 0.26          # the same throttle the gap analysis uses
PAGE_SIZE = 20                   # TMDb's, and not negotiable
MAX_PAGES = 25
OWNED_CACHE_SECONDS = 600
GENRE_CACHE_SECONDS = 86400

# TMDb's film genres, in case the list cannot be fetched. These ids have been
# stable for years; the live list is still preferred because names are
# returned in the configured language.
FALLBACK_GENRES = [
    (28, "Action"), (12, "Adventure"), (16, "Animation"), (35, "Comedy"),
    (80, "Crime"), (99, "Documentary"), (18, "Drama"), (10751, "Family"),
    (14, "Fantasy"), (36, "History"), (27, "Horror"), (10402, "Music"),
    (9648, "Mystery"), (10749, "Romance"), (878, "Science Fiction"),
    (10770, "TV Movie"), (53, "Thriller"), (10752, "War"), (37, "Western"),
]

_genres = None                  # (fetched_at, [{"id", "name"}])
_owned = None                   # (built_at, index dict)


def _tmdb():
    return config.get("tmdb") or {}


def configured():
    key = str(_tmdb().get("api_key") or "")
    return bool(key) and not key.startswith("YOUR_") and key != "REPLACE_ME"


def _get(path, params=None):
    """A throttled TMDb call. Raises on anything but a good reply."""
    time.sleep(RATE_LIMIT_DELAY)
    query = {"api_key": _tmdb().get("api_key", ""),
             "language": _tmdb().get("language", "en-US")}
    query.update(params or {})
    base = _tmdb().get("base_url") or "https://api.themoviedb.org/3"
    response = requests.get(f"{base}{path}", params=query, timeout=15)
    response.raise_for_status()
    return response.json()


# ---- the genre vocabulary -------------------------------------------------

def genres():
    """TMDb's film genres, id and name, cached for a day."""
    global _genres
    if _genres and time.time() - _genres[0] < GENRE_CACHE_SECONDS:
        return _genres[1]
    found = []
    if configured():
        try:
            found = [{"id": g["id"], "name": g["name"]}
                     for g in _get("/genre/movie/list").get("genres", [])]
        except Exception:
            found = []
    if not found:
        found = [{"id": i, "name": n} for i, n in FALLBACK_GENRES]
    _genres = (time.time(), found)
    return found


def _genre_names():
    return {g["id"]: g["name"] for g in genres()}


# ---- what is already on the shelf ----------------------------------------

def _title_keys(title, year):
    """The keys a film is known by when no id matches.

    A film's year is not one thing - Plex has the regional release, TMDb the
    premiere - so a year either side is allowed. The bare title is never
    enough on its own once a year is known: Halloween, The Thing, Carrie and
    Suspiria were all remade under the same name, and owning the original does
    not make the remake any less missing.
    """
    name = fold(title)
    if not name:
        return []
    if year:
        return [(name, year + delta) for delta in (-1, 0, 1)] + [(name, None)]
    return [(name, "any")]


def owned_index(refresh=False):
    """Every film Plex lists, keyed by TMDb id, IMDb id and folded title.

    Built from a single listing per library rather than a lookup per film: a
    shopping list a hundred films long would otherwise be a hundred round
    trips. Ten minutes is long enough to page through results and short
    enough that a film added this evening is not offered again tomorrow.
    """
    global _owned
    if _owned and not refresh and time.time() - _owned[0] < OWNED_CACHE_SECONDS:
        return _owned[1]

    index = {"tmdb": set(), "imdb": set(), "titles": set(), "count": 0}
    for section in movie_sections():
        try:
            films = section.all()
        except Exception as e:
            raise PlexUnavailable(str(e))
        for film in films:
            index["count"] += 1
            for guid in (film.guids or []):
                gid = guid.id or ""
                if gid.startswith("tmdb://"):
                    index["tmdb"].add(gid[7:])
                elif gid.startswith("imdb://"):
                    index["imdb"].add(gid[7:])
            for name in (film.title, film.originalTitle):
                # Only the exact year goes in; the tolerance is applied when
                # a candidate is looked up, so it is not doubled. A film Plex
                # holds no year for is keyed as None, so it still matches a
                # dated candidate; "any" answers a candidate with no year.
                key = fold(name)
                if key:
                    index["titles"].add((key, film.year))
                    index["titles"].add((key, "any"))
    _owned = (time.time(), index)
    return index


def is_owned(index, tmdb_id, title, original_title, year):
    if str(tmdb_id) in index["tmdb"]:
        return True
    for name in (title, original_title):
        for key in _title_keys(name, year):
            if key in index["titles"]:
                return True
    return False


# ---- the search itself ----------------------------------------------------

SORTS = {
    "rating": "vote_average.desc",
    "popularity": "popularity.desc",
    "newest": "primary_release_date.desc",
}


def find_missing(db, genre_ids, match="all", min_rating=6.0, min_votes=200,
                 year_from=None, year_to=None, sort="rating", pages=5,
                 refresh=False):
    """Films TMDb rates well in these genres that Plex does not have.

    match: "all" needs every genre on the film (horror AND comedy); "any" takes
    a film carrying at least one of them.
    min_votes matters more than it looks: without a floor, the top of a
    rating-sorted list is a run of films nobody has seen, each carried to a
    perfect ten by its own crew.
    """
    if not configured():
        raise RuntimeError("no TMDb API key in config.yaml")
    ids = [str(int(g)) for g in genre_ids if str(g).strip()]
    if not ids:
        raise ValueError("pick at least one genre")
    pages = max(1, min(int(pages or 1), MAX_PAGES))
    today = datetime.date.today().isoformat()

    params = {
        "with_genres": ("|" if match == "any" else ",").join(ids),
        "vote_average.gte": float(min_rating or 0),
        "vote_count.gte": int(min_votes or 0),
        "sort_by": SORTS.get(sort, SORTS["rating"]),
        "include_adult": "false",
        "include_video": "false",
        # Nothing that is still to come out: it cannot be had, so it should not
        # be offered. The gap analysis says the same of unreleased sequels.
        "primary_release_date.lte": today,
    }
    if year_from:
        params["primary_release_date.gte"] = f"{int(year_from)}-01-01"
    if year_to:
        params["primary_release_date.lte"] = min(today, f"{int(year_to)}-12-31")

    owned = owned_index(refresh=refresh)
    names = _genre_names()

    missing, seen = [], set()
    total_results = total_pages = 0
    considered = 0
    for page in range(1, pages + 1):
        data = _get("/discover/movie", dict(params, page=page))
        total_results = data.get("total_results", 0)
        total_pages = data.get("total_pages", 0)
        for item in data.get("results", []):
            considered += 1
            tmdb_id = item.get("id")
            if not tmdb_id or tmdb_id in seen:
                continue
            seen.add(tmdb_id)
            title = (item.get("title") or "").strip()
            original = (item.get("original_title") or "").strip()
            released = (item.get("release_date") or "")[:4]
            year = int(released) if released.isdigit() else None
            if is_owned(owned, tmdb_id, title, original, year):
                continue
            wanted = db.find_wanted(title, year) if db is not None else None
            missing.append({
                "tmdb_id": tmdb_id,
                "title": title,
                "original_title": original if original != title else "",
                "year": year,
                "rating": round(float(item.get("vote_average") or 0), 1),
                "votes": int(item.get("vote_count") or 0),
                "popularity": round(float(item.get("popularity") or 0), 1),
                "genres": [names.get(g, str(g)) for g in item.get("genre_ids", [])],
                "overview": (item.get("overview") or "").strip(),
                "poster": POSTER + item["poster_path"] if item.get("poster_path") else "",
                "language": item.get("original_language", ""),
                "wanted_status": wanted["status"] if wanted else None,
            })
        if page >= total_pages:
            break

    return {
        "films": missing,
        "considered": considered,
        "owned_of_considered": considered - len(missing),
        "library_films": owned["count"],
        "total_matching": total_results,
        "pages_read": min(pages, total_pages) if total_pages else 0,
        "pages_available": min(total_pages, MAX_PAGES),
        "rating_source": "tmdb",
    }
