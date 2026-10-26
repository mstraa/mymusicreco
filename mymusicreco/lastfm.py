"""Thin Last.fm API client with a SQLite-backed cache and a simple rate limit."""

import json
import threading
import time

import httpx

from .db import Store

API_URL = "https://ws.audioscrobbler.com/2.0/"

HOUR = 3600
DAY = 24 * HOUR

# How long each method's answers stay fresh. User data changes often,
# catalog data (tags, similar artists) hardly ever.
TTL = {
    "user.getrecenttracks": 1 * HOUR,
    "user.gettoptracks": 6 * HOUR,
    "user.gettopartists": 6 * HOUR,
    "track.getinfo": 3 * DAY,
    "artist.gettoptracks": 7 * DAY,
    "artist.getsimilar": 30 * DAY,
    "artist.gettoptags": 30 * DAY,
    "track.getsimilar": 30 * DAY,
    "tag.gettoptracks": 7 * DAY,
}


class LastFMError(RuntimeError):
    pass


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class LastFM:
    def __init__(self, api_key: str, store: Store, min_interval: float = 0.2, client: httpx.Client | None = None):
        self.api_key = api_key
        self.store = store
        self.min_interval = min_interval
        self._client = client or httpx.Client(timeout=20, headers={"User-Agent": "MyMusicReco/0.1"})
        self._lock = threading.Lock()
        self._last_call = 0.0

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_call + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def call(self, method: str, use_cache: bool = True, **params) -> dict:
        method = method.lower()
        params = {k: str(v) for k, v in params.items() if v is not None}
        cache_key = method + "?" + json.dumps(params, sort_keys=True)
        cached = self.store.cache_get(cache_key) if use_cache else None
        if cached is not None:
            return cached

        query = {"method": method, "api_key": self.api_key, "format": "json", "autocorrect": "1", **params}
        data: dict = {}
        for attempt in range(4):
            self._throttle()
            try:
                resp = self._client.get(API_URL, params=query)
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 3:
                    raise LastFMError(f"{method}: {exc}") from exc
                time.sleep(1 + attempt)
                continue
            error = data.get("error")
            if error in (8, 11, 16, 29):  # transient / rate limited
                time.sleep(1 + 2 * attempt)
                continue
            if error == 6:  # not found: cache the miss so we stop asking
                data = {}
            elif error:
                raise LastFMError(f"{method}: {data.get('message')}")
            break
        if use_cache:
            self.store.cache_set(cache_key, data, TTL.get(method, DAY))
        return data

    # --- user -----------------------------------------------------------

    def user_top_artists(self, user: str, period: str = "overall", limit: int = 200) -> list[dict]:
        data = self.call("user.getTopArtists", user=user, period=period, limit=limit)
        return _as_list(data.get("topartists", {}).get("artist"))

    def user_top_tracks(self, user: str, period: str = "overall", limit: int = 1000, page: int = 1) -> list[dict]:
        data = self.call("user.getTopTracks", user=user, period=period, limit=limit, page=page)
        return _as_list(data.get("toptracks", {}).get("track"))

    def user_recent_tracks(self, user: str, limit: int = 200) -> list[dict]:
        data = self.call("user.getRecentTracks", user=user, limit=limit)
        return _as_list(data.get("recenttracks", {}).get("track"))

    def user_scrobbles(self, user: str, start: int, end: int, max_pages: int = 100) -> list[dict]:
        """Every scrobble between two UNIX timestamps (no cache: the answer changes as you listen)."""
        tracks: list[dict] = []
        page, pages = 1, 1
        while page <= min(pages, max_pages):
            data = self.call("user.getRecentTracks", use_cache=False, user=user, limit=200, page=page,
                             **{"from": start, "to": end})
            recent = data.get("recenttracks", {})
            pages = int(recent.get("@attr", {}).get("totalPages") or 0)
            tracks += [t for t in _as_list(recent.get("track")) if t.get("date")]  # skip "now playing"
            page += 1
        return tracks

    def track_user_playcount(self, user: str, artist: str, title: str) -> int | None:
        data = self.call("track.getInfo", artist=artist, track=title, username=user)
        track = data.get("track")
        if not track:
            return None
        try:
            return int(track.get("userplaycount", 0))
        except (TypeError, ValueError):
            return None

    # --- catalog --------------------------------------------------------

    def artist_top_tags(self, artist: str) -> list[dict]:
        data = self.call("artist.getTopTags", artist=artist)
        return _as_list(data.get("toptags", {}).get("tag"))

    def artist_similar(self, artist: str, limit: int = 30) -> list[dict]:
        data = self.call("artist.getSimilar", artist=artist, limit=limit)
        return _as_list(data.get("similarartists", {}).get("artist"))

    def artist_top_tracks(self, artist: str, limit: int = 20) -> list[dict]:
        data = self.call("artist.getTopTracks", artist=artist, limit=limit)
        return _as_list(data.get("toptracks", {}).get("track"))

    def track_similar(self, artist: str, title: str, limit: int = 30) -> list[dict]:
        data = self.call("track.getSimilar", artist=artist, track=title, limit=limit)
        return _as_list(data.get("similartracks", {}).get("track"))

    def tag_top_tracks(self, tag: str, limit: int = 200) -> list[dict]:
        data = self.call("tag.getTopTracks", tag=tag, limit=limit)
        return _as_list(data.get("tracks", {}).get("track"))
