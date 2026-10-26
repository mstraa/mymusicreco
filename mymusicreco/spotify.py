"""Spotify Web API: OAuth (authorization code flow), track matching and playlist sync.

Uses the endpoints that Development Mode apps keep after Spotify's February 2026 changes:
POST /me/playlists to create, PUT/POST /playlists/{id}/items to fill, search limited to 10 results.
"""

import base64
import html
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .db import Store
from .models import Playlist, Track, norm

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
SCOPES = "playlist-read-private playlist-modify-private playlist-modify-public"

DAY = 24 * 3600
MATCH_TTL = 30 * DAY
MISS_TTL = 7 * DAY
NAME_PREFIX = "MyMusicReco · "


class SpotifyError(RuntimeError):
    pass


class NotConnected(SpotifyError):
    pass


class SpotifyAuth:
    """Tokens live in the SQLite store so the CLI and the web app share one login."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, store: Store,
                 client: httpx.Client | None = None):
        if not client_id or not client_secret:
            raise SpotifyError("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET are not set (put them in .env).")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.store = store
        self._client = client or httpx.Client(timeout=20)
        self._lock = threading.Lock()

    def authorize_url(self) -> str:
        state = secrets.token_urlsafe(16)
        self.store.kv_set("spotify:state", state)
        return AUTH_URL + "?" + urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": state,
        })

    def complete(self, code: str, state: str | None) -> None:
        expected = self.store.kv_get("spotify:state")
        if not expected or state != expected:
            raise SpotifyError("OAuth state mismatch: start the login again.")
        self.store.kv_set("spotify:state", None)
        self._save(self._token_request({"grant_type": "authorization_code", "code": code,
                                        "redirect_uri": self.redirect_uri}))

    def complete_from_url(self, redirected_url: str) -> None:
        """For logins where the callback can't reach the app: paste the URL the browser ended on."""
        query = parse_qs(urlparse(html.unescape(redirected_url.strip())).query)
        if "error" in query:
            raise SpotifyError(f"Spotify refused the login: {query['error'][0]}")
        if "code" not in query:
            raise SpotifyError("No ?code= in that URL.")
        self.complete(query["code"][0], query.get("state", [None])[0])

    @property
    def connected(self) -> bool:
        return bool(self.store.kv_get("spotify:token"))

    def disconnect(self) -> None:
        self.store.kv_set("spotify:token", None)

    def access_token(self) -> str:
        with self._lock:
            token = self.store.kv_get("spotify:token")
            if not token:
                raise NotConnected("Not connected to Spotify: run `python -m mymusicreco spotify login`.")
            if token["expires_at"] - 60 < time.time():
                fresh = self._token_request({"grant_type": "refresh_token", "refresh_token": token["refresh_token"]})
                fresh.setdefault("refresh_token", token["refresh_token"])
                token = self._save(fresh)
            return token["access_token"]

    def _token_request(self, data: dict) -> dict:
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = self._client.post(TOKEN_URL, data=data, headers={"Authorization": f"Basic {basic}"})
        if resp.status_code != 200:
            raise SpotifyError(f"Spotify token request failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json()

    def _save(self, payload: dict) -> dict:
        token = {
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
            "expires_at": time.time() + int(payload.get("expires_in", 3600)),
        }
        self.store.kv_set("spotify:token", token)
        return token


class Spotify:
    def __init__(self, auth: SpotifyAuth, store: Store, client: httpx.Client | None = None):
        self.auth = auth
        self.store = store
        self._client = client or httpx.Client(timeout=20, base_url=API_URL)

    def request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(5):
            headers = {"Authorization": f"Bearer {self.auth.access_token()}"}
            resp = self._client.request(method, path, headers=headers, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(min(int(resp.headers.get("Retry-After", 1 + attempt)), 30))
                continue
            if resp.status_code >= 400:
                raise SpotifyError(f"{method} {path} → {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.content else {}
        raise SpotifyError(f"{method} {path}: Spotify kept rate limiting, try again later.")

    def me(self) -> dict:
        return self.request("GET", "/me")

    # --- matching -------------------------------------------------------

    def find_track(self, track: Track) -> str | None:
        """Spotify URI for an artist/title pair, or None. Results (and misses) are cached."""
        key = "spotify:search?" + "|".join(track.key)
        cached = self.store.cache_get(key)
        if cached is not None:
            return cached.get("uri")
        uri = None
        for q in (f'track:"{track.title}" artist:"{track.artist}"', f"{track.artist} {track.title}"):
            # market=from_token would need the user-read-private scope
            items = self.request("GET", "/search", params={"q": q, "type": "track", "limit": 10})
            uri = best_match(track, items.get("tracks", {}).get("items", []))
            if uri:
                break
        self.store.cache_set(key, {"uri": uri}, MATCH_TTL if uri else MISS_TTL)
        return uri

    # --- playlists ------------------------------------------------------

    def _followed(self, playlist_id: str) -> bool:
        """Deleting a playlist in Spotify only unfollows it, so check the user's list, not the playlist."""
        offset = 0
        while True:
            page = self.request("GET", "/me/playlists", params={"limit": 50, "offset": offset})
            if any(item and item.get("id") == playlist_id for item in page.get("items", [])):
                return True
            if not page.get("next"):
                return False
            offset += 50

    def sync(self, playlist: Playlist) -> dict:
        """Create the Spotify playlist on first sync, then replace its contents in place."""
        uris, missing = [], []
        for t in playlist.tracks:
            uri = self.find_track(t)
            if uri and uri not in uris:
                uris.append(uri)
            elif not uri:
                missing.append(f"{t.artist} — {t.title}")

        name = NAME_PREFIX + playlist.name
        description = playlist.description[:300]
        id_key = f"spotify:playlist:{playlist.id}"
        sp_id = self.store.kv_get(id_key)
        if sp_id and self._followed(sp_id):
            self.request("PUT", f"/playlists/{sp_id}", json={"name": name, "description": description})
        else:
            created = self.request("POST", "/me/playlists",
                                   json={"name": name, "description": description, "public": False})
            sp_id = created["id"]
            self.store.kv_set(id_key, sp_id)

        self.request("PUT", f"/playlists/{sp_id}/items", json={"uris": uris[:100]})
        for i in range(100, len(uris), 100):
            self.request("POST", f"/playlists/{sp_id}/items", json={"uris": uris[i:i + 100]})

        return {
            "id": playlist.id,
            "name": name,
            "spotify_id": sp_id,
            "url": f"https://open.spotify.com/playlist/{sp_id}",
            "matched": len(uris),
            "total": len(playlist.tracks),
            "missing": missing,
        }


def best_match(track: Track, items: list[dict]) -> str | None:
    """Pick the search result whose artist and title agree with ours, preferring popular ones."""
    want_artist, want_title = track.key
    best, best_score = None, 0.0
    for item in items:
        artists = [norm(a.get("name", "")) for a in item.get("artists", [])]
        title = norm(item.get("name", ""))
        if want_artist not in artists and not any(want_artist in a or a in want_artist for a in artists if a):
            continue
        if title == want_title:
            score = 2.0
        elif want_title in title or title in want_title:
            score = 1.0
        else:
            continue
        score += item.get("popularity", 0) / 1000
        if score > best_score:
            best, best_score = item.get("uri"), score
    return best
