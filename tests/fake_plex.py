"""A Plex server built from real plexapi objects, with no network behind it.

The items are genuine plexapi Movie, Show, Season and Episode objects parsed
from the same XML Plex sends, so code under test reads them exactly as it
would read the real thing. Anything that would make plexapi go back to the
server - including its habit of re-fetching a whole film when an empty field is
read - reaches FakeServer.query and fails the test, which is the point: a
search that quietly costs one request per result is the bug being guarded.

Every request that would have gone to Plex is appended to server.calls, so a
test can assert that a search asked Plex nothing at all.
"""

import unicodedata
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from plexapi.video import Episode, Movie, Season, Show

_KINDS = {"movie": 1, "show": 2, "season": 3, "episode": 4}


def film(key, title, year=None, *, original=None, tmdb=None, imdb=None, files=(),
         genres=(), added=1_600_000_000, content_rating=None, audience_rating=None,
         directors=(), actors=(), summary=""):
    return {"type": "movie", "key": str(key), "title": title, "year": year, "original": original,
            "tmdb": tmdb, "imdb": imdb, "files": list(files), "genres": list(genres), "added": added,
            "content_rating": content_rating, "audience_rating": audience_rating,
            "directors": list(directors), "actors": list(actors), "summary": summary}


def series(key, title, year=None, *, seasons=None, tmdb=None, added=1_600_000_000, summary=""):
    seasons = dict(seasons or {1: 6})
    return {"type": "show", "key": str(key), "title": title, "year": year, "tmdb": tmdb,
            "seasons": seasons, "added": added, "summary": summary}


def _attr(element, name, value):
    if value is not None:
        element.set(name, str(value))


def _xml(spec, section_key):
    kind = spec["type"]
    tag = "Video" if kind in ("movie", "episode") else "Directory"
    element = ET.Element(tag)
    _attr(element, "ratingKey", spec["key"])
    _attr(element, "key", f"/library/metadata/{spec['key']}")
    _attr(element, "type", kind)
    _attr(element, "title", spec.get("title"))
    _attr(element, "originalTitle", spec.get("original"))
    _attr(element, "year", spec.get("year"))
    _attr(element, "summary", spec.get("summary"))
    _attr(element, "thumb", f"/library/metadata/{spec['key']}/thumb/1")
    _attr(element, "addedAt", spec.get("added"))
    _attr(element, "updatedAt", spec.get("added"))
    _attr(element, "librarySectionID", section_key)
    _attr(element, "contentRating", spec.get("content_rating"))
    _attr(element, "audienceRating", spec.get("audience_rating"))
    if kind == "movie":
        _attr(element, "duration", 6_000_000)
        media = ET.SubElement(element, "Media", id=spec["key"])
        for number, path in enumerate(spec.get("files") or []):
            ET.SubElement(media, "Part", id=f"{spec['key']}{number}", file=path)
        for tag_name, values in (("Genre", spec.get("genres")), ("Director", spec.get("directors")),
                                 ("Role", spec.get("actors"))):
            for value in values or []:
                ET.SubElement(element, tag_name, tag=value)
    if kind == "show":
        _attr(element, "childCount", len(spec["seasons"]))
        _attr(element, "leafCount", sum(spec["seasons"].values()))
    for scheme in ("tmdb", "imdb"):
        if spec.get(scheme):
            ET.SubElement(element, "Guid", id=f"{scheme}://{spec[scheme]}")
    return element


