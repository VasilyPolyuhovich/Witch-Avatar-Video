import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import generate_witch_video as gwv  # noqa: E402
import pod_up  # noqa: E402


def test_default_image_points_at_echomimicv3():
    assert "echomimicv3" in pod_up.DEFAULT_IMAGE_REF


def test_default_min_vram_matches_echomimicv3_floor():
    assert pod_up.DEFAULT_MIN_VRAM >= 40.0


def test_compute_remote_paths_uses_run_echomimicv3():
    paths = gwv.compute_remote_paths("job-1", Path("photo.jpg"), Path("audio.wav"))
    assert paths["run_script"] == "/root/jobs/job-1/run_echomimicv3.sh"


def test_build_remote_cmd_invokes_run_echomimicv3():
    paths = gwv.compute_remote_paths("job-1", Path("photo.jpg"), Path("audio.wav"))
    cmd = gwv.build_remote_cmd(paths)
    assert cmd.startswith("/root/jobs/job-1/run_echomimicv3.sh")
    assert "--image" in cmd and "--audio" in cmd and "--output-dir" in cmd


def test_default_run_timeout_raised_for_slower_model():
    assert gwv.DEFAULT_RUN_TIMEOUT_S >= 900
