import hashlib
import re
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from coriolis_operator import main
from coriolis_operator.main import reconcile_appliance
from coriolis_operator.reconcile import (
    SUPPORTED_INITIAL_VERSION,
    SUPPORTED_PROFILE,
    accepted_conditions,
    appliance_identity,
    appliance_resource_name,
    blocked_conditions,
    build_resource_metadata,
    build_state_config_map,
    build_status,
    rejected_conditions,
    state_config_map_name,
)

CONDITION_TYPES = [
    "Accepted",
    "Progressing",
    "Reconciled",
    "Ready",
    "Degraded",
    "Upgradeable",
]

OWNER = {
    "apiVersion": "coriolis.cloudbase.it/v1alpha1",
    "kind": "CoriolisAppliance",
    "name": "example",
    "uid": "abc-123",
}


def sample_meta(generation: int = 7) -> dict:
    return {
        "name": "example",
        "namespace": "operators",
        "generation": generation,
        "uid": "abc-123",
    }


def condition(
    condition_type: str,
    condition_status: str,
    reason: str,
    message: str,
    generation: int = 7,
    transition: str = "2026-08-20T12:30:00Z",
) -> dict:
    return {
        "type": condition_type,
        "status": condition_status,
        "reason": reason,
        "message": message,
        "observedGeneration": generation,
        "lastTransitionTime": transition,
    }


