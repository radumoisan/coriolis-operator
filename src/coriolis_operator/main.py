"""Kopf entrypoint and CoriolisAppliance reconciliation handlers."""

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any

import kopf
from kubernetes import client  # type: ignore[import-untyped]

from coriolis_operator.common import BOOTSTRAP_COMPONENT
from coriolis_operator.configuration import (
    SensitiveCoriolisCredentials,
    render_coriolis_config,
    render_sensitive_coriolis_config,
)
from coriolis_operator.ingress import resolve_ingress_settings
from coriolis_operator.mariadb import (
    SensitiveMariaDBCredentials,
    resolve_mariadb_settings,
)
from coriolis_operator.rabbitmq import resolve_rabbitmq_settings
from coriolis_operator.reconcile import (
    DEPENDENCY_SERVICES,
    MARKER_COLLISION,
    SUPPORTED_INITIAL_VERSION,
    SUPPORTED_PROFILE,
    OwnedClassification,
    RetainedClassification,
    accepted_conditions,
    appliance_resource_name,
    blocked_conditions,
    bootstrap_failed_conditions,
    bootstrap_running_conditions,
    build_coriolis_config_map,
    build_coriolis_config_secret,
    build_coriolis_credentials_secret,
    build_dependency_service,
    build_infrastructure_credentials_secret,
    build_state_config_map,
    build_status,
    classify_existing_marker,
    classify_owned_resource,
    collision_conditions,
    invalid_runtime_configuration_conditions,
    kubernetes_coriolis_render_inputs,
    preflight_api_resources,
    preflight_common_bootstrap_resources,
    preflight_conductor_resource,
    preflight_deployer_manager_resource,
    preflight_foundational_resources,
    preflight_ingress_resources,
    preflight_keystone_resources,
    preflight_mariadb_resources,
    preflight_memcached_resource,
    preflight_minion_manager_resource,
    preflight_rabbitmq_resources,
    preflight_scheduler_resource,
    preflight_transfer_cron_resource,
    preflight_web_resources,
    preflight_worker_resource,
    rejected_conditions,
    retry_conditions,
)

GROUP = "coriolis.cloudbase.it"
VERSION = "v1alpha1"
PLURAL = "coriolisappliances"
WATCH_NAMESPACE = os.environ.get("WATCH_NAMESPACE") or None
LIVENESS_ENDPOINT = os.environ.get("LIVENESS_ENDPOINT", "http://0.0.0.0:8080/healthz")
FIELD_MANAGER = "coriolis-operator"
STATE_CREDENTIALS_RETENTION = "state-credentials"


class ReconcileRetry(Exception):
    """Carry a sanitized status through to the Kopf handler's retry path."""

    def __init__(self, status: dict[str, Any]) -> None:
        self.status = status


def _accepted_version(status: Mapping[str, Any] | None) -> str | None:
    if not isinstance(status, Mapping):
        return None
    value = status.get("acceptedVersion")
    if not isinstance(value, str) or not value:
        return None
    return value


def _prior_conditions(status: Mapping[str, Any] | None) -> object:
    if not isinstance(status, Mapping):
        return None
    return status.get("conditions")


def _has_resource_collision(status: Mapping[str, Any] | None) -> bool:
    """Return whether status is the stable collision result eligible for retry."""
    if not isinstance(status, Mapping):
        return False
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return False
    return any(
        isinstance(condition, Mapping)
        and condition.get("type") == "Reconciled"
        and condition.get("status") == "False"
        and condition.get("reason") == "ResourceCollision"
        for condition in conditions
    )


