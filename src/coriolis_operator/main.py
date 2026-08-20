"""Kopf entrypoint and CoriolisAppliance reconciliation handlers."""

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any

import kopf
from kubernetes import client  # type: ignore[import-untyped]

from coriolis_operator.reconcile import build_state_config_map, build_status

GROUP = "coriolis.cloudbase.it"
VERSION = "v1alpha1"
PLURAL = "coriolisappliances"
WATCH_NAMESPACE = os.environ.get("WATCH_NAMESPACE") or None
LIVENESS_ENDPOINT = os.environ.get("LIVENESS_ENDPOINT", "http://0.0.0.0:8080/healthz")


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
    version = str(spec["version"])
    owner = {
        "apiVersion": f"{GROUP}/{VERSION}",
        "kind": "CoriolisAppliance",
        "name": name,
        "uid": str(meta["uid"]),
    }
    body = build_state_config_map(
        name=name,
        namespace=namespace,
        version=version,
        generation=generation,
        owner=owner,
    )
    api = core_api or client.CoreV1Api()
    api.api_client.default_headers["Content-Type"] = "application/apply-patch+yaml"
    api.patch_namespaced_config_map(
        name=body["metadata"]["name"],
        namespace=namespace,
        body=body,
        field_manager="coriolis-operator",
        force=True,
    )
    prior_conditions = status.get("conditions") if status is not None else None
    return build_status(generation, prior_conditions=prior_conditions)


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


def main() -> None:
    """Run the operator with optional namespace restriction and liveness probe."""
    log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), None)
    logging.basicConfig(level=log_level if isinstance(log_level, int) else logging.INFO)
    asyncio.run(
        kopf.operator(namespace=WATCH_NAMESPACE, liveness_endpoint=LIVENESS_ENDPOINT)
    )
