"""Focused tests for pure Barbican reconcile builders and preflight."""

import base64
import copy
import json

import pytest

from coriolis_operator.barbican import (
    BARBICAN_API_COMMAND,
    BARBICAN_API_IMAGE,
    BARBICAN_API_STATE_DIR,
    BARBICAN_CONFIG_KEYS,
    BARBICAN_DB_SYNC_COMMAND,
    BARBICAN_HEALTH_PROBE,
    BARBICAN_IMAGE_PULL_SECRET_NAME,
    BARBICAN_PORT,
    BARBICAN_REPLICAS,
    BARBICAN_RUN_AS_ID,
    BARBICAN_RUNTIME_DIR,
    BARBICAN_SUPPLEMENTAL_GROUP,
    BARBICAN_TERMINATION_GRACE_PERIOD_SECONDS,
    BARBICAN_TMP_DIR,
    BARBICAN_VASSAL_PATH,
    BARBICAN_WORKER_COMMAND,
    BARBICAN_WORKER_IMAGE,
)
from coriolis_operator.reconcile import (
    BARBICAN_CREDENTIALS_KEYS,
    CORIOLIS_CREDENTIALS_KEYS,
    INFRASTRUCTURE_CREDENTIALS_KEYS,
    KEYSTONE_DATABASE_CREDENTIALS_KEYS,
    OwnedClassification,
    RetainedClassification,
    appliance_identity,
    appliance_resource_name,
    build_barbican_api_deployment,
    build_barbican_api_service,
    build_barbican_config_map,
    build_barbican_config_secret,
    build_barbican_credentials_secret,
    build_barbican_worker_deployment,
    generate_barbican_credentials,
    preflight_barbican_resources,
)

APPLIANCE = "example"
NAMESPACE = "operators"
VERSION = "2603.4"
RETENTION = "state-credentials"
OWNER = {
    "apiVersion": "coriolis.cloudbase.it/v1alpha1",
    "kind": "CoriolisAppliance",
    "name": "example",
    "uid": "abc-123",
}
_CRYPTO_RAW = b"k" * 32
_CRYPTO_KEY = base64.urlsafe_b64encode(_CRYPTO_RAW).decode("ascii")
# Public upstream default, constructed locally and never stored as a KEK literal.
_BUILTIN_DEFAULT_KEK = base64.urlsafe_b64encode(
    b"thirty_two_byte_keyblahblahblahh"
).decode("ascii")
RABBITMQ_PASSWORD = "rabbitpass1"
RETAINED_VALUES = {
    "barbican_database_password": "db synthetic",
    "barbican_keystone_password": "keystone synthetic",
    "barbican_crypto_key": _CRYPTO_KEY,
}


def _fail_password(_: int) -> str:
    raise AssertionError("password factory called")


def _fail_bytes(_: int) -> bytes:
    raise AssertionError("byte factory called")


def preflight_kwargs(**overrides: object) -> dict:
    kwargs: dict = {
        "appliance_name": APPLIANCE,
        "namespace": NAMESPACE,
        "accepted_version": VERSION,
        "owner": OWNER,
        "retention": RETENTION,
        "database_host": f"{APPLIANCE}-mariadb",
        "rabbitmq_host": f"{APPLIANCE}-rabbitmq",
        "keystone_host": f"{APPLIANCE}-keystone",
        "barbican_host": f"{APPLIANCE}-barbican-api",
        "rabbitmq_password": RABBITMQ_PASSWORD,
        "barbican_credentials_secret": None,
        "barbican_config_map": None,
        "barbican_config_secret": None,
        "barbican_api_service": None,
        "barbican_api_deployment": None,
        "barbican_worker_deployment": None,
        "password_factory": _fail_password,
        "byte_factory": _fail_bytes,
    }
    kwargs.update(overrides)
    return kwargs


def absent_preflight(
    **overrides: object,
) -> tuple[object, list[int], list[int]]:
    password_calls: list[int] = []
    byte_calls: list[int] = []

    def password_factory(size: int) -> str:
        password_calls.append(size)
        return f"gen-password-{len(password_calls)}"

    def byte_factory(size: int) -> bytes:
        byte_calls.append(size)
        return bytes([0x40 + len(byte_calls)]) * size

    result = preflight_barbican_resources(
        **preflight_kwargs(
            password_factory=password_factory,
            byte_factory=byte_factory,
            **overrides,
        )
    )
    return result, password_calls, byte_calls


