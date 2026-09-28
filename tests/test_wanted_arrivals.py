"""Asking for a film the shelves lack, and being told - once, correctly - when it arrives.

The case that prompted these: the library holds Resident Evil (2002) and its
five sequels, and a new film also called Resident Evil opened in cinemas in
September 2026. Asking for it was answered "we already have that" by the 2002
film, and a request for it would have been settled - "it's here" - by the same
old film.
"""

import json

import pytest

from api import arrivals as settling
from api.tmdb_lookup import movie as real_tmdb_movie
from config import config
from scanner import library_index
from tests.fake_plex import film, series

RE_2026 = {"tmdb_id": 1234, "title": "Resident Evil", "year": 2026, "overview": "The 2026 film.",
           "poster": "", "language": "en", "popularity": 90.0, "original_title": "Resident Evil",
           "release_date": "2026-09-18"}
RE_2002 = {"tmdb_id": 1576, "title": "Resident Evil", "year": 2002, "overview": "The 2002 film.",
           "poster": "", "language": "en", "popularity": 20.0, "original_title": "Resident Evil",
           "release_date": "2002-03-15"}
# What TMDb says each id is, for the check that an id sent with an ask is the film named.
TMDB_FILMS = {1234: RE_2026, 1576: RE_2002,
              999: {"tmdb_id": 999, "title": "Resident Evil: Welcome Back", "year": 2025,
                    "original_title": "Resident Evil: Welcome Back"}}


def six_films():
    return [
        film(101, "Resident Evil", 2002, tmdb=1576),
        film(102, "Resident Evil: Apocalypse", 2004, tmdb=1577),
        film(103, "Resident Evil: Extinction", 2007, tmdb=7737),
        film(104, "Resident Evil: Afterlife", 2010, tmdb=35791),
        film(105, "Resident Evil: Retribution", 2012, tmdb=71679),
        film(106, "Resident Evil: The Final Chapter", 2016, tmdb=173897),
    ]


@pytest.fixture
def world(plex, client, monkeypatch, tmp_path):
    """Six Resident Evil films, a synced index, two signed-in members, a throwaway db, recorded pushes."""
    from api import plex_auth, tmdb_lookup
    from api import push as web_push

    monkeypatch.setitem(config, "database", {"path": str(tmp_path / "ops.db")})
    movies = plex.add_section(1, "Movies", "movie", six_films())
    tv = plex.add_section(3, "TV", "show", [])
    library_index.full_sync()
    plex.calls.clear()

    people = {"sess-jules": {"id": "7", "username": "Jules", "role": "shared", "plex_token": "t7"},
              "sess-sam": {"id": "8", "username": "Sam", "role": "shared", "plex_token": "t8"}}
    monkeypatch.setattr(plex_auth, "session_user", lambda db, token: people.get(token))
    monkeypatch.setattr(plex_auth, "allowed_users",
                        lambda max_age=600: {"1": {"username": "owner", "role": "owner"}})
    pushes = []

    def send_event(db, key, plex_user_id, url="/", tag="", **fields):
        pushes.append({"event": key, "to": str(plex_user_id), "tag": tag, **fields})
        return {"sent": 1, "failed": []}

    monkeypatch.setattr(web_push, "send_event", send_event)
    monkeypatch.setattr(web_push, "owner_id", lambda: "1")
    monkeypatch.setattr(tmdb_lookup, "configured", lambda: True)
    monkeypatch.setattr(tmdb_lookup, "suggest", lambda title, hint="", year=None, limit=5:
                        [g for g in (RE_2026, RE_2002) if not year or g["year"] == year])
    monkeypatch.setattr(tmdb_lookup, "suggest_shows", lambda title, hint="", limit=5: [])
    monkeypatch.setattr(tmdb_lookup, "availability", lambda tmdb_id: ("cinema", None))
    monkeypatch.setattr(tmdb_lookup, "movie", lambda tmdb_id: TMDB_FILMS.get(int(tmdb_id)))
    client.set_cookie("plexops_session", "sess-jules")
    # The first read of the library is itself an arrival; check the (empty)
    # list against it now, so nothing a test does later rides on it.
    library_index.on_arrivals(settling.settle_now)
    library_index.deliver_arrivals()
    assert library_index.arrivals_waiting() is False
    return {"client": client, "movies": movies, "tv": tv, "pushes": pushes, "plex": plex}


