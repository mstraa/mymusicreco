"""Playlist generators: weekly discovery, daily mixes and mood/activity playlists."""

import random
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from .lastfm import LastFM
from .models import Playlist, Track, norm
from .taste import NON_GENRE_TAGS, TasteProfile, is_non_music, tags_for

WEEKLY_SIZE = 30
DAILY_SIZE = 25
DAILY_MIXES = 6
DAILY_KNOWN_SHARE = 0.7
MOOD_SIZE = 30
MOOD_KNOWN_SHARE = 0.5

# Each mood is a set of weighted tags; "avoid" tags push artists out.
MOODS: dict[str, dict] = {
    "running": {
        "name": "Running",
        "description": "Énergie et tempo élevé pour courir.",
        "seed_tags": ["running", "workout", "energetic"],
        "tags": {
            "drum and bass": 1.0, "neurofunk": 1.0, "liquid funk": 0.7, "jungle": 0.8, "breakbeat": 0.8,
            "electro house": 0.9, "big beat": 0.9, "electro": 0.8, "house": 0.6, "techno": 0.7, "dubstep": 0.8,
            "electronic rock": 0.7, "edm": 0.8, "dance": 0.6, "french house": 0.7, "electroclash": 0.7,
            "punk": 0.7, "punk rock": 0.7, "nu metal": 0.8, "metal": 0.6, "alternative metal": 0.7, "hard rock": 0.7,
            "rap metal": 0.8, "hip-hop": 0.5, "rap": 0.5, "trap": 0.5, "running": 1.0, "workout": 1.0,
            "energetic": 1.0, "upbeat": 0.8, "synthwave": 0.5, "darksynth": 0.8, "psytrance": 0.9, "trance": 0.6,
        },
        "avoid": {"ambient", "acoustic", "folk", "classical", "piano", "sad", "slow", "lo-fi", "chillout", "downtempo"},
    },
    "focus": {
        "name": "Focus",
        "description": "Peu de paroles, ambiance régulière pour se concentrer.",
        "seed_tags": ["focus", "study", "instrumental"],
        "tags": {
            "instrumental": 1.0, "ambient": 1.0, "post-rock": 0.9, "lo-fi": 1.0, "chillhop": 1.0, "downtempo": 0.8,
            "electronica": 0.7, "idm": 0.8, "minimal": 0.7, "deep house": 0.6, "classical": 0.8, "piano": 0.9,
            "modern classical": 1.0, "neoclassical": 1.0, "soundtrack": 0.7, "study": 1.0, "focus": 1.0,
            "jazz": 0.5, "nu jazz": 0.7, "trip-hop": 0.6, "melodic techno": 0.6, "organic house": 0.7,
        },
        "avoid": {"metal", "punk", "rap", "hip-hop", "hardcore", "screamo", "metalcore", "nu metal", "french rap"},
    },
    "chill": {
        "name": "Chill",
        "description": "Pour décompresser.",
        "seed_tags": ["chill", "chillout", "relax"],
        "tags": {
            "chillout": 1.0, "chill": 1.0, "downtempo": 1.0, "trip-hop": 0.9, "lo-fi": 0.9, "chillhop": 0.9,
            "electro swing": 0.6, "nu jazz": 0.8, "acoustic": 0.8, "folk": 0.7, "indie folk": 0.8, "soul": 0.7,
            "neo-soul": 0.8, "jazz": 0.6, "dream pop": 0.8, "indie pop": 0.5, "relax": 1.0, "mellow": 1.0,
            "deep house": 0.6, "ambient": 0.6, "singer-songwriter": 0.6, "bossa nova": 0.8,
        },
        "avoid": {"metal", "punk", "hardcore", "drum and bass", "neurofunk", "dubstep", "screamo", "metalcore"},
    },
}


JUNK_TITLE = re.compile(r"official (music )?video|teledysk|lyric video|\(video\)|\bvisualizer\b|full album|\blive at\b", re.I)


def rng_for(*parts: str) -> random.Random:
    return random.Random(":".join(parts))


def weighted_order(items: list, weights: list[float], rng: random.Random) -> list:
    """Order items by weighted random sampling without replacement (Efraimidis–Spirakis)."""
    keyed = [(rng.random() ** (1.0 / max(w, 1e-6)), i) for i, w in enumerate(weights)]
    return [items[i] for _, i in sorted(keyed, reverse=True)]


