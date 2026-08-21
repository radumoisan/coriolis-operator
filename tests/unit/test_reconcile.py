import base64
import copy
import hashlib
import re
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from kubernetes import client

from coriolis_operator import main
from coriolis_operator.main import reconcile_appliance
from coriolis_operator.reconcile import (
    APPLIANCE_NAME_ANNOTATION,
    CORIOLIS_CREDENTIALS_KEYS,
    EXTERNAL_READ_ONLY_RESOURCES,
    INFRASTRUCTURE_CREDENTIALS_KEYS,
    MARKER_COLLISION,
    MARKER_LEGACY,
    MARKER_MANAGED,
    RETENTION_ANNOTATION,
    STEP_CA_CREDENTIALS_KEYS,
    SUPPORTED_INITIAL_VERSION,
    SUPPORTED_PROFILE,
    RetainedClassification,
    accepted_conditions,
    appliance_identity,
    appliance_resource_name,
    blocked_conditions,
    build_coriolis_config_map,
    build_coriolis_config_secret,
    build_coriolis_credentials_secret,
    build_infrastructure_credentials_secret,
    build_resource_metadata,
    build_state_config_map,
    build_status,
    build_step_ca_credentials_secret,
    classify_existing_marker,
    classify_retained_resource,
    collision_conditions,
    generate_coriolis_credentials,
    generate_infrastructure_credentials,
    generate_step_ca_credentials,
    rejected_conditions,
    state_config_map_name,
    validated_retained_secret_values,
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

CORIOLIS_CREDENTIALS = {
    "coriolis_database_password": "db synthetic",
    "coriolis_keystone_password": "keystone synthetic",
    "temp_keypair_password": "keypair synthetic",
}
INFRASTRUCTURE_CREDENTIALS = {
    "database_password": "database synthetic",
    "rabbitmq_password": "rabbitmq synthetic",
    "keystone_admin_password": "admin synthetic",
}
STEP_CA_CREDENTIALS = {"init_password": "step-ca synthetic"}
CORIOLIS_CONFIG = {
    "coriolis-api.wsgi": "wsgi application",
    "wsgi-coriolis.conf": "wsgi config",
    "vixdisklib.conf": "disk config",
    "api-paste.ini": "paste config",
    "policy.yml": "policy config",
    "coriolis.release": "2603.4",
}
CORIOLIS_CONFIG_SECRET = {"coriolis.conf": "secret config"}


@pytest.mark.parametrize(
    ("generator", "expected_keys"),
    [
        (
            generate_coriolis_credentials,
            {
                "coriolis_database_password",
                "coriolis_keystone_password",
                "temp_keypair_password",
            },
        ),
        (
            generate_infrastructure_credentials,
            {
                "database_password",
                "rabbitmq_password",
                "keystone_admin_password",
            },
        ),
        (generate_step_ca_credentials, {"init_password"}),
    ],
)
def test_credential_generators_use_independent_32_byte_tokens(
    generator, expected_keys: set[str]
) -> None:
    calls: list[int] = []

    def token_factory(bytes_count: int) -> str:
        calls.append(bytes_count)
        return f"synthetic-{len(calls)}"

    values = generator(token_factory)

    assert set(values) == expected_keys
    assert calls == [32] * len(expected_keys)
    assert values == {
        key: f"synthetic-{index}"
        for index, key in enumerate(sorted(expected_keys), start=1)
    }


def test_credential_generators_make_seven_32_byte_factory_calls() -> None:
    calls: list[int] = []

    def token_factory(bytes_count: int) -> str:
        calls.append(bytes_count)
        return "synthetic"

    generate_coriolis_credentials(token_factory)
    generate_infrastructure_credentials(token_factory)
    generate_step_ca_credentials(token_factory)

    assert calls == [32] * 7


@pytest.mark.parametrize(
    ("generator", "builder"),
    [
        (generate_coriolis_credentials, build_coriolis_credentials_secret),
        (generate_infrastructure_credentials, build_infrastructure_credentials_secret),
        (generate_step_ca_credentials, build_step_ca_credentials_secret),
    ],
)
def test_generated_credentials_compose_with_secret_builders(generator, builder) -> None:
    values = generator(lambda _: "synthetic-token")

    body = builder(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        retention="retain",
        values=values,
    )

    assert {
        key: base64.b64decode(value).decode("utf-8")
        for key, value in body["data"].items()
    } == values


@pytest.mark.parametrize(
    "generator",
    [
        generate_coriolis_credentials,
        generate_infrastructure_credentials,
        generate_step_ca_credentials,
    ],
)
def test_credential_generators_return_url_safe_non_empty_production_tokens(
    generator,
) -> None:
    values = generator()

    assert values
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in values.values())


