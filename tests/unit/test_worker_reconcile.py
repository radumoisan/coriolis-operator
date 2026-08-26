import copy

from coriolis_operator.reconcile import (
    OwnedClassification,
    appliance_identity,
    appliance_resource_name,
    build_worker_deployment,
    preflight_worker_resource,
)
from coriolis_operator.worker import (
    WORKER_ARGS,
    WORKER_COMMAND,
    WORKER_COMPONENT,
    WORKER_IMAGE,
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


def test_worker_deployment_has_frozen_runtime_contract() -> None:
    deployment = build_worker_deployment(**kwargs())
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "example-coriolis-worker"
    assert deployment["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    assert deployment["spec"]["selector"] == {
        "matchLabels": {
            "coriolis.cloudbase.it/appliance": "example",
            "coriolis.cloudbase.it/component": WORKER_COMPONENT,
        }
    }
    assert pod["hostname"] == deployment["metadata"]["name"]
    assert pod["imagePullSecrets"] == [{"name": "coriolis-appliance-registry"}]
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["terminationGracePeriodSeconds"] == 30
    assert WORKER_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/coriolis-worker:2603.4"
        "@sha256:ff30999d6e43709411f197b1b6b80dbce1d7e5498a27f869df93a061626ab2c9"
    )
    assert container["command"] == [WORKER_COMMAND]
    assert container["args"] == list(WORKER_ARGS)
    assert container["image"] == WORKER_IMAGE
    assert container["imagePullPolicy"] == "Always"
    assert container["env"] == [
        {"name": "HOME", "value": "/tmp"},
        {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
    ]
    assert container["securityContext"] == {
        "runAsUser": 0,
        "runAsGroup": 0,
        "privileged": True,
        "allowPrivilegeEscalation": True,
        "readOnlyRootFilesystem": True,
        "seccompProfile": {"type": "Unconfined"},
    }
    assert container["volumeMounts"] == [
        {"name": "config", "mountPath": "/etc/coriolis", "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "logs", "mountPath": "/var/log/coriolis"},
        {"name": "export", "mountPath": "/opt/coriolis/export"},
        {"name": "dev", "mountPath": "/dev"},
        {"name": "lib-modules", "mountPath": "/lib/modules", "readOnly": True},
    ]
    assert pod["volumes"] == [
        {"name": "config", "secret": {"secretName": "example-coriolis-config-secret"}},
        {"name": "tmp", "emptyDir": {"medium": "Memory"}},
        {"name": "logs", "emptyDir": {}},
        {"name": "export", "emptyDir": {}},
        {"name": "dev", "hostPath": {"path": "/dev", "type": "Directory"}},
        {
            "name": "lib-modules",
            "hostPath": {"path": "/lib/modules", "type": "Directory"},
        },
    ]


def test_worker_omits_unqualified_runtime_surfaces() -> None:
    pod = build_worker_deployment(**kwargs())["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert not {"ports", "resources", "envFrom", "capabilities"} & set(container)
    assert not {
        "initContainers",
        "serviceAccountName",
        "hostNetwork",
        "hostPID",
        "hostIPC",
    } & set(pod)
    assert not {"startupProbe", "readinessProbe", "livenessProbe"} & set(container)
    assert all("persistentVolumeClaim" not in volume for volume in pod["volumes"])
    assert all("mountPropagation" not in mount for mount in container["volumeMounts"])


def test_worker_deployment_uses_label_safe_name_and_hostname() -> None:
    appliance_name = f"{'a' * 63}.{'b' * 63}"
    deployment = build_worker_deployment(
        appliance_name=appliance_name,
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )

    assert deployment["metadata"]["name"] == appliance_resource_name(
        appliance_name, WORKER_COMPONENT
    )
    assert (
        deployment["spec"]["template"]["spec"]["hostname"]
        == deployment["metadata"]["name"]
    )
    assert deployment["spec"]["selector"]["matchLabels"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": WORKER_COMPONENT,
    }


def test_worker_preflight_absent_managed_and_collision() -> None:
    absent = preflight_worker_resource(**kwargs(), worker_deployment=None)
    assert absent.classification is OwnedClassification.ABSENT
    assert absent.manifests == (build_worker_deployment(**kwargs()),)

    managed = copy.deepcopy(absent.manifests[0])
    managed_result = preflight_worker_resource(**kwargs(), worker_deployment=managed)
    assert managed_result.classification is OwnedClassification.MANAGED
    assert managed_result.manifests == absent.manifests

    managed["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"
    collision = preflight_worker_resource(**kwargs(), worker_deployment=managed)
    assert collision.classification is OwnedClassification.COLLISION
    assert collision.manifests == ()
