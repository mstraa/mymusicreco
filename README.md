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

## Export vers Spotify

Chaque playlist devient une playlist privée « MyMusicReco · … » dans ton compte Spotify. Elle est créée au premier envoi,
puis son contenu est remplacé à chaque nouvel envoi (même lien, pas de doublons). Si tu la supprimes dans Spotify,
elle est recréée au prochain envoi.

1. Crée une app sur <https://developer.spotify.com/dashboard> (API : *Web API*) et ajoute la Redirect URI
   `http://127.0.0.1:8000/spotify/callback`. Depuis février 2026, le propriétaire d'une app en mode développement doit avoir Spotify Premium.
2. Mets `SPOTIFY_CLIENT_ID` et `SPOTIFY_CLIENT_SECRET` dans `.env`.
3. Connecte-toi une fois :
   - via l'interface web : `python -m mymusicreco serve`, puis « Connecter Spotify » ;
   - ou en ligne de commande : `python -m mymusicreco spotify login`, ouvre l'adresse affichée, et si la page de retour
     ne s'affiche pas (app web arrêtée), colle son adresse dans le terminal.
4. Envoie les playlists :

```bash
python -m mymusicreco spotify sync              # toutes
python -m mymusicreco spotify sync weekly daily-2 mood-running
python -m mymusicreco spotify sync --from exports/   # les JSON déjà exportés, sans repasser par Last.fm
python -m mymusicreco spotify status
```

Dans l'interface web, chaque playlist a un bouton « Envoyer vers Spotify », et « Tout envoyer » envoie tout.
Chaque titre est cherché sur Spotify (artiste + titre, versions « radio edit », « feat. »… tolérées) ; les correspondances
sont gardées en cache 30 jours, et les titres introuvables sont listés.

### API

| Route | |
|---|---|
| `GET /api/profile` | résumé du profil de goûts |
| `GET /api/playlists/weekly` | découvertes de la semaine |
| `GET /api/playlists/daily` | les daily mixes |
| `GET /api/playlists/mood/{running,focus,chill}` | playlist d'humeur |
| `GET /api/playlists/{id}/export?format=csv\|json` | export d'une playlist |
| `GET /api/spotify/status` | connexion Spotify |
| `POST /api/spotify/sync/{all,weekly,daily,mood,<id>}` | envoi vers Spotify |

Toutes les routes de playlist acceptent `?refresh=true`.

## Configuration

| Variable | Défaut | |
|---|---|---|
| `LASTFM_API_KEY` | — | obligatoire |
| `LASTFM_USER` | `ImRedCrow` | utilisateur Last.fm |
| `MYMUSICRECO_DB` | `mymusicreco.db` | fichier SQLite (cache + playlists) |
| `MYMUSICRECO_EXCLUDE_ARTISTS` | — | artistes à ignorer, séparés par des virgules |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | — | pour l'export Spotify |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8000/spotify/callback` | doit être déclarée dans l'app Spotify |

Les humeurs sont définies dans `mymusicreco/playlists.py` (`MOODS`) : un dictionnaire de tags pondérés et de tags à éviter,
facile à étendre (« soirée », « voiture »…).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Les tests utilisent de faux clients Last.fm et Spotify, sans réseau.
