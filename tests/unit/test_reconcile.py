import re
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from coriolis_operator import main
from coriolis_operator.main import reconcile_appliance
from coriolis_operator.reconcile import (
    build_state_config_map,
    build_status,
    state_config_map_name,
)


def test_state_config_map_name_is_deterministic() -> None:
    assert state_config_map_name("example") == "example-operator-state"


def assert_dns_subdomain(name: str) -> None:
    assert len(name) <= 253
    for label in name.split("."):
        assert len(label) <= 63
        assert re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", label)


def test_state_config_map_name_truncates_long_resource_names_with_suffix() -> None:
    shared_prefix = f"{'a' * 63}.{'b' * 63}.{'c' * 63}.{'d' * 60}"
    first_resource_name = f"{shared_prefix}a"
    second_resource_name = f"{shared_prefix}b"

    first_config_map_name = state_config_map_name(first_resource_name)
    second_config_map_name = state_config_map_name(second_resource_name)

    assert first_config_map_name == state_config_map_name(first_resource_name)
    assert first_config_map_name != second_config_map_name
    assert first_config_map_name.endswith("-operator-state")
    assert_dns_subdomain(first_config_map_name)
    assert_dns_subdomain(second_config_map_name)


def test_state_config_map_name_hashes_when_suffix_would_overflow_final_label() -> None:
    config_map_name = state_config_map_name("a" * 63)

    assert config_map_name.endswith("-operator-state")
    assert config_map_name != f"{'a' * 63}-operator-state"
    assert_dns_subdomain(config_map_name)


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
    timestamp = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)

    assert build_status(7, timestamp=timestamp) == {
        "observedGeneration": 7,
        "conditions": [
            {
                "type": "Accepted",
                "status": "True",
                "reason": "Accepted",
                "message": "The requested appliance configuration is valid.",
                "observedGeneration": 7,
                "lastTransitionTime": "2026-08-20T12:30:00Z",
            },
            {
                "type": "Reconciled",
                "status": "True",
                "reason": "Reconciled",
                "message": "The requested appliance state was applied to Kubernetes.",
                "observedGeneration": 7,
                "lastTransitionTime": "2026-08-20T12:30:00Z",
            },
            {
                "type": "Ready",
                "status": "False",
                "reason": "RuntimeNotImplemented",
                "message": "The appliance runtime is not implemented yet.",
                "observedGeneration": 7,
                "lastTransitionTime": "2026-08-20T12:30:00Z",
            },
        ],
    }


def test_status_preserves_transition_time_when_condition_status_is_unchanged() -> None:
    prior_conditions = [
        {
            "type": "Accepted",
            "status": "True",
            "lastTransitionTime": "2026-08-19T10:00:00Z",
        }
    ]

    status = build_status(
        7,
        prior_conditions=prior_conditions,
        timestamp=datetime(2026, 8, 20, 12, 30, tzinfo=UTC),
    )

    assert status["conditions"][0]["lastTransitionTime"] == "2026-08-19T10:00:00Z"
    assert status["conditions"][1]["lastTransitionTime"] == "2026-08-20T12:30:00Z"


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
    assert status["observedGeneration"] == 7
    assert [condition["status"] for condition in status["conditions"]] == [
        "True",
        "True",
        "False",
    ]


def test_handler_updates_patch_status_and_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconciled_status = {"observedGeneration": 7, "conditions": []}
    reconcile = MagicMock(return_value=reconciled_status)
    monkeypatch.setattr(main, "reconcile_appliance", reconcile)
    patch = MagicMock()

    result = main._handle_reconcile(
        {"version": "2026.8.0"}, {"name": "example"}, patch, {"conditions": []}
    )

    assert result is None
    reconcile.assert_called_once_with(
        spec={"version": "2026.8.0"},
        meta={"name": "example"},
        status={"conditions": []},
    )
    patch.status.update.assert_called_once_with(reconciled_status)


def test_handler_propagates_reconcile_failure_without_patching_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_api = MagicMock()
    core_api.patch_namespaced_config_map.side_effect = RuntimeError("API failed")
    monkeypatch.setattr(main.client, "CoreV1Api", MagicMock(return_value=core_api))
    patch = MagicMock()

    with pytest.raises(RuntimeError, match="API failed"):
        main._handle_reconcile(
            {"version": "2026.8.0"},
            {
                "name": "example",
                "namespace": "operators",
                "generation": 7,
                "uid": "abc-123",
            },
            patch,
        )

    patch.status.update.assert_not_called()
