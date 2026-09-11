"""Wraps scripts/pod_up.py's deploy/rank/terminate functions into a
stateful session object the WebUI backend can drive: start a pod, poll
whether it's ready, forward generate/status/result calls to its HTTP
API, and stop it. See
docs/superpowers/specs/2026-08-30-webui-design.md."""
import sys
import time
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import pod_up  # noqa: E402

# The port the backend's HTTP API (whatever Docker image implements the
# /health,/heartbeat,/generate,/status,/result contract) listens on
# inside the pod.
HTTP_PRIVATE_PORT = 8000

# Confirmed via a real Task 8 deploy (2026-08-31): RunPod accepts this
# mixed tcp+http format and publishes both ports in runtime.ports.
DEFAULT_PORTS = "22/tcp,8000/http"

# Raised from 8.0 (the stub backend's arbitrary placeholder) to match
# scripts/pod_up.py's own EchoMimicV3 floor -- see
# docs/superpowers/plans/2026-09-09-echomimicv3-migration-implementation.md,
# Task 6, for why 40.0 and not something lower.
DEFAULT_MIN_VRAM = 40.0
# Raised from 0.60 2026-09-11, same rationale as scripts/pod_up.py's own
# DEFAULT_MAX_PRICE: a render finishes in ~10-15 min and the pod
# terminates right after, so a pricier card only adds cents per video --
# worth it for the much wider GPU pool it unlocks.
DEFAULT_MAX_PRICE = 3.00
# Excludes AMD/ROCm cards -- our image is CUDA-only (cu124).
DEFAULT_GPU_MATCH = "^(?!AMD)"
DEFAULT_CONTAINER_DISK_GB = 20
DEFAULT_START_TIMEOUT_S = 600


class RunPodSession:
    def __init__(self, account_key, image_ref):
        self.account_key = account_key
        self.image_ref = image_ref
        self.pod_id = None
        self.base_url = None
        self.started_at = None

    def start(self):
        cfg = {
            "image": self.image_ref,
            "pod_name": f"{pod_up.POD_NAME_PREFIX}-webui",
            "container_disk": DEFAULT_CONTAINER_DISK_GB,
            "volume_gb": pod_up.DEFAULT_VOLUME_GB,
            "ports": DEFAULT_PORTS,
            "registry_auth_id": pod_up.env("REGISTRY_AUTH_ID"),
            "network_volume_id": pod_up.env("NETWORK_VOLUME_ID", pod_up.DEFAULT_NETWORK_VOLUME_ID),
            "data_center_id": None,
            # The pod's own heartbeat-timeout safety net calls RunPod's
            # terminate API on itself (see docs/superpowers/specs/2026-08-30-
            # webui-design.md's Cost safety section) -- it needs this same
            # scoped key, not a second credential. RUNPOD_POD_ID is already
            # auto-injected by RunPod into every pod, so only the key needs
            # passing explicitly.
            "extra_env": {"RUNPOD_API_KEY": self.account_key},
        }
        if cfg["network_volume_id"]:
            cfg["data_center_id"] = pod_up.network_volume_dc(self.account_key, cfg["network_volume_id"])

        ranked = pod_up.rank_gpus(self.account_key, DEFAULT_MIN_VRAM, DEFAULT_MAX_PRICE, DEFAULT_GPU_MATCH)
        if not ranked:
            raise RuntimeError(
                f"no_gpu_available: no in-stock Secure GPU with >={DEFAULT_MIN_VRAM:g}GB "
                f"VRAM under ${DEFAULT_MAX_PRICE:g}/hr matching /{DEFAULT_GPU_MATCH}/ "
                "right now. RunPod GPU stock changes minute-to-minute -- wait a bit "
                "and click Start again."
            )

        public_key = pod_up.load_public_key()
        pod_id, _machine, _gpu_id, _gpu_price = pod_up.deploy_with_fallback(
            self.account_key, ranked, cfg, public_key, DEFAULT_START_TIMEOUT_S)

        self.pod_id = pod_id
        self.started_at = time.monotonic()
        self._refresh_endpoint()
        return pod_id

    def stop(self):
        if self.pod_id:
            pod_up.terminate(self.account_key, self.pod_id)
        self.pod_id = None
        self.base_url = None
        self.started_at = None

    def _refresh_endpoint(self):
        """Builds base_url from RunPod's proxy domain, not the raw ip:port
        pod_up.get_port_endpoint() returns. Confirmed against a real Task 8
        deploy (2026-08-31) and RunPod's own docs
        (docs.runpod.io/pods/configuration/expose-ports): hosts without a
        public IP for a given port report `isIpPublic: false` and an
        internal-only overlay IP for HTTP-type ports, which is unreachable
        from outside RunPod's network -- the proxy domain is the only
        universally-correct way to reach an HTTP port. get_port_endpoint's
        return value is used only to confirm the port has been published in
        runtime.ports yet, not to build the URL itself."""
        if self.pod_id and not self.base_url:
            endpoint = pod_up.get_port_endpoint(self.account_key, self.pod_id, HTTP_PRIVATE_PORT)
            if endpoint:
                self.base_url = f"https://{self.pod_id}-{HTTP_PRIVATE_PORT}.proxy.runpod.net"

    def poll_health(self):
        """Sends a heartbeat to the pod (this IS the heartbeat -- see
        this module's docstring) and returns whether it reports itself
        ready. Returns False, without raising, on any connection error --
        'not reachable yet' is a normal state while the pod boots."""
        if not self.pod_id:
            return False
        self._refresh_endpoint()
        if not self.base_url:
            return False
        try:
            requests.post(f"{self.base_url}/heartbeat", timeout=5)
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            return bool(resp.json().get("ready", False))
        except requests.RequestException:
            return False

    def elapsed_seconds(self):
        if self.started_at is None:
            return 0
        return time.monotonic() - self.started_at

    def generate(self, image_bytes, image_filename, text, voice_bytes, voice_filename):
        files = {"image": (image_filename, image_bytes)}
        if voice_bytes:
            files["voice"] = (voice_filename, voice_bytes)
        resp = requests.post(f"{self.base_url}/generate", data={"text": text}, files=files, timeout=30)
        resp.raise_for_status()
        return resp.json()["job_id"]

    def get_status(self, job_id):
        resp = requests.get(f"{self.base_url}/status/{job_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_result(self, job_id):
        resp = requests.get(f"{self.base_url}/result/{job_id}", timeout=30)
        resp.raise_for_status()
        return resp.content
