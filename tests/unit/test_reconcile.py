import base64
import copy
import hashlib
import re
from datetime import UTC, datetime
from unittest.mock import ANY, MagicMock, call

import kopf
import pytest
from kubernetes import client

from coriolis_operator import main
from coriolis_operator.common import (
    BOOTSTRAP_COMPONENT,
    BOOTSTRAP_SCRIPT_ANNOTATION,
    BOOTSTRAP_TEMPLATE_ANNOTATION,
    CONDUCTOR_IMAGE,
    render_bootstrap_script,
)
from coriolis_operator.main import _bootstrap_job_state, reconcile_appliance
from coriolis_operator.mariadb import (
    SensitiveMariaDBCredentials,
    resolve_mariadb_settings,
)
from coriolis_operator.rabbitmq import resolve_rabbitmq_settings
from coriolis_operator.reconcile import (
    APPLIANCE_NAME_ANNOTATION,
    CORIOLIS_CREDENTIALS_KEYS,
    DEPENDENCY_SERVICES,
    EXTERNAL_READ_ONLY_RESOURCES,
    INFRASTRUCTURE_CREDENTIALS_KEYS,
    MARKER_COLLISION,
    MARKER_LEGACY,
    MARKER_MANAGED,
    RETENTION_ANNOTATION,
    SUPPORTED_INITIAL_VERSION,
    SUPPORTED_PROFILE,
    OwnedClassification,
    RetainedClassification,
    accepted_conditions,
    appliance_identity,
    appliance_resource_name,
    blocked_conditions,
    bootstrap_running_conditions,
    build_api_deployment,
    build_api_service,
    build_common_bootstrap_config_map,
    build_common_bootstrap_job,
    build_conductor_deployment,
    build_coriolis_config_map,
    build_coriolis_config_secret,
    build_coriolis_credentials_secret,
    build_dependency_service,
    build_infrastructure_credentials_secret,
    build_memcached_deployment,
    build_resource_metadata,
    build_state_config_map,
    build_status,
    classify_existing_marker,
    classify_owned_resource,
    classify_retained_resource,
    collision_conditions,
    generate_coriolis_credentials,
    generate_infrastructure_credentials,
    invalid_runtime_configuration_conditions,
    kubernetes_coriolis_render_inputs,
    preflight_foundational_resources,
    preflight_keystone_resources,
    preflight_mariadb_resources,
    preflight_rabbitmq_resources,
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
CORIOLIS_CONFIG = {
    "coriolis-api.wsgi": "wsgi application",
    "wsgi-coriolis.conf": "wsgi config",
    "vixdisklib.conf": "disk config",
    "api-paste.ini": "paste config",
    "policy.yml": "policy config",
    "coriolis.release": "2603.4",
}
CORIOLIS_CONFIG_SECRET = {"coriolis.conf": "secret config"}
MARIADB_STORAGE = {"mariadb": {"storageClassName": "synthetic-storage", "size": "10Gi"}}
MARIADB_RESOURCES = {
    "mariadb": {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    }
}
RABBITMQ_STORAGE = {"rabbitmq": {"storageClassName": "rabbitmq-storage", "size": "1Gi"}}
RABBITMQ_RESOURCES = {
    "rabbitmq": {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    }
}


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


def test_credential_generators_make_six_32_byte_factory_calls() -> None:
    calls: list[int] = []

    def token_factory(bytes_count: int) -> str:
        calls.append(bytes_count)
        return "synthetic"

    generate_coriolis_credentials(token_factory)
    generate_infrastructure_credentials(token_factory)

    assert calls == [32] * 6


@pytest.mark.parametrize(
    ("generator", "builder"),
    [
        (generate_coriolis_credentials, build_coriolis_credentials_secret),
        (generate_infrastructure_credentials, build_infrastructure_credentials_secret),
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


def valid_spec(*, include_profile: bool = True) -> dict:
    spec = {
        "version": "2603.4",
        "storage": copy.deepcopy(MARIADB_STORAGE | RABBITMQ_STORAGE),
        "resources": copy.deepcopy(MARIADB_RESOURCES | RABBITMQ_RESOURCES),
    }
    if include_profile:
        spec["profile"] = "core"
    return spec


def _api_exception(status: int) -> Exception:
    return client.ApiException(status=status)


def make_core_api(existing=None) -> MagicMock:
    api = MagicMock()
    api.api_client.default_headers = {}

    def read_config_map(*, name: str, namespace: str) -> object:
        if name.endswith("-operator-state") and existing is not None:
            return existing
        raise _api_exception(404)

    api.read_namespaced_config_map.side_effect = read_config_map
    api.read_namespaced_secret.side_effect = _api_exception(404)
    api.read_namespaced_service.side_effect = _api_exception(404)
    api.read_namespaced_persistent_volume_claim.side_effect = _api_exception(404)
    return api


def make_apps_api(existing=None) -> MagicMock:
    api = MagicMock()
    api.api_client.default_headers = {}

    def read_stateful_set(*, name: str, namespace: str) -> object:
        if name == "example-mariadb" and existing is not None:
            return existing
        raise _api_exception(404)

    api.read_namespaced_stateful_set.side_effect = read_stateful_set
    api.read_namespaced_deployment.side_effect = _api_exception(404)
    return api


def _bootstrap_script(appliance_name: str = "example") -> str:
    return render_bootstrap_script(
        coriolis_api_host=appliance_resource_name(appliance_name, "coriolis-api"),
        rabbitmq_host=appliance_resource_name(appliance_name, "rabbitmq"),
        memcached_host=appliance_resource_name(appliance_name, "memcached"),
        database_host=appliance_resource_name(appliance_name, "mariadb"),
        keystone_host=appliance_resource_name(appliance_name, "keystone"),
    )


def bootstrap_job_body() -> dict:
    return build_common_bootstrap_job(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
        script=_bootstrap_script(),
    )


_DEFAULT_JOB = object()


def succeeded_bootstrap_job(appliance_name: str = "example") -> dict:
    body = build_common_bootstrap_job(
        appliance_name=appliance_name,
        namespace="operators",
        accepted_version="2603.4",
        owner=dict(OWNER, name=appliance_name, uid="abc-123"),
        script=_bootstrap_script(appliance_name),
    )
    body["metadata"]["resourceVersion"] = "1"
    body["status"] = {"succeeded": 1}
    return body


def _to_v1_volume(volume: dict) -> client.V1Volume:
    name = volume["name"]
    if "emptyDir" in volume:
        return client.V1Volume(name=name, empty_dir=client.V1EmptyDirVolumeSource())
    if "configMap" in volume:
        cm = volume["configMap"]
        items = [
            client.V1KeyToPath(
                key=item["key"], path=item["path"], mode=item.get("mode")
            )
            for item in cm.get("items", [])
        ]
        return client.V1Volume(
            name=name,
            config_map=client.V1ConfigMapVolumeSource(
                name=cm["name"], items=items or None
            ),
        )
    if "secret" in volume:
        secret = volume["secret"]
        items = [
            client.V1KeyToPath(
                key=item["key"], path=item["path"], mode=item.get("mode")
            )
            for item in secret.get("items", [])
        ]
        return client.V1Volume(
            name=name,
            secret=client.V1SecretVolumeSource(
                secret_name=secret["secretName"], items=items or None
            ),
        )
    raise AssertionError(f"unsupported bootstrap volume source: {volume}")


def to_v1_bootstrap_job(body: dict, status: dict | None = None) -> client.V1Job:
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
        resource_version=meta.get("resourceVersion"),
    )
    spec = body["spec"]
    template_meta = spec["template"]["metadata"]
    template_object_meta = client.V1ObjectMeta(
        labels=template_meta.get("labels"),
        annotations=template_meta.get("annotations"),
    )
    pod_dict = spec["template"]["spec"]
    containers = []
    for container in pod_dict["containers"]:
        env = [
            client.V1EnvVar(name=entry["name"], value=entry.get("value"))
            for entry in container.get("env", [])
        ]
        mounts = [
            client.V1VolumeMount(
                name=mount["name"],
                mount_path=mount["mountPath"],
                read_only=mount.get("readOnly"),
            )
            for mount in container.get("volumeMounts", [])
        ]
        container_sc = None
        sc = container.get("securityContext")
        if sc is not None:
            container_sc = client.V1SecurityContext(
                run_as_non_root=sc.get("runAsNonRoot"),
                read_only_root_filesystem=sc.get("readOnlyRootFilesystem"),
                allow_privilege_escalation=sc.get("allowPrivilegeEscalation"),
                capabilities=client.V1Capabilities(
                    drop=sc.get("capabilities", {}).get("drop")
                ),
                seccomp_profile=client.V1SeccompProfile(
                    type=sc.get("seccompProfile", {}).get("type")
                ),
            )
        containers.append(
            client.V1Container(
                name=container["name"],
                image=container["image"],
                command=container.get("command"),
                env=env or None,
                security_context=container_sc,
                volume_mounts=mounts or None,
            )
        )
    pod_security = None
    pod_sc = pod_dict.get("securityContext")
    if pod_sc is not None:
        pod_security = client.V1PodSecurityContext(
            run_as_user=pod_sc.get("runAsUser"),
            run_as_group=pod_sc.get("runAsGroup"),
            fs_group=pod_sc.get("fsGroup"),
            fs_group_change_policy=pod_sc.get("fsGroupChangePolicy"),
        )
    volumes = [_to_v1_volume(volume) for volume in pod_dict.get("volumes", [])]
    image_pull_secrets = [
        client.V1LocalObjectReference(name=entry["name"])
        for entry in pod_dict.get("imagePullSecrets", [])
    ]
    pod_spec = client.V1PodSpec(
        restart_policy=pod_dict.get("restartPolicy"),
        automount_service_account_token=pod_dict.get("automountServiceAccountToken"),
        enable_service_links=pod_dict.get("enableServiceLinks"),
        termination_grace_period_seconds=pod_dict.get("terminationGracePeriodSeconds"),
        security_context=pod_security,
        containers=containers,
        volumes=volumes or None,
        image_pull_secrets=image_pull_secrets or None,
    )
    template = client.V1PodTemplateSpec(metadata=template_object_meta, spec=pod_spec)
    job_spec = client.V1JobSpec(
        backoff_limit=spec["backoffLimit"],
        active_deadline_seconds=spec["activeDeadlineSeconds"],
        completions=spec["completions"],
        parallelism=spec["parallelism"],
        template=template,
    )
    job_status = None
    if status is not None:
        conditions = [
            client.V1JobCondition(type=item.get("type"), status=item.get("status"))
            for item in status.get("conditions", [])
        ]
        job_status = client.V1JobStatus(
            succeeded=status.get("succeeded"),
            failed=status.get("failed"),
            conditions=conditions or None,
        )
    return client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=object_meta,
        spec=job_spec,
        status=job_status,
    )


def make_batch_api(
    job: object = _DEFAULT_JOB, appliance_name: str = "example"
) -> MagicMock:
    api = MagicMock()
    api.api_client.default_headers = {}

    def read_job(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == appliance_resource_name(appliance_name, BOOTSTRAP_COMPONENT):
            if job is _DEFAULT_JOB:
                return succeeded_bootstrap_job(appliance_name=appliance_name)
            return job
        raise _api_exception(404)

    api.read_namespaced_job.side_effect = read_job
    return api


def mariadb_bodies() -> dict[str, dict]:
    preflight = preflight_mariadb_resources(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        settings=resolve_mariadb_settings(
            storage=MARIADB_STORAGE, resources=MARIADB_RESOURCES
        ),
        credentials=SensitiveMariaDBCredentials(
            database_password="database synthetic",
            coriolis_database_password="db synthetic",
            keystone_database_password="keystone synthetic",
        ),
        owner=OWNER,
        mariadb_data_pvc=None,
        mariadb_config_map=None,
        mariadb_config_secret=None,
        mariadb_stateful_set=None,
    )
    return {
        body["metadata"]["name"]: copy.deepcopy(body) for body in preflight.manifests
    }


def rabbitmq_bodies() -> dict[str, dict]:
    preflight = preflight_rabbitmq_resources(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        settings=resolve_rabbitmq_settings(
            storage=RABBITMQ_STORAGE, resources=RABBITMQ_RESOURCES
        ),
        owner=OWNER,
        rabbitmq_data_pvc=None,
        rabbitmq_config_map=None,
        rabbitmq_stateful_set=None,
    )
    return {
        body["metadata"]["name"]: copy.deepcopy(body) for body in preflight.manifests
    }


def keystone_bodies() -> dict[str, dict]:
    preflight = preflight_keystone_resources(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
        retention="state-credentials",
        database_host="example-mariadb",
        keystone_host="example-keystone",
        keystone_admin_password="admin synthetic",
        keystone_database_credentials_secret=None,
        keystone_fernet_keys_secret=None,
        keystone_credential_keys_secret=None,
        keystone_config_map=None,
        keystone_config_secret=None,
        keystone_deployment=None,
        database_token_factory=lambda _: "database synthetic",
        fernet_byte_factory=lambda _: b"f" * 32,
        credential_byte_factory=lambda _: b"c" * 32,
    )
    return {
        body["metadata"]["name"]: copy.deepcopy(body) for body in preflight.manifests
    }


def test_keystone_prepare_init_does_not_chmod_mount_roots_and_keeps_0600_files() -> (
    None
):
    deployment = keystone_bodies()["example-keystone"]
    prepare = next(
        container
        for container in deployment["spec"]["template"]["spec"]["initContainers"]
        if container["name"] == "prepare"
    )
    script = "".join(prepare["args"])

    assert "install -d" not in script

    expected = [
        "/etc/keystone/runtime/bootstrap.py",
        "/etc/keystone/runtime/keystone.conf",
        "/etc/keystone/runtime/auth-request.json",
        "/etc/keystone/runtime/admin-password",
        "/etc/keystone/fernet-keys/0",
        "/etc/keystone/fernet-keys/1",
        "/etc/keystone/credential-keys/0",
        "/etc/keystone/credential-keys/1",
    ]
    assert script.count("install -m 0600") == 8
    for path in expected:
        assert path in script

    security_context = prepare["securityContext"]
    assert security_context["runAsNonRoot"] is True
    assert security_context["readOnlyRootFilesystem"] is True
    assert security_context["allowPrivilegeEscalation"] is False
    assert security_context["capabilities"] == {"drop": ["ALL"]}


def configure_mariadb_existing(
    core_api: MagicMock, apps_api: MagicMock, existing: dict[str, dict]
) -> None:
    def read_config_map(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name in existing:
            return existing[name]
        raise _api_exception(404)

    def read_secret(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name in existing:
            return existing[name]
        raise _api_exception(404)

    core_api.read_namespaced_config_map.side_effect = read_config_map
    core_api.read_namespaced_secret.side_effect = read_secret

    def read_persistent_volume_claim(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name in existing:
            return existing[name]
        raise _api_exception(404)

    def read_stateful_set(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name in existing:
            return existing[name]
        raise _api_exception(404)

    core_api.read_namespaced_persistent_volume_claim.side_effect = (
        read_persistent_volume_claim
    )
    apps_api.read_namespaced_stateful_set.side_effect = read_stateful_set


def configure_keystone_existing(
    core_api: MagicMock, apps_api: MagicMock, existing: dict[str, dict]
) -> None:
    def read_config_map(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name in existing:
            return existing[name]
        raise _api_exception(404)

    def read_secret(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name in existing:
            return existing[name]
        raise _api_exception(404)

    def read_deployment(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name in existing:
            return existing[name]
        raise _api_exception(404)

    core_api.read_namespaced_config_map.side_effect = read_config_map
    core_api.read_namespaced_secret.side_effect = read_secret
    apps_api.read_namespaced_deployment.side_effect = read_deployment


def api_writes(api: MagicMock) -> list:
    return [
        item
        for item in api.method_calls
        if item[0].startswith(("create_namespaced", "patch_namespaced", "delete"))
    ]


@pytest.fixture(autouse=True)
def stub_apps_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.client, "AppsV1Api", make_apps_api)
    monkeypatch.setattr(main.client, "BatchV1Api", make_batch_api)


def desired_body(meta=None) -> dict:
    meta = meta or sample_meta()
    owner = {
        "apiVersion": OWNER["apiVersion"],
        "kind": OWNER["kind"],
        "name": meta["name"],
        "uid": meta["uid"],
    }
    body = build_state_config_map(
        name=meta["name"],
        namespace=meta["namespace"],
        profile=SUPPORTED_PROFILE,
        accepted_version=SUPPORTED_INITIAL_VERSION,
        generation=meta["generation"],
        owner=owner,
    )
    body["metadata"]["resourceVersion"] = "1"
    return body


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
            "resourceVersion": "1",
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
            resource_version=meta.get("resourceVersion"),
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
        raise AssertionError("Kubernetes API client must not be instantiated")

    monkeypatch.setattr(main.client, "CoreV1Api", fail)
    monkeypatch.setattr(main.client, "AppsV1Api", fail)


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


@pytest.mark.parametrize(
    "appliance_name",
    ["example", "example.domain", f"{'a' * 40}.{'b' * 40}.{'c' * 40}.{'d' * 40}"],
)
def test_kubernetes_render_inputs_are_fixed_and_derive_component_names(
    appliance_name: str,
) -> None:
    inputs = kubernetes_coriolis_render_inputs(appliance_name)

    assert inputs.bind_address == "0.0.0.0"
    assert inputs.coriolis_port == 7667
    assert inputs.coriolis_config_dir == "/etc/coriolis"
    assert inputs.coriolis_vmware_vix_disklib_log_dir == "/var/log/coriolis/vmware-root"
    assert inputs.endpoints.rabbitmq_host == appliance_resource_name(
        appliance_name, "rabbitmq"
    )
    assert inputs.endpoints.memcached_host == appliance_resource_name(
        appliance_name, "memcached"
    )
    assert inputs.endpoints.database_host == appliance_resource_name(
        appliance_name, "mariadb"
    )
    assert inputs.endpoints.keystone_host == appliance_resource_name(
        appliance_name, "keystone"
    )


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


def test_dependency_service_definition_and_builders_have_exact_contract() -> None:
    assert DEPENDENCY_SERVICES == (
        ("rabbitmq", 5672),
        ("memcached", 11211),
        ("mariadb", 3306),
        ("keystone", 5000),
    )

    for component, port in DEPENDENCY_SERVICES:
        body = build_dependency_service(
            appliance_name="example.domain",
            namespace="operators",
            accepted_version="2603.4",
            owner=OWNER,
            component=component,
        )

        assert body["apiVersion"] == "v1"
        assert body["kind"] == "Service"
        assert body["metadata"]["name"] == appliance_resource_name(
            "example.domain", component
        )
        assert body["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
        assert body["spec"] == {
            "type": "ClusterIP",
            "selector": {
                "coriolis.cloudbase.it/appliance": appliance_identity("example.domain"),
                "coriolis.cloudbase.it/component": component,
            },
            "ports": [
                {
                    "name": component,
                    "protocol": "TCP",
                    "port": port,
                    "targetPort": port,
                }
            ],
        }


def test_dependency_service_builder_rejects_unsupported_component() -> None:
    with pytest.raises(ValueError, match="^unsupported dependency service component$"):
        build_dependency_service(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            owner=OWNER,
            component="barbican",
        )


@pytest.mark.parametrize(
    ("builder", "values", "retained"),
    [
        (build_coriolis_credentials_secret, CORIOLIS_CREDENTIALS, True),
        (build_infrastructure_credentials_secret, INFRASTRUCTURE_CREDENTIALS, True),
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


@pytest.mark.parametrize(
    ("builder", "values", "retained"),
    [
        (build_coriolis_credentials_secret, CORIOLIS_CREDENTIALS, True),
        (build_infrastructure_credentials_secret, INFRASTRUCTURE_CREDENTIALS, True),
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
                "The foundational appliance resources, dependency Services, MariaDB, "
                "RabbitMQ, Memcached, Keystone, Coriolis-common bootstrap, Coriolis "
                "API, Coriolis conductor, and controller state marker were reconciled "
                "in Kubernetes; runtime readiness is not implemented yet.",
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
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
    )

    assert core_api.method_calls == [
        call.read_namespaced_config_map(
            name="example-operator-state", namespace="operators"
        ),
        call.read_namespaced_secret(
            name="example-coriolis-credentials", namespace="operators"
        ),
        call.read_namespaced_secret(
            name="example-infrastructure-credentials", namespace="operators"
        ),
        call.read_namespaced_config_map(
            name="example-coriolis-config", namespace="operators"
        ),
        call.read_namespaced_secret(
            name="example-coriolis-config-secret", namespace="operators"
        ),
        call.read_namespaced_service(name="example-rabbitmq", namespace="operators"),
        call.read_namespaced_service(name="example-memcached", namespace="operators"),
        call.read_namespaced_service(name="example-mariadb", namespace="operators"),
        call.read_namespaced_service(name="example-keystone", namespace="operators"),
        call.read_namespaced_service(
            name="example-coriolis-api", namespace="operators"
        ),
        call.read_namespaced_persistent_volume_claim(
            name="example-mariadb-data", namespace="operators"
        ),
        call.read_namespaced_config_map(
            name="example-mariadb-config", namespace="operators"
        ),
        call.read_namespaced_secret(
            name="example-mariadb-config-secret", namespace="operators"
        ),
        call.read_namespaced_persistent_volume_claim(
            name="example-rabbitmq-data", namespace="operators"
        ),
        call.read_namespaced_config_map(
            name="example-rabbitmq-config", namespace="operators"
        ),
        call.read_namespaced_secret(
            name="example-keystone-database-credentials", namespace="operators"
        ),
        call.read_namespaced_secret(
            name="example-keystone-fernet-keys", namespace="operators"
        ),
        call.read_namespaced_secret(
            name="example-keystone-credential-keys", namespace="operators"
        ),
        call.read_namespaced_config_map(
            name="example-keystone-config", namespace="operators"
        ),
        call.read_namespaced_secret(
            name="example-keystone-config-secret", namespace="operators"
        ),
        call.read_namespaced_config_map(
            name="example-common-bootstrap-v2", namespace="operators"
        ),
        call.create_namespaced_secret(namespace="operators", body=ANY),
        call.create_namespaced_secret(namespace="operators", body=ANY),
        call.create_namespaced_config_map(namespace="operators", body=ANY),
        call.create_namespaced_secret(namespace="operators", body=ANY),
        call.create_namespaced_service(namespace="operators", body=ANY),
        call.create_namespaced_service(namespace="operators", body=ANY),
        call.create_namespaced_service(namespace="operators", body=ANY),
        call.create_namespaced_service(namespace="operators", body=ANY),
        call.create_namespaced_secret(namespace="operators", body=ANY),
        call.create_namespaced_secret(namespace="operators", body=ANY),
        call.create_namespaced_secret(namespace="operators", body=ANY),
        call.create_namespaced_persistent_volume_claim(namespace="operators", body=ANY),
        call.create_namespaced_config_map(namespace="operators", body=ANY),
        call.create_namespaced_secret(namespace="operators", body=ANY),
        call.create_namespaced_persistent_volume_claim(namespace="operators", body=ANY),
        call.create_namespaced_config_map(namespace="operators", body=ANY),
        call.create_namespaced_config_map(namespace="operators", body=ANY),
        call.create_namespaced_secret(namespace="operators", body=ANY),
        call.create_namespaced_config_map(namespace="operators", body=ANY),
        call.create_namespaced_service(namespace="operators", body=ANY),
        call.create_namespaced_config_map(namespace="operators", body=ANY),
    ]
    assert status["observedGeneration"] == 7
    assert status["acceptedVersion"] == "2603.4"
    created_credentials = [
        call.kwargs["body"]
        for call in core_api.create_namespaced_secret.call_args_list
        if call.kwargs["body"]["metadata"]["name"]
        in {"example-coriolis-credentials", "example-infrastructure-credentials"}
    ]
    assert len(created_credentials) == 2
    assert all(
        body["metadata"]["annotations"][RETENTION_ANNOTATION] == "state-credentials"
        for body in created_credentials
    )
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
        spec=valid_spec(include_profile=False),
        meta=sample_meta(),
        core_api=core_api,
    )

    body = next(
        item.kwargs["body"]
        for item in core_api.create_namespaced_config_map.call_args_list
        if item.kwargs["body"]["metadata"]["name"] == "example-operator-state"
    )
    assert body["data"]["profile"] == SUPPORTED_PROFILE
    assert status["acceptedVersion"] == "2603.4"


def test_reconcile_treats_empty_accepted_version_as_absent() -> None:
    core_api = make_core_api()

    status = reconcile_appliance(
        spec=valid_spec(include_profile=False),
        meta=sample_meta(),
        status={"acceptedVersion": "", "conditions": []},
        core_api=core_api,
    )

    core_api.create_namespaced_config_map.assert_called()
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
        spec=valid_spec(),
        meta=sample_meta(generation=8),
        status={
            "acceptedVersion": "2603.4",
            "observedGeneration": 7,
            "conditions": [],
        },
        core_api=core_api,
    )

    core_api.create_namespaced_config_map.assert_called()
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
        valid_spec(),
        {"name": "example"},
        patch,
        {"conditions": []},
    )

    assert result is None
    reconcile.assert_called_once_with(
        spec=valid_spec(),
        meta={"name": "example"},
        status={"conditions": []},
    )
    patch.status.update.assert_called_once_with({"observedGeneration": 7})


def test_handler_does_not_patch_unchanged_computed_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = {"observedGeneration": 7, "conditions": [], "external": "preserved"}
    monkeypatch.setattr(
        main, "reconcile_appliance", MagicMock(return_value=dict(status))
    )
    patch = MagicMock()

    main._handle_reconcile(valid_spec(), {"name": "example"}, patch, status)

    patch.status.update.assert_not_called()


def test_handler_does_not_patch_unchanged_retry_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = {"observedGeneration": 7, "conditions": [], "external": "preserved"}
    monkeypatch.setattr(
        main,
        "reconcile_appliance",
        MagicMock(side_effect=main.ReconcileRetry(dict(status))),
    )
    patch = MagicMock()

    with pytest.raises(kopf.TemporaryError):
        main._handle_reconcile(valid_spec(), {"name": "example"}, patch, status)

    patch.status.update.assert_not_called()


def test_resource_collision_predicate_requires_exact_reconciled_condition() -> None:
    assert main._has_resource_collision(
        {
            "conditions": [
                {
                    "type": "Reconciled",
                    "status": "False",
                    "reason": "ResourceCollision",
                }
            ]
        }
    )


@pytest.mark.parametrize(
    "status",
    [
        None,
        {},
        {"conditions": "invalid"},
        {"conditions": [{}]},
        {
            "conditions": [
                {"type": "Reconciled", "status": "True", "reason": "ResourceCollision"}
            ]
        },
        {"conditions": [{"type": "Reconciled", "status": "False", "reason": "Other"}]},
    ],
)
def test_resource_collision_predicate_rejects_malformed_or_noncollision_status(
    status: object,
) -> None:
    candidate = status if isinstance(status, dict) else None
    assert not main._has_resource_collision(candidate)


def test_collision_timer_noops_without_stable_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled = MagicMock()
    monkeypatch.setattr(main, "_handle_reconcile", handled)

    main.retry_resource_collision(
        valid_spec(), {"name": "example"}, MagicMock(), {"conditions": []}
    )

    handled.assert_not_called()


def test_collision_timer_routes_stable_collision_through_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled = MagicMock()
    status = {
        "conditions": [
            {"type": "Reconciled", "status": "False", "reason": "ResourceCollision"}
        ]
    }
    monkeypatch.setattr(main, "_handle_reconcile", handled)
    patch = MagicMock()

    main.retry_resource_collision(valid_spec(), {"name": "example"}, patch, status)

    handled.assert_called_once_with(valid_spec(), {"name": "example"}, patch, status)


def test_collision_timer_avoids_churn_then_publishes_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collision_status = {
        "observedGeneration": 7,
        "conditions": [
            {"type": "Reconciled", "status": "False", "reason": "ResourceCollision"}
        ],
    }
    recovered_status = {
        "observedGeneration": 7,
        "acceptedVersion": "2603.4",
        "conditions": [
            {"type": "Reconciled", "status": "True", "reason": "Reconciled"}
        ],
    }
    reconcile = MagicMock(side_effect=[collision_status, recovered_status])
    monkeypatch.setattr(main, "reconcile_appliance", reconcile)

    unchanged_patch = MagicMock()
    main.retry_resource_collision(
        valid_spec(), {"name": "example"}, unchanged_patch, collision_status
    )
    unchanged_patch.status.update.assert_not_called()

    recovered_patch = MagicMock()
    main.retry_resource_collision(
        valid_spec(), {"name": "example"}, recovered_patch, collision_status
    )
    recovered_patch.status.update.assert_called_once_with(
        {
            "acceptedVersion": "2603.4",
            "conditions": recovered_status["conditions"],
        }
    )
    assert reconcile.call_count == 2


def test_collision_timer_continues_kopf_retry_after_bootstrap_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collision_status = {
        "observedGeneration": 7,
        "conditions": [
            {"type": "Reconciled", "status": "False", "reason": "ResourceCollision"}
        ],
    }
    bootstrap_running_status = build_status(
        7,
        accepted_version=None,
        conditions=bootstrap_running_conditions(),
        prior_conditions=collision_status["conditions"],
    )
    accepted_status = build_status(
        7,
        accepted_version="2603.4",
        conditions=accepted_conditions(),
        prior_conditions=bootstrap_running_status["conditions"],
    )
    reconcile = MagicMock(
        side_effect=[main.ReconcileRetry(bootstrap_running_status), accepted_status]
    )
    monkeypatch.setattr(main, "reconcile_appliance", reconcile)

    running_patch = MagicMock()
    with pytest.raises(kopf.TemporaryError):
        main.retry_resource_collision(
            valid_spec(), {"name": "example"}, running_patch, collision_status
        )
    running_patch.status.update.assert_called_once_with(
        {"conditions": bootstrap_running_status["conditions"]}
    )

    accepted_patch = MagicMock()
    main.retry_resource_collision(
        valid_spec(),
        {"name": "example"},
        accepted_patch,
        bootstrap_running_status,
        retry=1,
    )

    accepted_patch.status.update.assert_called_once_with(
        {
            "acceptedVersion": "2603.4",
            "conditions": accepted_status["conditions"],
        }
    )
    assert reconcile.call_count == 2


def test_profile_field_handler_routes_through_reconcile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled = MagicMock()
    monkeypatch.setattr(main, "_handle_reconcile", handled)
    patch = MagicMock()

    result = main.update_appliance_profile(
        spec=valid_spec(),
        meta={"name": "example"},
        patch=patch,
        status={"conditions": []},
    )

    assert result is None
    handled.assert_called_once_with(
        valid_spec(),
        {"name": "example"},
        patch,
        {"conditions": []},
    )


def test_handler_patches_sanitized_status_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_api = make_core_api()
    core_api.create_namespaced_config_map.side_effect = RuntimeError("API failed")
    monkeypatch.setattr(main.client, "CoreV1Api", MagicMock(return_value=core_api))
    patch = MagicMock()

    with pytest.raises(kopf.TemporaryError):
        main._handle_reconcile(
            valid_spec(),
            {
                "name": "example",
                "namespace": "operators",
                "generation": 7,
                "uid": "abc-123",
            },
            patch,
        )

    reconciled = patch.status.update.call_args.args[0]
    assert reconciled["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "API failed" not in repr(reconciled)


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
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
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
        spec=valid_spec(),
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
    batch_api = make_batch_api(appliance_name=appliance_name)

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=meta,
        core_api=core_api,
        batch_api=batch_api,
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
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
    )

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
    assert statuses["Ready"]["reason"] == "RuntimeNotImplemented"
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
        spec=valid_spec(),
        meta=sample_meta(generation=8),
        status={"acceptedVersion": "2603.4", "conditions": prior_conditions},
        core_api=core_api,
    )

    core_api.patch_namespaced_config_map.assert_not_called()
    assert status["acceptedVersion"] == "2603.4"
    degraded = next(c for c in status["conditions"] if c["type"] == "Degraded")
    assert degraded["status"] == "True"
    assert degraded["lastTransitionTime"] == "2026-08-19T09:00:00Z"


def test_reconcile_non_404_read_error_requests_sanitized_retry() -> None:
    core_api = make_core_api()
    core_api.read_namespaced_config_map.side_effect = client.ApiException(status=403)

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceReadFailed"
    core_api.patch_namespaced_config_map.assert_not_called()


def test_reconcile_generic_read_error_requests_sanitized_retry() -> None:
    core_api = make_core_api()
    core_api.read_namespaced_config_map.side_effect = RuntimeError("read failed")

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceReadFailed"
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
        "The existing resource 'operators/example-operator-state' conflicts with "
        "operator-managed identity and was not modified."
    )
    assert conditions[1] == ("Progressing", "False", "ResourceCollision", message)
    assert conditions[2] == ("Reconciled", "False", "ResourceCollision", message)
    assert conditions[3] == (
        "Ready",
        "False",
        "RuntimeNotImplemented",
        "The appliance runtime is not implemented yet.",
    )
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
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
    )

    core_api.patch_namespaced_config_map.assert_called_once()
    assert status["acceptedVersion"] == "2603.4"


def test_reconcile_compatible_legacy_v1_config_map_normalizes_generation() -> None:
    core_api = make_core_api(existing=to_v1_legacy_marker(generation="1"))

    status = reconcile_appliance(
        spec=valid_spec(),
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
        spec=valid_spec(),
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


def owned_body(component: str = "coriolis-config") -> dict:
    return {
        "metadata": build_resource_metadata(
            resource_name=appliance_resource_name("example", component),
            namespace="operators",
            appliance_name="example",
            component=component,
            accepted_version=SUPPORTED_INITIAL_VERSION,
            owner=OWNER,
        )
    }


def classify_owned(
    existing: object, component: str = "coriolis-config"
) -> OwnedClassification:
    return classify_owned_resource(
        existing=existing,
        resource_name=appliance_resource_name("example", component),
        namespace="operators",
        appliance_name="example",
        component=component,
        accepted_version=SUPPORTED_INITIAL_VERSION,
        owner=OWNER,
    )


def test_owned_classifier_accepts_exact_mapping_and_model_with_content_drift() -> None:
    body = owned_body()
    body["data"] = {"unexpected": "drift"}
    body["type"] = "not-opaque"
    body["metadata"]["labels"]["example.com/extra"] = "x"
    body["metadata"]["annotations"]["example.com/extra"] = "y"
    model = to_v1_config_map(body)
    secret_model = client.V1Secret(metadata=model.metadata, type="not-opaque")

    assert classify_owned(None) is OwnedClassification.ABSENT
    assert classify_owned(body) is OwnedClassification.MANAGED
    assert classify_owned(model) is OwnedClassification.MANAGED
    assert classify_owned(secret_model) is OwnedClassification.MANAGED


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body["metadata"].update(name="other"),
        lambda body: body["metadata"].update(namespace="other"),
        lambda body: body["metadata"]["labels"].pop("app.kubernetes.io/managed-by"),
        lambda body: body["metadata"]["labels"].update(
            {"coriolis.cloudbase.it/component": "other"}
        ),
        lambda body: body["metadata"]["annotations"].pop(APPLIANCE_NAME_ANNOTATION),
        lambda body: body["metadata"]["annotations"].update(
            {APPLIANCE_NAME_ANNOTATION: "other"}
        ),
        lambda body: body["metadata"]["annotations"].update(
            {RETENTION_ANNOTATION: "retain"}
        ),
        lambda body: body["metadata"].pop("ownerReferences"),
        lambda body: body["metadata"]["ownerReferences"][0].update(controller=False),
        lambda body: body["metadata"]["ownerReferences"][0].update(name="other"),
        lambda body: body["metadata"]["ownerReferences"][0].update(uid="other"),
    ],
    ids=(
        "wrong-name",
        "wrong-namespace",
        "missing-label",
        "conflicting-label",
        "missing-annotation",
        "conflicting-annotation",
        "retention",
        "missing-owner",
        "controller-false",
        "changed-owner-name",
        "changed-owner-uid",
    ),
)
def test_owned_classifier_rejects_identity_collisions(mutate) -> None:
    body = owned_body()
    mutate(body)
    assert classify_owned(body) is OwnedClassification.COLLISION


def test_owned_classifier_does_not_mutate_input() -> None:
    body = owned_body()
    before = copy.deepcopy(body)
    classify_owned(body)
    assert body == before


def foundational_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "appliance_name": "example",
        "namespace": "operators",
        "accepted_version": SUPPORTED_INITIAL_VERSION,
        "retention": "retain",
        "owner": OWNER,
        "coriolis_credentials_secret": None,
        "infrastructure_credentials_secret": None,
        "coriolis_config_map": None,
        "coriolis_config_secret": None,
    }
    kwargs.update(overrides)
    return kwargs


def test_foundational_preflight_absent_generates_only_retained_credentials() -> None:
    calls: list[str] = []

    def factory(_: int) -> str:
        calls.append("token")
        return f"generated-{len(calls)}"

    result = preflight_foundational_resources(
        **foundational_kwargs(
            coriolis_token_factory=factory,
            infrastructure_token_factory=factory,
        )
    )

    assert set(result.credentials) == {
        "example-coriolis-credentials",
        "example-infrastructure-credentials",
    }
    assert calls == ["token"] * 6
    assert "generated-1" not in repr(result)
    assert (
        result.classifications["example-coriolis-config"] is OwnedClassification.ABSENT
    )
    assert (
        result.classifications["example-coriolis-config-secret"]
        is OwnedClassification.ABSENT
    )


def test_foundational_preflight_reuses_retained_models_without_factories() -> None:
    coriolis_secret = build_coriolis_credentials_secret(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        retention="retain",
        values=CORIOLIS_CREDENTIALS,
    )
    retained_secrets = {
        "coriolis_credentials_secret": client.V1Secret(
            metadata=to_v1_retained(coriolis_secret).metadata,
            type="Opaque",
            data=coriolis_secret["data"],
        ),
        "infrastructure_credentials_secret": build_infrastructure_credentials_secret(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            retention="retain",
            values=INFRASTRUCTURE_CREDENTIALS,
        ),
    }
    result = preflight_foundational_resources(
        **foundational_kwargs(
            **retained_secrets,
            coriolis_config_map=owned_body(),
            coriolis_config_secret=owned_body("coriolis-config-secret"),
            coriolis_token_factory=lambda _: pytest.fail("factory called"),
            infrastructure_token_factory=lambda _: pytest.fail("factory called"),
        )
    )

    assert result.credentials["example-coriolis-credentials"] == CORIOLIS_CREDENTIALS
    assert (
        result.credentials["example-infrastructure-credentials"]
        == INFRASTRUCTURE_CREDENTIALS
    )
    assert all(value.value != "collision" for value in result.classifications.values())


def test_foundational_preflight_omits_credentials_after_semantic_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = build_coriolis_credentials_secret(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        retention="retain",
        values=CORIOLIS_CREDENTIALS,
    )
    invalid["data"]["coriolis_database_password"] = "encoded-secret-sentinel"
    validated = MagicMock(wraps=validated_retained_secret_values)
    monkeypatch.setattr(
        "coriolis_operator.reconcile.validated_retained_secret_values", validated
    )

    result = preflight_foundational_resources(
        **foundational_kwargs(
            coriolis_credentials_secret=invalid,
            infrastructure_token_factory=lambda _: pytest.fail("factory called"),
            coriolis_token_factory=lambda _: pytest.fail("factory called"),
        )
    )

    assert validated.call_count == 1
    assert result.credentials == {}
    assert (
        result.classifications["example-coriolis-credentials"]
        is RetainedClassification.COLLISION
    )
    assert "encoded-secret-sentinel" not in repr(result)
    assert "db synthetic" not in repr(result)


def test_foundational_preflight_generates_only_absent_retained_resource() -> None:
    reused = build_coriolis_credentials_secret(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        retention="retain",
        values=CORIOLIS_CREDENTIALS,
    )
    calls: list[int] = []

    result = preflight_foundational_resources(
        **foundational_kwargs(
            coriolis_credentials_secret=reused,
            infrastructure_token_factory=lambda size: calls.append(size) or "generated",
        )
    )

    assert calls == [32] * 3
    assert result.credentials["example-coriolis-credentials"] == CORIOLIS_CREDENTIALS
    assert result.credentials["example-infrastructure-credentials"] == {
        key: "generated" for key in INFRASTRUCTURE_CREDENTIALS
    }


@pytest.mark.parametrize(
    ("collision_field", "resource_name"),
    [
        ("coriolis_credentials_secret", "example-coriolis-credentials"),
        ("infrastructure_credentials_secret", "example-infrastructure-credentials"),
        ("coriolis_config_map", "example-coriolis-config"),
        ("coriolis_config_secret", "example-coriolis-config-secret"),
    ],
)
def test_foundational_metadata_collision_skips_semantics_and_generation(
    monkeypatch: pytest.MonkeyPatch,
    collision_field: str,
    resource_name: str,
) -> None:
    validated = MagicMock()
    monkeypatch.setattr(
        "coriolis_operator.reconcile.validated_retained_secret_values", validated
    )
    existing: dict[str, object] = {
        "coriolis_credentials_secret": build_coriolis_credentials_secret(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            retention="retain",
            values=CORIOLIS_CREDENTIALS,
        ),
        "infrastructure_credentials_secret": build_infrastructure_credentials_secret(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            retention="retain",
            values=INFRASTRUCTURE_CREDENTIALS,
        ),
    }
    existing[collision_field] = {"metadata": {}}
    result = preflight_foundational_resources(
        **foundational_kwargs(
            **existing,
            coriolis_token_factory=lambda _: pytest.fail("factory called"),
            infrastructure_token_factory=lambda _: pytest.fail("factory called"),
        )
    )

    validated.assert_not_called()
    assert result.credentials == {}
    assert result.classifications[resource_name].value == "collision"


def test_foundational_preflight_does_not_mutate_existing_resources() -> None:
    existing = build_coriolis_credentials_secret(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        retention="retain",
        values=CORIOLIS_CREDENTIALS,
    )
    before = copy.deepcopy(existing)

    preflight_foundational_resources(
        **foundational_kwargs(coriolis_credentials_secret=existing)
    )

    assert existing == before


def test_foundational_gate_reuses_retained_secrets_without_writing_them() -> None:
    api = make_core_api()
    api.read_namespaced_secret.side_effect = [
        build_coriolis_credentials_secret(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            retention="state-credentials",
            values=CORIOLIS_CREDENTIALS,
        ),
        build_infrastructure_credentials_secret(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            retention="state-credentials",
            values=INFRASTRUCTURE_CREDENTIALS,
        ),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
    ]

    reconcile_appliance(spec=valid_spec(), meta=sample_meta(), core_api=api)

    created_secret_names = [
        call.kwargs["body"]["metadata"]["name"]
        for call in api.create_namespaced_secret.call_args_list
    ]
    patched_secret_names = [
        call.kwargs["name"] for call in api.patch_namespaced_secret.call_args_list
    ]
    assert created_secret_names == [
        "example-coriolis-config-secret",
        "example-keystone-database-credentials",
        "example-keystone-fernet-keys",
        "example-keystone-credential-keys",
        "example-mariadb-config-secret",
        "example-keystone-config-secret",
    ]
    assert patched_secret_names == []
    config_secret = next(
        item.kwargs["body"]
        for item in api.create_namespaced_secret.call_args_list
        if item.kwargs["body"]["metadata"]["name"] == "example-coriolis-config-secret"
    )
    rendered = base64.b64decode(config_secret["data"]["coriolis.conf"]).decode()
    assert CORIOLIS_CREDENTIALS["coriolis_database_password"] in rendered
    assert INFRASTRUCTURE_CREDENTIALS["rabbitmq_password"] in rendered


def test_foundational_gate_collision_has_no_writes_after_complete_reads() -> None:
    api = make_core_api()
    collided = build_coriolis_credentials_secret(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        retention="state-credentials",
        values=CORIOLIS_CREDENTIALS,
    )
    collided["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "other"
    api.read_namespaced_secret.side_effect = [
        collided,
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
    ]

    status = reconcile_appliance(spec=valid_spec(), meta=sample_meta(), core_api=api)

    assert api.read_namespaced_secret.call_count == 8
    assert api.create_namespaced_secret.call_count == 0
    assert api.create_namespaced_config_map.call_count == 0
    assert {item["reason"] for item in status["conditions"]} >= {"ResourceCollision"}


@pytest.mark.parametrize("status_code", [404, 409])
def test_create_absence_and_already_exists_have_distinct_outcomes(
    status_code: int,
) -> None:
    api = make_core_api()
    if status_code == 409:
        api.create_namespaced_secret.side_effect = client.ApiException(
            status=409, reason="credential-value-must-not-leak"
        )

    if status_code == 404:
        status = reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )
        assert status["acceptedVersion"] == "2603.4"
    else:
        with pytest.raises(main.ReconcileRetry) as excinfo:
            reconcile_appliance(
                spec=valid_spec(),
                meta=sample_meta(),
                core_api=api,
            )
        assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
        assert "credential-value-must-not-leak" not in repr(excinfo.value.status)


def test_managed_resources_use_resource_version_and_marker_is_last() -> None:
    api = make_core_api(existing=desired_body())
    managed_config = owned_body()
    managed_config["metadata"]["resourceVersion"] = "17"
    api.read_namespaced_config_map.side_effect = [
        desired_body(),
        managed_config,
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
    ]
    api.read_namespaced_secret.side_effect = [_api_exception(404)] * 8

    reconcile_appliance(spec=valid_spec(), meta=sample_meta(), core_api=api)

    config_apply = api.patch_namespaced_config_map.call_args_list[0].kwargs
    marker_apply = api.patch_namespaced_config_map.call_args_list[1].kwargs
    assert config_apply["body"]["metadata"]["resourceVersion"] == "17"
    assert config_apply["field_manager"] == "coriolis-operator"
    assert config_apply["force"] is True
    assert marker_apply["name"] == "example-operator-state"
    assert api.method_calls[-1] == call.patch_namespaced_config_map(**marker_apply)


def test_apply_conflict_keeps_prior_writes_and_preserves_accepted_version() -> None:
    api = make_core_api(existing=desired_body())
    api.read_namespaced_secret.side_effect = [_api_exception(404)] * 8
    api.patch_namespaced_config_map.side_effect = client.ApiException(
        status=409, reason="rendered-secret-must-not-leak"
    )

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            status={"acceptedVersion": "2603.4", "conditions": []},
            core_api=api,
        )

    assert api.create_namespaced_secret.call_count == 8
    assert excinfo.value.status["acceptedVersion"] == "2603.4"
    assert excinfo.value.status["conditions"][2]["reason"] == "MarkerApplyFailed"
    assert "rendered-secret-must-not-leak" not in repr(excinfo.value.status)


def test_initial_retry_does_not_establish_accepted_version() -> None:
    api = make_core_api()
    api.read_namespaced_secret.side_effect = client.ApiException(
        status=500, reason="token"
    )

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )

    assert "acceptedVersion" not in excinfo.value.status


def test_existing_managed_resource_without_resource_version_retries_before_write() -> (
    None
):
    api = make_core_api(existing=desired_body())
    api.read_namespaced_secret.side_effect = [_api_exception(404)] * 8
    marker = desired_body()
    marker["metadata"].pop("resourceVersion")
    api.read_namespaced_config_map.side_effect = [
        marker,
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
    ]

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "MarkerApplyFailed"
    api.create_namespaced_secret.assert_not_called()
    api.create_namespaced_config_map.assert_not_called()
    api.patch_namespaced_config_map.assert_not_called()


def test_apply_header_is_removed_before_later_create() -> None:
    api = make_core_api()
    managed_config = owned_body()
    managed_config["metadata"]["resourceVersion"] = "17"
    api.read_namespaced_config_map.side_effect = [
        _api_exception(404),
        managed_config,
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
    ]
    api.read_namespaced_secret.side_effect = [_api_exception(404)] * 8

    def create_secret(*, namespace: str, body: dict) -> None:
        if body["metadata"]["name"] == "example-coriolis-config-secret":
            assert "Content-Type" not in api.api_client.default_headers

    api.create_namespaced_secret.side_effect = create_secret

    reconcile_appliance(spec=valid_spec(), meta=sample_meta(), core_api=api)

    assert "Content-Type" not in api.api_client.default_headers


def test_apply_header_is_restored_after_patch_failure() -> None:
    api = make_core_api(existing=desired_body())
    api.read_namespaced_secret.side_effect = [_api_exception(404)] * 8
    api.patch_namespaced_config_map.side_effect = client.ApiException(status=409)
    api.api_client.default_headers["Content-Type"] = "application/json"

    with pytest.raises(main.ReconcileRetry):
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )

    assert api.api_client.default_headers["Content-Type"] == "application/json"


def test_retry_status_has_the_frozen_condition_contract() -> None:
    api = make_core_api()
    api.read_namespaced_secret.side_effect = client.ApiException(status=500)

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )

    assert [
        (condition["type"], condition["status"], condition["reason"])
        for condition in excinfo.value.status["conditions"]
    ] == [
        ("Accepted", "True", "Accepted"),
        ("Progressing", "True", "Retrying"),
        ("Reconciled", "False", "ResourceReadFailed"),
        ("Ready", "False", "RuntimeNotImplemented"),
        ("Degraded", "True", "ResourceReadFailed"),
        ("Upgradeable", "False", "UpgradeNotSupported"),
    ]


