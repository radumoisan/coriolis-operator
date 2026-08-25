import copy

from coriolis_operator.deployer_manager import (
    DEPLOYER_MANAGER_ARGS,
    DEPLOYER_MANAGER_COMMAND,
    DEPLOYER_MANAGER_COMPONENT,
    DEPLOYER_MANAGER_CONFIG_DIR,
    DEPLOYER_MANAGER_IMAGE,
    DEPLOYER_MANAGER_LOG_DIR,
    DEPLOYER_MANAGER_RUN_AS_ID,
)
from coriolis_operator.reconcile import (
    OwnedClassification,
    appliance_identity,
    appliance_resource_name,
    build_deployer_manager_deployment,
    preflight_deployer_manager_resource,
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


def test_deployer_manager_deployment_has_frozen_runtime_contract() -> None:
    deployment = build_deployer_manager_deployment(**kwargs())
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "example-coriolis-deployer-manager"
    assert deployment["metadata"]["namespace"] == "operators"
    assert deployment["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert deployment["metadata"]["labels"]["app.kubernetes.io/component"] == (
        DEPLOYER_MANAGER_COMPONENT
    )
    assert deployment["metadata"]["labels"]["coriolis.cloudbase.it/component"] == (
        DEPLOYER_MANAGER_COMPONENT
    )
    assert deployment["metadata"]["annotations"] == {
        "coriolis.cloudbase.it/appliance-name": "example"
    }
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    assert deployment["spec"]["selector"] == {
        "matchLabels": {
            "coriolis.cloudbase.it/appliance": "example",
            "coriolis.cloudbase.it/component": DEPLOYER_MANAGER_COMPONENT,
        }
    }
    assert (
        deployment["spec"]["template"]["metadata"]["labels"]
        == deployment["metadata"]["labels"]
    )
    assert pod["imagePullSecrets"] == [{"name": "coriolis-appliance-registry"}]
    assert pod["securityContext"] == {
        "runAsUser": DEPLOYER_MANAGER_RUN_AS_ID,
        "runAsGroup": DEPLOYER_MANAGER_RUN_AS_ID,
        "fsGroup": DEPLOYER_MANAGER_RUN_AS_ID,
        "fsGroupChangePolicy": "OnRootMismatch",
    }
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["terminationGracePeriodSeconds"] == 15
    assert DEPLOYER_MANAGER_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/coriolis-deployer-manager:2603.4"
        "@sha256:a2a7091daf8e172b96fa0b48d19ffad285d7bfaad42fc7e8cd44a688f06f36aa"
    )
    assert container["name"] == DEPLOYER_MANAGER_COMPONENT
    assert container["image"] == DEPLOYER_MANAGER_IMAGE
    assert container["command"] == [DEPLOYER_MANAGER_COMMAND]
    assert container["args"] == list(DEPLOYER_MANAGER_ARGS)
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
        {"name": "config", "mountPath": DEPLOYER_MANAGER_CONFIG_DIR, "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "logs", "mountPath": DEPLOYER_MANAGER_LOG_DIR},
    ]
    assert pod["volumes"] == [
        {
            "name": "config",
            "projected": {
                "sources": [
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
                    }
                ]
            },
        },
        {"name": "tmp", "emptyDir": {"medium": "Memory"}},
        {"name": "logs", "emptyDir": {}},
    ]


def test_deployer_manager_omits_unneeded_runtime_surfaces() -> None:
    pod = build_deployer_manager_deployment(**kwargs())["spec"]["template"]["spec"]
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
    assert {mount["name"] for mount in container["volumeMounts"]} == {
        "config",
        "tmp",
        "logs",
    }
    assert {volume["name"] for volume in pod["volumes"]} == {"config", "tmp", "logs"}


def test_deployer_manager_deployment_uses_label_safe_name_and_selector() -> None:
    appliance_name = f"{'a' * 63}.{'b' * 63}"
    deployment = build_deployer_manager_deployment(
        appliance_name=appliance_name,
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )

    assert deployment["metadata"]["name"] == appliance_resource_name(
        appliance_name, DEPLOYER_MANAGER_COMPONENT
    )
    assert deployment["spec"]["selector"]["matchLabels"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": DEPLOYER_MANAGER_COMPONENT,
    }


def test_deployer_manager_preflight_absent_managed_and_collision() -> None:
    absent = preflight_deployer_manager_resource(
        **kwargs(), deployer_manager_deployment=None
    )
    assert absent.classification is OwnedClassification.ABSENT
    assert absent.manifests == (build_deployer_manager_deployment(**kwargs()),)

    managed = copy.deepcopy(absent.manifests[0])
    managed_result = preflight_deployer_manager_resource(
        **kwargs(), deployer_manager_deployment=managed
    )
    assert managed_result.classification is OwnedClassification.MANAGED
    assert managed_result.manifests == absent.manifests

    managed["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"
    collision = preflight_deployer_manager_resource(
        **kwargs(), deployer_manager_deployment=managed
    )
    assert collision.classification is OwnedClassification.COLLISION
    assert collision.manifests == ()