def assert_no_api_instantiation(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("CoreV1Api must not be instantiated")

    monkeypatch.setattr(main.client, "CoreV1Api", fail)


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


def test_appliance_resource_name_short_is_unchanged_and_deterministic() -> None:
    assert appliance_resource_name("appliance", "core") == "appliance-core"
    assert appliance_resource_name("appliance", "core") == appliance_resource_name(
        "appliance", "core"
    )


def test_appliance_resource_name_dotted_input_is_hashed_and_label_safe() -> None:
    name = appliance_resource_name("my.appliance.example.com", "core")

    assert "." not in name
    assert len(name) <= 63
    assert re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", name)
    desired = "my.appliance.example.com-core"
    expected_hash = hashlib.sha256(desired.encode()).hexdigest()[:12]
    assert f"-{expected_hash}-core" in name


def test_appliance_resource_name_long_overflow_is_hashed_and_label_safe() -> None:
    long_name = f"{'a' * 63}.{'b' * 63}"
    name = appliance_resource_name(long_name, "core")

    assert "." not in name
    assert len(name) <= 63
    assert re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", name)
    assert name.endswith("-core")
    desired = f"{long_name}-core"
    expected_hash = hashlib.sha256(desired.encode()).hexdigest()[:12]
    assert f"-{expected_hash}-core" in name


def test_appliance_resource_name_shared_visible_prefix_differs_by_hash() -> None:
    shared = f"{'a' * 40}.{'b' * 40}.{'c' * 40}."
    first = appliance_resource_name(shared + "d" * 40, "core")
    second = appliance_resource_name(shared + "e" * 40, "core")

    assert first != second
    assert first[:45] == second[:45]


def test_appliance_resource_name_rejects_invalid_appliance_names() -> None:
    for invalid in ["", "UPPER", "a_b", "-start", "end-", "a..b", "A.b", "a" * 254]:
        with pytest.raises(ValueError):
            appliance_resource_name(invalid, "core")


def test_appliance_resource_name_rejects_invalid_components() -> None:
    for invalid in ["", "UPPER", "bad_component", "-bad", "bad-", "c" * 49]:
        with pytest.raises(ValueError):
            appliance_resource_name("appliance", invalid)


def test_appliance_resource_name_accepts_max_length_component() -> None:
    assert appliance_resource_name("appliance", "c" * 48).endswith("c" * 48)


def test_appliance_identity_short_is_unchanged() -> None:
    assert appliance_identity("appliance") == "appliance"


def test_appliance_identity_dotted_is_hashed_and_label_safe() -> None:
    identity = appliance_identity("my.appliance.example.com")

    assert "." not in identity
    assert len(identity) <= 63
    assert re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", identity)
    expected_hash = hashlib.sha256(b"my.appliance.example.com").hexdigest()[:12]
    assert identity.endswith(f"-{expected_hash}")


def test_appliance_identity_long_overflow_is_label_safe() -> None:
    long_name = f"{'a' * 63}.{'b' * 63}"
    identity = appliance_identity(long_name)

    assert "." not in identity
    assert len(identity) <= 63
    assert re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", identity)
    expected_hash = hashlib.sha256(long_name.encode()).hexdigest()[:12]
    assert identity.endswith(f"-{expected_hash}")


def test_appliance_identity_is_deterministic_and_collision_resistant() -> None:
    assert appliance_identity("a.b.c.d") == appliance_identity("a.b.c.d")
    assert appliance_identity("a.b.c.d") != appliance_identity("a-b-c-d")
    assert appliance_identity("x.y") != appliance_identity("x.z")


def test_appliance_identity_rejects_invalid_appliance_names() -> None:
    for invalid in ["", "UPPER", "a_b", "-start", "end-", "a..b", "A.b", "a" * 254]:
        with pytest.raises(ValueError):
            appliance_identity(invalid)


def test_build_resource_metadata_owner_mode_labels_and_annotation() -> None:
    metadata = build_resource_metadata(
        resource_name="example-operator-state",
        namespace="operators",
        appliance_name="example",
        component="operator-state",
        accepted_version="2603.4",
        owner=OWNER,
    )

    assert metadata["name"] == "example-operator-state"
    assert metadata["namespace"] == "operators"
    assert metadata["labels"] == {
        "app.kubernetes.io/name": "coriolis",
        "app.kubernetes.io/instance": "example",
        "app.kubernetes.io/version": "2603.4",
        "app.kubernetes.io/component": "operator-state",
        "app.kubernetes.io/part-of": "coriolis-appliance",
        "app.kubernetes.io/managed-by": "coriolis-operator",
        "coriolis.cloudbase.it/appliance": "example",
        "coriolis.cloudbase.it/component": "operator-state",
    }
    assert metadata["annotations"] == {
        "coriolis.cloudbase.it/appliance-name": "example"
    }
    assert metadata["ownerReferences"] == [
        {
            "apiVersion": "coriolis.cloudbase.it/v1alpha1",
            "kind": "CoriolisAppliance",
            "name": "example",
            "uid": "abc-123",
            "controller": True,
        }
    ]


def test_build_resource_metadata_retention_mode() -> None:
    metadata = build_resource_metadata(
        resource_name="example-backup",
        namespace="operators",
        appliance_name="example",
        component="backup",
        accepted_version="2603.4",
        retention="daily",
    )

    assert "ownerReferences" not in metadata
    assert metadata["annotations"] == {
        "coriolis.cloudbase.it/appliance-name": "example",
        "coriolis.cloudbase.it/retention": "daily",
    }
    assert metadata["labels"]["coriolis.cloudbase.it/component"] == "backup"
    assert metadata["labels"]["app.kubernetes.io/component"] == "backup"


def test_build_resource_metadata_rejects_owner_and_retention() -> None:
    with pytest.raises(ValueError):
        build_resource_metadata(
            resource_name="x",
            namespace="n",
            appliance_name="a",
            component="c",
            accepted_version="v",
            owner=OWNER,
            retention="daily",
        )


def test_build_resource_metadata_rejects_neither_owner_nor_retention() -> None:
    with pytest.raises(ValueError):
        build_resource_metadata(
            resource_name="x",
            namespace="n",
            appliance_name="a",
            component="c",
            accepted_version="v",
        )


def test_build_resource_metadata_rejects_invalid_retention() -> None:
    for invalid in ["", "UPPER", "bad_class"]:
        with pytest.raises(ValueError):
            build_resource_metadata(
                resource_name="x",
                namespace="n",
                appliance_name="a",
                component="c",
                accepted_version="v",
                retention=invalid,
            )


def test_build_resource_metadata_rejects_invalid_component() -> None:
    with pytest.raises(ValueError):
        build_resource_metadata(
            resource_name="x",
            namespace="n",
            appliance_name="a",
            component="UPPER",
            accepted_version="v",
            owner=OWNER,
        )


def test_state_config_map_records_accepted_version_profile_and_generation() -> None:
    config_map = build_state_config_map(
        name="example",
        namespace="operators",
        profile="core",
        accepted_version="2603.4",
        generation=7,
        owner=OWNER,
    )

    assert config_map["metadata"]["name"] == "example-operator-state"
    assert config_map["metadata"]["namespace"] == "operators"
    assert config_map["metadata"]["labels"] == {
        "app.kubernetes.io/name": "coriolis",
        "app.kubernetes.io/instance": "example",
        "app.kubernetes.io/version": "2603.4",
        "app.kubernetes.io/component": "operator-state",
        "app.kubernetes.io/part-of": "coriolis-appliance",
        "app.kubernetes.io/managed-by": "coriolis-operator",
        "coriolis.cloudbase.it/appliance": "example",
        "coriolis.cloudbase.it/component": "operator-state",
    }
    assert config_map["metadata"]["annotations"] == {
        "coriolis.cloudbase.it/appliance-name": "example"
    }
    assert config_map["data"] == {
        "acceptedVersion": "2603.4",
        "profile": "core",
        "generation": "7",
    }
    assert config_map["metadata"]["ownerReferences"] == [
        {
            "apiVersion": "coriolis.cloudbase.it/v1alpha1",
            "kind": "CoriolisAppliance",
            "name": "example",
            "uid": "abc-123",
            "controller": True,
        }
    ]


def test_state_config_map_preserves_legacy_dotted_name() -> None:
    appliance_name = "example.domain"
    config_map = build_state_config_map(
        name=appliance_name,
        namespace="operators",
        profile="core",
        accepted_version="2603.4",
        generation=7,
        owner=OWNER,
    )

    assert config_map["metadata"]["name"] == "example.domain-operator-state"
    assert config_map["metadata"]["labels"][
        "coriolis.cloudbase.it/appliance"
    ] == appliance_identity(appliance_name)
    assert config_map["metadata"]["annotations"] == {
        "coriolis.cloudbase.it/appliance-name": appliance_name
    }


def test_build_status_reports_accepted_api_only_slice() -> None:
    timestamp = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)

    assert build_status(
        7,
        accepted_version="2603.4",
        conditions=accepted_conditions(),
        timestamp=timestamp,
    ) == {
        "observedGeneration": 7,
        "acceptedVersion": "2603.4",
        "conditions": [
            condition(
                "Accepted",
                "True",
                "Accepted",
                "The requested profile and version are supported.",
            ),
            condition(
                "Progressing",
                "False",
                "RuntimeNotImplemented",
                "The appliance runtime is not implemented yet.",
            ),
            condition(
                "Reconciled",
                "True",
                "Reconciled",
                "The accepted profile/version controller state marker was recorded "
                "in Kubernetes.",
            ),
            condition(
                "Ready",
                "False",
                "RuntimeNotImplemented",
                "The appliance runtime is not implemented yet.",
            ),
            condition(
                "Degraded",
                "False",
                "NotDegraded",
                "The appliance is not degraded.",
            ),
            condition(
                "Upgradeable",
                "False",
                "UpgradeNotSupported",
                "The core profile has no supported upgrade path.",
            ),
        ],
    }


