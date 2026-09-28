"""The library index, and the searches that ask it before they ask Plex."""

import json

import pytest

from config import config
from scanner import library_index, paths, wanted_search
from tests.fake_plex import FakeServer, film, series


def shelves(plex):
    movies = plex.add_section(1, "Movies", "movie", [
        film(101, "Resident Evil", 2002, tmdb=1576, directors=["Paul W.S. Anderson"], actors=["Milla Jovovich"]),
        film(102, "Resident Evil: Afterlife", 2010, tmdb=35791, directors=["Paul W.S. Anderson"]),
        film(103, "The Matrix", 1999, tmdb=603, directors=["Lana Wachowski", "Lilly Wachowski"]),
        film(104, "Ocean's Eleven", 2001, tmdb=161, directors=["Steven Soderbergh"]),
        film(105, "Anchorman: The Legend of Ron Burgundy", 2004, tmdb=8699),
        film(106, "Amélie", 2001, original="Le Fabuleux Destin d'Amélie Poulain", tmdb=194),
    ])
    kids = plex.add_section(2, "Kids", "movie", [film(201, "Paddington", 2014, tmdb=116149)])
    tv = plex.add_section(3, "TV", "show", [series(301, "Blackadder", 1983, seasons={1: 6, 2: 6, 3: 6, 4: 6},
                                                    tmdb=7246)])
    return movies, kids, tv


def synced(plex):
    sections = shelves(plex)
    library_index.full_sync()
    plex.calls.clear()
    return sections


def titles(results):
    return [m["title"] for m in results]


def age_recent_look():
    with library_index._connect() as conn:
        conn.execute("UPDATE sections SET recent_checked_at = 0")
        conn.commit()


# ---- building and keeping it -------------------------------------------------

def test_a_full_sync_reads_every_library(plex):
    shelves(plex)
    assert library_index.full_sync() == {"Movies": 6, "Kids": 1, "TV": 1}
    state = library_index.status()
    assert (state["films"], state["series"], state["seasons"]) == (7, 1, 4)
    assert state["people"] >= 5
    assert state["ready"] is True


def test_a_resync_forgets_what_plex_dropped_and_keeps_what_it_could_not_read(plex):
    movies, kids, _tv = synced(plex)
    movies.items = [spec for spec in movies.items if spec["key"] != "102"]
    kids.fail = True
    report = library_index.full_sync()
    assert report["Kids"].startswith("failed")
    # Afterlife is gone; Paddington stays, because Kids could not be read to the end.
    assert library_index.status()["films"] == 6
    kids.fail = False
    assert titles(wanted_search.search_plex("paddington")) == ["Paddington"]
    assert wanted_search.search_plex("afterlife") == []


def test_a_new_episode_brings_its_series_up_to_date(plex):
    _movies, _kids, tv = synced(plex)
    tv.items[0]["seasons"][5] = 6
    assert library_index.add_or_refresh("3010501") == "Blackadder"
    assert wanted_search.seasons_held("301") == [1, 2, 3, 4, 5]


def test_the_worker_builds_it_then_tops_it_up(plex):
    movies, _kids, _tv = shelves(plex)
    worker = library_index._Worker()
    worker.tick()
    assert library_index.status()["films"] == 7
    age_recent_look()
    movies.items.append(film(107, "Paddington 2", 2017, added=1_700_000_000))
    worker.tick()
    assert library_index.status()["films"] == 8


# ---- what a search asks Plex ---------------------------------------------------------------------

def test_a_title_the_library_lacks_is_answered_without_asking_plex(plex):
    synced(plex)
    assert wanted_search.search_plex("Paddington 2") == []
    assert wanted_search.search_shows("Paddington 2") == []
    assert plex.searches() == []


def test_a_title_it_holds_is_confirmed_only_where_it_is_held(plex):
    synced(plex)
    assert titles(wanted_search.search_plex("resident evil")) == ["Resident Evil", "Resident Evil: Afterlife"]
    assert {library for _kind, library, _query in plex.searches()} == {"Movies"}


