"""Pure values and Kubernetes resource bodies used by the controller."""

import base64
import hashlib
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from kubernetes.utils.quantity import parse_quantity  # type: ignore[import-untyped]

from coriolis_operator.configuration import (
    KubernetesCoriolisRenderInputs,
    SensitiveCoriolisEndpoints,
)
from coriolis_operator.mariadb import (
    MARIADB_BOOTSTRAP_COMPLETE_MARKER,
    MARIADB_CONFIG_DIR,
    MARIADB_CONFIG_KEYS,
    MARIADB_DATA_DIR,
    MARIADB_IMAGE,
    MARIADB_IMAGE_PULL_SECRET_NAME,
    MARIADB_PVC_ACCESS_MODE,
    MARIADB_PVC_RETENTION_VALUE,
    MARIADB_PVC_VOLUME_MODE,
    MARIADB_REPLICAS,
    MARIADB_RUN_AS_ID,
    MARIADB_RUNTIME_DIR,
    MARIADB_SECRET_CONFIG_KEYS,
    MARIADB_SECRET_DIR,
    MARIADB_SOCKET_PATH,
    MARIADB_SUPPLEMENTAL_GROUP,
    MARIADB_TERMINATION_GRACE_PERIOD_SECONDS,
    MariaDBSettings,
    SensitiveMariaDBCredentials,
    render_mariadb_config,
    render_sensitive_mariadb_config,
)
from coriolis_operator.memcached import (
    MEMCACHED_ARGS,
    MEMCACHED_COMMAND,
    MEMCACHED_IMAGE,
    MEMCACHED_IMAGE_PULL_SECRET_NAME,
    MEMCACHED_PORT,
    MEMCACHED_PROTOCOL_PROBE_COMMAND,
    MEMCACHED_REPLICAS,
    MEMCACHED_RUN_AS_ID,
    MEMCACHED_TERMINATION_GRACE_PERIOD_SECONDS,
)

STATE_CONFIG_MAP_SUFFIX = "-operator-state"
CONFIG_MAP_NAME_MAX_LENGTH = 253
DNS_LABEL_MAX_LENGTH = 63
NAME_HASH_LENGTH = 12
MAX_COMPONENT_LENGTH = DNS_LABEL_MAX_LENGTH - 1 - NAME_HASH_LENGTH - 2

DNS_SUBDOMAIN_RE = re.compile(
    r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*"
)
DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?")

SUPPORTED_PROFILE = "core"
SUPPORTED_INITIAL_VERSION = "2603.4"

RUNTIME_NOT_IMPLEMENTED_MESSAGE = "The appliance runtime is not implemented yet."
NOT_DEGRADED_MESSAGE = "The appliance is not degraded."
NOT_RECONCILED_MESSAGE = "No resources were applied to Kubernetes."
RECONCILED_MESSAGE = (
    "The foundational appliance resources, dependency Services, MariaDB resources, "
    "and controller state marker were reconciled in Kubernetes; runtime readiness is "
    "not implemented yet."
)
UPGRADE_NOT_SUPPORTED_MESSAGE = "The core profile has no supported upgrade path."
INVALID_RUNTIME_CONFIGURATION_MESSAGE = (
    "Complete valid MariaDB storage and resource configuration is required."
)
RESOURCE_COLLISION_MESSAGE = (
    "The existing resource '{namespace}/{name}' conflicts with operator-managed "
    "identity and was not modified."
)

MARKER_MANAGED = "managed"
MARKER_LEGACY = "legacy"
MARKER_COLLISION = "collision"

OPERATOR_MANAGEMENT_LABELS = (
    "app.kubernetes.io/name",
    "app.kubernetes.io/instance",
    "app.kubernetes.io/version",
    "app.kubernetes.io/component",
    "app.kubernetes.io/part-of",
    "app.kubernetes.io/managed-by",
    "coriolis.cloudbase.it/appliance",
    "coriolis.cloudbase.it/component",
)
APPLIANCE_NAME_ANNOTATION = "coriolis.cloudbase.it/appliance-name"
RETENTION_ANNOTATION = "coriolis.cloudbase.it/retention"

CORIOLIS_CREDENTIALS_KEYS = frozenset(
    {
        "coriolis_database_password",
        "coriolis_keystone_password",
        "temp_keypair_password",
    }
)
INFRASTRUCTURE_CREDENTIALS_KEYS = frozenset(
    {
        "database_password",
        "rabbitmq_password",
        "keystone_admin_password",
    }
)
CORIOLIS_CONFIG_KEYS = frozenset(
    {
        "coriolis-api.wsgi",
        "wsgi-coriolis.conf",
        "vixdisklib.conf",
        "api-paste.ini",
        "policy.yml",
        "coriolis.release",
    }
)
CORIOLIS_CONFIG_SECRET_KEYS = frozenset({"coriolis.conf"})
DEPENDENCY_SERVICES = (
    ("rabbitmq", 5672),
    ("memcached", 11211),
    ("mariadb", 3306),
    ("keystone", 5000),
)

# Pre-existing resources the operator references read-only and must never
# create, adopt, mutate, or classify as operator-retained. The registry pull
# Secret is the canonical example; it sits outside the retained-resource
# classifier/reconciliation policy entirely and is always classified as a
# collision (fail closed) even before the absent check.
EXTERNAL_READ_ONLY_RESOURCES = ("coriolis-appliance-registry",)

Condition = tuple[str, str, str, str]


def retry_conditions(category: str) -> list[Condition]:
    """Return value-safe conditions for a retryable Kubernetes API failure."""
    message = "Kubernetes resource reconciliation will be retried."
    return [
        (
            "Accepted",
            "True",
            "Accepted",
            "The requested profile and version are supported.",
        ),
        ("Progressing", "True", "Retrying", message),
        ("Reconciled", "False", category, message),
        ("Ready", "False", "RuntimeNotImplemented", RUNTIME_NOT_IMPLEMENTED_MESSAGE),
        ("Degraded", "True", category, message),
        ("Upgradeable", "False", "UpgradeNotSupported", UPGRADE_NOT_SUPPORTED_MESSAGE),
    ]


