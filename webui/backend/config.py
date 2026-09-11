"""Configuration for the local WebUI: the unlock password and RunPod
credentials it needs to manage a pod's lifecycle. See
docs/superpowers/specs/2026-08-30-webui-design.md -- the RunPod account
key is loaded via pod_up.load_account_key() (a file path, scoped/capped
key recommended per the spec), kept separate from WEBUI_PASSWORD, which
is a local app-unlock secret, not a RunPod credential."""
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import pod_up  # noqa: E402


def load_webui_password():
    password = os.environ.get("WEBUI_PASSWORD")
    if not password:
        raise RuntimeError("WEBUI_PASSWORD environment variable is not set")
    return password


def load_backend_image_ref():
    image_ref = os.environ.get("WEBUI_BACKEND_IMAGE")
    if not image_ref:
        raise RuntimeError("WEBUI_BACKEND_IMAGE environment variable is not set")
    return image_ref


def load_runpod_account_key():
    return pod_up.load_account_key()
