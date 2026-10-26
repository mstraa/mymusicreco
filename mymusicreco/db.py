import json
import sqlite3
import threading
import time


class Store:
    """SQLite store for the API response cache, generated playlists and small settings (kv)."""

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
                """
            )

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
