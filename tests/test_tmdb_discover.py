"""Discovery from TMDb, and what it leaves out because the page already shows it."""

import pytest
import requests

from config import config
from scanner import library_index
from tests.fake_plex import film

BASE = "https://tmdb.test/3"


class Answer:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def movie(tmdb_id, title, released):
    return {"id": tmdb_id, "title": title, "release_date": released,
            "overview": "", "poster_path": None, "vote_average": 7.1}


def series(tmdb_id, name, first_aired):
    return {"id": tmdb_id, "name": name, "first_air_date": first_aired,
            "overview": "", "poster_path": None, "vote_average": 7.8}


@pytest.fixture
def tmdb(monkeypatch):
    """TMDb configured and answered by hand. Set "films" or "shows" to what it should return."""
    from api import tmdb_lookup

    monkeypatch.setitem(config, "tmdb", {"api_key": "FIXTURE-TMDB-KEY", "base_url": BASE,
                                         "language": "en-GB"})
    monkeypatch.setattr(tmdb_lookup, "_GENRE_IDS", {})
    monkeypatch.setattr(tmdb_lookup, "_TV_GENRE_IDS", {})
    world = {"films": [], "shows": []}
    answers = {
        "/genre/movie/list": lambda: {"genres": [{"id": 27, "name": "Horror"}]},
        "/genre/tv/list": lambda: {"genres": [{"id": 10765, "name": "Sci-Fi & Fantasy"}]},
        "/discover/movie": lambda: {"results": world["films"]},
        "/discover/tv": lambda: {"results": world["shows"]},
    }

    def get(url, params=None, timeout=None):
        path = url[len(BASE):] if url.startswith(BASE) else url
        if path not in answers:
            # pytest.fail rather than an assert, which discovery's own
            # except Exception would swallow into an empty answer.
            pytest.fail(f"unexpected request: {url}")
        return Answer(answers[path]())

    monkeypatch.setattr(requests, "get", get)
    return world


RESIDENT_EVIL_2026 = movie(1234, "Resident Evil", "2026-09-18")
HALLOWEEN_2018 = movie(424139, "Halloween", "2018-10-18")
WEAPONS = movie(1078605, "Weapons", "2025-08-06")


def found(results):
    return [(r["title"], r["year"], r["tmdb_id"]) for r in results]


def test_a_film_of_the_same_name_from_another_year_is_still_offered(tmdb):
    from api import tmdb_lookup

    tmdb["films"] = [RESIDENT_EVIL_2026, HALLOWEEN_2018, WEAPONS]
    results = tmdb_lookup.discover({"genres": ["Horror"]},
                                   exclude_titles=[("Resident Evil", 2002), ("Halloween", 1978)])
    assert found(results) == [("Resident Evil", 2026, 1234), ("Halloween", 2018, 424139),
                              ("Weapons", 2025, 1078605)]


def test_the_film_on_the_page_is_not_offered_again(tmdb):
    from api import tmdb_lookup

    tmdb["films"] = [RESIDENT_EVIL_2026, HALLOWEEN_2018, WEAPONS]
    # A year out is still the same film: sources disagree about a December opening.
    results = tmdb_lookup.discover({"genres": ["Horror"]},
                                   exclude_titles=[("Halloween", 2018), ("Resident Evil", 2025)])
    assert found(results) == [("Weapons", 2025, 1078605)]


def test_without_a_year_on_either_side_the_name_decides(tmdb):
    from api import tmdb_lookup

    tmdb["films"] = [RESIDENT_EVIL_2026, HALLOWEEN_2018, WEAPONS]
    results = tmdb_lookup.discover({"genres": ["Horror"]},
                                   exclude_titles=["Halloween", ("Resident Evil", None)])
    assert found(results) == [("Weapons", 2025, 1078605)]

    tmdb["films"] = [movie(1078605, "Weapons", "")]
    assert tmdb_lookup.discover({"genres": ["Horror"]}, exclude_titles=[("Weapons", 2025)]) == []