class RetainedClassification(Enum):
    """Classification of an existing resource against operator-retained identity."""

    ABSENT = "absent"
    REUSE = "reuse"
    COLLISION = "collision"


class OwnedClassification(Enum):
    """Classification of an existing resource against operator-owned identity."""

    ABSENT = "absent"
    MANAGED = "managed"
    COLLISION = "collision"


@dataclass(frozen=True)
class FoundationalResourcePreflight:
    """Pure preflight outcome for foundational appliance resources."""

    classifications: Mapping[str, RetainedClassification | OwnedClassification]
    credentials: Mapping[str, Mapping[str, str]] = field(repr=False)


@dataclass(frozen=True)
class MariaDBResourcePreflight:
    """Pure preflight outcome and apply-ordered MariaDB resource bodies."""

    classifications: Mapping[str, RetainedClassification | OwnedClassification]
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class MemcachedResourcePreflight:
    """Pure preflight outcome and desired Memcached Deployment body."""

    classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


def state_config_map_name(resource_name: str) -> str:
    """Return the deterministic state ConfigMap name for an appliance."""
    final_label = resource_name.rsplit(".", 1)[-1]
    if (
        len(resource_name) + len(STATE_CONFIG_MAP_SUFFIX) <= CONFIG_MAP_NAME_MAX_LENGTH
        and len(final_label) + len(STATE_CONFIG_MAP_SUFFIX) <= DNS_LABEL_MAX_LENGTH
    ):
        return f"{resource_name}{STATE_CONFIG_MAP_SUFFIX}"

    name_hash = hashlib.sha256(resource_name.encode()).hexdigest()[:NAME_HASH_LENGTH]
    suffix_label = f"{name_hash}{STATE_CONFIG_MAP_SUFFIX}"
    prefix_length = CONFIG_MAP_NAME_MAX_LENGTH - len(suffix_label) - 1
    prefix = resource_name[:prefix_length].rstrip(".-")
    return f"{prefix}.{suffix_label}"


def _validate_appliance_name(appliance_name: str) -> None:
    if not isinstance(appliance_name, str) or not appliance_name:
        raise ValueError("appliance_name must be a non-empty string")
    if len(appliance_name) > CONFIG_MAP_NAME_MAX_LENGTH:
        raise ValueError(
            "appliance_name must be at most 253 characters (a DNS subdomain)"
        )
    if not DNS_SUBDOMAIN_RE.fullmatch(appliance_name):
        raise ValueError("appliance_name must be a lowercase DNS subdomain")


def _validate_component(component: str) -> None:
    if not isinstance(component, str) or not component:
        raise ValueError("component must be a non-empty string")
    if len(component) > MAX_COMPONENT_LENGTH:
        raise ValueError(
            "component is too long to fit a hashed resource name within "
            f"{DNS_LABEL_MAX_LENGTH} characters"
        )
    if not DNS_LABEL_RE.fullmatch(component):
        raise ValueError("component must be a lowercase DNS label token")


def appliance_resource_name(appliance_name: str, component: str) -> str:
    """Return a deterministic, label-safe resource name for a component."""
    _validate_appliance_name(appliance_name)
    _validate_component(component)
    desired_name = f"{appliance_name}-{component}"
    if "." not in appliance_name and len(desired_name) <= DNS_LABEL_MAX_LENGTH:
        return desired_name
    visible_prefix = appliance_name.replace(".", "-")
    name_hash = hashlib.sha256(desired_name.encode()).hexdigest()[:NAME_HASH_LENGTH]
    suffix = f"-{name_hash}-{component}"
    prefix = visible_prefix[: DNS_LABEL_MAX_LENGTH - len(suffix)].rstrip("-")
    return f"{prefix}{suffix}"


def kubernetes_coriolis_render_inputs(
    appliance_name: str,
) -> KubernetesCoriolisRenderInputs:
    """Return fixed Kubernetes configuration inputs for an appliance."""
    return KubernetesCoriolisRenderInputs(
        bind_address="0.0.0.0",
        coriolis_port=7667,
        coriolis_config_dir="/etc/coriolis",
        coriolis_vmware_vix_disklib_log_dir="/var/log/coriolis/vmware-root",
        endpoints=SensitiveCoriolisEndpoints(
            rabbitmq_host=appliance_resource_name(appliance_name, "rabbitmq"),
            memcached_host=appliance_resource_name(appliance_name, "memcached"),
            database_host=appliance_resource_name(appliance_name, "mariadb"),
            keystone_host=appliance_resource_name(appliance_name, "keystone"),
        ),
    )


def appliance_identity(appliance_name: str) -> str:
    """Return a label-safe identity token for an appliance."""
    _validate_appliance_name(appliance_name)
    if "." not in appliance_name and len(appliance_name) <= DNS_LABEL_MAX_LENGTH:
        return appliance_name
    visible_prefix = appliance_name.replace(".", "-")
    name_hash = hashlib.sha256(appliance_name.encode()).hexdigest()[:NAME_HASH_LENGTH]
    suffix = f"-{name_hash}"
    prefix = visible_prefix[: DNS_LABEL_MAX_LENGTH - len(suffix)].rstrip("-")
    return f"{prefix}{suffix}"


def _owner_reference(owner: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": str(owner["apiVersion"]),
        "kind": str(owner["kind"]),
        "name": str(owner["name"]),
        "uid": str(owner["uid"]),
        "controller": True,
    }