def _changed_computed_status(
    computed_status: Mapping[str, Any], status: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Return only changed operator-owned status fields without touching others."""
    prior_status = status if isinstance(status, Mapping) else {}
    return {
        key: value
        for key, value in computed_status.items()
        if key in {"acceptedVersion", "observedGeneration", "conditions"}
        and prior_status.get(key) != value
    }


def _reject(
    generation: int,
    *,
    reason: str,
    message: str,
    accepted_version: str | None = None,
    status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return build_status(
        generation,
        accepted_version=accepted_version,
        conditions=rejected_conditions(reason, message),
        prior_conditions=_prior_conditions(status),
    )


def _resource_version(resource: Any) -> str | None:
    metadata = (
        resource.get("metadata")
        if isinstance(resource, Mapping)
        else getattr(resource, "metadata", None)
    )
    value = (
        metadata.get("resourceVersion")
        if isinstance(metadata, Mapping)
        else getattr(metadata, "resource_version", None)
    )
    return value if isinstance(value, str) and value else None


def _retry_status(
    generation: int,
    accepted_version: str | None,
    status: Mapping[str, Any] | None,
    category: str,
) -> ReconcileRetry:
    return ReconcileRetry(
        build_status(
            generation,
            accepted_version=accepted_version,
            conditions=retry_conditions(category),
            prior_conditions=_prior_conditions(status),
        )
    )


def _read_or_absent(
    read: Any,
    *,
    name: str,
    namespace: str,
    generation: int,
    accepted_version: str | None,
    status: Mapping[str, Any] | None,
) -> Any | None:
    try:
        return read(name=name, namespace=namespace)
    except client.ApiException as exc:
        if exc.status == 404:
            return None
        raise _retry_status(
            generation, accepted_version, status, "ResourceReadFailed"
        ) from None
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceReadFailed"
        ) from None


def _create_or_apply(
    api: Any,
    *,
    kind: str,
    body: dict[str, Any],
    existing: Any | None,
    category: str,
    generation: int,
    accepted_version: str | None,
    status: Mapping[str, Any] | None,
) -> None:
    name = body["metadata"]["name"]
    namespace = body["metadata"]["namespace"]
    try:
        if existing is None:
            getattr(api, f"create_namespaced_{kind}")(namespace=namespace, body=body)
            return
        resource_version = _resource_version(existing)
        if resource_version is None:
            raise ValueError("managed resource is missing resourceVersion")
        body["metadata"]["resourceVersion"] = resource_version
        headers = api.api_client.default_headers
        had_content_type = "Content-Type" in headers
        previous_content_type = headers.get("Content-Type")
        headers["Content-Type"] = "application/apply-patch+yaml"
        try:
            getattr(api, f"patch_namespaced_{kind}")(
                name=name,
                namespace=namespace,
                body=body,
                field_manager=FIELD_MANAGER,
                force=True,
            )
        finally:
            if had_content_type:
                headers["Content-Type"] = previous_content_type
            else:
                headers.pop("Content-Type", None)
    except Exception:
        raise _retry_status(generation, accepted_version, status, category) from None


def _bootstrap_job_state(existing: Any) -> str:
    """Classify a read bootstrap Job as succeeded, failed, or active."""
    status = (
        existing.get("status")
        if isinstance(existing, Mapping)
        else getattr(existing, "status", None)
    )
    if status is None:
        return "active"
    status_map = status if isinstance(status, Mapping) else {}
    conditions = status_map.get("conditions")
    if conditions is None and not isinstance(status, Mapping):
        conditions = getattr(status, "conditions", None)
    if isinstance(conditions, list):
        for condition in conditions:
            condition_type = (
                condition.get("type")
                if isinstance(condition, Mapping)
                else getattr(condition, "type", None)
            )
            condition_status = (
                condition.get("status")
                if isinstance(condition, Mapping)
                else getattr(condition, "status", None)
            )
            if condition_type == "Failed" and condition_status == "True":
                return "failed"
    succeeded = status_map.get("succeeded")
    if succeeded is None and not isinstance(status, Mapping):
        succeeded = getattr(status, "succeeded", None)
    if isinstance(succeeded, int) and succeeded >= 1:
        return "succeeded"
    return "active"


def reconcile_appliance(
    *,
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    status: Mapping[str, Any] | None = None,
    core_api: client.CoreV1Api | None = None,
    apps_api: client.AppsV1Api | None = None,
    batch_api: client.BatchV1Api | None = None,
    networking_api: client.NetworkingV1Api | None = None,
) -> dict[str, Any]:
    """Reconcile foundational resources, dependency Services, and workloads."""
    name = str(meta["name"])
    namespace = str(meta["namespace"])
    generation = int(meta["generation"])
    owner = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "CoriolisAppliance",
        "name": name,
        "uid": str(meta["uid"]),
    }
    if "profile" in spec:
        profile = str(spec["profile"])
    else:
        profile = SUPPORTED_PROFILE
    requested_version = str(spec["version"])
    accepted_version = _accepted_version(status)

    if profile != SUPPORTED_PROFILE:
        return _reject(
            generation,
            reason="UnsupportedProfile",
            message=(
                f"Profile '{profile}' is not supported; "
                f"supported profile: {SUPPORTED_PROFILE}."
            ),
            accepted_version=accepted_version,
            status=status,
        )
    if accepted_version is not None and requested_version != accepted_version:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=blocked_conditions(accepted_version, requested_version),
            prior_conditions=_prior_conditions(status),
        )
    if accepted_version is None and requested_version != SUPPORTED_INITIAL_VERSION:
        return _reject(
            generation,
            reason="UnsupportedVersion",
            message=(
                f"Version '{requested_version}' is not supported; "
                f"initial supported version: {SUPPORTED_INITIAL_VERSION}."
            ),
            status=status,
        )

    try:
        mariadb_settings = resolve_mariadb_settings(
            storage=spec.get("storage"), resources=spec.get("resources")
        )
        rabbitmq_settings = resolve_rabbitmq_settings(
            storage=spec.get("storage"), resources=spec.get("resources")
        )
        ingress_settings = resolve_ingress_settings(spec.get("ingress"))
    except ValueError:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=invalid_runtime_configuration_conditions(),
            prior_conditions=_prior_conditions(status),
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None

    try:
        marker_body = build_state_config_map(
            name=name,
            namespace=namespace,
            profile=profile,
            accepted_version=requested_version,
            generation=generation,
            owner=owner,
        )
        marker_name = marker_body["metadata"]["name"]
        coriolis_credentials_name = appliance_resource_name(
            name, "coriolis-credentials"
        )
        infrastructure_credentials_name = appliance_resource_name(
            name, "infrastructure-credentials"
        )
        config_map_name = appliance_resource_name(name, "coriolis-config")
        config_secret_name = appliance_resource_name(name, "coriolis-config-secret")
        dependency_service_names = tuple(
            (component, appliance_resource_name(name, component))
            for component, _ in DEPENDENCY_SERVICES
        )
        coriolis_api_name = appliance_resource_name(name, "coriolis-api")
        coriolis_web_name = appliance_resource_name(name, "coriolis-web")
        keystone_ingress_name = appliance_resource_name(name, "keystone")
        conductor_deployment_name = appliance_resource_name(name, "coriolis-conductor")
        scheduler_deployment_name = appliance_resource_name(name, "coriolis-scheduler")
        transfer_cron_deployment_name = appliance_resource_name(
            name, "coriolis-transfer-cron"
        )
        minion_manager_deployment_name = appliance_resource_name(
            name, "coriolis-minion-manager"
        )
        deployer_manager_deployment_name = appliance_resource_name(
            name, "coriolis-deployer-manager"
        )
        worker_deployment_name = appliance_resource_name(name, "coriolis-worker")
        mariadb_data_pvc_name = appliance_resource_name(name, "mariadb-data")
        mariadb_config_map_name = appliance_resource_name(name, "mariadb-config")
        mariadb_config_secret_name = appliance_resource_name(
            name, "mariadb-config-secret"
        )
        mariadb_stateful_set_name = appliance_resource_name(name, "mariadb")
        memcached_deployment_name = appliance_resource_name(name, "memcached")
        rabbitmq_data_pvc_name = appliance_resource_name(name, "rabbitmq-data")
        rabbitmq_config_map_name = appliance_resource_name(name, "rabbitmq-config")
        rabbitmq_stateful_set_name = appliance_resource_name(name, "rabbitmq")
        keystone_database_credentials_name = appliance_resource_name(
            name, "keystone-database-credentials"
        )
        keystone_fernet_keys_name = appliance_resource_name(
            name, "keystone-fernet-keys"
        )
        keystone_credential_keys_name = appliance_resource_name(
            name, "keystone-credential-keys"
        )
        keystone_config_map_name = appliance_resource_name(name, "keystone-config")
        keystone_config_secret_name = appliance_resource_name(
            name, "keystone-config-secret"
        )
        keystone_deployment_name = appliance_resource_name(name, "keystone")
        bootstrap_resource_name = appliance_resource_name(name, BOOTSTRAP_COMPONENT)
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    try:
        api = core_api if core_api is not None else client.CoreV1Api()
        workloads_api = apps_api if apps_api is not None else client.AppsV1Api()
        batch_api = batch_api if batch_api is not None else client.BatchV1Api()
        ingress_api = (
            networking_api if networking_api is not None else client.NetworkingV1Api()
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceReadFailed"
        ) from None
    marker_existing = _read_or_absent(
        api.read_namespaced_config_map,
        name=marker_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    coriolis_credentials_existing = _read_or_absent(
        api.read_namespaced_secret,
        name=coriolis_credentials_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    infrastructure_credentials_existing = _read_or_absent(
        api.read_namespaced_secret,
        name=infrastructure_credentials_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    config_map_existing = _read_or_absent(
        api.read_namespaced_config_map,
        name=config_map_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    config_secret_existing = _read_or_absent(
        api.read_namespaced_secret,
        name=config_secret_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    dependency_services_existing = tuple(
        (
            component,
            service_name,
            _read_or_absent(
                api.read_namespaced_service,
                name=service_name,
                namespace=namespace,
                generation=generation,
                accepted_version=accepted_version,
                status=status,
            ),
        )
        for component, service_name in dependency_service_names
    )
    coriolis_api_service_existing = _read_or_absent(
        api.read_namespaced_service,
        name=coriolis_api_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    mariadb_data_pvc_existing = _read_or_absent(
        api.read_namespaced_persistent_volume_claim,
        name=mariadb_data_pvc_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    mariadb_config_map_existing = _read_or_absent(
        api.read_namespaced_config_map,
        name=mariadb_config_map_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    mariadb_config_secret_existing = _read_or_absent(
        api.read_namespaced_secret,
        name=mariadb_config_secret_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    mariadb_stateful_set_existing = _read_or_absent(
        workloads_api.read_namespaced_stateful_set,
        name=mariadb_stateful_set_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    memcached_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=memcached_deployment_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    rabbitmq_data_pvc_existing = _read_or_absent(
        api.read_namespaced_persistent_volume_claim,
        name=rabbitmq_data_pvc_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    rabbitmq_config_map_existing = _read_or_absent(
        api.read_namespaced_config_map,
        name=rabbitmq_config_map_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    rabbitmq_stateful_set_existing = _read_or_absent(
        workloads_api.read_namespaced_stateful_set,
        name=rabbitmq_stateful_set_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    keystone_database_credentials_existing = _read_or_absent(
        api.read_namespaced_secret,
        name=keystone_database_credentials_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    keystone_fernet_keys_existing = _read_or_absent(
        api.read_namespaced_secret,
        name=keystone_fernet_keys_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    keystone_credential_keys_existing = _read_or_absent(
        api.read_namespaced_secret,
        name=keystone_credential_keys_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    keystone_config_map_existing = _read_or_absent(
        api.read_namespaced_config_map,
        name=keystone_config_map_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    keystone_config_secret_existing = _read_or_absent(
        api.read_namespaced_secret,
        name=keystone_config_secret_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    keystone_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=keystone_deployment_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    coriolis_api_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=coriolis_api_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    conductor_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=conductor_deployment_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    scheduler_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=scheduler_deployment_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    transfer_cron_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=transfer_cron_deployment_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    minion_manager_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=minion_manager_deployment_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    deployer_manager_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=deployer_manager_deployment_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    worker_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=worker_deployment_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    bootstrap_config_map_existing = _read_or_absent(
        api.read_namespaced_config_map,
        name=bootstrap_resource_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    bootstrap_job_existing = _read_or_absent(
        batch_api.read_namespaced_job,
        name=bootstrap_resource_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    coriolis_web_service_existing = _read_or_absent(
        api.read_namespaced_service,
        name=coriolis_web_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    coriolis_web_deployment_existing = _read_or_absent(
        workloads_api.read_namespaced_deployment,
        name=coriolis_web_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    coriolis_web_ingress_existing = _read_or_absent(
        ingress_api.read_namespaced_ingress,
        name=coriolis_web_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    keystone_ingress_existing = _read_or_absent(
        ingress_api.read_namespaced_ingress,
        name=keystone_ingress_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    coriolis_api_ingress_existing = _read_or_absent(
        ingress_api.read_namespaced_ingress,
        name=coriolis_api_name,
        namespace=namespace,
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    try:
        marker_classification = (
            classify_existing_marker(existing=marker_existing, desired=marker_body)
            if marker_existing is not None
            else None
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if marker_classification == MARKER_COLLISION:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, marker_name),
            prior_conditions=_prior_conditions(status),
        )
    if marker_existing is not None and _resource_version(marker_existing) is None:
        raise _retry_status(generation, accepted_version, status, "MarkerApplyFailed")

    try:
        preflight = preflight_foundational_resources(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            retention=STATE_CREDENTIALS_RETENTION,
            owner=owner,
            coriolis_credentials_secret=coriolis_credentials_existing,
            infrastructure_credentials_secret=infrastructure_credentials_existing,
            coriolis_config_map=config_map_existing,
            coriolis_config_secret=config_secret_existing,
        )
        collision = next(
            (
                resource_name
                for resource_name, classification in preflight.classifications.items()
                if classification.value == "collision"
            ),
            None,
        )
        service_classifications = tuple(
            (
                component,
                service_name,
                existing,
                classify_owned_resource(
                    existing=existing,
                    resource_name=service_name,
                    namespace=namespace,
                    appliance_name=name,
                    component=component,
                    accepted_version=requested_version,
                    owner=owner,
                ),
            )
            for component, service_name, existing in dependency_services_existing
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if collision is not None:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, collision),
            prior_conditions=_prior_conditions(status),
        )
    service_collision = next(
        (
            service_name
            for _, service_name, _, classification in service_classifications
            if classification is OwnedClassification.COLLISION
        ),
        None,
    )
    if service_collision is not None:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, service_collision),
            prior_conditions=_prior_conditions(status),
        )
    for existing, classification in (
        (config_map_existing, preflight.classifications[config_map_name]),
        (config_secret_existing, preflight.classifications[config_secret_name]),
    ):
        if (
            classification is OwnedClassification.MANAGED
            and _resource_version(existing) is None
        ):
            raise _retry_status(
                generation, accepted_version, status, "ResourceApplyFailed"
            )
    for _, _, existing, classification in service_classifications:
        if (
            classification is OwnedClassification.MANAGED
            and _resource_version(existing) is None
        ):
            raise _retry_status(
                generation, accepted_version, status, "ResourceApplyFailed"
            )

    try:
        inputs = kubernetes_coriolis_render_inputs(name)
        keystone_preflight = preflight_keystone_resources(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            retention=STATE_CREDENTIALS_RETENTION,
            database_host=inputs.endpoints.database_host,
            keystone_host=inputs.endpoints.keystone_host,
            keystone_admin_password=preflight.credentials[
                infrastructure_credentials_name
            ]["keystone_admin_password"],
            keystone_database_credentials_secret=(
                keystone_database_credentials_existing
            ),
            keystone_fernet_keys_secret=keystone_fernet_keys_existing,
            keystone_credential_keys_secret=keystone_credential_keys_existing,
            keystone_config_map=keystone_config_map_existing,
            keystone_config_secret=keystone_config_secret_existing,
            keystone_deployment=keystone_deployment_existing,
        )
        keystone_collision = next(
            (
                resource_name
                for resource_name, classification in (
                    keystone_preflight.classifications.items()
                )
                if classification.value == "collision"
            ),
            None,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if keystone_collision is not None:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, keystone_collision),
            prior_conditions=_prior_conditions(status),
        )
    for existing, classification in (
        (
            keystone_config_map_existing,
            keystone_preflight.classifications[keystone_config_map_name],
        ),
        (
            keystone_config_secret_existing,
            keystone_preflight.classifications[keystone_config_secret_name],
        ),
        (
            keystone_deployment_existing,
            keystone_preflight.classifications[keystone_deployment_name],
        ),
    ):
        if (
            classification is OwnedClassification.MANAGED
            and _resource_version(existing) is None
        ):
            raise _retry_status(
                generation, accepted_version, status, "ResourceApplyFailed"
            )

    try:
        mariadb_preflight = preflight_mariadb_resources(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            settings=mariadb_settings,
            credentials=SensitiveMariaDBCredentials(
                database_password=preflight.credentials[
                    infrastructure_credentials_name
                ]["database_password"],
                coriolis_database_password=preflight.credentials[
                    coriolis_credentials_name
                ]["coriolis_database_password"],
                keystone_database_password=keystone_preflight.credentials[
                    keystone_database_credentials_name
                ]["keystone_database_password"],
            ),
            owner=owner,
            mariadb_data_pvc=mariadb_data_pvc_existing,
            mariadb_config_map=mariadb_config_map_existing,
            mariadb_config_secret=mariadb_config_secret_existing,
            mariadb_stateful_set=mariadb_stateful_set_existing,
        )
        mariadb_collision = next(
            (
                resource_name
                for resource_name, classification in (
                    mariadb_preflight.classifications.items()
                )
                if classification.value == "collision"
            ),
            None,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if mariadb_collision is not None:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, mariadb_collision),
            prior_conditions=_prior_conditions(status),
        )
    for existing, classification in (
        (
            mariadb_config_map_existing,
            mariadb_preflight.classifications[mariadb_config_map_name],
        ),
        (
            mariadb_config_secret_existing,
            mariadb_preflight.classifications[mariadb_config_secret_name],
        ),
        (
            mariadb_stateful_set_existing,
            mariadb_preflight.classifications[mariadb_stateful_set_name],
        ),
    ):
        if (
            classification is OwnedClassification.MANAGED
            and _resource_version(existing) is None
        ):
            raise _retry_status(
                generation, accepted_version, status, "ResourceApplyFailed"
            )

    try:
        rabbitmq_preflight = preflight_rabbitmq_resources(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            settings=rabbitmq_settings,
            owner=owner,
            rabbitmq_data_pvc=rabbitmq_data_pvc_existing,
            rabbitmq_config_map=rabbitmq_config_map_existing,
            rabbitmq_stateful_set=rabbitmq_stateful_set_existing,
        )
        rabbitmq_collision = next(
            (
                resource_name
                for resource_name, classification in (
                    rabbitmq_preflight.classifications.items()
                )
                if classification.value == "collision"
            ),
            None,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if rabbitmq_collision is not None:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, rabbitmq_collision),
            prior_conditions=_prior_conditions(status),
        )
    for existing, classification in (
        (
            rabbitmq_config_map_existing,
            rabbitmq_preflight.classifications[rabbitmq_config_map_name],
        ),
        (
            rabbitmq_stateful_set_existing,
            rabbitmq_preflight.classifications[rabbitmq_stateful_set_name],
        ),
    ):
        if (
            classification is OwnedClassification.MANAGED
            and _resource_version(existing) is None
        ):
            raise _retry_status(
                generation, accepted_version, status, "ResourceApplyFailed"
            )

    try:
        memcached_preflight = preflight_memcached_resource(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            memcached_deployment=memcached_deployment_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if memcached_preflight.classification is OwnedClassification.COLLISION:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, memcached_deployment_name),
            prior_conditions=_prior_conditions(status),
        )
    if (
        memcached_preflight.classification is OwnedClassification.MANAGED
        and _resource_version(memcached_deployment_existing) is None
    ):
        raise _retry_status(generation, accepted_version, status, "ResourceApplyFailed")

    try:
        bootstrap_preflight = preflight_common_bootstrap_resources(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            bootstrap_config_map=bootstrap_config_map_existing,
            bootstrap_job=bootstrap_job_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if (
        bootstrap_preflight.config_map_classification is OwnedClassification.COLLISION
        or bootstrap_preflight.job_classification is OwnedClassification.COLLISION
    ):
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, bootstrap_resource_name),
            prior_conditions=_prior_conditions(status),
        )

    try:
        conductor_preflight = preflight_conductor_resource(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            conductor_deployment=conductor_deployment_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if conductor_preflight.classification is OwnedClassification.COLLISION:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, conductor_deployment_name),
            prior_conditions=_prior_conditions(status),
        )
    if (
        conductor_preflight.classification is OwnedClassification.MANAGED
        and _resource_version(conductor_deployment_existing) is None
    ):
        raise _retry_status(generation, accepted_version, status, "ResourceApplyFailed")

    try:
        scheduler_preflight = preflight_scheduler_resource(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            scheduler_deployment=scheduler_deployment_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if scheduler_preflight.classification is OwnedClassification.COLLISION:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, scheduler_deployment_name),
            prior_conditions=_prior_conditions(status),
        )
    if (
        scheduler_preflight.classification is OwnedClassification.MANAGED
        and _resource_version(scheduler_deployment_existing) is None
    ):
        raise _retry_status(generation, accepted_version, status, "ResourceApplyFailed")

    try:
        transfer_cron_preflight = preflight_transfer_cron_resource(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            transfer_cron_deployment=transfer_cron_deployment_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if transfer_cron_preflight.classification is OwnedClassification.COLLISION:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, transfer_cron_deployment_name),
            prior_conditions=_prior_conditions(status),
        )
    if (
        transfer_cron_preflight.classification is OwnedClassification.MANAGED
        and _resource_version(transfer_cron_deployment_existing) is None
    ):
        raise _retry_status(generation, accepted_version, status, "ResourceApplyFailed")

    try:
        minion_manager_preflight = preflight_minion_manager_resource(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            minion_manager_deployment=minion_manager_deployment_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if minion_manager_preflight.classification is OwnedClassification.COLLISION:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, minion_manager_deployment_name),
            prior_conditions=_prior_conditions(status),
        )
    if (
        minion_manager_preflight.classification is OwnedClassification.MANAGED
        and _resource_version(minion_manager_deployment_existing) is None
    ):
        raise _retry_status(generation, accepted_version, status, "ResourceApplyFailed")

    try:
        deployer_manager_preflight = preflight_deployer_manager_resource(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            deployer_manager_deployment=deployer_manager_deployment_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if deployer_manager_preflight.classification is OwnedClassification.COLLISION:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(
                namespace, deployer_manager_deployment_name
            ),
            prior_conditions=_prior_conditions(status),
        )
    if (
        deployer_manager_preflight.classification is OwnedClassification.MANAGED
        and _resource_version(deployer_manager_deployment_existing) is None
    ):
        raise _retry_status(generation, accepted_version, status, "ResourceApplyFailed")

    try:
        worker_preflight = preflight_worker_resource(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            worker_deployment=worker_deployment_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if worker_preflight.classification is OwnedClassification.COLLISION:
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, worker_deployment_name),
            prior_conditions=_prior_conditions(status),
        )
    if (
        worker_preflight.classification is OwnedClassification.MANAGED
        and _resource_version(worker_deployment_existing) is None
    ):
        raise _retry_status(generation, accepted_version, status, "ResourceApplyFailed")

    try:
        api_preflight = preflight_api_resources(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            api_service=coriolis_api_service_existing,
            api_deployment=coriolis_api_deployment_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if OwnedClassification.COLLISION in (
        api_preflight.service_classification,
        api_preflight.deployment_classification,
    ):
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, coriolis_api_name),
            prior_conditions=_prior_conditions(status),
        )
    for existing, classification in (
        (coriolis_api_service_existing, api_preflight.service_classification),
        (coriolis_api_deployment_existing, api_preflight.deployment_classification),
    ):
        if (
            classification is OwnedClassification.MANAGED
            and _resource_version(existing) is None
        ):
            raise _retry_status(
                generation, accepted_version, status, "ResourceApplyFailed"
            )

    try:
        web_preflight = preflight_web_resources(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            web_service=coriolis_web_service_existing,
            web_deployment=coriolis_web_deployment_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    if OwnedClassification.COLLISION in (
        web_preflight.service_classification,
        web_preflight.deployment_classification,
    ):
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=collision_conditions(namespace, coriolis_web_name),
            prior_conditions=_prior_conditions(status),
        )
    for existing, classification in (
        (coriolis_web_service_existing, web_preflight.service_classification),
        (coriolis_web_deployment_existing, web_preflight.deployment_classification),
    ):
        if (
            classification is OwnedClassification.MANAGED
            and _resource_version(existing) is None
        ):
            raise _retry_status(
                generation, accepted_version, status, "ResourceApplyFailed"
            )

    try:
        ingress_preflight = preflight_ingress_resources(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            settings=ingress_settings,
            web_ingress=coriolis_web_ingress_existing,
            keystone_ingress=keystone_ingress_existing,
            api_ingress=coriolis_api_ingress_existing,
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    for resource_name, existing, classification in (
        (
            coriolis_web_name,
            coriolis_web_ingress_existing,
            ingress_preflight.web_classification,
        ),
        (
            keystone_ingress_name,
            keystone_ingress_existing,
            ingress_preflight.keystone_classification,
        ),
        (
            coriolis_api_name,
            coriolis_api_ingress_existing,
            ingress_preflight.api_classification,
        ),
    ):
        if classification is OwnedClassification.COLLISION:
            return build_status(
                generation,
                accepted_version=accepted_version,
                conditions=collision_conditions(namespace, resource_name),
                prior_conditions=_prior_conditions(status),
            )
        if (
            classification is OwnedClassification.MANAGED
            and _resource_version(existing) is None
        ):
            raise _retry_status(
                generation, accepted_version, status, "ResourceApplyFailed"
            )

    (
        mariadb_data_pvc_body,
        mariadb_config_map_body,
        mariadb_config_secret_body,
        mariadb_stateful_set_body,
    ) = mariadb_preflight.manifests
    (
        rabbitmq_data_pvc_body,
        rabbitmq_config_map_body,
        rabbitmq_stateful_set_body,
    ) = rabbitmq_preflight.manifests
    (
        keystone_database_credentials_body,
        keystone_fernet_keys_body,
        keystone_credential_keys_body,
        keystone_config_map_body,
        keystone_config_secret_body,
        keystone_deployment_body,
    ) = keystone_preflight.manifests
    coriolis_api_service_body, coriolis_api_deployment_body = api_preflight.manifests
    coriolis_web_service_body, coriolis_web_deployment_body = web_preflight.manifests
    (
        coriolis_web_ingress_body,
        keystone_ingress_body,
        coriolis_api_ingress_body,
    ) = ingress_preflight.manifests
    (conductor_deployment_body,) = conductor_preflight.manifests
    (scheduler_deployment_body,) = scheduler_preflight.manifests
    (transfer_cron_deployment_body,) = transfer_cron_preflight.manifests
    (minion_manager_deployment_body,) = minion_manager_preflight.manifests
    (deployer_manager_deployment_body,) = deployer_manager_preflight.manifests
    (worker_deployment_body,) = worker_preflight.manifests

    try:
        config_map_body = build_coriolis_config_map(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            values=render_coriolis_config(
                inputs=inputs, accepted_version=requested_version
            ),
        )
        config_secret_body = build_coriolis_config_secret(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            owner=owner,
            values=render_sensitive_coriolis_config(
                endpoints=inputs.endpoints,
                credentials=SensitiveCoriolisCredentials(
                    rabbitmq_password=preflight.credentials[
                        infrastructure_credentials_name
                    ]["rabbitmq_password"],
                    coriolis_database_password=preflight.credentials[
                        coriolis_credentials_name
                    ]["coriolis_database_password"],
                    coriolis_keystone_password=preflight.credentials[
                        coriolis_credentials_name
                    ]["coriolis_keystone_password"],
                    temp_keypair_password=preflight.credentials[
                        coriolis_credentials_name
                    ]["temp_keypair_password"],
                ),
            ),
        )
        coriolis_credentials_body = build_coriolis_credentials_secret(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            retention=STATE_CREDENTIALS_RETENTION,
            values=preflight.credentials[coriolis_credentials_name],
        )
        infrastructure_credentials_body = build_infrastructure_credentials_secret(
            appliance_name=name,
            namespace=namespace,
            accepted_version=requested_version,
            retention=STATE_CREDENTIALS_RETENTION,
            values=preflight.credentials[infrastructure_credentials_name],
        )
        dependency_service_bodies = tuple(
            (
                component,
                build_dependency_service(
                    appliance_name=name,
                    namespace=namespace,
                    accepted_version=requested_version,
                    owner=owner,
                    component=component,
                ),
            )
            for component, _ in DEPENDENCY_SERVICES
        )
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    resources = (
        (
            "secret",
            coriolis_credentials_body,
            coriolis_credentials_existing,
            preflight.classifications[coriolis_credentials_name],
            "ResourceApplyFailed",
        ),
        (
            "secret",
            infrastructure_credentials_body,
            infrastructure_credentials_existing,
            preflight.classifications[infrastructure_credentials_name],
            "ResourceApplyFailed",
        ),
        (
            "config_map",
            config_map_body,
            config_map_existing,
            preflight.classifications[config_map_name],
            "ResourceApplyFailed",
        ),
        (
            "secret",
            config_secret_body,
            config_secret_existing,
            preflight.classifications[config_secret_name],
            "ResourceApplyFailed",
        ),
    )
    for kind, body, existing, classification, category in resources:
        if classification is RetainedClassification.REUSE:
            continue
        _create_or_apply(
            api,
            kind=kind,
            body=body,
            existing=existing,
            category=category,
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    for (_, body), (_, _, existing, classification) in zip(
        dependency_service_bodies, service_classifications, strict=True
    ):
        _create_or_apply(
            api,
            kind="service",
            body=body,
            existing=existing,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    for body, existing, classification in (
        (
            keystone_database_credentials_body,
            keystone_database_credentials_existing,
            keystone_preflight.classifications[keystone_database_credentials_name],
        ),
        (
            keystone_fernet_keys_body,
            keystone_fernet_keys_existing,
            keystone_preflight.classifications[keystone_fernet_keys_name],
        ),
        (
            keystone_credential_keys_body,
            keystone_credential_keys_existing,
            keystone_preflight.classifications[keystone_credential_keys_name],
        ),
    ):
        if classification is RetainedClassification.REUSE:
            continue
        _create_or_apply(
            api,
            kind="secret",
            body=body,
            existing=existing,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    if (
        mariadb_preflight.classifications[mariadb_data_pvc_name]
        is RetainedClassification.ABSENT
    ):
        _create_or_apply(
            api,
            kind="persistent_volume_claim",
            body=mariadb_data_pvc_body,
            existing=None,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    for resource_api, kind, body, existing in (
        (
            api,
            "config_map",
            mariadb_config_map_body,
            mariadb_config_map_existing,
        ),
        (
            api,
            "secret",
            mariadb_config_secret_body,
            mariadb_config_secret_existing,
        ),
        (
            workloads_api,
            "stateful_set",
            mariadb_stateful_set_body,
            mariadb_stateful_set_existing,
        ),
    ):
        _create_or_apply(
            resource_api,
            kind=kind,
            body=body,
            existing=existing,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    if (
        rabbitmq_preflight.classifications[rabbitmq_data_pvc_name]
        is RetainedClassification.ABSENT
    ):
        _create_or_apply(
            api,
            kind="persistent_volume_claim",
            body=rabbitmq_data_pvc_body,
            existing=None,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    for resource_api, kind, body, existing in (
        (
            api,
            "config_map",
            rabbitmq_config_map_body,
            rabbitmq_config_map_existing,
        ),
        (
            workloads_api,
            "stateful_set",
            rabbitmq_stateful_set_body,
            rabbitmq_stateful_set_existing,
        ),
    ):
        _create_or_apply(
            resource_api,
            kind=kind,
            body=body,
            existing=existing,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    _create_or_apply(
        workloads_api,
        kind="deployment",
        body=memcached_preflight.manifests[0],
        existing=memcached_deployment_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    for resource_api, kind, body, existing in (
        (api, "config_map", keystone_config_map_body, keystone_config_map_existing),
        (api, "secret", keystone_config_secret_body, keystone_config_secret_existing),
        (
            workloads_api,
            "deployment",
            keystone_deployment_body,
            keystone_deployment_existing,
        ),
    ):
        _create_or_apply(
            resource_api,
            kind=kind,
            body=body,
            existing=existing,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    (bootstrap_config_map_body, bootstrap_job_body) = bootstrap_preflight.manifests
    if bootstrap_preflight.config_map_classification is OwnedClassification.ABSENT:
        _create_or_apply(
            api,
            kind="config_map",
            body=bootstrap_config_map_body,
            existing=None,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    if bootstrap_preflight.job_classification is OwnedClassification.ABSENT:
        _create_or_apply(
            batch_api,
            kind="job",
            body=bootstrap_job_body,
            existing=None,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
        raise ReconcileRetry(
            build_status(
                generation,
                accepted_version=accepted_version,
                conditions=bootstrap_running_conditions(),
                prior_conditions=_prior_conditions(status),
            )
        )
    job_state = _bootstrap_job_state(bootstrap_job_existing)
    if job_state == "failed":
        return build_status(
            generation,
            accepted_version=accepted_version,
            conditions=bootstrap_failed_conditions(),
            prior_conditions=_prior_conditions(status),
        )
    if job_state != "succeeded":
        raise ReconcileRetry(
            build_status(
                generation,
                accepted_version=accepted_version,
                conditions=bootstrap_running_conditions(),
                prior_conditions=_prior_conditions(status),
            )
        )
    _create_or_apply(
        workloads_api,
        kind="deployment",
        body=conductor_deployment_body,
        existing=conductor_deployment_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    _create_or_apply(
        workloads_api,
        kind="deployment",
        body=scheduler_deployment_body,
        existing=scheduler_deployment_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    _create_or_apply(
        workloads_api,
        kind="deployment",
        body=transfer_cron_deployment_body,
        existing=transfer_cron_deployment_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    _create_or_apply(
        workloads_api,
        kind="deployment",
        body=minion_manager_deployment_body,
        existing=minion_manager_deployment_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    _create_or_apply(
        workloads_api,
        kind="deployment",
        body=deployer_manager_deployment_body,
        existing=deployer_manager_deployment_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    _create_or_apply(
        workloads_api,
        kind="deployment",
        body=worker_deployment_body,
        existing=worker_deployment_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    _create_or_apply(
        api,
        kind="service",
        body=coriolis_api_service_body,
        existing=coriolis_api_service_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    _create_or_apply(
        workloads_api,
        kind="deployment",
        body=coriolis_api_deployment_body,
        existing=coriolis_api_deployment_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    _create_or_apply(
        api,
        kind="service",
        body=coriolis_web_service_body,
        existing=coriolis_web_service_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    _create_or_apply(
        workloads_api,
        kind="deployment",
        body=coriolis_web_deployment_body,
        existing=coriolis_web_deployment_existing,
        category="ResourceApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    for body, existing in (
        (coriolis_web_ingress_body, coriolis_web_ingress_existing),
        (keystone_ingress_body, keystone_ingress_existing),
        (coriolis_api_ingress_body, coriolis_api_ingress_existing),
    ):
        _create_or_apply(
            ingress_api,
            kind="ingress",
            body=body,
            existing=existing,
            category="ResourceApplyFailed",
            generation=generation,
            accepted_version=accepted_version,
            status=status,
        )
    _create_or_apply(
        api,
        kind="config_map",
        body=marker_body,
        existing=marker_existing,
        category="MarkerApplyFailed",
        generation=generation,
        accepted_version=accepted_version,
        status=status,
    )
    return build_status(
        generation,
        accepted_version=requested_version,
        conditions=accepted_conditions(),
        prior_conditions=_prior_conditions(status),
    )


def _handle_reconcile(
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    patch: kopf.Patch,
    status: Mapping[str, Any] | None = None,
    **_: Any,
) -> None:
    try:
        reconciled_status = reconcile_appliance(spec=spec, meta=meta, status=status)
    except ReconcileRetry as exc:
        changed_status = _changed_computed_status(exc.status, status)
        if changed_status:
            patch.status.update(changed_status)
        raise kopf.TemporaryError(
            "Kubernetes resource reconciliation will be retried.", delay=10
        ) from None
    changed_status = _changed_computed_status(reconciled_status, status)
    if changed_status:
        patch.status.update(changed_status)


@kopf.on.create(GROUP, VERSION, PLURAL)
def create_appliance(
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    patch: kopf.Patch,
    status: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Reconcile a newly created appliance."""
    _handle_reconcile(spec, meta, patch, status, **kwargs)


@kopf.on.resume(GROUP, VERSION, PLURAL)
def resume_appliance(
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    patch: kopf.Patch,
    status: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Reconcile an appliance after controller restart."""
    _handle_reconcile(spec, meta, patch, status, **kwargs)


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.version")
def update_appliance_version(
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    patch: kopf.Patch,
    status: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Reconcile the requested appliance version change."""
    _handle_reconcile(spec, meta, patch, status, **kwargs)


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.profile")
def update_appliance_profile(
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    patch: kopf.Patch,
    status: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Reconcile the requested appliance profile change."""
    _handle_reconcile(spec, meta, patch, status, **kwargs)


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.storage")
def update_appliance_storage(
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    patch: kopf.Patch,
    status: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Reconcile requested appliance storage changes."""
    _handle_reconcile(spec, meta, patch, status, **kwargs)


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.resources")
def update_appliance_resources(
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    patch: kopf.Patch,
    status: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Reconcile requested appliance resource changes."""
    _handle_reconcile(spec, meta, patch, status, **kwargs)


@kopf.on.field(GROUP, VERSION, PLURAL, field="spec.ingress")
def update_appliance_ingress(
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    patch: kopf.Patch,
    status: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Reconcile requested appliance ingress changes."""
    _handle_reconcile(spec, meta, patch, status, **kwargs)


@kopf.timer(GROUP, VERSION, PLURAL, initial_delay=60, interval=60)
def retry_resource_collision(
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    patch: kopf.Patch,
    status: Mapping[str, Any] | None = None,
    retry: int = 0,
    **kwargs: Any,
) -> None:
    """Retry only stable collisions, so removed conflicts converge without a watch."""
    if _has_resource_collision(status) or retry > 0:
        _handle_reconcile(spec, meta, patch, status, **kwargs)


def main() -> None:
    """Run the operator with optional namespace restriction and liveness probe."""
    log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), None)
    logging.basicConfig(level=log_level if isinstance(log_level, int) else logging.INFO)
    asyncio.run(
        kopf.operator(namespace=WATCH_NAMESPACE, liveness_endpoint=LIVENESS_ENDPOINT)
    )