def test_a_near_miss_on_the_name_needs_the_year_as_well(tmdb):
    from api import tmdb_lookup

    tmdb["films"] = [movie(194, "Amélie", "2001-04-25")]
    assert tmdb_lookup.discover({"genres": ["Horror"]}, exclude_titles=[("Amelie", 2001)]) == []
    kept = tmdb_lookup.discover({"genres": ["Horror"]}, exclude_titles=[("Amelie", 1970)])
    assert found(kept) == [("Amélie", 2001, 194)]


def test_a_series_of_the_same_name_from_another_year_is_still_offered(tmdb):
    from api import tmdb_lookup

    tmdb["shows"] = [series(57243, "Doctor Who", "2005-03-26"), series(121, "Doctor Who", "1963-11-23")]
    results = tmdb_lookup.discover_shows({"genres": ["Science Fiction"]},
                                         exclude_titles=[("Doctor Who", 1963)])
    assert found(results) == [("Doctor Who", 2005, 57243)]


def test_the_series_on_the_page_is_not_offered_again_nor_twice(tmdb):
    from api import tmdb_lookup

    doctor_who = series(57243, "Doctor Who", "2005-03-26")
    tmdb["shows"] = [doctor_who]
    assert tmdb_lookup.discover_shows({"genres": ["Science Fiction"]},
                                      exclude_titles=[("Doctor Who", 2005)]) == []
    tmdb["shows"] = [doctor_who, dict(doctor_who)]
    assert found(tmdb_lookup.discover_shows({"genres": ["Science Fiction"]})) == [
        ("Doctor Who", 2005, 57243)]


@pytest.fixture
def not_in_plex(plex, client, tmdb, monkeypatch, tmp_path):
    """A signed-in member asking for horror the library has not got.

    Halloween (2018) is on the shelf but not in the screenful of films the page
    was given, so discovery offers it and the library index has to take it back
    out. The model and the screenful are the only things stood in for.
    """
    from api import plex_auth, request_assistant, tmdb_lookup
    from api.request_assistant import MovieFilters

    monkeypatch.setitem(config, "database", {"path": str(tmp_path / "ops.db")})
    plex.add_section(1, "Movies", "movie", [
        film(101, "Resident Evil", 2002, tmdb=1576),
        film(102, "Halloween", 1978, tmdb=948),
        film(103, "Halloween", 2018, tmdb=424139),
    ])
    library_index.full_sync()
    person = {"id": "7", "username": "Jules", "role": "shared", "plex_token": "member-token"}
    monkeypatch.setattr(plex_auth, "session_user", lambda db, token: person if token == "sess" else None)
    client.set_cookie("plexops_session", "sess")

    monkeypatch.setattr(tmdb_lookup, "suggest", lambda *a, **k: [])
    monkeypatch.setattr(tmdb_lookup, "suggest_shows", lambda *a, **k: [])
    monkeypatch.setattr(request_assistant, "off_topic", lambda text: False)
    monkeypatch.setattr(request_assistant, "translate", lambda text, token=None: (
        MovieFilters(genres=["Horror"], only_missing=True, interpretation="Horror we lack."), "model"))
    screenful = [{"title": "Resident Evil", "year": 2002}, {"title": "Halloween", "year": 1978}]
    monkeypatch.setattr(request_assistant, "find_matches", lambda *a, **k: (screenful, ["Horror"], []))
    tmdb["films"] = [RESIDENT_EVIL_2026, HALLOWEEN_2018, WEAPONS]
    return client


def test_the_search_offers_the_remake_but_never_what_the_shelves_hold(not_in_plex):
    response = not_in_plex.post("/request/search", json={"query": "horror we have not got"})
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["kind"] == "missing_only"
    # Names on the wire, as the app reads them; the years stay on the server.
    assert body["held_titles"] == ["Resident Evil", "Halloween"]
    assert "_held" not in body
    assert [(m["title"], m["year"]) for m in body["missing"]] == [("Resident Evil", 2026), ("Weapons", 2025)]


def test_the_playlist_builder_wants_the_remake_but_never_what_the_shelves_hold(not_in_plex):
    response = not_in_plex.post("/request/playlist/auto", json={"query": "horror we have not got"})
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["added"] == 0
    assert [(w["title"], w["year"]) for w in body["also_wanted"]] == [("Resident Evil", 2026), ("Weapons", 2025)]
