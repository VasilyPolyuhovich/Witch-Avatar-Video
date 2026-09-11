import sys
from pathlib import Path

import pytest

WEBUI_DIR = Path(__file__).resolve().parent.parent.parent / "webui"
sys.path.insert(0, str(WEBUI_DIR.parent))
from webui.backend import config  # noqa: E402


def test_load_backend_image_ref_reads_env_var(monkeypatch):
    monkeypatch.setenv("WEBUI_BACKEND_IMAGE", "ghcr.io/example/some-image:latest")
    assert config.load_backend_image_ref() == "ghcr.io/example/some-image:latest"


def test_load_backend_image_ref_raises_when_unset(monkeypatch):
    monkeypatch.delenv("WEBUI_BACKEND_IMAGE", raising=False)
    with pytest.raises(RuntimeError, match="WEBUI_BACKEND_IMAGE"):
        config.load_backend_image_ref()
