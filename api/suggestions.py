"""Suggestions drawn from what someone has already watched.

The facts come from Plex - what this person played, and which films sit in the
same collection - so nothing is invented. The library is the only source: this
never proposes something that would have to be fetched.
"""

from config import config
from plexapi.server import PlexServer
from pydantic import BaseModel, Field

MAX_HISTORY = 40
MAX_FRANCHISE = 30

# Films sit in curated groupings as well as franchises - "00's Movies",
# "IMDb Top 250" - and following those produces "you watched Pirates of the
# Caribbean, try 8 Mile". Only series groupings are worth following.
import re

GENERIC_COLLECTION = re.compile(
    r"(^\d{2}'?s\b|\b\d{4}s\b|imdb|top \d+|best of|oscar|award|marathon|"
    r"christmas|halloween|watchlist|favourites|favorites|^movies$|^films$)", re.I
)
SERIES_COLLECTION = re.compile(r"(collection|saga|trilogy|series|duology|quadrilogy|anthology)$", re.I)


def franchise_collections(names):
    """Keep the groupings that look like a series, not a curated shelf."""
    return [
        name for name in names
        if SERIES_COLLECTION.search(name.strip()) and not GENERIC_COLLECTION.search(name)
    ]


def _owner_server():
    """Watch history is only readable with the owner's token."""
    return PlexServer(config["plex"]["url"], config["plex"]["token"])


def _viewer_server(token=None):
    """What to offer has to come from libraries this person can actually watch."""
    if not token:
        return _owner_server()
    return PlexServer(config["plex"]["url"], token)


def server_account_id(account_id):
    """The id this server files someone's history under.

    Shared users are recorded by their plex.tv id, but the owner is always
    account 1 locally - so looking the owner up by their plex.tv id quietly
    returns nothing, and every suggestion drawn from history comes back empty.
    """
    try:
        owner = PlexServer(config["plex"]["url"], config["plex"]["token"]).myPlexAccount()
        if str(account_id) == str(getattr(owner, "id", "")):
            return 1
    except Exception:
        pass
    return int(account_id)


def watched(account_id, limit=MAX_HISTORY):
    """Films this person has played, most recent first."""
    server = _owner_server()
    try:
        entries = server.history(maxresults=limit * 4,
                                 accountID=server_account_id(account_id))
    except Exception:
        return []
    films, seen = [], set()
    for entry in entries:
        if getattr(entry, "type", "") != "movie":
            continue
        key = str(getattr(entry, "ratingKey", "") or entry.title)
        if key in seen:
            continue
        seen.add(key)
        films.append({"title": entry.title, "rating_key": key,
                      "viewed_at": str(getattr(entry, "viewedAt", ""))})
        if len(films) >= limit:
            break
    return films


def _movie(server, rating_key):
    try:
        return server.fetchItem(int(rating_key))
    except Exception:
        return None


def next_in_collections(account_id, limit=6, token=None):
    """The obvious follow-ups: films sharing a collection with something they watched.

    A sequel is a lookup, not a guess - Plex already groups these - so this is
    exact rather than inferred, and it can only ever name films in the library.
    """
    server = _viewer_server(token)
    history = watched(account_id)
    if not history:
        return []

    seen_keys = {f["rating_key"] for f in history}
    suggestions, seen_titles = [], set()

    for film in history:
        item = _movie(server, film["rating_key"])
        if item is None:
            continue
        try:
            collections = [c.tag for c in (item.collections or [])]
        except Exception:
            collections = []
        for name in franchise_collections(collections):
            for section in server.library.sections():
                if section.type != "movie":
                    continue
                try:
                    siblings = section.search(collection=name, maxresults=MAX_FRANCHISE + 1)
                except Exception:
                    continue
                if len(siblings) > MAX_FRANCHISE:
                    continue      # too big to be a series; a shelf by another name
                for sibling in siblings:
                    key = str(sibling.ratingKey)
                    title = sibling.title.strip().lower()
                    if key in seen_keys or title in seen_titles:
                        continue
                    if getattr(sibling, "viewCount", 0):
                        continue          # already played
                    seen_titles.add(title)
                    suggestions.append({
                        "title": sibling.title,
                        "year": sibling.year,
                        "rating_key": key,
                        "rating": sibling.audienceRating or sibling.rating,
                        "thumb": sibling.thumb,
                        "library": section.title,
                        "because": f"you watched {item.title}",
                        "collection": name,
                    })
                    if len(suggestions) >= limit:
                        return suggestions
    return suggestions