def generated_credentials() -> dict[str, str]:
    return {
        "barbican_database_password": "gen-password-1",
        "barbican_keystone_password": "gen-password-2",
        "barbican_crypto_key": base64.urlsafe_b64encode(b"A" * 32).decode("ascii"),
    }


def manifests(appliance_name: str = APPLIANCE) -> tuple[dict, ...]:
    result, _, _ = absent_preflight(appliance_name=appliance_name)
    assert result.manifests
    return result.manifests


def bodies_by_identity(appliance_name: str = APPLIANCE) -> dict[tuple, dict]:
    return {
        (body["kind"], body["metadata"]["name"]): body
        for body in manifests(appliance_name)
    }


def _pod_spec(deployment: dict) -> dict:
    return deployment["spec"]["template"]["spec"]


def _restricted_pod_security() -> dict:
    return {
        "runAsUser": BARBICAN_RUN_AS_ID,
        "runAsGroup": BARBICAN_RUN_AS_ID,
        "fsGroup": BARBICAN_RUN_AS_ID,
        "fsGroupChangePolicy": "OnRootMismatch",
        "supplementalGroups": [BARBICAN_SUPPLEMENTAL_GROUP],
    }


def _restricted_container_security() -> dict:
    return {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def _config_volume() -> dict:
    return {
        "name": "config",
        "projected": {
            "sources": [
                {
                    "configMap": {
                        "name": appliance_resource_name(APPLIANCE, "barbican-config"),
                        "items": [
                            {
                                "key": "barbican-api-paste.ini",
                                "path": "barbican-api-paste.ini",
                                "mode": 0o444,
                            },
                            {
                                "key": "barbican-api.ini",
                                "path": "vassals/barbican-api.ini",
                                "mode": 0o444,
                            },
                            {
                                "key": "db-sync.py",
                                "path": "db-sync.py",
                                "mode": 0o444,
                            },
                            {
                                "key": "policy.yaml",
                                "path": "policy.yaml",
                                "mode": 0o444,
                            },
                        ],
                    }
                },
                {
                    "secret": {
                        "name": appliance_resource_name(
                            APPLIANCE, "barbican-config-secret"
                        ),
                        "items": [
                            {
                                "key": "barbican.conf",
                                "path": "barbican.conf",
                                "mode": 0o440,
                            }
                        ],
                    }
                },
            ]
        },
    }


def _assert_owned_metadata(body: dict, component: str) -> None:
    metadata = body["metadata"]
    assert metadata["name"] == appliance_resource_name(APPLIANCE, component)
    assert metadata["ownerReferences"] == [{**OWNER, "controller": True}]
    assert metadata["labels"]["coriolis.cloudbase.it/component"] == component
    assert "coriolis.cloudbase.it/retention" not in metadata["annotations"]


def test_credentials_keyset_is_exact_and_leaves_existing_keysets_unchanged() -> None:
    assert BARBICAN_CREDENTIALS_KEYS == frozenset(
        {
            "barbican_database_password",
            "barbican_keystone_password",
            "barbican_crypto_key",
        }
    )
    existing = (
        CORIOLIS_CREDENTIALS_KEYS
        | INFRASTRUCTURE_CREDENTIALS_KEYS
        | KEYSTONE_DATABASE_CREDENTIALS_KEYS
    )
    assert BARBICAN_CREDENTIALS_KEYS.isdisjoint(existing)


def test_generate_barbican_credentials_uses_two_independent_password_calls() -> None:
    calls: list[int] = []

    def password_factory(size: int) -> str:
        calls.append(size)
        return f"password-{len(calls)}"

    byte_calls: list[int] = []

    def byte_factory(size: int) -> bytes:
        byte_calls.append(size)
        return b"z" * size

    values = generate_barbican_credentials(password_factory, byte_factory)

    assert set(values) == BARBICAN_CREDENTIALS_KEYS
    assert calls == [32, 32]
    assert byte_calls == [32]
    assert values["barbican_database_password"] == "password-1"
    assert values["barbican_keystone_password"] == "password-2"
    assert values["barbican_database_password"] != values["barbican_keystone_password"]
    assert values["barbican_crypto_key"] == base64.urlsafe_b64encode(b"z" * 32).decode(
        "ascii"
    )


def test_generate_barbican_credentials_defaults_are_the_secrets_module() -> None:
    import inspect
    import secrets as secrets_module

    signature = inspect.signature(generate_barbican_credentials)
    assert (
        signature.parameters["password_factory"].default is secrets_module.token_urlsafe
    )
    assert signature.parameters["byte_factory"].default is secrets_module.token_bytes

    values = generate_barbican_credentials()

    assert set(values) == BARBICAN_CREDENTIALS_KEYS
    assert values["barbican_database_password"] != values["barbican_keystone_password"]
    assert len(base64.urlsafe_b64decode(values["barbican_crypto_key"])) == 32


def test_generate_barbican_credentials_rejects_empty_password_output() -> None:
    with pytest.raises(ValueError) as excinfo:
        generate_barbican_credentials(lambda _: "", lambda size: b"n" * size)
    assert str(excinfo.value) == (
        "credential token factory must return a non-empty string"
    )


def test_retained_credentials_secret_metadata_keyset_and_encoding() -> None:
    body = build_barbican_credentials_secret(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        retention=RETENTION,
        values=RETAINED_VALUES,
    )
    assert body["apiVersion"] == "v1"
    assert body["kind"] == "Secret"
    assert body["type"] == "Opaque"
    metadata = body["metadata"]
    assert metadata["name"] == "example-barbican-credentials"
    assert "ownerReferences" not in metadata
    assert metadata["annotations"]["coriolis.cloudbase.it/retention"] == RETENTION
    assert (
        metadata["labels"]["coriolis.cloudbase.it/component"] == "barbican-credentials"
    )
    assert set(body["data"]) == BARBICAN_CREDENTIALS_KEYS
    for key, value in RETAINED_VALUES.items():
        assert base64.b64decode(body["data"][key]).decode("utf-8") == value


def test_retained_credentials_secret_rejects_foreign_keyset() -> None:
    values = dict(RETAINED_VALUES)
    values["extra"] = "nope"
    with pytest.raises(ValueError):
        build_barbican_credentials_secret(
            appliance_name=APPLIANCE,
            namespace=NAMESPACE,
            accepted_version=VERSION,
            retention=RETENTION,
            values=values,
        )


def test_owned_config_map_and_config_secret_identities() -> None:
    config_map = build_barbican_config_map(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        owner=OWNER,
        values={key: f"asset {key}" for key in BARBICAN_CONFIG_KEYS},
    )
    assert config_map["kind"] == "ConfigMap"
    assert set(config_map["data"]) == BARBICAN_CONFIG_KEYS
    _assert_owned_metadata(config_map, "barbican-config")

    config_secret = build_barbican_config_secret(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        owner=OWNER,
        values={"barbican.conf": "confidential"},
    )
    assert config_secret["kind"] == "Secret"
    assert config_secret["type"] == "Opaque"
    assert set(config_secret["data"]) == {"barbican.conf"}
    _assert_owned_metadata(config_secret, "barbican-config-secret")


def test_owned_service_identity_and_selector() -> None:
    service = build_barbican_api_service(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        owner=OWNER,
    )
    assert service["apiVersion"] == "v1"
    assert service["kind"] == "Service"
    _assert_owned_metadata(service, "barbican-api")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(APPLIANCE),
        "coriolis.cloudbase.it/component": "barbican-api",
    }
    assert service["spec"]["ports"] == [
        {
            "name": "barbican-api",
            "protocol": "TCP",
            "port": BARBICAN_PORT,
            "targetPort": BARBICAN_PORT,
        }
    ]