def test_build_status_omits_accepted_version_when_none() -> None:
    status = build_status(
        7,
        accepted_version=None,
        conditions=rejected_conditions("UnsupportedProfile", "rejected"),
        timestamp=datetime(2026, 8, 20, 12, 30, tzinfo=UTC),
    )

    assert "acceptedVersion" not in status
    assert status["observedGeneration"] == 7


def test_status_preserves_transition_time_when_condition_status_is_unchanged() -> None:
    prior_conditions = [
        {
            "type": condition_type,
            "status": status_value,
            "lastTransitionTime": "2026-08-19T10:00:00Z",
        }
        for condition_type, status_value in [
            ("Accepted", "True"),
            ("Progressing", "False"),
            ("Reconciled", "True"),
            ("Ready", "False"),
            ("Degraded", "False"),
            ("Upgradeable", "False"),
        ]
    ]

    status = build_status(
        7,
        accepted_version="2603.4",
        conditions=accepted_conditions(),
        prior_conditions=prior_conditions,
        timestamp=datetime(2026, 8, 20, 12, 30, tzinfo=UTC),
    )

    for index in range(len(CONDITION_TYPES)):
        condition_status = status["conditions"][index]
        assert condition_status["lastTransitionTime"] == "2026-08-19T10:00:00Z"


