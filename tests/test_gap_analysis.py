"""Franchise gaps: which parts of a held film's collection are not held.

A part counts as held when its TMDb id is the id of a film on the shelves.
Titles could not decide it: Halloween (2018) has the same title as Halloween
(1978), Resident Evil: Welcome to Raccoon City contains Resident Evil, and
Saw II is one character away from Saw, so every one of them used to be hidden
behind the film it shares a name with.
"""

import re

import pytest

from scanner import gap_analysis


def part(tmdb_id, title, release_date):
    return {"id": tmdb_id, "title": title, "original_title": title, "release_date": release_date}


HALLOWEEN = {"id": 91361, "name": "Halloween Collection", "parts": [
    part(948, "Halloween", "1978-10-25"),
    part(424139, "Halloween", "2018-10-18"),
    part(610253, "Halloween Kills", "2021-10-14"),
    part(616820, "Halloween Ends", "2022-10-12"),
]}

RESIDENT_EVIL = {"id": 17255, "name": "Resident Evil Collection", "parts": [
    part(1576, "Resident Evil", "2002-03-15"),
    part(1577, "Resident Evil: Apocalypse", "2004-09-10"),
    part(7737, "Resident Evil: Extinction", "2007-09-20"),
    part(35791, "Resident Evil: Afterlife", "2010-09-09"),
    part(71679, "Resident Evil: Retribution", "2012-09-12"),
    part(173897, "Resident Evil: The Final Chapter", "2016-12-23"),
    part(460458, "Resident Evil: Welcome to Raccoon City", "2021-11-24"),
    part(1234, "Resident Evil", "2026-09-18"),
]}

SAW = {"id": 656, "name": "Saw Collection", "parts": [
    part(176, "Saw", "2004-10-01"),
    part(215, "Saw II", "2005-10-28"),
    part(214, "Saw III", "2006-10-27"),
    part(1300000, "Saw XI", ""),
]}

BLADE_RUNNER = {"id": 422837, "name": "Blade Runner Collection", "parts": [
    part(78, "Blade Runner", "1982-06-25"),
    part(335984, "Blade Runner 2049", "2017-10-04"),
]}

WONDER_WOMAN = {"id": 468552, "name": "Wonder Woman Collection", "parts": [
    part(297762, "Wonder Woman", "2017-05-30"),
    part(464052, "Wonder Woman 1984", "2020-12-16"),
]}

ALL_FILMS = [
    "/media/Movies/Resident.Evil.2002.1080p.BluRay.mkv",
    "/media/Movies/Resident.Evil.Apocalypse.2004.1080p.BluRay.mkv",
    "/media/Movies/Resident.Evil.Extinction.2007.1080p.BluRay.mkv",
    "/media/Movies/Resident.Evil.Afterlife.2010.1080p.BluRay.mkv",
    "/media/Movies/Resident.Evil.Retribution.2012.1080p.BluRay.mkv",
    "/media/Movies/Resident.Evil.The.Final.Chapter.2016.1080p.BluRay.mkv",
]


class Gaps:
    """Just enough of the database for the analysis to write gaps into."""

    def __init__(self, files=()):
        self.files = [{"file_path": f} for f in files]
        self.rows = []

    def get_all_media(self, library):
        return self.files

    def clear_gaps(self, gap_type=None):
        pass

    def insert_gap(self, **fields):
        self.rows.append(fields)

    def missing(self):
        return sorted((g["title"], g["detail"]) for g in self.rows
                      if g["gap_type"] == "missing_franchise_entry")


def fold(title):
    return re.sub(r"\s+", " ", title.replace(":", "")).strip().lower()


@pytest.fixture
def tmdb(monkeypatch):
    """TMDb as one collection, searched by title and, when given, year.

    Returns the list of paths asked for, so a test can count them.
    """
    calls = []

    def install(collection):
        parts = {p["id"]: p for p in collection["parts"]}

        def get(path, params=None):
            calls.append(path)
            if path == "/search/movie":
                year = params.get("year")
                return {"results": [
                    {"id": p["id"]} for p in collection["parts"]
                    if fold(p["title"]) == fold(params["query"])
                    and (year is None or p["release_date"][:4] == str(year))]}
            movie = re.fullmatch(r"/movie/(\d+)(/release_dates|/keywords)?", path)
            if movie and int(movie.group(1)) in parts:
                if movie.group(2):
                    return {}
                return {"belongs_to_collection": {"id": collection["id"]}}
            if path == f"/collection/{collection['id']}":
                return collection
            raise AssertionError(f"unexpected TMDb call: {path}")

        monkeypatch.setattr(gap_analysis, "_tmdb_get", get)
        return calls
    return install


