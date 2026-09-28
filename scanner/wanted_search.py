"""Search the Plex movie libraries and capture what is missing.

A search that finds nothing is not a dead end: the title goes straight onto the
wanted list so it is not forgotten. A search that cannot reach Plex raises
PlexUnavailable - an outage must never be recorded as a missing film.
"""

import datetime
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from plexapi.server import PlexServer

from config import config
from scanner import paths

# "Chef (2014)" / "Chef [2014]" - an explicit, bracketed year - and "Resident
# Evil (2026 film)", the way Wikipedia tells apart films of one name. A few
# words after the brackets describe the copy rather than name the film -
# "Resident Evil (2026) 4K" - so they are let go rather than costing the year.
BRACKETED_YEAR = re.compile(
    r"^\s*(?P<title>.+?)\s*[\(\[]\s*(?P<year>\d{4})(?:\s+(?P<film>film|movie))?\s*[\)\]]"
    r"(?:\s+[^\(\)\[\]]{1,24})?\s*$", re.I)
# "Chef 2014" - a bare trailing year, trusted only inside a plausible range so
# that titles like "Blade Runner 2049" keep their number. Inside that range it
# is still a guess - Wonder Woman 1984 is a name, not a year - which is why
# shelf_reading asks the shelf both ways.
TRAILING_YEAR = re.compile(r"^\s*(?P<title>.+?)\s+(?P<year>\d{4})\s*$")
# What parted a name from its year, "Resident Evil - 2026" or "Resident Evil,
# 2026", is not part of the name; kept, it made a wanted row of its own.
DANGLING = re.compile(r"(?:\s+[-–—]+|\s*,)\s*$")

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

    The library index is asked first which libraries could hold it at all. A
    name it has never seen is answered without asking Plex anything; one it
    has seen is searched for only in the libraries that hold it.
    """
    query = (title or "").strip()
    if not query:
        return []

    found = {}
    for section, results in _search_with_index(query, "show", show_sections(token)):
        for item in results:
            confidence = best_confidence(query, item.title, getattr(item, "originalTitle", None))
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
                "added_at": int(item.addedAt.timestamp()) if getattr(item, "addedAt", None) else 0,
            }
    ordered = sorted(found.values(), key=lambda m: (-m["confidence"], m["title"].lower()))
    return ordered


def _search_with_index(query, kind, sections):
    """(section, results) for a title search, asking Plex only where it can help.

    The library index says which libraries hold anything by that name; the
    rest are not asked. What the index finds is then confirmed with the
    person's own token, because Plex decides who may see what. Usually the
    search by the typed words confirms it, but Plex matches those words more
    literally than people type them - "oceans eleven" is not a word of "Ocean's
    Eleven" to Plex - so anything the index found that the typed words did not
    is looked up by its own title, which Plex always matches.
    """
    from scanner import library_index
    held = library_index.lookup(query, kind, sections)
    if held is None:
        return _search_sections(sections, query)
    holding = {key for key, _rating_key, _title in held}
    answered = _search_sections([s for s in sections if str(s.key) in holding], query)
    seen = {str(item.ratingKey) for _section, items in answered for item in items}
    missed = {}
    for key, rating_key, title in held:
        if rating_key not in seen:
            missed.setdefault((key, title), set()).add(rating_key)
    if not missed:
        return answered
    by_key = {str(s.key): s for s in sections}

    def by_own_title(job):
        (key, title), wanted = job
        section = by_key[key]
        _section, items = _search_sections([section], title)[0]
        return section, [item for item in items if str(item.ratingKey) in wanted]

    jobs = list(missed.items())[:5]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        extra = [pair for pair in pool.map(by_own_title, jobs) if pair[1]]
    return answered + extra


def _search_sections(sections, query):
    """The same title search in several libraries at once, answered in library order.

    Each library is its own request to Plex; asking them one after another made
    a search as slow as all of them added together.
    """
    def one(section):
        try:
            return section, listed(section.search(title=query))
        except Exception as e:
            raise PlexUnavailable(str(e))

    if len(sections) <= 1:
        return [one(section) for section in sections]
    with ThreadPoolExecutor(max_workers=min(len(sections), 8)) as pool:
        return list(pool.map(one, sections))


def seasons_held(rating_key, token=None):
    """Which season numbers of a series are actually here.

    Only ever asked about a series the person's own search has already shown
    them, so the index's list is theirs too: a share covers a whole series, not
    some of its seasons.
    """
    from scanner import library_index
    known = library_index.seasons_of(rating_key)
    if known is not None:
        return known
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


# An aside comes after the name it qualifies, never first. A name can open
# with brackets of its own, and taking them for an aside cost "(500) Days of
# Summer" its number and "[REC]" the whole of its name.
QUALIFIER = re.compile(
    r"(?<=\S)\s*[\(\[]\s*(?!(?:19|20)\d{2}(?:\s+(?:film|movie))?\s*[\)\]])[^\)\]]{1,24}[\)\]]\s*",
    re.I)


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
    title, year, _source = split_query(query)
    return title, year


def split_query(query):
    """parse_query, and where the year came from: "bracketed", "film", "trailing" or None.

    "film" is the Wikipedia form, "Resident Evil (2026 film)". Only a bare
    trailing year is a guess, and a caller searching the shelf should try it
    both ways - see shelf_reading.
    """
    raw = QUALIFIER.sub(" ", (query or "")).strip()
    if not raw:
        return "", None, None

    match = BRACKETED_YEAR.match(raw)
    if match:
        year = int(match.group("year"))
        title = DANGLING.sub("", match.group("title").strip())
        if title:
            if not plausible_year(year):
                return title, None, None
            return title, year, ("film" if match.group("film") else "bracketed")

    match = TRAILING_YEAR.match(raw)
    if match:
        year = int(match.group("year"))
        title = DANGLING.sub("", match.group("title").strip())
        if title and plausible_year(year):
            return title, year, "trailing"

    return DANGLING.sub("", raw), None, None


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

    The library index checks every file in the background, so a search asks it
    first and only looks at the disk itself when the index has no fresh answer
    for exactly these files. Looking at the disk from inside a search is what
    let one wedged network share hold a page open for as long as the share
    stayed wedged.
    """
    files = part_files(item)
    from scanner import library_index
    known = library_index.file_state(getattr(item, "ratingKey", None), files)
    if known is not None:
        return known
    return availability_of(files)


