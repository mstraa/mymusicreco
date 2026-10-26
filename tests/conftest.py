import pytest

from mymusicreco.config import Settings
from mymusicreco.db import Store
from mymusicreco.service import Recommender

GENRES = {
    "dnb": ["drum and bass", "electronic"],
    "house": ["house", "electronic", "dance"],
    "metal": ["metal", "alternative metal", "rock"],
    "chill": ["chillout", "downtempo", "trip-hop"],
}


def artist_genre(name: str) -> str:
    return name.split()[0].lower()


class FakeLastFM:
    """In-memory stand-in for the Last.fm client: artists are named '<genre> <n>'."""

    def __init__(self):
        self.calls = 0
        # The user knows artists 1-5 of each genre and their first 3 tracks, plus a podcast.
        self.known_artists = [f"{g} {i}" for g in GENRES for i in range(1, 6)] + ["Some Podcast"]
        self.library = [(a, f"{a} song {j}") for a in self.known_artists for j in range(1, 4)]
        self.scrobbles: list[tuple[int, str, str]] = []  # (ts, artist, title)
        self.scrobble_requests: list[tuple[int, int]] = []

    def _track(self, artist, title, plays=10):
        return {"name": title, "artist": {"name": artist}, "playcount": str(plays), "url": f"https://last.fm/{artist}/{title}"}

    def _count(self):
        self.calls += 1

    def user_top_artists(self, user, period="overall", limit=200):
        self._count()
        return [{"name": a, "playcount": str(100 - i)} for i, a in enumerate(self.known_artists[:limit])]

    def user_top_tracks(self, user, period="overall", limit=1000, page=1):
        self._count()
        if page > 1:
            return []
        return [self._track(a, t, 50 - i % 50) for i, (a, t) in enumerate(self.library[:limit])]

    def user_recent_tracks(self, user, limit=200):
        self._count()
        return []

    def user_scrobbles(self, user, start, end, max_pages=100):
        self._count()
        self.scrobble_requests.append((start, end))
        return [{"name": t, "artist": {"#text": a}, "date": {"uts": str(ts)}}
                for ts, a, t in self.scrobbles if start <= ts <= end]

    def track_user_playcount(self, user, artist, title):
        self._count()
        return 5 if (artist, title) in self.library else 0

    def artist_top_tags(self, artist):
        self._count()
        if "Podcast" in artist:
            return [{"name": "podcast", "count": 100}]
        tags = GENRES.get(artist_genre(artist), [])
        return [{"name": t, "count": 100 - 20 * i} for i, t in enumerate(tags)]

    def artist_similar(self, artist, limit=30):
        self._count()
        genre = artist_genre(artist)
        if genre not in GENRES:
            return []
        names = [f"{genre} {i}" for i in range(1, 40) if f"{genre} {i}" != artist]
        return [{"name": n, "match": str(1 - i / 50)} for i, n in enumerate(names)][:limit]

    def artist_top_tracks(self, artist, limit=20):
        self._count()
        return [self._track(artist, f"{artist} song {j}") for j in range(1, limit + 1)]

    def track_similar(self, artist, title, limit=30):
        self._count()
        return []

    def tag_top_tracks(self, tag, limit=200):
        self._count()
        return [self._track(f"chill {i}", f"chill {i} song 9") for i in range(1, 20)]


@pytest.fixture
def reco(tmp_path):
    settings = Settings(api_key="test", user="tester", db_path=str(tmp_path / "test.db"))
    return Recommender(settings, fm=FakeLastFM(), store=Store(settings.db_path))