@pytest.mark.parametrize("invalid_value", ["", 1])
@pytest.mark.parametrize(
    "generator",
    [
        generate_coriolis_credentials,
        generate_infrastructure_credentials,
        generate_step_ca_credentials,
    ],
)
def test_credential_generators_reject_invalid_factory_output_without_leakage(
    generator, invalid_value: object
) -> None:
    def token_factory(_: int) -> object:
        return invalid_value

    with pytest.raises(ValueError) as excinfo:
        generator(token_factory)

    assert (
        str(excinfo.value) == "credential token factory must return a non-empty string"
    )


def sample_meta(generation: int = 7) -> dict:
    return {
        "name": "example",
        "namespace": "operators",
        "generation": generation,
        "uid": "abc-123",
    }


def _api_exception(status: int) -> Exception:
    return client.ApiException(status=status)


def make_core_api(existing=None) -> MagicMock:
    api = MagicMock()
    api.api_client.default_headers = {}
    if existing is None:
        api.read_namespaced_config_map.side_effect = _api_exception(404)
    else:
        api.read_namespaced_config_map.return_value = existing
    return api


def desired_body(meta=None) -> dict:
    meta = meta or sample_meta()
    owner = {
        "apiVersion": OWNER["apiVersion"],
        "kind": OWNER["kind"],
        "name": meta["name"],
        "uid": meta["uid"],
    }
    return build_state_config_map(
        name=meta["name"],
        namespace=meta["namespace"],
        profile=SUPPORTED_PROFILE,
        accepted_version=SUPPORTED_INITIAL_VERSION,
        generation=meta["generation"],
        owner=owner,
    )


def legacy_marker(meta=None, *, generation: str = "1") -> dict:
    meta = meta or sample_meta()
    owner = {
        "apiVersion": OWNER["apiVersion"],
        "kind": OWNER["kind"],
        "name": meta["name"],
        "uid": meta["uid"],
    }
    return {
        "metadata": {
            "name": state_config_map_name(meta["name"]),
            "namespace": meta["namespace"],
            "ownerReferences": [dict(owner, controller=True)],
        },
        "data": {
            "acceptedVersion": SUPPORTED_INITIAL_VERSION,
            "profile": SUPPORTED_PROFILE,
            "generation": generation,
        },
    }


def to_v1_config_map(body: dict) -> client.V1ConfigMap:
    meta = body["metadata"]
    owner_refs = [
        client.V1OwnerReference(
            api_version=ref["apiVersion"],
            kind=ref["kind"],
            name=ref["name"],
            uid=ref["uid"],
            controller=ref.get("controller"),
        )
        for ref in meta.get("ownerReferences", [])
    ]
    return client.V1ConfigMap(
        metadata=client.V1ObjectMeta(
            name=meta["name"],
            namespace=meta["namespace"],
            labels=meta.get("labels"),
            annotations=meta.get("annotations"),
            owner_references=owner_refs or None,
        ),
        data=body.get("data"),
    )


def to_v1_legacy_marker(meta=None, *, generation: str = "1") -> client.V1ConfigMap:
    return to_v1_config_map(legacy_marker(meta=meta, generation=generation))


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


def _assert_standard_metadata(
    metadata: dict, component: str, *, retained: bool
) -> None:
    assert metadata["namespace"] == "operators"
    assert metadata["labels"] == {
        "app.kubernetes.io/name": "coriolis",
        "app.kubernetes.io/instance": "example",
        "app.kubernetes.io/version": "2603.4",
        "app.kubernetes.io/component": component,
        "app.kubernetes.io/part-of": "coriolis-appliance",
        "app.kubernetes.io/managed-by": "coriolis-operator",
        "coriolis.cloudbase.it/appliance": "example",
        "coriolis.cloudbase.it/component": component,
    }
    annotations = {"coriolis.cloudbase.it/appliance-name": "example"}
    if retained:
        annotations[RETENTION_ANNOTATION] = "retain"
        assert "ownerReferences" not in metadata
    else:
        assert metadata["ownerReferences"] == [dict(OWNER, controller=True)]
    assert metadata["annotations"] == annotations