def pick(candidates: list[Track], n: int, rng: random.Random, max_per_artist: int = 2,
         exclude: set | None = None, accept=None) -> list[Track]:
    """Pick n tracks, favouring high scores, with an artist cap and no duplicates."""
    chosen: list[Track] = []
    seen = set(exclude or ())
    per_artist: Counter = Counter()
    for track in weighted_order(candidates, [t.score for t in candidates], rng):
        if len(chosen) >= n:
            break
        artist = norm(track.artist)
        if track.key in seen or per_artist[artist] >= max_per_artist:
            continue
        seen.add(track.key)
        if accept is not None and not accept(track):
            continue
        per_artist[artist] += 1
        chosen.append(track)
    return chosen


def spread_artists(tracks: list[Track], rng: random.Random) -> list[Track]:
    """Shuffle, then avoid the same artist twice in a row where possible."""
    pool = tracks[:]
    rng.shuffle(pool)
    out: list[Track] = []
    while pool:
        for i, t in enumerate(pool):
            if not out or norm(out[-1].artist) != norm(t.artist):
                out.append(pool.pop(i))
                break
        else:
            out.append(pool.pop(0))
    return out


# Close genres that should share one daily mix.
THEME_FAMILIES = {"rap": "hip-hop", "french rap": "hip-hop", "neurofunk": "drum and bass", "liquid funk": "drum and bass"}


def theme_match(tag: str, theme: str) -> bool:
    """True when tag is the theme or a sub-genre of it ("alternative metal" for "metal")."""
    tag = THEME_FAMILIES.get(tag, tag)
    return tag == theme or tag.endswith(" " + theme) or tag.startswith(theme + " ")


def theme_score(tags: dict[str, float], theme: str) -> float:
    return max((w for t, w in tags.items() if theme_match(t, theme)), default=0.0)


def mood_fit(tags: dict[str, float], mood: dict) -> float:
    score = sum(w * mood["tags"].get(t, 0.0) for t, w in tags.items())
    penalty = sum(w for t, w in tags.items() if t in mood["avoid"])
    return max(0.0, score - 1.5 * penalty)


