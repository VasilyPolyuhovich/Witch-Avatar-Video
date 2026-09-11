import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

WEBUI_DIR = Path(__file__).resolve().parent.parent.parent / "webui"
sys.path.insert(0, str(WEBUI_DIR.parent))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WEBUI_BACKEND_IMAGE", "fake/image:latest")
    monkeypatch.setenv("ACCOUNT_KEY_FILE", str(Path(__file__).resolve().parent / "fake_key.txt"))
    Path(__file__).resolve().parent.joinpath("fake_key.txt").write_text("fake-runpod-key")
    from webui.backend import app as app_module
    import importlib
    importlib.reload(app_module)
    return TestClient(app_module.app)


def test_start_calls_session_start_and_returns_pod_id(client):
    from webui.backend import app as app_module
    with patch.object(app_module.session, "start", return_value="pod-123") as mock_start:
        resp = client.post("/api/start")
    assert resp.status_code == 200
    assert resp.json() == {"pod_id": "pod-123"}
    mock_start.assert_called_once()


def test_start_returns_503_when_no_gpu_available(client):
    from webui.backend import app as app_module
    with patch.object(app_module.session, "start", side_effect=RuntimeError("no_gpu_available")):
        resp = client.post("/api/start")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "no_gpu_available"


def test_stop_calls_session_stop(client):
    from webui.backend import app as app_module
    with patch.object(app_module.session, "stop") as mock_stop:
        resp = client.post("/api/stop")
    assert resp.status_code == 200
    mock_stop.assert_called_once()


def test_status_reports_inactive_when_no_pod(client):
    from webui.backend import app as app_module
    app_module.session.pod_id = None
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is False
    assert body["ready"] is False


def test_status_polls_health_when_pod_active(client):
    from webui.backend import app as app_module
    app_module.session.pod_id = "pod-123"
    with patch.object(app_module.session, "poll_health", return_value=True), \
         patch.object(app_module.session, "elapsed_seconds", return_value=42.0):
        resp = client.get("/api/status")
    body = resp.json()
    assert body == {"active": True, "ready": True, "elapsed_seconds": 42.0}
    app_module.session.pod_id = None


def test_generate_returns_409_when_not_ready(client):
    from webui.backend import app as app_module
    app_module.session.base_url = None
    resp = client.post(
        "/api/generate",
        data={"text": "hello"}, files={"image": ("photo.jpg", b"fake", "image/jpeg")})
    assert resp.status_code == 409


def _fake_tts(text, output_path, *, voice_sample=None, device=None):
    Path(output_path).write_bytes(b"synthesized-audio-bytes")


def test_generate_forwards_to_session_when_ready(client):
    from webui.backend import app as app_module
    app_module.session.base_url = "http://1.2.3.4:18000"
    with patch.object(app_module.generate_witch_video, "text_to_speech", side_effect=_fake_tts), \
         patch.object(app_module.session, "generate", return_value="job-1") as mock_generate:
        resp = client.post(
            "/api/generate",
            data={"text": "hello"}, files={"image": ("photo.jpg", b"fake", "image/jpeg")})
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "job-1"}
    mock_generate.assert_called_once()
    app_module.session.base_url = None


def test_generate_ignores_path_traversal_in_voice_filename(client):
    """voice.filename is client-controlled over this network-facing
    endpoint -- a name like '../../etc/cron.d/x' must never be joined
    into a filesystem path. Found by automated commit review, 2026-09-10."""
    from webui.backend import app as app_module
    app_module.session.base_url = "http://1.2.3.4:18000"
    captured = {}

    def fake_tts(text, output_path, *, voice_sample=None, device=None):
        captured["voice_sample_path"] = str(voice_sample)
        Path(output_path).write_bytes(b"synthesized-audio-bytes")

    with patch.object(app_module.generate_witch_video, "text_to_speech", side_effect=fake_tts), \
         patch.object(app_module.session, "generate", return_value="job-1"):
        resp = client.post(
            "/api/generate",
            data={"text": "hello"},
            files={"image": ("photo.jpg", b"fake-image", "image/jpeg"),
                   "voice": ("../../etc/cron.d/evil.wav", b"fake-voice", "audio/wav")})
    assert resp.status_code == 200
    assert ".." not in captured["voice_sample_path"]
    assert captured["voice_sample_path"].endswith("voice_sample.wav")
    app_module.session.base_url = None


def test_generate_synthesizes_speech_locally_instead_of_forwarding_raw_upload(client):
    """The 'voice' upload is a voice-CLONING reference sample (see
    webui/frontend/i18n.js's 'Voice sample (optional)' label), not
    ready-to-use driving audio -- the backend must run TTS on `text`
    itself and forward the *synthesized* audio to the pod, not the raw
    uploaded clip. Found via a user question, 2026-09-10 -- the pod-side
    contract (docker/echomimicv3-webui/app.py) already expected
    synthesized audio; this local backend was blindly pass-through
    forwarding the clone sample instead."""
    from webui.backend import app as app_module
    app_module.session.base_url = "http://1.2.3.4:18000"
    with patch.object(app_module.generate_witch_video, "text_to_speech", side_effect=_fake_tts) as mock_tts, \
         patch.object(app_module.session, "generate", return_value="job-1") as mock_generate:
        resp = client.post(
            "/api/generate",
            data={"text": "Привіт, світе"},
            files={"image": ("photo.jpg", b"fake-image", "image/jpeg"),
                   "voice": ("clone-sample.wav", b"raw-clone-sample-bytes", "audio/wav")})
    assert resp.status_code == 200

    mock_tts.assert_called_once()
    assert mock_tts.call_args.args[0] == "Привіт, світе"
    assert mock_tts.call_args.kwargs["voice_sample"] is not None

    mock_generate.assert_called_once()
    generate_args = mock_generate.call_args.args
    # session.generate(image_bytes, image_filename, text, voice_bytes, voice_filename)
    assert generate_args[3] == b"synthesized-audio-bytes"
    assert generate_args[3] != b"raw-clone-sample-bytes"
    app_module.session.base_url = None
