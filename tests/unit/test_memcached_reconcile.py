import copy

import pytest

from coriolis_operator import reconcile
from coriolis_operator.memcached import MEMCACHED_IMAGE
from coriolis_operator.reconcile import (
    OwnedClassification,
    appliance_identity,
    appliance_resource_name,
    build_dependency_service,
    build_memcached_deployment,
    preflight_memcached_resource,
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


def test_memcached_deployment_has_frozen_runtime_contract() -> None:
    deployment = build_memcached_deployment(**kwargs())

    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "example-memcached"
    assert deployment["metadata"]["namespace"] == "operators"
    assert deployment["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert (
        deployment["metadata"]["labels"]["app.kubernetes.io/component"] == "memcached"
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
            "coriolis.cloudbase.it/component": "memcached",
        }
    }
    assert spec["template"]["metadata"]["labels"] == deployment["metadata"]["labels"]
    assert pod["imagePullSecrets"] == [{"name": "coriolis-appliance-registry"}]
    assert pod["securityContext"] == {"runAsUser": 42457, "runAsGroup": 42457}
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["terminationGracePeriodSeconds"] == 30
    assert container["name"] == "memcached"
    assert container["image"] == MEMCACHED_IMAGE
    assert container["command"] == ["/usr/bin/memcached"]
    assert container["args"] == ["-p", "11211", "-U", "0"]
    assert container["ports"] == [
        {"name": "memcached", "containerPort": 11211, "protocol": "TCP"}
    ]
    assert container["securityContext"] == {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    for probe, period, failure in (
        (container["startupProbe"], 2, 30),
        (container["readinessProbe"], 5, 3),
        (container["livenessProbe"], 10, 3),
    ):
        assert probe["exec"]["command"][:2] == ["/usr/bin/bash", "-ec"]
        command = probe["exec"]["command"][2]
        assert "/dev/tcp/127.0.0.1/11211" in command
        assert "version\\r\\n" in command
        assert "VERSION\\ " in command
        assert probe["periodSeconds"] == period
        assert probe["timeoutSeconds"] == 2
        assert probe["failureThreshold"] == failure
    assert container["readinessProbe"]["successThreshold"] == 1
    assert "successThreshold" not in container["startupProbe"]
    assert "successThreshold" not in container["livenessProbe"]
    for forbidden in (
        "env",
        "envFrom",
        "resources",
        "volumeMounts",
        "volumes",
        "initContainers",
    ):
        assert forbidden not in container
        assert forbidden not in pod


def test_memcached_service_selects_the_deployment_pod() -> None:
    deployment = build_memcached_deployment(**kwargs())
    service = build_dependency_service(**kwargs(), component="memcached")
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]

    assert service["spec"]["selector"] == {
        key: pod_labels[key] for key in service["spec"]["selector"]
    }


def test_memcached_deployment_uses_label_safe_name_and_selector() -> None:
    appliance_name = f"{'a' * 63}.{'b' * 63}"

    deployment = build_memcached_deployment(
        appliance_name=appliance_name,
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
    )

    assert deployment["metadata"]["name"] == appliance_resource_name(
        appliance_name, "memcached"
    )
    assert deployment["spec"]["selector"]["matchLabels"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": "memcached",
    }


def test_memcached_preflight_builds_only_after_non_collision_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original = reconcile.classify_owned_resource

    def tracked_classify(**arguments: object) -> OwnedClassification:
        calls.append("classify")
        return original(**arguments)

    monkeypatch.setattr(reconcile, "classify_owned_resource", tracked_classify)
    result = preflight_memcached_resource(**kwargs(), memcached_deployment=None)

    assert calls == ["classify"]
    assert result.classification is OwnedClassification.ABSENT
    assert result.manifests == (build_memcached_deployment(**kwargs()),)

    managed = copy.deepcopy(result.manifests[0])
    managed_result = preflight_memcached_resource(
        **kwargs(), memcached_deployment=managed
    )
    assert managed_result.classification is OwnedClassification.MANAGED
    assert managed_result.manifests == (build_memcached_deployment(**kwargs()),)

    collision = copy.deepcopy(managed)
    collision["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"
    collision_result = preflight_memcached_resource(
        **kwargs(), memcached_deployment=collision
    )
    assert collision_result.classification is OwnedClassification.COLLISION
    assert collision_result.manifests == ()