def starter_picks(account_id, limit=3, token=None):
    """A few films to open someone's list with.

    Follow-ups to what they have watched come first, since those are exact.
    Anyone with no history gets the library's best-regarded films they have
    not played.
    """
    picks = next_in_collections(account_id, limit=limit, token=token)
    if len(picks) >= limit:
        return picks[:limit], "following on from what you have watched"

    server = _viewer_server(token)
    have = {p["rating_key"] for p in picks}
    for section in server.library.sections():
        if section.type != "movie" or len(picks) >= limit:
            continue
        try:
            best = section.search(
                filters={"and": [{"unwatched": True}, {"audienceRating>>=": 8}]},
                sort="audienceRating:desc", maxresults=limit * 2,
            )
        except Exception:
            continue
        for movie in best:
            if str(movie.ratingKey) in have or len(picks) >= limit:
                continue
            have.add(str(movie.ratingKey))
            picks.append({
                "title": movie.title, "year": movie.year,
                "rating_key": str(movie.ratingKey),
                "rating": movie.audienceRating or movie.rating,
                "thumb": movie.thumb, "library": section.title,
                "because": "well regarded and you have not seen it",
                "collection": "",
            })
    reason = ("following on from what you have watched" if any(p["because"].startswith("you watched")
              for p in picks) else "well regarded and unwatched")
    return picks[:limit], reason


# ---- "you watched Cars, fancy Shrek?" --------------------------------------

MAX_POOL = 90


class Nudge(BaseModel):
    """One film worth suggesting, and the watched film that justifies it."""

    title: str = Field(description="Exact title from the offered list.")
    because: str = Field(description="Exact title of the watched film it follows from.")
    line: str = Field(
        description="One short sentence to the person, in the assistant's own voice."
    )


class Nudges(BaseModel):
    picks: list[Nudge] = Field(default_factory=list)


def taste_nudges(account_id, limit=3, token=None, age=0):
    """Films they have not seen that follow from ones they have.

    A sequel is a lookup; this is the other half - the leap across franchises
    that a person would make and a collection never will. The model only ever
    chooses from titles already on the shelf and already known to be unwatched,
    so it cannot invent a film or offer one that would have to be fetched.
    """
    from api.request_assistant import ratings_for_age, suits_age
    from api import ai
    from api import persona

    seen = watched(account_id)
    llm = ai.provider()
    # Asked before the pool is built, because gathering it means a search
    # against every library and there is nothing to do with the result if
    # there is no model to hand it to.
    if not seen or llm is None:
        return []
    seen_titles = [f["title"] for f in seen][:20]
    seen_keys = {f["rating_key"] for f in seen}

    # The pool: well-regarded things in this person's libraries they have not
    # played. Age-bounded when the request is for a child.
    server = _viewer_server(token)
    allowed = ratings_for_age(age)
    pool = []
    for section in server.library.sections():
        if section.type != "movie":
            continue
        conditions = [{"unwatched": True}, {"audienceRating>>=": 7}]
        if allowed:
            conditions.append({"contentRating": allowed})
        try:
            found = section.search(filters={"and": conditions}, maxresults=40)
        except Exception:
            continue
        for movie in found:
            if str(movie.ratingKey) in seen_keys:
                continue
            if age and not suits_age(getattr(movie, "contentRating", None), age):
                continue
            pool.append(movie)
    if not pool:
        return []

    by_title = {}
    for movie in pool[:MAX_POOL]:
        by_title.setdefault(movie.title.strip().lower(), movie)

    # The voice first and the rules after it, the same order the search uses:
    # who is speaking is character, and the same words underneath the rules
    # would read as another rule to be weighed against them.
    reply = llm.structured(
        system=(
            persona.voice()
            + " You suggest films from a private library. Choose ONLY from the "
            "offered list, using its exact titles. Pick films that follow "
            "naturally from what they have watched - same feel, same humour, "
            "same era - and say why in one short sentence. Never suggest a "
            "film they have watched."
        ),
        prompt=(f"They have watched: {', '.join(seen_titles)}\n\n"
                f"Offer from: {', '.join(m.title for m in by_title.values())}\n\n"
                f"Choose {limit}."),
        schema=Nudges,
        max_tokens=2000,
    )
    picks = (reply.picks if reply else []) or []

    out = []
    for pick in picks[:limit]:
        movie = by_title.get((pick.title or "").strip().lower())
        if movie is None:
            continue          # it named something not on offer; drop it
        out.append({
            "title": movie.title,
            "year": movie.year,
            "rating_key": str(movie.ratingKey),
            "rating": movie.audienceRating or movie.rating,
            "thumb": movie.thumb,
            "content_rating": getattr(movie, "contentRating", None),
            "because": pick.because,
            "line": " ".join((pick.line or "").split())[:160],
        })
    return out
