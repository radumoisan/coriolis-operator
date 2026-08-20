"""Kopf entrypoint and CoriolisAppliance reconciliation handlers."""

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any

import kopf
from kubernetes import client  # type: ignore[import-untyped]

from coriolis_operator.reconcile import (
    MARKER_COLLISION,
    SUPPORTED_INITIAL_VERSION,
    SUPPORTED_PROFILE,
    accepted_conditions,
    blocked_conditions,
    build_state_config_map,
    build_status,
    classify_existing_marker,
    collision_conditions,
    rejected_conditions,
)

GROUP = "coriolis.cloudbase.it"
VERSION = "v1alpha1"
PLURAL = "coriolisappliances"
WATCH_NAMESPACE = os.environ.get("WATCH_NAMESPACE") or None
LIVENESS_ENDPOINT = os.environ.get("LIVENESS_ENDPOINT", "http://0.0.0.0:8080/healthz")


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


def reconcile_appliance(
    *,
    spec: Mapping[str, Any],
    meta: Mapping[str, Any],
    status: Mapping[str, Any] | None = None,
    core_api: client.CoreV1Api | None = None,
) -> dict[str, Any]:
    """Apply appliance state and return the status once Kubernetes accepts it."""
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

    body = build_state_config_map(
        name=name,
        namespace=namespace,
        profile=profile,
        accepted_version=requested_version,
        generation=generation,
        owner=owner,
    )
    target_name = body["metadata"]["name"]
    api = core_api if core_api is not None else client.CoreV1Api()
    try:
        existing = api.read_namespaced_config_map(name=target_name, namespace=namespace)
    except client.ApiException as exc:
        if exc.status != 404:
            raise
        existing = None
    if existing is not None:
        classification = classify_existing_marker(existing=existing, desired=body)
        if classification == MARKER_COLLISION:
            return build_status(
                generation,
                accepted_version=accepted_version,
                conditions=collision_conditions(namespace, target_name),
                prior_conditions=_prior_conditions(status),
            )
    api.api_client.default_headers["Content-Type"] = "application/apply-patch+yaml"
    api.patch_namespaced_config_map(
        name=body["metadata"]["name"],
        namespace=namespace,
        body=body,
        field_manager="coriolis-operator",
        force=True,
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
    reconciled_status = reconcile_appliance(spec=spec, meta=meta, status=status)
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