@pytest.mark.parametrize(
    ("builder", "values", "component", "retained"),
    [
        (
            build_coriolis_credentials_secret,
            CORIOLIS_CREDENTIALS,
            "coriolis-credentials",
            True,
        ),
        (
            build_infrastructure_credentials_secret,
            INFRASTRUCTURE_CREDENTIALS,
            "infrastructure-credentials",
            True,
        ),
        (
            build_step_ca_credentials_secret,
            STEP_CA_CREDENTIALS,
            "step-ca-credentials",
            True,
        ),
        (build_coriolis_config_map, CORIOLIS_CONFIG, "coriolis-config", False),
        (
            build_coriolis_config_secret,
            CORIOLIS_CONFIG_SECRET,
            "coriolis-config-secret",
            False,
        ),
    ],
    ids=(
        "coriolis-credentials",
        "infrastructure-credentials",
        "step-ca-credentials",
        "coriolis-config",
        "coriolis-config-secret",
    ),
)
def test_appliance_resource_builders_metadata_and_names(
    builder, values: dict[str, str], component: str, retained: bool
) -> None:
    kwargs = {
        "appliance_name": "example",
        "namespace": "operators",
        "accepted_version": "2603.4",
        "values": values,
    }
    if retained:
        kwargs["retention"] = "retain"
    else:
        kwargs["owner"] = OWNER

    body = builder(**kwargs)

    assert body["apiVersion"] == "v1"
    assert body["metadata"]["name"] == f"example-{component}"
    _assert_standard_metadata(body["metadata"], component, retained=retained)
    if body["kind"] == "Secret":
        assert body["type"] == "Opaque"
        assert "stringData" not in body
    else:
        assert body["kind"] == "ConfigMap"
        assert "type" not in body


@pytest.mark.parametrize(
    ("builder", "values", "retained"),
    [
        (build_coriolis_credentials_secret, CORIOLIS_CREDENTIALS, True),
        (build_infrastructure_credentials_secret, INFRASTRUCTURE_CREDENTIALS, True),
        (build_step_ca_credentials_secret, STEP_CA_CREDENTIALS, True),
        (build_coriolis_config_secret, CORIOLIS_CONFIG_SECRET, False),
    ],
)
def test_secret_builders_base64_encode_opaque_values(
    builder, values: dict[str, str], retained: bool
) -> None:
    kwargs = {
        "appliance_name": "example",
        "namespace": "operators",
        "accepted_version": "2603.4",
        "values": values,
    }
    if retained:
        kwargs["retention"] = "retain"
    else:
        kwargs["owner"] = OWNER

    body = builder(**kwargs)

    assert set(body["data"]) == set(values)
    assert {
        key: base64.b64decode(value).decode("utf-8")
        for key, value in body["data"].items()
    } == values
    assert "stringData" not in body


def test_coriolis_config_map_has_only_plain_approved_values() -> None:
    body = build_coriolis_config_map(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
        values=CORIOLIS_CONFIG,
    )

    assert body["data"] == CORIOLIS_CONFIG
    assert set(body["data"]) == {
        "coriolis-api.wsgi",
        "wsgi-coriolis.conf",
        "vixdisklib.conf",
        "api-paste.ini",
        "policy.yml",
        "coriolis.release",
    }


@pytest.mark.parametrize(
    ("builder", "values", "retained"),
    [
        (build_coriolis_credentials_secret, CORIOLIS_CREDENTIALS, True),
        (build_infrastructure_credentials_secret, INFRASTRUCTURE_CREDENTIALS, True),
        (build_step_ca_credentials_secret, STEP_CA_CREDENTIALS, True),
        (build_coriolis_config_map, CORIOLIS_CONFIG, False),
        (build_coriolis_config_secret, CORIOLIS_CONFIG_SECRET, False),
    ],
)
def test_resource_builders_reject_missing_extra_and_non_string_values(
    builder, values: dict[str, str], retained: bool
) -> None:
    kwargs = {
        "appliance_name": "example",
        "namespace": "operators",
        "accepted_version": "2603.4",
    }
    if retained:
        kwargs["retention"] = "retain"
    else:
        kwargs["owner"] = OWNER
    missing = dict(values)
    missing.pop(next(iter(missing)))
    extra = dict(values, unexpected="synthetic extra")
    non_string = dict(values)
    non_string[next(iter(non_string))] = 1

    for invalid_values in (missing, extra, non_string):
        with pytest.raises(ValueError) as excinfo:
            builder(**kwargs, values=invalid_values)
        assert "synthetic extra" not in str(excinfo.value)


def test_config_map_rejects_credentials_and_coriolis_conf() -> None:
    for forbidden_key in (*CORIOLIS_CREDENTIALS, "coriolis.conf"):
        values = dict(CORIOLIS_CONFIG, **{forbidden_key: "synthetic value"})
        with pytest.raises(ValueError) as excinfo:
            build_coriolis_config_map(
                appliance_name="example",
                namespace="operators",
                accepted_version="2603.4",
                owner=OWNER,
                values=values,
            )
        assert "synthetic value" not in str(excinfo.value)


def test_resource_builders_use_existing_metadata_validation() -> None:
    with pytest.raises(ValueError):
        build_coriolis_credentials_secret(
            appliance_name="UPPER",
            namespace="operators",
            accepted_version="2603.4",
            retention="retain",
            values=CORIOLIS_CREDENTIALS,
        )
    with pytest.raises(ValueError):
        build_step_ca_credentials_secret(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            retention="",
            values=STEP_CA_CREDENTIALS,
        )