class Generator:
    def __init__(self, fm: LastFM, profile: TasteProfile, workers: int = 4):
        self.fm = fm
        self.profile = profile
        self.workers = workers

    # --- shared building blocks ----------------------------------------

    def _map(self, fn, items):
        with ThreadPoolExecutor(self.workers) as pool:
            return list(pool.map(fn, items))

    def similar_artists(self, seeds: dict[str, float], per_seed: int = 30) -> dict[str, float]:
        """norm(artist) -> score, from Last.fm similar artists of the weighted seeds."""
        names = [self.profile.artist_names.get(s, s) for s in seeds]
        results = self._map(lambda a: self.fm.artist_similar(a, limit=per_seed), names)
        scores: dict[str, float] = defaultdict(float)
        for seed, similar in zip(seeds, results):
            for s in similar:
                key = norm(s["name"])
                self.profile.artist_names.setdefault(key, s["name"])
                try:
                    match = float(s.get("match", 0))
                except (TypeError, ValueError):
                    match = 0.0
                scores[key] += match * seeds[seed]
        for key in list(scores):
            if key in seeds or key in self.profile.excluded_artists:
                del scores[key]
        return dict(scores)

    def artist_tracks(self, artists: dict[str, float], per_artist: int = 10, source: str = "") -> list[Track]:
        """Top tracks of the given artists, scored by artist score and track rank."""
        names = [self.profile.artist_names.get(a, a) for a in artists]
        results = self._map(lambda a: self.fm.artist_top_tracks(a, limit=per_artist), names)
        tracks: list[Track] = []
        for (key, score), top in zip(artists.items(), results):
            for rank, t in enumerate(top):
                if JUNK_TITLE.search(t.get("name", "")):
                    continue
                artist = t.get("artist", {}).get("name") or self.profile.artist_names.get(key, key)
                tracks.append(Track(
                    artist=artist, title=t["name"], url=t.get("url", ""),
                    source=source or f"top titre de {artist}",
                    known=self.profile.is_known(artist, t["name"]),
                    score=score / (1 + 0.25 * rank),
                ))
        return tracks

    def is_unheard(self, track: Track) -> bool:
        """Double-check with Last.fm that the user never scrobbled the track."""
        plays = self.fm.track_user_playcount(self.profile.user, track.artist, track.title)
        return not plays

    def known_tracks_for(self, artists: set[str]) -> list[Track]:
        return [
            Track(artist=t["artist"], title=t["title"], url=t["url"], source="dans ta bibliothèque",
                  known=True, score=t["weight"])
            for t in self.profile.top_tracks if norm(t["artist"]) in artists
        ]

    # --- weekly discovery ----------------------------------------------

    def weekly(self, period: str) -> Playlist:
        rng = rng_for("weekly", self.profile.user, period)
        top = self.profile.top_artists(40)
        weights = [self.profile.artist_weights[a] for a in top]
        seeds = weighted_order(top, weights, rng)[:15]

        # A few fresh artists per seed, so one dense scene (say metalcore) can't take over the list.
        names = [self.profile.artist_names[a] for a in seeds]
        similar_lists = self._map(lambda a: self.fm.artist_similar(a, limit=30), names)
        per_seed: list[dict[str, float]] = []
        claimed: set[str] = set(top)
        for similar in similar_lists:
            picks: dict[str, float] = {}
            for s in similar:
                key = norm(s["name"])
                if key in claimed or key in self.profile.excluded_artists:
                    continue
                self.profile.artist_names.setdefault(key, s["name"])
                try:
                    match = float(s.get("match", 0))
                except (TypeError, ValueError):
                    match = 0.0
                picks[key] = match * (0.35 if key in self.profile.known_artists else 1.0)
            best = dict(sorted(picks.items(), key=lambda kv: -kv[1])[:5])
            claimed |= set(best)
            per_seed.append(best)

        all_artists = [self.profile.artist_names[a] for bucket in per_seed for a in bucket]
        tags = tags_for(self.fm, self.profile, all_artists)
        buckets: list[list[Track]] = []
        for seed, bucket in zip(seeds, per_seed):
            scored = {}
            for key, score in bucket.items():
                artist_tags = tags.get(key, {})
                if not is_non_music(self.profile.artist_names[key], artist_tags):
                    scored[key] = score * (0.5 + self.profile.tag_affinity(artist_tags))
            source = f"proche de {self.profile.artist_names[seed]}"
            buckets.append([t for t in self.artist_tracks(scored, per_artist=8, source=source) if not t.known])

        # Round-robin over seeds, one track per artist, each checked as never scrobbled.
        tracks: list[Track] = []
        orders = [weighted_order(b, [t.score for t in b], rng) for b in buckets]
        used_artists: set[str] = set()
        checks = 0
        while len(tracks) < WEEKLY_SIZE and any(orders) and checks < 150:
            for order in orders:
                while order and len(tracks) < WEEKLY_SIZE:
                    t = order.pop(0)
                    if norm(t.artist) in used_artists:
                        continue
                    checks += 1
                    if self.is_unheard(t):
                        used_artists.add(norm(t.artist))
                        tracks.append(t)
                        break
        return Playlist(
            id="weekly",
            name="Découvertes de la semaine",
            description=f"{len(tracks)} titres jamais écoutés, d'après tes artistes du moment (semaine {period}).",
            tracks=spread_artists(tracks, rng),
        )

    # --- daily mixes ---------------------------------------------------

    def daily_clusters(self, max_mixes: int = DAILY_MIXES) -> list[tuple[str, list[str]]]:
        """Group the user's top artists by their dominant genre tag."""
        artists = [a for a in self.profile.top_artists(120) if self.profile.artist_tags.get(a)]
        themes: list[str] = []
        for tag in self.profile.top_tags(30, genre_only=True):
            tag = THEME_FAMILIES.get(tag, tag)
            # "alternative metal" belongs to the "metal" mix rather than getting its own.
            if tag not in themes and not any(theme_match(tag, t) for t in themes):
                themes.append(tag)
            if len(themes) >= 12:
                break
        clusters: dict[str, list[str]] = defaultdict(list)
        for a in artists:
            tags = self.profile.artist_tags[a]
            scores = {t: theme_score(tags, t) * self.profile.tag_weights.get(t, 0.0) ** 0.3 for t in themes}
            best = max(scores, key=scores.get, default=None)
            if best and theme_score(tags, best) >= 0.2:
                clusters[best].append(a)
        ranked = sorted(
            (c for c in clusters.items() if len(c[1]) >= 3),
            key=lambda c: -sum(self.profile.artist_weights[a] for a in c[1]),
        )
        return ranked[:max_mixes]

    def daily(self, period: str) -> list[Playlist]:
        mixes = []
        for i, (theme, artists) in enumerate(self.daily_clusters(), start=1):
            rng = rng_for("daily", self.profile.user, period, theme)
            artist_set = set(artists)
            n_known = round(DAILY_SIZE * DAILY_KNOWN_SHARE)

            known = self.known_tracks_for(artist_set)
            if len(known) < n_known:  # pad with the artists' hits the user already played
                known += [t for t in self.artist_tracks({a: self.profile.artist_weights[a] for a in artists}) if t.known]
            chosen = pick(known, n_known, rng, max_per_artist=max(3, n_known // len(artists) + 2))

            seeds = {a: self.profile.artist_weights[a] for a in artists[:6]}
            similar = self.similar_artists(seeds, per_seed=20)
            best = dict(sorted(similar.items(), key=lambda kv: -kv[1])[:15])
            tags = tags_for(self.fm, self.profile, [self.profile.artist_names[a] for a in best])
            on_theme = {a: s for a, s in best.items() if theme_score(tags.get(a, {}), theme) > 0.1} or best
            new = [t for t in self.artist_tracks(on_theme, per_artist=6, source=f"nouveau · {theme}") if not t.known]
            chosen += pick(new, DAILY_SIZE - n_known, rng, max_per_artist=2, exclude={t.key for t in chosen})
            if len(chosen) < DAILY_SIZE:  # small library for this theme: fill with more new tracks
                chosen += pick(new, DAILY_SIZE - len(chosen), rng, max_per_artist=3, exclude={t.key for t in chosen})

            names = [self.profile.artist_names[a] for a in artists[:3]]
            mixes.append(Playlist(
                id=f"daily-{i}",
                name=f"Daily Mix {i} · {theme}",
                description=f"{', '.join(names)} et d'autres. ~70 % de titres connus, ~30 % de nouveautés.",
                tracks=spread_artists(chosen, rng),
            ))
        return mixes

    # --- mood / activity -----------------------------------------------

    def mood(self, mood_id: str, period: str) -> Playlist:
        mood = MOODS[mood_id]
        rng = rng_for("mood", mood_id, self.profile.user, period)

        fits = {a: mood_fit(self.profile.artist_tags.get(a, {}), mood) for a in self.profile.top_artists(120)}
        fitting = {a: f for a, f in fits.items() if f >= 0.3}
        n_known = round(MOOD_SIZE * MOOD_KNOWN_SHARE)

        known = self.known_tracks_for(set(fitting))
        for t in known:
            t.score *= fitting[norm(t.artist)]
            t.source = f"connu · {mood['name'].lower()}"
        chosen = pick(known, n_known, rng, max_per_artist=2)

        # New tracks: artists similar to the best-fitting ones, kept only if they fit the mood too.
        seeds = dict(sorted(((a, f * self.profile.artist_weights[a]) for a, f in fitting.items()), key=lambda kv: -kv[1])[:12])
        similar = self.similar_artists(seeds, per_seed=20) if seeds else {}
        best = dict(sorted(similar.items(), key=lambda kv: -kv[1])[:40])
        tags = tags_for(self.fm, self.profile, [self.profile.artist_names[a] for a in best])
        new_artists = {}
        for a, s in best.items():
            f = mood_fit(tags.get(a, {}), mood)
            if f >= 0.3 and not is_non_music(self.profile.artist_names[a], tags.get(a, {})):
                new_artists[a] = s * f
        new = [t for t in self.artist_tracks(new_artists, per_artist=6, source=f"nouveau · {mood['name'].lower()}") if not t.known]

        # Plus Last.fm's own mood tag charts, filtered by taste.
        for seed_tag in mood["seed_tags"][:2]:
            tagged = self.fm.tag_top_tracks(seed_tag, limit=150)
            artists = list({t["artist"]["name"] for t in tagged})[:60]
            artist_tags = tags_for(self.fm, self.profile, artists)
            for rank, t in enumerate(tagged):
                a = norm(t["artist"]["name"])
                at = artist_tags.get(a, {})
                affinity = self.profile.tag_affinity(at) + (0.5 if a in self.profile.known_artists else 0.0)
                if affinity < 0.35 or a in self.profile.excluded_artists or mood_fit(at, mood) < 0.2:
                    continue
                new.append(Track(
                    artist=t["artist"]["name"], title=t["name"], url=t.get("url", ""),
                    source=f"tag « {seed_tag} »", known=self.profile.is_known(t["artist"]["name"], t["name"]),
                    score=affinity / (1 + rank / 50),
                ))

        chosen += pick(new, MOOD_SIZE - len(chosen), rng, max_per_artist=2, exclude={t.key for t in chosen})
        if len(chosen) < MOOD_SIZE:  # not enough new material: top up with known tracks
            chosen += pick(known, MOOD_SIZE - len(chosen), rng, max_per_artist=3, exclude={t.key for t in chosen})
        return Playlist(id=f"mood-{mood_id}", name=mood["name"], description=mood["description"],
                        tracks=spread_artists(chosen, rng))