def _plain(text):
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _plex_matches(query, title):
    """Roughly how Plex's own title filter behaves: every word of the query starts a word of the title."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in _plain(title)).split()]
    wanted = [w for w in "".join(c if c.isalnum() else " " for c in _plain(query)).split()]
    return bool(wanted) and all(any(w.startswith(q) for w in words) for q in wanted)


class FakeSection:
    def __init__(self, server, key, title, kind, items):
        self._server = server
        self.key = key
        self.title = title
        self.type = kind
        self.items = list(items)
        self.fail = False

    def _build(self, spec, ekey):
        cls = {"movie": Movie, "show": Show}[spec["type"]]
        return cls(self._server, _xml(spec, self.key), initpath=ekey)

    def search(self, title=None, sort=None, maxresults=None, libtype=None, filters=None, **kwargs):
        self._server.calls.append(("search", self.title, title))
        if self.fail:
            raise ConnectionError("Plex went away")
        ekey = f"/library/sections/{self.key}/all"
        found = [spec for spec in self.items if title is None or _plex_matches(title, spec["title"])]
        if maxresults:
            found = found[:maxresults]
        return [self._build(spec, ekey) for spec in found]

    def all(self, **kwargs):
        return self.search(**kwargs)

    def fetchItems(self, ekey, container_size=None, maxresults=None, **kwargs):
        self._server.calls.append(("list", self.title, ekey))
        if self.fail:
            raise ConnectionError("Plex went away")
        query = dict(part.split("=", 1) for part in ekey.split("?", 1)[1].split("&"))
        kind = {number: name for name, number in _KINDS.items()}[int(query["type"])]
        newest = query.get("sort") == "addedAt:desc"
        if kind in ("movie", "show"):
            specs = sorted(self.items, key=lambda s: -s["added"]) if newest else list(self.items)
            if maxresults:
                specs = specs[:maxresults]
            return [self._build(spec, ekey) for spec in specs]
        out = []
        for show in self.items:
            for number, episodes in sorted(show.get("seasons", {}).items()):
                if kind == "season":
                    element = ET.Element("Directory", ratingKey=f"{show['key']}{number:02d}", type="season",
                                         parentRatingKey=show["key"], index=str(number),
                                         leafCount=str(episodes), title=f"Season {number}")
                    out.append(Season(self._server, element, initpath=ekey))
                else:
                    element = ET.Element("Video", ratingKey=f"{show['key']}{number:02d}01", type="episode",
                                         grandparentRatingKey=show["key"], parentIndex=str(number),
                                         index="1", title="Episode", addedAt=str(show["added"]))
                    out.append(Episode(self._server, element, initpath=ekey))
        if maxresults:
            out = out[:maxresults]
        return out

    def listFilterChoices(self, field, libtype=None):
        self._server.calls.append(("choices", self.title, field))
        role = {"director": "directors", "actor": "actors"}[field]
        names = sorted({name for spec in self.items for name in spec.get(role, [])})
        return [SimpleNamespace(title=name) for name in names]


class _Library:
    def __init__(self, server):
        self._server = server

    def sections(self):
        return list(self._server.sections)


class FakeServer:
    """The owner's view of one server: every library, every item."""

    def __init__(self, machine="fake-machine"):
        self.machineIdentifier = machine
        self.sections = []
        self.calls = []
        self.library = _Library(self)

    def add_section(self, key, title, kind, items=()):
        section = FakeSection(self, key, title, kind, items)
        self.sections.append(section)
        return section

    def searches(self):
        return [call for call in self.calls if call[0] == "search"]

    def _spec(self, rating_key):
        for section in self.sections:
            for spec in section.items:
                if spec["key"] == str(rating_key):
                    return section, spec
        raise LookupError(rating_key)

    def fetchItem(self, rating_key):
        self.calls.append(("fetch", None, str(rating_key)))
        for section in self.sections:
            for show in section.items:
                for number in show.get("seasons", {}):
                    if str(rating_key) == f"{show['key']}{number:02d}01":
                        element = ET.Element("Video", ratingKey=str(rating_key), type="episode",
                                             grandparentRatingKey=show["key"], parentIndex=str(number),
                                             index="1", librarySectionID=str(section.key))
                        episode = Episode(self, element, initpath=f"/library/metadata/{rating_key}")
                        episode._initpath = episode._details_key
                        return episode
        section, spec = self._spec(rating_key)
        item = section._build(spec, f"/library/metadata/{spec['key']}")
        item._initpath = item._details_key     # what plexapi's own fetchItem does: a full object
        return item

    def fetchItems(self, ekey, **kwargs):
        self.calls.append(("fetch", None, ekey))
        show_key = ekey.split("/")[3]
        section, spec = self._spec(show_key)
        return [Season(self, ET.Element("Directory", ratingKey=f"{show_key}{n:02d}", type="season",
                                        parentRatingKey=show_key, index=str(n), leafCount=str(e)),
                       initpath=ekey)
                for n, e in sorted(spec["seasons"].items())]

    def query(self, key, *args, **kwargs):
        raise AssertionError(f"an unexpected request to Plex (plexapi re-fetching an item?): {key}")