@pytest.mark.parametrize(
    ("builder", "values", "retained"),
    [
        (build_coriolis_credentials_secret, CORIOLIS_CREDENTIALS, True),
        (build_infrastructure_credentials_secret, INFRASTRUCTURE_CREDENTIALS, True),
        (build_step_ca_credentials_secret, STEP_CA_CREDENTIALS, True),
        (build_coriolis_config_map, CORIOLIS_CONFIG, False),
        (build_coriolis_config_secret, CORIOLIS_CONFIG_SECRET, False),
    ],
)
def test_resource_builders_do_not_mutate_inputs(
    builder, values: dict[str, str], retained: bool
) -> None:
    original = copy.deepcopy(values)
    kwargs = {
        "appliance_name": "example",
        "namespace": "operators",
        "accepted_version": "2603.4",
        "values": values,
    }
    if retained:
        kwargs["retention"] = "retain"
    else:
        kwargs["owner"] = OWNER

    builder(**kwargs)

    assert values == original


@pytest.mark.parametrize(
    ("builder", "values", "expected_keys"),
    [
        (
            build_coriolis_credentials_secret,
            CORIOLIS_CREDENTIALS,
            CORIOLIS_CREDENTIALS_KEYS,
        ),
        (
            build_infrastructure_credentials_secret,
            INFRASTRUCTURE_CREDENTIALS,
            INFRASTRUCTURE_CREDENTIALS_KEYS,
        ),
        (
            build_step_ca_credentials_secret,
            STEP_CA_CREDENTIALS,
            STEP_CA_CREDENTIALS_KEYS,
        ),
    ],
)
def test_validated_retained_secret_values_round_trips_retained_builders(
    builder, values: dict[str, str], expected_keys: frozenset[str]
) -> None:
    body = builder(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        retention="retain",
        values=values,
    )

    result = validated_retained_secret_values(
        existing=body, expected_keys=expected_keys
    )

    assert result == values
    assert result is not values


def test_validated_retained_secret_values_supports_v1_secret_and_absent_identity() -> (
    None
):
    secret = client.V1Secret(
        type="Opaque",
        data={"credential": base64.b64encode(b"synthetic decoded").decode("ascii")},
    )

    assert validated_retained_secret_values(
        existing=secret, expected_keys=frozenset({"credential"})
    ) == {"credential": "synthetic decoded"}
    assert validated_retained_secret_values(
        existing={"type": "Opaque", "data": secret.data},
        expected_keys=frozenset({"credential"}),
    ) == {"credential": "synthetic decoded"}


@pytest.mark.parametrize(
    "existing",
    [
        {"apiVersion": "v2", "kind": "Secret", "type": "Opaque", "data": {}},
        {"apiVersion": "v1", "kind": "ConfigMap", "type": "Opaque", "data": {}},
        {"kind": "Secret", "type": None, "data": {}},
        {"kind": "Secret", "type": "kubernetes.io/tls", "data": {}},
        {"kind": "Secret", "type": "Opaque", "stringData": {}, "data": {}},
        {"kind": "Secret", "type": "Opaque", "stringData": {"x": "y"}, "data": {}},
        {"kind": "Secret", "type": "Opaque"},
        {"kind": "Secret", "type": "Opaque", "data": "not-a-mapping"},
        {"kind": "Secret", "type": "Opaque", "data": {}},
        {"kind": "Secret", "type": "Opaque", "data": {"unexpected": "eA=="}},
        {
            "kind": "Secret",
            "type": "Opaque",
            "data": {"credential": "eA==", "unexpected": "eA=="},
        },
        {"kind": "Secret", "type": "Opaque", "data": {"credential": 1}},
        {
            "kind": "Secret",
            "type": "Opaque",
            "data": {"credential": "synthetic-encoded-sentinel"},
        },
        {
            "kind": "Secret",
            "type": "Opaque",
            "data": {"credential": "/w=="},
        },
        {"kind": "Secret", "type": "Opaque", "data": {"credential": ""}},
    ],
    ids=(
        "wrong-api-version",
        "wrong-kind",
        "missing-type",
        "wrong-type",
        "empty-string-data",
        "present-string-data",
        "missing-data",
        "non-mapping-data",
        "empty-keys",
        "missing-required-key",
        "extra-key",
        "non-string-value",
        "malformed-base64",
        "invalid-utf8",
        "decoded-empty-value",
    ),
)
def test_validated_retained_secret_values_rejects_invalid_persisted_shapes(
    existing: object,
) -> None:
    with pytest.raises(ValueError) as excinfo:
        validated_retained_secret_values(
            existing=existing, expected_keys=frozenset({"credential"})
        )

    message = str(excinfo.value)
    assert "synthetic-encoded-sentinel" not in message
    assert "synthetic decoded" not in message


