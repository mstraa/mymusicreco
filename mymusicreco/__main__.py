"""Command line:

python -m mymusicreco [serve|profile|weekly|daily|mood NAME] [--refresh] [--export DIR]
python -m mymusicreco spotify [login|status|logout|sync [all|weekly|daily|mood|PLAYLIST_ID ...]] [--refresh]
"""

import argparse
import json
from pathlib import Path

from .config import get_settings
from .models import Playlist
from .playlists import MOODS
from .service import Recommender, to_csv
from .spotify import SpotifyError


def print_playlist(p) -> None:
    print(f"\n== {p.name} ==\n{p.description}")
    for i, t in enumerate(p.tracks, start=1):
        flag = " " if t.known else "*"
        print(f"{i:>3}.{flag} {t.artist} — {t.title}   [{t.source}]")


def main() -> None:
    parser = argparse.ArgumentParser(prog="mymusicreco")
    parser.add_argument("command", choices=["serve", "profile", "weekly", "daily", "mood", "spotify"])
    parser.add_argument("args", nargs="*", help="mood name, or spotify action and targets")
    parser.add_argument("--refresh", action="store_true", help="regenerate instead of using today's/this week's playlist")
    parser.add_argument("--export", metavar="DIR", help="also write each playlist as JSON and CSV into DIR")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--url", help="spotify login: the URL the browser was redirected to")
    parser.add_argument("--from", dest="from_dir", metavar="DIR",
                        help="spotify sync: send the JSON playlists exported in DIR instead of generating them")
    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        uvicorn.run("mymusicreco.web:app", host=args.host, port=args.port)
        return

    reco = Recommender(get_settings())
    if args.command == "spotify":
        try:
            spotify_command(reco, args)
        except SpotifyError as exc:
            raise SystemExit(f"Spotify : {exc}")
        except KeyError as exc:
            raise SystemExit(f"Playlist inconnue : {exc}")
        return
    if args.command == "profile":
        print(json.dumps(reco.profile_summary(), ensure_ascii=False, indent=2))
        return
    if args.command == "weekly":
        playlists = [reco.weekly(args.refresh)]
    elif args.command == "daily":
        playlists = reco.daily(args.refresh)
    else:
        if not args.args or args.args[0] not in MOODS:
            parser.error(f"mood needs a name: {', '.join(MOODS)}")
        playlists = [reco.mood(args.args[0], args.refresh)]

    for p in playlists:
        print_playlist(p)
    print("\n(* = jamais écouté)")
    if args.export:
        out = Path(args.export)
        out.mkdir(parents=True, exist_ok=True)
        for p in playlists:
            (out / f"{p.id}.json").write_text(json.dumps(p.to_dict(), ensure_ascii=False, indent=2))
            (out / f"{p.id}.csv").write_text(to_csv(p))
        print(f"Exporté dans {out}/")


def spotify_command(reco: Recommender, args) -> None:
    action, targets = (args.args[0], args.args[1:]) if args.args else ("status", [])
    auth = reco.spotify().auth
    if action == "login":
        if not args.url:
            print("1. Ouvre cette adresse et accepte :\n\n" + auth.authorize_url() + "\n")
            print("2. Si l'app web ne tourne pas, la page de retour ne s'affichera pas : copie son adresse ici.")
            args.url = input("URL de retour : ")
        auth.complete_from_url(args.url)
        print(f"Connecté à Spotify en tant que {reco.spotify().me().get('display_name')}.")
    elif action == "logout":
        auth.disconnect()
        print("Déconnecté de Spotify.")
    elif action == "status":
        if auth.connected:
            print(f"Connecté à Spotify en tant que {reco.spotify().me().get('display_name')}.")
        else:
            print("Pas connecté : python -m mymusicreco spotify login")
    elif action == "sync":
        if args.from_dir:
            playlists = [Playlist.from_dict(json.loads(f.read_text()))
                         for f in sorted(Path(args.from_dir).glob("*.json"))]
            if targets and targets != ["all"]:
                playlists = [p for p in playlists if p.id in targets or p.id.split("-")[0] in targets]
        else:
            playlists = [p for t in (targets or ["all"]) for p in reco.select(t, args.refresh)]
        for p in playlists:
            r = reco.spotify().sync(p)
            print(f"{r['name']}: {r['matched']}/{r['total']} titres → {r['url']}")
            for m in r["missing"]:
                print(f"    introuvable : {m}")
    else:
        raise SystemExit(f"Unknown spotify action '{action}': login, status, logout, sync")


if __name__ == "__main__":
    main()
