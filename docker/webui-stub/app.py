"""Throwaway stub implementing the WebUI's backend HTTP contract
(/health, /heartbeat, /generate, /status/{id}, /result/{id}) so the
WebUI can be built and tested before a real talking-head model is
chosen. See docs/superpowers/specs/2026-08-30-webui-design.md's Testing
strategy section. A real backend later implements this same contract in
its own Docker image -- nothing about the WebUI needs to change."""
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

app = FastAPI()

# Simulated "still loading" period so /health's ready:false path is
# exercisable without needing a real slow model load.
WARMUP_S = 5
READY_AT = time.monotonic() + WARMUP_S

LAST_HEARTBEAT = time.monotonic()
HEARTBEAT_TIMEOUT_S = 90

JOBS = {}  # job_id -> {"status": "running"|"done"|"error", "error_code": str|None, "video": bytes|None}


def self_terminate():
    """Calls RunPod's terminate API on this pod's own ID, using the same
    scoped key the WebUI holds (passed in at deploy time), per the
    spec's heartbeat-timeout safety net."""
    api_key = os.environ.get("RUNPOD_API_KEY")
    pod_id = os.environ.get("RUNPOD_POD_ID")
    if not api_key or not pod_id:
        return
    import urllib.request
    body = json.dumps({"query": "mutation{podTerminate(input:{podId:%s})}" % json.dumps(pod_id)}).encode()
    req = urllib.request.Request(
        "https://api.runpod.io/graphql", data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "User-Agent": "witch-avatar-video-webui-stub/1.0"})
    urllib.request.urlopen(req, timeout=30)


def check_heartbeat_timeout():
    if time.monotonic() - LAST_HEARTBEAT > HEARTBEAT_TIMEOUT_S:
        self_terminate()


def _heartbeat_watchdog_loop():
    while True:
        time.sleep(10)
        check_heartbeat_timeout()


threading.Thread(target=_heartbeat_watchdog_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"ready": time.monotonic() >= READY_AT}


@app.post("/heartbeat")
def heartbeat():
    global LAST_HEARTBEAT
    LAST_HEARTBEAT = time.monotonic()
    return {"ok": True}


def _make_canned_video(job_id):
    """A 1-second black clip with a sine tone, generated on the fly with
    the pod's own ffmpeg -- stands in for a real render's output so the
    full download/playback path is exercised end-to-end."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        out_path = f.name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=black:s=320x320:d=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-c:a", "aac", "-shortest", out_path],
        check=True)
    with open(out_path, "rb") as f:
        video_bytes = f.read()
    os.unlink(out_path)
    return video_bytes


def _run_job(job_id):
    time.sleep(3)  # simulate render time
    try:
        video_bytes = _make_canned_video(job_id)
        JOBS[job_id] = {"status": "done", "error_code": None, "video": video_bytes}
    except Exception:
        JOBS[job_id] = {"status": "error", "error_code": "generation_failed", "video": None}


@app.post("/generate")
async def generate(text: str = Form(...), image: UploadFile = File(...), voice: UploadFile = File(None)):
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "running", "error_code": None, "video": None}
    threading.Thread(target=_run_job, args=(job_id,), daemon=True).start()
    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown_job_id")
    return {"status": job["status"], "error_code": job["error_code"], "detail": ""}


@app.get("/result/{job_id}")
def result(job_id: str):
    job = JOBS.get(job_id)
    if job is None or job["status"] != "done":
        raise HTTPException(status_code=404, detail="result_not_available")
    return Response(content=job["video"], media_type="video/mp4")
