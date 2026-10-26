from fastapi.testclient import TestClient

from mymusicreco.web import create_app


def test_api_endpoints(reco):
    client = TestClient(create_app(reco))
    assert client.get("/").status_code == 200
    assert client.get("/api/profile").json()["user"] == "tester"
    assert client.get("/api/playlists/weekly").json()["id"] == "weekly"
    assert len(client.get("/api/playlists/daily").json()) >= 3
    assert client.get("/api/playlists/mood/running").status_code == 200
    assert client.get("/api/playlists/mood/nope").status_code == 404

    csv_resp = client.get("/api/playlists/daily-1/export?format=csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert client.get("/api/playlists/weekly/export?format=json").json()["tracks"]
    assert client.get("/api/playlists/unknown/export").status_code == 404