def test_api_deployment_is_hardened_with_db_sync_init_and_probes() -> None:
    deployment = build_barbican_api_deployment(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        owner=OWNER,
    )
    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    _assert_owned_metadata(deployment, "barbican-api")
    spec = deployment["spec"]
    assert spec["replicas"] == BARBICAN_REPLICAS
    assert spec["strategy"] == {"type": "Recreate"}
    assert spec["selector"]["matchLabels"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(APPLIANCE),
        "coriolis.cloudbase.it/component": "barbican-api",
    }

    pod = _pod_spec(deployment)
    assert pod["imagePullSecrets"] == [{"name": BARBICAN_IMAGE_PULL_SECRET_NAME}]
    assert pod["securityContext"] == _restricted_pod_security()
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["terminationGracePeriodSeconds"] == (
        BARBICAN_TERMINATION_GRACE_PERIOD_SECONDS
    )
    assert "serviceAccountName" not in pod

    container = pod["containers"][0]
    assert container["name"] == "barbican-api"
    assert container["image"] == BARBICAN_API_IMAGE
    assert container["command"] == list(BARBICAN_API_COMMAND)
    assert container["ports"] == [
        {
            "name": "barbican-api",
            "containerPort": BARBICAN_PORT,
            "protocol": "TCP",
        }
    ]
    assert container["securityContext"] == _restricted_container_security()
    assert container["volumeMounts"] == [
        {"name": "config", "mountPath": BARBICAN_RUNTIME_DIR, "readOnly": True},
        {"name": "tmp", "mountPath": BARBICAN_TMP_DIR},
        {"name": "state", "mountPath": BARBICAN_API_STATE_DIR},
    ]
    for absent in ("env", "envFrom", "resources"):
        assert absent not in container

    probe_command = [BARBICAN_DB_SYNC_COMMAND[0], "-c", BARBICAN_HEALTH_PROBE]
    assert probe_command[0] == "/var/lib/kolla/venv/bin/python3"
    assert container["startupProbe"] == {
        "exec": {"command": probe_command},
        "periodSeconds": 2,
        "timeoutSeconds": 5,
        "failureThreshold": 30,
    }
    assert container["readinessProbe"] == {
        "exec": {"command": probe_command},
        "periodSeconds": 5,
        "timeoutSeconds": 5,
        "failureThreshold": 3,
        "successThreshold": 1,
    }
    assert container["livenessProbe"] == {
        "exec": {"command": probe_command},
        "periodSeconds": 10,
        "timeoutSeconds": 5,
        "failureThreshold": 6,
    }

    db_sync = pod["initContainers"][0]
    assert pod["initContainers"] == [db_sync]
    assert db_sync["name"] == "db-sync"
    assert db_sync["image"] == BARBICAN_API_IMAGE
    assert db_sync["command"] == list(BARBICAN_DB_SYNC_COMMAND)
    assert db_sync["securityContext"] == _restricted_container_security()
    assert db_sync["volumeMounts"] == [
        {"name": "config", "mountPath": BARBICAN_RUNTIME_DIR, "readOnly": True},
        {"name": "tmp", "mountPath": BARBICAN_TMP_DIR},
    ]

    assert pod["volumes"] == [
        _config_volume(),
        {"name": "tmp", "emptyDir": {"medium": "Memory"}},
        {"name": "state", "emptyDir": {}},
    ]


