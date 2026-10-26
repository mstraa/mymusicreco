import random
from collections import Counter

from mymusicreco.models import Track, norm
from mymusicreco.playlists import DAILY_SIZE, MOOD_SIZE, WEEKLY_SIZE, pick, theme_match
from mymusicreco.service import to_csv


def test_norm_strips_versions_and_features():
    assert norm("Leave The World Behind - Radio Edit") == "leave the world behind"
    assert norm("Song (feat. Someone)") == "song"
    assert norm("Tell Me Why - 1991 Remix") == "tell me why - 1991 remix"


def test_pick_caps_tracks_per_artist():
    tracks = [Track(artist=f"A{i % 3}", title=f"t{i}", score=1.0) for i in range(30)]
    chosen = pick(tracks, 10, random.Random(1), max_per_artist=2)
    assert len(chosen) == 6
    assert max(Counter(t.artist for t in chosen).values()) == 2


def test_theme_match_groups_subgenres():
    assert theme_match("alternative metal", "metal")
    assert theme_match("rap", "hip-hop")
    assert not theme_match("metalcore", "metal")


def test_profile_excludes_podcasts(reco):
    profile = reco.profile()
    assert "some podcast" in profile.excluded_artists
    assert "some podcast" not in profile.artist_weights
    assert all(norm(t["artist"]) != "some podcast" for t in profile.top_tracks)


def test_profile_uses_at_least_a_month_of_listening(reco):
    periods = []
    for name in ("user_top_artists", "user_top_tracks"):
        original = getattr(reco.fm, name)

        def spy(user, period="overall", *args, _original=original, **kwargs):
            periods.append(period)
            return _original(user, period, *args, **kwargs)

        setattr(reco.fm, name, spy)
    reco.profile()
    assert "7day" not in periods
    assert {"1month", "3month", "6month", "overall"} <= set(periods)


def test_weekly_only_unheard_and_one_per_artist(reco):
    weekly = reco.weekly()
    assert len(weekly.tracks) == WEEKLY_SIZE
    assert all((t.artist, t.title) not in reco.fm.library for t in weekly.tracks)
    assert max(Counter(t.artist for t in weekly.tracks).values()) == 1


def test_weekly_is_stored_for_the_week(reco):
    first = [t.key for t in reco.weekly().tracks]
    calls = reco.fm.calls
    assert [t.key for t in reco.weekly().tracks] == first
    assert reco.fm.calls == calls


def test_daily_mixes_by_genre_with_known_share(reco):
    mixes = reco.daily()
    themes = {m.name.split("· ")[1] for m in mixes}
    assert {"drum and bass", "house", "metal"} <= themes
    for mix in mixes:
        assert len(mix.tracks) == DAILY_SIZE
        known = sum(t.known for t in mix.tracks)
        assert 0.6 <= known / len(mix.tracks) <= 0.8
        prefixes = {t.artist.split()[0] for t in mix.tracks}
        assert len(prefixes) == 1  # one genre per mix


def test_mood_playlist_follows_mood_tags(reco):
    chill = reco.mood("chill")
    assert len(chill.tracks) == MOOD_SIZE
    assert all(t.artist.startswith("chill") for t in chill.tracks)
    assert any(t.known for t in chill.tracks) and any(not t.known for t in chill.tracks)


def test_csv_export(reco):
    lines = to_csv(reco.mood("chill")).strip().splitlines()
    assert lines[0] == "position,artist,title,known,source,url"
    assert len(lines) == MOOD_SIZE + 1