def build_resource_metadata(
    *,
    resource_name: str,
    namespace: str,
    appliance_name: str,
    component: str,
    accepted_version: str,
    owner: Mapping[str, Any] | None = None,
    retention: str | None = None,
) -> dict[str, Any]:
    """Build standard Kubernetes metadata for an owned or retained object."""
    if (owner is None) == (retention is None):
        raise ValueError("exactly one of owner or retention must be provided")
    _validate_component(component)
    identity = appliance_identity(appliance_name)
    if retention is not None:
        if not isinstance(retention, str) or not retention:
            raise ValueError("retention must be a non-empty string")
        if not DNS_LABEL_RE.fullmatch(retention):
            raise ValueError("retention must be a lowercase DNS label class")
    metadata: dict[str, Any] = {
        "name": resource_name,
        "namespace": namespace,
        "labels": {
            "app.kubernetes.io/name": "coriolis",
            "app.kubernetes.io/instance": identity,
            "app.kubernetes.io/version": accepted_version,
            "app.kubernetes.io/component": component,
            "app.kubernetes.io/part-of": "coriolis-appliance",
            "app.kubernetes.io/managed-by": "coriolis-operator",
            "coriolis.cloudbase.it/appliance": identity,
            "coriolis.cloudbase.it/component": component,
        },
        "annotations": {
            "coriolis.cloudbase.it/appliance-name": appliance_name,
        },
    }
    if retention is not None:
        metadata["annotations"]["coriolis.cloudbase.it/retention"] = retention
    else:
        assert owner is not None
        metadata["ownerReferences"] = [_owner_reference(owner)]
    return metadata


def build_state_config_map(
    *,
    name: str,
    namespace: str,
    profile: str,
    accepted_version: str,
    generation: int,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the complete server-side apply body for the owned ConfigMap."""
    metadata = build_resource_metadata(
        resource_name=state_config_map_name(name),
        namespace=namespace,
        appliance_name=name,
        component="operator-state",
        accepted_version=accepted_version,
        owner=owner,
    )
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": metadata,
        "data": {
            "acceptedVersion": accepted_version,
            "profile": profile,
            "generation": str(generation),
        },
    }


def build_dependency_service(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    component: str,
) -> dict[str, Any]:
    """Build the ClusterIP Service for one supported appliance dependency."""
    ports = dict(DEPENDENCY_SERVICES)
    if component not in ports:
        raise ValueError("unsupported dependency service component")
    port = ports[component]
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": build_resource_metadata(
            resource_name=appliance_resource_name(appliance_name, component),
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        ),
        "spec": {
            "type": "ClusterIP",
            "selector": {
                "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
                "coriolis.cloudbase.it/component": component,
            },
            "ports": [
                {
                    "name": component,
                    "protocol": "TCP",
                    "port": port,
                    "targetPort": port,
                }
            ],
        },
    }


def _validated_opaque_values(
    values: Mapping[str, str], expected_keys: frozenset[str], object_name: str
) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise ValueError(f"{object_name} values must be a mapping")
    provided_keys = set(values)
    missing = expected_keys - provided_keys
    extra = provided_keys - expected_keys
    if missing or extra:
        raise ValueError(f"{object_name} values must have exactly the required keys")
    if any(not isinstance(value, str) for value in values.values()):
        raise ValueError(f"{object_name} values must be strings")
    return dict(values)


def _encoded_secret_data(values: Mapping[str, str]) -> dict[str, str]:
    return {
        key: base64.b64encode(value.encode("utf-8")).decode("ascii")
        for key, value in values.items()
    }


def _generate_credentials(
    keys: frozenset[str], token_factory: Callable[[int], str]
) -> dict[str, str]:
    values = {key: token_factory(32) for key in sorted(keys)}
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise ValueError("credential token factory must return a non-empty string")
    return values


def generate_coriolis_credentials(
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> dict[str, str]:
    """Generate independent values for the retained Coriolis credentials Secret."""
    return _generate_credentials(CORIOLIS_CREDENTIALS_KEYS, token_factory)


def generate_infrastructure_credentials(
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> dict[str, str]:
    """Generate values for the retained infrastructure credentials Secret."""
    return _generate_credentials(INFRASTRUCTURE_CREDENTIALS_KEYS, token_factory)


def _build_retained_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    component: str,
    retention: str,
    values: Mapping[str, str],
    expected_keys: frozenset[str],
) -> dict[str, Any]:
    resource_name = appliance_resource_name(appliance_name, component)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": build_resource_metadata(
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            retention=retention,
        ),
        "type": "Opaque",
        "data": _encoded_secret_data(
            _validated_opaque_values(values, expected_keys, resource_name)
        ),
    }


def build_coriolis_credentials_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    retention: str,
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the retained Coriolis credentials Secret apply body."""
    return _build_retained_secret(
        appliance_name=appliance_name,
        namespace=namespace,
        accepted_version=accepted_version,
        component="coriolis-credentials",
        retention=retention,
        values=values,
        expected_keys=CORIOLIS_CREDENTIALS_KEYS,
    )


def build_infrastructure_credentials_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    retention: str,
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the retained infrastructure credentials Secret apply body."""
    return _build_retained_secret(
        appliance_name=appliance_name,
        namespace=namespace,
        accepted_version=accepted_version,
        component="infrastructure-credentials",
        retention=retention,
        values=values,
        expected_keys=INFRASTRUCTURE_CREDENTIALS_KEYS,
    )


def build_coriolis_config_map(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced Coriolis configuration ConfigMap apply body."""
    component = "coriolis-config"
    resource_name = appliance_resource_name(appliance_name, component)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": build_resource_metadata(
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        ),
        "data": _validated_opaque_values(values, CORIOLIS_CONFIG_KEYS, resource_name),
    }


