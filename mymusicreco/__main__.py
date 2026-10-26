"""Command line: python -m mymusicreco [serve|profile|weekly|daily|mood NAME] [--refresh] [--export DIR]"""

import argparse
import json
from pathlib import Path

from .config import get_settings
from .playlists import MOODS
from .service import Recommender, to_csv


def print_playlist(p) -> None:
    print(f"\n== {p.name} ==\n{p.description}")
    for i, t in enumerate(p.tracks, start=1):
        flag = " " if t.known else "*"
        print(f"{i:>3}.{flag} {t.artist} — {t.title}   [{t.source}]")


def main() -> None:
    parser = argparse.ArgumentParser(prog="mymusicreco")
    parser.add_argument("command", choices=["serve", "profile", "weekly", "daily", "mood"])
    parser.add_argument("mood", nargs="?", choices=list(MOODS))
    parser.add_argument("--refresh", action="store_true", help="regenerate instead of using today's/this week's playlist")
    parser.add_argument("--export", metavar="DIR", help="also write each playlist as JSON and CSV into DIR")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        uvicorn.run("mymusicreco.web:app", host=args.host, port=args.port)
        return

    reco = Recommender(get_settings())
    if args.command == "profile":
        print(json.dumps(reco.profile_summary(), ensure_ascii=False, indent=2))
        return
    if args.command == "weekly":
        playlists = [reco.weekly(args.refresh)]
    elif args.command == "daily":
        playlists = reco.daily(args.refresh)
    else:
        if not args.mood:
            parser.error(f"mood needs a name: {', '.join(MOODS)}")
        playlists = [reco.mood(args.mood, args.refresh)]

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


if __name__ == "__main__":
    main()
