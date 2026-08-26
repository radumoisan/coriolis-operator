import copy

from coriolis_operator.common import CONDUCTOR_IMAGE
from coriolis_operator.conductor import (
    CONDUCTOR_ARGS,
    CONDUCTOR_COMMAND,
    CONDUCTOR_COMPONENT,
    CONDUCTOR_CONFIG_DIR,
    CONDUCTOR_CONFIG_MAP_KEYS,
    CONDUCTOR_LOCKS_DIR,
    CONDUCTOR_LOG_DIR,
    CONDUCTOR_RUN_AS_ID,
)
from coriolis_operator.reconcile import (
    OwnedClassification,
    appliance_identity,
    appliance_resource_name,
    build_conductor_deployment,
    preflight_conductor_resource,
)

OWNER = {
    "apiVersion": "coriolis.cloudbase.it/v1alpha1",
    "kind": "CoriolisAppliance",
    "name": "example",
    "uid": "abc-123",
}


def kwargs() -> dict[str, object]:
    return {
        "appliance_name": "example",
        "namespace": "operators",
        "accepted_version": "2603.4",
        "owner": OWNER,
    }


def test_conductor_deployment_has_frozen_runtime_contract() -> None:
    deployment = build_conductor_deployment(**kwargs())

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "example-coriolis-conductor"
    assert deployment["metadata"]["namespace"] == "operators"
    assert deployment["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert (
        deployment["metadata"]["labels"]["app.kubernetes.io/component"]
        == CONDUCTOR_COMPONENT
    )
    assert deployment["metadata"]["labels"]["coriolis.cloudbase.it/component"] == (
        CONDUCTOR_COMPONENT
    )
    assert deployment["metadata"]["annotations"] == {
        "coriolis.cloudbase.it/appliance-name": "example"
    }

    spec = deployment["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]
    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "Recreate"}
    assert spec["selector"] == {
        "matchLabels": {
            "coriolis.cloudbase.it/appliance": "example",
            "coriolis.cloudbase.it/component": CONDUCTOR_COMPONENT,
        }
    }
    assert spec["template"]["metadata"]["labels"] == deployment["metadata"]["labels"]
    assert pod["imagePullSecrets"] == [{"name": "coriolis-appliance-registry"}]
    assert pod["securityContext"] == {
        "runAsUser": CONDUCTOR_RUN_AS_ID,
        "runAsGroup": CONDUCTOR_RUN_AS_ID,
        "fsGroup": CONDUCTOR_RUN_AS_ID,
        "fsGroupChangePolicy": "OnRootMismatch",
    }
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["terminationGracePeriodSeconds"] == 45

    assert container["name"] == CONDUCTOR_COMPONENT
    assert container["image"] == CONDUCTOR_IMAGE
    assert container["command"] == [CONDUCTOR_COMMAND]
    assert container["args"] == list(CONDUCTOR_ARGS)
    assert container["securityContext"] == {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert container["volumeMounts"] == [
        {"name": "config", "mountPath": CONDUCTOR_CONFIG_DIR, "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "logs", "mountPath": CONDUCTOR_LOG_DIR},
        {"name": "locks", "mountPath": CONDUCTOR_LOCKS_DIR},
    ]
    assert pod["volumes"] == [
        {
            "name": "config",
            "projected": {
                "sources": [
                    {
                        "configMap": {
                            "name": "example-coriolis-config",
                            "items": [
                                {"key": key, "path": key, "mode": 0o444}
                                for key in CONDUCTOR_CONFIG_MAP_KEYS
                            ],
                        }
                    },
                    {
                        "secret": {
                            "name": "example-coriolis-config-secret",
                            "items": [
                                {
                                    "key": "coriolis.conf",
                                    "path": "coriolis.conf",
                                    "mode": 0o440,
                                }
                            ],
                        }
                    },
                ]
            },
        },
        {"name": "tmp", "emptyDir": {"medium": "Memory"}},
        {"name": "logs", "emptyDir": {}},
        {"name": "locks", "emptyDir": {}},
    ]


def test_conductor_env_is_exactly_the_two_fixed_non_sensitive_values() -> None:
    container = build_conductor_deployment(**kwargs())["spec"]["template"]["spec"][
        "containers"
    ][0]

    assert container["env"] == [
        {"name": "HOME", "value": "/tmp"},
        {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
    ]


def test_conductor_explicitly_omits_sensitive_and_runtime_surfaces() -> None:
    deployment = build_conductor_deployment(**kwargs())
    spec = deployment["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]

    assert "envFrom" not in container
    assert all(
        not name.startswith(("CORIOLIS", "KEYSTONE", "RABBITMQ", "DATABASE"))
        and name not in {"RUN_DBSYNC", "OS_", "OPENSTACK"}
        for entry in container.get("env", [])
        for name in [entry["name"]]
    )
    assert "ports" not in container
    assert "resources" not in container
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert probe not in container
    assert "initContainers" not in pod
    assert "serviceAccountName" not in pod
    assert "serviceAccount" not in pod
    assert all(mount.get("subPath") is None for mount in container["volumeMounts"])
    assert all(volume.get("persistentVolumeClaim") is None for volume in pod["volumes"])


def test_conductor_deployment_uses_label_safe_name_and_selector() -> None:
    appliance_name = f"{'a' * 63}.{'b' * 63}"

    deployment = build_conductor_deployment(
        appliance_name=appliance_name,
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )

    assert deployment["metadata"]["name"] == appliance_resource_name(
        appliance_name, CONDUCTOR_COMPONENT
    )
    assert deployment["spec"]["selector"]["matchLabels"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": CONDUCTOR_COMPONENT,
    }


def test_conductor_preflight_absent_managed_and_collision() -> None:
    absent = preflight_conductor_resource(**kwargs(), conductor_deployment=None)
    assert absent.classification is OwnedClassification.ABSENT
    assert absent.manifests == (build_conductor_deployment(**kwargs()),)

    managed = copy.deepcopy(absent.manifests[0])
    managed_result = preflight_conductor_resource(
        **kwargs(), conductor_deployment=managed
    )
    assert managed_result.classification is OwnedClassification.MANAGED
    assert managed_result.manifests == absent.manifests

    collision = copy.deepcopy(managed)
    collision["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"
    collision_result = preflight_conductor_resource(
        **kwargs(), conductor_deployment=collision
    )
    assert collision_result.classification is OwnedClassification.COLLISION
    assert collision_result.manifests == ()
