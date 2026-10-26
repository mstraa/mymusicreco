"""Ties the pieces together: profile caching and playlist persistence per period."""

import csv
import io
import threading
import time
from datetime import date

from .config import Settings
from .db import Store
from .history import History
from .lastfm import LastFM
from .models import Playlist
from .playlists import MOODS, Generator
from .spotify import Spotify, SpotifyAuth
from .taste import TasteProfile, build_profile, summarize

PROFILE_TTL = 6 * 3600


def week_period(today: date | None = None) -> str:
    year, week, _ = (today or date.today()).isocalendar()
    return f"{year}-W{week:02d}"


def day_period(today: date | None = None) -> str:
    return (today or date.today()).isoformat()


class Recommender:
    def __init__(self, settings: Settings, fm: LastFM | None = None, store: Store | None = None):
        self.settings = settings
        self.user = settings.user
        self.store = store or Store(settings.db_path)
        self.fm = fm or LastFM(settings.api_key, self.store)
        self._profile: TasteProfile | None = None
        self._profile_at = 0.0
        self._lock = threading.Lock()
        self._spotify: Spotify | None = None
        self.history = History(self.store, self.fm, self.user)

    def profile(self, refresh: bool = False) -> TasteProfile:
        with self._lock:
            if refresh or self._profile is None or time.time() - self._profile_at > PROFILE_TTL:
                self._profile = build_profile(self.fm, self.user)
                self._profile_at = time.time()
            return self._profile

    def profile_summary(self) -> dict:
        return summarize(self.profile())

    def _cached(self, kind: str, period: str, refresh: bool, build) -> list[Playlist]:
        if not refresh:
            data = self.store.playlist_get(kind, period)
            if data is not None:
                return [Playlist.from_dict(p) for p in data]
        playlists = build()
        self.store.playlist_set(kind, period, [p.to_dict() for p in playlists])
        for p in playlists:
            self.history.record(p.to_dict(), period)
        return playlists

    def weekly(self, refresh: bool = False) -> Playlist:
        period = week_period()
        return self._cached("weekly", period, refresh, lambda: [Generator(self.fm, self.profile()).weekly(period)])[0]

    def daily(self, refresh: bool = False) -> list[Playlist]:
        period = day_period()
        return self._cached("daily", period, refresh, lambda: Generator(self.fm, self.profile()).daily(period))

    def mood(self, mood_id: str, refresh: bool = False) -> Playlist:
        if mood_id not in MOODS:
            raise KeyError(mood_id)
        period = day_period()
        return self._cached(f"mood-{mood_id}", period, refresh,
                            lambda: [Generator(self.fm, self.profile()).mood(mood_id, period)])[0]

    def get(self, playlist_id: str, refresh: bool = False) -> Playlist:
        if playlist_id == "weekly":
            return self.weekly(refresh)
        if playlist_id.startswith("mood-"):
            return self.mood(playlist_id.removeprefix("mood-"), refresh)
        if playlist_id.startswith("daily-"):
            for p in self.daily(refresh):
                if p.id == playlist_id:
                    return p
        raise KeyError(playlist_id)

    def select(self, which: str = "all", refresh: bool = False) -> list[Playlist]:
        """'all', 'weekly', 'daily', 'mood' (every mood), or a playlist id like 'daily-2' / 'mood-running'."""
        if which == "all":
            return [self.weekly(refresh), *self.daily(refresh), *(self.mood(m, refresh) for m in MOODS)]
        if which == "daily":
            return self.daily(refresh)
        if which == "mood":
            return [self.mood(m, refresh) for m in MOODS]
        return [self.get(which, refresh)]

    def spotify(self) -> Spotify:
        if self._spotify is None:
            s = self.settings
            auth = SpotifyAuth(s.spotify_client_id, s.spotify_client_secret, s.spotify_redirect_uri, self.store)
            self._spotify = Spotify(auth, self.store)
        return self._spotify


def to_csv(playlist: Playlist) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["position", "artist", "title", "known", "source", "url"])
    for i, t in enumerate(playlist.tracks, start=1):
        writer.writerow([i, t.artist, t.title, "yes" if t.known else "no", t.source, t.url])
    return buf.getvalue()
