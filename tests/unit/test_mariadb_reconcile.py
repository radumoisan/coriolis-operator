import base64
import copy
import json
import os
import subprocess
from pathlib import Path
from typing import Any, TypedDict

import pytest
from kubernetes import client

from coriolis_operator import reconcile
from coriolis_operator.mariadb import (
    MARIADB_CONFIG_KEYS,
    MARIADB_IMAGE,
    MARIADB_SECRET_CONFIG_KEYS,
    MariaDBSettings,
    SensitiveMariaDBCredentials,
    resolve_mariadb_settings,
)
from coriolis_operator.reconcile import (
    OwnedClassification,
    RetainedClassification,
    build_mariadb_config_map,
    build_mariadb_config_secret,
    build_mariadb_data_pvc,
    build_mariadb_stateful_set,
    classify_mariadb_data_pvc,
    preflight_mariadb_resources,
)

OWNER = {
    "apiVersion": "coriolis.cloudbase.it/v1alpha1",
    "kind": "CoriolisAppliance",
    "name": "example",
    "uid": "abc-123",
}


class MariaDBArguments(TypedDict):
    appliance_name: str
    namespace: str
    accepted_version: str


def settings() -> MariaDBSettings:
    return resolve_mariadb_settings(
        storage={"mariadb": {"storageClassName": "standard", "size": "1Gi"}},
        resources={
            "mariadb": {
                "requests": {"cpu": "500m", "memory": "512Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            }
        },
    )


def credentials() -> SensitiveMariaDBCredentials:
    return SensitiveMariaDBCredentials(
        database_password="ADMIN_SENTINEL",
        coriolis_database_password="CORIOLIS_SENTINEL",
    )


def kwargs() -> MariaDBArguments:
    return {
        "appliance_name": "example",
        "namespace": "operators",
        "accepted_version": "2603.4",
    }


def test_mariadb_builders_have_exact_pvc_config_secret_and_statefulset_contract(
    tmp_path: Path,
) -> None:
    values = {
        "my.cnf": "config",
        "prepare-mariadb.sh": "prepare",
        "start-mariadb.sh": "start",
    }
    secret_values = {
        "admin.cnf": "ADMIN_SENTINEL",
        "coriolis.cnf": "CORIOLIS_SENTINEL",
        "bootstrap.sql": "bootstrap",
    }
    pvc = build_mariadb_data_pvc(**kwargs(), settings=settings())
    config = build_mariadb_config_map(**kwargs(), owner=OWNER, values=values)
    secret = build_mariadb_config_secret(**kwargs(), owner=OWNER, values=secret_values)
    stateful_set = build_mariadb_stateful_set(
        **kwargs(), owner=OWNER, settings=settings()
    )

    assert pvc["apiVersion"] == "v1"
    assert pvc["kind"] == "PersistentVolumeClaim"
    assert pvc["metadata"]["name"] == "example-mariadb-data"
    assert (
        pvc["metadata"]["annotations"]["coriolis.cloudbase.it/retention"]
        == "mariadb-data"
    )
    assert pvc["metadata"]["labels"]["app.kubernetes.io/component"] == "mariadb-data"
    assert (
        pvc["metadata"]["labels"]["coriolis.cloudbase.it/component"] == "mariadb-data"
    )
    assert "ownerReferences" not in pvc["metadata"]
    assert pvc["spec"] == {
        "storageClassName": "standard",
        "accessModes": ["ReadWriteOnce"],
        "volumeMode": "Filesystem",
        "resources": {"requests": {"storage": "1Gi"}},
    }
    assert config["metadata"]["name"] == "example-mariadb-config"
    assert set(config["data"]) == MARIADB_CONFIG_KEYS
    assert config["data"] == values
    assert secret["metadata"]["name"] == "example-mariadb-config-secret"
    assert secret["type"] == "Opaque"
    assert set(secret["data"]) == MARIADB_SECRET_CONFIG_KEYS
    assert "stringData" not in secret
    assert {
        key: base64.b64decode(value).decode() for key, value in secret["data"].items()
    } == secret_values
    for body, component in (
        (config, "mariadb-config"),
        (secret, "mariadb-config-secret"),
        (stateful_set, "mariadb"),
    ):
        assert body["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
        assert body["metadata"]["labels"]["app.kubernetes.io/component"] == component
        assert (
            body["metadata"]["labels"]["coriolis.cloudbase.it/component"] == component
        )

    spec = stateful_set["spec"]
    template = spec["template"]
    pod = template["spec"]
    main = pod["containers"][0]
    init = pod["initContainers"][0]
    assert stateful_set["apiVersion"] == "apps/v1"
    assert stateful_set["kind"] == "StatefulSet"
    assert stateful_set["metadata"]["name"] == "example-mariadb"
    assert spec["serviceName"] == "example-mariadb"
    assert spec["replicas"] == 1
    assert spec["selector"]["matchLabels"] == {
        "coriolis.cloudbase.it/appliance": "example",
        "coriolis.cloudbase.it/component": "mariadb",
    }
    assert template["metadata"]["labels"] == stateful_set["metadata"]["labels"]
    assert (
        spec["selector"]["matchLabels"].items()
        <= template["metadata"]["labels"].items()
    )
    assert "volumeClaimTemplates" not in spec
    assert pod["imagePullSecrets"] == [{"name": "coriolis-appliance-registry"}]
    assert pod["securityContext"] == {
        "runAsUser": 42434,
        "runAsGroup": 42434,
        "fsGroup": 42434,
        "fsGroupChangePolicy": "OnRootMismatch",
        "supplementalGroups": [42400],
    }
    assert pod["terminationGracePeriodSeconds"] == 30
    assert main["image"] == init["image"] == MARIADB_IMAGE
    assert main["args"] == ["/etc/mariadb/start-mariadb.sh"]
    assert init["args"] == ["/etc/mariadb/prepare-mariadb.sh"]
    assert "command" not in main and "command" not in init
    assert "env" not in main and "env" not in init
    assert main["resources"] == {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    }
    assert main["ports"] == [
        {"name": "mariadb", "containerPort": 3306, "protocol": "TCP"}
    ]
    for container in (init, main):
        assert container["securityContext"] == {
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        }
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["data"] == {
        "name": "data",
        "persistentVolumeClaim": {"claimName": "example-mariadb-data"},
    }
    assert volumes["runtime"] == {"name": "runtime", "emptyDir": {}}
    assert volumes["tmp"] == {"name": "tmp", "emptyDir": {}}
    assert {
        item["key"] for item in volumes["config"]["configMap"]["items"]
    } == MARIADB_CONFIG_KEYS
    assert {
        item["key"]: item["path"] for item in volumes["config"]["configMap"]["items"]
    } == {key: key for key in MARIADB_CONFIG_KEYS}
    assert {
        item["key"]: item["mode"] for item in volumes["config"]["configMap"]["items"]
    } == {
        "my.cnf": 0o444,
        "prepare-mariadb.sh": 0o555,
        "start-mariadb.sh": 0o555,
    }
    assert {
        item["key"]: item["mode"] for item in volumes["secret"]["secret"]["items"]
    } == {key: 0o440 for key in MARIADB_SECRET_CONFIG_KEYS}
    assert {
        item["key"]: item["path"] for item in volumes["secret"]["secret"]["items"]
    } == {key: key for key in MARIADB_SECRET_CONFIG_KEYS}
    assert {mount["mountPath"] for mount in init["volumeMounts"]} == {
        "/var/lib/mysql",
        "/run/mysqld",
        "/tmp",
        "/etc/mariadb",
        "/etc/mariadb-secret",
    }
    assert "/etc/mariadb-secret" not in {
        mount["mountPath"] for mount in main["volumeMounts"]
    }
    assert "secret" not in {mount["name"] for mount in main["volumeMounts"]}
    assert all(
        "subPath" not in mount
        for container in (init, main)
        for mount in container["volumeMounts"]
    )
    for probe, failure in (
        (main["startupProbe"], 30),
        (main["readinessProbe"], 3),
        (main["livenessProbe"], 6),
    ):
        assert probe["periodSeconds"] == 10
        assert probe["timeoutSeconds"] == 5
        assert probe["failureThreshold"] == failure
    assert main["readinessProbe"]["successThreshold"] == 1
    startup_command = main["startupProbe"]["exec"]["command"][-1]
    readiness_command = main["readinessProbe"]["exec"]["command"][-1]
    liveness_command = main["livenessProbe"]["exec"]["command"][-1]
    assert "bootstrap-complete" in startup_command
    for command in (startup_command, liveness_command):
        assert "mariadb-admin --defaults-file=/run/mysqld/admin.cnf" in command
        assert (
            "ping --silent && mariadb --defaults-file=/run/mysqld/admin.cnf" in command
        )
        assert "--execute=SELECT\\ 1" in command
    assert "mariadb-admin" not in readiness_command
    assert "mariadb --defaults-file=/run/mysqld/coriolis.cnf" in readiness_command
    assert "--execute=SELECT\\ 1" in readiness_command
    probe_binaries = tmp_path / "probe-bin"
    probe_binaries.mkdir()
    for name, content in (
        ("mariadb-admin", "#!/bin/sh\nexit 0\n"),
        ("mariadb", "#!/bin/sh\nexit 1\n"),
    ):
        binary = probe_binaries / name
        binary.write_text(content)
        binary.chmod(0o755)
    marker = tmp_path / "bootstrap-complete"
    marker.touch()
    for command in (startup_command, liveness_command):
        result = subprocess.run(
            [
                "/bin/sh",
                "-ec",
                command.replace("/run/mysqld/bootstrap-complete", str(marker)),
            ],
            check=False,
            env={**os.environ, "PATH": f"{probe_binaries}:{os.environ['PATH']}"},
            text=True,
        )
        assert result.returncode != 0
    non_secret_manifests = json.dumps((pvc, config, stateful_set))
    stateful_set_values = json.dumps(stateful_set)
    for sentinel in ("ADMIN_SENTINEL", "CORIOLIS_SENTINEL"):
        assert sentinel not in non_secret_manifests
        assert sentinel not in stateful_set_values


def test_mariadb_builders_validate_keys_and_do_not_mutate_inputs() -> None:
    values = {key: key for key in MARIADB_CONFIG_KEYS}
    secret_values = {key: key for key in MARIADB_SECRET_CONFIG_KEYS}
    before = copy.deepcopy((values, secret_values))
    build_mariadb_config_map(**kwargs(), owner=OWNER, values=values)
    build_mariadb_config_secret(**kwargs(), owner=OWNER, values=secret_values)
    assert (values, secret_values) == before
    for builder, valid in (
        (build_mariadb_config_map, values),
        (build_mariadb_config_secret, secret_values),
    ):
        invalid = dict(valid, unexpected="value")
        with pytest.raises(ValueError):
            builder(**kwargs(), owner=OWNER, values=invalid)


def test_mariadb_pvc_classifier_reuses_equivalent_bound_model() -> None:
    pvc = build_mariadb_data_pvc(**kwargs(), settings=settings())
    existing = copy.deepcopy(pvc)
    existing["spec"]["resources"]["requests"]["storage"] = "1024Mi"
    existing["spec"]["volumeName"] = "pvc-bound"
    existing["status"] = {"phase": "Bound", "capacity": {"storage": "1Gi"}}
    assert (
        classify_mariadb_data_pvc(**kwargs(), settings=settings(), existing=existing)
        is RetainedClassification.REUSE
    )

    model = client.V1PersistentVolumeClaim(
        api_version="v1",
        kind="PersistentVolumeClaim",
        metadata=client.V1ObjectMeta(
            name="example-mariadb-data",
            namespace="operators",
            labels=pvc["metadata"]["labels"],
            annotations=pvc["metadata"]["annotations"],
        ),
        spec=client.V1PersistentVolumeClaimSpec(
            storage_class_name="standard",
            access_modes=["ReadWriteOnce"],
            volume_mode="Filesystem",
            volume_name="pvc-bound",
            resources=client.V1ResourceRequirements(requests={"storage": "1024Mi"}),
        ),
    )
    assert (
        classify_mariadb_data_pvc(**kwargs(), settings=settings(), existing=model)
        is RetainedClassification.REUSE
    )


def test_mariadb_pvc_classifier_rejects_volume_attributes_class_drift() -> None:
    pvc = build_mariadb_data_pvc(**kwargs(), settings=settings())
    model = client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(
            name="example-mariadb-data",
            namespace="operators",
            labels=pvc["metadata"]["labels"],
            annotations=pvc["metadata"]["annotations"],
        ),
        spec=client.V1PersistentVolumeClaimSpec(
            storage_class_name="standard",
            access_modes=["ReadWriteOnce"],
            volume_mode="Filesystem",
            resources=client.V1ResourceRequirements(requests={"storage": "1Gi"}),
        ),
    )
    if "volume_attributes_class_name" in model.spec.openapi_types:
        model.spec.volume_attributes_class_name = "fast"
        existing: object = model
    else:

        class PersistentVolumeClaimSpecFake:
            storage_class_name = "standard"
            access_modes = ["ReadWriteOnce"]
            volume_mode = "Filesystem"
            resources = {"requests": {"storage": "1Gi"}}
            selector = None
            data_source = None
            data_source_ref = None
            volume_attributes_class_name = "fast"

        existing = dict(pvc, spec=PersistentVolumeClaimSpecFake())

    assert (
        classify_mariadb_data_pvc(**kwargs(), settings=settings(), existing=existing)
        is RetainedClassification.COLLISION
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["spec"].update({"storageClassName": "other"}),
        lambda value: value["spec"].update({"accessModes": ["ReadWriteMany"]}),
        lambda value: value["spec"].update({"volumeMode": "Block"}),
        lambda value: value["spec"]["resources"]["requests"].update({"storage": "2Gi"}),
        lambda value: value["spec"]["resources"].update({"limits": {"storage": "1Gi"}}),
        lambda value: value["spec"].update({"selector": {}}),
        lambda value: value["spec"].update({"dataSource": {"name": "source"}}),
        lambda value: value["spec"].update({"unexpected": "value"}),
    ],
)
def test_mariadb_pvc_classifier_rejects_immutable_spec_drift(
    mutate: Any,
) -> None:
    existing = build_mariadb_data_pvc(**kwargs(), settings=settings())
    mutate(existing)
    assert (
        classify_mariadb_data_pvc(**kwargs(), settings=settings(), existing=existing)
        is RetainedClassification.COLLISION
    )


def test_mariadb_preflight_short_circuits_before_sensitive_rendering_and_bodies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original_pvc = reconcile.classify_mariadb_data_pvc
    original_owned = reconcile.classify_owned_resource

    def tracked_pvc(**arguments: Any) -> RetainedClassification:
        calls.append("pvc")
        return original_pvc(**arguments)

    def tracked_owned(**arguments: Any) -> OwnedClassification:
        calls.append(arguments["component"])
        return original_owned(**arguments)

    monkeypatch.setattr(reconcile, "classify_mariadb_data_pvc", tracked_pvc)
    monkeypatch.setattr(reconcile, "classify_owned_resource", tracked_owned)
    result = preflight_mariadb_resources(
        **kwargs(),
        settings=settings(),
        credentials=credentials(),
        owner=OWNER,
        mariadb_data_pvc=None,
        mariadb_config_map=None,
        mariadb_config_secret=None,
        mariadb_stateful_set=None,
    )
    assert calls == ["pvc", "mariadb-config", "mariadb-config-secret", "mariadb"]
    assert list(result.classifications.values()) == [
        RetainedClassification.ABSENT,
        OwnedClassification.ABSENT,
        OwnedClassification.ABSENT,
        OwnedClassification.ABSENT,
    ]
    assert [body["kind"] for body in result.manifests] == [
        "PersistentVolumeClaim",
        "ConfigMap",
        "Secret",
        "StatefulSet",
    ]
    rendered = json.dumps(result.manifests)
    assert "ADMIN_SENTINEL" not in rendered and "CORIOLIS_SENTINEL" not in rendered
    assert "ADMIN_SENTINEL" not in repr(result)
    decoded = base64.b64decode(result.manifests[2]["data"]["admin.cnf"]).decode()
    assert "ADMIN_SENTINEL" in decoded

    monkeypatch.setattr(
        reconcile,
        "classify_mariadb_data_pvc",
        lambda **_: RetainedClassification.COLLISION,
    )
    monkeypatch.setattr(
        reconcile,
        "render_sensitive_mariadb_config",
        lambda **_: pytest.fail("sensitive rendering must not run"),
    )
    collision = preflight_mariadb_resources(
        **kwargs(),
        settings=settings(),
        credentials=credentials(),
        owner=OWNER,
        mariadb_data_pvc=None,
        mariadb_config_map=None,
        mariadb_config_secret=None,
        mariadb_stateful_set=None,
    )
    assert list(collision.classifications.values()) == [
        RetainedClassification.COLLISION
    ]
    assert collision.manifests == ()
