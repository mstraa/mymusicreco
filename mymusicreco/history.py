"""Archive of every generated playlist, with how often each track was scrobbled since the playlist was created."""

import time

from .db import Store
from .lastfm import LastFM
from .models import track_key

DAY = 24 * 3600
# Scrobbles can reach Last.fm late (offline phone, delayed sync): each refresh re-reads this much of the past.
LATE_SCROBBLES = 3 * DAY
# Don't ask Last.fm again if the scrobbles were read less than this long ago.
MIN_REFRESH = 5 * 60


def _artist_name(track: dict) -> str:
    artist = track.get("artist", {})
    return artist.get("#text") or artist.get("name", "") if isinstance(artist, dict) else str(artist)


class History:
    def __init__(self, store: Store, fm: LastFM, user: str):
        self.store = store
        self.fm = fm
        self.user = user

    def record(self, playlist: dict, period: str, created_at: float | None = None) -> int:
        return self.store.history_add(playlist["id"], period, created_at or time.time(), playlist)

    def refresh_scrobbles(self, force: bool = False) -> None:
        """Bring the local copy of the scrobbles up to date, from the oldest archived playlist to now."""
        oldest = self.store.history_oldest()
        if oldest is None:
            return
        now = int(time.time())
        covered = self.store.kv_get("scrobbles_covered")  # [start, end] already read from Last.fm
        if covered and not force and now - covered[1] < MIN_REFRESH and covered[0] <= oldest:
            return
        start = int(oldest)
        ranges = [(start, now)]
        if covered:
            ranges = [(covered[1] - LATE_SCROBBLES, now)]
            if start < covered[0]:
                ranges.append((start, covered[0]))
            start = min(start, covered[0])
        for lo, hi in ranges:
            rows = []
            for t in self.fm.user_scrobbles(self.user, max(lo, 0), hi):
                artist, title = track_key(_artist_name(t), t.get("name", ""))
                rows.append((int(t["date"]["uts"]), artist, title))
            self.store.scrobbles_add(rows)
        self.store.kv_set("scrobbles_covered", [start, now])

    def _with_plays(self, entry: dict, counts: dict) -> dict:
        p = entry["playlist"]
        tracks = [{**t, "plays": counts.get(track_key(t["artist"], t["title"]), 0)} for t in p["tracks"]]
        return {
            "id": entry["id"],
            "playlist_id": entry["playlist_id"],
            "period": entry["period"],
            "created_at": entry["created_at"],
            "name": p["name"],
            "description": p["description"],
            "track_count": len(tracks),
            "listened_tracks": sum(1 for t in tracks if t["plays"]),
            "plays": sum(t["plays"] for t in tracks),
            "tracks": tracks,
        }

    def list(self, playlist_id: str | None = None, limit: int = 100, refresh: bool = True) -> list[dict]:
        """Newest first, each with per-track play counts since its creation."""
        if refresh:
            self.refresh_scrobbles()
        entries = self.store.history_list(playlist_id, limit)
        counts: dict[float, dict] = {}
        out = []
        for e in entries:
            if e["created_at"] not in counts:
                counts[e["created_at"]] = self.store.scrobble_counts(e["created_at"])
            out.append(self._with_plays(e, counts[e["created_at"]]))
        return out

    def get(self, entry_id: int, refresh: bool = True) -> dict:
        entry = self.store.history_get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        if refresh:
            self.refresh_scrobbles()
        return self._with_plays(entry, self.store.scrobble_counts(entry["created_at"]))
