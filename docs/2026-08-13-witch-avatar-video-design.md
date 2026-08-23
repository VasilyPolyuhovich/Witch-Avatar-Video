# Witch Avatar Video — design

**Status:** Approved design, ready for implementation planning.
**Date:** 2026-08-13

## Goal

Generate short (5-15s) talking-head video flyers for Instagram, featuring a
fully fictional character — a "gadalka"/witch persona (not a real person).
The character already has generated reference photos and static image
flyers. This project adds: text (Russian) → the character speaking that
text, with approximate lip-sync, adequate facial expression, and (where the
reference photo shows them) natural head motion — reusing the operational
patterns proven in the sibling project `AI-Avatar-Video` (RunPod on-demand
GPU pods, deploy/render/terminate safety contract), deliberately scoped
down to match this project's much lower complexity budget.

## Requirements (gathered during brainstorming)

- **Volume:** low — roughly 1-5 videos/week. Not a high-throughput pipeline;
  simplicity and low fixed cost matter more than raw efficiency.
- **Language:** Russian speech.
- **Voice:** 1-2 consistent character voices (not required to be bit-for-bit
  identical across renders, but recognizably "the gadalka" as a loose brand
  identity).
- **Quality bar:** explicitly NOT hyper-realism. Approximate lip-sync is
  fine. Facial expression/head motion should look "adequate," not
  best-in-class.
- **Duration:** 5-15 seconds per clip.
- **Reference images:** mixed — some are close-up portraits, some are wider
  shots showing hands/body. **Accepted limitation:** the chosen approach
  (SadTalker-class, portrait/head animation only) will only animate the
  face/head region. On wider shots, hands and body stay static in the
  source pose while only the face moves. This is a known, accepted
  constraint, not a bug — see "Alternatives considered" below for why a
  full-body-animation approach was ruled out for now.
- **Budget:** not fixed going in; the chosen approach's marginal cost is
  low (roughly GPU-minutes + pennies of TTS per video, no per-video API
  fee), which was itself part of why it was chosen over a pay-per-video
  hosted API.

**Estimated marginal cost** (rough, to be confirmed by the first real
render): SadTalker's low VRAM requirement opens up GPUs in the
~$0.20-0.40/hr range (vs. `AI-Avatar-Video`'s A100-class ~$1.40-1.60/hr),
and a 5-15s clip should need only a few minutes of pod time (boot + a short
render, no multi-minute `torch.compile`-style warmup). Ballpark: **under
$0.10/video** in GPU time, plus a fraction of a cent in ElevenLabs TTS —
roughly **$0.40-2/month** at 1-5 videos/week. For comparison, the hosted-API
approach considered earlier (VEED Fabric/D-ID/HeyGen) would have run
roughly **$10-30/month** at the same volume. The gap is small in absolute
terms at this volume, but confirms the self-hosted route isn't more
expensive on a per-video basis, only more expensive in setup/maintenance
time — which is the real trade-off being made here.

## Chosen approach: self-hosted SadTalker on RunPod, reusing AI-Avatar-Video patterns

### Why this approach

Reuses this project's own hard-won operational experience (GPU
ranking/deploy/retry, pod-always-terminates safety contract) while
deliberately avoiding the specific pain points that experience surfaced,
because this project's shape is different from `AI-Avatar-Video`'s in ways
that make those pain points avoidable:

- **No 30GB+ checkpoint, no network volume.** SadTalker's weights are a few
  GB, not tens of GB — small enough to bake directly into the Docker image.
  This sidesteps the *entire* class of network-volume I/O-stall/corruption
  bugs that `AI-Avatar-Video` spent significant effort diagnosing and
  mitigating (see that project's `docs/decisions.md`) — there's no
  MooseFS/FUSE-backed persistent volume in this design at all.
- **No flash-attn-style architecture lock-in.** `AI-Avatar-Video`'s LongCat
  image required a from-source flash-attn build pinned to
  `TORCH_CUDA_ARCH_LIST="8.0;9.0"` (A100/H100 only), which combined with a
  DC-locked network volume to produce a real "no GPU available" scarcity
  problem. SadTalker has no comparable build constraint, so the GPU match
  filter can stay broad (any CUDA GPU with enough VRAM), giving far more
  deployable candidates and much lower odds of hitting "no free card."
- **No warm-pod session complexity.** At 1-5 videos/week there's no
  meaningful benefit to `AI-Avatar-Video`'s `PodSession` warm-pod-reuse
  design (built to amortize LongCat's ~5 min `torch.compile` warmup across
  several renders in one UI session). Every render here is a fresh
  "deploy → render → download → terminate" pod, mirroring
  `generate_avatar_video.py`'s simpler CLI-only code path — no Gradio UI,
  no reconnect-resilience, no foreign-pod detection needed (single-user
  tool, not a shared-account multi-colleague tool).

### Alternatives considered, and why they were set aside

- **Hosted talking-photo APIs** (VEED Fabric, D-ID, HeyGen, Banuba): near-zero
  setup, ~$0.30-1.50/video at this length, would have been the pragmatic
  default for a low-volume project. Set aside because the user weighed the
  self-hosted route as having more long-term potential — worth watching
  whether volume/quality needs grow enough to justify a self-hosted
  investment, and this project explicitly wants to build that. Revisit this
  option if the self-hosted pipeline's operational cost (time spent
  debugging/maintaining) starts to exceed what a few dollars a week in API
  fees would have cost.