def test_status_preserves_transition_time_across_reason_change() -> None:
    prior_conditions = [
        {
            "type": "Accepted",
            "status": "True",
            "lastTransitionTime": "2026-08-18T09:00:00Z",
        },
        {
            "type": "Upgradeable",
            "status": "False",
            "reason": "UpgradeNotSupported",
            "lastTransitionTime": "2026-08-19T10:00:00Z",
        },
    ]

    status = build_status(
        8,
        accepted_version="2603.4",
        conditions=blocked_conditions("2603.4", "2603.5"),
        prior_conditions=prior_conditions,
        timestamp=datetime(2026, 8, 20, 12, 30, tzinfo=UTC),
    )

    upgradeable = status["conditions"][5]
    assert upgradeable["status"] == "False"
    assert upgradeable["reason"] == "UpgradeBlocked"
    assert upgradeable["lastTransitionTime"] == "2026-08-19T10:00:00Z"
    accepted = status["conditions"][0]
    assert accepted["status"] == "False"
    assert accepted["lastTransitionTime"] == "2026-08-20T12:30:00Z"


def test_reconcile_appliance_server_side_applies_state_and_returns_status() -> None:
    core_api = MagicMock()
    core_api.api_client.default_headers = {}

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(),
        core_api=core_api,
    )

    expected_body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "example-operator-state",
            "namespace": "operators",
            "labels": {
                "app.kubernetes.io/name": "coriolis",
                "app.kubernetes.io/instance": "example",
                "app.kubernetes.io/version": "2603.4",
                "app.kubernetes.io/component": "operator-state",
                "app.kubernetes.io/part-of": "coriolis-appliance",
                "app.kubernetes.io/managed-by": "coriolis-operator",
                "coriolis.cloudbase.it/appliance": "example",
                "coriolis.cloudbase.it/component": "operator-state",
            },
            "annotations": {
                "coriolis.cloudbase.it/appliance-name": "example",
            },
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
        "data": {
            "acceptedVersion": "2603.4",
            "profile": "core",
            "generation": "7",
        },
    }
    core_api.patch_namespaced_config_map.assert_called_once_with(
        name="example-operator-state",
        namespace="operators",
        body=expected_body,
        field_manager="coriolis-operator",
        force=True,
    )
    assert core_api.api_client.default_headers == {
        "Content-Type": "application/apply-patch+yaml"
    }
    assert status["observedGeneration"] == 7
    assert status["acceptedVersion"] == "2603.4"
    condition_statuses = [
        condition_status["status"] for condition_status in status["conditions"]
    ]
    assert condition_statuses == [
        "True",
        "False",
        "True",
        "False",
        "False",
        "False",
    ]


