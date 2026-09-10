"""Real WebUI backend: implements the same /health, /heartbeat,
/generate, /status/{id}, /result/{id} contract as docker/webui-stub, but
calls real EchoMimicV3-Flash inference (via infer_flash.py, in-process --
not a subprocess wrapper around scripts/run_echomimicv3.sh, since that
script is designed for SSH/CLI use and this container already has the
repo + weights mounted the same way). See
docs/superpowers/specs/2026-08-30-webui-design.md for the contract this
implements, and docs/superpowers/plans/2026-09-09-echomimicv3-migration-implementation.md
for why this exists (replacing the stub).

Note on /generate's contract: the stub's version took `text` (spoken by
a TTS voice server-side) and an optional `voice` reference clip. This
project's real pipeline generates speech *locally* via Chatterbox before
ever touching a pod (see scripts/generate_witch_video.py's
text_to_speech) -- so here, `voice` is repurposed as the already-
synthesized driving audio, not a voice-cloning reference sample, and
`text` is accepted but unused by this endpoint (kept for contract
compatibility with webui/backend/app.py's existing /api/generate route,
which still forwards both). This is a deliberate seam, not an oversight.
"""
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

WEIGHTS_DIR = "/workspace/models/echomimicv3/flash"
REPO_DIR = "/opt/echomimic_v3"
FPS = 25
MAX_FRAMES = 138

# Same warmup/heartbeat shape as docker/webui-stub -- see that image's
# git history for the RunPod proxy-warmup and heartbeat-timeout
# rationale; unchanged here.
WARMUP_S = 5
READY_AT = time.monotonic() + WARMUP_S

LAST_HEARTBEAT = time.monotonic()
HEARTBEAT_TIMEOUT_S = 300

JOBS = {}  # job_id -> {"status": "running"|"done"|"error", "error_code": str|None, "video": bytes|None}


def self_terminate():
    api_key = os.environ.get("RUNPOD_API_KEY")
    pod_id = os.environ.get("RUNPOD_POD_ID")
    if not api_key or not pod_id:
        return
    import urllib.request
    body = json.dumps({"query": "mutation{podTerminate(input:{podId:%s})}" % json.dumps(pod_id)}).encode()
    req = urllib.request.Request(
        "https://api.runpod.io/graphql", data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "User-Agent": "witch-avatar-video-echomimicv3-webui/1.0"})
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


def run_inference(image_bytes, image_filename, audio_bytes, audio_filename):
    """Runs EchoMimicV3-Flash on one image+audio pair, returns the
    resulting mp4's bytes. Video length is computed from the actual audio
    duration and capped at MAX_FRAMES -- see run_echomimicv3.sh's own
    comment (this mirrors that script's logic since both need it, and
    this container calls infer_flash.py directly rather than shelling
    out to that SSH-oriented script)."""
    with tempfile.TemporaryDirectory() as tmp:
        image_path = os.path.join(tmp, image_filename)
        audio_path = os.path.join(tmp, audio_filename)
        with open(image_path, "wb") as f:
            f.write(image_bytes)
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        import librosa
        duration = librosa.get_duration(path=audio_path)
        video_length = max(1, min(MAX_FRAMES, round(duration * FPS)))

        cmd = [
            "python3", "infer_flash.py",
            "--image_path", image_path,
            "--audio_path", audio_path,
            "--prompt", "A person is speaking.",
            "--num_inference_steps", "8",
            "--config_path", "config/config.yaml",
            "--model_name", "flash/Wan2.1-Fun-V1.1-1.3B-InP",
            "--ckpt_idx", "50000",
            "--transformer_path", "flash/transformer/diffusion_pytorch_model.safetensors",
            "--save_path", tmp,
            "--wav2vec_model_dir", "flash/chinese-wav2vec2-base",
            "--sampler_name", "Flow_Unipc",
            "--video_length", str(video_length),
            "--guidance_scale", "6.0",
            "--audio_guidance_scale", "3.0",
            "--audio_scale", "1.0",
            "--neg_scale", "1.0",
            "--neg_steps", "0",
            "--seed", "43",
            "--enable_teacache",
            "--teacache_threshold", "0.1",
            "--num_skip_start_steps", "5",
            "--riflex_k", "6",
            "--ulysses_degree", "1",
            "--ring_degree", "1",
            "--weight_dtype", "bfloat16",
            "--sample_size", "768", "768",
            "--fps", str(FPS),
            "--add_prompt", "",
            "--negative_prompt", "",
            "--shift", "5.0",
        ]
        result = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"infer_flash.py failed: {result.stderr[-2000:]}")

        output_file = None
        for line in result.stdout.splitlines():
            if line.strip().startswith("Saved output to: "):
                output_file = line.strip()[len("Saved output to: "):]
        if not output_file or not os.path.isfile(output_file):
            raise RuntimeError("infer_flash.py did not report a final output file")

        with open(output_file, "rb") as f:
            return f.read()


def _run_job(job_id, image_bytes, image_filename, audio_bytes, audio_filename):
    try:
        video_bytes = run_inference(image_bytes, image_filename, audio_bytes, audio_filename)
        JOBS[job_id] = {"status": "done", "error_code": None, "video": video_bytes}
    except Exception:
        JOBS[job_id] = {"status": "error", "error_code": "generation_failed", "video": None}


@app.post("/generate")
async def generate(text: str = Form(...), image: UploadFile = File(...), voice: UploadFile = File(None)):
    if voice is None:
        raise HTTPException(status_code=422, detail="voice (driving audio) is required")
    job_id = uuid.uuid4().hex
    image_bytes = await image.read()
    voice_bytes = await voice.read()
    JOBS[job_id] = {"status": "running", "error_code": None, "video": None}
    threading.Thread(
        target=_run_job, args=(job_id, image_bytes, image.filename, voice_bytes, voice.filename), daemon=True,
    ).start()
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
