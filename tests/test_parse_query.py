"""Reading a title and a year out of what somebody typed.

The cases are real names the old reading got wrong: a number that is part of
the name taken for a year (Wonder Woman 1984, Death Race 2000), a name that
opens with brackets taken for an aside ((500) Days of Summer), and years
written the way Wikipedia or a release name writes them lost altogether.
"""

import datetime as real

import pytest

from config import config
from scanner import library_index, wanted_search
from scanner.wanted_search import parse_query, query_hint, split_query
from tests.fake_plex import film, series


class Today(real.date):
    """Pinned, so that what counts as a plausible year does not move under the tests."""
    @classmethod
    def today(cls):
        return real.date(2026, 9, 28)


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setattr(wanted_search.datetime, "date", Today)


# ---- the words alone ------------------------------------------------------------------------

@pytest.mark.parametrize("query, expected", [
    # A bare trailing year is a guess: the words cannot tell these from "Chef 2014", so the
    # shelf decides (see the searches below).
    ("Wonder Woman 1984", ("Wonder Woman", 1984, "trailing")),
    ("Death Race 2000", ("Death Race", 2000, "trailing")),
    ("Class of 1984", ("Class of", 1984, "trailing")),
    ("Space: 1999", ("Space:", 1999, "trailing")),
    ("Chef 2014", ("Chef", 2014, "trailing")),
    # A name that opens with brackets keeps them.
    ("(500) Days of Summer (2009)", ("(500) Days of Summer", 2009, "bracketed")),
    ("(500) Days of Summer", ("(500) Days of Summer", None, None)),
    ("[REC] (2007)", ("[REC]", 2007, "bracketed")),
    # Years written the way Wikipedia and release names write them.
    ("Resident Evil (2026 film)", ("Resident Evil", 2026, "film")),
    ("Resident Evil (2026 movie)", ("Resident Evil", 2026, "film")),
    ("Resident Evil [2026 Film]", ("Resident Evil", 2026, "film")),
    ("Resident Evil (2026) 4K", ("Resident Evil", 2026, "bracketed")),
    # Whatever parted the name from its year is not part of the name.
    ("Resident Evil - 2026", ("Resident Evil", 2026, "trailing")),
    ("Resident Evil, 2026", ("Resident Evil", 2026, "trailing")),
    ("Resident Evil – 2026", ("Resident Evil", 2026, "trailing")),    # an en dash, pasted
    ("Resident Evil - (2026)", ("Resident Evil", 2026, "bracketed")),
    # More than five years ahead is never a year.
    ("Blade Runner 2049", ("Blade Runner 2049", None, None)),
    # Unchanged.
    ("Chef (2014)", ("Chef", 2014, "bracketed")),
    ("Chef [2014]", ("Chef", 2014, "bracketed")),
    ("Taxi (French)", ("Taxi", None, None)),
    ("Taxi (French) (1998)", ("Taxi", 1998, "bracketed")),
    ("Alien (original) 1979", ("Alien", 1979, "trailing")),
    ("1917", ("1917", None, None)),
    ("2001: A Space Odyssey", ("2001: A Space Odyssey", None, None)),
    ("", ("", None, None)),
])
def test_split_query(query, expected):
    assert split_query(query) == expected
    assert parse_query(query) == expected[:2]


def test_the_hint_is_an_aside_after_the_name_and_never_a_year():
    assert query_hint("Taxi (French)") == "French"
    assert query_hint("(500) Days of Summer") == ""
    assert query_hint("Resident Evil (2026 film)") == ""


def test_blade_runner_2049_from_2044_is_read_as_a_year(monkeypatch):
    # The words split it once 2049 is within five years; the shelf has to put it back together.
    monkeypatch.setattr(Today, "today", classmethod(lambda cls: real.date(2044, 1, 1)))
    assert split_query("Blade Runner 2049") == ("Blade Runner", 2049, "trailing")


# ---- the shelf decides ----------------------------------------------------------------------

def shelf():
    return [
        film(201, "Wonder Woman", 2017),
        film(202, "Wonder Woman 1984", 2020),
        film(203, "Death Race", 2008),
        film(204, "Death Race 2000", 1975),
        film(205, "Class of 1984", 1982),
        film(206, "(500) Days of Summer", 2009),
        film(207, "Blade Runner", 1982),
        film(208, "Blade Runner 2049", 2017),
        film(209, "Chef", 2014),
        film(210, "Resident Evil", 2002),
    ]


