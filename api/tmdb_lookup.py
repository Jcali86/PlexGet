"""Identify a film the library does not have, so the request names a real one.

Someone half-remembers a title - "Taxi, the French one" - and asking for that
string puts a vague entry on the wanted list that nobody can act on later.
Looking it up first turns it into "Taxi (1998)", which can be found and bought.

Only used when the library has no match, and only ever to name a film. Nothing
here fetches anything.
"""

import datetime
import re
import time

import requests
from thefuzz import fuzz

from config import config

CACHE_SECONDS = 3600
POSTER = "https://image.tmdb.org/t/p/w185"
_cache = {}

# "the French one" is a real clue about which film is meant.
LANGUAGE_HINTS = {
    "french": "fr", "france": "fr", "japanese": "ja", "japan": "ja", "anime": "ja",
    "korean": "ko", "korea": "ko", "spanish": "es", "italian": "it", "german": "de",
    "swedish": "sv", "danish": "da", "norwegian": "no", "chinese": "zh",
    "hindi": "hi", "bollywood": "hi", "russian": "ru", "portuguese": "pt",
}


def configured():
    key = str((config.get("tmdb") or {}).get("api_key", ""))
    return bool(key) and not key.startswith("YOUR_")


def _language_from(hint):
    words = re.findall(r"[a-z]+", (hint or "").lower())
    for word in words:
        if word in LANGUAGE_HINTS:
            return LANGUAGE_HINTS[word]
    return ""


# TMDb release types. Only the last two put a film somewhere it can be got.
THEATRICAL, DIGITAL, PHYSICAL = 3, 4, 5


def availability(tmdb_id):
    """Whether a film can actually be had yet, and what to say if not.

    A film still in the cinema cannot be added to anybody's shelf, so
    offering to put it on the wanted list is a promise nobody can keep. TMDb
    knows the difference: a digital or physical release date in the past means
    it exists to be had; only a theatrical one means it is still on at the
    pictures.

    Returns (state, when) where state is 'out', 'cinema' or 'unreleased'.
    """
    if not configured() or not tmdb_id:
        return "out", None
    cache_key = ("avail", tmdb_id)
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]

    today = datetime.date.today()
    try:
        r = requests.get(
            f"{config['tmdb']['base_url']}/movie/{tmdb_id}/release_dates",
            params={"api_key": config["tmdb"]["api_key"]}, timeout=12,
        )
        r.raise_for_status()
        countries = r.json().get("results", [])
    except Exception:
        return "out", None      # never block a request on TMDb being awkward

    earliest = {}
    for country in countries:
        for entry in country.get("release_dates", []):
            kind, when = entry.get("type"), (entry.get("release_date") or "")[:10]
            if not when:
                continue
            if kind not in earliest or when < earliest[kind]:
                earliest[kind] = when

    def gone(kind):
        when = earliest.get(kind)
        return bool(when) and when <= today.isoformat()

    if gone(DIGITAL) or gone(PHYSICAL):
        state = ("out", None)
    elif gone(THEATRICAL):
        state = ("cinema", earliest.get(DIGITAL))
    elif earliest:
        state = ("unreleased", earliest.get(THEATRICAL) or earliest.get(DIGITAL))
    else:
        # TMDb holds no release dates at all - common for older or obscure
        # films. Unknown is not the same as unreleased, and refusing to let
        # somebody ask for a 1982 TV film because a database is thin would be
        # far more annoying than the thing this guard exists to prevent.
        state = ("out", None)

    _cache[cache_key] = (time.time(), state)
    return state


