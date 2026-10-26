import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from mymusicreco.models import Playlist, Track
from mymusicreco.spotify import API_URL, Spotify, SpotifyAuth, SpotifyError, best_match
from mymusicreco.web import create_app


class FakeSpotifyServer:
    """Answers the token, search and playlist endpoints the exporter uses."""

    def __init__(self):
        self.playlists: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.token_grants: list[str] = []
        self.created = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path == "/api/token":
            form = parse_qs(request.content.decode())
            self.token_grants.append(form["grant_type"][0])
            return httpx.Response(200, json={"access_token": "tok", "refresh_token": "ref", "expires_in": 3600})
        path = path.removeprefix("/v1")
        if path == "/me":
            return httpx.Response(200, json={"id": "me", "display_name": "Alex"})
        if path == "/search":
            q = request.url.params["q"]
            if "Nobody" in q:
                return httpx.Response(200, json={"tracks": {"items": []}})
            artist = q.split('artist:"')[1].rstrip('"') if 'artist:"' in q else q.split()[0]
            title = q.split('track:"')[1].split('"')[0] if 'track:"' in q else ""
            items = [
                {"uri": "spotify:track:wrong", "name": title, "artists": [{"name": "Someone Else"}], "popularity": 90},
                {"uri": f"spotify:track:{artist}-{title}".replace(" ", "_"), "name": f"{title} - Radio Edit",
                 "artists": [{"name": artist}], "popularity": 50},
            ]
            return httpx.Response(200, json={"tracks": {"items": items}})
        if path == "/me/playlists" and request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": i} for i in self.playlists], "next": None})
        if path == "/me/playlists" and request.method == "POST":
            body = json.loads(request.content)
            self.created += 1
            pid = f"pl{self.created}"
            self.playlists[pid] = {"name": body["name"], "items": []}
            return httpx.Response(201, json={"id": pid})
        parts = path.strip("/").split("/")
        if parts[0] == "playlists":
            pl = self.playlists.get(parts[1])
            if pl is None:
                return httpx.Response(404, json={"error": {"status": 404}})
            if len(parts) == 2:
                if request.method == "PUT":
                    pl["name"] = json.loads(request.content)["name"]
                    return httpx.Response(200)
                return httpx.Response(200, json={"id": parts[1]})
            uris = json.loads(request.content)["uris"]
            if request.method == "PUT":
                pl["items"] = list(uris)
            else:
                pl["items"] += uris
            return httpx.Response(201, json={"snapshot_id": "x"})
        return httpx.Response(404)


@pytest.fixture
def server():
    return FakeSpotifyServer()


@pytest.fixture
def spotify(reco, server):
    transport = httpx.MockTransport(server)
    auth = SpotifyAuth("cid", "secret", "http://127.0.0.1:8000/spotify/callback", reco.store,
                       client=httpx.Client(transport=transport))
    sp = Spotify(auth, reco.store, client=httpx.Client(transport=transport, base_url=API_URL))
    reco._spotify = sp
    return sp


def login(sp: Spotify) -> None:
    state = parse_qs(urlparse(sp.auth.authorize_url()).query)["state"][0]
    # pasted from a chat, the & may come back HTML-escaped
    sp.auth.complete_from_url(f"http://127.0.0.1:8000/spotify/callback?code=abc&amp;state={state}")


def test_login_rejects_bad_state(spotify):
    spotify.auth.authorize_url()
    with pytest.raises(SpotifyError):
        spotify.auth.complete("abc", "forged")
    assert not spotify.auth.connected


def test_login_and_token_refresh(spotify, server):
    login(spotify)
    assert spotify.auth.connected
    assert spotify.me()["display_name"] == "Alex"
    token = spotify.store.kv_get("spotify:token")
    token["expires_at"] = 0
    spotify.store.kv_set("spotify:token", token)
    spotify.me()
    assert server.token_grants == ["authorization_code", "refresh_token"]


def test_best_match_requires_artist_and_title():
    items = [
        {"uri": "a", "name": "Song", "artists": [{"name": "Other"}], "popularity": 99},
        {"uri": "b", "name": "Song (feat. X)", "artists": [{"name": "Band"}], "popularity": 10},
        {"uri": "c", "name": "Different", "artists": [{"name": "Band"}], "popularity": 99},
    ]
    assert best_match(Track("Band", "Song"), items) == "b"
    assert best_match(Track("Nobody", "Song"), items) is None


def test_sync_creates_then_replaces_in_place(spotify, server):
    login(spotify)
    tracks = [Track(f"Artist {i}", f"Song {i}") for i in range(130)] + [Track("Nobody", "Ghost")]
    first = spotify.sync(Playlist("weekly", "Découvertes", "desc", tracks))
    assert first["matched"] == 130 and first["missing"] == ["Nobody — Ghost"]
    assert server.playlists[first["spotify_id"]]["name"] == "MyMusicReco · Découvertes"
    assert len(server.playlists[first["spotify_id"]]["items"]) == 130

    searches = sum(1 for c in server.calls if c[1].endswith("/search"))
    second = spotify.sync(Playlist("weekly", "Découvertes", "desc", tracks[:5]))
    assert second["spotify_id"] == first["spotify_id"]
    assert len(server.playlists) == 1
    assert len(server.playlists[first["spotify_id"]]["items"]) == 5
    assert sum(1 for c in server.calls if c[1].endswith("/search")) == searches  # matches are cached


def test_sync_recreates_deleted_playlist(spotify, server):
    login(spotify)
    p = Playlist("mood-chill", "Chill", "desc", [Track("A", "B")])
    first = spotify.sync(p)
    del server.playlists[first["spotify_id"]]
    assert spotify.sync(p)["spotify_id"] != first["spotify_id"]


def test_web_sync(reco, spotify):
    client = TestClient(create_app(reco))
    assert client.get("/api/spotify/status").json() == {"configured": True, "connected": False}
    assert client.post("/api/spotify/sync/weekly").status_code == 401
    login(spotify)
    assert client.get("/api/spotify/status").json()["user"] == "Alex"
    results = client.post("/api/spotify/sync/all").json()
    assert {r["id"] for r in results} >= {"weekly", "daily-1", "mood-running", "mood-focus", "mood-chill"}
    assert all(r["matched"] == r["total"] for r in results)
    assert client.post("/api/spotify/sync/nope").status_code == 404