def test_validated_retained_secret_values_does_not_mutate_input() -> None:
    existing = {
        "type": "Opaque",
        "data": {"credential": base64.b64encode(b"synthetic decoded").decode("ascii")},
    }
    before = copy.deepcopy(existing)

    result = validated_retained_secret_values(
        existing=existing, expected_keys=frozenset({"credential"})
    )

    assert existing == before
    assert result == {"credential": "synthetic decoded"}
    assert result is not existing["data"]


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
    core_api = make_core_api()

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(),
        core_api=core_api,
    )

    expected_body = desired_body()
    core_api.read_namespaced_config_map.assert_called_once_with(
        name="example-operator-state",
        namespace="operators",
    )
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
    core_api = make_core_api()

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
    core_api = make_core_api()

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
    core_api = make_core_api()

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
    core_api = make_core_api()
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


def _mutate_label(key: str, value: str):
    def mutate(body: dict) -> dict:
        existing = copy.deepcopy(body)
        existing["metadata"]["labels"][key] = value
        return existing

    return mutate


def _mutate_annotation(value: str):
    def mutate(body: dict) -> dict:
        existing = copy.deepcopy(body)
        existing["metadata"]["annotations"][APPLIANCE_NAME_ANNOTATION] = value
        return existing

    return mutate


def _drop_label(key: str):
    def mutate(body: dict) -> dict:
        existing = copy.deepcopy(body)
        del existing["metadata"]["labels"][key]
        return existing

    return mutate


def _mutate_owner(key: str, value: str):
    def mutate(body: dict) -> dict:
        existing = copy.deepcopy(body)
        existing["metadata"]["ownerReferences"][0][key] = value
        return existing

    return mutate


def _mutate_owner_controller(value: bool):
    def mutate(body: dict) -> dict:
        existing = copy.deepcopy(body)
        existing["metadata"]["ownerReferences"][0]["controller"] = value
        return existing

    return mutate


def _legacy_incompatible(accepted_version: str, profile: str):
    def mutate(body: dict) -> dict:
        existing = legacy_marker()
        existing["data"]["acceptedVersion"] = accepted_version
        existing["data"]["profile"] = profile
        return existing

    return mutate


COLLISION_MUTATORS = [
    (
        "appliance-identity-label",
        _mutate_label("coriolis.cloudbase.it/appliance", "other"),
    ),
    ("component-label", _mutate_label("coriolis.cloudbase.it/component", "other")),
    ("managed-by-label", _mutate_label("app.kubernetes.io/managed-by", "other")),
    ("full-name-annotation", _mutate_annotation("other")),
    ("partial-managed-metadata", _drop_label("coriolis.cloudbase.it/component")),
    ("owner-uid-mismatch", _mutate_owner("uid", "other")),
    ("owner-controller-false", _mutate_owner_controller(False)),
    ("owner-name-mismatch", _mutate_owner("name", "other")),
    ("incompatible-legacy-data", _legacy_incompatible("2026.9.0", SUPPORTED_PROFILE)),
]


def test_reconcile_matching_managed_marker_proceeds_with_unchanged_body() -> None:
    core_api = make_core_api(existing=desired_body())

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(),
        core_api=core_api,
    )

    core_api.read_namespaced_config_map.assert_called_once_with(
        name="example-operator-state", namespace="operators"
    )
    core_api.patch_namespaced_config_map.assert_called_once_with(
        name="example-operator-state",
        namespace="operators",
        body=desired_body(),
        field_manager="coriolis-operator",
        force=True,
    )
    assert status["acceptedVersion"] == "2603.4"


def test_reconcile_compatible_legacy_marker_normalizes_stale_generation() -> None:
    core_api = make_core_api(existing=legacy_marker(generation="1"))

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(),
        core_api=core_api,
    )

    body = core_api.patch_namespaced_config_map.call_args.kwargs["body"]
    assert body["data"]["generation"] == "7"
    assert body["data"]["acceptedVersion"] == "2603.4"
    assert body["data"]["profile"] == "core"
    assert status["acceptedVersion"] == "2603.4"


@pytest.mark.parametrize(
    "appliance_name",
    [
        "example.domain",
        f"{'a' * 40}.{'b' * 40}.{'c' * 40}." + "d" * 40,
    ],
)
def test_reconcile_compatible_legacy_dotted_and_long_names_proceed(
    appliance_name: str,
) -> None:
    meta = sample_meta()
    meta["name"] = appliance_name
    core_api = make_core_api(existing=legacy_marker(meta=meta, generation="1"))

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=meta,
        core_api=core_api,
    )

    core_api.patch_namespaced_config_map.assert_called_once()
    assert status["acceptedVersion"] == "2603.4"


