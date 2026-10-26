import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse

from .config import get_settings
from .playlists import MOODS
from .service import Recommender, to_csv

STATIC = Path(__file__).parent / "static"


def create_app(recommender: Recommender | None = None) -> FastAPI:
    app = FastAPI(title="MyMusicReco")
    state: dict = {"reco": recommender}

    def reco() -> Recommender:
        if state["reco"] is None:
            state["reco"] = Recommender(get_settings())
        return state["reco"]

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/profile")
    def profile(refresh: bool = False):
        if refresh:
            reco().profile(refresh=True)
        return reco().profile_summary()

    @app.get("/api/moods")
    def moods():
        return [{"id": k, "name": m["name"], "description": m["description"]} for k, m in MOODS.items()]

    @app.get("/api/playlists/weekly")
    def weekly(refresh: bool = False):
        return reco().weekly(refresh).to_dict()

    @app.get("/api/playlists/daily")
    def daily(refresh: bool = False):
        return [p.to_dict() for p in reco().daily(refresh)]

    @app.get("/api/playlists/mood/{mood_id}")
    def mood(mood_id: str, refresh: bool = False):
        if mood_id not in MOODS:
            raise HTTPException(404, f"Unknown mood '{mood_id}'. Try: {', '.join(MOODS)}")
        return reco().mood(mood_id, refresh).to_dict()

    @app.get("/api/playlists/{playlist_id}/export")
    def export(playlist_id: str, format: str = Query("json", pattern="^(json|csv)$")):
        try:
            playlist = reco().get(playlist_id)
        except KeyError:
            raise HTTPException(404, f"Unknown playlist '{playlist_id}'")
        filename = f"{playlist_id}.{format}"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if format == "csv":
            return PlainTextResponse(to_csv(playlist), media_type="text/csv", headers=headers)
        return PlainTextResponse(json.dumps(playlist.to_dict(), ensure_ascii=False, indent=2),
                                 media_type="application/json", headers=headers)

    return app


app = create_app()
