"""What each library in config actually is, according to Plex.

The keys under `nas` are yours to name - "movies", "kids", "documentaries",
whatever the folders are called - so nothing here may assume a particular set
of them. What a library holds, and which Plex section owns it, are questions
Plex can answer: sections know their type and the folders they were pointed at,
and matching those folders against the ones in config joins the two up.

Plex is not always reachable, and a scan or an audit should not stop because of
it, so every answer falls back: to what config says outright, and failing that
to what the key is called. Guessing from a name is the last resort, not the
first.
"""

import re

from config import config

from scanner import paths

_sections = None
_resolved = {}

# What a key called this most likely holds. Only ever consulted when Plex
# cannot be asked and config says nothing, and deliberately narrow: a key these
# do not recognise is left unknown rather than guessed at, because a library
# analysed as the wrong kind is worse than one left out.
TV_WORDS = ("tv", "show", "serie", "episode")
MOVIE_WORDS = ("movie", "film", "cinema", "feature")


def _overrides():
    return config.get("libraries") or {}


def keys():
    """Every library key in config, in the order they are written."""
    return list((config.get("nas") or {}).keys())


def paths_for(key):
    """The folders one library is spread across."""
    entry = (config.get("nas") or {}).get(key, [])
    if isinstance(entry, str):
        entry = [entry]
    return [paths.trim(p) for p in entry or [] if isinstance(p, str) and p.strip()]


def _plex_sections():
    """Every Plex section, asked for once and kept, or [] when Plex is quiet."""
    global _sections
    if _sections is None:
        try:
            from scanner.wanted_search import get_server

            _sections = list(get_server().library.sections())
        except Exception:
            _sections = []
    return _sections


def _match_section(key):
    """The Plex section that owns a library key, by the folders they share.

    A section lists the folders it was pointed at. Either it was pointed at the
    same folder config names, or at the drive above it, so both directions
    count as a match.
    """
    wanted = paths_for(key)
    for section in _plex_sections():
        for location in getattr(section, "locations", None) or []:
            for mine in wanted:
                if paths.is_under(mine, location) or paths.is_under(location, mine):
                    return section
    # Failing that, a section named after the key - "4k" and "4K" are the same
    # library to everyone except a string comparison.
    tidy = key.replace("_", " ").strip().lower()
    for section in _plex_sections():
        if (section.title or "").strip().lower() == tidy:
            return section
    return None


def section(key):
    """The Plex section a library key belongs to, or None."""
    if key not in _resolved:
        _resolved[key] = _match_section(key)
    return _resolved[key]


def section_name(key):
    """What Plex calls this library, for the calls that take a section title.

    Config wins, for the case where the folders do not line up and Plex cannot
    be made to say so - a library shared from another server, most often.
    """
    named = (_overrides().get("sections") or {}).get(key)
    if named:
        return named
    found = section(key)
    return found.title if found is not None else None


def kind(key):
    """"movie" or "show" - what this library holds, or None when nothing says."""
    stated = (_overrides().get("kinds") or {}).get(key)
    if stated in ("movie", "show"):
        return stated
    found = section(key)
    if found is not None and found.type in ("movie", "show"):
        return found.type
    words = [w.rstrip("s") for w in re.split(r"[^a-z0-9]+", key.lower()) if w]
    if any(w in TV_WORDS for w in words):
        return "show"
    if any(w in MOVIE_WORDS for w in words):
        return "movie"
    return None


def keys_of_kind(wanted):
    """Every library key holding the given kind, "movie" or "show"."""
    return [key for key in keys() if kind(key) == wanted]


def is_manual(key):
    """Is this a library whose quality is looked after by hand?

    A 4K library is the usual case: the files are chosen deliberately and an
    automated audit only ever nags about them. Nothing marks itself manual, so
    this is the one thing that has to be said in config outright.
    """
    manual = _overrides().get("manual") or []
    return key in set(manual)


def forget():
    """Drop what was learnt from Plex, so the next question is asked afresh."""
    global _sections
    _sections = None
    _resolved.clear()
