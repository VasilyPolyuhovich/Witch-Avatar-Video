"""Local WebUI backend: serves the static frontend and exposes a small
password-gated API that drives a RunPodSession. See
docs/superpowers/specs/2026-08-30-webui-design.md."""
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from . import config
from .runpod_session import RunPodSession

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

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
    voice_bytes = await voice.read() if voice else None
    voice_filename = voice.filename if voice else None
    job_id = session.generate(image_bytes, image.filename, text, voice_bytes, voice_filename)
    return {"job_id": job_id}


@app.get("/api/generate/{job_id}/status")
def api_generate_status(job_id: str, _: None = Depends(require_password)):
    return session.get_status(job_id)


@app.get("/api/generate/{job_id}/result")
def api_generate_result(job_id: str, _: None = Depends(require_password)):
    content = session.get_result(job_id)
    return Response(content=content, media_type="video/mp4")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
