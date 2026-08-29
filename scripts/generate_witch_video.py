#!/usr/bin/env python3
"""On-demand witch avatar video generation: text -> local Chatterbox
Multilingual V3 TTS -> deploy a fresh MuseTalk pod -> render -> download
-> terminate, always, in one call. See
docs/2026-08-13-witch-avatar-video-design.md for the full design.

Usage:
    python3 scripts/generate_witch_video.py \\
        --image path/to/gadalka_portrait.jpg \\
        --text "Текст, який каже гадалка..." \\
        --voice-sample assets/gadalka-voice-reference.wav \\
        --output path/to/flyer.mp4
    python3 scripts/generate_witch_video.py ... --dry-run    # rank GPUs, print the command, no TTS call, no spend
    python3 scripts/generate_witch_video.py ... --tts-only   # just render audio.wav locally, skip the GPU step

`--voice-sample` is optional -- omit it to use Chatterbox's built-in
default voice (see Task 5, which picks between the two).

Cost safety: the pod is ALWAYS terminated in a finally block, including on
crash or Ctrl-C (matching AI-Avatar-Video's proven safety contract). The
render step has a hard timeout (--timeout, default 600s) -- generous
headroom over an expected few-minute render for a 5-15s clip on a much
lighter model than LongCat. TTS runs entirely locally before any pod exists
(and is skipped entirely under --dry-run), so a bad text/reference-clip or
a GPU-market check never costs anything at all -- unlike the ElevenLabs
version this replaced, local Chatterbox inference has zero marginal cost,
not just a cheap one.
"""
import argparse
import json
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))  # so `import pod_up` works regardless of caller's cwd
import pod_up  # noqa: E402

DEFAULT_RUN_TIMEOUT_S = 600
DEFAULT_TTS_DEVICE = "mps"  # this project's dev machine is Apple Silicon; override for portability
FINAL_OUTPUT_MARKER = "Final output will be: "

# Tuned against assets/gadalka-voice-reference.wav specifically (Task 5,
# Step 7) -- higher exaggeration and lower cfg_weight than Chatterbox's own
# defaults (0.5/0.5), confirmed by direct A/B to give noticeably more
# natural intonation with THIS reference clip. Not validated against the
# built-in default voice, so only applied when a voice_sample is given.
CLONE_EXAGGERATION = 0.65
CLONE_CFG_WEIGHT = 0.4
INTER_PHRASE_PAUSE_S = 0.5


@dataclass
class GenerationResult:
    local_path: str
    elapsed_s: float
    gpu_id: str
    gpu_price_per_hr: float
    est_cost_usd: float
    pod_id: str
    job_id: str


def log(msg):
    print(f"[generate] {msg}")