@pytest.mark.parametrize(
    "mutate",
    [case[1] for case in COLLISION_MUTATORS],
    ids=[case[0] for case in COLLISION_MUTATORS],
)
def test_reconcile_collision_blocks_and_never_patches(mutate) -> None:
    core_api = make_core_api(existing=mutate(desired_body()))

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(),
        core_api=core_api,
    )

    core_api.read_namespaced_config_map.assert_called_once()
    core_api.patch_namespaced_config_map.assert_not_called()
    assert "acceptedVersion" not in status
    assert [c["type"] for c in status["conditions"]] == CONDITION_TYPES
    statuses = {c["type"]: c for c in status["conditions"]}
    assert statuses["Accepted"]["status"] == "True"
    assert statuses["Accepted"]["reason"] == "Accepted"
    assert statuses["Progressing"]["status"] == "False"
    assert statuses["Progressing"]["reason"] == "ResourceCollision"
    assert statuses["Reconciled"]["status"] == "False"
    assert statuses["Reconciled"]["reason"] == "ResourceCollision"
    assert statuses["Ready"]["status"] == "False"
    assert statuses["Ready"]["reason"] == "ResourceCollision"
    assert statuses["Degraded"]["status"] == "True"
    assert statuses["Degraded"]["reason"] == "ResourceCollision"
    assert statuses["Upgradeable"]["status"] == "False"
    assert statuses["Upgradeable"]["reason"] == "UpgradeNotSupported"
    assert "operators/example-operator-state" in statuses["Reconciled"]["message"]


def test_reconcile_collision_preserves_prior_accepted_version_and_transition() -> None:
    core_api = make_core_api(
        existing=_mutate_label("coriolis.cloudbase.it/appliance", "other")(
            desired_body()
        )
    )
    prior_conditions = [
        {
            "type": "Degraded",
            "status": "True",
            "lastTransitionTime": "2026-08-19T09:00:00Z",
        }
    ]

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(generation=8),
        status={"acceptedVersion": "2603.4", "conditions": prior_conditions},
        core_api=core_api,
    )

    core_api.patch_namespaced_config_map.assert_not_called()
    assert status["acceptedVersion"] == "2603.4"
    degraded = next(c for c in status["conditions"] if c["type"] == "Degraded")
    assert degraded["status"] == "True"
    assert degraded["lastTransitionTime"] == "2026-08-19T09:00:00Z"


def test_reconcile_propagates_non_404_api_read_error_without_patch() -> None:
    core_api = make_core_api()
    core_api.read_namespaced_config_map.side_effect = client.ApiException(status=403)

    with pytest.raises(client.ApiException) as excinfo:
        reconcile_appliance(
            spec={"profile": "core", "version": "2603.4"},
            meta=sample_meta(),
            core_api=core_api,
        )

    assert excinfo.value.status == 403
    core_api.patch_namespaced_config_map.assert_not_called()


def test_reconcile_propagates_generic_read_error_without_patch() -> None:
    core_api = make_core_api()
    core_api.read_namespaced_config_map.side_effect = RuntimeError("read failed")

    with pytest.raises(RuntimeError, match="read failed"):
        reconcile_appliance(
            spec={"profile": "core", "version": "2603.4"},
            meta=sample_meta(),
            core_api=core_api,
        )

    core_api.patch_namespaced_config_map.assert_not_called()


def test_classify_managed_ignores_extra_labels_and_annotations() -> None:
    existing = copy.deepcopy(desired_body())
    existing["metadata"]["labels"]["example.com/extra"] = "x"
    existing["metadata"]["annotations"]["example.com/extra"] = "y"

    assert classify_existing_marker(existing=existing, desired=desired_body()) == (
        MARKER_MANAGED
    )


@pytest.mark.parametrize(
    "appliance_name",
    [
        "example.domain",
        f"{'a' * 40}.{'b' * 40}.{'c' * 40}." + "d" * 40,
    ],
)
def test_classify_legacy_dotted_and_long_names(appliance_name: str) -> None:
    meta = sample_meta()
    meta["name"] = appliance_name

    assert (
        classify_existing_marker(
            existing=legacy_marker(meta=meta, generation="1"),
            desired=desired_body(meta),
        )
        == MARKER_LEGACY
    )


def test_classify_existing_marker_returns_expected_classes() -> None:
    body = desired_body()
    assert classify_existing_marker(existing=body, desired=body) == MARKER_MANAGED
    assert (
        classify_existing_marker(existing=legacy_marker(), desired=body)
        == MARKER_LEGACY
    )
    collided = _mutate_label("coriolis.cloudbase.it/appliance", "other")(body)
    assert classify_existing_marker(existing=collided, desired=body) == (
        MARKER_COLLISION
    )


