import sys
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import find_datacenter  # noqa: E402
import pod_up  # noqa: E402


def test_list_volume_capable_datacenters_filters_by_storage_support():
    response = {"data": {"dataCenters": [
        {"id": "EU-RO-1", "storageSupport": True},
        {"id": "EU-SE-1", "storageSupport": False},
        {"id": "US-TX-3", "storageSupport": True},
    ]}}
    with patch.object(pod_up, "gql", return_value=response):
        result = find_datacenter.list_volume_capable_datacenters("fake-key")
    assert result == ["EU-RO-1", "US-TX-3"]


def _gpu_types_response(rows):
    return {"data": {"gpuTypes": rows}}


def test_stock_in_datacenter_excludes_out_of_stock_and_too_small():
    rows = [
        {"id": "NVIDIA A40", "memoryInGb": 48,
         "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 0.49}},
        {"id": "NVIDIA RTX 3090", "memoryInGb": 24,
         "lowestPrice": {"stockStatus": "High", "uninterruptablePrice": 0.20}},
        {"id": "NVIDIA H100 80GB HBM3", "memoryInGb": 80,
         "lowestPrice": {"stockStatus": None, "uninterruptablePrice": None}},
    ]
    with patch.object(pod_up, "gql", return_value=_gpu_types_response(rows)):
        result = find_datacenter.stock_in_datacenter("fake-key", "EU-RO-1", 40.0, 3.00, "")
    assert [r["id"] for r in result] == ["NVIDIA A40"]


def test_stock_in_datacenter_excludes_over_price_ceiling():
    rows = [
        {"id": "NVIDIA A40", "memoryInGb": 48,
         "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 5.00}},
    ]
    with patch.object(pod_up, "gql", return_value=_gpu_types_response(rows)):
        result = find_datacenter.stock_in_datacenter("fake-key", "EU-RO-1", 40.0, 3.00, "")
    assert result == []


def test_stock_in_datacenter_excludes_amd_by_default_gpu_match():
    rows = [
        {"id": "AMD Instinct MI300X OAM", "memoryInGb": 192,
         "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 2.39}},
        {"id": "NVIDIA A100 80GB PCIe", "memoryInGb": 80,
         "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 1.59}},
    ]
    with patch.object(pod_up, "gql", return_value=_gpu_types_response(rows)):
        result = find_datacenter.stock_in_datacenter(
            "fake-key", "EU-RO-1", 40.0, 3.00, pod_up.DEFAULT_GPU_MATCH)
    assert [r["id"] for r in result] == ["NVIDIA A100 80GB PCIe"]


def test_stock_in_datacenter_sorts_cheapest_first():
    rows = [
        {"id": "NVIDIA H100 80GB HBM3", "memoryInGb": 80,
         "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 2.89}},
        {"id": "NVIDIA A40", "memoryInGb": 48,
         "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 0.49}},
    ]
    with patch.object(pod_up, "gql", return_value=_gpu_types_response(rows)):
        result = find_datacenter.stock_in_datacenter("fake-key", "EU-RO-1", 40.0, 3.00, "")
    assert [r["id"] for r in result] == ["NVIDIA A40", "NVIDIA H100 80GB HBM3"]


def test_create_volume_builds_expected_mutation():
    with patch.object(pod_up, "gql", return_value={"data": {"createNetworkVolume": {
            "id": "abc123", "name": "test-vol", "size": 30, "dataCenterId": "EU-SE-1"}}}) as mock_gql:
        result = find_datacenter.create_volume("fake-key", "EU-SE-1", "test-vol", 30)
    assert result["data"]["createNetworkVolume"]["id"] == "abc123"
    mutation = mock_gql.call_args.args[1]
    assert "createNetworkVolume" in mutation
    assert '"EU-SE-1"' in mutation
    assert '"test-vol"' in mutation
    assert "size:30" in mutation


def test_list_volumes_returns_network_volumes():
    response = {"data": {"myself": {"networkVolumes": [
        {"id": "vol1", "name": "n", "size": 30, "dataCenterId": "EU-RO-1"}]}}}
    with patch.object(pod_up, "gql", return_value=response):
        result = find_datacenter.list_volumes("fake-key")
    assert result == [{"id": "vol1", "name": "n", "size": 30, "dataCenterId": "EU-RO-1"}]


def test_delete_volume_sends_expected_mutation():
    with patch.object(pod_up, "gql", return_value={"data": {"deleteNetworkVolume": None}}) as mock_gql:
        find_datacenter.delete_volume("fake-key", "vol1")
    mutation = mock_gql.call_args.args[1]
    assert "deleteNetworkVolume" in mutation
    assert '"vol1"' in mutation