def _unknown(files):
    """The verdict for files that could not be looked at: unknown, never missing."""
    return {"files": len(files), "missing_files": [], "store_offline": bool(files),
            "all_missing": False, "some_missing": False}


# After a disk check has run out of time, others are not attempted for a while.
# A wedged mount stays wedged, and every search waiting its turn behind it
# would otherwise pay the full timeout again, once per film.
_wedged_until = 0.0


def availability_of(files):
    """Check these files on disk now, giving up after the probe timeout.

    The store each file lives on is asked first, through the guarded probe in
    scanner.paths, so a drive that has gone away is found out without touching
    any file on it. The files themselves are then looked at on a thread this is
    prepared to abandon, because a file on no configured store is looked at
    directly, and a hung network mount does not refuse - it hangs.
    """
    global _wedged_until
    files = list(files or [])
    if not files:
        return files_state(files)
    if time.time() < _wedged_until:
        return _unknown(files)
    outcome = []

    def check():
        try:
            outcome.append(files_state(files))
        except Exception:
            # A fault is not a hang: answer unknown without holding back the
            # checks that come after.
            outcome.append(_unknown(files))

    worker = threading.Thread(target=check, daemon=True)
    worker.start()
    worker.join(paths.probe_timeout())
    if outcome:
        return outcome[0]
    _wedged_until = time.time() + paths.probe_cache()
    return _unknown(files)


def files_state(files):
    """Which of these files are on disk, judged the way availability() judges.

    Unguarded: the library index runs this on its own thread, where a slow
    answer holds up nobody. Everything on the request path goes through
    availability_of() instead.
    """
    offline, checkable = [], []
    for path in files:
        names = paths.store_for(path)
        if names and not any(paths.store_ready(name) for name in names):
            offline.append(path)
            continue
        # Judge each copy at the path this app can actually reach, not the one
        # Plex happens to name.
        checkable.append(paths.readable_path(path))
    missing = [path for path in checkable if not os.path.exists(path)]
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


def listed(items):
    """Search results as Plex listed them, never quietly fetched again one by one.

    plexapi treats a search result as a partial object: reading any field that
    came back empty - an unrated film's contentRating, a missing audienceRating
    or originalTitle - makes it fetch that whole film from Plex again, one
    request per film, inside the search. The listing already carries every
    field these pages show, so an empty field is simply empty.
    """
    for item in items or []:
        try:
            item._autoReload = False
        except AttributeError:
            pass
    return items


_APOSTROPHES = re.compile("['`\u2018\u2019\u02bc]")


def fold(value):
    """Compare titles the way people do: ignoring case, accents and punctuation.

    An apostrophe joins rather than splits, because nobody types one: split,
    "oceans eleven" was never Ocean's Eleven and "dont look up" was never Don't
    Look Up. An ampersand is the word it stands for.
    """
    decomposed = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    stripped = _APOSTROPHES.sub("", stripped).replace("&", " and ")
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