def test_worker_deployment_is_hardened_without_api_surface() -> None:
    deployment = build_barbican_worker_deployment(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        owner=OWNER,
    )
    _assert_owned_metadata(deployment, "barbican-worker")
    spec = deployment["spec"]
    assert spec["replicas"] == BARBICAN_REPLICAS
    assert spec["strategy"] == {"type": "Recreate"}

    pod = _pod_spec(deployment)
    assert pod["imagePullSecrets"] == [{"name": BARBICAN_IMAGE_PULL_SECRET_NAME}]
    assert pod["securityContext"] == _restricted_pod_security()
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["terminationGracePeriodSeconds"] == (
        BARBICAN_TERMINATION_GRACE_PERIOD_SECONDS
    )
    assert "initContainers" not in pod
    assert "serviceAccountName" not in pod

    assert pod["containers"] == [
        {
            "name": "barbican-worker",
            "image": BARBICAN_WORKER_IMAGE,
            "command": list(BARBICAN_WORKER_COMMAND),
            "securityContext": _restricted_container_security(),
            "volumeMounts": [
                {
                    "name": "config",
                    "mountPath": BARBICAN_RUNTIME_DIR,
                    "readOnly": True,
                },
                {"name": "tmp", "mountPath": BARBICAN_TMP_DIR},
            ],
        }
    ]
    container = pod["containers"][0]
    for absent in (
        "ports",
        "env",
        "envFrom",
        "resources",
        "startupProbe",
        "readinessProbe",
        "livenessProbe",
    ):
        assert absent not in container

    assert pod["volumes"] == [
        _config_volume(),
        {"name": "tmp", "emptyDir": {"medium": "Memory"}},
    ]
    volume_names = {volume["name"] for volume in pod["volumes"]}
    assert "state" not in volume_names