def build_coriolis_config_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced Coriolis configuration Secret apply body."""
    component = "coriolis-config-secret"
    resource_name = appliance_resource_name(appliance_name, component)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": build_resource_metadata(
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        ),
        "type": "Opaque",
        "data": _encoded_secret_data(
            _validated_opaque_values(values, CORIOLIS_CONFIG_SECRET_KEYS, resource_name)
        ),
    }


def _mapping_value(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is None:
        value = getattr(obj, _snake_case(name), None)
    return value


def validated_retained_secret_values(
    *, existing: Any, expected_keys: frozenset[str]
) -> dict[str, str]:
    """Return validated decoded values from a persisted retained Secret."""
    api_version = _field(existing, "apiVersion")
    if api_version is not None and api_version != "v1":
        raise ValueError("retained Secret apiVersion is invalid")
    kind = _field(existing, "kind")
    if kind is not None and kind != "Secret":
        raise ValueError("retained Secret kind is invalid")
    if _field(existing, "type") != "Opaque":
        raise ValueError("retained Secret type is invalid")
    if _field(existing, "stringData") is not None:
        raise ValueError("retained Secret must not contain stringData")

    data = _field(existing, "data")
    if not isinstance(data, Mapping):
        raise ValueError("retained Secret data is invalid")
    if set(data) != expected_keys:
        raise ValueError("retained Secret data keys are invalid")

    decoded: dict[str, str] = {}
    for key in sorted(expected_keys):
        encoded = data[key]
        if not isinstance(encoded, str):
            raise ValueError("retained Secret data values are invalid")
        try:
            value = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            raise ValueError("retained Secret data encoding is invalid") from None
        if not value:
            raise ValueError("retained Secret data values must be non-empty")
        decoded[key] = value
    return decoded


def _owner_reference_dict(ref: Any) -> dict[str, Any]:
    if isinstance(ref, Mapping):
        return {
            "apiVersion": str(ref.get("apiVersion") or ""),
            "kind": str(ref.get("kind") or ""),
            "name": str(ref.get("name") or ""),
            "uid": str(ref.get("uid") or ""),
            "controller": ref.get("controller"),
        }
    return {
        "apiVersion": str(getattr(ref, "api_version", "") or ""),
        "kind": str(getattr(ref, "kind", "") or ""),
        "name": str(getattr(ref, "name", "") or ""),
        "uid": str(getattr(ref, "uid", "") or ""),
        "controller": getattr(ref, "controller", None),
    }


def _controller_owner_reference(owner_references: Any) -> dict[str, Any] | None:
    refs = owner_references if isinstance(owner_references, list) else []
    for ref in refs:
        normalized = _owner_reference_dict(ref)
        if normalized["controller"] is True:
            return normalized
    return None


def _owner_references_match(
    existing: dict[str, Any] | None, desired: dict[str, Any] | None
) -> bool:
    if existing is None or desired is None:
        return False
    return (
        all(
            existing.get(key) == desired.get(key)
            for key in ("apiVersion", "kind", "name", "uid")
        )
        and existing.get("controller") is True
        and desired.get("controller") is True
    )


def _normalize_marker(existing: Any) -> dict[str, Any]:
    metadata = _field(existing, "metadata")
    if metadata is None:
        metadata = {}
    return {
        "labels": _mapping_value(_field(metadata, "labels")),
        "annotations": _mapping_value(_field(metadata, "annotations")),
        "ownerReferences": _controller_owner_reference(
            _field(metadata, "ownerReferences")
        ),
        "data": _mapping_value(_field(existing, "data")),
    }


def classify_existing_marker(
    *,
    existing: Any,
    desired: Mapping[str, Any],
) -> str:
    """Classify an existing marker ConfigMap as managed, legacy, or collision."""
    existing_norm = _normalize_marker(existing)
    desired_metadata = _field(desired, "metadata")
    if not isinstance(desired_metadata, Mapping):
        return MARKER_COLLISION
    desired_labels = _mapping_value(desired_metadata.get("labels"))
    desired_annotations = _mapping_value(desired_metadata.get("annotations"))
    desired_data = _mapping_value(desired.get("data"))
    desired_owner = _controller_owner_reference(desired_metadata.get("ownerReferences"))

    labels = existing_norm["labels"]
    annotations = existing_norm["annotations"]
    existing_owner = existing_norm["ownerReferences"]
    existing_data = existing_norm["data"]

    management_present = (
        any(key in labels for key in OPERATOR_MANAGEMENT_LABELS)
        or APPLIANCE_NAME_ANNOTATION in annotations
        or RETENTION_ANNOTATION in annotations
    )

    if management_present:
        if RETENTION_ANNOTATION in annotations:
            return MARKER_COLLISION
        for key, expected in desired_labels.items():
            if labels.get(key) != expected:
                return MARKER_COLLISION
        if annotations.get(APPLIANCE_NAME_ANNOTATION) != desired_annotations.get(
            APPLIANCE_NAME_ANNOTATION
        ):
            return MARKER_COLLISION
        if not _owner_references_match(existing_owner, desired_owner):
            return MARKER_COLLISION
        return MARKER_MANAGED

    if not _owner_references_match(existing_owner, desired_owner):
        return MARKER_COLLISION
    if existing_data.get("acceptedVersion") != desired_data.get("acceptedVersion"):
        return MARKER_COLLISION
    if existing_data.get("profile") != desired_data.get("profile"):
        return MARKER_COLLISION
    return MARKER_LEGACY


def classify_retained_resource(
    *,
    existing: Any,
    resource_name: str,
    namespace: str,
    appliance_name: str,
    component: str,
    accepted_version: str,
    retention: str,
) -> RetainedClassification:
    """Classify an existing resource against the operator's retained identity.

    An absent resource is eligible for creation (``ABSENT``). A retained
    resource may be reused automatically only when its deterministic name and
    namespace and every operator-controlled identity field match the retained
    metadata produced by ``build_resource_metadata``: the full appliance-name
    annotation, the standard managed/identity labels, the component label, and
    the exact retention annotation/class. The object must have **no** owner
    references; owner plus retention is a collision even if an owner UID
    matches. Missing/partial/conflicting operator identity metadata is a
    collision and is never normalized; unrelated extra labels/annotations are
    permitted. A matching ownerless retained object is ``REUSE`` (no mutation
    or adoption patching). Anything else is ``COLLISION``.

    The creating appliance CR UID is deliberately **not** part of the identity:
    retained resources survive CR deletion/recreation, so automatic exact-match
    reattachment must work even when the CR UID changes. Any stale
    ``coriolis.cloudbase.it/appliance-uid`` annotation is treated as an
    unrelated extra annotation and ignored.

    External/pre-existing resources (see ``EXTERNAL_READ_ONLY_RESOURCES``)
    fail closed as ``COLLISION`` regardless of presence or forged matching
    metadata, before the absent check, and are never reused.

    ``existing`` may be a mapping-shaped fake or a real Kubernetes model object
    with snake_case attributes. This is a namespace trust boundary: anyone who
    can create resources in the namespace can forge the operator's identity
    metadata, so automatic exact-match reuse must not be treated as proof of
    origin.
    """
    if resource_name in EXTERNAL_READ_ONLY_RESOURCES:
        return RetainedClassification.COLLISION
    if existing is None:
        return RetainedClassification.ABSENT
    metadata = _field(existing, "metadata")
    if metadata is None:
        return RetainedClassification.COLLISION
    if _field(metadata, "name") != resource_name:
        return RetainedClassification.COLLISION
    if _field(metadata, "namespace") != namespace:
        return RetainedClassification.COLLISION
    owner_refs = _field(metadata, "ownerReferences")
    if owner_refs is not None and len(list(owner_refs)) > 0:
        return RetainedClassification.COLLISION
    expected = build_resource_metadata(
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        retention=retention,
    )
    labels = _mapping_value(_field(metadata, "labels"))
    annotations = _mapping_value(_field(metadata, "annotations"))
    for key, value in expected["labels"].items():
        if labels.get(key) != value:
            return RetainedClassification.COLLISION
    for key, value in expected["annotations"].items():
        if annotations.get(key) != value:
            return RetainedClassification.COLLISION
    return RetainedClassification.REUSE


def classify_owned_resource(
    *,
    existing: Any,
    resource_name: str,
    namespace: str,
    appliance_name: str,
    component: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> OwnedClassification:
    """Classify an existing resource against the operator-owned identity."""
    if existing is None:
        return OwnedClassification.ABSENT
    metadata = _field(existing, "metadata")
    if metadata is None:
        return OwnedClassification.COLLISION
    if _field(metadata, "name") != resource_name:
        return OwnedClassification.COLLISION
    if _field(metadata, "namespace") != namespace:
        return OwnedClassification.COLLISION

    expected = build_resource_metadata(
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    labels = _mapping_value(_field(metadata, "labels"))
    annotations = _mapping_value(_field(metadata, "annotations"))
    if RETENTION_ANNOTATION in annotations:
        return OwnedClassification.COLLISION
    for key, value in expected["labels"].items():
        if labels.get(key) != value:
            return OwnedClassification.COLLISION
    if annotations.get(APPLIANCE_NAME_ANNOTATION) != expected["annotations"].get(
        APPLIANCE_NAME_ANNOTATION
    ):
        return OwnedClassification.COLLISION
    if not _owner_references_match(
        _controller_owner_reference(_field(metadata, "ownerReferences")),
        _controller_owner_reference(expected.get("ownerReferences")),
    ):
        return OwnedClassification.COLLISION
    return OwnedClassification.MANAGED


def build_memcached_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the one-replica, ephemeral Memcached Deployment."""
    component = "memcached"
    resource_name = appliance_resource_name(appliance_name, component)
    metadata = build_resource_metadata(
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    selector = {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": component,
    }
    protocol_probe = {
        "exec": {
            "command": [
                "/usr/bin/bash",
                "-ec",
                MEMCACHED_PROTOCOL_PROBE_COMMAND,
            ]
        }
    }
    container = {
        "name": component,
        "image": MEMCACHED_IMAGE,
        "command": [MEMCACHED_COMMAND],
        "args": list(MEMCACHED_ARGS),
        "ports": [
            {
                "name": component,
                "containerPort": MEMCACHED_PORT,
                "protocol": "TCP",
            }
        ],
        "securityContext": {
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "startupProbe": dict(
            protocol_probe,
            periodSeconds=2,
            timeoutSeconds=2,
            failureThreshold=30,
        ),
        "readinessProbe": dict(
            protocol_probe,
            periodSeconds=5,
            timeoutSeconds=2,
            failureThreshold=3,
            successThreshold=1,
        ),
        "livenessProbe": dict(
            protocol_probe,
            periodSeconds=10,
            timeoutSeconds=2,
            failureThreshold=3,
        ),
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": MEMCACHED_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "imagePullSecrets": [{"name": MEMCACHED_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "runAsUser": MEMCACHED_RUN_AS_ID,
                        "runAsGroup": MEMCACHED_RUN_AS_ID,
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        MEMCACHED_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [container],
                },
            },
        },
    }