# "Matrix" is The Matrix. Measured against the full name it only ever came out
# as a maybe, and a maybe is never claimed, so naming a film without its
# article sent the search down the slow path meant for films the library does
# not have.
_ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)


def best_confidence(query, *names):
    """title_confidence against every name a film goes by, the best of them.

    Its title, its title without a leading article, and its original title - so
    "matrix" finds The Matrix and "le fabuleux destin d'amelie poulain" finds
    Amelie. The rules for each comparison are title_confidence's own.
    """
    best = 0
    for name in names:
        name = (name or "").strip()
        if not name:
            continue
        best = max(best, title_confidence(query, name))
        bare = without_article(name)
        if bare != name:
            best = max(best, title_confidence(query, bare))
        if best == 2:
            break
    return best


def without_article(name):
    """A name without a leading "The", "A" or "An"."""
    return _ARTICLE.sub("", (name or "").strip())


def named_exactly(name, matches):
    """Is any of these called exactly this, rather than merely starting with it?"""
    wanted = fold(without_article(name))
    return any(fold(without_article(m.get("title"))) == wanted for m in matches)


def shelf_reading(query, *lookups):
    """(title, year, found): what to look for on the shelf, a bare trailing year read both ways.

    "Chef 2014" is Chef from 2014, but Wonder Woman 1984 is the whole of a
    film's name, and so are Death Race 2000 and Class of 1984. Read as a year,
    the number found the 2017 Wonder Woman instead, or nothing, and a film on
    the shelf looked missing. The words cannot tell the two apart; the shelf
    can. Each lookup is asked for the whole of it, and if anything is called
    exactly that, the number was part of the name. found is then what each
    lookup returned, so the caller need not ask again; otherwise it is None and
    the year stands.
    """
    title, year, source = split_query(query)
    if source == "trailing":
        whole = f"{title} {year}"
        found = [lookup(whole) for lookup in lookups]
        if any(named_exactly(whole, matches) for matches in found):
            return whole, None, found
    return title, year, None


def search_plex(title, token=None):
    """Search every movie library for a title.

    Plex matches on word boundaries, case- and accent-insensitively, so
    "anchorman" finds "Anchorman: The Legend of Ron Burgundy" and "amelie"
    finds "Amelie". A film held in two libraries is returned once, listing both.

    The library index is asked first which libraries could hold it. A title it
    has never seen is answered with nothing, without a single request to Plex -
    the case that used to cost the most, because every library had to be asked
    to be sure. One it has seen is confirmed with the person's own token, only
    in the libraries that hold it, since Plex also decides who may see what.
    """
    query = (title or "").strip()
    if not query:
        return []

    collected, originals = {}, {}
    for section, results in _search_with_index(query, "movie", movie_sections(token)):
        for item in results:
            key = ((item.title or "").strip().lower(), item.year)
            if key in collected:
                if section.title not in collected[key]["libraries"]:
                    collected[key]["libraries"].append(section.title)
                continue
            state = availability(item)
            originals[key] = getattr(item, "originalTitle", None)
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
    for key, movie in collected.items():
        confidence = best_confidence(query, movie["title"], originals.get(key))
        if confidence:
            movie["confidence"] = confidence
            scored.append((confidence, movie))
    scored.sort(key=lambda row: (-row[0], row[1]["title"].lower(), row[1]["year"] or 0))
    return [movie for _confidence, movie in scored]


def same_year(matches, year):
    """The matches from the year that was named, allowing one either side.

    A year given is a year meant. The rule this replaces trusted a year only
    when something matched it and otherwise handed back every match, so
    "Resident Evil (2026)" was answered with the 2002 film: asking for the new
    one was told "we already have that", and a request for it would have been
    settled - "it's here" - by the old one. One year of slack, because Plex and
    TMDb disagree on a film's year often enough: a festival premiere against
    the release, one country against another.
    """
    if not year:
        return matches
    exact = [m for m in matches if m.get("year") == year]
    return exact or [m for m in matches if m.get("year") and abs(m["year"] - year) <= 1]