def test_search_results_are_never_fetched_again_one_by_one(plex):
    # The fake fails any request plexapi makes on its own, and none of these
    # films has a content rating, an audience rating or an original title.
    synced(plex)
    assert titles(wanted_search.search_plex("resident evil")) == ["Resident Evil", "Resident Evil: Afterlife"]
    assert titles(wanted_search.search_shows("blackadder")) == ["Blackadder"]


def test_names_are_matched_the_way_people_type_them(plex):
    synced(plex)
    assert [(m["title"], m["confidence"]) for m in wanted_search.search_plex("matrix")] == [("The Matrix", 2)]
    assert titles(wanted_search.search_plex("oceans eleven")) == ["Ocean's Eleven"]
    assert wanted_search.search_plex("oceans eleven")[0]["confidence"] == 2
    # Found by its original title, which Plex's own title search does not look at.
    assert [(m["title"], m["confidence"]) for m in
            wanted_search.search_plex("le fabuleux destin d'amelie poulain")] == [("Amélie", 2)]


def test_something_added_a_moment_ago_is_looked_for_before_a_miss_is_believed(plex):
    movies, _kids, _tv = synced(plex)
    age_recent_look()
    movies.items.append(film(107, "Paddington 2", 2017, added=1_700_000_000))
    assert titles(wanted_search.search_plex("Paddington 2")) == ["Paddington 2"]
    assert any(kind == "list" and "sort=addedAt:desc" in key for kind, _library, key in plex.calls)


def test_an_index_that_cannot_confirm_a_miss_lets_plex_answer(plex):
    movies, _kids, _tv = synced(plex)
    age_recent_look()
    movies.items.append(film(107, "Paddington 2", 2017, added=1_700_000_000))

    def refuse(*args, **kwargs):
        raise ConnectionError("no answer")

    movies.fetchItems = refuse
    assert titles(wanted_search.search_plex("Paddington 2")) == ["Paddington 2"]


def test_a_missing_folder_is_never_created_and_searches_carry_on(plex, tmp_path, monkeypatch):
    shelves(plex)
    elsewhere = tmp_path / "Volumes" / "OWC STX" / "PlexGet" / "library_index.db"
    monkeypatch.setitem(config, "index", {"path": str(elsewhere), "background": False})
    monkeypatch.setattr(library_index, "_availability", {"at": 0.0, "verdict": (False, "")})
    ok, why = library_index.available()
    assert ok is False and "does not exist" in why
    assert titles(wanted_search.search_plex("paddington")) == ["Paddington"]
    assert plex.searches()
    assert not elsewhere.parent.exists() and not (tmp_path / "Volumes").exists()
    assert library_index.status()["available"] is False


# ---- files on disk -------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    folder = tmp_path / "media"
    folder.mkdir()
    monkeypatch.setitem(config, "paths", {"roots": [str(folder)]})
    monkeypatch.setattr(paths, "_stores", None)
    monkeypatch.setattr(paths, "_state", {})
    return folder


def test_files_are_checked_in_the_background_and_a_search_reads_the_answers(plex, store, monkeypatch):
    here = store / "Resident Evil (2002).mkv"
    here.write_text("film")
    gone = store / "Resident Evil Afterlife (2010).mkv"
    plex.add_section(1, "Movies", "movie", [film(101, "Resident Evil", 2002, files=[str(here)]),
                                            film(102, "Resident Evil: Afterlife", 2010, files=[str(gone)])])
    library_index.full_sync()
    assert library_index.check_files() == {"ok": 1, "missing": 1, "unknown": 0}

    def disk(*args):
        pytest.fail("a search looked at the disk itself")

    monkeypatch.setattr(wanted_search, "files_state", disk)
    monkeypatch.setattr(wanted_search, "availability_of", disk)
    results = wanted_search.search_plex("resident evil")
    assert {m["title"]: m["file_missing"] for m in results} == {
        "Resident Evil": False, "Resident Evil: Afterlife": True}


