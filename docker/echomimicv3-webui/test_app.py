import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def test_health_reports_not_ready_during_warmup():
    client = TestClient(app_module.app)
    app_module.READY_AT = time.monotonic() + 5
    resp = client.get("/health")
    assert resp.json() == {"ready": False}


def test_run_inference_ignores_path_traversal_in_filenames(tmp_path, monkeypatch):
    """A malicious client-supplied filename like '../../etc/cron.d/x'
    must never be joined into a filesystem path -- found by automated
    commit review, 2026-09-10."""
    import sys
    import types

    fake_librosa = types.ModuleType("librosa")
    fake_librosa.get_duration = lambda path: 1.0
    monkeypatch.setitem(sys.modules, "librosa", fake_librosa)

    captured = {}

    class FakeCompletedProcess:
        returncode = 0
        stdout = f"Saved output to: {tmp_path}/out.mp4"
        stderr = ""

    def fake_run(cmd, cwd, capture_output, text):
        captured["image_path"] = cmd[cmd.index("--image_path") + 1]
        captured["audio_path"] = cmd[cmd.index("--audio_path") + 1]
        (tmp_path / "out.mp4").write_bytes(b"fake-mp4")
        return FakeCompletedProcess()

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    app_module.run_inference(
        b"fake-jpg-bytes", "../../etc/cron.d/evil.jpg",
        b"fake-wav-bytes", "../../../tmp/evil.wav",
    )
    assert ".." not in captured["image_path"]
    assert ".." not in captured["audio_path"]
    assert captured["image_path"].endswith("image.jpg")
    assert captured["audio_path"].endswith("audio.wav")


def test_generate_runs_inference_and_reports_done():
    client = TestClient(app_module.app)
    app_module.READY_AT = time.monotonic()  # ready now
    with patch.object(app_module, "run_inference", return_value=b"fake-mp4-bytes") as mock_run:
        resp = client.post(
            "/generate",
            data={"text": "unused-by-this-endpoint"},
            files={"image": ("photo.jpg", b"fake-jpg-bytes"), "voice": ("audio.wav", b"fake-wav-bytes")},
        )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    status = None
    for _ in range(50):
        status = client.get(f"/status/{job_id}").json()
        if status["status"] != "running":
            break
        time.sleep(0.05)
    assert status["status"] == "done"
    mock_run.assert_called_once()
    result = client.get(f"/result/{job_id}")
    assert result.status_code == 200
    assert result.content == b"fake-mp4-bytes"