def test_foundational_collision_message_is_generic_and_value_safe() -> None:
    api = make_core_api()
    collided = build_coriolis_credentials_secret(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        retention="state-credentials",
        values=CORIOLIS_CREDENTIALS,
    )
    collided["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "other"
    api.read_namespaced_secret.side_effect = [
        collided,
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
    ]

    status = reconcile_appliance(spec=valid_spec(), meta=sample_meta(), core_api=api)

    message = status["conditions"][1]["message"]
    assert message == (
        "The existing resource 'operators/example-coriolis-credentials' conflicts "
        "with operator-managed identity and was not modified."
    )
    assert "ConfigMap" not in message
    assert "marker" not in message


def test_foundational_managed_apply_conflict_stops_later_writes_and_marker() -> None:
    api = make_core_api()
    managed_config = owned_body()
    managed_config["metadata"]["resourceVersion"] = "17"
    api.read_namespaced_config_map.side_effect = [
        _api_exception(404),
        managed_config,
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
    ]
    api.read_namespaced_secret.side_effect = [_api_exception(404)] * 8
    api.patch_namespaced_config_map.side_effect = client.ApiException(status=409)

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert api.create_namespaced_secret.call_count == 2
    api.create_namespaced_config_map.assert_not_called()
    assert api.patch_namespaced_config_map.call_count == 1


@pytest.mark.parametrize("failed_read", range(1, 6))
def test_non_404_at_each_foundational_read_position_prevents_writes(
    failed_read: int,
) -> None:
    api = MagicMock()
    api.api_client.default_headers = {}
    reads = 0

    def read(*, name: str, namespace: str) -> object:
        nonlocal reads
        reads += 1
        if reads == failed_read:
            raise client.ApiException(status=500)
        raise client.ApiException(status=404)

    api.read_namespaced_config_map.side_effect = read
    api.read_namespaced_secret.side_effect = read

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )

    assert reads == failed_read
    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceReadFailed"
    api.create_namespaced_secret.assert_not_called()
    api.create_namespaced_config_map.assert_not_called()
    api.patch_namespaced_secret.assert_not_called()
    api.patch_namespaced_config_map.assert_not_called()