def test_a_drive_that_cannot_be_read_is_unknown_not_missing(plex, store):
    # An empty folder where the drive should be: the drive has gone, not the film.
    plex.add_section(1, "Movies", "movie", [film(101, "Resident Evil", 2002, files=[str(store / "RE.mkv")])])
    library_index.full_sync()
    assert library_index.check_files() == {"ok": 0, "missing": 0, "unknown": 1}
    assert wanted_search.search_plex("resident evil")[0]["file_missing"] is False


def test_a_check_that_hangs_gives_up_and_says_unknown(monkeypatch):
    monkeypatch.setattr(wanted_search, "_wedged_until", 0.0)
    monkeypatch.setattr(paths, "probe_timeout", lambda: 0.2)

    def hang(files):
        import time
        time.sleep(5)

    monkeypatch.setattr(wanted_search, "files_state", hang)
    verdict = wanted_search.availability_of(["/Volumes/Gone/film.mkv"])
    assert verdict["all_missing"] is False and verdict["store_offline"] is True
    # and the next film does not wait its turn behind the same hung drive
    monkeypatch.setattr(wanted_search, "files_state", lambda files: pytest.fail("tried the hung drive again"))
    assert wanted_search.availability_of(["/Volumes/Gone/other.mkv"])["store_offline"] is True


# ---- the other questions it answers --------------------------------------------------------------

def test_suggestions_already_on_the_shelves_are_recognised_without_searching(plex):
    synced(plex)
    verdicts = library_index.owned([
        {"tmdb_id": 603, "title": "Matrix, The", "year": 1999},   # by TMDb id
        {"tmdb_id": None, "title": "Ocean's Eleven", "year": 2001},  # by name and year
        {"tmdb_id": 346648, "title": "Paddington 2", "year": 2017},  # not here
        {"tmdb_id": None, "title": "Resident Evil", "year": 2026},  # same name, another film
    ], "movie", ["1", "2"])
    assert verdicts == [True, True, False, False]
    assert plex.searches() == []


def test_people_are_found_without_listing_every_actor(plex):
    from api import request_assistant

    synced(plex)
    assert library_index.people_named("soderbergh", ["1", "2"], "director") == ["Steven Soderbergh"]
    assert sorted(library_index.people_named("wachowski", ["1"], "director")) == [
        "Lana Wachowski", "Lilly Wachowski"]
    sections = wanted_search.movie_sections()
    assert request_assistant._resolve_person("soderbergh", sections, "director") == ["Steven Soderbergh"]
    assert not [call for call in plex.calls if call[0] == "choices"]


def test_a_misspelt_title_is_offered_as_a_maybe_and_only_where_the_person_can_see_it(plex, monkeypatch):
    from api import request_assistant

    movies, _kids, _tv = synced(plex)
    assert library_index.near_misses("anchroman", "movie", ["1", "2"])[0][1] == \
        "Anchorman: The Legend of Ron Burgundy"
    owner = request_assistant.misspelt("padington")
    assert [(m["title"], m["confidence"]) for m in owner] == [("Paddington", 1)]

    # A member whose share leaves out the Kids library is never offered it.
    member = FakeServer()
    member.add_section(1, "Movies", "movie", movies.items)
    monkeypatch.setattr(wanted_search, "server_for", lambda token=None: member if token == "member" else plex)
    assert request_assistant.misspelt("padington", "member") == []


def test_the_folding_rules(plex):
    fold = wanted_search.fold
    assert fold("Ocean's Eleven") == fold("oceans eleven")
    assert fold("Don’t Look Up") == fold("dont look up")
    assert fold("Fast & Furious") == fold("fast and furious")
    assert wanted_search.best_confidence("taxi", "Taxi Driver") == 1
    assert wanted_search.best_confidence("the odyssey", "The Odyssey: The Making of an Epic") == 1


# ---- through the routes -------------------------------------------------------------------------

@pytest.fixture
def member(monkeypatch, client):
    from api import plex_auth

    person = {"id": "7", "username": "Jules", "role": "shared", "plex_token": "member-token"}
    monkeypatch.setattr(plex_auth, "session_user", lambda db, token: person if token == "sess" else None)
    client.set_cookie("plexops_session", "sess")
    return person


