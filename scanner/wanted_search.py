"""Search the Plex movie libraries and capture what is missing.

A search that finds nothing is not a dead end: the title goes straight onto the
wanted list so it is not forgotten. A search that cannot reach Plex raises
PlexUnavailable - an outage must never be recorded as a missing film.
"""

import datetime
import os
import re
import unicodedata

from plexapi.server import PlexServer

from config import config
from scanner import paths

# "Chef (2014)" / "Chef [2014]" - an explicit, bracketed year.
BRACKETED_YEAR = re.compile(r"^\s*(?P<title>.+?)\s*[\(\[]\s*(?P<year>\d{4})\s*[\)\]]\s*$")
# "Chef 2014" - a bare trailing year, trusted only inside a plausible range so
# that titles like "Blade Runner 2049" keep their number.
TRAILING_YEAR = re.compile(r"^\s*(?P<title>.+?)\s+(?P<year>\d{4})\s*$")

_server = None
_as_user = {}


class PlexUnavailable(RuntimeError):
    """Plex could not be reached. Callers must treat this as unknown, not missing."""


class PlexTokenDead(PlexUnavailable):
    """This person's own Plex token is no longer valid.

    Different from Plex being down: the server is fine, this session's
    credentials are not - after a password change, a sign-out-everywhere, or
    the share being withdrawn. The only cure is signing in again, so callers
    must say so rather than reporting an outage.
    """


def get_server():
    """Connect to Plex once and reuse it."""
    global _server
    if _server is None:
        try:
            _server = PlexServer(config["plex"]["url"], config["plex"]["token"], timeout=20)
        except Exception as e:
            raise PlexUnavailable(str(e))
    return _server


def server_for(token=None):
    """The server as a given person sees it, or as the owner when none is given.

    Libraries are shared per person - someone without 4K should not be shown
    films from it, let alone be able to put one in a playlist they cannot play.
    Connecting with their own token means Plex decides that, not us.
    """
    if not token:
        return get_server()
    if token not in _as_user:
        try:
            _as_user[token] = PlexServer(config["plex"]["url"], token, timeout=20)
        except Exception as e:
            # A 401 means the token is finished, not that Plex is poorly.
            if "401" in str(e) or "unauthorized" in str(e).lower():
                raise PlexTokenDead("that Plex sign-in has expired")
            raise PlexUnavailable(str(e))
    return _as_user[token]


def show_sections(token=None):
    """Every TV library visible to this person."""
    try:
        return [s for s in server_for(token).library.sections() if s.type == "show"]
    except PlexUnavailable:
        raise
    except Exception as e:
        raise PlexUnavailable(str(e))


def search_shows(title, token=None):
    """Find TV series by name.

    A series is returned as one result carrying its own counts, never as a heap
    of episodes: someone asking for Blackadder wants to know whether it is here
    and how much of it, not to scroll past twenty-four separate rows.
    """
    query = (title or "").strip()
    if not query:
        return []

    found = {}
    for section in show_sections(token):
        try:
            results = section.search(title=query)
        except Exception as e:
            raise PlexUnavailable(str(e))
        for item in results:
            confidence = title_confidence(query, item.title or "")
            if not confidence:
                continue
            key = ((item.title or "").strip().lower(), item.year)
            existing = found.get(key)
            if existing:
                if section.title not in existing["libraries"]:
                    existing["libraries"].append(section.title)
                continue
            found[key] = {
                "title": (item.title or "").strip(),
                "year": item.year,
                "rating_key": str(item.ratingKey),
                "thumb": item.thumb,
                "art": getattr(item, "art", None),
                "summary": (item.summary or "").strip(),
                "seasons": getattr(item, "childCount", None),
                "episodes": getattr(item, "leafCount", None),
                "watched_episodes": getattr(item, "viewedLeafCount", 0),
                "libraries": [section.title],
                "confidence": confidence,
                "kind": "show",
            }
    ordered = sorted(found.values(), key=lambda m: (-m["confidence"], m["title"].lower()))
    return ordered


def seasons_held(rating_key, token=None):
    """Which season numbers of a series are actually here."""
    try:
        show = server_for(token).fetchItem(int(rating_key))
        return sorted(
            s.seasonNumber for s in show.seasons()
            if getattr(s, "seasonNumber", None) is not None
        )
    except Exception:
        return []


def movie_sections(token=None):
    """Every movie library visible to this person."""
    try:
        return [s for s in server_for(token).library.sections() if s.type == "movie"]
    except PlexUnavailable:
        raise
    except Exception as e:
        raise PlexUnavailable(str(e))