def analyse(db, limit=None):
    """Run the whole movie analysis and insist every film got through it.

    run_movie_analysis catches and prints whatever goes wrong with a film, the
    fake's own complaints included, so a broken path shows up only as a film
    that was never counted.
    """
    result = gap_analysis.run_movie_analysis(db, libraries=["Movies"], limit=limit)
    assert result["movies_analyzed"] == min(len(db.files), limit or len(db.files))
    assert result["franchise_gaps"] == len(db.missing())
    return db.missing()


def test_a_remake_of_a_held_film_is_still_missing(tmdb):
    tmdb(HALLOWEEN)
    # Named the way the importer and Plex name films.
    db = Gaps(["/media/Movies/Halloween (1978).mkv"])

    assert analyse(db) == [
        ("Halloween", "Release: 2018-10-18"),
        ("Halloween Ends", "Release: 2022-10-12"),
        ("Halloween Kills", "Release: 2021-10-14"),
    ]


def test_films_whose_titles_contain_a_held_one_are_still_missing(tmdb):
    calls = tmdb(RESIDENT_EVIL)
    db = Gaps(ALL_FILMS)

    assert analyse(db) == [
        ("Resident Evil", "Release: 2026-09-18"),
        ("Resident Evil: Welcome to Raccoon City", "Release: 2021-11-24"),
    ]
    assert calls.count(f"/collection/{RESIDENT_EVIL['id']}") == 1


def test_a_limited_run_still_knows_every_held_film(tmdb):
    tmdb(SAW)
    # Only Saw is analysed, but Saw III has to be known as held too. Judged by
    # its title instead, it would pass for Saw II and hide it.
    db = Gaps(["/media/Movies/Saw (2004).mkv", "/media/Movies/Saw III (2006).mkv"])

    assert analyse(db, limit=1) == [
        ("Saw II", "Release: 2005-10-28"),
        ("Saw XI", "Release: TBA"),
    ]


def test_sequels_a_year_apart_and_announced_films_are_missing(tmdb):
    tmdb(SAW)
    db = Gaps(["/media/Movies/Saw (2004).mkv"])

    assert analyse(db) == [
        ("Saw II", "Release: 2005-10-28"),
        ("Saw III", "Release: 2006-10-27"),
        ("Saw XI", "Release: TBA"),
    ]


def test_a_title_ending_in_an_impossible_year_is_searched_for_whole(tmdb):
    tmdb(BLADE_RUNNER)
    db = Gaps(["/media/Movies/Blade Runner 2049.mkv"])

    assert analyse(db) == [("Blade Runner", "Release: 1982-06-25")]


def test_a_film_tmdb_cannot_find_still_hides_its_own_entry(tmdb):
    tmdb(WONDER_WOMAN)
    # Read as Wonder Woman from 1984, which TMDb has no film for.
    db = Gaps([
        "/media/Movies/Wonder.Woman.2017.1080p.BluRay.mkv",
        "/media/Movies/Wonder Woman 1984.mkv",
    ])

    assert analyse(db) == []


@pytest.mark.parametrize("path, expected", [
    ("/media/Movies/Halloween (1978).mkv", ("Halloween", 1978)),
    ("/media/Movies/Wonder Woman 1984 (2020).mkv", ("Wonder Woman 1984", 2020)),
    ("/media/Movies/Halloween.1978.1080p.BluRay.mkv", ("Halloween", 1978)),
    ("/media/Movies/Blade Runner 2049.mkv", ("Blade Runner 2049", None)),
    # A believable year cannot be told from a title's last word.
    ("/media/Movies/Wonder Woman 1984.mkv", ("Wonder Woman", 1984)),
])
def test_parse_movie_title(path, expected):
    assert gap_analysis._parse_movie_title(path) == expected


# The title match, used only for held films TMDb could not find.
@pytest.mark.parametrize("title, year, owned, expected", [
    # A filename dated by local release, a year after TMDb's first release.
    ("halloween", 1978, {("halloween", 1979)}, True),
    ("halloween", 2018, {("halloween", 1978)}, False),
    # partial_ratio catches a number spelled out, but only with the years agreeing.
    ("one hundred and one dalmatians", 1961, {("101 dalmatians", 1961)}, True),
    ("one hundred and one dalmatians", 1961, {("101 dalmatians", None)}, False),
    # With no year to go on, the title alone decides.
    ("halloween", 2018, {("halloween", None)}, True),
    ("halloween", None, {("halloween", 1978)}, True),
    # 'Wonder Woman 1984.mkv' parses as Wonder Woman from the year 1984.
    ("wonder woman 1984", 2020, {("wonder woman", 1984)}, True),
    ("wonder woman", 2017, {("wonder woman", 1984)}, False),
])
def test_title_owned(title, year, owned, expected):
    assert gap_analysis._title_owned(title, title, year, owned) is expected