def preflight_memcached_resource(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    memcached_deployment: Any | None,
) -> MemcachedResourcePreflight:
    """Classify Memcached before building its desired Deployment."""
    component = "memcached"
    resource_name = appliance_resource_name(appliance_name, component)
    classification = classify_owned_resource(
        existing=memcached_deployment,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    if classification is OwnedClassification.COLLISION:
        return MemcachedResourcePreflight(classification, ())
    return MemcachedResourcePreflight(
        classification,
        (
            build_memcached_deployment(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
            ),
        ),
    )


def build_mariadb_data_pvc(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    settings: MariaDBSettings,
) -> dict[str, Any]:
    """Build the ownerless retained MariaDB data claim."""
    component = "mariadb-data"
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": build_resource_metadata(
            resource_name=appliance_resource_name(appliance_name, component),
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            retention=MARIADB_PVC_RETENTION_VALUE,
        ),
        "spec": {
            "storageClassName": settings.storage.storage_class_name,
            "accessModes": [MARIADB_PVC_ACCESS_MODE],
            "volumeMode": MARIADB_PVC_VOLUME_MODE,
            "resources": {"requests": {"storage": settings.storage.size}},
        },
    }


def build_mariadb_config_map(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced MariaDB configuration ConfigMap."""
    component = "mariadb-config"
    resource_name = appliance_resource_name(appliance_name, component)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": build_resource_metadata(
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        ),
        "data": _validated_opaque_values(values, MARIADB_CONFIG_KEYS, resource_name),
    }


def build_mariadb_config_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced MariaDB configuration Secret."""
    component = "mariadb-config-secret"
    resource_name = appliance_resource_name(appliance_name, component)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": build_resource_metadata(
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        ),
        "type": "Opaque",
        "data": _encoded_secret_data(
            _validated_opaque_values(values, MARIADB_SECRET_CONFIG_KEYS, resource_name)
        ),
    }


