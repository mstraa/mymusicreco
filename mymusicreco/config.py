import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    api_key: str
    user: str
    db_path: str


def get_settings() -> Settings:
    load_dotenv()
    api_key = os.environ.get("LASTFM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LASTFM_API_KEY is not set (put it in .env or the environment).")
    return Settings(
        api_key=api_key,
        user=os.environ.get("LASTFM_USER", "ImRedCrow"),
        db_path=os.environ.get("MYMUSICRECO_DB", "mymusicreco.db"),
    )
