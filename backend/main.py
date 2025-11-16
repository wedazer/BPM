import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import librosa
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import imageio_ffmpeg
import sys

app = FastAPI()

# Allow local dev frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DIRECT_MEDIA_EXT = {
    ".mp4", ".mov", ".wav", ".mp3", ".aac", ".flac", ".m4a", ".ogg", ".webm", ".mkv", ".avi"
}

class URLBody(BaseModel):
    url: str

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
os.environ.setdefault("FFMPEG_BINARY", FFMPEG_EXE)
os.environ.setdefault("FFMPEG_LOCATION", str(Path(FFMPEG_EXE).parent))
os.environ.setdefault("YTDLP_FFMPEG_LOCATION", str(Path(FFMPEG_EXE).parent))

# Call yt-dlp via module to avoid PATH issues on Windows
YTDLP_CMD = [sys.executable, "-m", "yt_dlp"]


def _has_ffmpeg() -> bool:
    try:
        subprocess.run([FFMPEG_EXE, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except Exception:
        return False


def _run(cmd: list[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _download_with_ytdlp(url: str, out_wav: Path) -> tuple[bool, str]:
    # Download best available audio WITHOUT post-processing, then convert ourselves
    tmp_dir = out_wav.parent
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        *YTDLP_CMD,
        "-f", "ba/bestaudio",
        "-o", str(tmp_dir / "audio.%(ext)s"),
        url,
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        return False, proc.stderr or proc.stdout
    # Find produced file (any extension)
    produced = None
    for f in tmp_dir.glob("audio.*"):
        produced = f
        break
    if not produced or not produced.exists():
        return False, "Aucun fichier audio téléchargé."
    ok, err = _ensure_wav(produced, out_wav)
    if not ok:
        return False, err
    return True, ""


def _is_direct_media(url: str) -> bool:
    # Simple check by extension or common CDN signatures
    m = re.search(r"(\.[a-z0-9]{2,4})(?:\?|#|$)", url, re.IGNORECASE)
    if not m:
        return False
    return m.group(1).lower() in DIRECT_MEDIA_EXT


def _http_download(url: str, out_path: Path) -> tuple[bool, str]:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "*/*",
        }
        # timeout=(connect, read)
        with requests.get(url, headers=headers, stream=True, timeout=(4, 12)) as r:
            r.raise_for_status()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        f.write(chunk)
        return True, ""
    except Exception as e:
        return False, str(e)


def _preflight_head(url: str) -> tuple[bool, str, dict]:
    """Quick HEAD check to avoid long hangs on slow/blocked hosts."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "*/*",
        }
        r = requests.head(url, headers=headers, allow_redirects=True, timeout=(3, 5))
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}", {}
        info = {
            "content_type": r.headers.get("Content-Type", ""),
            "content_length": r.headers.get("Content-Length", ""),
        }
        return True, "", info
    except Exception as e:
        return False, str(e), {}


def _ensure_wav(input_path: Path, out_wav: Path) -> tuple[bool, str]:
    if input_path.suffix.lower() == ".wav":
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(input_path, out_wav)
        return True, ""
    if not _has_ffmpeg():
        return False, "FFmpeg non installé."
    cmd = [
        FFMPEG_EXE, "-y",
        "-i", str(input_path),
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        str(out_wav),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        return False, proc.stderr or proc.stdout
    return True, ""


def _analyze_bpm(wav_path: Path) -> tuple[Optional[float], Optional[float], str]:
    try:
        y, sr = librosa.load(str(wav_path), sr=44100, mono=True)
        if y.size == 0:
            return None, None, "Cette vidéo ne contient pas d'audio."
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        if tempo is None or tempo <= 0:
            return None, None, "Impossible de détecter un tempo clair."
        confidence = None
        try:
            # Rough confidence from onset strength variance
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            confidence = float(min(1.0, max(0.0, onset_env.std() / (onset_env.std() + 1e-6))))
        except Exception:
            confidence = None
        return float(tempo), confidence, ""
    except Exception as e:
        return None, None, str(e)


@app.get("/status")
async def status():
    return {"status": "ok"}


@app.post("/bpm/url")
async def bpm_from_url(body: URLBody):
    try:
        url = body.url.strip()
        if not url:
            raise HTTPException(status_code=400, detail={"error": "URL manquante."})

        workdir = Path(tempfile.mkdtemp(prefix="bpm_url_"))
        out_path = workdir / "input"
        out_wav = workdir / "audio.wav"

        try:
            if _is_direct_media(url):
                ok_h, err_h, info = _preflight_head(url)
                if not ok_h:
                    return {"error": "Impossible d'extraire l'audio depuis ce lien.", "details": f"Pré-vérification échouée: {err_h}"}
                ok, err = _http_download(url, out_path)
                if not ok:
                    return {"error": "Impossible d'extraire l'audio depuis ce lien.", "details": f"Téléchargement direct: {err}"}
                ok, err = _ensure_wav(out_path, out_wav)
                if not ok:
                    if "FFmpeg" in err:
                        return {"error": "Impossible d'extraire l'audio depuis ce lien.", "details": "FFmpeg requis pour conversion."}
                    return {"error": "Impossible de détecter un tempo clair.", "details": err}
            else:
                ok, err = _download_with_ytdlp(url, out_wav)
                if not ok:
                    return {"error": "Impossible d'extraire l'audio depuis ce lien.", "details": err}

            if not out_wav.exists() or out_wav.stat().st_size == 0:
                return {"error": "Cette vidéo ne contient pas d'audio."}

            bpm, conf, err = _analyze_bpm(out_wav)
            if bpm is None:
                return {"error": "Impossible de détecter un tempo clair.", "details": err}
            resp = {"bpm": round(bpm, 2)}
            if conf is not None:
                resp["confidence"] = round(conf, 3)
            return resp
        finally:
            try:
                shutil.rmtree(workdir, ignore_errors=True)
            except Exception:
                pass
    except HTTPException:
        raise
    except Exception as e:
        return {"error": "Impossible d'extraire l'audio depuis ce lien.", "details": str(e)}


@app.post("/bpm/upload")
async def bpm_from_upload(file: UploadFile = File(...)):
    try:
        workdir = Path(tempfile.mkdtemp(prefix="bpm_up_"))
        in_path = workdir / (Path(file.filename).name or "input")
        out_wav = workdir / "audio.wav"
        try:
            with open(in_path, "wb") as f:
                while True:
                    chunk = await file.read(1024 * 512)
                    if not chunk:
                        break
                    f.write(chunk)
            if in_path.stat().st_size == 0:
                return {"error": "Cette vidéo ne contient pas d'audio."}
            ok, err = _ensure_wav(in_path, out_wav)
            if not ok:
                if "FFmpeg" in err:
                    return {"error": "Impossible d'extraire l'audio depuis ce fichier.", "details": "FFmpeg requis pour conversion."}
                return {"error": "Impossible de détecter un tempo clair.", "details": err}
            bpm, conf, err = _analyze_bpm(out_wav)
            if bpm is None:
                return {"error": "Impossible de détecter un tempo clair.", "details": err}
            resp = {"bpm": round(bpm, 2)}
            if conf is not None:
                resp["confidence"] = round(conf, 3)
            return resp
        finally:
            try:
                shutil.rmtree(workdir, ignore_errors=True)
            except Exception:
                pass
    except Exception as e:
        return {"error": "Impossible d'extraire l'audio depuis ce fichier.", "details": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
