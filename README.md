# MyMusicReco

Une petite app qui fait comme les playlists de Spotify (Discover Weekly, Daily Mix, playlists « running »…),
mais à partir de ton historique **Last.fm**. Elle produit des listes de titres ; la lecture viendra plus tard.

## Les playlists

| Playlist | Contenu | Renouvellement |
|---|---|---|
| **Découvertes de la semaine** | 30 titres jamais scrobblés, un par artiste, venant d'artistes proches de tes artistes du moment. Chaque titre est vérifié auprès de Last.fm (`userplaycount = 0`). | chaque semaine (lundi) |
| **Daily Mix 1 à 6** | Tes artistes regroupés par genre dominant (drum and bass, house, metal…). ~70 % de titres que tu connais, ~30 % de nouveautés d'artistes proches du même genre. | chaque jour |
| **Running / Focus / Chill** | Titres de ta bibliothèque dont les artistes collent à l'humeur (tags Last.fm), plus des nouveautés d'artistes proches et des charts de tags filtrés selon tes goûts. ~50/50. | chaque jour |

Le profil de goûts combine tes top artistes sur 7 jours, 1 mois, 6 mois et depuis toujours (le récent pèse plus),
leurs tags, et les ~5000 titres les plus écoutés de ta bibliothèque pour savoir ce que tu connais déjà.
Les podcasts (HugoDécrypte, WVFRM…) sont détectés et ignorés.

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis mets ta clé API Last.fm dans LASTFM_API_KEY
```

La clé API se crée sur <https://www.last.fm/api/account/create>. Le fichier `.env` est ignoré par git.

## Utilisation

Interface web :

```bash
python -m mymusicreco serve        # http://127.0.0.1:8000
```

En ligne de commande :

```bash
python -m mymusicreco profile
python -m mymusicreco weekly
python -m mymusicreco daily --export exports/
python -m mymusicreco mood running --refresh
```

`--refresh` régénère la playlist au lieu de reprendre celle de la semaine / du jour.
`--export DIR` écrit chaque playlist en JSON et CSV.

La première génération interroge beaucoup Last.fm (~30 à 60 s) ; les réponses sont gardées en cache dans
SQLite (`mymusicreco.db`), donc les suivantes sont quasi instantanées.

### API

| Route | |
|---|---|
| `GET /api/profile` | résumé du profil de goûts |
| `GET /api/playlists/weekly` | découvertes de la semaine |
| `GET /api/playlists/daily` | les daily mixes |
| `GET /api/playlists/mood/{running,focus,chill}` | playlist d'humeur |
| `GET /api/playlists/{id}/export?format=csv\|json` | export d'une playlist |

Toutes les routes de playlist acceptent `?refresh=true`.

## Configuration

| Variable | Défaut | |
|---|---|---|
| `LASTFM_API_KEY` | — | obligatoire |
| `LASTFM_USER` | `ImRedCrow` | utilisateur Last.fm |
| `MYMUSICRECO_DB` | `mymusicreco.db` | fichier SQLite (cache + playlists) |
| `MYMUSICRECO_EXCLUDE_ARTISTS` | — | artistes à ignorer, séparés par des virgules |

Les humeurs sont définies dans `mymusicreco/playlists.py` (`MOODS`) : un dictionnaire de tags pondérés et de tags à éviter,
facile à étendre (« soirée », « voiture »…).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Les tests utilisent un faux client Last.fm, sans réseau.
