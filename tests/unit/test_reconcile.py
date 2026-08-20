from unittest.mock import MagicMock

from coriolis_operator.main import reconcile_appliance
from coriolis_operator.reconcile import (
    build_state_config_map,
    build_status,
    state_config_map_name,
)


def test_state_config_map_name_is_deterministic() -> None:
    assert state_config_map_name("example") == "example-operator-state"


def test_state_config_map_name_truncates_long_resource_names_with_suffix() -> None:
    resource_name = "a" * 253

    config_map_name = state_config_map_name(resource_name)

    assert config_map_name == "a" * 238 + "-operator-state"
    assert len(config_map_name) == 253


def test_state_config_map_records_requested_version_generation_and_owner() -> None:
    config_map = build_state_config_map(
        name="example",
        namespace="operators",
        version="2026.8.0",
        generation=7,
        owner={
            "apiVersion": "coriolis.cloudbase.it/v1alpha1",
            "kind": "CoriolisAppliance",
            "name": "example",
            "uid": "abc-123",
        },
    )

    assert config_map["metadata"]["name"] == "example-operator-state"
    assert config_map["metadata"]["namespace"] == "operators"
    assert config_map["data"] == {"requestedVersion": "2026.8.0", "generation": "7"}
    assert config_map["metadata"]["ownerReferences"] == [
        {
            "apiVersion": "coriolis.cloudbase.it/v1alpha1",
            "kind": "CoriolisAppliance",
            "name": "example",
            "uid": "abc-123",
            "controller": True,
        }
    ]


def test_status_reports_reconciled_but_runtime_not_ready() -> None:
    assert build_status(7) == {
        "observedGeneration": 7,
        "conditions": [
            {
                "type": "Accepted",
                "status": "True",
                "reason": "Accepted",
                "observedGeneration": 7,
            },
            {
                "type": "Reconciled",
                "status": "True",
                "reason": "Reconciled",
                "observedGeneration": 7,
            },
            {
                "type": "Ready",
                "status": "False",
                "reason": "RuntimeNotImplemented",
                "observedGeneration": 7,
            },
        ],
    }


def test_reconcile_appliance_server_side_applies_state_and_returns_status() -> None:
    core_api = MagicMock()

    status = reconcile_appliance(
        spec={"version": "2026.8.0"},
        meta={
            "name": "example",
            "namespace": "operators",
            "generation": 7,
            "uid": "abc-123",
        },
        core_api=core_api,
    )

    expected_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "example-operator-state",
            "namespace": "operators",
            "ownerReferences": [
                {
                    "apiVersion": "coriolis.cloudbase.it/v1alpha1",
                    "kind": "CoriolisAppliance",
                    "name": "example",
                    "uid": "abc-123",
                    "controller": True,
                }
            ],
        },
        "data": {"requestedVersion": "2026.8.0", "generation": "7"},
    }
    core_api.patch_namespaced_config_map.assert_called_once_with(
        name="example-operator-state",
        namespace="operators",
        body=expected_body,
        field_manager="coriolis-operator",
        force=True,
        _content_type="application/apply-patch+yaml",
    )
    assert status == build_status(7)