@pytest.mark.parametrize(
    "target",
    [
        "build_state_config_map",
        "classify_existing_marker",
        "preflight_foundational_resources",
        "render_coriolis_config",
    ],
)
def test_preparation_failures_are_sanitized_and_prevent_writes(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    api = make_core_api(
        existing=desired_body() if target == "classify_existing_marker" else None
    )

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("preparation-sentinel-credential")

    monkeypatch.setattr(main, target, fail)

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "preparation-sentinel-credential" not in repr(excinfo.value.status)
    api.create_namespaced_secret.assert_not_called()
    api.create_namespaced_config_map.assert_not_called()
    api.patch_namespaced_secret.assert_not_called()
    api.patch_namespaced_config_map.assert_not_called()


def test_credential_generation_failure_is_sanitized_before_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_: int) -> str:
        raise RuntimeError("generated-credential-sentinel")

    monkeypatch.setattr(
        "coriolis_operator.reconcile.generate_coriolis_credentials", fail
    )
    api = make_core_api()

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "generated-credential-sentinel" not in repr(excinfo.value.status)
    api.create_namespaced_secret.assert_not_called()
    api.create_namespaced_config_map.assert_not_called()


def test_core_api_construction_failure_patches_sanitized_status_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> object:
        raise RuntimeError("construction-sentinel-token")

    monkeypatch.setattr(main.client, "CoreV1Api", fail)
    patch = MagicMock()

    with pytest.raises(kopf.TemporaryError):
        main._handle_reconcile(valid_spec(), sample_meta(), patch)

    reconciled = patch.status.update.call_args.args[0]
    assert reconciled["conditions"][2]["reason"] == "ResourceReadFailed"
    assert "construction-sentinel-token" not in repr(reconciled)


