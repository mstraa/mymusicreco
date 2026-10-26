import json
import sqlite3
import threading
import time


class Store:
    """SQLite store for the API response cache, generated playlists, their history, scrobbles and settings (kv)."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS api_cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS playlists (
                    kind TEXT NOT NULL,
                    period TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (kind, period)
                );
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS history_created ON history (created_at);
                CREATE TABLE IF NOT EXISTS scrobbles (
                    ts INTEGER NOT NULL,
                    artist TEXT NOT NULL,
                    title TEXT NOT NULL,
                    PRIMARY KEY (ts, artist, title)
                );
                """
            )
        self._backfill_history()

    def _backfill_history(self) -> None:
        """Playlists generated before the history existed become its first entries."""
        with self._lock:
            if self._conn.execute("SELECT 1 FROM history LIMIT 1").fetchone():
                return
            rows = self._conn.execute("SELECT period, created_at, data FROM playlists ORDER BY created_at").fetchall()
            for period, created_at, data in rows:
                for p in json.loads(data):
                    self._conn.execute(
                        "INSERT INTO history (playlist_id, period, created_at, data) VALUES (?, ?, ?, ?)",
                        (p["id"], period, created_at, json.dumps(p)),
                    )
            self._conn.commit()

    def cache_get(self, key: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM api_cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None or row[1] < time.time():
            return None
        return json.loads(row[0])

    def cache_set(self, key: str, value, ttl: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO api_cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time() + ttl),
            )
            self._conn.commit()

    def playlist_get(self, kind: str, period: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM playlists WHERE kind = ? AND period = ?", (kind, period)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def playlist_set(self, kind: str, period: str, data) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO playlists (kind, period, created_at, data) VALUES (?, ?, ?, ?)",
                (kind, period, time.time(), json.dumps(data)),
            )
            self._conn.commit()

    def kv_get(self, key: str):
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def kv_set(self, key: str, value) -> None:
        """Store a JSON value; None deletes the key."""
        with self._lock:
            if value is None:
                self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            else:
                self._conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))
            self._conn.commit()

    # --- history ------------------------------------------------------------

    def history_add(self, playlist_id: str, period: str, created_at: float, data) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO history (playlist_id, period, created_at, data) VALUES (?, ?, ?, ?)",
                (playlist_id, period, created_at, json.dumps(data)),
            )
            self._conn.commit()
            return cur.lastrowid

    def history_list(self, playlist_id: str | None = None, limit: int = 100) -> list[dict]:
        """Newest first."""
        sql = "SELECT id, playlist_id, period, created_at, data FROM history"
        args: tuple = ()
        if playlist_id:
            sql += " WHERE playlist_id = ?"
            args = (playlist_id,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (*args, limit)).fetchall()
        return [self._history_row(r) for r in rows]

    def history_get(self, entry_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, playlist_id, period, created_at, data FROM history WHERE id = ?", (entry_id,)
            ).fetchone()
        return self._history_row(row) if row else None

    def history_oldest(self) -> float | None:
        with self._lock:
            row = self._conn.execute("SELECT MIN(created_at) FROM history").fetchone()
        return row[0]

    @staticmethod
    def _history_row(row) -> dict:
        return {"id": row[0], "playlist_id": row[1], "period": row[2], "created_at": row[3],
                "playlist": json.loads(row[4])}

    # --- scrobbles (normalized artist/title, see models.track_key) ------------

    def scrobbles_add(self, rows: list[tuple[int, str, str]]) -> None:
        with self._lock:
            self._conn.executemany("INSERT OR IGNORE INTO scrobbles (ts, artist, title) VALUES (?, ?, ?)", rows)
            self._conn.commit()

    def scrobble_counts(self, since: float) -> dict[tuple[str, str], int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT artist, title, COUNT(*) FROM scrobbles WHERE ts >= ? GROUP BY artist, title", (since,)
            ).fetchall()
        return {(a, t): n for a, t, n in rows}