def search_and_capture(db, query, capture=True, year=None, tmdb_id=None):
    """Search Plex; add the title to the wanted list when nothing matches.

    year and tmdb_id, when the caller knows them, are the film exactly - a
    button on a TMDb card knows both - and a year given this way is used as it
    is rather than read back out of the title.
    """
    title, said_year, found = shelf_reading(query, search_plex)
    if year is None:
        year = said_year
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

    everything = found[0] if found else search_plex(title)
    matches = same_year(everything, year)
    # Only certainty answers "we have that" - the same bar search_by_title
    # holds. A confidence of 1 is the making-of documentary, or Taxi Driver
    # turning up for "Taxi": worth mentioning, never worth claiming, and
    # claiming it here is how somebody asks for The Odyssey, is told "good
    # news, we already have that" on the strength of "The Odyssey: The Making
    # of an Epic", and never gets the film they wanted.
    certain = [m for m in matches if m.get("confidence", 2) >= 2]
    result["matches"] = certain
    # Films of the same name from other years are worth naming for the same
    # reason - "the 2002 one is here, not the one you asked for" - and never
    # worth claiming.
    kept = {m["rating_key"] for m in matches}
    result["close"] = ([m for m in matches if m.get("confidence") == 1 and not m["file_missing"]]
                       + [m for m in everything if m["rating_key"] not in kept
                          and m.get("confidence", 2) >= 2 and not m["file_missing"]])

    # Plex still lists a film after its file disappears. If every copy of every
    # match is gone, the library cannot actually play it, so it belongs on the
    # wanted list even though the search "found" something.
    playable = [m for m in certain if not m["file_missing"]]
    result["found"] = bool(playable)
    result["file_missing"] = bool(certain) and not playable
    if playable or not capture:
        return result
    if certain:
        gone = certain[0]
        note = "Plex lists this but the file is missing from disk"
        row, created = db.insert_wanted(
            gone["title"], gone["year"], (query or "").strip(), notes=note, tmdb_id=tmdb_id
        )
        result["added_to_wanted"] = created
        result["already_wanted"] = not created
        result["wanted"] = dict(row)
        return result

    row, created = db.insert_wanted(title, year, (query or "").strip(), tmdb_id=tmdb_id)
    result["added_to_wanted"] = created
    result["already_wanted"] = not created
    result["wanted"] = dict(row)
    return result


def recheck(db):
    """Re-search every wanted entry and report the ones Plex now has.

    Nothing is reclassified here; settle_wanted in api/routes.py does that,
    and marking something acquired by hand stays possible from the console.
    """
    now_in_plex = []
    entries = db.get_wanted("wanted")
    for entry in entries:
        matches = arrived(entry)
        if matches:
            found = dict(entry)
            found["matches"] = matches
            now_in_plex.append(found)
    return {"checked": len(entries), "now_in_plex": now_in_plex}


def _asked_at(entry):
    """When an entry was added, as a Unix time. SQLite writes it in UTC."""
    try:
        return datetime.datetime.strptime(str(entry["added_at"])[:19], "%Y-%m-%d %H:%M:%S") \
            .replace(tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def arrived(entry):
    """What in Plex now answers this wanted entry; empty while it has not arrived.

    A film by the same name from another year is not it: this is the check
    that sends "it's here", and it once sent that for Resident Evil (2026) on
    the strength of the 2002 film. A series is looked for among the series -
    the film libraries hold nothing that answers it - and a season among that
    series' seasons.
    """
    keys = entry.keys()
    kind = (entry["kind"] if "kind" in keys else None) or "film"
    title, year = entry["title"], entry["year"]

    if kind == "show":
        shows = [s for s in same_year(search_shows(title), year) if s.get("confidence", 2) >= 2]
        season = entry["season"] if "season" in keys else None
        if season:
            return [s for s in shows if season in seasons_held(s["rating_key"])]
        # The whole series, or "the next season" of one that is partly here:
        # finding the series proves nothing when it was already on the shelf
        # when they asked, so it has to have been added since.
        asked = _asked_at(entry)
        return [s for s in shows if asked and (s.get("added_at") or 0) > asked]

    tmdb_id = entry["tmdb_id"] if "tmdb_id" in keys else None
    if tmdb_id:
        from scanner import library_index
        by_id = library_index.films_by_tmdb(tmdb_id)
        if by_id:
            return by_id
    films = same_year(search_plex(title), year)
    # Plex keeps listing a title after its file is gone - which is often
    # exactly why the title is on this list. Only a copy that can actually be
    # played means it has arrived; otherwise announcing "it's here" is a lie,
    # and it would flip straight back on the next check. The same goes for a
    # companion piece: the making-of arriving is not the film arriving,
    # however alike the names.
    return [m for m in films if not m["file_missing"] and m.get("confidence", 2) >= 2]


def export_line(title, year):
    return f"{title} ({year})" if year else title


def export_text(db):
    """Plain text, one `Title (Year)` per line, for copy-paste when acquiring."""
    lines = [export_line(row["title"], row["year"]) for row in db.get_wanted("wanted")]
    return "\n".join(lines) + ("\n" if lines else "")