def _managed_dependency_service(
    component: str, resource_version: str | None = "17"
) -> dict:
    body = build_dependency_service(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
        component=component,
    )
    if resource_version is not None:
        body["metadata"]["resourceVersion"] = resource_version
    return body


def test_dependency_services_read_and_create_in_order() -> None:
    api = make_core_api()

    reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=api,
    )

    read_names = [
        call.kwargs["name"] for call in api.read_namespaced_service.call_args_list
    ]
    assert read_names == [
        appliance_resource_name("example", component)
        for component, _ in DEPENDENCY_SERVICES
    ] + ["example-coriolis-api"]
    created_names = [
        call.kwargs["body"]["metadata"]["name"]
        for call in api.create_namespaced_service.call_args_list
    ]
    assert created_names == [
        appliance_resource_name("example", component)
        for component, _ in DEPENDENCY_SERVICES
    ] + ["example-coriolis-api"]
    assert api.method_calls[-1] == call.create_namespaced_config_map(
        namespace="operators", body=ANY
    )


def test_managed_dependency_services_use_guarded_ssa_and_restore_content_type() -> None:
    api = make_core_api()
    api.api_client.default_headers["Content-Type"] = "application/json"
    api.read_namespaced_service.side_effect = [
        *[
            _managed_dependency_service(component)
            for component, _ in DEPENDENCY_SERVICES
        ],
        _api_exception(404),
    ]

    def patch_service(**_: object) -> None:
        assert (
            api.api_client.default_headers["Content-Type"]
            == "application/apply-patch+yaml"
        )

    api.patch_namespaced_service.side_effect = patch_service

    reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=api,
    )

    patched_names = [
        call.kwargs["name"] for call in api.patch_namespaced_service.call_args_list
    ]
    assert patched_names == [
        appliance_resource_name("example", component)
        for component, _ in DEPENDENCY_SERVICES
    ]
    for service_call in api.patch_namespaced_service.call_args_list:
        assert service_call.kwargs["body"]["metadata"]["resourceVersion"] == "17"
        assert service_call.kwargs["field_manager"] == "coriolis-operator"
        assert service_call.kwargs["force"] is True
    assert api.api_client.default_headers["Content-Type"] == "application/json"


def test_dependency_service_collision_has_no_writes_after_all_service_reads() -> None:
    api = make_core_api()
    collided = _managed_dependency_service("rabbitmq")
    collided["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"
    api.read_namespaced_service.side_effect = [
        collided,
        *[_api_exception(404) for _ in DEPENDENCY_SERVICES[1:]],
        _api_exception(404),
    ]

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=api,
    )

    assert api.read_namespaced_service.call_count == len(DEPENDENCY_SERVICES) + 1
    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert "operators/example-rabbitmq" in status["conditions"][2]["message"]
    assert not [
        method
        for method in api.method_calls
        if method[0].startswith(("create", "patch"))
    ]


def test_dependency_service_read_error_and_missing_version_retry() -> None:
    read_error_api = make_core_api()
    read_error_api.read_namespaced_service.side_effect = client.ApiException(status=403)
    missing_version_api = make_core_api()
    missing_version_api.read_namespaced_service.side_effect = [
        _managed_dependency_service("rabbitmq", resource_version=None),
        *[_api_exception(404) for _ in DEPENDENCY_SERVICES[1:]],
        _api_exception(404),
    ]

    for api in (read_error_api, missing_version_api):
        with pytest.raises(main.ReconcileRetry) as excinfo:
            reconcile_appliance(
                spec=valid_spec(),
                meta=sample_meta(),
                core_api=api,
            )

        expected = (
            "ResourceReadFailed" if api is read_error_api else "ResourceApplyFailed"
        )
        assert excinfo.value.status["conditions"][2]["reason"] == expected
        assert not [
            method
            for method in api.method_calls
            if method[0].startswith(("create", "patch"))
        ]


@pytest.mark.parametrize("operation", ["create", "patch"])
def test_dependency_service_apply_failure_prevents_marker_write(operation: str) -> None:
    api = make_core_api()
    if operation == "patch":
        api.read_namespaced_service.side_effect = [
            *[
                _managed_dependency_service(component)
                for component, _ in DEPENDENCY_SERVICES
            ],
            _api_exception(404),
        ]
        api.patch_namespaced_service.side_effect = [
            None,
            client.ApiException(status=409),
        ]
    else:
        api.create_namespaced_service.side_effect = [
            None,
            client.ApiException(status=409),
        ]

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert api.create_namespaced_config_map.call_count == 1
    assert (
        api.create_namespaced_config_map.call_args.kwargs["body"]["metadata"]["name"]
        == "example-coriolis-config"
    )
    service_calls = getattr(api, f"{operation}_namespaced_service").call_args_list
    service_names = [
        service_call.kwargs["body"]["metadata"]["name"]
        if operation == "create"
        else service_call.kwargs["name"]
        for service_call in service_calls
    ]
    assert service_names == [
        "example-rabbitmq",
        "example-memcached",
    ]
    assert len(service_calls) == 2
    assert api.create_namespaced_secret.call_count == 3
    api.patch_namespaced_config_map.assert_not_called()
    assert not [method for method in api.method_calls if method[0].startswith("delete")]


def test_invalid_runtime_configuration_conditions_are_stable() -> None:
    assert [
        (condition_type, status, reason)
        for condition_type, status, reason, _ in (
            invalid_runtime_configuration_conditions()
        )
    ] == [
        ("Accepted", "True", "Accepted"),
        ("Progressing", "False", "InvalidRuntimeConfiguration"),
        ("Reconciled", "False", "InvalidRuntimeConfiguration"),
        ("Ready", "False", "RuntimeNotImplemented"),
        ("Degraded", "True", "InvalidRuntimeConfiguration"),
        ("Upgradeable", "False", "UpgradeNotSupported"),
    ]
    messages = {
        message
        for _, _, reason, message in invalid_runtime_configuration_conditions()
        if reason == "InvalidRuntimeConfiguration"
    }
    assert messages == {
        "Complete valid MariaDB and RabbitMQ storage and resource configuration is "
        "required."
    }