def test_collision_conditions_are_deterministic_and_identify_marker() -> None:
    conditions = collision_conditions("operators", "example-operator-state")

    assert [c[0] for c in conditions] == CONDITION_TYPES
    assert conditions[0] == (
        "Accepted",
        "True",
        "Accepted",
        "The requested profile and version are supported.",
    )
    assert conditions[5] == (
        "Upgradeable",
        "False",
        "UpgradeNotSupported",
        "The core profile has no supported upgrade path.",
    )
    message = (
        "The existing ConfigMap 'operators/example-operator-state' conflicts "
        "with the operator's managed state marker and was not modified."
    )
    assert conditions[1] == ("Progressing", "False", "ResourceCollision", message)
    assert conditions[2] == ("Reconciled", "False", "ResourceCollision", message)
    assert conditions[3] == ("Ready", "False", "ResourceCollision", message)
    assert conditions[4] == ("Degraded", "True", "ResourceCollision", message)


def test_classify_managed_v1_config_map_object() -> None:
    existing = to_v1_config_map(desired_body())

    assert classify_existing_marker(existing=existing, desired=desired_body()) == (
        MARKER_MANAGED
    )


def test_classify_legacy_v1_config_map_object() -> None:
    existing = to_v1_legacy_marker()

    assert classify_existing_marker(existing=existing, desired=desired_body()) == (
        MARKER_LEGACY
    )


def test_reconcile_matching_managed_v1_config_map_proceeds() -> None:
    core_api = make_core_api(existing=to_v1_config_map(desired_body()))

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(),
        core_api=core_api,
    )

    core_api.patch_namespaced_config_map.assert_called_once()
    assert status["acceptedVersion"] == "2603.4"


def test_reconcile_compatible_legacy_v1_config_map_normalizes_generation() -> None:
    core_api = make_core_api(existing=to_v1_legacy_marker(generation="1"))

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(),
        core_api=core_api,
    )

    body = core_api.patch_namespaced_config_map.call_args.kwargs["body"]
    assert body["data"]["generation"] == "7"
    assert body["data"]["acceptedVersion"] == "2603.4"
    assert status["acceptedVersion"] == "2603.4"


def test_reconcile_retention_annotation_collides_and_skips_ssa() -> None:
    existing = desired_body()
    existing["metadata"]["annotations"][RETENTION_ANNOTATION] = "daily"
    core_api = make_core_api(existing=existing)

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(),
        core_api=core_api,
    )

    core_api.patch_namespaced_config_map.assert_not_called()
    statuses = {c["type"]: c for c in status["conditions"]}
    assert statuses["Reconciled"]["reason"] == "ResourceCollision"
    assert statuses["Degraded"]["status"] == "True"
    assert statuses["Degraded"]["reason"] == "ResourceCollision"


RETAINED = {
    "resource_name": "example-data",
    "namespace": "operators",
    "appliance_name": "example",
    "component": "data",
    "accepted_version": SUPPORTED_INITIAL_VERSION,
    "retention": "daily",
}


def retained_metadata(meta=None) -> dict:
    meta = meta or RETAINED
    return build_resource_metadata(
        resource_name=meta["resource_name"],
        namespace=meta["namespace"],
        appliance_name=meta["appliance_name"],
        component=meta["component"],
        accepted_version=meta["accepted_version"],
        retention=meta["retention"],
    )


def retained_body(meta=None) -> dict:
    meta = meta or RETAINED
    return {"metadata": retained_metadata(meta)}


def classify(existing, meta=None) -> RetainedClassification:
    meta = meta or RETAINED
    return classify_retained_resource(
        existing=existing,
        resource_name=meta["resource_name"],
        namespace=meta["namespace"],
        appliance_name=meta["appliance_name"],
        component=meta["component"],
        accepted_version=meta["accepted_version"],
        retention=meta["retention"],
    )


def _ret_annotation(body, value):
    body["metadata"]["annotations"][APPLIANCE_NAME_ANNOTATION] = value
    return body


def _ret_label(body, key, value):
    body["metadata"]["labels"][key] = value
    return body


def _ret_drop_label(body, key):
    del body["metadata"]["labels"][key]
    return body


def _ret_drop_annotation(body, key):
    del body["metadata"]["annotations"][key]
    return body


def _ret_add_owner(body):
    body["metadata"]["ownerReferences"] = [dict(OWNER, controller=True)]
    return body


