import sys
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import pod_up  # noqa: E402


def _fake_gql_response(ports):
    return {"data": {"pod": {"runtime": {"ports": ports}}}}


def test_get_port_endpoint_finds_matching_private_port():
    ports = [
        {"ip": "1.2.3.4", "isIpPublic": True, "publicPort": 10022, "privatePort": 22, "type": "tcp"},
        {"ip": "1.2.3.4", "isIpPublic": True, "publicPort": 18000, "privatePort": 8000, "type": "http"},
    ]
    with patch.object(pod_up, "gql", return_value=_fake_gql_response(ports)):
        result = pod_up.get_port_endpoint("fake-key", "fake-pod-id", 8000)
    assert result == ("1.2.3.4", 18000)


def test_get_port_endpoint_returns_none_when_not_published():
    with patch.object(pod_up, "gql", return_value=_fake_gql_response([])):
        result = pod_up.get_port_endpoint("fake-key", "fake-pod-id", 8000)
    assert result is None


def test_get_ssh_endpoint_still_works_via_get_port_endpoint():
    ports = [{"ip": "1.2.3.4", "isIpPublic": True, "publicPort": 10022, "privatePort": 22, "type": "tcp"}]
    with patch.object(pod_up, "gql", return_value=_fake_gql_response(ports)):
        result = pod_up.get_ssh_endpoint("fake-key", "fake-pod-id")
    assert result == ("1.2.3.4", 10022)
