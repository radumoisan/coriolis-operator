import copy
import json

from coriolis_operator.rabbitmq import (
    RABBITMQ_CONFIG_KEYS,
    RabbitMQSettings,
    resolve_rabbitmq_settings,
)
from coriolis_operator.reconcile import (
    OwnedClassification,
    RetainedClassification,
    appliance_resource_name,
    build_dependency_service,
    build_rabbitmq_config_map,
    build_rabbitmq_data_pvc,
    build_rabbitmq_stateful_set,
    classify_rabbitmq_data_pvc,
    preflight_rabbitmq_resources,
)

OWNER = {
    "apiVersion": "coriolis.cloudbase.it/v1alpha1",
    "kind": "CoriolisAppliance",
    "name": "example",
    "uid": "abc-123",
}
KWARGS = {
    "appliance_name": "example",
    "namespace": "operators",
    "accepted_version": "2603.4",
}


def settings() -> RabbitMQSettings:
    return resolve_rabbitmq_settings(
        storage={"rabbitmq": {"storageClassName": "standard", "size": "1Gi"}},
        resources={
            "rabbitmq": {
                "requests": {"cpu": "500m", "memory": "512Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            }
        },
    )


def test_rabbitmq_manifests_are_retained_secure_and_service_compatible() -> None:
    resolved = settings()
    pvc = build_rabbitmq_data_pvc(**KWARGS, settings=resolved)
    config = build_rabbitmq_config_map(
        **KWARGS,
        owner=OWNER,
        values={key: key for key in RABBITMQ_CONFIG_KEYS},
    )
    stateful_set = build_rabbitmq_stateful_set(**KWARGS, owner=OWNER, settings=resolved)
    service = build_dependency_service(**KWARGS, owner=OWNER, component="rabbitmq")

    assert pvc["metadata"]["name"] == "example-rabbitmq-data"
    assert "ownerReferences" not in pvc["metadata"]
    assert (
        pvc["metadata"]["annotations"]["coriolis.cloudbase.it/retention"]
        == "rabbitmq-data"
    )
    assert pvc["spec"] == {
        "storageClassName": "standard",
        "accessModes": ["ReadWriteOnce"],
        "volumeMode": "Filesystem",
        "resources": {"requests": {"storage": "1Gi"}},
    }
    assert config["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
    assert set(config["data"]) == RABBITMQ_CONFIG_KEYS
    assert "rabbitmq_password" not in json.dumps(config)
    spec = stateful_set["spec"]
    pod = spec["template"]["spec"]
    container = pod["containers"][0]
    assert spec["serviceName"] == service["metadata"]["name"] == "example-rabbitmq"
    assert spec["selector"]["matchLabels"] == service["spec"]["selector"]
    assert (
        spec["selector"]["matchLabels"].items()
        <= spec["template"]["metadata"]["labels"].items()
    )
    assert "volumeClaimTemplates" not in spec
    assert pod["terminationGracePeriodSeconds"] == 60
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["securityContext"] == {
        "runAsUser": 42439,
        "runAsGroup": 42439,
        "fsGroup": 42439,
        "fsGroupChangePolicy": "OnRootMismatch",
    }
    assert container["command"] == ["/etc/rabbitmq/start-rabbitmq.sh"]
    assert "args" not in container
    assert "env" not in container
    assert "envFrom" not in container
    assert container["resources"] == {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    }
    assert container["securityContext"] == {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert container["startupProbe"]["failureThreshold"] == 36
    assert container["startupProbe"]["periodSeconds"] == 5
    assert container["startupProbe"]["timeoutSeconds"] == 5
    assert container["startupProbe"]["exec"]["command"] == [
        "/bin/sh",
        "-ec",
        "/usr/sbin/rabbitmq-diagnostics -q check_running && "
        "/usr/sbin/rabbitmq-diagnostics -q check_port_listener 5672",
    ]
    assert container["readinessProbe"]["exec"]["command"] == [
        "/bin/sh",
        "-ec",
        "/usr/sbin/rabbitmq-diagnostics -q check_running && "
        "/usr/sbin/rabbitmq-diagnostics -q check_port_listener 5672 && "
        "/usr/sbin/rabbitmq-diagnostics -q check_local_alarms",
    ]
    assert container["readinessProbe"]["successThreshold"] == 1
    assert container["livenessProbe"]["exec"]["command"] == [
        "/bin/sh",
        "-ec",
        "/usr/sbin/rabbitmq-diagnostics -q check_running",
    ]
    assert container["livenessProbe"]["periodSeconds"] == 10
    assert container["livenessProbe"]["timeoutSeconds"] == 5
    assert container["livenessProbe"]["failureThreshold"] == 6
    volumes = {volume["name"]: volume for volume in pod["volumes"]}
    assert volumes["runtime"] == {"name": "runtime", "emptyDir": {}}
    assert volumes["logs"] == {"name": "logs", "emptyDir": {}}
    assert volumes["secret"]["secret"]["items"] == [
        {"key": "rabbitmq_password", "path": "rabbitmq_password", "mode": 0o440}
    ]
    assert (
        volumes["secret"]["secret"]["secretName"]
        == "example-infrastructure-credentials"
    )
    assert {
        item["key"]: item["mode"] for item in volumes["config"]["configMap"]["items"]
    } == {
        "rabbitmq.conf": 0o444,
        "start-rabbitmq.sh": 0o555,
    }
    rendered = json.dumps((pvc, config, stateful_set))
    assert "SENTINEL_PASSWORD" not in rendered
    assert all("subPath" not in mount for mount in container["volumeMounts"])


def test_rabbitmq_pvc_classification_is_exact_and_preflight_stops_on_collision() -> (
    None
):
    resolved = settings()
    pvc = build_rabbitmq_data_pvc(**KWARGS, settings=resolved)
    equivalent = copy.deepcopy(pvc)
    equivalent["spec"]["resources"]["requests"]["storage"] = "1024Mi"
    equivalent["spec"]["volumeName"] = "bound"
    assert (
        classify_rabbitmq_data_pvc(**KWARGS, settings=resolved, existing=equivalent)
        is RetainedClassification.REUSE
    )
    for mutation in (
        lambda body: body["spec"].update({"accessModes": ["ReadWriteMany"]}),
        lambda body: body["spec"].update({"dataSource": {"name": "source"}}),
        lambda body: body["spec"]["resources"].update({"limits": {"storage": "1Gi"}}),
    ):
        invalid = copy.deepcopy(pvc)
        mutation(invalid)
        assert (
            classify_rabbitmq_data_pvc(**KWARGS, settings=resolved, existing=invalid)
            is RetainedClassification.COLLISION
        )

    result = preflight_rabbitmq_resources(
        **KWARGS,
        settings=resolved,
        owner=OWNER,
        rabbitmq_data_pvc=None,
        rabbitmq_config_map=None,
        rabbitmq_stateful_set=None,
    )
    assert list(result.classifications.values()) == [
        RetainedClassification.ABSENT,
        OwnedClassification.ABSENT,
        OwnedClassification.ABSENT,
    ]
    assert [body["kind"] for body in result.manifests] == [
        "PersistentVolumeClaim",
        "ConfigMap",
        "StatefulSet",
    ]
    collision = preflight_rabbitmq_resources(
        **KWARGS,
        settings=resolved,
        owner=OWNER,
        rabbitmq_data_pvc=invalid,
        rabbitmq_config_map=None,
        rabbitmq_stateful_set=None,
    )
    assert collision.manifests == ()
    assert len(collision.classifications) == 1


def test_rabbitmq_long_names_keep_related_resource_identity() -> None:
    appliance_name = "a" * 56
    assert appliance_resource_name(
        appliance_name, "rabbitmq"
    ) != appliance_resource_name(appliance_name, "rabbitmq-data")
