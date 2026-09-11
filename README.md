# Witch Avatar Video

Generates short (5-15s) talking-head video flyers of a fictional witch/
fortune-teller ("гадалка") character for Instagram, from a reference photo
and a line of Russian-language text: local text-to-speech, then an
on-demand GPU pod renders the lip-synced video and self-terminates.

Full design rationale: [`docs/2026-08-13-witch-avatar-video-design.md`](docs/2026-08-13-witch-avatar-video-design.md).
Empirical findings/decisions log: [`docs/decisions.md`](docs/decisions.md).

## How it works

1. **Text-to-speech runs locally**, on your own machine — [Chatterbox
   Multilingual TTS](https://github.com/resemble-ai/chatterbox) turns your
   Russian text into a `.wav`, optionally cloning a voice from a short
   reference sample. No GPU pod exists yet at this point, so a bad text or
   reference clip costs nothing.
2. **A GPU pod deploys on RunPod on demand** ([EchoMimicV3-Flash](https://github.com/antgroup/echomimic_v3),
   a Wan2.1-Fun-based diffusion model), renders the photo+audio into a
   video, and the pod is **always terminated afterward** — success,
   failure, or Ctrl-C — so nothing is ever left running and billing by
   accident.
3. **Two independent ways to drive this**, both hitting the same
   underlying pod images:
   - `scripts/generate_witch_video.py` — a CLI script, run it and get a
     `.mp4` back.
   - The **WebUI** (`webui/`) — a small password-gated local web page
     (type text, upload a photo, click Generate) for non-technical use.

## Prerequisites

- **Python 3.10+** locally (developed/tested on Apple Silicon, macOS).
- **A RunPod account** with billing set up ([runpod.io](https://runpod.io)) —
  this is what actually runs the GPU render. Typical cost: **~$0.05-0.10
  per generated video** (see [Cost](#cost) below).
- **`ffmpeg`** installed locally (`brew install ffmpeg` on macOS) — used
  for TTS audio post-processing.
- **Docker**, only if you ever need to rebuild the pod images yourself
  (not needed for day-to-day use — the images are already built and
  published to GHCR by CI).

## One-time setup

### 1. Clone and install

```bash
git clone <this-repo-url>
cd Witch-Avatar-Video
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**Known local-setup gotcha:** `chatterbox-tts`'s watermarking dependency
(`resemble-perth`) needs the legacy `pkg_resources` module, which modern
`setuptools` (anything ≥ ~76) no longer ships. If you see `TypeError:
'NoneType' object is not callable` coming from inside Chatterbox, run:

```bash
.venv/bin/pip install "setuptools==75.8.0"
```

(pip will print a resolver warning about `torch` wanting a newer
`setuptools` — that's just a warning, not a real conflict; torch runs
fine.) See `docs/decisions.md` / project memory for the full story if
this keeps recurring.

### 2. Get a RunPod API key and SSH keypair

1. Create an API key at [runpod.io/console/user/settings](https://www.runpod.io/console/user/settings)
   and save it to `~/.runpod-key-witch-video` (a bare text file,
   just the key, no quotes/newline issues).
2. Generate an SSH keypair RunPod pods will trust:
   ```bash
   mkdir -p ~/.runpod/ssh
   ssh-keygen -t rsa -b 4096 -f ~/.runpod/ssh/runpodctl-witch-video-ssh-key -N ""
   ```
   (`scripts/pod_up.py` reads these two paths by default — override via
   `ACCOUNT_KEY_FILE` / `SSH_PUBKEY_FILE` / `SSH_PRIVKEY_FILE` env vars
   if you keep them elsewhere.)

### 3. Set up the network volume (one-time, ~24GB of model weights)

EchoMimicV3's weights (~24GB) live on a RunPod **network volume**, not
baked into the pod image — a volume, once populated, is reused by every
future render without re-downloading anything.

1. Create a 30GB network volume via the RunPod console (Storage → Network
   Volumes), in whichever datacenter has good stock for 40GB+ GPUs (see
   [Known limitations](#known-limitations) on why 40GB+ is required).
   Note its id.
2. Deploy a cheap temporary pod with that volume attached at `/workspace`
   (any small GPU is fine — this step doesn't need real compute, just
   the volume mounted):
   ```bash
   NETWORK_VOLUME_ID=<your-volume-id> MIN_VRAM=8 MAX_PRICE=0.2 \
     .venv/bin/python3 scripts/pod_up.py
   ```
3. SSH into that pod (the command prints the SSH line), upload and run
   the population script, then terminate the pod:
   ```bash
   scp -P <port> -i ~/.runpod/ssh/runpodctl-witch-video-ssh-key \
     scripts/populate_echomimicv3_volume.sh root@<ip>:/root/
   ssh -p <port> -i ~/.runpod/ssh/runpodctl-witch-video-ssh-key root@<ip> \
     "chmod +x /root/populate_echomimicv3_volume.sh && /root/populate_echomimicv3_volume.sh"
   ```
   Expect this to download ~24GB and take a few minutes. It SHA256-verifies
   every pickle-format checkpoint it downloads before finishing.
4. Terminate the temporary pod (via the RunPod console, or
   `podTerminate` through the API) — don't leave it running.
5. Set `DEFAULT_NETWORK_VOLUME_ID` in `scripts/pod_up.py` to your volume's
   id (or export `NETWORK_VOLUME_ID` every time you run a script instead
   of hardcoding it).

You only need to do this once. The images themselves (`witch-avatar-echomimicv3`
and `witch-avatar-echomimicv3-webui`) are already public on GHCR and get
rebuilt automatically by CI whenever `docker/echomimicv3*/` changes — no
manual Docker build needed for normal use.

## Usage

### CLI

```bash
.venv/bin/python3 scripts/generate_witch_video.py \
  --image assets/gadalka_portrait.jpg \
  --text "Добро пожаловать, дитя моё. Огонь свечи откроет тебе то, что скрыто во тьме." \
  --voice-sample assets/gadalka-voice-reference.wav \
  --output outputs/flyer.mp4
```

- `--voice-sample` is optional — omit it to use Chatterbox's built-in
  default voice instead of cloning one.
- `--dry-run` ranks available GPUs and prints what would happen — no TTS
  call, no deploy, no spend. Use this to sanity-check GPU pricing/stock
  before a real run.
- `--tts-only` generates just the local audio file and stops — useful for
  previewing how the text sounds before spending anything on a pod.
- `--max-price` / `--min-vram` override the GPU search (defaults: 40GB
  minimum, $0.60/hr ceiling — see [Known limitations](#known-limitations)).
- Full flag list: `--help`.

The pod is **always** terminated in a `finally` block, including on
Ctrl-C or a crash mid-render — you should never need to manually clean up
a pod after a CLI run.

### WebUI

Start the local backend (this runs on your own machine, not on RunPod —
it's what *drives* the RunPod pod on your behalf):

```bash
WEBUI_PASSWORD=<pick-a-password> \
WEBUI_BACKEND_IMAGE=ghcr.io/vasilypolyuhovich/witch-avatar-echomimicv3-webui:latest \
ACCOUNT_KEY_FILE=~/.runpod-key-witch-video \
.venv/bin/uvicorn webui.backend.app:app --port 8080
```

Then open `http://localhost:8080`, enter the password, and:

1. Click **Start** — deploys a pod (takes ~1-2 minutes to become Ready).
2. Upload a photo, type the text, optionally attach a short voice-clone
   sample.
3. Click **Generate** — the WebUI synthesizes speech locally (same
   Chatterbox step as the CLI) and forwards it to the pod for rendering.
   Expect **~9-10 minutes** for a short clip.
4. Click **Stop** when you're done — or just leave it: the pod has a
   5-minute heartbeat-timeout safety net and self-terminates if the
   WebUI stops sending heartbeats (browser closed, laptop asleep, etc.),
   so an unattended session can't run up costs indefinitely.

The page supports English and Ukrainian (switch in the header).

## Cost

- **GPU render time:** ~$0.05-0.10 per video on the cheapest available
  ≥40GB card (usually an NVIDIA A40 at $0.49/hr; a full render —
  deploy + model load + generation + download — takes ~9-10 minutes).
- **Network volume storage:** a fixed, ongoing cost independent of how
  many videos you generate — roughly $0.07/GB/month, so **~$2/month**
  for the 30GB EchoMimicV3 volume. This bills whether or not you're
  actively rendering anything; delete the volume via the RunPod console
  if you're pausing the project for a while.
- **Local TTS and the WebUI backend itself cost nothing** — they run on
  your own machine.

## Known limitations

- **Needs a ≥40GB GPU.** A real test on a 24GB card (RTX A5000) ran out
  of memory loading the model even with its own default low-VRAM mode.
  Confirmed working on 48GB (NVIDIA A40). Don't lower `MIN_VRAM` below
  40 without re-testing.
- **~5.5 second cap per single generation** (138 frames at 25fps) is
  EchoMimicV3-Flash's own documented limit for a single pass without its
  "Long Video CFG" mode, which this project hasn't wired in/verified yet.
  `scripts/run_echomimicv3.sh` and the WebUI backend both compute the
  needed frame count from your actual audio length and **cap it at 138
  frames with a loud warning** rather than silently producing something
  wrong — audio past that point gets cut off. If you need longer clips
  routinely, this needs follow-up work (see `docs/superpowers/plans/`
  for the migration plan's Task 11 notes).
- **GPU availability can be patchy in the network volume's datacenter.**
  A network volume pins every deploy to one specific RunPod datacenter
  (volumes aren't portable between datacenters). If deploys start failing
  with "supply refused" for every GPU candidate, that specific
  datacenter may be temporarily out of stock for your VRAM tier — retry
  in a few minutes, or raise `--max-price` to reach a pricier card that's
  still in that DC.
- **Chatterbox's Russian stress-marking (наголоси) is not enabled.** The
  underlying package (`russian_text_stresser`) is unmaintained and known
  to have installation issues on modern Python; TTS output has correct
  Russian pronunciation for unambiguous words but doesn't disambiguate
  stress-dependent word pairs. General intonation/expressiveness is
  already tuned via the `exaggeration`/`cfg_weight` parameters in
  `scripts/generate_witch_video.py`.
- **The rendered video doesn't add or animate a separate background** —
  EchoMimicV3 generates the whole frame from your source photo; whatever
  background is already in that photo is what appears (with some natural
  secondary motion near the subject — candle flicker, fabric — but not
  independent scene animation). To use a different background, edit the
  source photo before generating, not after.

## Repository layout

| Path | What it is |
|---|---|
| `scripts/generate_witch_video.py` | CLI entry point |
| `scripts/pod_up.py` | RunPod GPU deploy/rank/terminate primitives, shared by both front doors |
| `scripts/run_echomimicv3.sh` | Runs on the pod for the CLI path; wraps EchoMimicV3's inference script |
| `scripts/populate_echomimicv3_volume.sh` | One-time network-volume weight download (see [setup](#3-set-up-the-network-volume-one-time-24gb-of-model-weights)) |
| `docker/echomimicv3/` | Pod image for the CLI path (SSH-only, no HTTP server) |
| `docker/echomimicv3-webui/` | Pod image for the WebUI path (small HTTP API: `/health`, `/generate`, `/status`, `/result`) |
| `docker/webui-stub/` | A throwaway fake backend (canned test clip, no real GPU inference) used to build/test the WebUI's HTTP plumbing before the real backend existed — kept for reference, not part of the real pipeline |
| `webui/backend/` | The local FastAPI server you run yourself; drives a `docker/echomimicv3-webui` pod over HTTP |
| `webui/frontend/` | The static page `webui/backend` serves |
| `tests/` | pytest suite (`./.venv/bin/python -m pytest tests/`) |
| `docs/2026-08-13-witch-avatar-video-design.md` | Original design spec |
| `docs/decisions.md` | Empirical findings log, updated as things are discovered |
