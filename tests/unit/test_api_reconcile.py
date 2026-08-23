import copy

import pytest

from coriolis_operator.api import (
    API_ARGS,
    API_COMMAND,
    API_CONFIG_DIR,
    API_CONFIG_MAP_KEYS,
    API_IMAGE,
    API_LOCKS_DIR,
    API_LOG_DIR,
    API_PORT,
    API_PROTOCOL_PROBE,
    API_RUN_AS_ID,
)
from coriolis_operator.reconcile import (
    OwnedClassification,
    appliance_identity,
    appliance_resource_name,
    build_api_deployment,
    build_api_service,
    preflight_api_resources,
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


def test_api_manifests_have_frozen_runtime_contract() -> None:
    service = build_api_service(**kwargs())
    deployment = build_api_deployment(**kwargs())

    assert service["metadata"]["name"] == "example-coriolis-api"
    assert service["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert service["spec"] == {
        "type": "ClusterIP",
        "selector": {
            "coriolis.cloudbase.it/appliance": "example",
            "coriolis.cloudbase.it/component": "coriolis-api",
        },
        "ports": [
            {
                "name": "api",
                "protocol": "TCP",
                "port": API_PORT,
                "targetPort": API_PORT,
            }
        ],
    }

    spec = deployment["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]
    assert deployment["metadata"]["name"] == service["metadata"]["name"]
    assert deployment["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "Recreate"}
    assert service["spec"]["selector"] == spec["selector"]["matchLabels"]
    assert spec["template"]["metadata"]["labels"] == deployment["metadata"]["labels"]
    assert pod["imagePullSecrets"] == [{"name": "coriolis-appliance-registry"}]
    assert pod["securityContext"] == {
        "runAsUser": API_RUN_AS_ID,
        "runAsGroup": API_RUN_AS_ID,
        "fsGroup": API_RUN_AS_ID,
        "fsGroupChangePolicy": "OnRootMismatch",
    }
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["terminationGracePeriodSeconds"] == 15

    assert container["name"] == "coriolis-api"
    assert container["image"] == API_IMAGE
    assert container["command"] == [API_COMMAND]
    assert container["args"] == list(API_ARGS)
    assert container["ports"] == [
        {"name": "api", "containerPort": API_PORT, "protocol": "TCP"}
    ]
    assert container["securityContext"] == {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert container["volumeMounts"] == [
        {"name": "config", "mountPath": API_CONFIG_DIR, "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "logs", "mountPath": API_LOG_DIR},
        {"name": "locks", "mountPath": API_LOCKS_DIR},
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
                                for key in API_CONFIG_MAP_KEYS
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
    for probe_name, period, failure in (
        ("startupProbe", 2, 30),
        ("readinessProbe", 5, 3),
        ("livenessProbe", 10, 6),
    ):
        probe = container[probe_name]
        assert probe["exec"]["command"] == [
            "/usr/bin/python3",
            "-c",
            API_PROTOCOL_PROBE,
        ]
        assert probe["periodSeconds"] == period
        assert probe["timeoutSeconds"] == 5
        assert probe["failureThreshold"] == failure
    assert container["readinessProbe"]["successThreshold"] == 1
    for forbidden in ("env", "envFrom", "resources"):
        assert forbidden not in container
    assert "initContainers" not in pod


def test_api_manifests_use_label_safe_names_and_matching_selectors() -> None:
    appliance_name = f"{'a' * 63}.{'b' * 63}"
    arguments = dict(kwargs(), appliance_name=appliance_name)

    service = build_api_service(**arguments)
    deployment = build_api_deployment(**arguments)

    assert service["metadata"]["name"] == appliance_resource_name(
        appliance_name, "coriolis-api"
    )
    assert deployment["metadata"]["name"] == service["metadata"]["name"]
    assert service["spec"]["selector"] == {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": "coriolis-api",
    }
    assert deployment["spec"]["selector"]["matchLabels"] == service["spec"]["selector"]


@pytest.mark.parametrize("collision_kind", ["service", "deployment"])
def test_api_preflight_is_all_or_nothing_on_collision(collision_kind: str) -> None:
    service = build_api_service(**kwargs())
    deployment = build_api_deployment(**kwargs())
    collision = service if collision_kind == "service" else deployment
    collision = copy.deepcopy(collision)
    collision["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "other"

    result = preflight_api_resources(
        **kwargs(),
        api_service=collision if collision_kind == "service" else service,
        api_deployment=(collision if collision_kind == "deployment" else deployment),
    )

    assert OwnedClassification.COLLISION in (
        result.service_classification,
        result.deployment_classification,
    )
    assert result.manifests == ()


def test_api_preflight_builds_absent_and_managed_resources() -> None:
    absent = preflight_api_resources(**kwargs(), api_service=None, api_deployment=None)
    assert absent.service_classification is OwnedClassification.ABSENT
    assert absent.deployment_classification is OwnedClassification.ABSENT
    assert absent.manifests == (
        build_api_service(**kwargs()),
        build_api_deployment(**kwargs()),
    )

    managed = preflight_api_resources(
        **kwargs(),
        api_service=absent.manifests[0],
        api_deployment=absent.manifests[1],
    )
    assert managed.service_classification is OwnedClassification.MANAGED
    assert managed.deployment_classification is OwnedClassification.MANAGED
    assert managed.manifests == absent.manifests