def test_a_film_we_lack_is_answered_without_the_model(plex, client, member, monkeypatch):
    from api import request_assistant, tmdb_lookup

    synced(plex)
    monkeypatch.setattr(tmdb_lookup, "suggest", lambda title, hint="", year=None, limit=5: [
        {"tmdb_id": 1234, "title": "Resident Evil", "year": 2026, "overview": "", "poster": "",
         "language": "en", "popularity": 90.0, "original_title": "Resident Evil"}])
    monkeypatch.setattr(tmdb_lookup, "suggest_shows", lambda title, hint="", limit=5: [])
    monkeypatch.setattr(tmdb_lookup, "availability", lambda tmdb_id: ("cinema", "2026-12-01"))
    monkeypatch.setattr(request_assistant, "translate", lambda *a, **k: pytest.fail("asked the model"))

    response = client.post("/request/search", json={"query": "Resident evil 2026"})
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["kind"] == "title_missing"
    assert body["matches"] == []
    assert body["did_you_mean"][0]["release_state"] == "cinema"
    assert titles(body["close"]) == ["Resident Evil", "Resident Evil: Afterlife"]


def test_a_mood_is_not_mistaken_for_a_film_of_the_same_name(monkeypatch):
    from api import request_assistant, tmdb_lookup

    monkeypatch.setattr(tmdb_lookup, "suggest", lambda *a, **k: [
        {"tmdb_id": 1, "title": "Comedy", "year": 2011, "popularity": 50.0, "original_title": "Comedy"}])
    monkeypatch.setattr(tmdb_lookup, "suggest_shows", lambda *a, **k: [])
    assert request_assistant.names_a_film("comedy") is False
    assert request_assistant.names_a_film("something to make me laugh") is False


def test_a_mood_search_after_a_title_miss_does_not_search_by_title_again(monkeypatch):
    from api import request_assistant

    monkeypatch.setattr(request_assistant, "search_by_title", lambda *a, **k: pytest.fail("title twice"))
    monkeypatch.setattr(request_assistant, "search_by_person", lambda *a, **k: pytest.fail("person twice"))
    monkeypatch.setattr(request_assistant, "off_topic", lambda text: True)
    assert request_assistant.search("something gentle", tried_title=True)["kind"] == "cheeky"


def test_plex_announcing_a_film_brings_it_in_before_the_wanted_list_is_checked(
        plex, client, monkeypatch, tmp_path):
    from api import push as web_push
    from db import Database

    monkeypatch.setitem(config, "database", {"path": str(tmp_path / "ops.db")})
    _movies, kids, _tv = synced(plex)
    db = Database()
    db.insert_wanted("Paddington 2", 2017, "Paddington 2 (2017)")
    db.close()
    kids.items.append(film(202, "Paddington 2", 2017, tmdb=346648, added=1_700_000_000))
    monkeypatch.setattr(web_push, "owner_id", lambda: "1")
    monkeypatch.setattr(web_push, "send_event", lambda *a, **k: {"sent": 1})

    payload = {"event": "library.new", "Metadata": {"ratingKey": "202", "type": "movie"}}
    response = client.post("/plex/webhook", data={"payload": json.dumps(payload)})
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["indexed"] == "Paddington 2"
    assert body["settled"] == ["Paddington 2 (2017)"]


def test_the_index_routes_are_the_owners(plex, client, monkeypatch):
    synced(plex)
    state = client.get("/index/status")
    assert state.status_code == 200 and state.get_json()["films"] == 7
    outsider = client.get("/index/status", headers={"X-Forwarded-For": "203.0.113.9"})
    assert outsider.status_code == 403
    asked = []
    monkeypatch.setattr(library_index, "request_sync", lambda full=False: asked.append(full) or "started")
    assert client.post("/index/sync", json={"full": True}).get_json()["sync"] == "started"
    assert asked == [True]
