"""Franchise gaps for films that share a name with one already held.

Titles alone could not tell Halloween (2018) from Halloween (1978), and since
token_set_ratio and partial_ratio both score 100 when one title contains
another, a held Resident Evil (2002) also hid Resident Evil: Welcome to
Raccoon City and the 2026 Resident Evil. Every one of them belongs in the gap
report, so the year now has to agree as well as the title.
"""

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

BLADE_RUNNER = {"id": 422837, "name": "Blade Runner Collection", "parts": [
    part(78, "Blade Runner", "1982-06-25"),
    part(335984, "Blade Runner 2049", "2017-10-04"),
]}


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
        return sorted({(g["title"], g["detail"]) for g in self.rows
                       if g["gap_type"] == "missing_franchise_entry"})


@pytest.fixture
def tmdb(monkeypatch):
    """Answer TMDb with one collection, whichever held film is looked up."""
    def install(collection):
        def get(path, params=None):
            if path == "/search/movie":
                return {"results": [{"id": 1}]}
            if path == "/movie/1":
                return {"belongs_to_collection": {"id": collection["id"]}}
            if path == f"/collection/{collection['id']}":
                return collection
            if path in ("/movie/1/release_dates", "/movie/1/keywords"):
                return {}
            raise AssertionError(f"unexpected TMDb call: {path}")
        monkeypatch.setattr(gap_analysis, "_tmdb_get", get)
    return install


def analyse(db):
    """Run the whole movie analysis and insist every film got through it.

    run_movie_analysis catches and prints whatever goes wrong with a film, the
    fake's own complaints included, so a broken path shows up only as a film
    that was never counted.
    """
    result = gap_analysis.run_movie_analysis(db, libraries=["Movies"])
    assert result["movies_analyzed"] == len(db.files)
    return db.missing()


def test_a_remake_of_a_held_film_is_still_missing(tmdb):
    tmdb(HALLOWEEN)
    db = Gaps()

    found = gap_analysis.analyze_movie_franchise(
        "Halloween", 1978, "Movies", db, {("halloween", 1978)})

    assert found == 3
    assert db.missing() == [
        ("Halloween", "Release: 2018-10-18"),
        ("Halloween Ends", "Release: 2022-10-12"),
        ("Halloween Kills", "Release: 2021-10-14"),
    ]


def test_films_whose_titles_contain_a_held_one_are_still_missing(tmdb):
    tmdb(RESIDENT_EVIL)
    db = Gaps([
        "/media/Movies/Resident.Evil.2002.1080p.BluRay.mkv",
        "/media/Movies/Resident.Evil.Apocalypse.2004.1080p.BluRay.mkv",
        "/media/Movies/Resident.Evil.Extinction.2007.1080p.BluRay.mkv",
        "/media/Movies/Resident.Evil.Afterlife.2010.1080p.BluRay.mkv",
        "/media/Movies/Resident.Evil.Retribution.2012.1080p.BluRay.mkv",
        "/media/Movies/Resident.Evil.The.Final.Chapter.2016.1080p.BluRay.mkv",
    ])

    assert analyse(db) == [
        ("Resident Evil", "Release: 2026-09-18"),
        ("Resident Evil: Welcome to Raccoon City", "Release: 2021-11-24"),
    ]


def test_the_importers_own_naming_carries_the_year(tmdb):
    tmdb(HALLOWEEN)
    db = Gaps(["/media/Movies/Halloween (1978).mkv"])

    assert analyse(db) == [
        ("Halloween", "Release: 2018-10-18"),
        ("Halloween Ends", "Release: 2022-10-12"),
        ("Halloween Kills", "Release: 2021-10-14"),
    ]


def test_a_title_ending_in_a_number_is_not_mistaken_for_a_year(tmdb):
    tmdb(BLADE_RUNNER)
    db = Gaps([
        "/media/Movies/Blade.Runner.1982.1080p.BluRay.mkv",
        "/media/Movies/Blade Runner 2049.mkv",
    ])

    assert analyse(db) == []


@pytest.mark.parametrize("path, expected", [
    ("/media/Movies/Halloween (1978).mkv", ("Halloween", 1978)),
    ("/media/Movies/Wonder Woman 1984 (2020).mkv", ("Wonder Woman 1984", 2020)),
    ("/media/Movies/Halloween.1978.1080p.BluRay.mkv", ("Halloween", 1978)),
])
def test_parse_movie_title(path, expected):
    assert gap_analysis._parse_movie_title(path) == expected


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
    # 'Blade Runner 2049.mkv' parses as Blade Runner from the year 2049.
    ("blade runner 2049", 2017, {("blade runner", 2049)}, True),
    ("blade runner", 1982, {("blade runner", 2049)}, False),
])
def test_title_owned(title, year, owned, expected):
    assert gap_analysis._title_owned(title, title, year, owned) is expected
