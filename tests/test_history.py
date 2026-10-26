import time

import httpx
from fastapi.testclient import TestClient

from mymusicreco.db import Store
from mymusicreco.lastfm import LastFM
from mymusicreco.web import create_app


def test_every_generation_is_archived(reco):
    reco.weekly()
    reco.weekly()  # same week: reused, not archived again
    reco.weekly(refresh=True)
    reco.daily()
    entries = reco.history.list(refresh=False)
    assert [e["playlist_id"] for e in entries].count("weekly") == 2
    assert {e["playlist_id"] for e in entries} >= {"weekly", "daily-1", "daily-2"}
    weekly = reco.history.list("weekly", refresh=False)
    assert len(weekly) == 2 and weekly[0]["created_at"] >= weekly[1]["created_at"]
    assert weekly[0]["track_count"] == len(weekly[0]["tracks"]) > 0


def test_plays_counted_per_track_after_creation(reco):
    playlist = reco.mood("chill")
    created = int(time.time()) - 1000
    entry_id = reco.history.record(playlist.to_dict(), "2026-10-26", created)
    first, second = playlist.tracks[0], playlist.tracks[1]
    reco.fm.scrobbles = [
        (created - 100, first.artist, first.title),  # before creation: ignored
        (created + 10, first.artist, first.title),
        (created + 20, first.artist.upper(), f"{first.title} - Radio Edit"),  # same track once normalized
        (created + 30, second.artist, second.title),
        (created + 40, "someone", "else"),
    ]
    e = reco.history.get(entry_id)
    plays = [t["plays"] for t in e["tracks"]]
    assert plays[:2] == [2, 1] and sum(plays) == 3
    assert e["listened_tracks"] == 2 and e["plays"] == 3


def test_scrobbles_are_read_incrementally(reco, monkeypatch):
    reco.weekly()
    oldest = reco.store.history_oldest()
    reco.history.list()
    assert reco.fm.scrobble_requests == [(int(oldest), reco.fm.scrobble_requests[0][1])]
    reco.history.list()  # just read: no new request
    assert len(reco.fm.scrobble_requests) == 1

    later = time.time() + 3600
    monkeypatch.setattr(time, "time", lambda: later)
    reco.history.list()
    start, end = reco.fm.scrobble_requests[-1]
    assert end == int(later) and start < int(oldest)  # re-reads a margin for late scrobbles

    # An imported playlist older than what was read: the missing range is fetched too.
    older = int(oldest - 10 * 24 * 3600)
    reco.history.record(reco.weekly().to_dict(), "2026-W01", older)
    reco.history.list()
    assert reco.fm.scrobble_requests[-1][0] == older
    assert reco.store.kv_get("scrobbles_covered")[0] == older


def test_existing_playlists_are_backfilled(tmp_path):
    path = str(tmp_path / "old.db")
    store = Store(path)
    store.playlist_set("daily", "2026-10-25", [
        {"id": "daily-1", "name": "Daily Mix 1", "description": "", "tracks": []},
        {"id": "daily-2", "name": "Daily Mix 2", "description": "", "tracks": []},
    ])
    store._conn.execute("DELETE FROM history")
    store._conn.commit()
    reopened = Store(path)
    assert sorted(e["playlist_id"] for e in reopened.history_list()) == ["daily-1", "daily-2"]
    assert len(Store(path).history_list()) == 2  # only once


def test_user_scrobbles_pages_and_skips_now_playing(tmp_path):
    pages = {
        "1": [{"name": "Now", "artist": {"#text": "A"}, "@attr": {"nowplaying": "true"}},
              {"name": "One", "artist": {"#text": "A"}, "date": {"uts": "200"}}],
        "2": [{"name": "Two", "artist": {"#text": "B"}, "date": {"uts": "100"}}],
    }
    seen = []

    def handler(request):
        params = request.url.params
        seen.append((params["from"], params["to"], params["page"]))
        return httpx.Response(200, json={"recenttracks": {"track": pages[params["page"]],
                                                          "@attr": {"totalPages": "2"}}})

    fm = LastFM("k", Store(str(tmp_path / "c.db")), min_interval=0,
                client=httpx.Client(transport=httpx.MockTransport(handler)))
    tracks = fm.user_scrobbles("u", 50, 300)
    assert [t["name"] for t in tracks] == ["One", "Two"]
    assert seen == [("50", "300", "1"), ("50", "300", "2")]
    fm.user_scrobbles("u", 50, 300)
    assert len(seen) == 4  # never cached


def test_history_api(reco):
    client = TestClient(create_app(reco))
    client.get("/api/playlists/weekly")
    entries = client.get("/api/history").json()
    assert entries[0]["playlist_id"] == "weekly"
    assert "plays" in entries[0]["tracks"][0]
    assert client.get(f"/api/history/{entries[0]['id']}").json()["name"] == entries[0]["name"]
    assert client.get("/api/history?playlist=daily-1").json() == []
    assert client.get("/api/history/9999").status_code == 404
