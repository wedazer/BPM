# Site BPM — Détection multi-plateformes

Backend FastAPI + frontend statique pour coller un lien (YouTube, TikTok, Instagram, Twitter/X, MP4/MP3, etc.) ou envoyer un fichier et obtenir le BPM.

## Prérequis
- Python 3.10+
- FFmpeg installé et accessible dans le PATH (obligatoire pour certaines conversions)
  - Windows: installez FFmpeg (https://www.gyan.dev/ffmpeg/builds/), ajoutez `bin/` au PATH, puis redémarrez le terminal
- Accès réseau sortant (pour récupérer les médias)

yt-dlp est installé via `requirements.txt`.

## Installation
```powershell
# Dans le dossier du projet
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

Vérifiez FFmpeg:
```powershell
ffmpeg -version
```

## Lancement (dev)
```powershell
# Démarre l'API
uvicorn backend.main:app --reload --port 8000
```

Frontend:
- Ouvrez `frontend/index.html` dans votre navigateur
- Ou servez-le (optionnel):
```powershell
# Exemple simple (Python)
python -m http.server 5500 -d frontend
# Puis: http://127.0.0.1:5500/
```

## Endpoints
- GET `/status` → `{ "status": "ok" }`
- POST `/bpm/url` → body: `{ "url": "https://..." }` → réponse: `{ bpm, confidence? }` ou `{ error, details? }`
- POST `/bpm/upload` (multipart `file`) → réponse: `{ bpm, confidence? }` ou `{ error, details? }`

## Notes
- Cas pris en charge:
  - Plateformes via yt-dlp (YouTube, TikTok, Instagram, X/Twitter, Vimeo, Dailymotion, SoundCloud, etc.)
  - Liens directs: `.mp4 .mov .wav .mp3 .aac .flac .m4a .ogg .webm .mkv .avi`
  - Upload de fichiers
- Si un média n’a pas d’audio, l’API renverra une erreur adaptée.
- La détection retourne aussi une estimation de fiabilité (approx.).

## Déploiement
- Backend: Render / Railway / Replit
- Frontend: Vercel / Netlify / GitHub Pages

Pensez à fournir FFmpeg dans l’image/instance backend (ex: apt-get install ffmpeg).
