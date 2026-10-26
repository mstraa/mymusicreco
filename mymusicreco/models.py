import re
from dataclasses import asdict, dataclass

_NOISE = re.compile(r"\s*[\(\[](feat\.?|ft\.?|with|remaster|remastered|radio edit|original mix)[^\)\]]*[\)\]]", re.I)
_DASH_SUFFIX = re.compile(r"\s+-\s+(\d{4}\s+)?(remaster(ed)?|radio edit|original mix|single version|.*\bedit).*$", re.I)


def norm(text: str) -> str:
    text = _NOISE.sub("", text)
    text = _DASH_SUFFIX.sub("", text)
    return " ".join(text.lower().split())


def track_key(artist: str, title: str) -> tuple[str, str]:
    return norm(artist), norm(title)


@dataclass
class Track:
    artist: str
    title: str
    url: str = ""
    source: str = ""  # why it was picked, e.g. "similar to Fred again.."
    known: bool = False  # already in the user's library
    score: float = 0.0

    @property
    def key(self) -> tuple[str, str]:
        return track_key(self.artist, self.title)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Playlist:
    id: str
    name: str
    description: str
    tracks: list[Track]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tracks": [t.to_dict() for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Playlist":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            tracks=[Track(**t) for t in data["tracks"]],
        )