def _mariadb_container_security_context() -> dict[str, Any]:
    return {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def build_mariadb_stateful_set(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    settings: MariaDBSettings,
) -> dict[str, Any]:
    """Build the MariaDB StatefulSet using the retained data claim."""
    component = "mariadb"
    resource_name = appliance_resource_name(appliance_name, component)
    data_claim_name = appliance_resource_name(appliance_name, "mariadb-data")
    config_map_name = appliance_resource_name(appliance_name, "mariadb-config")
    secret_name = appliance_resource_name(appliance_name, "mariadb-config-secret")
    metadata = build_resource_metadata(
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    selector = {
        "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
        "coriolis.cloudbase.it/component": component,
    }
    config_items = [
        {"key": "my.cnf", "path": "my.cnf", "mode": 0o444},
        {"key": "prepare-mariadb.sh", "path": "prepare-mariadb.sh", "mode": 0o555},
        {"key": "start-mariadb.sh", "path": "start-mariadb.sh", "mode": 0o555},
    ]
    secret_items = [
        {"key": key, "path": key, "mode": 0o440}
        for key in sorted(MARIADB_SECRET_CONFIG_KEYS)
    ]
    volumes = [
        {"name": "data", "persistentVolumeClaim": {"claimName": data_claim_name}},
        {"name": "runtime", "emptyDir": {}},
        {"name": "tmp", "emptyDir": {}},
        {
            "name": "config",
            "configMap": {"name": config_map_name, "items": config_items},
        },
        {
            "name": "secret",
            "secret": {"secretName": secret_name, "items": secret_items},
        },
    ]
    init_container = {
        "name": "prepare-mariadb",
        "image": MARIADB_IMAGE,
        "args": [f"{MARIADB_CONFIG_DIR}/prepare-mariadb.sh"],
        "securityContext": _mariadb_container_security_context(),
        "volumeMounts": [
            {"name": "data", "mountPath": MARIADB_DATA_DIR},
            {"name": "runtime", "mountPath": MARIADB_RUNTIME_DIR},
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "config", "mountPath": MARIADB_CONFIG_DIR, "readOnly": True},
            {"name": "secret", "mountPath": MARIADB_SECRET_DIR, "readOnly": True},
        ],
    }
    main_container = {
        "name": "mariadb",
        "image": MARIADB_IMAGE,
        "args": [f"{MARIADB_CONFIG_DIR}/start-mariadb.sh"],
        "ports": [{"name": "mariadb", "containerPort": 3306, "protocol": "TCP"}],
        "resources": {
            "requests": {
                "cpu": settings.resources.requests_cpu,
                "memory": settings.resources.requests_memory,
            },
            "limits": {
                "cpu": settings.resources.limits_cpu,
                "memory": settings.resources.limits_memory,
            },
        },
        "securityContext": _mariadb_container_security_context(),
        "volumeMounts": [
            {"name": "data", "mountPath": MARIADB_DATA_DIR},
            {"name": "runtime", "mountPath": MARIADB_RUNTIME_DIR},
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "config", "mountPath": MARIADB_CONFIG_DIR, "readOnly": True},
        ],
        "startupProbe": {
            "exec": {
                "command": [
                    "sh",
                    "-ec",
                    f"test -f {MARIADB_BOOTSTRAP_COMPLETE_MARKER} && "
                    f"mariadb-admin --defaults-file={MARIADB_RUNTIME_DIR}/admin.cnf "
                    f"--socket={MARIADB_SOCKET_PATH} ping --silent && "
                    f"mariadb --defaults-file={MARIADB_RUNTIME_DIR}/admin.cnf "
                    "--execute=SELECT\\ 1",
                ]
            },
            "periodSeconds": 10,
            "timeoutSeconds": 5,
            "failureThreshold": 30,
        },
        "readinessProbe": {
            "exec": {
                "command": [
                    "sh",
                    "-ec",
                    f"test -f {MARIADB_RUNTIME_DIR}/coriolis.cnf && "
                    f"mariadb --defaults-file={MARIADB_RUNTIME_DIR}/coriolis.cnf "
                    "--execute=SELECT\\ 1",
                ]
            },
            "periodSeconds": 10,
            "timeoutSeconds": 5,
            "failureThreshold": 3,
            "successThreshold": 1,
        },
        "livenessProbe": {
            "exec": {
                "command": [
                    "sh",
                    "-ec",
                    f"mariadb-admin --defaults-file={MARIADB_RUNTIME_DIR}/admin.cnf "
                    "ping --silent && "
                    f"mariadb --defaults-file={MARIADB_RUNTIME_DIR}/admin.cnf "
                    "--execute=SELECT\\ 1",
                ]
            },
            "periodSeconds": 10,
            "timeoutSeconds": 5,
            "failureThreshold": 6,
        },
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": metadata,
        "spec": {
            "serviceName": resource_name,
            "replicas": MARIADB_REPLICAS,
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "imagePullSecrets": [{"name": MARIADB_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "runAsUser": MARIADB_RUN_AS_ID,
                        "runAsGroup": MARIADB_RUN_AS_ID,
                        "fsGroup": MARIADB_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                        "supplementalGroups": [MARIADB_SUPPLEMENTAL_GROUP],
                    },
                    "terminationGracePeriodSeconds": (
                        MARIADB_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "initContainers": [init_container],
                    "containers": [main_container],
                    "volumes": volumes,
                },
            },
        },
    }


def _positive_quantity(value: Any) -> Decimal | None:
    if type(value) is not str or not value or value.strip() != value:
        return None
    try:
        parsed = parse_quantity(value)
    except (ArithmeticError, TypeError, ValueError):
        return None
    if not isinstance(parsed, Decimal) or not parsed.is_finite() or parsed <= 0:
        return None
    return parsed