def suggest(title, hint="", year=None, limit=5):
    """Films this might be, best guess first."""
    if not configured() or not (title or "").strip():
        return []

    cache_key = (title.strip().lower(), (hint or "").lower(), year)
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]

    params = {
        "api_key": config["tmdb"]["api_key"],
        "query": title.strip(),
        "language": config["tmdb"].get("language", "en-US"),
        "include_adult": "false",
    }
    if year:
        params["year"] = year
    try:
        response = requests.get(
            f"{config['tmdb']['base_url']}/search/movie", params=params, timeout=12
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []

    wanted_language = _language_from(hint)
    found = []
    for item in results:
        name = (item.get("title") or "").strip()
        if not name:
            continue
        released = (item.get("release_date") or "")[:4]
        found.append({
            "tmdb_id": item.get("id"),
            "title": name,
            "year": int(released) if released.isdigit() else None,
            "overview": (item.get("overview") or "").strip(),
            "poster": POSTER + item["poster_path"] if item.get("poster_path") else "",
            "language": item.get("original_language", ""),
            "popularity": item.get("popularity", 0),
            "original_title": (item.get("original_title") or "").strip(),
        })

    def rank(film):
        # A language clue outranks raw popularity: asking for "Taxi (French)"
        # means the French one, however famous the others are.
        matches_language = wanted_language and film["language"] == wanted_language
        exact_name = film["title"].strip().lower() == title.strip().lower()
        return (not matches_language, not exact_name, -film["popularity"])

    found.sort(key=rank)
    found = found[:limit]
    _cache[cache_key] = (time.time(), found)
    return found


def suggest_shows(title, hint="", limit=5):
    """Series this might be, best guess first."""
    if not configured() or not (title or "").strip():
        return []
    cache_key = ("tv", title.strip().lower(), (hint or "").lower())
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]

    try:
        response = requests.get(
            f"{config['tmdb']['base_url']}/search/tv",
            params={"api_key": config["tmdb"]["api_key"], "query": title.strip(),
                    "language": config["tmdb"].get("language", "en-US")},
            timeout=12,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []

    wanted_language = _language_from(hint)
    found = []
    for item in results:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        started = (item.get("first_air_date") or "")[:4]
        found.append({
            "title": name,
            "year": int(started) if started.isdigit() else None,
            "overview": (item.get("overview") or "").strip(),
            "poster": POSTER + item["poster_path"] if item.get("poster_path") else "",
            "language": item.get("original_language", ""),
            "popularity": item.get("popularity", 0),
            "tmdb_id": item.get("id"),
        })
    found.sort(key=lambda f: (
        not (wanted_language and f["language"] == wanted_language),
        f["title"].strip().lower() != title.strip().lower(),
        -f["popularity"],
    ))
    found = found[:limit]
    _cache[cache_key] = (time.time(), found)
    return found


def seasons_that_exist(title, year=None):
    """Which seasons of a series exist in the world, per TMDb.

    Used to answer "you have series one; two and three also exist" - which is
    the difference between a library that looks complete and one that is.
    """
    if not configured():
        return []
    cache_key = ("seasons", (title or "").strip().lower(), year)
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]

    shows = suggest_shows(title)
    if not shows:
        return []
    best = shows[0]
    if year and any(s["year"] == year for s in shows):
        best = next(s for s in shows if s["year"] == year)

    try:
        response = requests.get(
            f"{config['tmdb']['base_url']}/tv/{best['tmdb_id']}",
            params={"api_key": config["tmdb"]["api_key"],
                    "language": config["tmdb"].get("language", "en-US")},
            timeout=12,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []

    seasons = []
    for season in data.get("seasons", []):
        number = season.get("season_number")
        if number is None or number == 0:      # specials are not a series
            continue
        aired = (season.get("air_date") or "")[:4]
        seasons.append({
            "season": number,
            "episodes": season.get("episode_count", 0),
            "year": int(aired) if aired.isdigit() else None,
            "name": (season.get("name") or "").strip(),
        })
    seasons.sort(key=lambda s: s["season"])
    _cache[cache_key] = (time.time(), seasons)
    return seasons


_GENRE_IDS = {}


def genre_ids():
    """TMDb's own genre numbering, fetched once."""
    global _GENRE_IDS
    if _GENRE_IDS or not configured():
        return _GENRE_IDS
    try:
        response = requests.get(
            f"{config['tmdb']['base_url']}/genre/movie/list",
            params={"api_key": config["tmdb"]["api_key"],
                    "language": config["tmdb"].get("language", "en-US")},
            timeout=12,
        )
        response.raise_for_status()
        _GENRE_IDS = {g["name"].strip().lower(): g["id"] for g in response.json().get("genres", [])}
    except Exception:
        return {}
    return _GENRE_IDS


def discover(filters, exclude_titles=(), limit=8):
    """Films matching the same brief that the library does not have.

    Drawn from TMDb rather than invented: everything returned is a real film
    with a real year, so what lands on the wanted list can be gone and found.
    """
    if not configured():
        return []

    ids = genre_ids()
    wanted = [ids[g.strip().lower()] for g in (filters.get("genres") or []) if g.strip().lower() in ids]
    country = (filters.get("origin_country") or "").strip().upper()

    def year_of(key):
        try:
            return int(filters.get(key))
        except (TypeError, ValueError):
            return None

    year_from, year_to = year_of("year_from"), year_of("year_to")

    # A genre is not the only brief there is. "New films that are not in plex
    # yet" names a year and nothing else, and returning empty without one
    # answered exactly that request with nothing at all. Anything that narrows
    # the field will do; only a request that narrows nothing is refused, since
    # popularity alone would just list whatever is out this week.
    if not (wanted or country or year_from or year_to or filters.get("min_rating")):
        return []

    params = {
        "api_key": config["tmdb"]["api_key"],
        "language": config["tmdb"].get("language", "en-US"),
        "sort_by": "popularity.desc",
        "include_adult": "false",
        "page": 1,
    }
    if country:
        params["with_origin_country"] = country
    if filters.get("min_rating"):
        params["vote_average.gte"] = filters["min_rating"]
    if year_from:
        params["primary_release_date.gte"] = f"{year_from}-01-01"
    if year_to:
        params["primary_release_date.lte"] = f"{year_to}-12-31"
    if filters.get("max_runtime_minutes"):
        params["with_runtime.lte"] = filters["max_runtime_minutes"]
    if filters.get("min_runtime_minutes"):
        params["with_runtime.gte"] = filters["min_runtime_minutes"]

    # Votes are how TMDb says somebody has actually seen a thing, and two
    # hundred of them is a fair bar for a film that has been out for years. It
    # is the wrong bar for one released last month - which is the only kind
    # "new films we haven't got" is asking about - so when the request reaches
    # into the last couple of years, the bar comes down with it.
    this_year = datetime.date.today().year
    params["vote_count.gte"] = 20 if (year_from and year_from >= this_year - 2) else 200

    def ask(join):
        if wanted:
            params["with_genres"] = join.join(str(i) for i in wanted)
        try:
            response = requests.get(
                f"{config['tmdb']['base_url']}/discover/movie", params=params, timeout=15
            )
            response.raise_for_status()
            return response.json().get("results", [])
        except Exception:
            return []

    # A comma is AND, which is what "romantic comedy" means and why it stays
    # the first thing tried. Three or more genres AND-ed together reliably
    # returns nothing, though, and somebody listing that many is describing a
    # range rather than an intersection - so an empty answer is asked again as
    # an OR before it is believed.
    results = ask(",")
    if not results and len(wanted) > 1:
        results = ask("|")

    def flatten(text):
        """Compare titles the way a person would, so "Se7en" is not a new film."""
        swapped = (text or "").lower()
        for digit, letter in (("7", "v"), ("0", "o"), ("1", "i"), ("3", "e"), ("4", "a"), ("5", "s")):
            swapped = swapped.replace(digit, letter)
        return re.sub(r"[^a-z]", "", swapped)

    already = {flatten(t) for t in exclude_titles if t}
    found = []
    for item in results:
        name = (item.get("title") or "").strip()
        if not name:
            continue
        flat = flatten(name)
        if flat in already:
            continue
        # And catch the near misses: stylised spellings, articles, punctuation.
        if any(fuzz.ratio(flat, seen) >= 88 for seen in already):
            continue
        released = (item.get("release_date") or "")[:4]
        found.append({
            "title": name,
            "year": int(released) if released.isdigit() else None,
            "overview": (item.get("overview") or "").strip(),
            "poster": POSTER + item["poster_path"] if item.get("poster_path") else "",
            "rating": round(item.get("vote_average", 0), 1),
        })
        if len(found) >= limit:
            break
    return found


# ---- what the library has not got ------------------------------------------

# TMDb numbers television genres separately from films, and does not use the
# same names: there is no Thriller on television, Action and Adventure are one
# genre, and so are Sci-Fi and Fantasy. The model chooses from the library's
# own genre names, so those have to be translated on the way out. One name may
# open several doors, which is why the values are lists.
_TV_ALIASES = {
    "action": ["action & adventure"],
    "adventure": ["action & adventure"],
    "science fiction": ["sci-fi & fantasy"],
    "sci-fi": ["sci-fi & fantasy"],
    "fantasy": ["sci-fi & fantasy"],
    "war": ["war & politics"],
    # Television has no Thriller or Suspense of its own; what people mean by it
    # lives under Mystery and Crime, so ask for both rather than nothing.
    "thriller": ["mystery", "crime"],
    "suspense": ["mystery", "crime"],
    "horror": ["mystery"],
    "music": ["reality"],
    "musical": ["reality"],
    "history": ["documentary"],
}

_TV_GENRE_IDS = {}


def show_genre_ids():
    """TMDb's television genre numbering, fetched once."""
    global _TV_GENRE_IDS
    if _TV_GENRE_IDS or not configured():
        return _TV_GENRE_IDS
    try:
        response = requests.get(
            f"{config['tmdb']['base_url']}/genre/tv/list",
            params={"api_key": config["tmdb"]["api_key"],
                    "language": config["tmdb"].get("language", "en-US")},
            timeout=12,
        )
        response.raise_for_status()
        _TV_GENRE_IDS = {g["name"].strip().lower(): g["id"] for g in response.json().get("genres", [])}
    except Exception:
        return {}
    return _TV_GENRE_IDS


def _tv_genre_numbers(names):
    """Library genre names as TMDb television genre ids, aliases applied."""
    table = show_genre_ids()
    if not table:
        return []
    numbers = []
    for name in names or []:
        key = (name or "").strip().lower()
        for candidate in _TV_ALIASES.get(key, [key]):
            number = table.get(candidate)
            if number and number not in numbers:
                numbers.append(number)
    return numbers


def discover_shows(filters, exclude_titles=(), limit=8):
    """Series matching the brief that the library does not have.

    The television half of discover(), and the same bargain: everything here is
    a real series with a real year, so what lands on the wanted list is
    something somebody can actually go and find.

    Genres are OR-ed rather than AND-ed. "From animated to thrillers" is a
    range somebody is describing, not a series that must be all three at once -
    and asking TMDb for the intersection of three genres reliably returns
    nothing, which reads on the page as "there is none of that in the world".
    """
    if not configured():
        return []

    wanted = _tv_genre_numbers(filters.get("genres"))
    country = (filters.get("origin_country") or "").strip().upper()
    # With neither a genre nor a country there is no brief to discover against,
    # and popularity.desc alone would just return whatever is on television
    # this week.
    if not wanted and not country:
        return []

    params = {
        "api_key": config["tmdb"]["api_key"],
        "language": config["tmdb"].get("language", "en-US"),
        "sort_by": "popularity.desc",
        # Television carries far fewer votes than film, so the film threshold
        # of 200 would throw away most of what anybody means by a good series.
        "vote_count.gte": 40,
        "include_adult": "false",
        "page": 1,
    }
    if wanted:
        params["with_genres"] = "|".join(str(i) for i in wanted)
    if country:
        params["with_origin_country"] = country
    if filters.get("min_rating"):
        params["vote_average.gte"] = filters["min_rating"]
    if filters.get("year_from"):
        params["first_air_date.gte"] = f"{filters['year_from']}-01-01"
    if filters.get("year_to"):
        params["first_air_date.lte"] = f"{filters['year_to']}-12-31"

    try:
        response = requests.get(
            f"{config['tmdb']['base_url']}/discover/tv", params=params, timeout=15
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except Exception:
        return []

    def flatten(text):
        return re.sub(r"[^a-z]", "", (text or "").lower())

    already = {flatten(t) for t in exclude_titles if t}
    already.discard("")
    found = []
    for item in results:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        flat = flatten(name)
        if not flat or flat in already:
            continue
        if any(fuzz.ratio(flat, seen) >= 88 for seen in already):
            continue
        first = (item.get("first_air_date") or "")[:4]
        found.append({
            "title": name,
            "year": int(first) if first.isdigit() else None,
            "overview": (item.get("overview") or "").strip(),
            "poster": POSTER + item["poster_path"] if item.get("poster_path") else "",
            "rating": round(item.get("vote_average", 0), 1),
            "kind": "show",
        })
        already.add(flat)
        if len(found) >= limit:
            break
    return found
