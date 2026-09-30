"""The front page's poster shelf, and what it costs Plex to fill it."""

from api.request_assistant import showcase
from tests.fake_plex import film, series


def test_the_shelf_asks_plex_once_per_row_and_never_once_per_poster(plex):
    # None of these carries a tagline, a backdrop or a rating, which is what
    # used to make plexapi fetch every poster on the shelf again in full. The
    # fake fails any such fetch, so filling the shelf at all is the test.
    plex.add_section(1, "Movies", "movie",
                     [film(100 + n, f"Film {n}", 2000 + n) for n in range(20)])
    plex.add_section(3, "TV", "show", [series(301, "Blackadder", 1983)])

    rows = showcase(per_row=14, token="someone")

    assert [row["title"] for row in rows] == [
        "Just added", "Best of the shelf", "Series on the shelf", "Nobody has watched these yet"]
    assert len(rows[0]["films"]) == 14
    assert rows[2]["films"][0]["title"] == "Blackadder"
    assert rows[0]["films"][0]["tagline"] == ""
    # Three film rows and one of series: one search each, and nothing else.
    assert [call[0] for call in plex.calls] == ["search"] * 4


def test_playlist_counts_come_from_the_listing_not_a_request_per_list(monkeypatch):
    from types import SimpleNamespace

    from api import user_library

    class Listed:
        playlistType, leafCount, title = "video", 3, "Friday Films"

        def items(self):
            raise AssertionError("a request to Plex for every playlist")

    monkeypatch.setattr(user_library, "as_user",
                        lambda token: SimpleNamespace(playlists=lambda: [Listed()]))
    assert user_library.playlists_for("someone") == [{"title": "Friday Films", "items": 3}]