def text_to_speech(text, output_path, *, voice_sample=None, device=DEFAULT_TTS_DEVICE):
    """text -> local Chatterbox Multilingual V3 inference -> output_path
    (wav). Raises RuntimeError on failure -- this is step 1, entirely
    local, deliberately before any pod exists (see module docstring).
    chatterbox/torch/numpy/soundfile are imported HERE, not at module
    level, so --dry-run (which never calls this function) doesn't pay
    torch's import cost or require chatterbox-tts to be installed at all
    -- see Task 5 for the local venv this needs.

    Two corrections vs. this project's original plan draft, both confirmed
    2026-08-27/28 against the real installed chatterbox-tts==0.1.7 (see
    Task 5's plan notes for the full story):
    - `ChatterboxMultilingualTTS.from_pretrained` takes only `device` --
      no `t3_model` kwarg exists in the released package.
    - Saves via `soundfile.write`, not `torchaudio.save` -- the installed
      torchaudio routes `.save()` through a separate `torchcodec` package
      this project doesn't depend on.

    Text is split into phrases and generated one at a time, then rejoined
    with a short silence, rather than one `generate()` call over the full
    string -- confirmed 2026-08-28 (Task 5 Step 7) to noticeably improve
    pacing/intonation with this project's cloned voice.
    """
    import os
    import re

    import numpy as np
    import soundfile as sf
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # see Task 5: HF's newer download
    # backend repeatedly stalled/failed downloading this model; classic HTTP doesn't.
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    kwargs = {"language_id": "ru"}
    if voice_sample:
        kwargs["audio_prompt_path"] = str(voice_sample)
        kwargs["exaggeration"] = CLONE_EXAGGERATION
        kwargs["cfg_weight"] = CLONE_CFG_WEIGHT

    phrases = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    if not phrases:
        raise ValueError(f"No speakable text in: {text!r}")

    pause = np.zeros(int(INTER_PHRASE_PAUSE_S * model.sr), dtype=np.float32)
    chunks = []
    for i, phrase in enumerate(phrases):
        wav = model.generate(phrase, **kwargs)
        chunks.append(wav.squeeze().cpu().numpy())
        if i < len(phrases) - 1:
            chunks.append(pause)
    full = np.concatenate(chunks)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = output_path.with_suffix(".raw.wav")
    sf.write(str(raw_path), full, model.sr)

    # Chatterbox's cloned-voice output carries background hiss inherited
    # from the (old, public-domain) reference clip and is quite quiet
    # (~-30dB mean) -- both confirmed 2026-08-28 (first real render) to
    # confuse SadTalker's mel-spectrogram-driven lip-sync: it produced no
    # real lip-sync and erratic, speech-independent mouth/expression
    # motion instead, reacting to noise rather than words. Denoise +
    # loudness-normalize before handing off to SadTalker.
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(raw_path), "-af",
         "afftdn=nf=-25,loudnorm=I=-16:TP=-1.5:LRA=11",
         "-ar", str(model.sr), str(output_path)],
        check=True, capture_output=True,
    )
    raw_path.unlink()
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Chatterbox produced no audio at {output_path}")


def validate_image(image_path):
    image_path = Path(image_path)
    if not image_path.is_file() or image_path.stat().st_size == 0:
        raise ValueError(f"Image not found or empty: {image_path}")
    return image_path


def compute_remote_paths(job_id, image_path, audio_path):
    # Job inputs/outputs still live on the pod-local disk (fast, ephemeral,
    # cleaned up by pod termination); only the MuseTalk model weights live
    # on the network volume (mounted at /workspace/models, see
    # scripts/run_musetalk.sh) -- these are two independent volumes for
    # two different purposes, not a contradiction.
    base = f"/root/jobs/{job_id}"
    return {
        "job_dir": base,
        "input_dir": f"{base}/input",
        "output_dir": f"{base}/output",
        "image": f"{base}/input/{Path(image_path).name}",
        "audio": f"{base}/input/{Path(audio_path).name}",
        "run_script": f"{base}/run_musetalk.sh",
    }


def build_remote_cmd(paths):
    return " ".join([
        shlex.quote(paths["run_script"]),
        "--image", shlex.quote(paths["image"]),
        "--audio", shlex.quote(paths["audio"]),
        "--output-dir", shlex.quote(paths["output_dir"]),
    ])


def run_ssh(ip, port, key_path, remote_cmd, timeout=60, text=True):
    cmd = ["ssh", "-p", str(port), *pod_up.ssh_flags(key_path),
           "-o", "ConnectTimeout=15", f"root@{ip}", remote_cmd]
    r = subprocess.run(cmd, capture_output=True, text=text, timeout=timeout)
    if r.returncode != 0:
        stderr = r.stderr if text else r.stderr.decode(errors="replace")
        raise RuntimeError(f"ssh command failed ({r.returncode}): {remote_cmd}\n{stderr}")
    return r.stdout