@pytest.fixture
def library(plex, monkeypatch, tmp_path):
    monkeypatch.setitem(config, "database", {"path": str(tmp_path / "ops.db")})
    plex.add_section(1, "Movies", "movie", shelf())
    plex.add_section(3, "TV", "show", [series(301, "Space: 1999", 1975, seasons={1: 24, 2: 24})])
    library_index.full_sync()
    return plex


def titled(results):
    return [(m["title"], m["year"]) for m in results]


@pytest.mark.parametrize("query, expected", [
    ("Wonder Woman 1984", [("Wonder Woman 1984", 2020)]),
    ("Death Race 2000", [("Death Race 2000", 1975)]),
    ("Class of 1984", [("Class of 1984", 1982)]),
    ("(500) Days of Summer (2009)", [("(500) Days of Summer", 2009)]),
    ("Chef 2014", [("Chef", 2014)]),                 # a year still reads as a year
    ("Wonder Woman 2017", [("Wonder Woman", 2017)]),
])
def test_a_title_search_finds_the_film_on_the_shelf(library, query, expected):
    from api.request_assistant import search_by_title

    found = search_by_title(query)
    assert titled(found["matches"]) == expected


def test_a_series_with_a_number_in_its_name_is_found(library):
    from api.request_assistant import search_by_title

    found = search_by_title("Space: 1999")
    assert (titled(found["shows"]), found["matches"]) == ([("Space: 1999", 1975)], [])


def test_blade_runner_2049_is_still_found_once_2049_reads_as_a_year(library, monkeypatch):
    from api.request_assistant import search_by_title

    monkeypatch.setattr(Today, "today", classmethod(lambda cls: real.date(2044, 1, 1)))
    assert titled(search_by_title("Blade Runner 2049")["matches"]) == [("Blade Runner 2049", 2017)]


@pytest.mark.parametrize("query", [
    "Resident Evil (2026 film)", "Resident Evil (2026) 4K", "Resident Evil - 2026",
])
def test_the_2002_film_does_not_answer_for_the_2026_one(library, query):
    from api.request_assistant import close_matches, search_by_title

    assert search_by_title(query) is None
    # but is offered as the one of that name that is here, which needs the name read right
    assert ("Resident Evil", 2002) in titled(close_matches(query))


def test_the_close_matches_are_not_the_same_name_from_another_year(library):
    from api.request_assistant import close_matches

    # Read as "Death Race" from 2000, the 2008 film would be offered as a near thing.
    assert ("Death Race", 2008) not in titled(close_matches("Death Race 2000"))


def test_the_route_answers_with_the_film_asked_for(library, client, monkeypatch):
    from api import plex_auth

    monkeypatch.setattr(plex_auth, "session_user", lambda db, token:
                        {"id": "7", "username": "Jules", "role": "shared", "plex_token": "t7"})
    client.set_cookie("plexops_session", "sess-jules")
    body = client.post("/request/search", json={"query": "Wonder Woman 1984"}).get_json()
    assert (body["kind"], titled(body["matches"])) == ("title", [("Wonder Woman 1984", 2020)])


# ---- asking for it ----------------------------------------------------------------------------

def capture(query, **kwargs):
    from db import Database

    db = Database()
    try:
        return wanted_search.search_and_capture(db, query, **kwargs)
    finally:
        db.close()


def wanted_rows():
    from db import Database

    db = Database()
    try:
        return [(r["title"], r["year"]) for r in db.get_wanted(status=None)]
    finally:
        db.close()


@pytest.mark.parametrize("query, expected", [
    ("Wonder Woman 1984", ("Wonder Woman 1984", 2020)),
    ("Death Race 2000", ("Death Race 2000", 1975)),
    ("Class of 1984", ("Class of 1984", 1982)),
    ("(500) Days of Summer (2009)", ("(500) Days of Summer", 2009)),
])
def test_asking_for_a_film_on_the_shelf_is_told_it_is_here(library, query, expected):
    result = capture(query)
    assert (result["found"], titled(result["matches"])) == (True, [expected])
    assert wanted_rows() == []


def test_one_film_asked_for_three_ways_is_one_row(library):
    first = capture("Resident Evil - 2026")
    assert (first["found"], first["added_to_wanted"]) == (False, True)
    assert capture("Resident Evil (2026 film)")["already_wanted"] is True
    assert capture("Resident Evil (2026) 4K")["already_wanted"] is True
    assert wanted_rows() == [("Resident Evil", 2026)]


