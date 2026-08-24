import copy

from coriolis_operator.reconcile import (
    OwnedClassification,
    appliance_identity,
    appliance_resource_name,
    build_transfer_cron_deployment,
    preflight_transfer_cron_resource,
)
from coriolis_operator.transfer_cron import (
    TRANSFER_CRON_ARGS,
    TRANSFER_CRON_COMMAND,
    TRANSFER_CRON_COMPONENT,
    TRANSFER_CRON_CONFIG_DIR,
    TRANSFER_CRON_CONFIG_MAP_KEYS,
    TRANSFER_CRON_IMAGE,
    TRANSFER_CRON_LOG_DIR,
    TRANSFER_CRON_RUN_AS_ID,
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


def test_transfer_cron_deployment_has_frozen_runtime_contract() -> None:
    deployment = build_transfer_cron_deployment(**kwargs())
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "example-coriolis-transfer-cron"
    assert deployment["metadata"]["namespace"] == "operators"
    assert deployment["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert deployment["metadata"]["labels"]["app.kubernetes.io/component"] == (
        TRANSFER_CRON_COMPONENT
    )
    assert deployment["metadata"]["labels"]["coriolis.cloudbase.it/component"] == (
        TRANSFER_CRON_COMPONENT
    )
    assert deployment["metadata"]["annotations"] == {
        "coriolis.cloudbase.it/appliance-name": "example"
    }
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    assert deployment["spec"]["selector"] == {
        "matchLabels": {
            "coriolis.cloudbase.it/appliance": "example",
            "coriolis.cloudbase.it/component": TRANSFER_CRON_COMPONENT,
        }
    }
    assert (
        deployment["spec"]["template"]["metadata"]["labels"]
        == deployment["metadata"]["labels"]
    )
    assert pod["imagePullSecrets"] == [{"name": "coriolis-appliance-registry"}]
    assert pod["securityContext"] == {
        "runAsUser": TRANSFER_CRON_RUN_AS_ID,
        "runAsGroup": TRANSFER_CRON_RUN_AS_ID,
        "fsGroup": TRANSFER_CRON_RUN_AS_ID,
        "fsGroupChangePolicy": "OnRootMismatch",
    }
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["terminationGracePeriodSeconds"] == 15
    assert TRANSFER_CRON_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/coriolis-transfer-cron:2603.4"
        "@sha256:3a44d3b40ba92dff9217b8e7d6a7ca3e7a202efa2641c771ce9b2a3552b3ea9c"
    )
    assert container["name"] == TRANSFER_CRON_COMPONENT
    assert container["image"] == TRANSFER_CRON_IMAGE
    assert container["command"] == [TRANSFER_CRON_COMMAND]
    assert container["args"] == list(TRANSFER_CRON_ARGS)
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
        {"name": "config", "mountPath": TRANSFER_CRON_CONFIG_DIR, "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "logs", "mountPath": TRANSFER_CRON_LOG_DIR},
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
                                for key in TRANSFER_CRON_CONFIG_MAP_KEYS
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


def test_transfer_cron_omits_unneeded_runtime_surfaces() -> None:
    pod = build_transfer_cron_deployment(**kwargs())["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert "envFrom" not in container
    assert "ports" not in container
    assert "resources" not in container
    assert not {"startupProbe", "readinessProbe", "livenessProbe"} & set(container)
    assert "initContainers" not in pod
    assert "serviceAccountName" not in pod
    assert "serviceAccount" not in pod
    assert all("subPath" not in mount for mount in container["volumeMounts"])
    assert all("persistentVolumeClaim" not in volume for volume in pod["volumes"])
    assert "locks" not in {mount["name"] for mount in container["volumeMounts"]}
    assert "locks" not in {volume["name"] for volume in pod["volumes"]}
    assert {mount["name"] for mount in container["volumeMounts"]} == {
        "config",
        "tmp",
        "logs",
    }


def test_transfer_cron_deployment_uses_label_safe_name_and_selector() -> None:
    appliance_name = f"{'a' * 63}.{'b' * 63}"
    deployment = build_transfer_cron_deployment(
        appliance_name=appliance_name,
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )

    assert deployment["metadata"]["name"] == appliance_resource_name(
        appliance_name, TRANSFER_CRON_COMPONENT
    )
    assert deployment["spec"]["selector"]["matchLabels"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": TRANSFER_CRON_COMPONENT,
    }


def test_transfer_cron_preflight_absent_managed_and_collision() -> None:
    absent = preflight_transfer_cron_resource(**kwargs(), transfer_cron_deployment=None)
    assert absent.classification is OwnedClassification.ABSENT
    assert absent.manifests == (build_transfer_cron_deployment(**kwargs()),)

    managed = copy.deepcopy(absent.manifests[0])
    managed_result = preflight_transfer_cron_resource(
        **kwargs(), transfer_cron_deployment=managed
    )
    assert managed_result.classification is OwnedClassification.MANAGED
    assert managed_result.manifests == absent.manifests

    managed["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"
    collision = preflight_transfer_cron_resource(
        **kwargs(), transfer_cron_deployment=managed
    )
    assert collision.classification is OwnedClassification.COLLISION
    assert collision.manifests == ()