def classify_mariadb_data_pvc(
    *,
    existing: Any,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    settings: MariaDBSettings,
) -> RetainedClassification:
    """Classify a retained MariaDB claim, including immutable desired spec fields."""
    resource_name = appliance_resource_name(appliance_name, "mariadb-data")
    classification = classify_retained_resource(
        existing=existing,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component="mariadb-data",
        accepted_version=accepted_version,
        retention=MARIADB_PVC_RETENTION_VALUE,
    )
    if classification is not RetainedClassification.REUSE:
        return classification
    if (_field(existing, "apiVersion") not in (None, "v1")) or (
        _field(existing, "kind") not in (None, "PersistentVolumeClaim")
    ):
        return RetainedClassification.COLLISION
    spec = _field(existing, "spec")
    if spec is None:
        return RetainedClassification.COLLISION
    if isinstance(spec, Mapping):
        allowed = {
            "storageClassName",
            "accessModes",
            "volumeMode",
            "resources",
            "volumeName",
        }
        if set(spec) - allowed:
            return RetainedClassification.COLLISION
    if _field(spec, "storageClassName") != settings.storage.storage_class_name:
        return RetainedClassification.COLLISION
    access_modes = _field(spec, "accessModes")
    if not isinstance(access_modes, Sequence) or isinstance(access_modes, str):
        return RetainedClassification.COLLISION
    if list(access_modes) != [MARIADB_PVC_ACCESS_MODE]:
        return RetainedClassification.COLLISION
    if _field(spec, "volumeMode") != MARIADB_PVC_VOLUME_MODE:
        return RetainedClassification.COLLISION
    if _field(spec, "selector") is not None:
        return RetainedClassification.COLLISION
    if (
        _field(spec, "dataSource") is not None
        or _field(spec, "dataSourceRef") is not None
        or _field(spec, "volumeAttributesClassName") is not None
    ):
        return RetainedClassification.COLLISION
    resources = _field(spec, "resources")
    if isinstance(resources, Mapping) and set(resources) - {"requests", "limits"}:
        return RetainedClassification.COLLISION
    requests = _field(resources, "requests")
    if not isinstance(requests, Mapping) or set(requests) != {"storage"}:
        return RetainedClassification.COLLISION
    if _positive_quantity(requests.get("storage")) != _positive_quantity(
        settings.storage.size
    ):
        return RetainedClassification.COLLISION
    limits = _field(resources, "limits")
    if limits not in (None, {}) and (not isinstance(limits, Mapping) or bool(limits)):
        return RetainedClassification.COLLISION
    return RetainedClassification.REUSE


def preflight_mariadb_resources(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    settings: MariaDBSettings,
    credentials: SensitiveMariaDBCredentials,
    owner: Mapping[str, Any],
    mariadb_data_pvc: Any | None,
    mariadb_config_map: Any | None,
    mariadb_config_secret: Any | None,
    mariadb_stateful_set: Any | None,
) -> MariaDBResourcePreflight:
    """Classify MariaDB resources before rendering any sensitive configuration."""
    resources = (
        ("mariadb-data", mariadb_data_pvc),
        ("mariadb-config", mariadb_config_map),
        ("mariadb-config-secret", mariadb_config_secret),
        ("mariadb", mariadb_stateful_set),
    )
    classifications: dict[str, RetainedClassification | OwnedClassification] = {}
    for component, existing in resources:
        resource_name = appliance_resource_name(appliance_name, component)
        classification: RetainedClassification | OwnedClassification
        if component == "mariadb-data":
            classification = classify_mariadb_data_pvc(
                existing=existing,
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                settings=settings,
            )
        else:
            classification = classify_owned_resource(
                existing=existing,
                resource_name=resource_name,
                namespace=namespace,
                appliance_name=appliance_name,
                component=component,
                accepted_version=accepted_version,
                owner=owner,
            )
        classifications[resource_name] = classification
        if classification.value == "collision":
            return MariaDBResourcePreflight(classifications, ())

    config_values = render_mariadb_config()
    secret_values = render_sensitive_mariadb_config(credentials=credentials)
    return MariaDBResourcePreflight(
        classifications,
        (
            build_mariadb_data_pvc(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                settings=settings,
            ),
            build_mariadb_config_map(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
                values=config_values,
            ),
            build_mariadb_config_secret(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
                values=secret_values,
            ),
            build_mariadb_stateful_set(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
                settings=settings,
            ),
        ),
    )


