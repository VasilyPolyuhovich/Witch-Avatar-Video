import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

WEBUI_DIR = Path(__file__).resolve().parent.parent.parent / "webui"
sys.path.insert(0, str(WEBUI_DIR.parent))
SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import pod_up  # noqa: E402
from webui.backend.runpod_session import RunPodSession  # noqa: E402


def test_start_deploys_pod_and_resolves_http_endpoint():
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    with patch.object(pod_up, "rank_gpus", return_value=[{"id": "GPU-1", "vram": 16, "price": 0.2, "stock": "High"}]), \
         patch.object(pod_up, "load_public_key", return_value="ssh-rsa fake"), \
         patch.object(pod_up, "deploy_with_fallback", return_value=("pod-123", "machine-1", "GPU-1", 0.2)), \
         patch.object(pod_up, "get_port_endpoint", return_value=("1.2.3.4", 18000)):
        pod_id = session.start()
    assert pod_id == "pod-123"
    assert session.pod_id == "pod-123"
    assert session.base_url == "https://pod-123-8000.proxy.runpod.net"
    assert session.started_at is not None


def test_start_passes_account_key_to_pod_as_runpod_api_key():
    """The pod's own heartbeat-timeout safety net self-terminates via the
    RunPod API using this env var (see docs/superpowers/specs/2026-08-30-
    webui-design.md's Cost safety section) -- without it, self-terminate
    silently no-ops forever (confirmed missing in a real Task 8 deploy,
    2026-09-06)."""
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    with patch.object(pod_up, "rank_gpus", return_value=[{"id": "GPU-1", "vram": 16, "price": 0.2, "stock": "High"}]), \
         patch.object(pod_up, "load_public_key", return_value="ssh-rsa fake"), \
         patch.object(pod_up, "deploy_with_fallback", return_value=("pod-123", "machine-1", "GPU-1", 0.2)) as mock_deploy, \
         patch.object(pod_up, "get_port_endpoint", return_value=("1.2.3.4", 18000)):
        session.start()
    cfg = mock_deploy.call_args.args[2]
    assert cfg["extra_env"] == {"RUNPOD_API_KEY": "fake-key"}


def test_start_raises_runtime_error_when_no_gpu_available():
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    with patch.object(pod_up, "rank_gpus", return_value=[]):
        try:
            session.start()
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "no_gpu_available" in str(e)


def test_stop_terminates_pod_and_clears_state():
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    session.pod_id = "pod-123"
    session.base_url = "http://1.2.3.4:18000"
    with patch.object(pod_up, "terminate") as mock_terminate:
        session.stop()
    mock_terminate.assert_called_once_with("fake-key", "pod-123")
    assert session.pod_id is None
    assert session.base_url is None
    assert session.started_at is None


def test_stop_is_a_no_op_when_no_pod_is_active():
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    with patch.object(pod_up, "terminate") as mock_terminate:
        session.stop()
    mock_terminate.assert_not_called()


def test_poll_health_returns_false_when_no_pod_active():
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    assert session.poll_health() is False


def test_poll_health_sends_heartbeat_and_returns_ready_flag():
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    session.pod_id = "pod-123"
    session.base_url = "http://1.2.3.4:18000"
    fake_response = MagicMock()
    fake_response.json.return_value = {"ready": True}
    with patch("webui.backend.runpod_session.requests.post") as mock_post, \
         patch("webui.backend.runpod_session.requests.get", return_value=fake_response) as mock_get:
        result = session.poll_health()
    assert result is True
    mock_post.assert_called_once_with("http://1.2.3.4:18000/heartbeat", timeout=5)
    mock_get.assert_called_once_with("http://1.2.3.4:18000/health", timeout=5)


def test_poll_health_returns_false_on_connection_error():
    import requests
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    session.pod_id = "pod-123"
    session.base_url = "http://1.2.3.4:18000"
    with patch("webui.backend.runpod_session.requests.post", side_effect=requests.RequestException("boom")):
        result = session.poll_health()
    assert result is False


def test_elapsed_seconds_is_zero_before_start():
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    assert session.elapsed_seconds() == 0


def test_generate_posts_multipart_and_returns_job_id():
    session = RunPodSession(account_key="fake-key", image_ref="fake/image:latest")
    session.base_url = "http://1.2.3.4:18000"
    fake_response = MagicMock()
    fake_response.json.return_value = {"job_id": "job-1"}
    fake_response.raise_for_status.return_value = None
    with patch("webui.backend.runpod_session.requests.post", return_value=fake_response) as mock_post:
        job_id = session.generate(b"fake-image-bytes", "photo.jpg", "Hello", None, None)
    assert job_id == "job-1"
    called_kwargs = mock_post.call_args.kwargs
    assert called_kwargs["data"] == {"text": "Hello"}
    assert "image" in called_kwargs["files"]
    assert "voice" not in called_kwargs["files"]