def test_reconcile_omitted_profile_defaults_to_core() -> None:
    core_api = MagicMock()
    core_api.api_client.default_headers = {}

    status = reconcile_appliance(
        spec={"version": "2603.4"},
        meta=sample_meta(),
        core_api=core_api,
    )

    core_api.patch_namespaced_config_map.assert_called_once()
    body = core_api.patch_namespaced_config_map.call_args.kwargs["body"]
    assert body["data"]["profile"] == SUPPORTED_PROFILE
    assert status["acceptedVersion"] == "2603.4"


def test_reconcile_treats_empty_accepted_version_as_absent() -> None:
    core_api = MagicMock()
    core_api.api_client.default_headers = {}

    status = reconcile_appliance(
        spec={"version": "2603.4"},
        meta=sample_meta(),
        status={"acceptedVersion": "", "conditions": []},
        core_api=core_api,
    )

    core_api.patch_namespaced_config_map.assert_called_once()
    assert status["acceptedVersion"] == "2603.4"


def test_reconcile_rejects_unsupported_profile_without_instantiation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_no_api_instantiation(monkeypatch)

    status = reconcile_appliance(
        spec={"profile": "gpu", "version": "2603.4"},
        meta=sample_meta(),
    )

    assert "acceptedVersion" not in status
    assert status["observedGeneration"] == 7
    conditions = {item["type"]: item for item in status["conditions"]}
    assert list(conditions) == CONDITION_TYPES
    assert conditions["Accepted"]["status"] == "False"
    assert conditions["Accepted"]["reason"] == "UnsupportedProfile"
    assert conditions["Reconciled"]["status"] == "False"
    assert conditions["Reconciled"]["reason"] == "NotReconciled"
    assert conditions["Ready"]["status"] == "False"
    assert conditions["Ready"]["reason"] == "RuntimeNotImplemented"
    assert conditions["Progressing"]["status"] == "False"
    assert conditions["Progressing"]["reason"] == "RuntimeNotImplemented"
    assert conditions["Degraded"]["status"] == "False"
    assert conditions["Upgradeable"]["status"] == "False"
    assert conditions["Upgradeable"]["reason"] == "UpgradeNotSupported"


def test_reconcile_rejects_explicit_empty_profile_without_instantiation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_no_api_instantiation(monkeypatch)

    status = reconcile_appliance(
        spec={"profile": "", "version": "2603.4"},
        meta=sample_meta(),
    )

    assert "acceptedVersion" not in status
    conditions = {item["type"]: item for item in status["conditions"]}
    assert conditions["Accepted"]["status"] == "False"
    assert conditions["Accepted"]["reason"] == "UnsupportedProfile"
    assert conditions["Accepted"]["message"] == (
        "Profile '' is not supported; supported profile: core."
    )
    assert conditions["Reconciled"]["status"] == "False"


def test_reconcile_preserves_accepted_version_on_unsupported_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_no_api_instantiation(monkeypatch)

    status = reconcile_appliance(
        spec={"profile": "gpu", "version": "2603.4"},
        meta=sample_meta(generation=8),
        status={
            "acceptedVersion": "2603.4",
            "observedGeneration": 7,
            "conditions": [],
        },
    )

    assert status["acceptedVersion"] == "2603.4"
    assert status["observedGeneration"] == 8
    conditions = {item["type"]: item for item in status["conditions"]}
    assert conditions["Accepted"]["status"] == "False"
    assert conditions["Accepted"]["reason"] == "UnsupportedProfile"
    assert conditions["Reconciled"]["status"] == "False"
    assert conditions["Upgradeable"]["status"] == "False"
    assert conditions["Upgradeable"]["reason"] == "UpgradeNotSupported"


def test_reconcile_rejects_unsupported_initial_version_without_instantiation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_no_api_instantiation(monkeypatch)

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2026.8.0"},
        meta=sample_meta(),
    )

    assert "acceptedVersion" not in status
    conditions = {item["type"]: item for item in status["conditions"]}
    assert conditions["Accepted"]["status"] == "False"
    assert conditions["Accepted"]["reason"] == "UnsupportedVersion"
    assert conditions["Reconciled"]["status"] == "False"
    assert conditions["Upgradeable"]["status"] == "False"
    assert conditions["Upgradeable"]["reason"] == "UpgradeNotSupported"