def plausible_year(year):
    return 1888 <= year <= datetime.date.today().year + 5


QUALIFIER = re.compile(r"\s*[\(\[]\s*(?!(?:19|20)\d{2}\s*[\)\]])[^\)\]]{1,24}[\)\]]\s*")


def query_hint(query):
    """The parenthesised aside, if there was one: "French", "original", "cartoon"."""
    found = QUALIFIER.search(query or "")
    return found.group(0).strip(" ()[]") if found else ""


def parse_query(query):
    """Split a typed query into a title and, if present, a year.

    A parenthesised aside that is not a year - "Taxi (French)", "Alien
    (original)" - is how people qualify a half-remembered title. It is not part
    of the name, so it is dropped before searching and kept as a hint.
    """
    raw = QUALIFIER.sub(" ", (query or "")).strip()
    if not raw:
        return "", None

    match = BRACKETED_YEAR.match(raw)
    if match:
        year = int(match.group("year"))
        title = match.group("title").strip()
        if title:
            return title, (year if plausible_year(year) else None)

    match = TRAILING_YEAR.match(raw)
    if match:
        year = int(match.group("year"))
        title = match.group("title").strip()
        if title and plausible_year(year):
            return title, year

    return raw, None


def part_files(item):
    """Every file backing a Plex item, across all its copies."""
    files = []
    try:
        for media in item.media or []:
            for part in media.parts or []:
                if part.file:
                    files.append(part.file)
    except Exception:
        pass
    return files


def availability(item):
    """Which of a film's files are actually on disk.

    Plex keeps an item in the library after its file disappears - a deleted
    download, an unmounted drive - so "Plex has it" and "you can watch it" are
    different questions. A film whose every copy is gone is, in practice,
    missing - UNLESS the store it lives on is offline, in which case it is
    simply unknown, and saying so beats declaring it gone.
    """
    # Judge each copy at the path this app can actually reach, not the one
    # Plex happens to name.
    files = [paths.readable_path(f) for f in part_files(item)]
    offline = [f for f in files if paths.store_offline(f)]
    # Only files whose store is actually readable can be judged; a file on an
    # offline store is unknown, never missing.
    checkable = [f for f in files if f not in offline]
    missing = [f for f in checkable if not os.path.exists(f)]
    return {
        "files": len(files),
        "missing_files": missing,
        "store_offline": bool(offline),
        # A real absence only when every copy was reachable enough to check and
        # every one was gone. A copy on an offline store cannot be ruled fine
        # or otherwise, so a deleted copy beside an offline one is not
        # "all missing".
        "all_missing": not offline and bool(checkable)
                       and len(missing) == len(checkable),
        "some_missing": bool(missing) and len(missing) < len(files),
    }


def fold(value):
    """Compare titles the way people do: ignoring case, accents and punctuation."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", stripped)).strip().lower()


def _bare(value):
    """Lower-case and strip accents, but keep punctuation - the boundary matters."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


# A subtitle usually means the same film - "Anchorman: The Legend of Ron
# Burgundy" is Anchorman. These subtitles mean the opposite: a piece ABOUT the
# film, which is not the film. Searching "The Odyssey" and being told "yes, we
# have that" on the strength of "The Odyssey: The Making of an Epic" is how
# somebody ends up never asking for the film they actually wanted.
COMPANION = re.compile(
    r"^(the\s+)?("
    r"making of|behind the scenes|a look (inside|back|at)|inside\b|"
    r"featurette|bonus|special features?|extras?|deleted scenes|bloopers?|"
    r"gag reel|outtakes?|sneak peek|preview|trailer|teaser|"
    r"companion|anatomy of|story of|legacy of|documentary|retrospective|"
    r"cast and crew|commentary"
    r")\b", re.I)


def title_confidence(query, title):
    """How sure are we that this result is the film they named?

    Plex matches word prefixes, so a hit has to be checked rather than trusted.
    What separates a subtitle from a different film is the punctuation after the
    match: "Anchorman: The Legend of Ron Burgundy" is Anchorman, but "Taxi
    Driver" is not Taxi - one continues with a colon, the other with another
    word of the actual name.

    2 - it is that film
    1 - it might be; worth offering, not worth claiming
    0 - a prefix collision
    """
    q, t = fold(query), fold(title)
    if not q or not t:
        return 0
    if q == t:
        return 2

    raw_q, raw_t = _bare(query), _bare(title)
    if raw_t.startswith(raw_q):
        rest = raw_t[len(raw_q):].lstrip()
        if not rest or rest[0] in ":-–—,([":
            # A companion piece is worth offering, never worth claiming.
            if COMPANION.match(rest.lstrip(":-–—,([ ").strip()):
                return 1
            return 2          # a subtitle follows: same film
        return 1              # another word of a different name follows
    if (len(q) >= 6 or " " in q) and re.search(rf"\b{re.escape(q)}\b", t):
        return 1
    return 0