def preflight_foundational_resources(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    retention: str,
    owner: Mapping[str, Any],
    coriolis_credentials_secret: Any | None,
    infrastructure_credentials_secret: Any | None,
    coriolis_config_map: Any | None,
    coriolis_config_secret: Any | None,
    coriolis_token_factory: Callable[[int], str] = secrets.token_urlsafe,
    infrastructure_token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> FoundationalResourcePreflight:
    """Classify foundational resources before validating or generating credentials."""
    retained = (
        (
            "coriolis-credentials",
            coriolis_credentials_secret,
            CORIOLIS_CREDENTIALS_KEYS,
            generate_coriolis_credentials,
            coriolis_token_factory,
        ),
        (
            "infrastructure-credentials",
            infrastructure_credentials_secret,
            INFRASTRUCTURE_CREDENTIALS_KEYS,
            generate_infrastructure_credentials,
            infrastructure_token_factory,
        ),
    )
    owned = (
        ("coriolis-config", coriolis_config_map),
        ("coriolis-config-secret", coriolis_config_secret),
    )
    names = {
        component: appliance_resource_name(appliance_name, component)
        for component, *_ in (*retained, *owned)
    }
    classifications: dict[str, RetainedClassification | OwnedClassification] = {}
    for component, existing, _, _, _ in retained:
        resource_name = names[component]
        classifications[resource_name] = classify_retained_resource(
            existing=existing,
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            retention=retention,
        )
    for component, existing in owned:
        resource_name = names[component]
        classifications[resource_name] = classify_owned_resource(
            existing=existing,
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        )
    if any(value.value == "collision" for value in classifications.values()):
        return FoundationalResourcePreflight(classifications, {})

    credentials: dict[str, Mapping[str, str]] = {}
    for component, existing, expected_keys, _, _ in retained:
        resource_name = names[component]
        if classifications[resource_name] is RetainedClassification.REUSE:
            try:
                credentials[resource_name] = validated_retained_secret_values(
                    existing=existing, expected_keys=expected_keys
                )
            except ValueError:
                classifications[resource_name] = RetainedClassification.COLLISION
    if any(value.value == "collision" for value in classifications.values()):
        return FoundationalResourcePreflight(classifications, {})

    for component, _, _, generator, token_factory in retained:
        resource_name = names[component]
        if classifications[resource_name] is RetainedClassification.ABSENT:
            credentials[resource_name] = generator(token_factory)
    return FoundationalResourcePreflight(classifications, credentials)


def collision_conditions(namespace: str, name: str) -> list[Condition]:
    """Conditions when an existing foundational resource blocks reconciliation."""
    message = RESOURCE_COLLISION_MESSAGE.format(namespace=namespace, name=name)
    return [
        (
            "Accepted",
            "True",
            "Accepted",
            "The requested profile and version are supported.",
        ),
        ("Progressing", "False", "ResourceCollision", message),
        ("Reconciled", "False", "ResourceCollision", message),
        ("Ready", "False", "RuntimeNotImplemented", RUNTIME_NOT_IMPLEMENTED_MESSAGE),
        ("Degraded", "True", "ResourceCollision", message),
        (
            "Upgradeable",
            "False",
            "UpgradeNotSupported",
            UPGRADE_NOT_SUPPORTED_MESSAGE,
        ),
    ]


def invalid_runtime_configuration_conditions() -> list[Condition]:
    """Return stable conditions for incomplete or invalid runtime configuration."""
    reason = "InvalidRuntimeConfiguration"
    return [
        (
            "Accepted",
            "True",
            "Accepted",
            "The requested profile and version are supported.",
        ),
        ("Progressing", "False", reason, INVALID_RUNTIME_CONFIGURATION_MESSAGE),
        ("Reconciled", "False", reason, INVALID_RUNTIME_CONFIGURATION_MESSAGE),
        ("Ready", "False", "RuntimeNotImplemented", RUNTIME_NOT_IMPLEMENTED_MESSAGE),
        ("Degraded", "True", reason, INVALID_RUNTIME_CONFIGURATION_MESSAGE),
        (
            "Upgradeable",
            "False",
            "UpgradeNotSupported",
            UPGRADE_NOT_SUPPORTED_MESSAGE,
        ),
    ]


def accepted_conditions() -> list[Condition]:
    """Conditions for a valid foundational-resource reconcile."""
    return [
        (
            "Accepted",
            "True",
            "Accepted",
            "The requested profile and version are supported.",
        ),
        (
            "Progressing",
            "False",
            "RuntimeNotImplemented",
            RUNTIME_NOT_IMPLEMENTED_MESSAGE,
        ),
        ("Reconciled", "True", "Reconciled", RECONCILED_MESSAGE),
        ("Ready", "False", "RuntimeNotImplemented", RUNTIME_NOT_IMPLEMENTED_MESSAGE),
        ("Degraded", "False", "NotDegraded", NOT_DEGRADED_MESSAGE),
        ("Upgradeable", "False", "UpgradeNotSupported", UPGRADE_NOT_SUPPORTED_MESSAGE),
    ]


def rejected_conditions(reason: str, message: str) -> list[Condition]:
    """Conditions for an initial acceptance rejection (profile or version)."""
    return [
        ("Accepted", "False", reason, message),
        (
            "Progressing",
            "False",
            "RuntimeNotImplemented",
            RUNTIME_NOT_IMPLEMENTED_MESSAGE,
        ),
        ("Reconciled", "False", "NotReconciled", NOT_RECONCILED_MESSAGE),
        ("Ready", "False", "RuntimeNotImplemented", RUNTIME_NOT_IMPLEMENTED_MESSAGE),
        ("Degraded", "False", "NotDegraded", NOT_DEGRADED_MESSAGE),
        ("Upgradeable", "False", "UpgradeNotSupported", UPGRADE_NOT_SUPPORTED_MESSAGE),
    ]


def blocked_conditions(
    accepted_version: str, requested_version: str
) -> list[Condition]:
    """Conditions when a requested version change is blocked."""
    version_change_message = (
        f"Version change from '{accepted_version}' to '{requested_version}' "
        "is rejected; the accepted version is immutable."
    )
    return [
        ("Accepted", "False", "VersionChangeRejected", version_change_message),
        (
            "Progressing",
            "False",
            "RuntimeNotImplemented",
            RUNTIME_NOT_IMPLEMENTED_MESSAGE,
        ),
        ("Reconciled", "False", "NotReconciled", NOT_RECONCILED_MESSAGE),
        ("Ready", "False", "RuntimeNotImplemented", RUNTIME_NOT_IMPLEMENTED_MESSAGE),
        ("Degraded", "False", "NotDegraded", NOT_DEGRADED_MESSAGE),
        (
            "Upgradeable",
            "False",
            "UpgradeBlocked",
            "Version changes are blocked; the accepted version is immutable.",
        ),
    ]


def _transition_time(
    condition_type: str,
    condition_status: str,
    prior_conditions: object,
    timestamp: str,
) -> str:
    if not isinstance(prior_conditions, list):
        return timestamp

    for condition in prior_conditions:
        if not isinstance(condition, Mapping):
            continue
        previous_time = condition.get("lastTransitionTime")
        if (
            condition.get("type") == condition_type
            and condition.get("status") == condition_status
            and isinstance(previous_time, str)
            and _is_rfc3339(previous_time)
        ):
            return previous_time
    return timestamp


def _is_rfc3339(value: str) -> bool:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _condition(
    condition_type: str,
    condition_status: str,
    reason: str,
    message: str,
    generation: int,
    prior_conditions: object,
    timestamp_value: str,
) -> dict[str, Any]:
    return {
        "type": condition_type,
        "status": condition_status,
        "reason": reason,
        "message": message,
        "observedGeneration": generation,
        "lastTransitionTime": _transition_time(
            condition_type,
            condition_status,
            prior_conditions,
            timestamp_value,
        ),
    }


def build_status(
    generation: int,
    *,
    accepted_version: str | None,
    conditions: Sequence[Condition],
    prior_conditions: object = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build status for the given conditions, preserving transition times."""
    now = timestamp or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    timestamp_value = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    result: dict[str, Any] = {
        "observedGeneration": generation,
        "conditions": [
            _condition(
                condition_type,
                condition_status,
                reason,
                message,
                generation,
                prior_conditions,
                timestamp_value,
            )
            for condition_type, condition_status, reason, message in conditions
        ],
    }
    if accepted_version is not None:
        result["acceptedVersion"] = accepted_version
    return result
