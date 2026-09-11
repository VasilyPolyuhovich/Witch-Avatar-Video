"""Local WebUI backend: serves the static frontend and exposes a small
password-gated API that drives a RunPodSession. See
docs/superpowers/specs/2026-08-30-webui-design.md.

The 'voice' upload in /api/generate is a voice-CLONING reference sample
(see webui/frontend/i18n.js's "Voice sample (optional)" label) -- this
backend runs Chatterbox TTS on `text` itself (locally, on this machine,
same as scripts/generate_witch_video.py's own text_to_speech -- NOT on
the GPU pod, which can't have both Chatterbox's and EchoMimicV3's pinned
transformers/diffusers versions installed at once, they conflict) and
forwards the *synthesized* audio to the pod, not the raw uploaded clip.
Fixed 2026-09-10 after this was found to be a blind pass-through instead
-- see docs/superpowers/plans/2026-09-09-echomimicv3-migration-implementation.md,
Task 9b."""
import sys
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from . import config
from .runpod_session import RunPodSession

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import generate_witch_video  # noqa: E402

app = FastAPI()

_password = config.load_webui_password()
_account_key = config.load_runpod_account_key()
_image_ref = config.load_backend_image_ref()
session = RunPodSession(account_key=_account_key, image_ref=_image_ref)


def require_password(x_webui_password: str = Header(default="")):
    if x_webui_password != _password:
        raise HTTPException(status_code=401, detail="invalid_password")


@app.post("/api/start")
def api_start(_: None = Depends(require_password)):
    try:
        pod_id = session.start()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"pod_id": pod_id}


@app.post("/api/stop")
def api_stop(_: None = Depends(require_password)):
    session.stop()
    return {"ok": True}


@app.get("/api/status")
def api_status(_: None = Depends(require_password)):
    active = session.pod_id is not None
    ready = session.poll_health() if active else False
    return {
        "active": active,
        "ready": ready,
        "elapsed_seconds": session.elapsed_seconds(),
    }


@app.post("/api/generate")
async def api_generate(
    text: str = Form(...),
    image: UploadFile = File(...),
    voice: UploadFile = File(None),
    _: None = Depends(require_password),
):
    if not session.base_url:
        raise HTTPException(status_code=409, detail="not_ready")
    image_bytes = await image.read()

    with tempfile.TemporaryDirectory() as tmp:
        voice_sample_path = None
        if voice is not None:
            voice_bytes = await voice.read()
            # voice.filename is client-controlled over this network-facing
            # endpoint -- joining it into a path directly let a name like
            # "../../etc/cron.d/x" write outside `tmp` (found by automated
            # commit review, 2026-09-10, same class of bug already fixed
            # in docker/echomimicv3-webui/app.py). Keep only the extension.
            voice_ext = Path(voice.filename or "").suffix or ".wav"
            voice_sample_path = Path(tmp) / f"voice_sample{voice_ext}"
            voice_sample_path.write_bytes(voice_bytes)

        audio_path = Path(tmp) / "synthesized.wav"
        generate_witch_video.text_to_speech(text, audio_path, voice_sample=voice_sample_path)
        synthesized_audio_bytes = audio_path.read_bytes()

    job_id = session.generate(image_bytes, image.filename, text, synthesized_audio_bytes, "synthesized.wav")
    return {"job_id": job_id}


@app.get("/api/generate/{job_id}/status")
def api_generate_status(job_id: str, _: None = Depends(require_password)):
    return session.get_status(job_id)


@app.get("/api/generate/{job_id}/result")
def api_generate_result(job_id: str, _: None = Depends(require_password)):
    content = session.get_result(job_id)
    return Response(content=content, media_type="video/mp4")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