def search_plex(title, token=None):
    """Search every movie library for a title.

    Plex matches on word boundaries, case- and accent-insensitively, so
    "anchorman" finds "Anchorman: The Legend of Ron Burgundy" and "amelie"
    finds "Amelie". A film held in two libraries is returned once, listing both.
    """
    query = (title or "").strip()
    if not query:
        return []

    collected = {}
    for section in movie_sections(token):
        try:
            results = section.search(title=query)
        except Exception as e:
            raise PlexUnavailable(str(e))
        for item in results:
            key = ((item.title or "").strip().lower(), item.year)
            if key in collected:
                if section.title not in collected[key]["libraries"]:
                    collected[key]["libraries"].append(section.title)
                continue
            state = availability(item)
            collected[key] = {
                "title": (item.title or "").strip(),
                "year": item.year,
                "rating_key": str(item.ratingKey),
                "thumb": item.thumb,
                "summary": (item.summary or "").strip(),
                "content_rating": item.contentRating or "",
                "duration_ms": item.duration,
                "libraries": [section.title],
                "file_missing": state["all_missing"],
                "missing_files": state["missing_files"],
                "added_at": int(item.addedAt.timestamp()) if getattr(item, "addedAt", None) else 0,
            }
    scored = []
    for movie in collected.values():
        confidence = title_confidence(query, movie["title"])
        if confidence:
            movie["confidence"] = confidence
            scored.append((confidence, movie))
    scored.sort(key=lambda row: (-row[0], row[1]["title"].lower(), row[1]["year"] or 0))
    return [movie for _confidence, movie in scored]


def narrow_by_year(matches, year):
    """When the user gave a year and something matches it exactly, trust it."""
    if not year:
        return matches
    exact = [m for m in matches if m["year"] == year]
    return exact or matches


def search_and_capture(db, query, capture=True):
    """Search Plex; add the title to the wanted list when nothing matches."""
    title, year = parse_query(query)
    result = {
        "query": (query or "").strip(),
        "title": title,
        "year": year,
        "found": False,
        "matches": [],
        "added_to_wanted": False,
        "already_wanted": False,
        "wanted": None,
    }
    if not title:
        return result

    matches = narrow_by_year(search_plex(title), year)
    result["matches"] = matches
    result["found"] = bool(matches)

    # Plex still lists a film after its file disappears. If every copy of every
    # match is gone, the library cannot actually play it, so it belongs on the
    # wanted list even though the search "found" something.
    playable = [m for m in matches if not m["file_missing"]]
    result["file_missing"] = bool(matches) and not playable
    if playable or not capture:
        return result
    if matches:
        gone = matches[0]
        note = "Plex lists this but the file is missing from disk"
        row, created = db.insert_wanted(
            gone["title"], gone["year"], (query or "").strip(), notes=note
        )
        result["added_to_wanted"] = created
        result["already_wanted"] = not created
        result["wanted"] = dict(row)
        return result

    row, created = db.insert_wanted(title, year, (query or "").strip())
    result["added_to_wanted"] = created
    result["already_wanted"] = not created
    result["wanted"] = dict(row)
    return result


def recheck(db):
    """Re-search every wanted entry and report the ones Plex now has.

    Nothing is reclassified automatically - marking something acquired stays a
    deliberate, manual step.
    """
    now_in_plex = []
    entries = db.get_wanted("wanted")
    for entry in entries:
        matches = narrow_by_year(search_plex(entry["title"]), entry["year"])
        # Plex keeps listing a title after its file is gone - which is often
        # exactly why the title is on this list. Only a copy that can actually
        # be played means it has arrived; otherwise announcing "it's here" is a
        # lie, and it would flip straight back on the next check.
        playable = [m for m in matches if not m["file_missing"]]
        if playable:
            found = dict(entry)
            found["matches"] = playable
            now_in_plex.append(found)
    return {"checked": len(entries), "now_in_plex": now_in_plex}


def export_line(title, year):
    return f"{title} ({year})" if year else title


def export_text(db):
    """Plain text, one `Title (Year)` per line, for copy-paste when acquiring."""
    lines = [export_line(row["title"], row["year"]) for row in db.get_wanted("wanted")]
    return "\n".join(lines) + ("\n" if lines else "")
