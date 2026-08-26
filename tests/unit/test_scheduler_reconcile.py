import copy

from coriolis_operator.reconcile import (
    OwnedClassification,
    appliance_identity,
    appliance_resource_name,
    build_scheduler_deployment,
    preflight_scheduler_resource,
)
from coriolis_operator.scheduler import (
    SCHEDULER_ARGS,
    SCHEDULER_COMMAND,
    SCHEDULER_COMPONENT,
    SCHEDULER_CONFIG_DIR,
    SCHEDULER_CONFIG_MAP_KEYS,
    SCHEDULER_IMAGE,
    SCHEDULER_LOG_DIR,
    SCHEDULER_RUN_AS_ID,
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


def test_scheduler_deployment_has_frozen_runtime_contract() -> None:
    deployment = build_scheduler_deployment(**kwargs())

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "example-coriolis-scheduler"
    assert deployment["metadata"]["namespace"] == "operators"
    assert deployment["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert deployment["metadata"]["labels"]["app.kubernetes.io/component"] == (
        SCHEDULER_COMPONENT
    )
    assert deployment["metadata"]["labels"]["coriolis.cloudbase.it/component"] == (
        SCHEDULER_COMPONENT
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
            "coriolis.cloudbase.it/component": SCHEDULER_COMPONENT,
        }
    }
    assert spec["template"]["metadata"]["labels"] == deployment["metadata"]["labels"]
    assert pod["imagePullSecrets"] == [{"name": "coriolis-appliance-registry"}]
    assert pod["securityContext"] == {
        "runAsUser": 42434,
        "runAsGroup": 42434,
        "fsGroup": 42434,
        "fsGroupChangePolicy": "OnRootMismatch",
    }
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["terminationGracePeriodSeconds"] == 30

    assert SCHEDULER_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/coriolis-scheduler:2603.4"
        "@sha256:45bea9e0bab4cac0fdddee6d3eac52006d12cf7de1e798e2949dd9ebc2a73c41"
    )
    assert container["name"] == SCHEDULER_COMPONENT
    assert container["image"] == SCHEDULER_IMAGE
    assert container["command"] == [SCHEDULER_COMMAND]
    assert container["args"] == list(SCHEDULER_ARGS)
    assert container["env"] == [
        {"name": "HOME", "value": "/tmp"},
        {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
    ]
    assert container["securityContext"] == {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert container["volumeMounts"] == [
        {"name": "config", "mountPath": SCHEDULER_CONFIG_DIR, "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "logs", "mountPath": SCHEDULER_LOG_DIR},
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
                                for key in SCHEDULER_CONFIG_MAP_KEYS
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
    ]


def test_scheduler_explicitly_omits_unneeded_runtime_surfaces() -> None:
    deployment = build_scheduler_deployment(**kwargs())
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert SCHEDULER_RUN_AS_ID == 42434
    assert "envFrom" not in container
    assert "ports" not in container
    assert "resources" not in container
    assert all(
        probe not in container
        for probe in ("startupProbe", "readinessProbe", "livenessProbe")
    )
    assert "initContainers" not in pod
    assert "serviceAccountName" not in pod
    assert "serviceAccount" not in pod
    assert all(mount.get("subPath") is None for mount in container["volumeMounts"])
    assert all(volume.get("persistentVolumeClaim") is None for volume in pod["volumes"])
    assert "locks" not in {mount["name"] for mount in container["volumeMounts"]}
    assert "locks" not in {volume["name"] for volume in pod["volumes"]}
    assert {mount["name"] for mount in container["volumeMounts"]} == {
        "config",
        "tmp",
        "logs",
    }


def test_scheduler_deployment_uses_label_safe_name_and_selector() -> None:
    appliance_name = f"{'a' * 63}.{'b' * 63}"

    deployment = build_scheduler_deployment(
        appliance_name=appliance_name,
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )

    assert deployment["metadata"]["name"] == appliance_resource_name(
        appliance_name, SCHEDULER_COMPONENT
    )
    assert deployment["spec"]["selector"]["matchLabels"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": SCHEDULER_COMPONENT,
    }


def test_scheduler_preflight_absent_managed_and_collision() -> None:
    absent = preflight_scheduler_resource(**kwargs(), scheduler_deployment=None)
    assert absent.classification is OwnedClassification.ABSENT
    assert absent.manifests == (build_scheduler_deployment(**kwargs()),)

    managed = copy.deepcopy(absent.manifests[0])
    managed_result = preflight_scheduler_resource(
        **kwargs(), scheduler_deployment=managed
    )
    assert managed_result.classification is OwnedClassification.MANAGED
    assert managed_result.manifests == absent.manifests

    collision = copy.deepcopy(managed)
    collision["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"
    collision_result = preflight_scheduler_resource(
        **kwargs(), scheduler_deployment=collision
    )
    assert collision_result.classification is OwnedClassification.COLLISION
    assert collision_result.manifests == ()