def scp_up(ip, port, key_path, local_path, remote_path, timeout=120):
    cmd = ["scp", "-P", str(port), *pod_up.ssh_flags(key_path), local_path, f"root@{ip}:{remote_path}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"scp upload failed ({r.returncode}): {local_path} -> {remote_path}\n{r.stderr}")


def scp_down(ip, port, key_path, remote_path, local_path, timeout=120):
    cmd = ["scp", "-P", str(port), *pod_up.ssh_flags(key_path), f"root@{ip}:{remote_path}", local_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"scp download failed ({r.returncode}): {remote_path} -> {local_path}\n{r.stderr}")


def run_remote_detached(ip, port, key_path, remote_cmd, remote_job_dir, run_timeout_s,
                         *, poll_interval=10, max_disconnect_s=600):
    """Launch remote_cmd DETACHED from the SSH session (setsid+nohup, stdio
    redirected to a file, backgrounded+disowned) and monitor it via repeated
    short-lived reconnecting polls instead of one fragile long-lived stream
    -- a transient LOCAL network blip must not kill a real GPU render.
    Ported near-verbatim from AI-Avatar-Video's generate_avatar_video.py,
    which needed this after two confirmed-live incidents of exactly that.
    Returns the full accumulated remote log text on success; raises
    TimeoutError/RuntimeError on failure."""
    log_path = f"{remote_job_dir}/render.log"
    exit_path = f"{remote_job_dir}/render.exit"
    marker = f"@@EXIT_{uuid.uuid4().hex}@@"
    launch = (
        f"rm -f {shlex.quote(exit_path)}; "
        f"setsid nohup bash -c {shlex.quote(remote_cmd + '; echo $? > ' + shlex.quote(exit_path))} "
        f"> {shlex.quote(log_path)} 2>&1 < /dev/null & disown"
    )
    run_ssh(ip, port, key_path, launch, timeout=30)

    chunks = []
    offset = 0
    deadline = time.time() + run_timeout_s
    last_success = time.time()
    while True:
        if time.time() > deadline:
            raise TimeoutError(f"remote command exceeded {run_timeout_s}s")
        poll_cmd = (
            f"tail -c +{offset + 1} {shlex.quote(log_path)} 2>/dev/null; "
            f"printf '%s' {shlex.quote(marker)}; "
            f"(test -f {shlex.quote(exit_path)} && cat {shlex.quote(exit_path)}) || echo RUNNING"
        )
        try:
            raw = run_ssh(ip, port, key_path, poll_cmd, timeout=30, text=False)
            last_success = time.time()
        except Exception as e:
            if time.time() - last_success > max_disconnect_s:
                raise RuntimeError(f"lost contact with pod for over {max_disconnect_s}s: {e}")
            time.sleep(poll_interval)
            continue

        new_bytes, _, rest = raw.partition(marker.encode())
        if new_bytes:
            offset += len(new_bytes)
            chunks.append(new_bytes)
        status = rest.strip().decode(errors="replace")
        if status != "RUNNING":
            exit_code = int(status) if status.lstrip("-").isdigit() else 1
            if exit_code != 0:
                raise RuntimeError(f"remote command exited with code {exit_code}")
            return b"".join(chunks).decode(errors="replace")
        time.sleep(poll_interval)


def _safe_terminate(account_key, pod_id):
    """A bare `finally: pod_up.terminate(...)` that itself raises would
    silently replace whatever original exception was propagating. Retry
    once, and if it still fails, shout rather than swallow."""
    for attempt in (1, 2):
        try:
            pod_up.terminate(account_key, pod_id)
            log(f"pod {pod_id} terminated")
            return
        except Exception as e:
            log(f"CRITICAL: terminate attempt {attempt} failed for pod {pod_id}: {e}")
            time.sleep(5)
    log(f"CRITICAL: pod {pod_id} may STILL BE RUNNING AND BILLING -- "
        f"terminate manually now (RunPod console)")


def generate_witch_video(
    image_path, text, *, voice_sample=None, tts_device=DEFAULT_TTS_DEVICE, output_path=None,
    min_vram=pod_up.DEFAULT_MIN_VRAM, max_price=pod_up.DEFAULT_MAX_PRICE,
    gpu_match=pod_up.DEFAULT_GPU_MATCH, run_timeout_s=DEFAULT_RUN_TIMEOUT_S,
    start_timeout=600, dry_run=False, tts_only=False,
):
    """Deploy a fresh MuseTalk pod, render image+audio into a video,
    retrieve it, and terminate the pod -- always, even on error/Ctrl-C.
    Returns a GenerationResult, or None if dry_run=True."""
    t0 = time.monotonic()
    image_path = validate_image(image_path)

    job_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
    audio_path = Path("outputs") / f"{job_id}-audio.wav"
    paths = compute_remote_paths(job_id, image_path, audio_path)

    if dry_run:
        log(f"job_id={job_id} (--dry-run: no TTS call, no deploy, no spend)")
        account_key = pod_up.load_account_key()
        ranked = pod_up.rank_gpus(account_key, min_vram, max_price, gpu_match)
        log(f"{len(ranked)} candidate GPU(s):")
        for g in ranked:
            log(f"  {g['id']:<42} {g['vram']:>4}G  ${g['price']:<6} stock={g['stock']}")
        log(f"would run: {build_remote_cmd(paths)}")
        return None

    log(f"job_id={job_id}: generating speech via local Chatterbox TTS "
        f"(device={tts_device}, voice_sample={voice_sample or 'built-in default'}) ...")
    text_to_speech(text, audio_path, voice_sample=voice_sample, device=tts_device)
    log(f"audio ready: {audio_path} ({audio_path.stat().st_size} bytes)")

    if tts_only:
        return GenerationResult(
            local_path=str(audio_path), elapsed_s=time.monotonic() - t0,
            gpu_id="", gpu_price_per_hr=0.0, est_cost_usd=0.0, pod_id="", job_id=job_id)

    account_key = pod_up.load_account_key()
    ranked = pod_up.rank_gpus(account_key, min_vram, max_price, gpu_match)
    if not ranked:
        raise RuntimeError(
            f"No in-stock Secure GPU with >={min_vram:g}GB VRAM under "
            f"${max_price:g}/hr matching /{gpu_match or '.*'}/ right now.")

    public_key = pod_up.load_public_key()
    key_path = pod_up.private_key_path()
    cfg = {
        "image": pod_up.env("IMAGE", pod_up.DEFAULT_IMAGE_REF),
        "pod_name": f"{pod_up.POD_NAME_PREFIX}-{job_id}",
        "container_disk": pod_up.DEFAULT_CONTAINER_DISK_GB,
        "volume_gb": pod_up.DEFAULT_VOLUME_GB,
        "ports": "22/tcp",
        "registry_auth_id": pod_up.env("REGISTRY_AUTH_ID"),
        # Required for MuseTalk's ~4.1GB weights, which live on a network
        # volume rather than being baked into the image -- see
        # docs/superpowers/specs/2026-08-28-musetalk-migration-design.md.
        # data_center_id is derived automatically (network volumes are
        # datacenter-locked), not a separate env var -- mirrors
        # pod_up.py's own main() exactly, so the two can't drift apart.
        "network_volume_id": pod_up.env("NETWORK_VOLUME_ID", pod_up.DEFAULT_NETWORK_VOLUME_ID),
        "data_center_id": None,
    }
    if cfg["network_volume_id"]:
        cfg["data_center_id"] = pod_up.network_volume_dc(account_key, cfg["network_volume_id"])
        log(f"network volume {cfg['network_volume_id']} is in {cfg['data_center_id']} -- deploy pinned to that DC")
    log(f"deploying pod {cfg['pod_name']} ...")
    pod_id, machine, gpu_id, gpu_price = pod_up.deploy_with_fallback(
        account_key, ranked, cfg, public_key, start_timeout)
    log(f"pod {pod_id} running on {gpu_id} @ ${gpu_price}/hr (host {machine})")

    try:
        log("waiting for SSH port mapping to be published ...")
        deadline = time.time() + 120
        endpoint = None
        while time.time() < deadline:
            endpoint = pod_up.get_ssh_endpoint(account_key, pod_id)
            if endpoint:
                break
            time.sleep(5)
        if not endpoint:
            raise RuntimeError(f"Pod {pod_id} never published an SSH port mapping within 120s")
        ip, port = endpoint
        log(f"SSH endpoint {ip}:{port} -- waiting for sshd to accept connections ...")
        if not pod_up.wait_ssh_ready(ip, port, key_path, timeout=180):
            raise RuntimeError(f"SSH never became ready at {ip}:{port} within 180s")
        log("SSH ready")

        log(f"creating remote job dirs under {paths['job_dir']} ...")
        run_ssh(ip, port, key_path,
                f"mkdir -p {shlex.quote(paths['input_dir'])} {shlex.quote(paths['output_dir'])}")

        log("uploading image, audio, and run_musetalk.sh ...")
        scp_up(ip, port, key_path, str(image_path), paths["image"])
        scp_up(ip, port, key_path, str(audio_path), paths["audio"])
        scp_up(ip, port, key_path, str(SCRIPT_DIR / "run_musetalk.sh"), paths["run_script"])
        run_ssh(ip, port, key_path, f"chmod +x {shlex.quote(paths['run_script'])}")

        log("starting generation -- this takes roughly 1-5 minutes on a fresh pod ...")
        log_text = run_remote_detached(ip, port, key_path, build_remote_cmd(paths),
                                        paths["job_dir"], run_timeout_s)

        output_filename = None
        for line in log_text.splitlines():
            if line.strip().startswith(FINAL_OUTPUT_MARKER):
                output_filename = line.strip()[len(FINAL_OUTPUT_MARKER):]
        if not output_filename:
            raise RuntimeError(
                "run_musetalk.sh finished but never printed "
                "'Final output will be: ...' -- can't locate the result")

        local_out = Path(output_path) if output_path else Path("outputs") / f"{job_id}.mp4"
        local_out.parent.mkdir(parents=True, exist_ok=True)
        log(f"downloading result to {local_out} ...")
        scp_down(ip, port, key_path, output_filename, str(local_out))

        elapsed_s = time.monotonic() - t0
        est_cost_usd = gpu_price * elapsed_s / 3600
        log(f"done in {elapsed_s / 60:.1f} min, ~${est_cost_usd:.2f} ({gpu_id} @ ${gpu_price}/hr)")
        return GenerationResult(
            local_path=str(local_out), elapsed_s=elapsed_s, gpu_id=gpu_id,
            gpu_price_per_hr=gpu_price, est_cost_usd=est_cost_usd,
            pod_id=pod_id, job_id=job_id)
    finally:
        _safe_terminate(account_key, pod_id)


def _cli():
    p = argparse.ArgumentParser(
        description="Generate a witch avatar video via an on-demand RunPod MuseTalk pod.")
    p.add_argument("--image", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--voice-sample",
                    help="Path to a local reference audio clip for Chatterbox voice cloning. "
                         "Omit to use Chatterbox's built-in default voice (see Task 5).")
    p.add_argument("--tts-device", default=DEFAULT_TTS_DEVICE,
                    help="torch device for local Chatterbox inference: cuda/cpu/mps "
                         "(default: mps -- override if that path doesn't work on your machine, "
                         "see Task 5 Step 6).")
    p.add_argument("--output")
    p.add_argument("--max-price", type=float, default=pod_up.DEFAULT_MAX_PRICE)
    p.add_argument("--min-vram", type=float, default=pod_up.DEFAULT_MIN_VRAM)
    p.add_argument("--gpu-match", default=pod_up.DEFAULT_GPU_MATCH)
    p.add_argument("--timeout", type=int, default=DEFAULT_RUN_TIMEOUT_S,
                    help="Hard timeout in seconds for the remote render step (default: 600).")
    p.add_argument("--dry-run", action="store_true",
                    help="Rank GPUs and print the command that would run -- no deploy, no TTS call, no spend")
    p.add_argument("--tts-only", action="store_true",
                    help="Generate just the audio locally via Chatterbox, skip the GPU step entirely")
    p.add_argument("--json", action="store_true", help="Print the result as one JSON line")
    args = p.parse_args()

    try:
        result = generate_witch_video(
            args.image, args.text, voice_sample=args.voice_sample, tts_device=args.tts_device,
            output_path=args.output,
            max_price=args.max_price, min_vram=args.min_vram, gpu_match=args.gpu_match,
            run_timeout_s=args.timeout, dry_run=args.dry_run, tts_only=args.tts_only)
    except Exception as e:
        print(f"[generate] FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        return
    assert result is not None  # only None when dry_run=True, handled above
    if args.json:
        print(json.dumps(asdict(result)))
    else:
        print(f"[generate] ready: {result.local_path}")


if __name__ == "__main__":
    _cli()