# ---- not on the shelf: TMDb decides -------------------------------------------------------------

def guess(tmdb_id, title, year, popularity):
    return {"tmdb_id": tmdb_id, "title": title, "year": year, "overview": "", "poster": "",
            "language": "en", "popularity": popularity, "original_title": title,
            "release_date": f"{year}-06-01"}


FILMS = [
    guess(464052, "Wonder Woman 1984", 2020, 40.0),
    guess(297762, "Wonder Woman", 2017, 60.0),
    guess(212778, "Chef", 2014, 20.0),
    guess(900001, "Chef 2014", 2014, 0.2),           # an obscure short: no reason to doubt the year
    guess(900002, "Hamlet 2000", 1999, 5.0),         # made up, to put TMDb at odds with the shelf
]
SHOWS = [guess(1981, "Galactica 1980", 1980, 8.0)]


def catalogue(entries):
    def search(title, hint="", year=None, limit=5):
        wanted = wanted_search.fold(title)
        return [g for g in entries if wanted_search.fold(g["title"]).startswith(wanted)
                and (not year or g["year"] == year)][:limit]
    return search


@pytest.fixture
def elsewhere(plex, client, monkeypatch, tmp_path):
    """A shelf without Wonder Woman 1984 or Galactica 1980, TMDb with both, and a spy on the model path."""
    from api import plex_auth, routes, tmdb_lookup

    monkeypatch.setitem(config, "database", {"path": str(tmp_path / "ops.db")})
    plex.add_section(1, "Movies", "movie", [film(201, "Wonder Woman", 2017), film(211, "Hamlet", 2000)])
    plex.add_section(3, "TV", "show", [])
    library_index.full_sync()

    monkeypatch.setattr(tmdb_lookup, "configured", lambda: True)
    monkeypatch.setattr(tmdb_lookup, "suggest", catalogue(FILMS))
    monkeypatch.setattr(tmdb_lookup, "suggest_shows", catalogue(SHOWS))
    monkeypatch.setattr(tmdb_lookup, "availability", lambda tmdb_id: ("out", None))
    monkeypatch.setattr(tmdb_lookup, "seasons_that_exist", lambda title, year=None: [])
    monkeypatch.setattr(plex_auth, "session_user", lambda db, token:
                        {"id": "7", "username": "Jules", "role": "shared", "plex_token": "t7"})
    client.set_cookie("plexops_session", "sess-jules")

    modelled = []
    real = routes.mood_search
    monkeypatch.setattr(routes, "mood_search", lambda query, **kw: modelled.append(query) or real(query, **kw))
    return {"client": client, "modelled": modelled}


def search_for(world, query):
    return world["client"].post("/request/search", json={"query": query}).get_json()


def test_a_film_we_lack_is_named_whole_and_costs_no_model_call(elsewhere):
    body = search_for(elsewhere, "Wonder Woman 1984")
    assert (body["kind"], body["playlist_name"]) == ("title_missing", "Wonder Woman 1984")
    assert titled(body["did_you_mean"])[:1] == [("Wonder Woman 1984", 2020)]
    assert elsewhere["modelled"] == []


def test_a_series_we_lack_is_named_whole(elsewhere):
    # Not Space: 1999, whose "space" reads as a mood for science fiction before the name is looked at.
    body = search_for(elsewhere, "Galactica 1980")
    assert titled(body["did_you_mean_shows"])[:1] == [("Galactica 1980", 1980)]
    assert elsewhere["modelled"] == []


def test_a_year_still_reads_as_a_year_unless_the_whole_name_is_well_known(elsewhere):
    body = search_for(elsewhere, "Chef 2014")
    assert titled(body["did_you_mean"])[:1] == [("Chef", 2014)]


@pytest.mark.parametrize("kwargs, expected", [
    ({}, ("Wonder Woman 1984", None)),
    ({"year": 2020}, ("Wonder Woman 1984", 2020)),   # a year sent alongside a title typed bare
])
def test_asking_for_a_film_we_lack_records_its_whole_name(elsewhere, kwargs, expected):
    result = capture("Wonder Woman 1984", **kwargs)
    assert (result["found"], result["added_to_wanted"]) == (False, True)
    assert wanted_rows() == [expected]


def test_the_shelf_answers_before_tmdb(elsewhere):
    result = capture("Hamlet 2000")
    assert (result["found"], titled(result["matches"])) == (True, [("Hamlet", 2000)])
    assert wanted_rows() == []