def test_projected_config_mounts_vassal_under_runtime_dir() -> None:
    assert BARBICAN_VASSAL_PATH == f"{BARBICAN_RUNTIME_DIR}/vassals/barbican-api.ini"
    sources = _pod_spec(manifests()[4])["volumes"][0]["projected"]["sources"]
    paths = {item["key"]: item["path"] for item in sources[0]["configMap"]["items"]}
    assert paths == {
        "barbican-api-paste.ini": "barbican-api-paste.ini",
        "barbican-api.ini": "vassals/barbican-api.ini",
        "db-sync.py": "db-sync.py",
        "policy.yaml": "policy.yaml",
    }


def test_preflight_absent_generates_credentials_and_ordered_manifests() -> None:
    result, password_calls, byte_calls = absent_preflight()

    assert password_calls == [32, 32]
    assert byte_calls == [32]
    assert set(result.credentials) == {"example-barbican-credentials"}
    assert result.credentials["example-barbican-credentials"] == generated_credentials()
    assert result.classifications == {
        "example-barbican-credentials": RetainedClassification.ABSENT,
        "example-barbican-config": OwnedClassification.ABSENT,
        "example-barbican-config-secret": OwnedClassification.ABSENT,
        "example-barbican-api": OwnedClassification.ABSENT,
        "example-barbican-worker": OwnedClassification.ABSENT,
    }
    identity = [(body["kind"], body["metadata"]["name"]) for body in result.manifests]
    assert identity == [
        ("Secret", "example-barbican-credentials"),
        ("ConfigMap", "example-barbican-config"),
        ("Secret", "example-barbican-config-secret"),
        ("Service", "example-barbican-api"),
        ("Deployment", "example-barbican-api"),
        ("Deployment", "example-barbican-worker"),
    ]


def test_preflight_reuses_retained_values_without_calling_factories() -> None:
    existing_secret = build_barbican_credentials_secret(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        retention=RETENTION,
        values=RETAINED_VALUES,
    )
    bodies = bodies_by_identity()
    result = preflight_barbican_resources(
        **preflight_kwargs(
            barbican_credentials_secret=existing_secret,
            barbican_config_map=bodies[("ConfigMap", "example-barbican-config")],
            barbican_config_secret=bodies[("Secret", "example-barbican-config-secret")],
            barbican_api_service=bodies[("Service", "example-barbican-api")],
            barbican_api_deployment=bodies[("Deployment", "example-barbican-api")],
            barbican_worker_deployment=bodies[
                ("Deployment", "example-barbican-worker")
            ],
        )
    )

    assert result.credentials == {"example-barbican-credentials": RETAINED_VALUES}
    assert result.classifications == {
        "example-barbican-credentials": RetainedClassification.REUSE,
        "example-barbican-config": OwnedClassification.MANAGED,
        "example-barbican-config-secret": OwnedClassification.MANAGED,
        "example-barbican-api": OwnedClassification.MANAGED,
        "example-barbican-worker": OwnedClassification.MANAGED,
    }
    assert len(result.manifests) == 6


@pytest.mark.parametrize(
    ("managed_field", "managed_kind"),
    [
        ("barbican_api_service", "Service"),
        ("barbican_api_deployment", "Deployment"),
    ],
)
def test_preflight_mixed_api_absent_and_managed_is_not_collision(
    managed_field: str, managed_kind: str
) -> None:
    bodies = bodies_by_identity()
    result, _, _ = absent_preflight(
        **{managed_field: bodies[(managed_kind, "example-barbican-api")]},
    )
    assert result.classifications["example-barbican-api"] is OwnedClassification.ABSENT
    managed = OwnedClassification.MANAGED
    absent = OwnedClassification.ABSENT
    if managed_field == "barbican_api_service":
        assert result.api_service_classification is managed
        assert result.api_deployment_classification is absent
    else:
        assert result.api_service_classification is absent
        assert result.api_deployment_classification is managed
    assert len(result.manifests) == 6


