"""Build a taste profile for a Last.fm user: who they play, what tags define them,
and which tracks they already know."""

import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .lastfm import LastFM
from .models import norm, track_key

# Tags that say nothing about the sound.
JUNK_TAGS = {
    "seen live", "favorites", "favourite", "favorite", "my favorite", "favourites", "love",
    "awesome", "beautiful", "cool", "amazing", "best", "spotify", "albums i own", "under 2000 listeners",
    "male vocalists", "female vocalists", "female vocalist", "male vocalist", "vocal art", "all",
    "videos", "fip", "check out", "good", "great", "epic", "genius", "legend", "legends",
}
TAG_ALIASES = {
    "drum n bass": "drum and bass", "drum & bass": "drum and bass", "dnb": "drum and bass", "d&b": "drum and bass",
    "hip hop": "hip-hop", "hiphop": "hip-hop", "rap francais": "french rap", "rap français": "french rap",
    "electronica": "electronic", "electro house": "electro house", "edm": "edm", "chill out": "chillout",
    "post rock": "post-rock", "lo fi": "lo-fi", "lofi": "lo-fi", "trip hop": "trip-hop", "nu metal": "nu metal",
    "alternative": "alternative", "indie pop": "indie pop", "synthpop": "synthpop", "synth-pop": "synthpop",
}
# Tags too broad or not about genre: fine for scoring, bad as a daily-mix theme.
NON_GENRE_TAGS = {
    "french", "uk", "british", "american", "german", "france", "usa", "english", "canadian", "australian",
    "swedish", "dutch", "belgian", "japanese", "00s", "10s", "90s", "80s", "70s", "60s", "2010s", "2020s",
    "electronic", "rock", "alternative", "pop", "indie", "dance", "instrumental", "music", "soundtrack",
}
NON_MUSIC_TAGS = {"podcast", "podcasts", "news", "spoken word", "talk", "comedy", "audiobook", "videos"}
NON_MUSIC_NAME = re.compile(r"podcast|actus du jour|\bnews\b|radio show|audiobook", re.I)

# A week of scrobbles is too little to judge taste: the shortest window is a month.
PERIOD_WEIGHTS = {"1month": 3.0, "3month": 2.0, "6month": 1.5, "overall": 1.0}


def clean_tag(name: str) -> str:
    tag = " ".join(name.lower().split())
    return TAG_ALIASES.get(tag, tag)


@dataclass
class TasteProfile:
    user: str
    artist_weights: dict[str, float] = field(default_factory=dict)  # norm(artist) -> affinity 0..~
    artist_names: dict[str, str] = field(default_factory=dict)  # norm(artist) -> display name
    artist_tags: dict[str, dict[str, float]] = field(default_factory=dict)  # norm(artist) -> tag -> 0..1
    tag_weights: dict[str, float] = field(default_factory=dict)
    known_artists: set[str] = field(default_factory=set)
    known_tracks: set[tuple[str, str]] = field(default_factory=set)
    top_tracks: list[dict] = field(default_factory=list)  # {"artist", "title", "url", "plays", "weight"}
    excluded_artists: set[str] = field(default_factory=set)

    def top_artists(self, n: int) -> list[str]:
        ranked = sorted(self.artist_weights.items(), key=lambda kv: -kv[1])
        return [a for a, _ in ranked[:n]]

    def top_tags(self, n: int, genre_only: bool = False) -> list[str]:
        ranked = sorted(self.tag_weights.items(), key=lambda kv: -kv[1])
        tags = [t for t, _ in ranked if not genre_only or t not in NON_GENRE_TAGS]
        return tags[:n]

    def is_known(self, artist: str, title: str) -> bool:
        return track_key(artist, title) in self.known_tracks

    def tag_affinity(self, tags: dict[str, float]) -> float:
        """How well a tag set matches the user's tag profile (0..1)."""
        if not tags or not self.tag_weights:
            return 0.0
        top = max(self.tag_weights.values())
        score = sum(w * self.tag_weights.get(t, 0.0) / top for t, w in tags.items())
        return min(1.0, score / max(1.0, sum(tags.values())) * 2)


def parse_tags(raw: list[dict], limit: int = 10) -> dict[str, float]:
    tags: dict[str, float] = {}
    for item in raw[:limit * 2]:
        try:
            count = float(item.get("count", 0))
        except (TypeError, ValueError):
            count = 0.0
        name = clean_tag(item.get("name", ""))
        if not name or count <= 0 or name in JUNK_TAGS:
            continue
        tags[name] = max(tags.get(name, 0.0), count / 100.0)
        if len(tags) >= limit:
            break
    return tags


def is_non_music(name: str, tags: dict[str, float]) -> bool:
    if NON_MUSIC_NAME.search(name):
        return True
    return any(t in NON_MUSIC_TAGS for t in tags) or (bool(tags) and set(tags) <= {"videos"})


