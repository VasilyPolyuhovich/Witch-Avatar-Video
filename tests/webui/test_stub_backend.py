import sys
import time
from pathlib import Path
from unittest.mock import patch

STUB_DIR = Path(__file__).resolve().parent.parent.parent / "docker" / "webui-stub"
sys.path.insert(0, str(STUB_DIR))

from fastapi.testclient import TestClient  # noqa: E402
import app as stub_app  # noqa: E402


def test_health_is_not_ready_immediately_after_import():
    stub_app.READY_AT = time.monotonic() + 999  # not ready yet
    client = TestClient(stub_app.app)
    resp = client.get("/health")
    assert resp.json() == {"ready": False}


def test_health_is_ready_once_warmup_elapsed():
    stub_app.READY_AT = time.monotonic() - 1  # already past
    client = TestClient(stub_app.app)
    resp = client.get("/health")
    assert resp.json() == {"ready": True}


def test_heartbeat_updates_last_heartbeat_time():
    stub_app.READY_AT = time.monotonic() - 1
    client = TestClient(stub_app.app)
    before = stub_app.LAST_HEARTBEAT
    client.post("/heartbeat")
    assert stub_app.LAST_HEARTBEAT > before


def test_generate_then_status_then_result_round_trip():
    stub_app.READY_AT = time.monotonic() - 1
    client = TestClient(stub_app.app)
    resp = client.post(
        "/generate", data={"text": "hello"},
        files={"image": ("photo.jpg", b"fake-image-bytes", "image/jpeg")})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    deadline = time.monotonic() + 5
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/status/{job_id}").json()
        if status["status"] != "running":
            break
        time.sleep(0.1)
    assert status["status"] == "done"

    result = client.get(f"/result/{job_id}")
    assert result.status_code == 200
    assert result.headers["content-type"] == "video/mp4"
    assert len(result.content) > 0


def test_status_for_unknown_job_id_is_an_error():
    client = TestClient(stub_app.app)
    resp = client.get("/status/does-not-exist")
    assert resp.status_code == 404


def test_missed_heartbeats_trigger_self_terminate():
    stub_app.READY_AT = time.monotonic() - 1
    stub_app.LAST_HEARTBEAT = time.monotonic() - stub_app.HEARTBEAT_TIMEOUT_S - 1
    with patch.object(stub_app, "self_terminate") as mock_terminate:
        stub_app.check_heartbeat_timeout()
    mock_terminate.assert_called_once()


def test_recent_heartbeat_does_not_trigger_self_terminate():
    stub_app.READY_AT = time.monotonic() - 1
    stub_app.LAST_HEARTBEAT = time.monotonic()
    with patch.object(stub_app, "self_terminate") as mock_terminate:
        stub_app.check_heartbeat_timeout()
    mock_terminate.assert_not_called()
