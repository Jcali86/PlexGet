"""Playlists that belong to the person, not to the server owner.

A playlist made with the owner's token is the owner's: it shows up in their
Plex, not in the viewer's. Acting with each person's own token instead means
their list is genuinely theirs - it appears in their Plex app on any device,
and it is theirs to keep if they ever stop using this.

Plex will not create an empty playlist, so a person's list appears in Plex the
moment they add their first film rather than at sign-up.
"""

from plexapi.server import PlexServer

from config import config

_servers = {}


def app_name():
    return (config.get("app") or {}).get("name", "Plex Requests")


def default_playlist_name(username):
    return f"{username}'s Picks"


def as_user(token):
    """Connect to this server using someone's own Plex token.

    Their token authenticates against the server because it is shared with
    them; what they can see and do is whatever Plex already allows them.
    """
    if not token:
        raise PermissionError("no Plex token for this session")
    if token not in _servers:
        _servers[token] = PlexServer(config["plex"]["url"], token)
    return _servers[token]


def playlists_for(token):
    """Video playlists belonging to this person."""
    server = as_user(token)
    out = []
    for playlist in server.playlists():
        if getattr(playlist, "playlistType", "") != "video":
            continue
        try:
            count = len(playlist.items())
        except Exception:
            count = 0
        out.append({"title": playlist.title, "items": count})
    return sorted(out, key=lambda p: p["title"].lower())


def _people(items, limit=8):
    return [p.tag for p in (items or [])][:limit]


def item_detail(token, rating_key):
    """Everything worth knowing about one film or series, from Plex itself.

    Plex already holds the cast, the crew and the studio for anything on the
    shelf - fetched with this person's own token, so it can only ever show
    them something they are allowed to see.
    """
    server = as_user(token)
    try:
        item = server.fetchItem(int(rating_key))
    except Exception:
        return None

    kind = getattr(item, "type", "")
    detail = {
        "kind": kind,
        "title": item.title,
        "year": item.year,
        "summary": (item.summary or "").strip(),
        "rating": item.audienceRating or item.rating,
        "critic_rating": getattr(item, "rating", None),
        "content_rating": getattr(item, "contentRating", None),
        "studio": getattr(item, "studio", None),
        "tagline": (getattr(item, "tagline", "") or "").strip(),
        "genres": _people(getattr(item, "genres", None)),
        "thumb": item.thumb,
        "art": getattr(item, "art", None),
        "rating_key": str(item.ratingKey),
        "runtime_minutes": round((getattr(item, "duration", 0) or 0) / 60000) or None,
        "added_at": int(item.addedAt.timestamp()) if getattr(item, "addedAt", None) else 0,
    }

    if kind == "movie":
        detail["directors"] = _people(getattr(item, "directors", None), 3)
        detail["writers"] = _people(getattr(item, "writers", None), 3)
        detail["cast"] = _people(getattr(item, "roles", None), 8)
        try:
            media = (item.media or [])[0]
            detail["quality"] = " · ".join(
                x for x in [getattr(media, "videoResolution", "") and
                            str(media.videoResolution).upper(),
                            getattr(media, "videoCodec", "") and str(media.videoCodec).upper(),
                            getattr(media, "audioCodec", "") and str(media.audioCodec).upper()]
                if x)
        except Exception:
            detail["quality"] = ""
    elif kind == "show":
        detail["cast"] = _people(getattr(item, "roles", None), 8)
        detail["seasons"] = getattr(item, "childCount", None)
        detail["episodes"] = getattr(item, "leafCount", None)
        detail["watched_episodes"] = getattr(item, "viewedLeafCount", 0)
        try:
            detail["season_list"] = [
                {"season": s.seasonNumber, "episodes": getattr(s, "leafCount", None)}
                for s in item.seasons() if getattr(s, "seasonNumber", None) is not None
            ]
        except Exception:
            detail["season_list"] = []
    return detail


def _their_playlist(server, name):
    return next(
        (p for p in server.playlists()
         if getattr(p, "playlistType", "") == "video"
         and p.title.lower() == (name or "").lower()), None)


def playlist_items(token, name):
    """The films in one of this person's playlists, ready for the page."""
    server = as_user(token)
    playlist = _their_playlist(server, name)
    if playlist is None:
        return None
    films = []
    for item in playlist.items():
        if getattr(item, "type", "") != "movie":
            continue
        films.append({
            "title": item.title,
            "year": item.year,
            "rating_key": str(item.ratingKey),
            "thumb": item.thumb,
            "rating": item.audienceRating or item.rating,
            "runtime_minutes": round((item.duration or 0) / 60000) or None,
            "added_at": int(item.addedAt.timestamp()) if getattr(item, "addedAt", None) else 0,
        })
    return films


def remove_from_playlist(token, name, rating_keys):
    """Take films out of one of this person's playlists. Only theirs - the
    token scopes it, so nobody can trim anybody else's list."""
    server = as_user(token)
    playlist = _their_playlist(server, name)
    if playlist is None:
        return {"error": f"no playlist called {name!r}"}
    wanted_out = {str(k) for k in rating_keys}
    items = [i for i in playlist.items() if str(i.ratingKey) in wanted_out]
    if items:
        playlist.removeItems(items)
    return {"playlist": playlist.title, "removed": len(items)}


def add_to_playlist(token, playlist_name, rating_keys, username=""):
    """Put films into one of this person's playlists, creating it if needed."""
    server = as_user(token)
    name = (playlist_name or "").strip() or default_playlist_name(username or "My")

    items = []
    for key in rating_keys:
        try:
            items.append(server.fetchItem(int(key)))
        except Exception:
            continue
    if not items:
        return {"error": "none of those films could be found"}

    existing = next(
        (p for p in server.playlists() if p.title.lower() == name.lower()), None
    )
    if existing is None:
        server.createPlaylist(name, items=items)
        return {"playlist": name, "added": len(items), "created": True}

    already = {item.ratingKey for item in existing.items()}
    fresh = [item for item in items if item.ratingKey not in already]
    if fresh:
        existing.addItems(fresh)
    return {
        "playlist": name,
        "added": len(fresh),
        "already_there": len(items) - len(fresh),
        "created": False,
    }