def fetch_artist_tags(fm: LastFM, artists: list[str], workers: int = 4) -> dict[str, dict[str, float]]:
    with ThreadPoolExecutor(workers) as pool:
        results = pool.map(lambda a: parse_tags(fm.artist_top_tags(a)), artists)
        return {norm(a): tags for a, tags in zip(artists, results)}


def build_profile(fm: LastFM, user: str, library_pages: int = 5, tagged_artists: int = 120) -> TasteProfile:
    profile = TasteProfile(user=user)
    excluded_env = os.environ.get("MYMUSICRECO_EXCLUDE_ARTISTS", "")
    profile.excluded_artists = {norm(a) for a in excluded_env.split(",") if a.strip()}

    # Artist affinity: the last months count more than all-time.
    for period, weight in PERIOD_WEIGHTS.items():
        artists = fm.user_top_artists(user, period=period, limit=200)
        if not artists:
            continue
        top_plays = max(int(a.get("playcount", 0)) for a in artists) or 1
        for a in artists:
            key = norm(a["name"])
            profile.artist_names.setdefault(key, a["name"])
            profile.artist_weights[key] = profile.artist_weights.get(key, 0.0) + weight * int(a.get("playcount", 0)) / top_plays

    for a in fm.user_top_artists(user, period="overall", limit=1000):
        key = norm(a["name"])
        profile.known_artists.add(key)
        profile.artist_names.setdefault(key, a["name"])

    # Known tracks: the most-played part of the library plus recent scrobbles.
    library: list[dict] = []
    for page in range(1, library_pages + 1):
        tracks = fm.user_top_tracks(user, period="overall", limit=1000, page=page)
        library += tracks
        if len(tracks) < 1000:
            break
    for t in library:
        profile.known_tracks.add(track_key(t["artist"]["name"], t["name"]))
    for t in fm.user_recent_tracks(user, limit=200):
        artist = t.get("artist", {})
        artist_name = artist.get("#text") or artist.get("name", "")
        profile.known_tracks.add(track_key(artist_name, t.get("name", "")))

    # Favourite tracks, weighted the same way as artists.
    seen: dict[tuple[str, str], dict] = {}
    for period in PERIOD_WEIGHTS:
        tracks = library if period == "overall" else fm.user_top_tracks(user, period=period, limit=300)
        if not tracks:
            continue
        top_plays = max(int(t.get("playcount", 0)) for t in tracks) or 1
        for t in tracks:
            artist_name = t["artist"]["name"]
            key = track_key(artist_name, t["name"])
            profile.known_tracks.add(key)
            entry = seen.setdefault(key, {
                "artist": artist_name, "title": t["name"], "url": t.get("url", ""), "plays": 0, "weight": 0.0,
            })
            entry["plays"] = max(entry["plays"], int(t.get("playcount", 0)))
            entry["weight"] += PERIOD_WEIGHTS[period] * int(t.get("playcount", 0)) / top_plays

    # Tags of the artists that matter most.
    top = profile.top_artists(tagged_artists)
    profile.artist_tags = fetch_artist_tags(fm, [profile.artist_names[a] for a in top])
    for key in top:
        tags = profile.artist_tags.get(key, {})
        if is_non_music(profile.artist_names[key], tags) or key in profile.excluded_artists:
            profile.excluded_artists.add(key)
            continue
        for tag, w in tags.items():
            profile.tag_weights[tag] = profile.tag_weights.get(tag, 0.0) + w * profile.artist_weights[key]

    for key in list(profile.excluded_artists):
        profile.artist_weights.pop(key, None)
    profile.top_tracks = sorted(
        (t for t in seen.values() if norm(t["artist"]) not in profile.excluded_artists),
        key=lambda t: -t["weight"],
    )
    return profile


def tags_for(fm: LastFM, profile: TasteProfile, artists: list[str]) -> dict[str, dict[str, float]]:
    """Artist tags, reusing those already in the profile."""
    missing = [a for a in artists if norm(a) not in profile.artist_tags]
    if missing:
        profile.artist_tags.update(fetch_artist_tags(fm, missing))
    return {norm(a): profile.artist_tags.get(norm(a), {}) for a in artists}


def summarize(profile: TasteProfile) -> dict:
    by_tag = defaultdict(float, profile.tag_weights)
    return {
        "user": profile.user,
        "top_artists": [profile.artist_names[a] for a in profile.top_artists(20)],
        "top_tags": sorted(by_tag, key=lambda t: -by_tag[t])[:20],
        "known_tracks": len(profile.known_tracks),
        "known_artists": len(profile.known_artists),
        "excluded_artists": sorted(profile.artist_names.get(a, a) for a in profile.excluded_artists),
    }
