"""Kopf entrypoint and CoriolisAppliance reconciliation handlers."""

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any

import kopf
from kubernetes import client  # type: ignore[import-untyped]

from coriolis_operator.configuration import (
    SensitiveCoriolisCredentials,
    render_coriolis_config,
    render_sensitive_coriolis_config,
)
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
    kubernetes_coriolis_render_inputs,
    preflight_foundational_resources,
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
    api: client.CoreV1Api,
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


def reconcile_appliance(
    *,
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    status: Mapping[str, Any] | None = None,
    core_api: client.CoreV1Api | None = None,
) -> dict[str, Any]:
    """Reconcile foundational resources and dependency Services."""
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
    except ReconcileRetry:
        raise
    except Exception:
        raise _retry_status(
            generation, accepted_version, status, "ResourceApplyFailed"
        ) from None
    try:
        api = core_api if core_api is not None else client.CoreV1Api()
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
        patch.status.update(exc.status)
        raise kopf.TemporaryError(
            "Kubernetes resource reconciliation will be retried.", delay=10
        ) from None
    patch.status.update(reconciled_status)


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


def main() -> None:
    """Run the operator with optional namespace restriction and liveness probe."""
    log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), None)
    logging.basicConfig(level=log_level if isinstance(log_level, int) else logging.INFO)
    asyncio.run(
        kopf.operator(namespace=WATCH_NAMESPACE, liveness_endpoint=LIVENESS_ENDPOINT)
    )