- **Full-body animation models:** would properly animate hands/gestures on
  the wider reference shots instead of leaving them static, but this is a
  much less mature, more complex open-source space than single-portrait
  lip-sync/head-animation, and out of scope for a first version.
- **Fully local (no cloud GPU):** zero ongoing cost, but needs a capable
  local GPU and manual runs; not pursued as the primary path since RunPod
  on-demand access is already proven and paid for via the sibling project's
  account infrastructure knowledge (a separate RunPod account/key should be
  used for this project, not the shared `AI-Avatar-Video` account/volume,
  to keep billing and blast radius separate).

## Architecture

**Components:**
- `scripts/pod_up.py` — copied from `AI-Avatar-Video` largely as-is (it's
  already generic GPU deploy/rank/retry tooling, not LongCat-specific).
  Adjust defaults: `MIN_VRAM` lowered (e.g. 16GB starting point, to be
  validated against SadTalker's actual footprint during implementation),
  `GPU_MATCH` broadened (no A100/H100-only restriction).
- `docker/sadtalker/` — new Docker image: SadTalker + its weights baked in
  at build time (pin an exact upstream commit, same convention as
  `AI-Avatar-Video`'s pinned LongCat commit), no network volume dependency.
  SSH-accessible, same `entrypoint.sh` shape as the sibling project
  (authorize the deploying key, keep the container alive for SSH-driven
  rendering).
- `scripts/run_sadtalker.sh` — runs ON the pod over SSH, wraps SadTalker's
  inference entrypoint with this project's validated flag choices (analogous
  to `run_longcat_avatar.sh`).
- `scripts/generate_witch_video.py` — the orchestrator, mirroring
  `generate_avatar_video.py`'s one-shot CLI (deploy → upload → render →
  download → terminate, always terminate in `finally`). No PodSession
  equivalent needed.
- TTS step runs **locally**, before any pod exists: text → ElevenLabs API
  (Russian support confirmed; Voice Design or voice library to pick the 1-2
  character voices) → local `audio.mp3`. A bad text/voice-id fails here,
  before any cloud spend.

**CLI shape:**
```bash
python3 scripts/generate_witch_video.py \
  --image path/to/gadalka_portrait.jpg \
  --text "Текст, який каже гадалка..." \
  --voice-id <elevenlabs_voice_id> \
  --output path/to/flyer.mp4
```
Flags to include: `--dry-run` (rank GPUs, print the command, no deploy, no
TTS call — free) and `--tts-only` (generate just the audio locally, skip
the GPU step entirely — lets voice/text iteration happen for pennies before
ever touching a pod).

**Data flow:**
1. Local: text → ElevenLabs TTS → `audio.mp3`.
2. Local: validate image + audio files exist and are non-empty.
3. Rank GPUs, deploy with retry/blocklist (reused `pod_up.py` logic), wait
   for container start + SSH ready.
4. Upload image + audio to the pod.
5. Run `run_sadtalker.sh` over SSH → produces a raw output video.
6. Download the result locally.
7. Terminate the pod — always, in a `finally` block, matching
   `AI-Avatar-Video`'s safety contract (render failure, timeout, or success
   all lead here).
8. **Out of MVP scope:** 9:16 cropping/padding and any branding overlay for
   the "flyer" look. Do this manually (e.g. in CapCut or directly in
   Instagram's own editor) for the first version; only automate it later if
   it becomes a repetitive bottleneck.

## Error handling

Reuses `AI-Avatar-Video`'s proven safety contract directly:
- The pod is **always** terminated — success, render failure, or hard
  timeout all funnel through one `finally`-guarded termination path, with a
  loud, un-swallowed failure message if termination itself ever fails
  (checkable via account balance/RunPod console as the backstop).
- A hard timeout on the render step. Starting default: **600s (10 min)** —
  generous headroom over an expected few-minute render for a 5-15s clip on
  a lighter model than LongCat; adjust after the first real timings are
  observed.
- TTS failures never touch a pod at all (step 1 is entirely local/API,
  before step 3's deploy).
- GPU scarcity handled by the same rank/retry/blocklist logic already
  proven in `pod_up.py`, now with a much less restrictive `GPU_MATCH`,
  meaningfully lowering the odds of hitting "no free card" that
  `AI-Avatar-Video` sometimes ran into with its A100/H100-only constraint.

## Testing / validation approach

- `--dry-run`: confirms GPU candidates and the exact command that would
  run, no spend.
- `--tts-only`: validates voice/text choices cheaply (ElevenLabs pricing is
  per-character, effectively pennies for a 5-15s clip's worth of text) before
  any GPU cost.
- First real end-to-end test: one full run against a real pod, watching
  timing closely to calibrate the render timeout and confirm the `--image`
  variety (portrait vs. wider shot) both produce acceptable results before
  relying on the pipeline for regular use.

## Open questions for implementation planning

- Exact SadTalker fork/commit to pin, and its real VRAM footprint (informs
  final `MIN_VRAM`/`GPU_MATCH` defaults) — validate empirically during
  first implementation pass, same dry-run-first discipline
  `AI-Avatar-Video` used throughout.
- Whether to use a dedicated RunPod account/key for this project (recommended,
  to keep billing and access separate from `AI-Avatar-Video`'s shared
  account) or share the existing one — a deliberate choice to make before
  first deploy, not a default to fall into.
- Which 1-2 ElevenLabs voices to use for the character — pick during
  implementation, informed by a few cheap `--tts-only` trials.