def to_v1_retained(body, kind="secret") -> object:
    meta = body["metadata"]
    owner_refs = [
        client.V1OwnerReference(
            api_version=ref["apiVersion"],
            kind=ref["kind"],
            name=ref["name"],
            uid=ref["uid"],
            controller=ref.get("controller"),
        )
        for ref in meta.get("ownerReferences", [])
    ]
    object_meta = client.V1ObjectMeta(
        name=meta["name"],
        namespace=meta["namespace"],
        labels=meta.get("labels"),
        annotations=meta.get("annotations"),
        owner_references=owner_refs or None,
    )
    if kind == "pvc":
        return client.V1PersistentVolumeClaim(metadata=object_meta)
    return client.V1Secret(metadata=object_meta)


def test_retained_absent_is_eligible_for_creation() -> None:
    assert classify(None) == RetainedClassification.ABSENT


@pytest.mark.parametrize(
    "kind",
    ["secret", "pvc"],
)
def test_retained_exact_match_is_reuse(kind: str) -> None:
    body = retained_body()
    existing = to_v1_retained(body, kind=kind)

    assert classify(existing) == RetainedClassification.REUSE
    assert classify(body) == RetainedClassification.REUSE


def test_retained_dict_exact_match_is_reuse() -> None:
    assert classify(retained_body()) == RetainedClassification.REUSE


def test_retained_changed_cr_uid_with_otherwise_exact_identity_is_reuse() -> None:
    body = retained_body()
    body["metadata"]["annotations"]["coriolis.cloudbase.it/appliance-uid"] = "stale-uid"
    assert classify(body) == RetainedClassification.REUSE


def test_retained_retention_annotation_mismatch_collides() -> None:
    body = retained_body()
    body["metadata"]["annotations"][RETENTION_ANNOTATION] = "weekly"
    assert classify(body) == RetainedClassification.COLLISION


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: _ret_annotation(body, "other"),
        lambda body: _ret_label(body, "coriolis.cloudbase.it/appliance", "other"),
        lambda body: _ret_label(body, "coriolis.cloudbase.it/component", "other"),
        lambda body: _ret_label(body, "app.kubernetes.io/managed-by", "other"),
        lambda body: _ret_label(body, "app.kubernetes.io/version", "2026.9.0"),
    ],
)
def test_retained_conflicting_identity_field_collides(mutate) -> None:
    assert classify(mutate(retained_body())) == RetainedClassification.COLLISION


def test_retained_name_mismatch_collides() -> None:
    body = retained_body()
    body["metadata"]["name"] = "other-data"
    assert classify(body) == RetainedClassification.COLLISION


def test_retained_namespace_mismatch_collides() -> None:
    body = retained_body()
    body["metadata"]["namespace"] = "other"
    assert classify(body) == RetainedClassification.COLLISION


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: _ret_drop_label(body, "coriolis.cloudbase.it/appliance"),
        lambda body: _ret_drop_label(body, "coriolis.cloudbase.it/component"),
        lambda body: _ret_drop_label(body, "app.kubernetes.io/component"),
        lambda body: _ret_drop_annotation(body, APPLIANCE_NAME_ANNOTATION),
        lambda body: _ret_drop_annotation(body, RETENTION_ANNOTATION),
    ],
)
def test_retained_missing_partial_identity_metadata_collides(mutate) -> None:
    assert classify(mutate(retained_body())) == RetainedClassification.COLLISION


def test_retained_any_owner_reference_collides() -> None:
    body = _ret_add_owner(retained_body())
    assert classify(body) == RetainedClassification.COLLISION


def test_retained_matching_owner_uid_still_collides() -> None:
    body = _ret_add_owner(retained_body())
    body["metadata"]["ownerReferences"][0]["uid"] = OWNER["uid"]
    assert classify(body) == RetainedClassification.COLLISION


def test_retained_unrelated_extra_metadata_is_allowed() -> None:
    body = retained_body()
    body["metadata"]["labels"]["example.com/extra"] = "x"
    body["metadata"]["annotations"]["example.com/extra"] = "y"
    assert classify(body) == RetainedClassification.REUSE


def test_retained_external_read_only_secret_is_never_reused() -> None:
    external_meta = dict(RETAINED)
    external_meta["resource_name"] = "coriolis-appliance-registry"
    assert "coriolis-appliance-registry" in EXTERNAL_READ_ONLY_RESOURCES

    assert classify(None, external_meta) == RetainedClassification.COLLISION

    forged = retained_body(external_meta)
    assert classify(forged, external_meta) == RetainedClassification.COLLISION


def test_retained_non_mapping_metadata_collides() -> None:
    assert classify({"metadata": "not-a-mapping"}) == RetainedClassification.COLLISION


def test_retained_no_input_mutation() -> None:
    body = retained_body()
    before = copy.deepcopy(body)
    classify(body)
    assert body == before