def test_reconcile_blocks_version_change_without_instantiation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_no_api_instantiation(monkeypatch)

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.5"},
        meta=sample_meta(generation=8),
        status={
            "acceptedVersion": "2603.4",
            "observedGeneration": 7,
            "conditions": [],
        },
    )

    assert status["acceptedVersion"] == "2603.4"
    assert status["observedGeneration"] == 8
    conditions = {item["type"]: item for item in status["conditions"]}
    assert conditions["Accepted"]["status"] == "False"
    assert conditions["Accepted"]["reason"] == "VersionChangeRejected"
    assert conditions["Upgradeable"]["status"] == "False"
    assert conditions["Upgradeable"]["reason"] == "UpgradeBlocked"
    assert conditions["Reconciled"]["status"] == "False"
    assert conditions["Reconciled"]["reason"] == "NotReconciled"
    assert conditions["Ready"]["status"] == "False"
    assert conditions["Ready"]["reason"] == "RuntimeNotImplemented"
    assert conditions["Progressing"]["status"] == "False"
    assert conditions["Degraded"]["status"] == "False"


def test_reconcile_with_accepted_version_matching_requested_applies_normally() -> None:
    core_api = MagicMock()
    core_api.api_client.default_headers = {}

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(generation=8),
        status={
            "acceptedVersion": "2603.4",
            "observedGeneration": 7,
            "conditions": [],
        },
        core_api=core_api,
    )

    core_api.patch_namespaced_config_map.assert_called_once()
    assert status["acceptedVersion"] == SUPPORTED_INITIAL_VERSION
    assert status["observedGeneration"] == 8
    conditions = {item["type"]: item["status"] for item in status["conditions"]}
    assert conditions == {
        "Accepted": "True",
        "Progressing": "False",
        "Reconciled": "True",
        "Ready": "False",
        "Degraded": "False",
        "Upgradeable": "False",
    }


def test_handler_updates_patch_status_and_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconciled_status = {"observedGeneration": 7, "conditions": []}
    reconcile = MagicMock(return_value=reconciled_status)
    monkeypatch.setattr(main, "reconcile_appliance", reconcile)
    patch = MagicMock()

    result = main._handle_reconcile(
        {"profile": "core", "version": "2603.4"},
        {"name": "example"},
        patch,
        {"conditions": []},
    )

    assert result is None
    reconcile.assert_called_once_with(
        spec={"profile": "core", "version": "2603.4"},
        meta={"name": "example"},
        status={"conditions": []},
    )
    patch.status.update.assert_called_once_with(reconciled_status)


def test_profile_field_handler_routes_through_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled = MagicMock()
    monkeypatch.setattr(main, "_handle_reconcile", handled)
    patch = MagicMock()

    result = main.update_appliance_profile(
        spec={"profile": "core", "version": "2603.4"},
        meta={"name": "example"},
        patch=patch,
        status={"conditions": []},
    )

    assert result is None
    handled.assert_called_once_with(
        {"profile": "core", "version": "2603.4"},
        {"name": "example"},
        patch,
        {"conditions": []},
    )


def test_handler_propagates_reconcile_failure_without_patching_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_api = MagicMock()
    core_api.patch_namespaced_config_map.side_effect = RuntimeError("API failed")
    monkeypatch.setattr(main.client, "CoreV1Api", MagicMock(return_value=core_api))
    patch = MagicMock()

    with pytest.raises(RuntimeError, match="API failed"):
        main._handle_reconcile(
            {"profile": "core", "version": "2603.4"},
            {
                "name": "example",
                "namespace": "operators",
                "generation": 7,
                "uid": "abc-123",
            },
            patch,
        )

    patch.status.update.assert_not_called()
