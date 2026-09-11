"""Configuration for the local WebUI: the RunPod credentials it needs to
manage a pod's lifecycle. See docs/superpowers/specs/2026-08-30-webui-design.md
-- the RunPod account key is loaded via pod_up.load_account_key() (a file
path, scoped/capped key recommended per the spec).

Originally also gated the UI behind a WEBUI_PASSWORD unlock secret (for a
"distribute an installed copy with an embedded key to a client" scenario).
Removed 2026-09-11: the actual usage is a single operator running this on
their own machine via start_webui.sh, bound to 127.0.0.1, with a RunPod
key they provide themselves -- a login prompt guarded nothing real there
and just added friction."""
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import pod_up  # noqa: E402


def load_backend_image_ref():
    image_ref = os.environ.get("WEBUI_BACKEND_IMAGE")
    if not image_ref:
        raise RuntimeError("WEBUI_BACKEND_IMAGE environment variable is not set")
    return image_ref


def load_runpod_account_key():
    return pod_up.load_account_key()
