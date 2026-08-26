import copy

import pytest

from coriolis_operator.reconcile import (
    OwnedClassification,
    appliance_identity,
    appliance_resource_name,
    build_web_deployment,
    build_web_service,
    preflight_web_resources,
)
from coriolis_operator.web import (
    WEB_BIND_ADDRESS,
    WEB_COMPONENT,
    WEB_IMAGE,
    WEB_IMAGE_PULL_SECRET_NAME,
    WEB_PORT,
    WEB_PROBE_PATH,
    WEB_RUN_AS_ID,
    WEB_TERMINATION_GRACE_PERIOD_SECONDS,
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


def test_web_manifests_have_frozen_runtime_contract() -> None:
    service = build_web_service(**kwargs())
    deployment = build_web_deployment(**kwargs())

    metadata = {
        "name": "example-coriolis-web",
        "namespace": "operators",
        "labels": {
            "app.kubernetes.io/name": "coriolis",
            "app.kubernetes.io/instance": "example",
            "app.kubernetes.io/version": "2603.4",
            "app.kubernetes.io/component": WEB_COMPONENT,
            "app.kubernetes.io/part-of": "coriolis-appliance",
            "app.kubernetes.io/managed-by": "coriolis-operator",
            "coriolis.cloudbase.it/appliance": "example",
            "coriolis.cloudbase.it/component": WEB_COMPONENT,
        },
        "annotations": {"coriolis.cloudbase.it/appliance-name": "example"},
        "ownerReferences": [dict(OWNER, controller=True)],
    }
    selector = {
        "coriolis.cloudbase.it/appliance": "example",
        "coriolis.cloudbase.it/component": WEB_COMPONENT,
    }
    assert service == {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": metadata,
        "spec": {
            "type": "ClusterIP",
            "selector": selector,
            "ports": [
                {
                    "name": "web",
                    "protocol": "TCP",
                    "port": WEB_PORT,
                    "targetPort": WEB_PORT,
                }
            ],
        },
    }
    assert deployment == {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": metadata["labels"]},
                "spec": {
                    "imagePullSecrets": [{"name": WEB_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "runAsUser": WEB_RUN_AS_ID,
                        "runAsGroup": WEB_RUN_AS_ID,
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        WEB_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [
                        {
                            "name": WEB_COMPONENT,
                            "image": WEB_IMAGE,
                            "imagePullPolicy": "Always",
                            "env": [{"name": "BIND", "value": WEB_BIND_ADDRESS}],
                            "ports": [
                                {
                                    "name": "web",
                                    "containerPort": WEB_PORT,
                                    "protocol": "TCP",
                                }
                            ],
                            "securityContext": {
                                "readOnlyRootFilesystem": False,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "startupProbe": {
                                "httpGet": {"path": WEB_PROBE_PATH, "port": "web"},
                                "periodSeconds": 2,
                                "timeoutSeconds": 5,
                                "failureThreshold": 30,
                            },
                            "readinessProbe": {
                                "httpGet": {"path": WEB_PROBE_PATH, "port": "web"},
                                "periodSeconds": 5,
                                "timeoutSeconds": 5,
                                "failureThreshold": 3,
                                "successThreshold": 1,
                            },
                            "livenessProbe": {
                                "httpGet": {"path": WEB_PROBE_PATH, "port": "web"},
                                "periodSeconds": 10,
                                "timeoutSeconds": 5,
                                "failureThreshold": 6,
                            },
                        }
                    ],
                },
            },
        },
    }


def test_web_manifests_use_label_safe_names_and_matching_selectors() -> None:
    appliance_name = f"{'a' * 63}.{'b' * 63}"
    arguments = dict(kwargs(), appliance_name=appliance_name)

    service = build_web_service(**arguments)
    deployment = build_web_deployment(**arguments)

    assert service["metadata"]["name"] == appliance_resource_name(
        appliance_name, WEB_COMPONENT
    )
    assert deployment["metadata"]["name"] == service["metadata"]["name"]
    assert service["spec"]["selector"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": WEB_COMPONENT,
    }
    assert deployment["spec"]["selector"]["matchLabels"] == service["spec"]["selector"]


@pytest.mark.parametrize("collision_kind", ["service", "deployment"])
def test_web_preflight_is_all_or_nothing_on_collision(collision_kind: str) -> None:
    service = build_web_service(**kwargs())
    deployment = build_web_deployment(**kwargs())
    collision = copy.deepcopy(service if collision_kind == "service" else deployment)
    collision["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"

    result = preflight_web_resources(
        **kwargs(),
        web_service=collision if collision_kind == "service" else service,
        web_deployment=collision if collision_kind == "deployment" else deployment,
    )

    assert OwnedClassification.COLLISION in (
        result.service_classification,
        result.deployment_classification,
    )
    assert result.manifests == ()


def test_web_preflight_builds_absent_and_managed_resources_without_mutation() -> None:
    arguments = kwargs()
    original_arguments = copy.deepcopy(arguments)
    absent = preflight_web_resources(**arguments, web_service=None, web_deployment=None)
    assert arguments == original_arguments
    assert absent.service_classification is OwnedClassification.ABSENT
    assert absent.deployment_classification is OwnedClassification.ABSENT
    assert absent.manifests == (
        build_web_service(**kwargs()),
        build_web_deployment(**kwargs()),
    )

    existing_service, existing_deployment = copy.deepcopy(absent.manifests)
    existing = copy.deepcopy((existing_service, existing_deployment))
    managed = preflight_web_resources(
        **kwargs(),
        web_service=existing_service,
        web_deployment=existing_deployment,
    )
    assert (existing_service, existing_deployment) == existing
    assert managed.service_classification is OwnedClassification.MANAGED
    assert managed.deployment_classification is OwnedClassification.MANAGED
    assert managed.manifests == absent.manifests
