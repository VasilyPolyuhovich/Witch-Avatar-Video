#!/usr/bin/env python3
"""GPU deploy/rank/retry/terminate primitives for Witch-Avatar-Video's
on-demand render pods -- adapted from AI-Avatar-Video's scripts/pod_up.py
(see docs/2026-08-13-witch-avatar-video-design.md for why this project
reuses that project's operational patterns). Originally deliberately
simpler than the original: no network volume (SadTalker's baked-in
checkpoints were under 1GB, small enough to live in the Docker image
itself), no A100/H100-only GPU_MATCH (no flash-attn-style architecture
lock-in), no foreign-pod detection (single-user tool, not a shared
account). Network volume support was reintroduced 2026-08-28 when the
backend switched to MuseTalk, whose ~4.1GB of weights don't fit that
"bake into the image" rationale -- see
docs/superpowers/specs/2026-08-28-musetalk-migration-design.md. The
GPU_MATCH/foreign-pod-detection simplifications still hold.

Usage:
    python3 scripts/pod_up.py                # deploy, print SSH command
    python3 scripts/pod_up.py --dry-run       # just rank GPUs by stock/price

Env knobs: IMAGE, MIN_VRAM (default 16), MAX_PRICE (default 0.60), GPU_MATCH
    (default "" -- no restriction), CONTAINER_DISK_GB (default 30),
    POD_NAME, REGISTRY_AUTH_ID (unset by default -- see the note on main()
    below), ACCOUNT_KEY_FILE, SSH_PUBKEY_FILE, START_TIMEOUT (default 600s),
    MAX_TRIES_PER_GPU (default 2), NETWORK_VOLUME_ID (unset by default --
    required for MuseTalk's model weights, see
    docs/superpowers/specs/2026-08-28-musetalk-migration-design.md; the
    matching datacenter is looked up automatically via the RunPod API,
    see network_volume_dc() -- no separate DATA_CENTER_ID env var, so the
    two can never drift out of sync. Network volumes are datacenter-
    locked, so setting this pins GPU search to that one datacenter and
    reintroduces SUPPLY_CONSTRAINT risk).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

API = "https://api.runpod.io/graphql"
UA = "witch-avatar-video-pod-up/1.0"  # default python-urllib UA gets 403'd by the RunPod WAF

POD_NAME_PREFIX = "witch-avatar-video"
STOCK_RANK = {"High": 0, "Medium": 1, "Low": 2}

DEFAULT_MIN_VRAM = 16.0
DEFAULT_MAX_PRICE = 0.60
DEFAULT_GPU_MATCH = ""  # deliberately unrestricted -- SadTalker has no per-arch build constraint
DEFAULT_CONTAINER_DISK_GB = 30
DEFAULT_VOLUME_GB = 10  # small pod volume, unused for anything but required by the deploy API
# Points at MuseTalk, not SadTalker, as of the 2026-08-28 migration (see
# docs/superpowers/specs/2026-08-28-musetalk-migration-design.md) --
# docker/sadtalker/ is kept as a non-wired-in fallback, not the active path.
DEFAULT_IMAGE_REF = "ghcr.io/vasilypolyuhovich/witch-avatar-musetalk:latest"
DEFAULT_NETWORK_VOLUME_ID = None
DEFAULT_SSH_PUBKEY_FILE = "~/.runpod/ssh/runpodctl-witch-video-ssh-key.pub"
DEFAULT_SSH_PRIVKEY_FILE = "~/.runpod/ssh/runpodctl-witch-video-ssh-key"
DEFAULT_ACCOUNT_KEY_FILE = "~/.runpod-key-witch-video"


def env(name, default=None):
    return os.environ.get(name, default)


def read_file(path):
    with open(os.path.expanduser(path)) as f:
        return f.read().strip()


def load_account_key():
    path = env("ACCOUNT_KEY_FILE", DEFAULT_ACCOUNT_KEY_FILE)
    try:
        return read_file(path)
    except OSError as e:
        sys.exit(f"ERROR: cannot read RunPod account key from {path}: {e}")


def load_public_key():
    path = env("SSH_PUBKEY_FILE", DEFAULT_SSH_PUBKEY_FILE)
    try:
        return read_file(path)
    except OSError:
        print(f"[pod_up] WARNING: no SSH public key at {path} -- pod will "
              f"deploy without PUBLIC_KEY; SSH access won't be authorized.")
        return None


def private_key_path():
    return os.path.expanduser(env("SSH_PRIVKEY_FILE", DEFAULT_SSH_PRIVKEY_FILE))


def gql(account_key, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"Bearer {account_key}",
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def rank_gpus(account_key, min_vram, max_price, gpu_match):
    q = """query{gpuTypes{id memoryInGb secureCloud securePrice
      lowestPrice(input:{gpuCount:1,secureCloud:true}){stockStatus uninterruptablePrice}}}"""
    data = gql(account_key, q)
    gpus = (data.get("data") or {}).get("gpuTypes")
    if gpus is None:
        sys.exit(f"ERROR: gpuTypes query failed: {json.dumps(data)[:300]}")
    out = []
    for g in gpus:
        vram = g.get("memoryInGb") or 0
        price = g.get("securePrice")
        stock = (g.get("lowestPrice") or {}).get("stockStatus")
        if not g.get("secureCloud") or vram < min_vram:
            continue
        if gpu_match and not re.search(gpu_match, g["id"], re.IGNORECASE):
            continue
        if stock not in STOCK_RANK:  # None/unknown => not buyable right now
            continue
        if price is None or price > max_price:
            continue
        out.append({"id": g["id"], "vram": vram, "price": price, "stock": stock})
    out.sort(key=lambda x: (x["price"], STOCK_RANK[x["stock"]]))
    return out


def build_env_list(public_key):
    return [{"key": "PUBLIC_KEY", "value": public_key}] if public_key else []


def network_volume_dc(account_key, vol_id):
    """The datacenter a network volume lives in -- required alongside
    networkVolumeId in deploy() since volumes are datacenter-locked.
    Looked up via the API rather than taken as a separate env var so the
    two can never drift out of sync (ported from the runpod-pod-ops
    skill's reference pod_up.py, which uses this exact pattern)."""
    data = gql(account_key, "query{myself{networkVolumes{id dataCenterId}}}")
    vols = ((data.get("data") or {}).get("myself") or {}).get("networkVolumes") or []
    for v in vols:
        if v["id"] == vol_id:
            return v["dataCenterId"]
    sys.exit(f"ERROR: network volume {vol_id} not found on this account")


def deploy(account_key, gpu_id, cfg, public_key):
    inp = {
        "cloudType": "SECURE",
        "gpuTypeId": gpu_id,
        "gpuCount": 1,
        "name": cfg["pod_name"],
        "imageName": cfg["image"],
        "containerDiskInGb": cfg["container_disk"],
        "volumeMountPath": "/workspace",
        "ports": cfg["ports"],
        "env": build_env_list(public_key),
    }
    if cfg["registry_auth_id"]:
        inp["containerRegistryAuthId"] = cfg["registry_auth_id"]
    # Reintroduced 2026-08-28 for MuseTalk's ~4.1GB weights (too big to
    # bake into the image, unlike SadTalker's <1GB) -- see
    # docs/superpowers/specs/2026-08-28-musetalk-migration-design.md.
    # Network volumes are datacenter-locked, so dataCenterId is set
    # alongside it -- this pins GPU search to one datacenter, reintroducing
    # SUPPLY_CONSTRAINT risk the rest of this module's unrestricted
    # GPU_MATCH design otherwise avoids. volumeInGb (an ephemeral pod
    # volume) is only requested when there's NO network volume -- the two
    # are alternatives, not additive (matches the runpod-pod-ops skill's
    # reference pod_up.py).
    if cfg["network_volume_id"]:
        inp["networkVolumeId"] = cfg["network_volume_id"]
        inp["dataCenterId"] = cfg["data_center_id"]
    else:
        inp["volumeInGb"] = cfg["volume_gb"]
    mut = ("mutation($input:PodFindAndDeployOnDemandInput!){"
           "podFindAndDeployOnDemand(input:$input){id imageName machineId}}")
    return gql(account_key, mut, {"input": inp})


def pod_status(account_key, pod_id):
    q = ("query{pod(input:{podId:%s}){desiredStatus machineId "
         "runtime{uptimeInSeconds}}}" % json.dumps(pod_id))
    p = (gql(account_key, q).get("data") or {}).get("pod") or {}
    rt = p.get("runtime") or {}
    return p.get("desiredStatus"), p.get("machineId"), (rt.get("uptimeInSeconds") or 0)


def terminate(account_key, pod_id):
    gql(account_key, "mutation{podTerminate(input:{podId:%s})}" % json.dumps(pod_id))


def wait_container_start(account_key, pod_id, machine, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, m, up = pod_status(account_key, pod_id)
        if m:
            machine = m
        if up and up > 0:
            return up, machine
        time.sleep(15)
    return 0, machine


def get_ssh_endpoint(account_key, pod_id):
    q = ("query{pod(input:{podId:%s}){runtime{ports{ip isIpPublic "
         "publicPort privatePort type}}}}" % json.dumps(pod_id))
    p = (gql(account_key, q).get("data") or {}).get("pod") or {}
    for prt in ((p.get("runtime") or {}).get("ports")) or []:
        if prt.get("privatePort") == 22 and prt.get("type") == "tcp":
            return prt.get("ip"), prt.get("publicPort")
    return None


def ssh_flags(key_path):
    return ["-i", key_path,
            "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes"]


def wait_ssh_ready(ip, port, key_path, timeout=180, interval=5):
    deadline = time.time() + timeout
    cmd = ["ssh", "-p", str(port), *ssh_flags(key_path),
           "-o", "ConnectTimeout=10", f"root@{ip}", "true"]
    while time.time() < deadline:
        try:
            if subprocess.run(cmd, capture_output=True, timeout=15).returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(interval)
    return False


def deploy_with_fallback(account_key, ranked, cfg, public_key, start_timeout, max_tries=2):
    """Try each ranked GPU candidate; blocklist hosts that accept the deploy
    but never boot (uptime stays 0), retry the next candidate. Returns
    (pod_id, machine, gpu_id, gpu_price) for the first pod that actually
    starts. Raises RuntimeError if every candidate fails. Ported from
    AI-Avatar-Video's pod_up.py, which needed this after real 2026-07-24
    "broken host" incidents -- see that project's module docstring."""
    blocklist = set()
    for g in ranked:
        for attempt in range(1, max_tries + 1):
            print(f"[pod_up] trying {g['id']} @ ${g['price']}/hr "
                  f"(stock={g['stock']}, attempt {attempt}/{max_tries}) ...")
            res = deploy(account_key, g["id"], cfg, public_key)
            pod = (res.get("data") or {}).get("podFindAndDeployOnDemand")
            if not (pod and pod.get("id")):
                err = (res.get("errors") or [{}])[0].get("message", "unknown")
                print(f"[pod_up]   supply refused: {err}")
                break  # this GPU is out of stock -> next candidate
            pid = pod["id"]
            machine = pod.get("machineId")
            if machine and machine in blocklist:
                print(f"[pod_up]   landed on known-bad host {machine} again -> terminating, retry")
                terminate(account_key, pid)
                continue
            print(f"[pod_up] deployed {pid} on host {machine or '?'}; "
                  f"verifying container start (<= {start_timeout // 60} min) ...")
            up, machine = wait_container_start(account_key, pid, machine, start_timeout)
            if up > 0:
                print(f"[pod_up] CONTAINER STARTED (uptime={up}s, host {machine}) -- pod {pid}")
                return pid, machine, g["id"], g["price"]
            print(f"[pod_up]   host {machine or '?'} never started the container "
                  f"(broken host / device-node fault) -> terminating + blocklisting")
            terminate(account_key, pid)
            if machine:
                blocklist.add(machine)

    raise RuntimeError(
        "No candidate could start a container. Blocklisted hosts: "
        + (", ".join(sorted(blocklist)) or "none")
        + ". Supply may be short or hosts flaky -- retry later or raise MAX_PRICE.")


def main():
    args = set(sys.argv[1:])
    min_vram = float(env("MIN_VRAM") or str(DEFAULT_MIN_VRAM))
    max_price = float(env("MAX_PRICE") or str(DEFAULT_MAX_PRICE))
    gpu_match = env("GPU_MATCH", DEFAULT_GPU_MATCH)
    account_key = load_account_key()

    ranked = rank_gpus(account_key, min_vram, max_price, gpu_match)
    if not ranked:
        sys.exit(f"No in-stock Secure GPU with >={min_vram:g}GB VRAM under ${max_price:g}/hr "
                  f"matching /{gpu_match}/ right now.")

    print(f"Candidates (>= {min_vram:g}GB, Secure, in stock, <= ${max_price:g}/hr, "
          f"matching /{gpu_match or '.*'}/), best first:")
    for g in ranked:
        print(f"  {g['id']:<42} {g['vram']:>4}G  ${g['price']:<6} stock={g['stock']}")
    if "--dry-run" in args:
        return

    public_key = load_public_key()
    cfg = {
        "image": env("IMAGE", DEFAULT_IMAGE_REF),
        "pod_name": env("POD_NAME", POD_NAME_PREFIX),
        "container_disk": int(env("CONTAINER_DISK_GB") or str(DEFAULT_CONTAINER_DISK_GB)),
        "volume_gb": int(env("VOLUME_GB") or str(DEFAULT_VOLUME_GB)),
        "ports": env("PORTS", "22/tcp"),
        # Unset by default: if the GHCR package this image gets pushed to
        # defaults to private (GitHub Actions' publish token can push but
        # can't flip visibility -- confirmed the hard way in AI-Avatar-Video,
        # see that project's pod_up.py docstring), create a RunPod saved
        # registry credential (`saveRegistryAuth`) and set this env var to
        # its id, or make the package public instead. See Task 5 in
        # docs/superpowers/plans/2026-08-23-witch-avatar-video-implementation.md.
        "registry_auth_id": env("REGISTRY_AUTH_ID"),
        "network_volume_id": env("NETWORK_VOLUME_ID", DEFAULT_NETWORK_VOLUME_ID),
        "data_center_id": None,
    }
    if cfg["network_volume_id"]:
        cfg["data_center_id"] = network_volume_dc(account_key, cfg["network_volume_id"])
        print(f"[pod_up] network volume {cfg['network_volume_id']} is in "
              f"{cfg['data_center_id']} -- deploy pinned to that DC")

    start_timeout = int(env("START_TIMEOUT") or "600")
    max_tries = int(env("MAX_TRIES_PER_GPU") or "2")

    try:
        pid, machine, gpu_id, gpu_price = deploy_with_fallback(
            account_key, ranked, cfg, public_key, start_timeout, max_tries)
    except RuntimeError as e:
        sys.exit(str(e))
    print(f"[pod_up] pod {pid} running {gpu_id} @ ${gpu_price}/hr on host {machine}")

    endpoint = get_ssh_endpoint(account_key, pid)
    if endpoint:
        ip, port = endpoint
        print(f"[pod_up] SSH: ssh -p {port} -i {private_key_path()} "
              f"-o PubkeyAcceptedAlgorithms=+ssh-rsa -o StrictHostKeyChecking=no root@{ip}")
    else:
        print("[pod_up] SSH port mapping not published yet -- check the RunPod console's Connect panel")


if __name__ == "__main__":
    main()