@pytest.mark.parametrize(
    "field",
    [
        "barbican_credentials_secret",
        "barbican_config_map",
        "barbican_config_secret",
        "barbican_api_service",
        "barbican_api_deployment",
        "barbican_worker_deployment",
    ],
)
def test_every_collision_is_all_or_nothing_without_touching_factories(
    field: str,
) -> None:
    expected_names = {
        "barbican_credentials_secret": "example-barbican-credentials",
        "barbican_config_map": "example-barbican-config",
        "barbican_config_secret": "example-barbican-config-secret",
        "barbican_api_service": "example-barbican-api",
        "barbican_api_deployment": "example-barbican-api",
        "barbican_worker_deployment": "example-barbican-worker",
    }
    result = preflight_barbican_resources(
        **preflight_kwargs(**{field: {"metadata": {}}})
    )
    assert result.credentials == {}
    assert result.manifests == ()
    assert result.classifications[expected_names[field]].value == "collision"
    explicit_field = {
        "barbican_api_service": "api_service_classification",
        "barbican_api_deployment": "api_deployment_classification",
    }.get(field)
    if explicit_field is not None:
        assert getattr(result, explicit_field) is OwnedClassification.COLLISION
    assert all(
        value.value != "collision"
        for name, value in result.classifications.items()
        if name != expected_names[field]
    )


def test_owner_forged_retained_secret_is_all_or_nothing_collision() -> None:
    collided = build_barbican_credentials_secret(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        retention=RETENTION,
        values=RETAINED_VALUES,
    )
    collided["metadata"]["ownerReferences"] = [{**OWNER, "controller": True}]
    result = preflight_barbican_resources(
        **preflight_kwargs(barbican_credentials_secret=collided)
    )
    assert result.credentials == {}
    assert result.manifests == ()
    assert (
        result.classifications["example-barbican-credentials"]
        is RetainedClassification.COLLISION
    )


def test_malformed_retained_data_becomes_collision_without_generation() -> None:
    invalid = build_barbican_credentials_secret(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        retention=RETENTION,
        values=RETAINED_VALUES,
    )
    invalid["data"]["barbican_crypto_key"] = "malformed-sentinel!!"
    bodies = bodies_by_identity()
    result = preflight_barbican_resources(
        **preflight_kwargs(
            barbican_credentials_secret=invalid,
            barbican_api_service=bodies[("Service", "example-barbican-api")],
        )
    )
    assert result.credentials == {}
    assert result.manifests == ()
    assert (
        result.classifications["example-barbican-credentials"]
        is RetainedClassification.COLLISION
    )
    assert result.api_service_classification is OwnedClassification.MANAGED
    assert result.api_deployment_classification is OwnedClassification.ABSENT
    assert "malformed-sentinel" not in repr(result)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("barbican_database_password", " leading", id="db-leading-space"),
        pytest.param("barbican_database_password", "trailing ", id="db-trailing-space"),
        pytest.param("barbican_database_password", "#hash", id="db-leading-hash"),
        pytest.param(
            "barbican_keystone_password", ";semi", id="keystone-leading-semicolon"
        ),
        pytest.param("barbican_keystone_password", "trail ", id="keystone-trailing"),
        pytest.param("barbican_keystone_password", "a\nb", id="keystone-control-char"),
        pytest.param(
            "barbican_crypto_key",
            base64.urlsafe_b64encode(b"k" * 16).decode("ascii"),
            id="kek-16-byte-valid-base64",
        ),
        pytest.param(
            "barbican_crypto_key",
            base64.b64encode(b"\xff" * 32).decode("ascii"),
            id="kek-noncanonical-alphabet",
        ),
        pytest.param("barbican_crypto_key", _BUILTIN_DEFAULT_KEK, id="kek-builtin"),
    ],
)
def test_semantically_invalid_retained_values_become_collision_without_generation(
    key: str, value: str
) -> None:
    invalid_values = dict(RETAINED_VALUES)
    invalid_values[key] = value
    invalid = build_barbican_credentials_secret(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        retention=RETENTION,
        values=invalid_values,
    )
    bodies = bodies_by_identity()
    # The default factories raise when called, so a clean return proves
    # neither generation nor rendering happened for the collision.
    result = preflight_barbican_resources(
        **preflight_kwargs(
            barbican_credentials_secret=invalid,
            barbican_api_service=bodies[("Service", "example-barbican-api")],
        )
    )
    assert result.credentials == {}
    assert result.manifests == ()
    assert (
        result.classifications["example-barbican-credentials"]
        is RetainedClassification.COLLISION
    )
    assert result.api_service_classification is OwnedClassification.MANAGED
    assert result.api_deployment_classification is OwnedClassification.ABSENT
    assert value not in repr(result)
    for retained in RETAINED_VALUES.values():
        assert retained not in repr(result)