@pytest.mark.parametrize(
    "spec",
    [
        {"profile": "core", "version": "2603.4"},
        {
            "profile": "core",
            "version": "2603.4",
            "storage": MARIADB_STORAGE,
        },
        {
            "profile": "core",
            "version": "2603.4",
            "storage": MARIADB_STORAGE,
            "resources": {
                "mariadb": {
                    "requests": {"cpu": "2", "memory": "512Mi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                }
            },
        },
    ],
)
def test_invalid_mariadb_configuration_is_stable_without_api_access(
    monkeypatch: pytest.MonkeyPatch, spec: dict
) -> None:
    assert_no_api_instantiation(monkeypatch)

    status = reconcile_appliance(spec=spec, meta=sample_meta())

    assert "acceptedVersion" not in status
    assert status["observedGeneration"] == 7
    assert status["conditions"][2]["reason"] == "InvalidRuntimeConfiguration"
    assert status["conditions"][2]["message"] == (
        "Complete valid MariaDB and RabbitMQ storage and resource configuration is "
        "required."
    )


def test_invalid_mariadb_configuration_preserves_accepted_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_no_api_instantiation(monkeypatch)

    status = reconcile_appliance(
        spec={"profile": "core", "version": "2603.4"},
        meta=sample_meta(generation=8),
        status={"acceptedVersion": "2603.4", "conditions": []},
    )

    assert status["acceptedVersion"] == "2603.4"
    assert status["observedGeneration"] == 8
    assert status["conditions"][2]["reason"] == "InvalidRuntimeConfiguration"


def test_mariadb_reads_and_writes_follow_the_frozen_cross_api_order() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    events: list[tuple[str, str, str]] = []

    def absent_read(resource: str):
        def read(*, name: str, namespace: str) -> object:
            assert namespace == "operators"
            events.append(("read", resource, name))
            raise _api_exception(404)

        return read

    def successful_create(resource: str):
        def create(*, namespace: str, body: dict) -> None:
            assert namespace == "operators"
            events.append(("write", resource, body["metadata"]["name"]))

        return create

    core_api.read_namespaced_config_map.side_effect = absent_read("configmap")
    core_api.read_namespaced_secret.side_effect = absent_read("secret")
    core_api.read_namespaced_service.side_effect = absent_read("service")
    core_api.read_namespaced_persistent_volume_claim.side_effect = absent_read("pvc")
    apps_api.read_namespaced_stateful_set.side_effect = absent_read("statefulset")
    apps_api.read_namespaced_deployment.side_effect = absent_read("deployment")
    core_api.create_namespaced_config_map.side_effect = successful_create("configmap")
    core_api.create_namespaced_secret.side_effect = successful_create("secret")
    core_api.create_namespaced_service.side_effect = successful_create("service")
    core_api.create_namespaced_persistent_volume_claim.side_effect = successful_create(
        "pvc"
    )
    apps_api.create_namespaced_stateful_set.side_effect = successful_create(
        "statefulset"
    )
    apps_api.create_namespaced_deployment.side_effect = successful_create("deployment")

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        apps_api=apps_api,
    )

    assert [event for event in events if event[0] == "read"] == [
        ("read", "configmap", "example-operator-state"),
        ("read", "secret", "example-coriolis-credentials"),
        ("read", "secret", "example-infrastructure-credentials"),
        ("read", "configmap", "example-coriolis-config"),
        ("read", "secret", "example-coriolis-config-secret"),
        ("read", "service", "example-rabbitmq"),
        ("read", "service", "example-memcached"),
        ("read", "service", "example-mariadb"),
        ("read", "service", "example-keystone"),
        ("read", "service", "example-coriolis-api"),
        ("read", "pvc", "example-mariadb-data"),
        ("read", "configmap", "example-mariadb-config"),
        ("read", "secret", "example-mariadb-config-secret"),
        ("read", "statefulset", "example-mariadb"),
        ("read", "deployment", "example-memcached"),
        ("read", "pvc", "example-rabbitmq-data"),
        ("read", "configmap", "example-rabbitmq-config"),
        ("read", "statefulset", "example-rabbitmq"),
        ("read", "secret", "example-keystone-database-credentials"),
        ("read", "secret", "example-keystone-fernet-keys"),
        ("read", "secret", "example-keystone-credential-keys"),
        ("read", "configmap", "example-keystone-config"),
        ("read", "secret", "example-keystone-config-secret"),
        ("read", "deployment", "example-keystone"),
        ("read", "deployment", "example-coriolis-api"),
        ("read", "deployment", "example-coriolis-conductor"),
        ("read", "configmap", "example-common-bootstrap-v2"),
    ]
    assert [event for event in events if event[0] == "write"] == [
        ("write", "secret", "example-coriolis-credentials"),
        ("write", "secret", "example-infrastructure-credentials"),
        ("write", "configmap", "example-coriolis-config"),
        ("write", "secret", "example-coriolis-config-secret"),
        ("write", "service", "example-rabbitmq"),
        ("write", "service", "example-memcached"),
        ("write", "service", "example-mariadb"),
        ("write", "service", "example-keystone"),
        ("write", "secret", "example-keystone-database-credentials"),
        ("write", "secret", "example-keystone-fernet-keys"),
        ("write", "secret", "example-keystone-credential-keys"),
        ("write", "pvc", "example-mariadb-data"),
        ("write", "configmap", "example-mariadb-config"),
        ("write", "secret", "example-mariadb-config-secret"),
        ("write", "statefulset", "example-mariadb"),
        ("write", "pvc", "example-rabbitmq-data"),
        ("write", "configmap", "example-rabbitmq-config"),
        ("write", "statefulset", "example-rabbitmq"),
        ("write", "deployment", "example-memcached"),
        ("write", "configmap", "example-keystone-config"),
        ("write", "secret", "example-keystone-config-secret"),
        ("write", "deployment", "example-keystone"),
        ("write", "configmap", "example-common-bootstrap-v2"),
        ("write", "deployment", "example-coriolis-conductor"),
        ("write", "service", "example-coriolis-api"),
        ("write", "deployment", "example-coriolis-api"),
        ("write", "configmap", "example-operator-state"),
    ]
    assert status["acceptedVersion"] == "2603.4"
    assert status["conditions"][2]["reason"] == "Reconciled"
    assert status["conditions"][3]["reason"] == "RuntimeNotImplemented"


@pytest.mark.parametrize(
    "resource_name",
    [
        "example-mariadb-data",
        "example-mariadb-config",
        "example-mariadb-config-secret",
        "example-mariadb",
    ],
)
def test_mariadb_collision_at_each_position_is_mutation_free(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    collided = mariadb_bodies()[resource_name]
    collided["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "other"
    configure_mariadb_existing(core_api, apps_api, {resource_name: collided})

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        apps_api=apps_api,
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert f"operators/{resource_name}" in status["conditions"][2]["message"]
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_mariadb_reuses_pvc_without_write_and_guarded_applies_managed_resources() -> (
    None
):
    core_api = make_core_api()
    apps_api = make_apps_api()
    existing = mariadb_bodies()
    for resource_name in (
        "example-mariadb-config",
        "example-mariadb-config-secret",
        "example-mariadb",
    ):
        existing[resource_name]["metadata"]["resourceVersion"] = "17"
    configure_mariadb_existing(core_api, apps_api, existing)
    core_api.api_client.default_headers["Content-Type"] = "application/json"
    apps_api.api_client.default_headers["Content-Type"] = "application/json"

    reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        apps_api=apps_api,
    )

    pvc_creates = [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_persistent_volume_claim.call_args_list
    ]
    assert "example-mariadb-data" not in pvc_creates
    core_api.patch_namespaced_persistent_volume_claim.assert_not_called()
    config_apply = core_api.patch_namespaced_config_map.call_args.kwargs
    secret_apply = core_api.patch_namespaced_secret.call_args.kwargs
    stateful_set_apply = apps_api.patch_namespaced_stateful_set.call_args.kwargs
    assert config_apply["name"] == "example-mariadb-config"
    assert secret_apply["name"] == "example-mariadb-config-secret"
    assert stateful_set_apply["name"] == "example-mariadb"
    for applied in (config_apply, secret_apply, stateful_set_apply):
        assert applied["body"]["metadata"]["resourceVersion"] == "17"
        assert applied["field_manager"] == "coriolis-operator"
        assert applied["force"] is True
    assert core_api.api_client.default_headers["Content-Type"] == "application/json"
    assert apps_api.api_client.default_headers["Content-Type"] == "application/json"
    marker = core_api.create_namespaced_config_map.call_args.kwargs["body"]
    assert marker["metadata"]["name"] == "example-operator-state"


@pytest.mark.parametrize(
    "spec",
    [
        {"profile": "core", "version": "2603.4"},
        {
            "profile": "core",
            "version": "2603.4",
            "storage": MARIADB_STORAGE,
            "resources": MARIADB_RESOURCES,
        },
        {
            "profile": "core",
            "version": "2603.4",
            "storage": MARIADB_STORAGE | RABBITMQ_STORAGE,
            "resources": MARIADB_RESOURCES
            | {
                "rabbitmq": {
                    "requests": {"cpu": "2", "memory": "512Mi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                }
            },
        },
    ],
)
def test_invalid_rabbitmq_configuration_is_mutation_free_before_api_access(
    monkeypatch: pytest.MonkeyPatch, spec: dict
) -> None:
    assert_no_api_instantiation(monkeypatch)

    status = reconcile_appliance(spec=spec, meta=sample_meta())

    assert status["conditions"][2]["reason"] == "InvalidRuntimeConfiguration"
    assert status["conditions"][2]["message"] == (
        "Complete valid MariaDB and RabbitMQ storage and resource configuration is "
        "required."
    )


@pytest.mark.parametrize(
    "resource_name",
    ["example-rabbitmq-data", "example-rabbitmq-config", "example-rabbitmq"],
)
def test_rabbitmq_collision_at_each_position_is_mutation_free(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    collided = rabbitmq_bodies()[resource_name]
    collided["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "other"
    configure_mariadb_existing(core_api, apps_api, {resource_name: collided})

    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert f"operators/{resource_name}" in status["conditions"][2]["message"]
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


@pytest.mark.parametrize(
    "resource_name",
    ["example-rabbitmq-data", "example-rabbitmq-config", "example-rabbitmq"],
)
def test_non_404_at_each_rabbitmq_read_position_prevents_writes(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()

    def fail_target(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == resource_name:
            raise client.ApiException(status=403, reason="rabbitmq-read-sentinel")
        raise _api_exception(404)

    if resource_name == "example-rabbitmq-data":
        core_api.read_namespaced_persistent_volume_claim.side_effect = fail_target
    elif resource_name == "example-rabbitmq-config":
        core_api.read_namespaced_config_map.side_effect = fail_target
    else:
        apps_api.read_namespaced_stateful_set.side_effect = fail_target

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceReadFailed"
    assert "rabbitmq-read-sentinel" not in repr(excinfo.value.status)
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


@pytest.mark.parametrize(
    "resource_name", ["example-rabbitmq-config", "example-rabbitmq"]
)
def test_managed_rabbitmq_without_resource_version_retries_before_writes(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    configure_mariadb_existing(
        core_api, apps_api, {resource_name: rabbitmq_bodies()[resource_name]}
    )

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_rabbitmq_reuses_pvc_and_guarded_applies_before_memcached() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    existing = rabbitmq_bodies()
    for resource_name in ("example-rabbitmq-config", "example-rabbitmq"):
        existing[resource_name]["metadata"]["resourceVersion"] = "19"
    configure_mariadb_existing(core_api, apps_api, existing)

    reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    assert "example-rabbitmq-data" not in [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_persistent_volume_claim.call_args_list
    ]
    config_apply = core_api.patch_namespaced_config_map.call_args_list[-1].kwargs
    stateful_apply = apps_api.patch_namespaced_stateful_set.call_args.kwargs
    assert config_apply["name"] == "example-rabbitmq-config"
    assert stateful_apply["name"] == "example-rabbitmq"
    assert config_apply["body"]["metadata"]["resourceVersion"] == "19"
    assert stateful_apply["body"]["metadata"]["resourceVersion"] == "19"
    write_names = [
        item.kwargs.get("body", {})
        .get("metadata", {})
        .get("name", item.kwargs.get("name"))
        for item in apps_api.method_calls
        if item[0].startswith(("create_namespaced", "patch_namespaced"))
    ]
    assert write_names.index("example-rabbitmq") < write_names.index(
        "example-memcached"
    )


@pytest.mark.parametrize(
    "resource_name",
    ["example-rabbitmq-data", "example-rabbitmq-config", "example-rabbitmq"],
)
def test_rabbitmq_apply_failure_is_sanitized_and_skips_memcached_and_marker(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()

    def fail_target(*, namespace: str, body: dict) -> None:
        if body["metadata"]["name"] == resource_name:
            raise client.ApiException(status=409, reason="rabbitmq-apply-sentinel")

    if resource_name == "example-rabbitmq-data":
        core_api.create_namespaced_persistent_volume_claim.side_effect = fail_target
    elif resource_name == "example-rabbitmq-config":
        core_api.create_namespaced_config_map.side_effect = fail_target
    else:
        apps_api.create_namespaced_stateful_set.side_effect = fail_target

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "rabbitmq-apply-sentinel" not in repr(excinfo.value.status)
    apps_api.create_namespaced_deployment.assert_not_called()
    assert "example-operator-state" not in [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_config_map.call_args_list
    ]


@pytest.mark.parametrize(
    "resource_name", ["example-rabbitmq-config", "example-rabbitmq"]
)
def test_rabbitmq_patch_failure_is_sanitized_and_skips_memcached_and_marker(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    existing = rabbitmq_bodies()
    for name in ("example-rabbitmq-config", "example-rabbitmq"):
        existing[name]["metadata"]["resourceVersion"] = "21"
    configure_mariadb_existing(core_api, apps_api, existing)
    failure = client.ApiException(status=409, reason="rabbitmq-patch-sentinel")
    if resource_name == "example-rabbitmq-config":
        core_api.patch_namespaced_config_map.side_effect = failure
    else:
        apps_api.patch_namespaced_stateful_set.side_effect = failure

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "rabbitmq-patch-sentinel" not in repr(excinfo.value.status)
    apps_api.create_namespaced_deployment.assert_not_called()
    assert "example-operator-state" not in [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_config_map.call_args_list
    ]


def test_rabbitmq_preflight_and_manifests_do_not_receive_or_expose_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_api = make_core_api()
    infrastructure = build_infrastructure_credentials_secret(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        retention="state-credentials",
        values={**INFRASTRUCTURE_CREDENTIALS, "rabbitmq_password": "rabbit-secret"},
    )
    core_api.read_namespaced_secret.side_effect = [
        _api_exception(404),
        infrastructure,
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
    ]
    captured: dict[str, object] = {}
    preflight = main.preflight_rabbitmq_resources

    def capture(**kwargs: object) -> object:
        captured.update(kwargs)
        return preflight(**kwargs)

    monkeypatch.setattr(main, "preflight_rabbitmq_resources", capture)
    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api
    )

    assert "rabbit-secret" not in repr(captured)
    assert "rabbitmq_password" not in captured
    rabbitmq_bodies_written = [
        item.kwargs["body"]
        for item in (
            core_api.create_namespaced_persistent_volume_claim.call_args_list
            + core_api.create_namespaced_config_map.call_args_list
        )
        if item.kwargs["body"]["metadata"]["name"].startswith("example-rabbitmq")
    ]
    assert all("rabbit-secret" not in repr(body) for body in rabbitmq_bodies_written)
    assert "rabbit-secret" not in repr(status)


@pytest.mark.parametrize(
    "resource_name",
    [
        "example-mariadb-data",
        "example-mariadb-config",
        "example-mariadb-config-secret",
        "example-mariadb",
    ],
)
def test_non_404_at_each_mariadb_read_position_prevents_writes(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()

    def fail_target(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == resource_name:
            raise client.ApiException(status=403, reason="read-value-sentinel")
        raise _api_exception(404)

    if resource_name == "example-mariadb-data":
        core_api.read_namespaced_persistent_volume_claim.side_effect = fail_target
    elif resource_name == "example-mariadb-config":
        core_api.read_namespaced_config_map.side_effect = fail_target
    elif resource_name == "example-mariadb-config-secret":
        core_api.read_namespaced_secret.side_effect = fail_target
    else:
        apps_api.read_namespaced_stateful_set.side_effect = fail_target

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            apps_api=apps_api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceReadFailed"
    assert "read-value-sentinel" not in repr(excinfo.value.status)
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


@pytest.mark.parametrize(
    "resource_name",
    [
        "example-mariadb-config",
        "example-mariadb-config-secret",
        "example-mariadb",
    ],
)
def test_managed_mariadb_resource_without_version_retries_before_writes(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    existing = {resource_name: mariadb_bodies()[resource_name]}
    configure_mariadb_existing(core_api, apps_api, existing)

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            apps_api=apps_api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


@pytest.mark.parametrize(
    "resource_name",
    [
        "example-mariadb-data",
        "example-mariadb-config",
        "example-mariadb-config-secret",
        "example-mariadb",
    ],
)
def test_mariadb_create_failure_stops_later_writes_and_marker(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()

    def fail_target(*, namespace: str, body: dict) -> None:
        assert namespace == "operators"
        if body["metadata"]["name"] == resource_name:
            raise client.ApiException(status=409, reason="apply-value-sentinel")

    if resource_name == "example-mariadb-data":
        core_api.create_namespaced_persistent_volume_claim.side_effect = fail_target
    elif resource_name == "example-mariadb-config":
        core_api.create_namespaced_config_map.side_effect = fail_target
    elif resource_name == "example-mariadb-config-secret":
        core_api.create_namespaced_secret.side_effect = fail_target
    else:
        apps_api.create_namespaced_stateful_set.side_effect = fail_target

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            apps_api=apps_api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "apply-value-sentinel" not in repr(excinfo.value.status)
    created_config_maps = [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_config_map.call_args_list
    ]
    assert "example-operator-state" not in created_config_maps
    assert not [item for item in api_writes(core_api) if item[0].startswith("delete")]
    assert not [item for item in api_writes(apps_api) if item[0].startswith("delete")]


@pytest.mark.parametrize(
    "resource_name",
    [
        "example-mariadb-config",
        "example-mariadb-config-secret",
        "example-mariadb",
    ],
)
def test_mariadb_patch_failure_preserves_accepted_version_and_skips_marker(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    existing = mariadb_bodies()
    for name in (
        "example-mariadb-config",
        "example-mariadb-config-secret",
        "example-mariadb",
    ):
        existing[name]["metadata"]["resourceVersion"] = "17"
    configure_mariadb_existing(core_api, apps_api, existing)
    failure = client.ApiException(status=409, reason="patch-value-sentinel")
    if resource_name == "example-mariadb-config":
        core_api.patch_namespaced_config_map.side_effect = failure
    elif resource_name == "example-mariadb-config-secret":
        core_api.patch_namespaced_secret.side_effect = failure
    else:
        apps_api.patch_namespaced_stateful_set.side_effect = failure

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            status={"acceptedVersion": "2603.4", "conditions": []},
            core_api=core_api,
            apps_api=apps_api,
        )

    assert excinfo.value.status["acceptedVersion"] == "2603.4"
    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "patch-value-sentinel" not in repr(excinfo.value.status)
    created_config_maps = [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_config_map.call_args_list
    ]
    assert "example-operator-state" not in created_config_maps


def _managed_memcached_deployment(resource_version: str | None = "17") -> dict:
    deployment = build_memcached_deployment(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )
    if resource_version is not None:
        deployment["metadata"]["resourceVersion"] = resource_version
    return deployment


def configure_memcached_existing(apps_api: MagicMock, deployment: dict) -> None:
    def read(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-memcached":
            return deployment
        raise _api_exception(404)

    apps_api.read_namespaced_deployment.side_effect = read


def test_memcached_collision_is_mutation_free_after_all_reads() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    collided = _managed_memcached_deployment()
    collided["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"
    configure_memcached_existing(apps_api, collided)

    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert "operators/example-memcached" in status["conditions"][2]["message"]
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_memcached_non_404_read_failure_prevents_writes() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    apps_api.read_namespaced_deployment.side_effect = client.ApiException(
        status=403, reason="memcached-read-sentinel"
    )

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceReadFailed"
    assert "memcached-read-sentinel" not in repr(excinfo.value.status)
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_managed_memcached_without_resource_version_retries_before_writes() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    configure_memcached_existing(apps_api, _managed_memcached_deployment(None))

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_managed_memcached_uses_guarded_ssa_and_restores_headers() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    configure_memcached_existing(apps_api, _managed_memcached_deployment())
    apps_api.api_client.default_headers["Content-Type"] = "application/json"

    def patch_deployment(**_: object) -> None:
        assert (
            apps_api.api_client.default_headers["Content-Type"]
            == "application/apply-patch+yaml"
        )

    apps_api.patch_namespaced_deployment.side_effect = patch_deployment
    reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    applied = apps_api.patch_namespaced_deployment.call_args.kwargs
    assert applied["name"] == "example-memcached"
    assert applied["body"]["metadata"]["resourceVersion"] == "17"
    assert applied["field_manager"] == "coriolis-operator"
    assert applied["force"] is True
    assert apps_api.api_client.default_headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("operation", ["create", "patch"])
def test_memcached_apply_failure_is_sanitized_and_skips_marker(operation: str) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    if operation == "patch":
        configure_memcached_existing(apps_api, _managed_memcached_deployment())
    getattr(
        apps_api, f"{operation}_namespaced_deployment"
    ).side_effect = client.ApiException(status=409, reason="memcached-apply-sentinel")

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            status={"acceptedVersion": "2603.4", "conditions": []},
            core_api=core_api,
            apps_api=apps_api,
        )

    assert excinfo.value.status["acceptedVersion"] == "2603.4"
    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "memcached-apply-sentinel" not in repr(excinfo.value.status)
    marker_names = [
        call.kwargs["body"]["metadata"]["name"]
        for call in core_api.create_namespaced_config_map.call_args_list
    ]
    assert "example-operator-state" not in marker_names
    assert not [
        method for method in api_writes(core_api) if method[0].startswith("delete")
    ]
    assert not [
        method for method in api_writes(apps_api) if method[0].startswith("delete")
    ]


@pytest.mark.parametrize(
    "handler",
    [main.update_appliance_storage, main.update_appliance_resources],
)
def test_mariadb_spec_field_handlers_route_through_reconcile(
    monkeypatch: pytest.MonkeyPatch, handler
) -> None:
    handled = MagicMock()
    monkeypatch.setattr(main, "_handle_reconcile", handled)
    patch = MagicMock()
    spec = valid_spec()
    status = {"acceptedVersion": "2603.4", "conditions": []}

    result = handler(spec=spec, meta=sample_meta(), patch=patch, status=status)

    assert result is None
    handled.assert_called_once_with(spec, sample_meta(), patch, status)


def test_apps_api_construction_failure_patches_sanitized_status_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core_api = make_core_api()

    def fail() -> object:
        raise RuntimeError("apps-construction-value-sentinel")

    monkeypatch.setattr(main.client, "CoreV1Api", MagicMock(return_value=core_api))
    monkeypatch.setattr(main.client, "AppsV1Api", fail)
    patch = MagicMock()

    with pytest.raises(kopf.TemporaryError):
        main._handle_reconcile(valid_spec(), sample_meta(), patch)

    reconciled = patch.status.update.call_args.args[0]
    assert reconciled["conditions"][2]["reason"] == "ResourceReadFailed"
    assert "apps-construction-value-sentinel" not in repr(reconciled)
    assert core_api.method_calls == []


def test_keystone_reads_all_resources_before_the_first_write() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    reads: list[str] = []

    def absent(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        reads.append(name)
        raise _api_exception(404)

    core_api.read_namespaced_config_map.side_effect = absent
    core_api.read_namespaced_secret.side_effect = absent
    core_api.read_namespaced_service.side_effect = absent
    core_api.read_namespaced_persistent_volume_claim.side_effect = absent
    apps_api.read_namespaced_stateful_set.side_effect = absent
    apps_api.read_namespaced_deployment.side_effect = absent

    reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    assert reads == [
        "example-operator-state",
        "example-coriolis-credentials",
        "example-infrastructure-credentials",
        "example-coriolis-config",
        "example-coriolis-config-secret",
        "example-rabbitmq",
        "example-memcached",
        "example-mariadb",
        "example-keystone",
        "example-coriolis-api",
        "example-mariadb-data",
        "example-mariadb-config",
        "example-mariadb-config-secret",
        "example-mariadb",
        "example-memcached",
        "example-rabbitmq-data",
        "example-rabbitmq-config",
        "example-rabbitmq",
        "example-keystone-database-credentials",
        "example-keystone-fernet-keys",
        "example-keystone-credential-keys",
        "example-keystone-config",
        "example-keystone-config-secret",
        "example-keystone",
        "example-coriolis-api",
        "example-coriolis-conductor",
        "example-common-bootstrap-v2",
    ]
    assert reads.index("example-keystone-database-credentials") > reads.index(
        "example-rabbitmq"
    )


@pytest.mark.parametrize(
    "resource_name",
    [
        "example-keystone-database-credentials",
        "example-keystone-fernet-keys",
        "example-keystone-credential-keys",
        "example-keystone-config",
        "example-keystone-config-secret",
        "example-keystone",
    ],
)
def test_keystone_collision_at_each_position_is_mutation_free(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    existing = keystone_bodies()
    existing[resource_name]["metadata"]["labels"]["app.kubernetes.io/managed-by"] = (
        "other"
    )
    configure_keystone_existing(
        core_api, apps_api, {resource_name: existing[resource_name]}
    )

    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert f"operators/{resource_name}" in status["conditions"][2]["message"]
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


@pytest.mark.parametrize(
    "resource_name",
    [
        "example-keystone-database-credentials",
        "example-keystone-fernet-keys",
        "example-keystone-credential-keys",
        "example-keystone-config",
        "example-keystone-config-secret",
        "example-keystone",
    ],
)
def test_non_404_at_each_keystone_read_position_prevents_writes(
    resource_name: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()

    def fail_target(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == resource_name:
            raise client.ApiException(status=403, reason="keystone-read-sentinel")
        raise _api_exception(404)

    if resource_name == "example-keystone":
        apps_api.read_namespaced_deployment.side_effect = fail_target
    elif resource_name == "example-keystone-config":
        core_api.read_namespaced_config_map.side_effect = fail_target
    else:
        core_api.read_namespaced_secret.side_effect = fail_target

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceReadFailed"
    assert "keystone-read-sentinel" not in repr(excinfo.value.status)
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_keystone_retained_creation_precedes_mariadb_and_marker_is_last() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    writes: list[str] = []

    def record(*, body: dict, **_: object) -> None:
        writes.append(body["metadata"]["name"])

    for api, methods in (
        (
            core_api,
            (
                "create_namespaced_secret",
                "create_namespaced_config_map",
                "create_namespaced_service",
                "create_namespaced_persistent_volume_claim",
            ),
        ),
        (
            apps_api,
            ("create_namespaced_stateful_set", "create_namespaced_deployment"),
        ),
    ):
        for method in methods:
            getattr(api, method).side_effect = record

    reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    assert writes == [
        "example-coriolis-credentials",
        "example-infrastructure-credentials",
        "example-coriolis-config",
        "example-coriolis-config-secret",
        "example-rabbitmq",
        "example-memcached",
        "example-mariadb",
        "example-keystone",
        "example-keystone-database-credentials",
        "example-keystone-fernet-keys",
        "example-keystone-credential-keys",
        "example-mariadb-data",
        "example-mariadb-config",
        "example-mariadb-config-secret",
        "example-mariadb",
        "example-rabbitmq-data",
        "example-rabbitmq-config",
        "example-rabbitmq",
        "example-memcached",
        "example-keystone-config",
        "example-keystone-config-secret",
        "example-keystone",
        "example-common-bootstrap-v2",
        "example-coriolis-conductor",
        "example-coriolis-api",
        "example-coriolis-api",
        "example-operator-state",
    ]


def test_keystone_retained_reuse_performs_no_retained_secret_writes() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    existing = keystone_bodies()
    configure_keystone_existing(
        core_api,
        apps_api,
        {
            name: existing[name]
            for name in (
                "example-keystone-database-credentials",
                "example-keystone-fernet-keys",
                "example-keystone-credential-keys",
            )
        },
    )

    reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    retained_names = {
        "example-keystone-database-credentials",
        "example-keystone-fernet-keys",
        "example-keystone-credential-keys",
    }
    assert not {
        call.kwargs["body"]["metadata"]["name"]
        for call in core_api.create_namespaced_secret.call_args_list
        if call.kwargs["body"]["metadata"]["name"] in retained_names
    }


@pytest.mark.parametrize(
    ("api_name", "method_name", "resource_kind", "resource_name"),
    [
        (
            "core",
            "create_namespaced_secret",
            "Secret",
            "example-keystone-database-credentials",
        ),
        (
            "core",
            "create_namespaced_secret",
            "Secret",
            "example-keystone-fernet-keys",
        ),
        (
            "core",
            "create_namespaced_secret",
            "Secret",
            "example-keystone-credential-keys",
        ),
        (
            "core",
            "create_namespaced_config_map",
            "ConfigMap",
            "example-keystone-config",
        ),
        (
            "core",
            "create_namespaced_secret",
            "Secret",
            "example-keystone-config-secret",
        ),
        (
            "apps",
            "create_namespaced_deployment",
            "Deployment",
            "example-keystone",
        ),
    ],
)
def test_keystone_create_failure_is_sanitized_and_stops_at_failed_write(
    api_name: str, method_name: str, resource_kind: str, resource_name: str
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    writes: list[tuple[str, str]] = []

    def record(*, body: dict, **_: object) -> None:
        writes.append((body["kind"], body["metadata"]["name"]))

    def fail_target(*, body: dict, **_: object) -> None:
        record(body=body)
        if (body["kind"], body["metadata"]["name"]) == (
            resource_kind,
            resource_name,
        ):
            raise client.ApiException(status=409, reason="keystone-apply-sentinel")

    for api, methods in (
        (
            core_api,
            (
                "create_namespaced_secret",
                "create_namespaced_config_map",
                "create_namespaced_service",
                "create_namespaced_persistent_volume_claim",
            ),
        ),
        (
            apps_api,
            ("create_namespaced_stateful_set", "create_namespaced_deployment"),
        ),
    ):
        for method in methods:
            getattr(api, method).side_effect = record
    target_api = core_api if api_name == "core" else apps_api
    getattr(target_api, method_name).side_effect = fail_target

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "keystone-apply-sentinel" not in repr(excinfo.value.status)
    assert writes[-1] == (resource_kind, resource_name)
    assert ("ConfigMap", "example-operator-state") not in writes


@pytest.mark.parametrize(
    ("api_name", "method_name", "resource_kind", "resource_name"),
    [
        (
            "core",
            "patch_namespaced_config_map",
            "ConfigMap",
            "example-keystone-config",
        ),
        (
            "core",
            "patch_namespaced_secret",
            "Secret",
            "example-keystone-config-secret",
        ),
        (
            "apps",
            "patch_namespaced_deployment",
            "Deployment",
            "example-keystone",
        ),
    ],
)
def test_keystone_patch_failure_is_sanitized_and_stops_at_failed_write(
    api_name: str, method_name: str, resource_kind: str, resource_name: str
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    existing = keystone_bodies()
    for name in (
        "example-keystone-config",
        "example-keystone-config-secret",
        "example-keystone",
    ):
        existing[name]["metadata"]["resourceVersion"] = "23"
    configure_keystone_existing(core_api, apps_api, existing)
    writes: list[tuple[str, str]] = []

    def record(*, body: dict, **_: object) -> None:
        writes.append((body["kind"], body["metadata"]["name"]))

    def fail_target(*, body: dict, **_: object) -> None:
        record(body=body)
        raise client.ApiException(status=409, reason="keystone-patch-sentinel")

    for api, methods in (
        (
            core_api,
            (
                "create_namespaced_secret",
                "create_namespaced_config_map",
                "create_namespaced_service",
                "create_namespaced_persistent_volume_claim",
                "patch_namespaced_config_map",
                "patch_namespaced_secret",
            ),
        ),
        (
            apps_api,
            (
                "create_namespaced_stateful_set",
                "create_namespaced_deployment",
                "patch_namespaced_deployment",
            ),
        ),
    ):
        for method in methods:
            getattr(api, method).side_effect = record
    target_api = core_api if api_name == "core" else apps_api
    getattr(target_api, method_name).side_effect = fail_target

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "keystone-patch-sentinel" not in repr(excinfo.value.status)
    assert writes[-1] == (resource_kind, resource_name)
    assert ("ConfigMap", "example-operator-state") not in writes


def bootstrap_config_map_body() -> dict:
    return build_common_bootstrap_config_map(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
        script=_bootstrap_script(),
    )


def assert_no_marker_write(core_api: MagicMock) -> None:
    marker_names = [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_config_map.call_args_list
    ]
    assert "example-operator-state" not in marker_names
    patched_markers = [
        item.kwargs["name"]
        for item in core_api.patch_namespaced_config_map.call_args_list
    ]
    assert "example-operator-state" not in patched_markers


def test_bootstrap_running_conditions_are_exact_six_conditions() -> None:
    assert [
        (condition_type, status, reason)
        for condition_type, status, reason, _ in bootstrap_running_conditions()
    ] == [
        ("Accepted", "True", "Accepted"),
        ("Progressing", "True", "BootstrapRunning"),
        ("Reconciled", "False", "BootstrapRunning"),
        ("Ready", "False", "RuntimeNotImplemented"),
        ("Degraded", "False", "NotDegraded"),
        ("Upgradeable", "False", "UpgradeNotSupported"),
    ]


def test_bootstrap_config_map_and_job_exact_contract() -> None:
    config_map = bootstrap_config_map_body()
    job = bootstrap_job_body()

    assert config_map["apiVersion"] == "v1"
    assert config_map["kind"] == "ConfigMap"
    assert config_map["metadata"]["name"] == "example-common-bootstrap-v2"
    assert config_map["metadata"]["namespace"] == "operators"
    assert config_map["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert config_map["immutable"] is True
    assert set(config_map["data"]) == {"bootstrap.py"}
    assert config_map["data"]["bootstrap.py"] == _bootstrap_script()

    assert job["apiVersion"] == "batch/v1"
    assert job["kind"] == "Job"
    assert job["metadata"]["name"] == "example-common-bootstrap-v2"
    assert job["metadata"]["namespace"] == "operators"
    assert job["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert BOOTSTRAP_SCRIPT_ANNOTATION in job["metadata"]["annotations"]
    assert BOOTSTRAP_TEMPLATE_ANNOTATION in job["metadata"]["annotations"]

    spec = job["spec"]
    assert spec["backoffLimit"] == 2
    assert spec["activeDeadlineSeconds"] == 600
    assert spec["completions"] == 1
    assert spec["parallelism"] == 1
    template_meta = spec["template"]["metadata"]
    assert (
        template_meta["annotations"][BOOTSTRAP_TEMPLATE_ANNOTATION]
        == (job["metadata"]["annotations"][BOOTSTRAP_TEMPLATE_ANNOTATION])
    )
    assert (
        template_meta["annotations"][BOOTSTRAP_SCRIPT_ANNOTATION]
        == (job["metadata"]["annotations"][BOOTSTRAP_SCRIPT_ANNOTATION])
    )
    assert "ttlSecondsAfterFinished" not in job["spec"]
    pod = spec["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert "serviceAccountName" not in pod
    assert pod["imagePullSecrets"] == [{"name": "coriolis-appliance-registry"}]
    pod_sc = pod["securityContext"]
    assert pod_sc["runAsUser"] == 42434
    assert pod_sc["runAsGroup"] == 42434
    assert pod_sc["fsGroup"] == 42434
    assert pod_sc["fsGroupChangePolicy"] == "OnRootMismatch"

    assert len(pod["containers"]) == 1
    container = pod["containers"][0]
    assert container["name"] == "common-bootstrap-v2"
    assert container["image"] == CONDUCTOR_IMAGE
    assert container["command"] == ["python3", "/etc/coriolis-bootstrap/bootstrap.py"]
    assert "args" not in container
    env = {entry["name"]: entry["value"] for entry in container["env"]}
    assert env == {"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"}
    for name in env:
        assert not any(
            marker in name for marker in ("PASSWORD", "TOKEN", "SECRET", "CREDENTIAL")
        )
    security_context = container["securityContext"]
    assert security_context["runAsNonRoot"] is True
    assert security_context["readOnlyRootFilesystem"] is True
    assert security_context["allowPrivilegeEscalation"] is False
    assert security_context["capabilities"] == {"drop": ["ALL"]}
    assert security_context["seccompProfile"] == {"type": "RuntimeDefault"}

    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert set(volumes) == {
        "tmp",
        "script",
        "config",
        "infra-credentials",
        "coriolis-credentials",
    }
    assert volumes["tmp"] == {"name": "tmp", "emptyDir": {}}
    assert volumes["script"]["configMap"]["name"] == "example-common-bootstrap-v2"
    assert volumes["script"]["configMap"]["items"] == [
        {"key": "bootstrap.py", "path": "bootstrap.py", "mode": 0o444}
    ]
    assert volumes["config"]["secret"]["secretName"] == "example-coriolis-config-secret"
    assert volumes["config"]["secret"]["items"] == [
        {"key": "coriolis.conf", "path": "coriolis.conf", "mode": 0o440}
    ]
    assert (
        volumes["infra-credentials"]["secret"]["secretName"]
        == "example-infrastructure-credentials"
    )
    assert volumes["infra-credentials"]["secret"]["items"] == [
        {
            "key": "keystone_admin_password",
            "path": "keystone-admin-password",
            "mode": 0o440,
        },
        {"key": "rabbitmq_password", "path": "rabbitmq-password", "mode": 0o440},
    ]
    assert (
        volumes["coriolis-credentials"]["secret"]["secretName"]
        == "example-coriolis-credentials"
    )
    assert volumes["coriolis-credentials"]["secret"]["items"] == [
        {
            "key": "coriolis_keystone_password",
            "path": "coriolis-keystone-password",
            "mode": 0o440,
        },
        {
            "key": "coriolis_database_password",
            "path": "coriolis-database-password",
            "mode": 0o440,
        },
    ]

    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    assert mounts["script"]["mountPath"] == "/etc/coriolis-bootstrap"
    assert mounts["script"]["readOnly"] is True
    assert mounts["config"]["mountPath"] == "/etc/coriolis"
    assert mounts["config"]["readOnly"] is True
    assert mounts["infra-credentials"]["mountPath"] == "/etc/coriolis-bootstrap-infra"
    assert mounts["infra-credentials"]["readOnly"] is True
    assert mounts["coriolis-credentials"]["mountPath"] == (
        "/etc/coriolis-bootstrap-coriolis"
    )
    assert mounts["coriolis-credentials"]["readOnly"] is True
    assert mounts["tmp"]["mountPath"] == "/tmp"
    assert "readOnly" not in mounts["tmp"]

    mount_paths = [mount["mountPath"] for mount in container["volumeMounts"]]
    assert len(set(mount_paths)) == 5
    assert len(mount_paths) == 5
    for path in mount_paths:
        assert not any(
            path != other and other.startswith(path + "/") for other in mount_paths
        ), f"nested mount path: {path}"
    for name in ("script", "config", "infra-credentials", "coriolis-credentials"):
        assert mounts[name]["readOnly"] is True


def test_bootstrap_absent_creates_job_and_raises_running_before_marker() -> None:
    core_api = make_core_api()
    batch_api = make_batch_api(job=None)

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            batch_api=batch_api,
        )

    bootstrap_names = [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_config_map.call_args_list
    ]
    assert bootstrap_names[-1] == "example-common-bootstrap-v2"
    batch_api.create_namespaced_job.assert_called_once_with(
        namespace="operators", body=ANY
    )
    batch_api.patch_namespaced_job.assert_not_called()
    assert_no_marker_write(core_api)
    statuses = {item["type"]: item for item in excinfo.value.status["conditions"]}
    assert statuses["Progressing"]["status"] == "True"
    assert statuses["Progressing"]["reason"] == "BootstrapRunning"
    assert statuses["Reconciled"]["status"] == "False"
    assert statuses["Reconciled"]["reason"] == "BootstrapRunning"
    assert statuses["Ready"]["status"] == "False"
    assert statuses["Ready"]["reason"] == "RuntimeNotImplemented"
    assert statuses["Degraded"]["status"] == "False"
    assert statuses["Degraded"]["reason"] == "NotDegraded"


def test_bootstrap_active_requeues_without_marker_or_job_patch() -> None:
    core_api = make_core_api()
    active = succeeded_bootstrap_job()
    active["status"] = {}
    batch_api = make_batch_api(job=active)

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            batch_api=batch_api,
        )

    batch_api.create_namespaced_job.assert_not_called()
    batch_api.patch_namespaced_job.assert_not_called()
    assert_no_marker_write(core_api)
    statuses = {item["type"]: item for item in excinfo.value.status["conditions"]}
    assert statuses["Progressing"]["status"] == "True"
    assert statuses["Progressing"]["reason"] == "BootstrapRunning"
    assert statuses["Degraded"]["status"] == "False"
    assert statuses["Degraded"]["reason"] == "NotDegraded"


def test_bootstrap_succeeded_writes_marker_last_without_job_write() -> None:
    core_api = make_core_api()
    batch_api = make_batch_api()

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        batch_api=batch_api,
    )

    batch_api.create_namespaced_job.assert_not_called()
    batch_api.patch_namespaced_job.assert_not_called()
    marker_names = [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_config_map.call_args_list
    ]
    assert marker_names[-1] == "example-operator-state"
    statuses = {item["type"]: item for item in status["conditions"]}
    assert statuses["Reconciled"]["status"] == "True"
    assert statuses["Reconciled"]["reason"] == "Reconciled"
    assert statuses["Ready"]["status"] == "False"
    assert statuses["Ready"]["reason"] == "RuntimeNotImplemented"
    assert statuses["Degraded"]["status"] == "False"


def test_api_resources_are_not_applied_until_bootstrap_succeeds() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    active = succeeded_bootstrap_job()
    active["status"] = {}

    with pytest.raises(main.ReconcileRetry):
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            apps_api=apps_api,
            batch_api=make_batch_api(job=active),
        )

    created_services = [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_service.call_args_list
    ]
    created_deployments = [
        item.kwargs["body"]["metadata"]["name"]
        for item in apps_api.create_namespaced_deployment.call_args_list
    ]
    assert "example-coriolis-api" not in created_services
    assert "example-coriolis-api" not in created_deployments
    assert_no_marker_write(core_api)


@pytest.mark.parametrize("collision_kind", ["service", "deployment"])
def test_api_collision_is_mutation_free(collision_kind: str) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    api_service = build_api_service(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )
    api_deployment = build_api_deployment(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )
    collided = api_service if collision_kind == "service" else api_deployment
    collided["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "other"

    def read_service(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-coriolis-api" and collision_kind == "service":
            return collided
        raise _api_exception(404)

    def read_deployment(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-coriolis-api" and collision_kind == "deployment":
            return collided
        raise _api_exception(404)

    core_api.read_namespaced_service.side_effect = read_service
    apps_api.read_namespaced_deployment.side_effect = read_deployment

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        apps_api=apps_api,
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert "operators/example-coriolis-api" in status["conditions"][2]["message"]
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_managed_api_resources_use_guarded_ssa() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    service = build_api_service(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )
    deployment = build_api_deployment(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )
    service["metadata"]["resourceVersion"] = "31"
    deployment["metadata"]["resourceVersion"] = "32"

    def read_service(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-coriolis-api":
            return service
        raise _api_exception(404)

    def read_deployment(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-coriolis-api":
            return deployment
        raise _api_exception(404)

    core_api.read_namespaced_service.side_effect = read_service
    apps_api.read_namespaced_deployment.side_effect = read_deployment
    core_api.api_client.default_headers["Content-Type"] = "application/json"
    apps_api.api_client.default_headers["Content-Type"] = "application/json"

    reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        apps_api=apps_api,
    )

    service_call = core_api.patch_namespaced_service.call_args
    assert service_call.kwargs["name"] == "example-coriolis-api"
    assert service_call.kwargs["body"]["metadata"]["resourceVersion"] == "31"
    assert service_call.kwargs["field_manager"] == "coriolis-operator"
    assert service_call.kwargs["force"] is True
    deployment_call = apps_api.patch_namespaced_deployment.call_args
    assert deployment_call.kwargs["name"] == "example-coriolis-api"
    assert deployment_call.kwargs["body"]["metadata"]["resourceVersion"] == "32"
    assert deployment_call.kwargs["field_manager"] == "coriolis-operator"
    assert deployment_call.kwargs["force"] is True
    assert core_api.api_client.default_headers["Content-Type"] == "application/json"
    assert apps_api.api_client.default_headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("resource_kind", ["service", "deployment"])
def test_api_apply_failure_is_sanitized_and_skips_marker(
    resource_kind: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()

    def fail_api(*, body: dict, **_: object) -> None:
        if body["metadata"]["name"] == "example-coriolis-api":
            raise client.ApiException(status=409, reason="api-apply-sentinel")

    if resource_kind == "service":
        core_api.create_namespaced_service.side_effect = fail_api
    else:
        apps_api.create_namespaced_deployment.side_effect = fail_api

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            apps_api=apps_api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "api-apply-sentinel" not in repr(excinfo.value.status)
    assert_no_marker_write(core_api)


@pytest.mark.parametrize("resource_kind", ["service", "deployment"])
def test_managed_api_without_resource_version_retries_before_writes(
    resource_kind: str,
) -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    body = (
        build_api_service(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            owner=OWNER,
        )
        if resource_kind == "service"
        else build_api_deployment(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            owner=OWNER,
        )
    )

    def read_service(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-coriolis-api" and resource_kind == "service":
            return body
        raise _api_exception(404)

    def read_deployment(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-coriolis-api" and resource_kind == "deployment":
            return body
        raise _api_exception(404)

    core_api.read_namespaced_service.side_effect = read_service
    apps_api.read_namespaced_deployment.side_effect = read_deployment

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            apps_api=apps_api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_conductor_collision_is_mutation_free() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    conductor = build_conductor_deployment(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )
    collided = copy.deepcopy(conductor)
    collided["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "other"

    def read_deployment(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-coriolis-conductor":
            return collided
        raise _api_exception(404)

    apps_api.read_namespaced_deployment.side_effect = read_deployment

    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert "operators/example-coriolis-conductor" in status["conditions"][2]["message"]
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_managed_conductor_uses_guarded_ssa() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    conductor = build_conductor_deployment(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )
    conductor["metadata"]["resourceVersion"] = "40"

    def read_deployment(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-coriolis-conductor":
            return conductor
        raise _api_exception(404)

    apps_api.read_namespaced_deployment.side_effect = read_deployment
    apps_api.api_client.default_headers["Content-Type"] = "application/json"

    reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
    )

    conductor_call = apps_api.patch_namespaced_deployment.call_args
    assert conductor_call.kwargs["name"] == "example-coriolis-conductor"
    assert conductor_call.kwargs["body"]["metadata"]["resourceVersion"] == "40"
    assert conductor_call.kwargs["field_manager"] == "coriolis-operator"
    assert conductor_call.kwargs["force"] is True
    assert apps_api.api_client.default_headers["Content-Type"] == "application/json"


def test_conductor_apply_failure_is_sanitized_and_skips_marker() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()

    def fail_conductor(*, body: dict, **_: object) -> None:
        if body["metadata"]["name"] == "example-coriolis-conductor":
            raise client.ApiException(status=409, reason="conductor-apply-sentinel")

    apps_api.create_namespaced_deployment.side_effect = fail_conductor

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "conductor-apply-sentinel" not in repr(excinfo.value.status)
    assert_no_marker_write(core_api)


def test_managed_conductor_without_resource_version_retries_before_writes() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    conductor = build_conductor_deployment(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )

    def read_deployment(*, name: str, namespace: str) -> object:
        assert namespace == "operators"
        if name == "example-coriolis-conductor":
            return conductor
        raise _api_exception(404)

    apps_api.read_namespaced_deployment.side_effect = read_deployment

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(), meta=sample_meta(), core_api=core_api, apps_api=apps_api
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert api_writes(core_api) == []
    assert api_writes(apps_api) == []


def test_bootstrap_active_creates_no_conductor() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    active = succeeded_bootstrap_job()
    active["status"] = {}

    with pytest.raises(main.ReconcileRetry):
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            apps_api=apps_api,
            batch_api=make_batch_api(job=active),
        )

    created_deployments = [
        item.kwargs["body"]["metadata"]["name"]
        for item in apps_api.create_namespaced_deployment.call_args_list
    ]
    assert "example-coriolis-conductor" not in created_deployments
    assert_no_marker_write(core_api)


def test_bootstrap_failed_creates_no_conductor() -> None:
    core_api = make_core_api()
    apps_api = make_apps_api()
    failed = succeeded_bootstrap_job()
    failed["status"] = {"conditions": [{"type": "Failed", "status": "True"}]}

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        apps_api=apps_api,
        batch_api=make_batch_api(job=failed),
    )

    created_deployments = [
        item.kwargs["body"]["metadata"]["name"]
        for item in apps_api.create_namespaced_deployment.call_args_list
    ]
    assert "example-coriolis-conductor" not in created_deployments
    assert status["conditions"][2]["reason"] == "BootstrapFailed"
    assert status["conditions"][4]["status"] == "True"
    assert_no_marker_write(core_api)


def test_bootstrap_terminal_failed_is_stable_degraded_without_marker() -> None:
    core_api = make_core_api()
    failed = succeeded_bootstrap_job()
    failed["status"] = {"conditions": [{"type": "Failed", "status": "True"}]}
    batch_api = make_batch_api(job=failed)

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        batch_api=batch_api,
    )

    batch_api.create_namespaced_job.assert_not_called()
    batch_api.patch_namespaced_job.assert_not_called()
    assert_no_marker_write(core_api)
    statuses = {item["type"]: item for item in status["conditions"]}
    assert statuses["Degraded"]["status"] == "True"
    assert statuses["Degraded"]["reason"] == "BootstrapFailed"
    assert statuses["Reconciled"]["status"] == "False"
    assert statuses["Reconciled"]["reason"] == "BootstrapFailed"
    assert statuses["Ready"]["status"] == "False"
    assert statuses["Progressing"]["status"] == "False"


def test_bootstrap_config_map_collision_has_zero_writes() -> None:
    core_api = make_core_api()
    collided = bootstrap_config_map_body()
    collided["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "other"
    core_api.read_namespaced_config_map.side_effect = [
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        collided,
    ]
    batch_api = make_batch_api(job=None)

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        batch_api=batch_api,
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert "operators/example-common-bootstrap-v2" in status["conditions"][2]["message"]
    assert api_writes(core_api) == []
    batch_api.create_namespaced_job.assert_not_called()


def _drift_bootstrap_job(mutate) -> dict:
    body = succeeded_bootstrap_job()
    mutate(body)
    return body


JOB_DRIFT_MUTATORS = [
    (
        "metadata-annotation",
        lambda b: b["metadata"]["annotations"].update(
            {BOOTSTRAP_TEMPLATE_ANNOTATION: "different"}
        ),
    ),
    (
        "pod-template-annotation",
        lambda b: b["spec"]["template"]["metadata"]["annotations"].update(
            {BOOTSTRAP_TEMPLATE_ANNOTATION: "different"}
        ),
    ),
    (
        "script-metadata-annotation",
        lambda b: b["metadata"]["annotations"].update(
            {BOOTSTRAP_SCRIPT_ANNOTATION: "different"}
        ),
    ),
    (
        "script-template-annotation",
        lambda b: b["spec"]["template"]["metadata"]["annotations"].update(
            {BOOTSTRAP_SCRIPT_ANNOTATION: "different"}
        ),
    ),
    ("backoffLimit", lambda b: b["spec"].update(backoffLimit=5)),
    ("activeDeadlineSeconds", lambda b: b["spec"].update(activeDeadlineSeconds=999)),
    ("completions", lambda b: b["spec"].update(completions=2)),
    ("parallelism", lambda b: b["spec"].update(parallelism=2)),
    (
        "image",
        lambda b: b["spec"]["template"]["spec"]["containers"][0].update(
            image="cr.example/other:tag"
        ),
    ),
    (
        "command",
        lambda b: b["spec"]["template"]["spec"]["containers"][0].update(
            command=["other"]
        ),
    ),
    (
        "env",
        lambda b: b["spec"]["template"]["spec"]["containers"][0]["env"].append(
            {"name": "EXTRA", "value": "x"}
        ),
    ),
    (
        "container-security",
        lambda b: b["spec"]["template"]["spec"]["containers"][0][
            "securityContext"
        ].update(runAsNonRoot=False),
    ),
    (
        "pod-security",
        lambda b: b["spec"]["template"]["spec"]["securityContext"].update(runAsUser=1),
    ),
    (
        "mounts",
        lambda b: b["spec"]["template"]["spec"]["containers"][0]["volumeMounts"].append(
            {"name": "tmp", "mountPath": "/tmp2"}
        ),
    ),
    (
        "volume-source",
        lambda b: b["spec"]["template"]["spec"]["volumes"][1]["configMap"].update(
            name="other-config"
        ),
    ),
    (
        "volume-items",
        lambda b: b["spec"]["template"]["spec"]["volumes"][1]["configMap"]["items"][
            0
        ].update(mode=0o755),
    ),
    (
        "pull-secret",
        lambda b: b["spec"]["template"]["spec"]["imagePullSecrets"][0].update(
            name="other-secret"
        ),
    ),
    (
        "labels",
        lambda b: b["spec"]["template"]["metadata"]["labels"].update(
            {"app.kubernetes.io/component": "other"}
        ),
    ),
]


@pytest.mark.parametrize(
    "mutate",
    [case[1] for case in JOB_DRIFT_MUTATORS],
    ids=[case[0] for case in JOB_DRIFT_MUTATORS],
)
def test_bootstrap_job_drift_is_collision_with_no_writes(mutate) -> None:
    core_api = make_core_api()
    batch_api = make_batch_api(job=_drift_bootstrap_job(mutate))

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        batch_api=batch_api,
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert "operators/example-common-bootstrap-v2" in status["conditions"][2]["message"]
    assert api_writes(core_api) == []
    batch_api.create_namespaced_job.assert_not_called()
    batch_api.patch_namespaced_job.assert_not_called()


def test_bootstrap_job_identity_collision_is_mutation_free() -> None:
    core_api = make_core_api()
    collided = succeeded_bootstrap_job()
    collided["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"
    batch_api = make_batch_api(job=collided)

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        batch_api=batch_api,
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert api_writes(core_api) == []
    batch_api.patch_namespaced_job.assert_not_called()


def test_bootstrap_job_script_change_is_collision_without_writes() -> None:
    different_script = render_bootstrap_script(
        coriolis_api_host="example-coriolis-api",
        rabbitmq_host="different-rabbitmq",
        memcached_host="example-memcached",
        database_host="example-mariadb",
        keystone_host="example-keystone",
    )
    existing = build_common_bootstrap_job(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
        script=different_script,
    )
    existing["metadata"]["resourceVersion"] = "1"
    core_api = make_core_api()
    batch_api = make_batch_api(job=existing)

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        batch_api=batch_api,
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert api_writes(core_api) == []
    batch_api.patch_namespaced_job.assert_not_called()


def test_bootstrap_config_map_immutable_drift_is_collision_with_no_writes() -> None:
    core_api = make_core_api()
    collided = bootstrap_config_map_body()
    collided["immutable"] = False
    core_api.read_namespaced_config_map.side_effect = [
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        collided,
    ]
    batch_api = make_batch_api()

    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, batch_api=batch_api
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert "operators/example-common-bootstrap-v2" in status["conditions"][2]["message"]
    assert api_writes(core_api) == []
    batch_api.create_namespaced_job.assert_not_called()


def test_bootstrap_config_map_data_drift_is_collision_with_no_writes() -> None:
    core_api = make_core_api()
    collided = bootstrap_config_map_body()
    collided["data"] = {"bootstrap.py": "corrupted script"}
    core_api.read_namespaced_config_map.side_effect = [
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        collided,
    ]
    batch_api = make_batch_api()

    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, batch_api=batch_api
    )

    assert status["conditions"][2]["reason"] == "ResourceCollision"
    assert api_writes(core_api) == []
    batch_api.create_namespaced_job.assert_not_called()


def test_bootstrap_config_map_managed_reuse_is_no_write() -> None:
    core_api = make_core_api()
    existing = bootstrap_config_map_body()
    existing["metadata"]["resourceVersion"] = "5"
    core_api.read_namespaced_config_map.side_effect = [
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        _api_exception(404),
        existing,
    ]
    batch_api = make_batch_api()

    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, batch_api=batch_api
    )

    created_names = [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_config_map.call_args_list
    ]
    assert "example-common-bootstrap-v2" not in created_names
    patched_names = [
        item.kwargs["name"]
        for item in core_api.patch_namespaced_config_map.call_args_list
    ]
    assert "example-common-bootstrap-v2" not in patched_names
    assert created_names[-1] == "example-operator-state"
    statuses = {item["type"]: item for item in status["conditions"]}
    assert statuses["Reconciled"]["status"] == "True"


def test_bootstrap_config_map_is_never_patched() -> None:
    core_api = make_core_api()
    batch_api = make_batch_api()

    reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, batch_api=batch_api
    )

    patched_names = [
        item.kwargs["name"]
        for item in core_api.patch_namespaced_config_map.call_args_list
    ]
    assert "example-common-bootstrap-v2" not in patched_names


def test_bootstrap_non_404_read_error_requests_sanitized_retry() -> None:
    core_api = make_core_api()
    batch_api = make_batch_api()
    batch_api.read_namespaced_job.side_effect = client.ApiException(
        status=500, reason="bootstrap-read-sentinel"
    )

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            batch_api=batch_api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceReadFailed"
    assert "bootstrap-read-sentinel" not in repr(excinfo.value.status)
    assert api_writes(core_api) == []
    batch_api.create_namespaced_job.assert_not_called()


def test_bootstrap_job_create_failure_is_sanitized_and_skips_marker() -> None:
    core_api = make_core_api()
    batch_api = make_batch_api(job=None)
    batch_api.create_namespaced_job.side_effect = client.ApiException(
        status=409, reason="bootstrap-create-sentinel"
    )

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            batch_api=batch_api,
        )

    assert excinfo.value.status["conditions"][2]["reason"] == "ResourceApplyFailed"
    assert "bootstrap-create-sentinel" not in repr(excinfo.value.status)
    assert_no_marker_write(core_api)


def test_bootstrap_job_reuse_performs_no_retained_writes() -> None:
    core_api = make_core_api()
    reused = succeeded_bootstrap_job()
    reused["metadata"]["resourceVersion"] = "5"
    batch_api = make_batch_api(job=reused)

    status = reconcile_appliance(
        spec=valid_spec(),
        meta=sample_meta(),
        core_api=core_api,
        batch_api=batch_api,
    )

    batch_api.create_namespaced_job.assert_not_called()
    batch_api.patch_namespaced_job.assert_not_called()
    assert status["acceptedVersion"] == "2603.4"


def test_bootstrap_long_names_derive_resource_and_api_host() -> None:
    appliance_name = f"{'a' * 40}.{'b' * 40}.{'c' * 40}." + "d" * 40
    job = build_common_bootstrap_job(
        appliance_name=appliance_name,
        namespace="operators",
        accepted_version="2603.4",
        owner=dict(OWNER, name=appliance_name, uid="abc-123"),
        script=_bootstrap_script(appliance_name),
    )
    config_map = build_common_bootstrap_config_map(
        appliance_name=appliance_name,
        namespace="operators",
        accepted_version="2603.4",
        owner=dict(OWNER, name=appliance_name, uid="abc-123"),
        script=_bootstrap_script(appliance_name),
    )
    resource_name = appliance_resource_name(appliance_name, BOOTSTRAP_COMPONENT)
    assert job["metadata"]["name"] == resource_name
    assert config_map["metadata"]["name"] == resource_name
    api_host = appliance_resource_name(appliance_name, "coriolis-api")
    assert (
        f"http://{api_host}:7667/v1/%(tenant_id)s" in config_map["data"]["bootstrap.py"]
    )


def test_bootstrap_job_state_model_active() -> None:
    job = client.V1Job(status=client.V1JobStatus())
    assert _bootstrap_job_state(job) == "active"


def test_bootstrap_job_state_model_succeeded_with_complete_condition() -> None:
    job = client.V1Job(
        status=client.V1JobStatus(
            succeeded=1,
            conditions=[client.V1JobCondition(type="Complete", status="True")],
        )
    )
    assert _bootstrap_job_state(job) == "succeeded"


def test_bootstrap_job_state_model_failed() -> None:
    job = client.V1Job(
        status=client.V1JobStatus(
            conditions=[client.V1JobCondition(type="Failed", status="True")]
        )
    )
    assert _bootstrap_job_state(job) == "failed"


def test_bootstrap_job_state_failed_precedes_partial_success() -> None:
    job = client.V1Job(
        status=client.V1JobStatus(
            succeeded=0,
            failed=2,
            conditions=[client.V1JobCondition(type="Failed", status="True")],
        )
    )
    assert _bootstrap_job_state(job) == "failed"


def test_bootstrap_job_state_ignores_non_terminal_failed_condition() -> None:
    job = client.V1Job(
        status=client.V1JobStatus(
            failed=1,
            conditions=[client.V1JobCondition(type="Failed", status="False")],
        )
    )
    assert _bootstrap_job_state(job) == "active"


def test_bootstrap_job_state_dict_failed_condition_status_robustness() -> None:
    assert _bootstrap_job_state({"status": {"succeeded": 1}}) == "succeeded"
    assert (
        _bootstrap_job_state(
            {"status": {"conditions": [{"type": "Failed", "status": "True"}]}}
        )
        == "failed"
    )
    assert (
        _bootstrap_job_state(
            {"status": {"conditions": [{"type": "Failed", "status": "False"}]}}
        )
        == "active"
    )


def test_bootstrap_succeeded_v1_job_model_writes_marker_last() -> None:
    core_api = make_core_api()
    job = to_v1_bootstrap_job(succeeded_bootstrap_job(), status={"succeeded": 1})
    batch_api = make_batch_api(job=job)

    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, batch_api=batch_api
    )

    batch_api.create_namespaced_job.assert_not_called()
    batch_api.patch_namespaced_job.assert_not_called()
    marker_names = [
        item.kwargs["body"]["metadata"]["name"]
        for item in core_api.create_namespaced_config_map.call_args_list
    ]
    assert marker_names[-1] == "example-operator-state"
    statuses = {item["type"]: item for item in status["conditions"]}
    assert statuses["Reconciled"]["status"] == "True"


def test_bootstrap_active_v1_job_model_requeues_without_marker() -> None:
    core_api = make_core_api()
    job = to_v1_bootstrap_job(succeeded_bootstrap_job(), status={})
    batch_api = make_batch_api(job=job)

    with pytest.raises(main.ReconcileRetry) as excinfo:
        reconcile_appliance(
            spec=valid_spec(),
            meta=sample_meta(),
            core_api=core_api,
            batch_api=batch_api,
        )

    statuses = {item["type"]: item for item in excinfo.value.status["conditions"]}
    assert statuses["Progressing"]["reason"] == "BootstrapRunning"
    assert statuses["Degraded"]["status"] == "False"
    assert_no_marker_write(core_api)


def test_bootstrap_failed_v1_job_model_is_degraded_without_marker() -> None:
    core_api = make_core_api()
    job = to_v1_bootstrap_job(
        succeeded_bootstrap_job(),
        status={"conditions": [{"type": "Failed", "status": "True"}]},
    )
    batch_api = make_batch_api(job=job)

    status = reconcile_appliance(
        spec=valid_spec(), meta=sample_meta(), core_api=core_api, batch_api=batch_api
    )

    statuses = {item["type"]: item for item in status["conditions"]}
    assert statuses["Degraded"]["status"] == "True"
    assert statuses["Degraded"]["reason"] == "BootstrapFailed"
    assert statuses["Reconciled"]["status"] == "False"
    assert_no_marker_write(core_api)