def ask(client, **body):
    response = client.post("/request/wanted", json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def wanted_rows():
    from db import Database

    db = Database()
    try:
        return [dict(r) for r in db.get_wanted(status=None)]
    finally:
        db.close()


def arrivals(world):
    return [p for p in world["pushes"] if p["event"] == "arrived"]


def look_again():
    """The index's periodic look at what each library added lately, as the worker makes it."""
    with library_index._connect() as conn:
        conn.execute("UPDATE sections SET recent_checked_at = 0")
        conn.commit()
    library_index.recent_sync()


def arrive_2026(world):
    """The 2026 film lands on the shelf and the index notices it without any webhook."""
    world["movies"].items.append(film(107, "Resident Evil", 2026, tmdb=1234, added=1_900_000_000))
    look_again()
    assert library_index.arrivals_waiting() is True     # noticed, and noted in the index file
    assert library_index.deliver_arrivals() is True


# ---- asking ---------------------------------------------------------------------------------

def test_tell_me_when_it_lands_puts_the_2026_film_on_the_list(world):
    answer = ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    assert (answer["found"], answer["added_to_wanted"]) == (False, True)
    assert [(m["title"], m["year"]) for m in answer["close"]][:1] == [("Resident Evil", 2002)]
    [row] = wanted_rows()
    assert (row["title"], row["year"], row["tmdb_id"], row["status"]) == ("Resident Evil", 2026, 1234, "wanted")
    assert [p["event"] for p in world["pushes"]] == ["requested"]


def test_the_year_in_the_title_is_honoured_too(world):
    # What the older page sent, and what a person typing it gets.
    answer = ask(world["client"], title="Resident Evil (2026)")
    assert answer["added_to_wanted"] is True
    assert ask(world["client"], title="Resident Evil")["found"] is True   # no year: the 2002 film is it


def test_the_owner_console_can_add_it_as_well(world):
    body = world["client"].get("/wanted/search?q=Resident Evil (2026)").get_json()
    assert (body["found"], body["added_to_wanted"]) == (False, True)


def test_asking_again_after_a_wrong_arrival_reopens_it(world):
    from db import Database

    ask(world["client"], title="Resident Evil (2026)", year=2026)
    db = Database()
    db.set_wanted_status(wanted_rows()[0]["id"], "acquired")
    db.close()
    answer = ask(world["client"], title="Resident Evil (2026)", year=2026)
    assert (answer["added_to_wanted"], answer.get("reopened")) == (True, True)
    assert wanted_rows()[0]["status"] == "wanted"


# ---- settling -------------------------------------------------------------------------------

def test_the_old_films_never_settle_the_new_one(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    settled = world["client"].post("/wanted/settle").get_json()
    assert settled["settled"] == []
    # Nor does Plex announcing some other film.
    world["movies"].items.append(film(108, "Paddington 2", 2017, added=1_800_000_000))
    payload = {"event": "library.new", "Metadata": {"ratingKey": "108", "type": "movie"}}
    hook = world["client"].post("/plex/webhook", data={"payload": json.dumps(payload)}).get_json()
    assert hook["settled"] == []
    assert arrivals(world) == []
    assert wanted_rows()[0]["status"] == "wanted"


def test_its_real_arrival_is_told_to_everybody_who_asked_without_a_webhook(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    world["client"].set_cookie("plexops_session", "sess-sam")
    again = ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    assert again["already_wanted"] is True
    arrive_2026(world)
    assert sorted(p["to"] for p in arrivals(world)) == ["7", "8"]
    assert {p["film"] for p in arrivals(world)} == {"Resident Evil (2026)"}
    assert wanted_rows()[0]["status"] == "acquired"
    # and only once
    world["client"].post("/wanted/settle")
    assert len(arrivals(world)) == 2


def test_arrival_is_recognised_by_tmdb_id_whatever_plex_calls_it(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    # A name no title search for "Resident Evil" finds: only the id can match it.
    world["movies"].items.append(film(107, "Biohazard", 2026, tmdb=1234, added=1_900_000_000))
    library_index.full_sync()
    world["client"].post("/wanted/settle")
    assert [p["to"] for p in arrivals(world)] == ["7"]


@pytest.mark.parametrize("tmdb_id", [None, "1234"])
def test_a_different_film_a_year_off_never_settles(world, tmdb_id):
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id=tmdb_id)
    # A sequel with a subtitle, a year out: certain by name, but not this film.
    world["movies"].items.append(film(107, "Resident Evil: Welcome Back", 2025, tmdb=999, added=1_900_000_000))
    look_again()
    library_index.deliver_arrivals()
    world["client"].post("/wanted/settle")
    assert arrivals(world) == []
    assert wanted_rows()[0]["status"] == "wanted"


def test_the_same_film_dated_a_year_off_by_plex_still_settles(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026)
    world["movies"].items.append(film(107, "Resident Evil", 2024, added=1_900_000_000))
    look_again()
    library_index.deliver_arrivals()
    assert arrivals(world) == []          # two years out is another film
    world["movies"].items.append(film(108, "Resident Evil", 2025, added=1_900_000_001))
    look_again()
    library_index.deliver_arrivals()
    assert [p["film"] for p in arrivals(world)] == ["Resident Evil (2026)"]


def test_the_year_sent_beats_a_year_in_the_title(world):
    answer = ask(world["client"], title="Resident Evil (2002)", year=2026)
    assert (answer["found"], answer["added_to_wanted"]) == (False, True)
    assert wanted_rows()[0]["year"] == 2026


def test_an_id_sent_later_is_kept_on_a_row_still_waiting(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026)
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    assert wanted_rows()[0]["tmdb_id"] == 1234


def test_an_id_for_another_film_is_not_kept(world):
    # 1576 is the 2002 film, which is on the shelf. Kept, it would settle the
    # 2026 request the next time anything arrived.
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1576")
    assert wanted_rows()[0]["tmdb_id"] is None
    world["client"].set_cookie("plexops_session", "sess-sam")
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1576")
    assert wanted_rows()[0]["tmdb_id"] is None
    world["client"].post("/wanted/settle")
    assert arrivals(world) == []


def test_without_tmdb_an_id_is_not_trusted(world, monkeypatch):
    from api import tmdb_lookup

    monkeypatch.setattr(tmdb_lookup, "configured", lambda: False)
    monkeypatch.setattr(tmdb_lookup, "movie", real_tmdb_movie)
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    assert wanted_rows()[0]["tmdb_id"] is None


def test_a_row_settled_by_its_id_is_not_reopened_by_its_name(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    world["movies"].items.append(film(107, "Biohazard", 2026, tmdb=1234, added=1_900_000_000))
    look_again()
    library_index.deliver_arrivals()
    assert len(arrivals(world)) == 1
    answer = ask(world["client"], title="Resident Evil (2026)", year=2026)
    assert answer["found"] is True and not answer.get("reopened")
    assert wanted_rows()[0]["status"] == "acquired"
    world["client"].post("/wanted/settle")
    assert len(arrivals(world)) == 1
    assert [p["event"] for p in world["pushes"]].count("requested") == 1


def test_a_film_whose_files_are_gone_is_not_an_arrival_by_id(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    world["movies"].items.append(film(107, "Biohazard", 2026, tmdb=1234, files=["/nowhere/Biohazard.mkv"],
                                      added=1_900_000_000))
    look_again()
    library_index.check_files()
    library_index.deliver_arrivals()
    assert arrivals(world) == []


def test_a_film_on_a_drive_that_is_offline_waits(world, monkeypatch, tmp_path):
    from scanner import wanted_search

    copy = tmp_path / "Resident Evil (2026).mkv"
    copy.write_text("")
    offline = {"files": 1, "missing_files": [], "store_offline": True, "all_missing": False,
               "some_missing": False}
    real = wanted_search.files_state
    monkeypatch.setattr(wanted_search, "files_state", lambda files: offline)
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    world["movies"].items.append(film(107, "Resident Evil", 2026, tmdb=1234, files=[str(copy)],
                                      added=1_900_000_000))
    look_again()
    library_index.check_files()
    library_index.deliver_arrivals()
    assert arrivals(world) == []          # not known to play, so not "it's here"
    monkeypatch.setattr(wanted_search, "files_state", real)
    library_index.check_files()           # the drive is back
    assert library_index.arrivals_waiting() is True
    library_index.deliver_arrivals()
    assert [p["to"] for p in arrivals(world)] == ["7"]


def test_a_file_put_back_is_an_arrival_without_a_webhook(world, tmp_path):
    copy = tmp_path / "Heat (1995).mkv"
    world["movies"].items.append(film(110, "Heat", 1995, files=[str(copy)]))
    library_index.full_sync()
    library_index.check_files()
    library_index.deliver_arrivals()
    answer = ask(world["client"], title="Heat (1995)")
    assert (answer["found"], answer["file_missing"], answer["added_to_wanted"]) == (False, True, True)
    copy.write_text("")                   # the owner puts the file back
    library_index.check_files()
    library_index.deliver_arrivals()
    assert [p["film"] for p in arrivals(world)] == ["Heat (1995)"]


def test_a_check_that_fails_is_tried_again_and_nothing_is_lost(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026)
    world["movies"].items.append(film(107, "Resident Evil", 2026, added=1_900_000_000))
    look_again()
    world["movies"].fail = True           # Plex falls over just as the list is checked
    with pytest.raises(Exception):
        library_index.deliver_arrivals()
    assert arrivals(world) == [] and library_index.arrivals_waiting() is True
    assert library_index.deliver_arrivals() is None      # waiting out the pause before trying again
    world["movies"].fail = False
    assert library_index.deliver_arrivals(force=True) is True
    assert [p["to"] for p in arrivals(world)] == ["7"]
    assert library_index.arrivals_waiting() is False


def test_the_worker_notices_and_tells_on_its_own(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026)
    world["movies"].items.append(film(107, "Resident Evil", 2026, added=1_900_000_000))
    with library_index._connect() as conn:
        conn.execute("UPDATE sections SET recent_checked_at = 0")
        conn.commit()
    worker = library_index._Worker()
    worker.round()
    assert (worker.last_error, worker.settle_error) == (None, None)
    assert [p["to"] for p in arrivals(world)] == ["7"]


def test_a_failed_check_is_reported_as_the_list_not_the_index(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026)
    world["movies"].items.append(film(107, "Resident Evil", 2026, added=1_900_000_000))
    look_again()
    world["movies"].fail = True
    worker = library_index._Worker()
    worker.settle()
    assert worker.last_error is None
    assert "wanted list" in worker.settle_error and "Nothing is lost" in worker.settle_error


def test_two_checks_that_both_see_it_tell_them_once(world):
    from db import Database
    from scanner import importer

    ask(world["client"], title="Resident Evil (2026)", year=2026)
    row_id = wanted_rows()[0]["id"]
    first, second = Database(), Database()
    try:
        assert first.claim_arrival(row_id) is not None
        assert second.claim_arrival(row_id) is None
        # The importer finishing the same film afterwards tells nobody again.
        assert importer.close_wanted(second, {"kind": "movie", "title": "Resident Evil", "year": 2026}) is None
    finally:
        first.close()
        second.close()


def test_somebody_asking_as_it_arrives_is_still_told(world, monkeypatch):
    from api import routes
    from db import Database

    ask(world["client"], title="Resident Evil (2026)", year=2026)
    real = routes.search_and_capture

    def arrives_meanwhile(db, *args, **kwargs):
        result = real(db, *args, **kwargs)
        other = Database()      # the index worker's check, settling it between the look and the note
        try:
            row = other.claim_arrival(result["wanted"]["id"])
            settling.web_push.tell_arrived(other, row, settling.label(row))
        finally:
            other.close()
        return result

    monkeypatch.setattr(routes, "search_and_capture", arrives_meanwhile)
    world["client"].set_cookie("plexops_session", "sess-sam")
    ask(world["client"], title="Resident Evil (2026)", year=2026)
    assert sorted(p["to"] for p in arrivals(world)) == ["7", "8"]


def test_nobody_reached_means_the_owner_is_told(world, monkeypatch):
    from api import push as web_push

    ask(world["client"], title="Resident Evil (2026)", year=2026)
    unreached = []
    monkeypatch.setattr(web_push, "send_event", lambda db, key, uid, **kw:
                        unreached.append((key, str(uid))) or {"sent": 0 if str(uid) != "1" else 1})
    arrive_2026(world)
    assert ("arrived", "7") in unreached and ("arrived", "1") in unreached


# ---- series -------------------------------------------------------------------------------------

def test_a_series_is_settled_by_a_series_not_a_film_of_the_same_name(world):
    ask(world["client"], title="Resident Evil", kind="show", season=2)
    world["client"].post("/wanted/settle")
    assert arrivals(world) == []          # the 2002 film is not series 2 of anything
    world["tv"].items.append(series(301, "Resident Evil", 2021, seasons={1: 8}, added=1_900_000_000))
    library_index.full_sync()
    world["client"].post("/wanted/settle")
    assert arrivals(world) == []          # series 1 is here; series 2 is not
    world["tv"].items[0]["seasons"][2] = 8
    library_index.full_sync()
    world["client"].post("/wanted/settle")
    assert [p["film"] for p in arrivals(world)] == ["Resident Evil, series 2"]


def test_asking_for_more_of_a_series_already_here_waits_for_the_next_one(world):
    world["tv"].items.append(series(302, "Blackadder", 1983, seasons={1: 6}, added=1_500_000_000))
    library_index.full_sync()
    answer = ask(world["client"], title="Blackadder", kind="show", year=1983)
    assert answer["season"] == 2
    world["client"].post("/wanted/settle")
    assert arrivals(world) == []          # series 1 was here before they asked
    world["tv"].items[0]["seasons"][2] = 6
    look_again()
    library_index.deliver_arrivals()
    assert [p["film"] for p in arrivals(world)] == ["Blackadder (1983), series 2"]


def test_a_new_season_of_a_series_here_is_an_arrival_without_a_webhook(world):
    world["tv"].items.append(series(301, "Resident Evil", 2021, seasons={1: 8}, added=1_500_000_000))
    library_index.full_sync()
    library_index.deliver_arrivals()
    ask(world["client"], title="Resident Evil", kind="show", season=2, year=2021)
    world["tv"].items[0]["seasons"][2] = 8
    look_again()
    assert library_index.arrivals_waiting() is True
    library_index.deliver_arrivals()
    assert [p["film"] for p in arrivals(world)] == ["Resident Evil (2021), series 2"]


def test_a_series_wrongly_marked_arrived_can_be_asked_for_again(world):
    from db import Database

    ask(world["client"], title="Resident Evil", kind="show", season=2)
    db = Database()
    db.set_wanted_status(wanted_rows()[0]["id"], "acquired")
    db.close()
    answer = ask(world["client"], title="Resident Evil", kind="show", season=2)
    assert (answer["added_to_wanted"], answer.get("reopened")) == (True, True)
    assert wanted_rows()[0]["status"] == "wanted"


def test_a_series_year_typed_after_its_name_is_read(world):
    ask(world["client"], title="Doctor Who 2005", kind="show", season=3)
    ask(world["client"], title="Space: 1999", kind="show", season=1)
    assert {(r["title"], r["year"]) for r in wanted_rows()} == {("Doctor Who", 2005), ("Space: 1999", None)}


def test_one_series_asked_for_with_and_without_its_year_is_one_row(world):
    ask(world["client"], title="Blackadder", kind="show", season=2)
    ask(world["client"], title="Blackadder", kind="show", season=2, year=1983)
    ask(world["client"], title="Blackadder", kind="show", season=2)
    assert [(r["title"], r["year"], r["season"]) for r in wanted_rows()] == [("Blackadder", 1983, 2)]


def test_several_seasons_of_one_series_never_break_the_database(world):
    from db import Database

    for season in (1, 2, 3):
        ask(world["client"], title="Space: 1999", kind="show", season=season)
    ask(world["client"], title="Space: 1999", kind="show")
    Database().close()                    # used to fail on open, taking every page with it
    assert world["client"].get("/request/wanted/list").status_code == 200
    assert {r["title"] for r in wanted_rows()} == {"Space: 1999"}   # not "Space:" from 1999


# ---- a title typed without a year ------------------------------------------------------------------

def test_a_title_we_have_mentions_the_new_one_of_that_name(world, monkeypatch):
    import datetime as real

    class Today(real.date):
        @classmethod
        def today(cls):
            return real.date(2026, 9, 28)

    from api import request_assistant
    monkeypatch.setattr(request_assistant.datetime, "date", Today)
    search = world["client"].post("/request/search", json={"query": "Resident Evil"}).get_json()
    assert search["kind"] == "title"
    assert search["translated_by"] == "title" and search["matches"]
    newer = world["client"].get("/request/newer?q=Resident Evil").get_json()["newer"]
    assert [(g["title"], g["year"], g["release_state"], g["wanted_status"]) for g in newer] == [
        ("Resident Evil", 2026, "cinema", None)]
    ask(world["client"], title="Resident Evil (2026)", year=2026, tmdb_id="1234")
    newer = world["client"].get("/request/newer?q=Resident Evil").get_json()["newer"]
    assert newer[0]["wanted_status"] == "wanted"
    # With a year typed there is nothing to add; long after release, nor is there.
    assert world["client"].get("/request/newer?q=Resident Evil 2002").get_json()["newer"] == []
    monkeypatch.setattr(Today, "today", classmethod(lambda cls: real.date(2029, 1, 1)))
    assert world["client"].get("/request/newer?q=Resident Evil").get_json()["newer"] == []


def test_the_new_one_is_not_offered_once_it_is_on_the_shelf(world, monkeypatch):
    import datetime as real

    class Today(real.date):
        @classmethod
        def today(cls):
            return real.date(2026, 9, 28)

    from api import request_assistant
    monkeypatch.setattr(request_assistant.datetime, "date", Today)
    # Held under another name: known by its TMDb id.
    world["movies"].items.append(film(107, "Biohazard", 2026, tmdb=1234, added=1_900_000_000))
    library_index.full_sync()
    assert world["client"].get("/request/newer?q=Resident Evil").get_json()["newer"] == []


def test_a_subtitled_film_is_not_the_new_one_of_that_name(world, monkeypatch):
    import datetime as real

    from api import request_assistant, tmdb_lookup

    class Today(real.date):
        @classmethod
        def today(cls):
            return real.date(2026, 9, 28)

    raccoon = dict(RE_2026, tmdb_id=4321, title="Resident Evil: Welcome to Raccoon City",
                   original_title="Resident Evil: Welcome to Raccoon City", release_date="2026-06-01")
    monkeypatch.setattr(request_assistant.datetime, "date", Today)
    monkeypatch.setattr(tmdb_lookup, "suggest", lambda title, hint="", year=None, limit=5: [raccoon])
    assert world["client"].get("/request/newer?q=Resident Evil").get_json()["newer"] == []


# ---- the importer and the notification catalogue ------------------------------------------------

def test_the_importer_does_not_mistake_the_new_film_for_the_old(world):
    from scanner import importer

    assert importer.plex_state({"kind": "movie", "title": "Resident Evil", "year": 2026})["in_plex"] is False
    assert importer.plex_state({"kind": "movie", "title": "Resident Evil", "year": 2002})["in_plex"] is True
    world["movies"].items.append(film(120, "Taxi Driver", 2004))
    library_index.full_sync()
    assert importer.plex_state({"kind": "movie", "title": "Taxi", "year": 2004})["in_plex"] is False


# ---- what a member may send ------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["\u00b2", "9" * 30, "-1", "12.5", "٣"])
def test_odd_numbers_are_ignored_not_a_crash(world, bad):
    answer = ask(world["client"], title="Resident Evil (2026)", year=bad, tmdb_id=bad, season=bad)
    assert answer["added_to_wanted"] is True
    [row] = wanted_rows()
    assert (row["year"], row["tmdb_id"]) == (2026, None)


def test_the_answer_does_not_carry_another_members_account_id(world):
    ask(world["client"], title="Resident Evil (2026)", year=2026)
    world["client"].set_cookie("plexops_session", "sess-sam")
    again = ask(world["client"], title="Resident Evil (2026)", year=2026)
    assert again["wanted"]["requested_by_id"] is None
    assert again["wanted"]["requested_by"] == "Jules"


def test_every_notification_has_a_default_wording():
    import config as config_module

    merged = config_module._with_defaults({"plex": {"url": "u", "token": "t"}})
    assert {"arrived", "requested", "file_missing", "message", "test"} <= set(merged["notifications"])


def test_a_notifications_block_is_taken_as_written():
    import config as config_module

    # Leaving an event out has always switched it off; an upgrade must not switch it back on.
    merged = config_module._with_defaults({"plex": {"url": "u", "token": "t"},
                                           "notifications": {"arrived": {"title": "Here"}}})
    assert set(merged["notifications"]) == {"arrived", "message"}