def test_invalid_external_rabbitmq_inputs_are_not_barbican_collisions() -> None:
    existing = build_barbican_credentials_secret(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        retention=RETENTION,
        values=RETAINED_VALUES,
    )
    with pytest.raises(ValueError) as excinfo:
        preflight_barbican_resources(
            **preflight_kwargs(
                barbican_credentials_secret=existing,
                rabbitmq_password="bad\npass",
            )
        )
    assert str(excinfo.value) == "invalid sensitive Barbican configuration input"
    assert "bad\npass" not in str(excinfo.value)
    with pytest.raises(ValueError) as excinfo:
        preflight_barbican_resources(
            **preflight_kwargs(
                barbican_credentials_secret=existing,
                rabbitmq_host="BAD host",
            )
        )
    assert str(excinfo.value) == "invalid sensitive Barbican configuration input"
    assert "BAD host" not in str(excinfo.value)


def test_preflight_repr_never_carries_credentials_or_manifests() -> None:
    result, _, _ = absent_preflight()
    rendered = repr(result)
    credentials = result.credentials["example-barbican-credentials"]
    for value in credentials.values():
        assert value not in rendered
    assert RABBITMQ_PASSWORD not in rendered
    assert "barbican.conf" not in rendered
    assert "apps/v1" not in rendered
    assert str(credentials) not in rendered
    assert str(result.manifests) not in rendered


def _decoded_data(body: dict) -> str:
    data = body.get("data", {})
    if body["kind"] == "Secret":
        data = {
            key: base64.b64decode(value).decode("utf-8") for key, value in data.items()
        }
    return json.dumps(data)


def test_sensitive_values_are_confined_to_retained_and_config_secrets() -> None:
    result, _, _ = absent_preflight()
    credentials = result.credentials["example-barbican-credentials"]
    rendered_conf = base64.b64decode(
        result.manifests[2]["data"]["barbican.conf"]
    ).decode("utf-8")
    assert credentials["barbican_database_password"] in rendered_conf
    assert credentials["barbican_keystone_password"] in rendered_conf
    assert f"kek = {credentials['barbican_crypto_key']}" in rendered_conf
    assert RABBITMQ_PASSWORD in rendered_conf

    retained_text = _decoded_data(result.manifests[0])
    for value in credentials.values():
        assert value in retained_text
    assert RABBITMQ_PASSWORD not in retained_text
    assert RABBITMQ_PASSWORD not in json.dumps(result.manifests[0])

    for index, body in enumerate(result.manifests):
        if index in (0, 2):
            continue
        serialized = json.dumps(body) + _decoded_data(body)
        for value in credentials.values():
            assert value not in serialized
        assert RABBITMQ_PASSWORD not in serialized


def test_preflight_uses_long_appliance_name_identity() -> None:
    long_name = f"{'a' * 60}.{'b' * 60}.example.org"
    result, _, _ = absent_preflight(appliance_name=long_name)
    names = [body["metadata"]["name"] for body in result.manifests]
    assert len(set(names)) == 5
    for name in names:
        assert len(name) <= 63
        assert name == name.lower()
        assert not name.startswith("-")
        assert not name.endswith("-")
    assert appliance_resource_name(long_name, "barbican-api") in names
    service = next(body for body in result.manifests if body["kind"] == "Service")
    assert service["spec"]["selector"]["coriolis.cloudbase.it/appliance"] == (
        appliance_identity(long_name)
    )
    deployment = next(
        body
        for body in result.manifests
        if body["kind"] == "Deployment"
        and body["metadata"]["labels"].get("coriolis.cloudbase.it/component")
        == "barbican-api"
    )
    assert deployment["spec"]["selector"]["matchLabels"][
        "coriolis.cloudbase.it/appliance"
    ] == appliance_identity(long_name)


def test_preflight_does_not_mutate_existing_resources() -> None:
    existing = build_barbican_credentials_secret(
        appliance_name=APPLIANCE,
        namespace=NAMESPACE,
        accepted_version=VERSION,
        retention=RETENTION,
        values=RETAINED_VALUES,
    )
    before = copy.deepcopy(existing)
    preflight_barbican_resources(
        **preflight_kwargs(barbican_credentials_secret=existing)
    )
    assert existing == before
