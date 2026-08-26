"""Pure values and Kubernetes resource bodies used by the controller."""

import base64
import hashlib
import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from kubernetes.utils.quantity import parse_quantity  # type: ignore[import-untyped]

from coriolis_operator.api import (
    API_ARGS,
    API_COMMAND,
    API_CONFIG_DIR,
    API_CONFIG_MAP_KEYS,
    API_IMAGE,
    API_IMAGE_PULL_SECRET_NAME,
    API_LOCKS_DIR,
    API_LOG_DIR,
    API_PORT,
    API_PROTOCOL_PROBE,
    API_REPLICAS,
    API_RUN_AS_ID,
    API_TERMINATION_GRACE_PERIOD_SECONDS,
)
from coriolis_operator.common import (
    BOOTSTRAP_ACTIVE_DEADLINE_SECONDS,
    BOOTSTRAP_BACKOFF_LIMIT,
    BOOTSTRAP_COMPONENT,
    BOOTSTRAP_CONFIG_DIR,
    BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR,
    BOOTSTRAP_IMAGE_PULL_SECRET_NAME,
    BOOTSTRAP_INFRA_CREDENTIALS_DIR,
    BOOTSTRAP_SCRIPT_ANNOTATION,
    BOOTSTRAP_SCRIPT_DIR,
    BOOTSTRAP_SCRIPT_FILENAME,
    BOOTSTRAP_SCRIPT_PATH,
    BOOTSTRAP_TEMPLATE_ANNOTATION,
    BOOTSTRAP_TERMINATION_GRACE_PERIOD_SECONDS,
    BOOTSTRAP_UID_GID,
    CONDUCTOR_IMAGE,
    render_bootstrap_script,
)
from coriolis_operator.conductor import (
    CONDUCTOR_ARGS,
    CONDUCTOR_COMMAND,
    CONDUCTOR_COMPONENT,
    CONDUCTOR_CONFIG_DIR,
    CONDUCTOR_CONFIG_MAP_KEYS,
    CONDUCTOR_IMAGE_PULL_SECRET_NAME,
    CONDUCTOR_LOCKS_DIR,
    CONDUCTOR_LOG_DIR,
    CONDUCTOR_REPLICAS,
    CONDUCTOR_RUN_AS_ID,
    CONDUCTOR_TERMINATION_GRACE_PERIOD_SECONDS,
)
from coriolis_operator.configuration import (
    KubernetesCoriolisRenderInputs,
    SensitiveCoriolisEndpoints,
)
from coriolis_operator.deployer_manager import (
    DEPLOYER_MANAGER_ARGS,
    DEPLOYER_MANAGER_COMMAND,
    DEPLOYER_MANAGER_COMPONENT,
    DEPLOYER_MANAGER_CONFIG_DIR,
    DEPLOYER_MANAGER_IMAGE,
    DEPLOYER_MANAGER_IMAGE_PULL_SECRET_NAME,
    DEPLOYER_MANAGER_LOG_DIR,
    DEPLOYER_MANAGER_REPLICAS,
    DEPLOYER_MANAGER_RUN_AS_ID,
    DEPLOYER_MANAGER_TERMINATION_GRACE_PERIOD_SECONDS,
)
from coriolis_operator.keystone import (
    KEYSTONE_AUTH_REQUEST_PATH,
    KEYSTONE_BOOTSTRAP_PATH,
    KEYSTONE_CONFIG_KEYS,
    KEYSTONE_CONFIG_PATH,
    KEYSTONE_CREDENTIAL_KEYS_DIR,
    KEYSTONE_FERNET_KEYS_DIR,
    KEYSTONE_IMAGE,
    KEYSTONE_IMAGE_PULL_SECRET_NAME,
    KEYSTONE_KEY_KEYS,
    KEYSTONE_PORT,
    KEYSTONE_REPLICAS,
    KEYSTONE_RUN_AS_ID,
    KEYSTONE_SECRET_CONFIG_KEYS,
    KEYSTONE_SUPPLEMENTAL_GROUP,
    KEYSTONE_TERMINATION_GRACE_PERIOD_SECONDS,
    SensitiveKeystoneCredentials,
    generate_keystone_keys,
    render_keystone_config,
    render_sensitive_keystone_config,
)
from coriolis_operator.mariadb import (
    MARIADB_BOOTSTRAP_COMPLETE_MARKER,
    MARIADB_BOOTSTRAP_SCHEMA_ANNOTATION,
    MARIADB_BOOTSTRAP_SCHEMA_VALUE,
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
from coriolis_operator.minion_manager import (
    MINION_MANAGER_ARGS,
    MINION_MANAGER_COMMAND,
    MINION_MANAGER_COMPONENT,
    MINION_MANAGER_CONFIG_DIR,
    MINION_MANAGER_CONFIG_MAP_KEYS,
    MINION_MANAGER_IMAGE,
    MINION_MANAGER_IMAGE_PULL_SECRET_NAME,
    MINION_MANAGER_LOG_DIR,
    MINION_MANAGER_REPLICAS,
    MINION_MANAGER_RUN_AS_ID,
    MINION_MANAGER_TERMINATION_GRACE_PERIOD_SECONDS,
)
from coriolis_operator.rabbitmq import (
    RABBITMQ_CONFIG_DIR,
    RABBITMQ_CONFIG_KEYS,
    RABBITMQ_DATA_DIR,
    RABBITMQ_DIAGNOSTICS,
    RABBITMQ_IMAGE,
    RABBITMQ_IMAGE_PULL_SECRET_NAME,
    RABBITMQ_LOG_DIR,
    RABBITMQ_PORT,
    RABBITMQ_PVC_ACCESS_MODE,
    RABBITMQ_PVC_RETENTION_VALUE,
    RABBITMQ_PVC_VOLUME_MODE,
    RABBITMQ_REPLICAS,
    RABBITMQ_RUN_AS_ID,
    RABBITMQ_RUNTIME_DIR,
    RABBITMQ_SECRET_DIR,
    RABBITMQ_TERMINATION_GRACE_PERIOD_SECONDS,
    RabbitMQSettings,
    render_rabbitmq_config,
)
from coriolis_operator.scheduler import (
    SCHEDULER_ARGS,
    SCHEDULER_COMMAND,
    SCHEDULER_COMPONENT,
    SCHEDULER_CONFIG_DIR,
    SCHEDULER_CONFIG_MAP_KEYS,
    SCHEDULER_IMAGE,
    SCHEDULER_IMAGE_PULL_SECRET_NAME,
    SCHEDULER_LOG_DIR,
    SCHEDULER_REPLICAS,
    SCHEDULER_RUN_AS_ID,
    SCHEDULER_TERMINATION_GRACE_PERIOD_SECONDS,
)
from coriolis_operator.transfer_cron import (
    TRANSFER_CRON_ARGS,
    TRANSFER_CRON_COMMAND,
    TRANSFER_CRON_COMPONENT,
    TRANSFER_CRON_CONFIG_DIR,
    TRANSFER_CRON_CONFIG_MAP_KEYS,
    TRANSFER_CRON_IMAGE,
    TRANSFER_CRON_IMAGE_PULL_SECRET_NAME,
    TRANSFER_CRON_LOG_DIR,
    TRANSFER_CRON_REPLICAS,
    TRANSFER_CRON_RUN_AS_ID,
    TRANSFER_CRON_TERMINATION_GRACE_PERIOD_SECONDS,
)
from coriolis_operator.worker import (
    WORKER_ARGS,
    WORKER_COMMAND,
    WORKER_COMPONENT,
    WORKER_CONFIG_DIR,
    WORKER_EXPORT_DIR,
    WORKER_IMAGE,
    WORKER_IMAGE_PULL_SECRET_NAME,
    WORKER_LOG_DIR,
    WORKER_REPLICAS,
    WORKER_TERMINATION_GRACE_PERIOD_SECONDS,
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
    "The foundational appliance resources, dependency Services, MariaDB, RabbitMQ, "
    "Memcached, Keystone, Coriolis-common bootstrap, Coriolis API, Coriolis "
    "conductor, Coriolis scheduler, Coriolis transfer-cron, Coriolis minion-manager, "
    "Coriolis deployer-manager, Coriolis worker, and controller state marker were "
    "reconciled in Kubernetes; runtime "
    "readiness is not implemented yet."
)
UPGRADE_NOT_SUPPORTED_MESSAGE = "The core profile has no supported upgrade path."
INVALID_RUNTIME_CONFIGURATION_MESSAGE = (
    "Complete valid MariaDB and RabbitMQ storage and resource configuration is "
    "required."
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
class RabbitMQResourcePreflight:
    """Pure preflight outcome and apply-ordered RabbitMQ resource bodies."""

    classifications: Mapping[str, RetainedClassification | OwnedClassification]
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class MemcachedResourcePreflight:
    """Pure preflight outcome and desired Memcached Deployment body."""

    classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class APIResourcePreflight:
    """Pure preflight outcome and desired Coriolis API resource bodies."""

    service_classification: OwnedClassification
    deployment_classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class ConductorResourcePreflight:
    """Pure preflight outcome and desired Coriolis conductor Deployment body."""

    classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class SchedulerResourcePreflight:
    """Pure preflight outcome and desired Coriolis scheduler Deployment body."""

    classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class TransferCronResourcePreflight:
    """Pure preflight outcome and desired Coriolis transfer-cron Deployment body."""

    classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class MinionManagerResourcePreflight:
    """Pure preflight outcome and desired Coriolis minion-manager Deployment."""

    classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class DeployerManagerResourcePreflight:
    """Pure preflight outcome and desired Coriolis deployer-manager Deployment."""

    classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class WorkerResourcePreflight:
    """Pure preflight outcome and desired Coriolis worker Deployment."""

    classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


@dataclass(frozen=True)
class KeystoneResourcePreflight:
    """Pure Keystone preflight outcome with credentials and manifests redacted."""

    classifications: Mapping[str, RetainedClassification | OwnedClassification]
    credentials: Mapping[str, Mapping[str, str]] = field(repr=False)
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


def build_api_service(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the internal ClusterIP Service for the Coriolis API."""
    component = "coriolis-api"
    metadata = build_resource_metadata(
        resource_name=appliance_resource_name(appliance_name, component),
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": metadata,
        "spec": {
            "type": "ClusterIP",
            "selector": {
                "coriolis.cloudbase.it/appliance": appliance_identity(appliance_name),
                "coriolis.cloudbase.it/component": component,
            },
            "ports": [
                {
                    "name": "api",
                    "protocol": "TCP",
                    "port": API_PORT,
                    "targetPort": API_PORT,
                }
            ],
        },
    }


def build_api_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the restricted single-replica direct Coriolis API Deployment."""
    component = "coriolis-api"
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
    probe = {
        "exec": {"command": ["/usr/bin/python3", "-c", API_PROTOCOL_PROBE]},
    }
    volume_mounts = [
        {"name": "config", "mountPath": API_CONFIG_DIR, "readOnly": True},
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "logs", "mountPath": API_LOG_DIR},
        {"name": "locks", "mountPath": API_LOCKS_DIR},
    ]
    container = {
        "name": component,
        "image": API_IMAGE,
        "command": [API_COMMAND],
        "args": list(API_ARGS),
        "ports": [{"name": "api", "containerPort": API_PORT, "protocol": "TCP"}],
        "securityContext": {
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumeMounts": volume_mounts,
        "startupProbe": dict(
            probe,
            periodSeconds=2,
            timeoutSeconds=5,
            failureThreshold=30,
        ),
        "readinessProbe": dict(
            probe,
            periodSeconds=5,
            timeoutSeconds=5,
            failureThreshold=3,
            successThreshold=1,
        ),
        "livenessProbe": dict(
            probe,
            periodSeconds=10,
            timeoutSeconds=5,
            failureThreshold=6,
        ),
    }
    config_map_name = appliance_resource_name(appliance_name, "coriolis-config")
    config_secret_name = appliance_resource_name(
        appliance_name, "coriolis-config-secret"
    )
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": API_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "imagePullSecrets": [{"name": API_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "runAsUser": API_RUN_AS_ID,
                        "runAsGroup": API_RUN_AS_ID,
                        "fsGroup": API_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        API_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "config",
                            "projected": {
                                "sources": [
                                    {
                                        "configMap": {
                                            "name": config_map_name,
                                            "items": [
                                                {
                                                    "key": key,
                                                    "path": key,
                                                    "mode": 0o444,
                                                }
                                                for key in API_CONFIG_MAP_KEYS
                                            ],
                                        }
                                    },
                                    {
                                        "secret": {
                                            "name": config_secret_name,
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
                    ],
                },
            },
        },
    }


def preflight_api_resources(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    api_service: Any | None,
    api_deployment: Any | None,
) -> APIResourcePreflight:
    """Classify all Coriolis API resources before building desired bodies."""
    component = "coriolis-api"
    resource_name = appliance_resource_name(appliance_name, component)
    service_classification = classify_owned_resource(
        existing=api_service,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    deployment_classification = classify_owned_resource(
        existing=api_deployment,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    if OwnedClassification.COLLISION in (
        service_classification,
        deployment_classification,
    ):
        return APIResourcePreflight(
            service_classification, deployment_classification, ()
        )
    return APIResourcePreflight(
        service_classification,
        deployment_classification,
        (
            build_api_service(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
            ),
            build_api_deployment(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
            ),
        ),
    )


def build_conductor_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the restricted single-replica direct Coriolis conductor Deployment.

    The conductor exposes no supported HTTP endpoint, so it intentionally has no
    Service, ports, listeners, or probes; Pod readiness is not RPC readiness.
    """
    component = CONDUCTOR_COMPONENT
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
    config_map_name = appliance_resource_name(appliance_name, "coriolis-config")
    config_secret_name = appliance_resource_name(
        appliance_name, "coriolis-config-secret"
    )
    container = {
        "name": component,
        "image": CONDUCTOR_IMAGE,
        "command": [CONDUCTOR_COMMAND],
        "args": list(CONDUCTOR_ARGS),
        "env": [
            {"name": "HOME", "value": "/tmp"},
            {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
        ],
        "securityContext": {
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumeMounts": [
            {"name": "config", "mountPath": CONDUCTOR_CONFIG_DIR, "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "logs", "mountPath": CONDUCTOR_LOG_DIR},
            {"name": "locks", "mountPath": CONDUCTOR_LOCKS_DIR},
        ],
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": CONDUCTOR_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "imagePullSecrets": [{"name": CONDUCTOR_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "runAsUser": CONDUCTOR_RUN_AS_ID,
                        "runAsGroup": CONDUCTOR_RUN_AS_ID,
                        "fsGroup": CONDUCTOR_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        CONDUCTOR_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "config",
                            "projected": {
                                "sources": [
                                    {
                                        "configMap": {
                                            "name": config_map_name,
                                            "items": [
                                                {
                                                    "key": key,
                                                    "path": key,
                                                    "mode": 0o444,
                                                }
                                                for key in CONDUCTOR_CONFIG_MAP_KEYS
                                            ],
                                        }
                                    },
                                    {
                                        "secret": {
                                            "name": config_secret_name,
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
                    ],
                },
            },
        },
    }


def preflight_conductor_resource(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    conductor_deployment: Any | None,
) -> ConductorResourcePreflight:
    """Classify the conductor before building its desired Deployment."""
    component = CONDUCTOR_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    classification = classify_owned_resource(
        existing=conductor_deployment,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    if classification is OwnedClassification.COLLISION:
        return ConductorResourcePreflight(classification, ())
    return ConductorResourcePreflight(
        classification,
        (
            build_conductor_deployment(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
            ),
        ),
    )


def build_scheduler_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the restricted single-replica direct Coriolis scheduler Deployment."""
    component = SCHEDULER_COMPONENT
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
    config_map_name = appliance_resource_name(appliance_name, "coriolis-config")
    config_secret_name = appliance_resource_name(
        appliance_name, "coriolis-config-secret"
    )
    container = {
        "name": component,
        "image": SCHEDULER_IMAGE,
        "command": [SCHEDULER_COMMAND],
        "args": list(SCHEDULER_ARGS),
        "env": [
            {"name": "HOME", "value": "/tmp"},
            {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
        ],
        "securityContext": {
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumeMounts": [
            {"name": "config", "mountPath": SCHEDULER_CONFIG_DIR, "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "logs", "mountPath": SCHEDULER_LOG_DIR},
        ],
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": SCHEDULER_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "imagePullSecrets": [{"name": SCHEDULER_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "runAsUser": SCHEDULER_RUN_AS_ID,
                        "runAsGroup": SCHEDULER_RUN_AS_ID,
                        "fsGroup": SCHEDULER_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        SCHEDULER_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "config",
                            "projected": {
                                "sources": [
                                    {
                                        "configMap": {
                                            "name": config_map_name,
                                            "items": [
                                                {
                                                    "key": key,
                                                    "path": key,
                                                    "mode": 0o444,
                                                }
                                                for key in SCHEDULER_CONFIG_MAP_KEYS
                                            ],
                                        }
                                    },
                                    {
                                        "secret": {
                                            "name": config_secret_name,
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
                    ],
                },
            },
        },
    }


def preflight_scheduler_resource(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    scheduler_deployment: Any | None,
) -> SchedulerResourcePreflight:
    """Classify the scheduler before building its desired Deployment."""
    component = SCHEDULER_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    classification = classify_owned_resource(
        existing=scheduler_deployment,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    if classification is OwnedClassification.COLLISION:
        return SchedulerResourcePreflight(classification, ())
    return SchedulerResourcePreflight(
        classification,
        (
            build_scheduler_deployment(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
            ),
        ),
    )


def build_transfer_cron_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the restricted single-replica Coriolis transfer-cron Deployment."""
    component = TRANSFER_CRON_COMPONENT
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
    config_map_name = appliance_resource_name(appliance_name, "coriolis-config")
    config_secret_name = appliance_resource_name(
        appliance_name, "coriolis-config-secret"
    )
    container = {
        "name": component,
        "image": TRANSFER_CRON_IMAGE,
        "command": [TRANSFER_CRON_COMMAND],
        "args": list(TRANSFER_CRON_ARGS),
        "env": [
            {"name": "HOME", "value": "/tmp"},
            {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
        ],
        "securityContext": {
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumeMounts": [
            {"name": "config", "mountPath": TRANSFER_CRON_CONFIG_DIR, "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "logs", "mountPath": TRANSFER_CRON_LOG_DIR},
        ],
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": TRANSFER_CRON_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "imagePullSecrets": [
                        {"name": TRANSFER_CRON_IMAGE_PULL_SECRET_NAME}
                    ],
                    "securityContext": {
                        "runAsUser": TRANSFER_CRON_RUN_AS_ID,
                        "runAsGroup": TRANSFER_CRON_RUN_AS_ID,
                        "fsGroup": TRANSFER_CRON_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        TRANSFER_CRON_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "config",
                            "projected": {
                                "sources": [
                                    {
                                        "configMap": {
                                            "name": config_map_name,
                                            "items": [
                                                {"key": key, "path": key, "mode": 0o444}
                                                for key in TRANSFER_CRON_CONFIG_MAP_KEYS
                                            ],
                                        }
                                    },
                                    {
                                        "secret": {
                                            "name": config_secret_name,
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
                    ],
                },
            },
        },
    }


def preflight_transfer_cron_resource(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    transfer_cron_deployment: Any | None,
) -> TransferCronResourcePreflight:
    """Classify transfer-cron before building its desired Deployment."""
    component = TRANSFER_CRON_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    classification = classify_owned_resource(
        existing=transfer_cron_deployment,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    if classification is OwnedClassification.COLLISION:
        return TransferCronResourcePreflight(classification, ())
    return TransferCronResourcePreflight(
        classification,
        (
            build_transfer_cron_deployment(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
            ),
        ),
    )


def build_minion_manager_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the restricted single-replica Coriolis minion-manager Deployment."""
    component = MINION_MANAGER_COMPONENT
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
    config_map_name = appliance_resource_name(appliance_name, "coriolis-config")
    config_secret_name = appliance_resource_name(
        appliance_name, "coriolis-config-secret"
    )
    container = {
        "name": component,
        "image": MINION_MANAGER_IMAGE,
        "command": [MINION_MANAGER_COMMAND],
        "args": list(MINION_MANAGER_ARGS),
        "env": [
            {"name": "HOME", "value": "/tmp"},
            {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
        ],
        "securityContext": {
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumeMounts": [
            {
                "name": "config",
                "mountPath": MINION_MANAGER_CONFIG_DIR,
                "readOnly": True,
            },
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "logs", "mountPath": MINION_MANAGER_LOG_DIR},
        ],
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": MINION_MANAGER_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "imagePullSecrets": [
                        {"name": MINION_MANAGER_IMAGE_PULL_SECRET_NAME}
                    ],
                    "securityContext": {
                        "runAsUser": MINION_MANAGER_RUN_AS_ID,
                        "runAsGroup": MINION_MANAGER_RUN_AS_ID,
                        "fsGroup": MINION_MANAGER_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        MINION_MANAGER_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "config",
                            "projected": {
                                "sources": [
                                    {
                                        "configMap": {
                                            "name": config_map_name,
                                            "items": [
                                                {"key": key, "path": key, "mode": 0o444}
                                                for key in (
                                                    MINION_MANAGER_CONFIG_MAP_KEYS
                                                )
                                            ],
                                        }
                                    },
                                    {
                                        "secret": {
                                            "name": config_secret_name,
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
                    ],
                },
            },
        },
    }


def preflight_minion_manager_resource(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    minion_manager_deployment: Any | None,
) -> MinionManagerResourcePreflight:
    """Classify minion-manager before building its desired Deployment."""
    component = MINION_MANAGER_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    classification = classify_owned_resource(
        existing=minion_manager_deployment,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    if classification is OwnedClassification.COLLISION:
        return MinionManagerResourcePreflight(classification, ())
    return MinionManagerResourcePreflight(
        classification,
        (
            build_minion_manager_deployment(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
            ),
        ),
    )


def build_deployer_manager_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the restricted single-replica Coriolis deployer-manager Deployment."""
    component = DEPLOYER_MANAGER_COMPONENT
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
    config_secret_name = appliance_resource_name(
        appliance_name, "coriolis-config-secret"
    )
    container = {
        "name": component,
        "image": DEPLOYER_MANAGER_IMAGE,
        "command": [DEPLOYER_MANAGER_COMMAND],
        "args": list(DEPLOYER_MANAGER_ARGS),
        "env": [
            {"name": "HOME", "value": "/tmp"},
            {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
        ],
        "securityContext": {
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumeMounts": [
            {
                "name": "config",
                "mountPath": DEPLOYER_MANAGER_CONFIG_DIR,
                "readOnly": True,
            },
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "logs", "mountPath": DEPLOYER_MANAGER_LOG_DIR},
        ],
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": DEPLOYER_MANAGER_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "imagePullSecrets": [
                        {"name": DEPLOYER_MANAGER_IMAGE_PULL_SECRET_NAME}
                    ],
                    "securityContext": {
                        "runAsUser": DEPLOYER_MANAGER_RUN_AS_ID,
                        "runAsGroup": DEPLOYER_MANAGER_RUN_AS_ID,
                        "fsGroup": DEPLOYER_MANAGER_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        DEPLOYER_MANAGER_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "config",
                            "projected": {
                                "sources": [
                                    {
                                        "secret": {
                                            "name": config_secret_name,
                                            "items": [
                                                {
                                                    "key": "coriolis.conf",
                                                    "path": "coriolis.conf",
                                                    "mode": 0o440,
                                                }
                                            ],
                                        }
                                    }
                                ]
                            },
                        },
                        {"name": "tmp", "emptyDir": {"medium": "Memory"}},
                        {"name": "logs", "emptyDir": {}},
                    ],
                },
            },
        },
    }


def preflight_deployer_manager_resource(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    deployer_manager_deployment: Any | None,
) -> DeployerManagerResourcePreflight:
    """Classify deployer-manager before building its desired Deployment."""
    component = DEPLOYER_MANAGER_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    classification = classify_owned_resource(
        existing=deployer_manager_deployment,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    if classification is OwnedClassification.COLLISION:
        return DeployerManagerResourcePreflight(classification, ())
    return DeployerManagerResourcePreflight(
        classification,
        (
            build_deployer_manager_deployment(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
            ),
        ),
    )


def build_worker_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the one-replica Coriolis worker Deployment."""
    component = WORKER_COMPONENT
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
    config_secret_name = appliance_resource_name(
        appliance_name, "coriolis-config-secret"
    )
    container = {
        "name": component,
        "image": WORKER_IMAGE,
        "imagePullPolicy": "Always",
        "command": [WORKER_COMMAND],
        "args": list(WORKER_ARGS),
        "env": [
            {"name": "HOME", "value": "/tmp"},
            {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
        ],
        "securityContext": {
            "runAsUser": 0,
            "runAsGroup": 0,
            "privileged": True,
            "allowPrivilegeEscalation": True,
            "readOnlyRootFilesystem": True,
            "seccompProfile": {"type": "Unconfined"},
        },
        "volumeMounts": [
            {"name": "config", "mountPath": WORKER_CONFIG_DIR, "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
            {"name": "logs", "mountPath": WORKER_LOG_DIR},
            {"name": "export", "mountPath": WORKER_EXPORT_DIR},
            {"name": "dev", "mountPath": "/dev"},
            {"name": "lib-modules", "mountPath": "/lib/modules", "readOnly": True},
        ],
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": WORKER_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "hostname": resource_name,
                    "imagePullSecrets": [{"name": WORKER_IMAGE_PULL_SECRET_NAME}],
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        WORKER_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "config",
                            "secret": {"secretName": config_secret_name},
                        },
                        {"name": "tmp", "emptyDir": {"medium": "Memory"}},
                        {"name": "logs", "emptyDir": {}},
                        {"name": "export", "emptyDir": {}},
                        {
                            "name": "dev",
                            "hostPath": {"path": "/dev", "type": "Directory"},
                        },
                        {
                            "name": "lib-modules",
                            "hostPath": {"path": "/lib/modules", "type": "Directory"},
                        },
                    ],
                },
            },
        },
    }


def preflight_worker_resource(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    worker_deployment: Any | None,
) -> WorkerResourcePreflight:
    """Classify the worker before building its desired Deployment."""
    component = WORKER_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    classification = classify_owned_resource(
        existing=worker_deployment,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    if classification is OwnedClassification.COLLISION:
        return WorkerResourcePreflight(classification, ())
    return WorkerResourcePreflight(
        classification,
        (
            build_worker_deployment(
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
                "metadata": {
                    "labels": dict(metadata["labels"]),
                    "annotations": {
                        MARIADB_BOOTSTRAP_SCHEMA_ANNOTATION: (
                            MARIADB_BOOTSTRAP_SCHEMA_VALUE
                        )
                    },
                },
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


def build_rabbitmq_data_pvc(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    settings: RabbitMQSettings,
) -> dict[str, Any]:
    """Build the ownerless retained RabbitMQ data claim."""
    component = "rabbitmq-data"
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": build_resource_metadata(
            resource_name=appliance_resource_name(appliance_name, component),
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            retention=RABBITMQ_PVC_RETENTION_VALUE,
        ),
        "spec": {
            "storageClassName": settings.storage.storage_class_name,
            "accessModes": [RABBITMQ_PVC_ACCESS_MODE],
            "volumeMode": RABBITMQ_PVC_VOLUME_MODE,
            "resources": {"requests": {"storage": settings.storage.size}},
        },
    }


def build_rabbitmq_config_map(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced RabbitMQ configuration ConfigMap."""
    component = "rabbitmq-config"
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
        "data": _validated_opaque_values(values, RABBITMQ_CONFIG_KEYS, resource_name),
    }


def _rabbitmq_container_security_context() -> dict[str, Any]:
    return {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def build_rabbitmq_stateful_set(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    settings: RabbitMQSettings,
) -> dict[str, Any]:
    """Build the RabbitMQ StatefulSet using the retained data claim."""
    component = "rabbitmq"
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
    config_items = [
        {"key": "rabbitmq.conf", "path": "rabbitmq.conf", "mode": 0o444},
        {"key": "start-rabbitmq.sh", "path": "start-rabbitmq.sh", "mode": 0o555},
    ]
    diagnostics = RABBITMQ_DIAGNOSTICS
    running_and_listener = (
        f"{diagnostics} -q check_running && {diagnostics} -q check_port_listener "
        f"{RABBITMQ_PORT}"
    )
    container = {
        "name": component,
        "image": RABBITMQ_IMAGE,
        "command": [RABBITMQ_CONFIG_DIR + "/start-rabbitmq.sh"],
        "ports": [
            {"name": component, "containerPort": RABBITMQ_PORT, "protocol": "TCP"}
        ],
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
        "securityContext": _rabbitmq_container_security_context(),
        "volumeMounts": [
            {"name": "data", "mountPath": RABBITMQ_DATA_DIR},
            {"name": "runtime", "mountPath": RABBITMQ_RUNTIME_DIR},
            {"name": "logs", "mountPath": RABBITMQ_LOG_DIR},
            {"name": "config", "mountPath": RABBITMQ_CONFIG_DIR, "readOnly": True},
            {"name": "secret", "mountPath": RABBITMQ_SECRET_DIR, "readOnly": True},
        ],
        "startupProbe": {
            "exec": {"command": ["/bin/sh", "-ec", running_and_listener]},
            "periodSeconds": 5,
            "timeoutSeconds": 5,
            "failureThreshold": 36,
        },
        "readinessProbe": {
            "exec": {
                "command": [
                    "/bin/sh",
                    "-ec",
                    f"{running_and_listener} && {diagnostics} -q check_local_alarms",
                ]
            },
            "periodSeconds": 10,
            "timeoutSeconds": 15,
            "failureThreshold": 3,
            "successThreshold": 1,
        },
        "livenessProbe": {
            "exec": {"command": ["/bin/sh", "-ec", f"{diagnostics} -q check_running"]},
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
            "replicas": RABBITMQ_REPLICAS,
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "imagePullSecrets": [{"name": RABBITMQ_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "runAsUser": RABBITMQ_RUN_AS_ID,
                        "runAsGroup": RABBITMQ_RUN_AS_ID,
                        "fsGroup": RABBITMQ_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "terminationGracePeriodSeconds": (
                        RABBITMQ_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "data",
                            "persistentVolumeClaim": {
                                "claimName": appliance_resource_name(
                                    appliance_name, "rabbitmq-data"
                                )
                            },
                        },
                        {"name": "runtime", "emptyDir": {}},
                        {"name": "logs", "emptyDir": {}},
                        {
                            "name": "config",
                            "configMap": {
                                "name": appliance_resource_name(
                                    appliance_name, "rabbitmq-config"
                                ),
                                "items": config_items,
                            },
                        },
                        {
                            "name": "secret",
                            "secret": {
                                "secretName": appliance_resource_name(
                                    appliance_name,
                                    "infrastructure-credentials",
                                ),
                                "items": [
                                    {
                                        "key": "rabbitmq_password",
                                        "path": "rabbitmq_password",
                                        "mode": 0o440,
                                    }
                                ],
                            },
                        },
                    ],
                },
            },
        },
    }


def classify_rabbitmq_data_pvc(
    *,
    existing: Any,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    settings: RabbitMQSettings,
) -> RetainedClassification:
    """Classify a retained RabbitMQ claim, including immutable desired fields."""
    resource_name = appliance_resource_name(appliance_name, "rabbitmq-data")
    classification = classify_retained_resource(
        existing=existing,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component="rabbitmq-data",
        accepted_version=accepted_version,
        retention=RABBITMQ_PVC_RETENTION_VALUE,
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
    if isinstance(spec, Mapping) and set(spec) - {
        "storageClassName",
        "accessModes",
        "volumeMode",
        "resources",
        "volumeName",
    }:
        return RetainedClassification.COLLISION
    if _field(spec, "storageClassName") != settings.storage.storage_class_name:
        return RetainedClassification.COLLISION
    access_modes = _field(spec, "accessModes")
    if not isinstance(access_modes, Sequence) or isinstance(access_modes, str):
        return RetainedClassification.COLLISION
    if list(access_modes) != [RABBITMQ_PVC_ACCESS_MODE]:
        return RetainedClassification.COLLISION
    if _field(spec, "volumeMode") != RABBITMQ_PVC_VOLUME_MODE:
        return RetainedClassification.COLLISION
    if any(
        _field(spec, field_name) is not None
        for field_name in (
            "selector",
            "dataSource",
            "dataSourceRef",
            "volumeAttributesClassName",
        )
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


def preflight_rabbitmq_resources(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    settings: RabbitMQSettings,
    owner: Mapping[str, Any],
    rabbitmq_data_pvc: Any | None,
    rabbitmq_config_map: Any | None,
    rabbitmq_stateful_set: Any | None,
) -> RabbitMQResourcePreflight:
    """Classify RabbitMQ resources before rendering or building manifests."""
    resources = (
        ("rabbitmq-data", rabbitmq_data_pvc),
        ("rabbitmq-config", rabbitmq_config_map),
        ("rabbitmq", rabbitmq_stateful_set),
    )
    classifications: dict[str, RetainedClassification | OwnedClassification] = {}
    for component, existing in resources:
        resource_name = appliance_resource_name(appliance_name, component)
        if component == "rabbitmq-data":
            classification: RetainedClassification | OwnedClassification = (
                classify_rabbitmq_data_pvc(
                    existing=existing,
                    appliance_name=appliance_name,
                    namespace=namespace,
                    accepted_version=accepted_version,
                    settings=settings,
                )
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
            return RabbitMQResourcePreflight(classifications, ())
    config_values = render_rabbitmq_config()
    return RabbitMQResourcePreflight(
        classifications,
        (
            build_rabbitmq_data_pvc(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                settings=settings,
            ),
            build_rabbitmq_config_map(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
                values=config_values,
            ),
            build_rabbitmq_stateful_set(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
                settings=settings,
            ),
        ),
    )


KEYSTONE_DATABASE_CREDENTIALS_KEYS = frozenset({"keystone_database_password"})


def generate_keystone_database_credentials(
    token_factory: Callable[[int], str] = secrets.token_urlsafe,
) -> dict[str, str]:
    """Generate the dedicated retained Keystone database password."""
    return _generate_credentials(KEYSTONE_DATABASE_CREDENTIALS_KEYS, token_factory)


def build_keystone_database_credentials_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    retention: str,
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the retained Keystone database credentials Secret."""
    return _build_retained_secret(
        appliance_name=appliance_name,
        namespace=namespace,
        accepted_version=accepted_version,
        component="keystone-database-credentials",
        retention=retention,
        values=values,
        expected_keys=KEYSTONE_DATABASE_CREDENTIALS_KEYS,
    )


def build_keystone_fernet_keys_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    retention: str,
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the retained Keystone Fernet key repository Secret."""
    return _build_retained_secret(
        appliance_name=appliance_name,
        namespace=namespace,
        accepted_version=accepted_version,
        component="keystone-fernet-keys",
        retention=retention,
        values=values,
        expected_keys=KEYSTONE_KEY_KEYS,
    )


def build_keystone_credential_keys_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    retention: str,
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the retained Keystone credential key repository Secret."""
    return _build_retained_secret(
        appliance_name=appliance_name,
        namespace=namespace,
        accepted_version=accepted_version,
        component="keystone-credential-keys",
        retention=retention,
        values=values,
        expected_keys=KEYSTONE_KEY_KEYS,
    )


def _build_keystone_owned(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    component: str,
    kind: str,
    values: Mapping[str, str],
    expected_keys: frozenset[str],
) -> dict[str, Any]:
    resource_name = appliance_resource_name(appliance_name, component)
    body: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": kind,
        "metadata": build_resource_metadata(
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        ),
    }
    checked = _validated_opaque_values(values, expected_keys, resource_name)
    if kind == "Secret":
        body.update({"type": "Opaque", "data": _encoded_secret_data(checked)})
    else:
        body["data"] = checked
    return body


def build_keystone_config_map(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced non-sensitive Keystone ConfigMap."""
    return _build_keystone_owned(
        appliance_name=appliance_name,
        namespace=namespace,
        accepted_version=accepted_version,
        owner=owner,
        component="keystone-config",
        kind="ConfigMap",
        values=values,
        expected_keys=KEYSTONE_CONFIG_KEYS,
    )


def build_keystone_config_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced sensitive Keystone configuration Secret."""
    return _build_keystone_owned(
        appliance_name=appliance_name,
        namespace=namespace,
        accepted_version=accepted_version,
        owner=owner,
        component="keystone-config-secret",
        kind="Secret",
        values=values,
        expected_keys=KEYSTONE_SECRET_CONFIG_KEYS,
    )


def _keystone_security_context() -> dict[str, Any]:
    return {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
    }


def build_keystone_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build Keystone's restricted single-replica direct-WSGI Deployment."""
    component = "keystone"
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
    config_name = appliance_resource_name(appliance_name, "keystone-config")
    secret_name = appliance_resource_name(appliance_name, "keystone-config-secret")
    fernet_name = appliance_resource_name(appliance_name, "keystone-fernet-keys")
    credential_name = appliance_resource_name(
        appliance_name, "keystone-credential-keys"
    )
    source_mounts = [
        {"name": "config-source", "mountPath": "/source/config", "readOnly": True},
        {"name": "secret-source", "mountPath": "/source/secret", "readOnly": True},
        {"name": "database-source", "mountPath": "/source/database", "readOnly": True},
        {"name": "fernet-source", "mountPath": "/source/fernet", "readOnly": True},
        {
            "name": "credential-source",
            "mountPath": "/source/credential",
            "readOnly": True,
        },
    ]
    runtime_mounts = [
        {"name": "config", "mountPath": "/etc/keystone/runtime"},
        {"name": "fernet", "mountPath": KEYSTONE_FERNET_KEYS_DIR},
        {"name": "credential", "mountPath": KEYSTONE_CREDENTIAL_KEYS_DIR},
        {"name": "tmp", "mountPath": "/tmp"},
        {"name": "run", "mountPath": "/run"},
        {"name": "data", "mountPath": "/var/lib/keystone"},
        {"name": "logs", "mountPath": "/var/log/kolla"},
    ]
    prepare = {
        "name": "prepare",
        "image": KEYSTONE_IMAGE,
        "command": ["/bin/sh", "-ec"],
        "args": [
            # emptyDir mount roots are root-owned; fsGroup supplies write access.
            "set -eu; "
            "install -m 0600 /source/config/bootstrap.py "
            "/etc/keystone/runtime/bootstrap.py; "
            "install -m 0600 /source/secret/keystone.conf "
            "/etc/keystone/runtime/keystone.conf; "
            "install -m 0600 /source/secret/auth-request.json "
            "/etc/keystone/runtime/auth-request.json; "
            "install -m 0600 /source/database/keystone_admin_password "
            "/etc/keystone/runtime/admin-password; "
            f"install -m 0600 /source/fernet/0 {KEYSTONE_FERNET_KEYS_DIR}/0; "
            f"install -m 0600 /source/fernet/1 {KEYSTONE_FERNET_KEYS_DIR}/1; "
            f"install -m 0600 /source/credential/0 {KEYSTONE_CREDENTIAL_KEYS_DIR}/0; "
            f"install -m 0600 /source/credential/1 {KEYSTONE_CREDENTIAL_KEYS_DIR}/1"
        ],
        "securityContext": _keystone_security_context(),
        "volumeMounts": source_mounts + runtime_mounts,
    }
    sync = {
        "name": "db-sync",
        "image": KEYSTONE_IMAGE,
        "command": ["/bin/sh", "-ec"],
        "args": [
            f"/var/lib/kolla/venv/bin/keystone-manage --config-file "
            f"{KEYSTONE_CONFIG_PATH} db_sync && "
            f"/var/lib/kolla/venv/bin/keystone-manage --config-file "
            f"{KEYSTONE_CONFIG_PATH} db_sync --check"
        ],
        "securityContext": _keystone_security_context(),
        "volumeMounts": runtime_mounts,
    }
    bootstrap = {
        "name": "bootstrap",
        "image": KEYSTONE_IMAGE,
        "command": ["/var/lib/kolla/venv/bin/python", KEYSTONE_BOOTSTRAP_PATH],
        "securityContext": _keystone_security_context(),
        "volumeMounts": runtime_mounts,
    }
    auth = (
        "status=$(curl --silent --show-error --output /tmp/body "
        "--dump-header /tmp/headers "
        "--write-out '%{http_code}' --header 'Content-Type: application/json' "
        f"--data-binary @{KEYSTONE_AUTH_REQUEST_PATH} "
        f"http://127.0.0.1:{KEYSTONE_PORT}/v3/auth/tokens); "
        "[ \"$status\" = 201 ] && grep -qi '^X-Subject-Token: .\\+' /tmp/headers"
    )
    main = {
        "name": component,
        "image": KEYSTONE_IMAGE,
        "command": [
            "/var/lib/kolla/venv/bin/keystone-wsgi-public",
            "--host",
            "0.0.0.0",
            "--port",
            "5000",
            "--",
            "--config-file",
            KEYSTONE_CONFIG_PATH,
        ],
        "ports": [
            {"name": "keystone", "containerPort": KEYSTONE_PORT, "protocol": "TCP"}
        ],
        "securityContext": _keystone_security_context(),
        "volumeMounts": runtime_mounts,
        "startupProbe": {
            "exec": {"command": ["/bin/sh", "-ec", auth]},
            "periodSeconds": 10,
            "timeoutSeconds": 10,
            "failureThreshold": 30,
        },
        "readinessProbe": {
            "exec": {"command": ["/bin/sh", "-ec", auth]},
            "periodSeconds": 10,
            "timeoutSeconds": 10,
            "failureThreshold": 3,
        },
        "livenessProbe": {
            "httpGet": {"path": "/v3", "port": KEYSTONE_PORT, "scheme": "HTTP"},
            "periodSeconds": 10,
            "timeoutSeconds": 5,
            "failureThreshold": 6,
        },
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": KEYSTONE_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": dict(metadata["labels"])},
                "spec": {
                    "imagePullSecrets": [{"name": KEYSTONE_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "runAsUser": KEYSTONE_RUN_AS_ID,
                        "runAsGroup": KEYSTONE_RUN_AS_ID,
                        "fsGroup": KEYSTONE_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                        "supplementalGroups": [KEYSTONE_SUPPLEMENTAL_GROUP],
                    },
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": (
                        KEYSTONE_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "initContainers": [prepare, sync, bootstrap],
                    "containers": [main],
                    "volumes": [
                        {
                            "name": "config-source",
                            "configMap": {
                                "name": config_name,
                                "items": [
                                    {
                                        "key": "bootstrap.py",
                                        "path": "bootstrap.py",
                                        "mode": 0o444,
                                    }
                                ],
                            },
                        },
                        {
                            "name": "secret-source",
                            "secret": {
                                "secretName": secret_name,
                                "items": [
                                    {"key": key, "path": key, "mode": 0o440}
                                    for key in sorted(KEYSTONE_SECRET_CONFIG_KEYS)
                                ],
                            },
                        },
                        {
                            "name": "database-source",
                            "secret": {
                                "secretName": appliance_resource_name(
                                    appliance_name, "infrastructure-credentials"
                                ),
                                "items": [
                                    {
                                        "key": "keystone_admin_password",
                                        "path": "keystone_admin_password",
                                        "mode": 0o440,
                                    }
                                ],
                            },
                        },
                        {
                            "name": "fernet-source",
                            "secret": {
                                "secretName": fernet_name,
                                "items": [
                                    {"key": key, "path": key, "mode": 0o440}
                                    for key in sorted(KEYSTONE_KEY_KEYS)
                                ],
                            },
                        },
                        {
                            "name": "credential-source",
                            "secret": {
                                "secretName": credential_name,
                                "items": [
                                    {"key": key, "path": key, "mode": 0o440}
                                    for key in sorted(KEYSTONE_KEY_KEYS)
                                ],
                            },
                        },
                        {"name": "config", "emptyDir": {}},
                        {"name": "fernet", "emptyDir": {}},
                        {"name": "credential", "emptyDir": {}},
                        {"name": "tmp", "emptyDir": {}},
                        {"name": "run", "emptyDir": {}},
                        {"name": "data", "emptyDir": {}},
                        {"name": "logs", "emptyDir": {}},
                    ],
                },
            },
        },
    }


def preflight_keystone_resources(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    retention: str,
    database_host: object,
    keystone_host: object,
    keystone_admin_password: str,
    keystone_database_credentials_secret: Any | None,
    keystone_fernet_keys_secret: Any | None,
    keystone_credential_keys_secret: Any | None,
    keystone_config_map: Any | None,
    keystone_config_secret: Any | None,
    keystone_deployment: Any | None,
    database_token_factory: Callable[[int], str] = secrets.token_urlsafe,
    fernet_byte_factory: Callable[[int], bytes] = secrets.token_bytes,
    credential_byte_factory: Callable[[int], bytes] = secrets.token_bytes,
) -> KeystoneResourcePreflight:
    """Classify all Keystone resources before validating, generating, or rendering."""
    retained = (
        (
            "keystone-database-credentials",
            keystone_database_credentials_secret,
            KEYSTONE_DATABASE_CREDENTIALS_KEYS,
        ),
        (
            "keystone-fernet-keys",
            keystone_fernet_keys_secret,
            KEYSTONE_KEY_KEYS,
        ),
        (
            "keystone-credential-keys",
            keystone_credential_keys_secret,
            KEYSTONE_KEY_KEYS,
        ),
    )
    owned = (
        ("keystone-config", keystone_config_map),
        ("keystone-config-secret", keystone_config_secret),
        ("keystone", keystone_deployment),
    )
    names = {
        component: appliance_resource_name(appliance_name, component)
        for component, *_ in (*retained, *owned)
    }
    classifications: dict[str, RetainedClassification | OwnedClassification] = {}
    for component, existing, _ in retained:
        name = names[component]
        classifications[name] = classify_retained_resource(
            existing=existing,
            resource_name=name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            retention=retention,
        )
    for component, existing in owned:
        name = names[component]
        classifications[name] = classify_owned_resource(
            existing=existing,
            resource_name=name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        )
    if any(value.value == "collision" for value in classifications.values()):
        return KeystoneResourcePreflight(classifications, {}, ())
    credentials: dict[str, Mapping[str, str]] = {}
    for component, existing, expected in retained:
        name = names[component]
        if classifications[name] is RetainedClassification.REUSE:
            try:
                credentials[name] = validated_retained_secret_values(
                    existing=existing, expected_keys=expected
                )
            except ValueError:
                classifications[name] = RetainedClassification.COLLISION
    if any(value.value == "collision" for value in classifications.values()):
        return KeystoneResourcePreflight(classifications, {}, ())
    for component, _, _ in retained:
        name = names[component]
        if classifications[name] is RetainedClassification.ABSENT:
            if component == "keystone-database-credentials":
                credentials[name] = generate_keystone_database_credentials(
                    database_token_factory
                )
            elif component == "keystone-fernet-keys":
                credentials[name] = generate_keystone_keys(fernet_byte_factory)
            else:
                credentials[name] = generate_keystone_keys(credential_byte_factory)
    config = render_keystone_config(keystone_host=keystone_host)
    secret = render_sensitive_keystone_config(
        database_host=database_host,
        keystone_host=keystone_host,
        credentials=SensitiveKeystoneCredentials(
            database_password=credentials[names["keystone-database-credentials"]][
                "keystone_database_password"
            ],
            admin_password=keystone_admin_password,
        ),
    )
    return KeystoneResourcePreflight(
        classifications,
        credentials,
        (
            build_keystone_database_credentials_secret(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                retention=retention,
                values=credentials[names["keystone-database-credentials"]],
            ),
            build_keystone_fernet_keys_secret(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                retention=retention,
                values=credentials[names["keystone-fernet-keys"]],
            ),
            build_keystone_credential_keys_secret(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                retention=retention,
                values=credentials[names["keystone-credential-keys"]],
            ),
            build_keystone_config_map(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
                values=config,
            ),
            build_keystone_config_secret(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
                values=secret,
            ),
            build_keystone_deployment(
                appliance_name=appliance_name,
                namespace=namespace,
                accepted_version=accepted_version,
                owner=owner,
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


BOOTSTRAP_RUNNING_MESSAGE = "The Coriolis-common bootstrap is running."
BOOTSTRAP_FAILED_MESSAGE = (
    "The Coriolis-common bootstrap failed and must be removed before continuing."
)


def bootstrap_running_conditions() -> list[Condition]:
    """Conditions when the Coriolis-common bootstrap is active or not yet done."""
    return [
        (
            "Accepted",
            "True",
            "Accepted",
            "The requested profile and version are supported.",
        ),
        ("Progressing", "True", "BootstrapRunning", BOOTSTRAP_RUNNING_MESSAGE),
        ("Reconciled", "False", "BootstrapRunning", BOOTSTRAP_RUNNING_MESSAGE),
        ("Ready", "False", "RuntimeNotImplemented", RUNTIME_NOT_IMPLEMENTED_MESSAGE),
        ("Degraded", "False", "NotDegraded", NOT_DEGRADED_MESSAGE),
        (
            "Upgradeable",
            "False",
            "UpgradeNotSupported",
            UPGRADE_NOT_SUPPORTED_MESSAGE,
        ),
    ]


def bootstrap_failed_conditions() -> list[Condition]:
    """Stable sanitized conditions when the Coriolis-common bootstrap is terminal."""
    return [
        (
            "Accepted",
            "True",
            "Accepted",
            "The requested profile and version are supported.",
        ),
        ("Progressing", "False", "BootstrapFailed", BOOTSTRAP_FAILED_MESSAGE),
        ("Reconciled", "False", "BootstrapFailed", BOOTSTRAP_FAILED_MESSAGE),
        ("Ready", "False", "RuntimeNotImplemented", RUNTIME_NOT_IMPLEMENTED_MESSAGE),
        ("Degraded", "True", "BootstrapFailed", BOOTSTRAP_FAILED_MESSAGE),
        (
            "Upgradeable",
            "False",
            "UpgradeNotSupported",
            UPGRADE_NOT_SUPPORTED_MESSAGE,
        ),
    ]


def _bootstrap_job_template_id(job_spec: Any) -> str:
    """Return a deterministic digest of the desired immutable Job spec."""
    serialized = json.dumps(job_spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:NAME_HASH_LENGTH]


def build_common_bootstrap_config_map(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    script: str,
) -> dict[str, Any]:
    """Build the immutable, create-only versioned bootstrap ConfigMap."""
    resource_name = appliance_resource_name(appliance_name, BOOTSTRAP_COMPONENT)
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": build_resource_metadata(
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=BOOTSTRAP_COMPONENT,
            accepted_version=accepted_version,
            owner=owner,
        ),
        "immutable": True,
        "data": {BOOTSTRAP_SCRIPT_FILENAME: script},
    }


def build_common_bootstrap_job(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    script: str,
) -> dict[str, Any]:
    """Build the immutable, create-only Coriolis-common bootstrap Job.

    The Job binds the exact rendered bootstrap script via a non-sensitive
    script-digest annotation in both its object metadata and its immutable
    pod-template metadata, and the template-id digest covers that script
    identity. Any change to the script or template under a given revision is
    therefore a collision rather than a patch.
    """
    component = BOOTSTRAP_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    metadata = build_resource_metadata(
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    config_secret_name = appliance_resource_name(
        appliance_name, "coriolis-config-secret"
    )
    infra_credentials_name = appliance_resource_name(
        appliance_name, "infrastructure-credentials"
    )
    coriolis_credentials_name = appliance_resource_name(
        appliance_name, "coriolis-credentials"
    )
    pod_spec = {
        "imagePullSecrets": [{"name": BOOTSTRAP_IMAGE_PULL_SECRET_NAME}],
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "terminationGracePeriodSeconds": BOOTSTRAP_TERMINATION_GRACE_PERIOD_SECONDS,
        "securityContext": {
            "runAsUser": BOOTSTRAP_UID_GID,
            "runAsGroup": BOOTSTRAP_UID_GID,
            "fsGroup": BOOTSTRAP_UID_GID,
            "fsGroupChangePolicy": "OnRootMismatch",
        },
        "containers": [
            {
                "name": component,
                "image": CONDUCTOR_IMAGE,
                "command": ["python3", BOOTSTRAP_SCRIPT_PATH],
                "env": [
                    {"name": "HOME", "value": "/tmp"},
                    {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                ],
                "securityContext": {
                    "runAsNonRoot": True,
                    "readOnlyRootFilesystem": True,
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "volumeMounts": [
                    {
                        "name": "script",
                        "mountPath": BOOTSTRAP_SCRIPT_DIR,
                        "readOnly": True,
                    },
                    {
                        "name": "config",
                        "mountPath": BOOTSTRAP_CONFIG_DIR,
                        "readOnly": True,
                    },
                    {
                        "name": "infra-credentials",
                        "mountPath": BOOTSTRAP_INFRA_CREDENTIALS_DIR,
                        "readOnly": True,
                    },
                    {
                        "name": "coriolis-credentials",
                        "mountPath": BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR,
                        "readOnly": True,
                    },
                    {"name": "tmp", "mountPath": "/tmp"},
                ],
            }
        ],
        "volumes": [
            {"name": "tmp", "emptyDir": {}},
            {
                "name": "script",
                "configMap": {
                    "name": resource_name,
                    "items": [
                        {
                            "key": BOOTSTRAP_SCRIPT_FILENAME,
                            "path": BOOTSTRAP_SCRIPT_FILENAME,
                            "mode": 0o444,
                        }
                    ],
                },
            },
            {
                "name": "config",
                "secret": {
                    "secretName": config_secret_name,
                    "items": [
                        {"key": "coriolis.conf", "path": "coriolis.conf", "mode": 0o440}
                    ],
                },
            },
            {
                "name": "infra-credentials",
                "secret": {
                    "secretName": infra_credentials_name,
                    "items": [
                        {
                            "key": "keystone_admin_password",
                            "path": "keystone-admin-password",
                            "mode": 0o440,
                        },
                        {
                            "key": "rabbitmq_password",
                            "path": "rabbitmq-password",
                            "mode": 0o440,
                        },
                    ],
                },
            },
            {
                "name": "coriolis-credentials",
                "secret": {
                    "secretName": coriolis_credentials_name,
                    "items": [
                        {
                            "key": "coriolis_keystone_password",
                            "path": "coriolis-keystone-password",
                            "mode": 0o440,
                        },
                        {
                            "key": "coriolis_database_password",
                            "path": "coriolis-database-password",
                            "mode": 0o440,
                        },
                    ],
                },
            },
        ],
    }
    template_metadata = {"labels": dict(metadata["labels"])}
    script_id = _bootstrap_script_id(script)
    metadata["annotations"][BOOTSTRAP_SCRIPT_ANNOTATION] = script_id
    template_metadata["annotations"] = {BOOTSTRAP_SCRIPT_ANNOTATION: script_id}
    job_spec = {
        "backoffLimit": BOOTSTRAP_BACKOFF_LIMIT,
        "activeDeadlineSeconds": BOOTSTRAP_ACTIVE_DEADLINE_SECONDS,
        "completions": 1,
        "parallelism": 1,
        "template": {"metadata": template_metadata, "spec": pod_spec},
    }
    template_id = _bootstrap_job_template_id(job_spec)
    metadata["annotations"][BOOTSTRAP_TEMPLATE_ANNOTATION] = template_id
    template_metadata["annotations"][BOOTSTRAP_TEMPLATE_ANNOTATION] = template_id
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": metadata,
        "spec": job_spec,
    }


def _bootstrap_script_id(script: str) -> str:
    """Return a deterministic digest of the exact rendered bootstrap script."""
    return hashlib.sha256(script.encode("utf-8")).hexdigest()[:NAME_HASH_LENGTH]


_SPEC_PROJECTION_MISMATCH = object()


def _project_spec_value(existing: Any, desired: Any) -> Any:
    """Project ``existing`` onto the exact shape/keys of ``desired``.

    Mappings are projected key-by-key via ``_field`` (handling both dict and
    Kubernetes model objects), lists element-wise by position, and scalars are
    copied directly. Server-added fields, defaults, and labels that are not
    present in ``desired`` are ignored; any structural or scalar mismatch
    returns ``_SPEC_PROJECTION_MISMATCH``.
    """
    if isinstance(desired, Mapping):
        if existing is None or isinstance(existing, (Sequence, str, bytes)):
            return _SPEC_PROJECTION_MISMATCH
        projected: dict[str, Any] = {}
        for key, desired_value in desired.items():
            existing_value = _field(existing, key)
            if existing_value is None and desired_value is not None:
                return _SPEC_PROJECTION_MISMATCH
            projected[key] = _project_spec_value(existing_value, desired_value)
        return projected
    if isinstance(desired, Sequence) and not isinstance(desired, (str, bytes)):
        if not isinstance(existing, Sequence) or isinstance(existing, (str, bytes)):
            return _SPEC_PROJECTION_MISMATCH
        if len(existing) != len(desired):
            return _SPEC_PROJECTION_MISMATCH
        return [
            _project_spec_value(existing_item, desired_item)
            for existing_item, desired_item in zip(existing, desired, strict=True)
        ]
    if isinstance(existing, Mapping) or (
        isinstance(existing, Sequence) and not isinstance(existing, (str, bytes))
    ):
        return _SPEC_PROJECTION_MISMATCH
    return existing


def _project_bootstrap_spec(existing_spec: Any, desired_spec: Any) -> Any | None:
    """Return the projected existing spec, or None on any mismatch."""
    projected = _project_spec_value(existing_spec, desired_spec)
    if projected is _SPEC_PROJECTION_MISMATCH:
        return None
    return projected


def _matching_annotations(existing_annotations: Any, desired_annotations: Any) -> bool:
    existing = _mapping_value(existing_annotations)
    desired = _mapping_value(desired_annotations)
    for key in (BOOTSTRAP_SCRIPT_ANNOTATION, BOOTSTRAP_TEMPLATE_ANNOTATION):
        if existing.get(key) != desired.get(key):
            return False
    return True


def classify_common_bootstrap_config_map(
    *,
    existing: Any,
    resource_name: str,
    namespace: str,
    appliance_name: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    desired_config_map: Mapping[str, Any],
) -> OwnedClassification:
    """Classify an existing immutable bootstrap ConfigMap, rejecting drift.

    A managed ConfigMap must be immutable and carry exactly the rendered
    bootstrap script. Any drift is an immutable collision and is never patched,
    deleted, or reused.
    """
    classification = classify_owned_resource(
        existing=existing,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=BOOTSTRAP_COMPONENT,
        accepted_version=accepted_version,
        owner=owner,
    )
    if classification is not OwnedClassification.MANAGED:
        return classification
    if _field(existing, "immutable") is not True:
        return OwnedClassification.COLLISION
    existing_data = _mapping_value(_field(existing, "data"))
    desired_data = _mapping_value(desired_config_map.get("data"))
    if existing_data != desired_data:
        return OwnedClassification.COLLISION
    return OwnedClassification.MANAGED


def classify_common_bootstrap_job(
    *,
    existing: Any,
    resource_name: str,
    namespace: str,
    appliance_name: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    desired_job: Mapping[str, Any],
) -> OwnedClassification:
    """Classify an existing Job against the exact desired spec and identity.

    A managed Job is reusable only when the projected existing spec equals the
    exact desired spec (including image, command, env, security contexts,
    mounts, volume sources/items/modes, pull Secret, restart/deadline/
    backoff/completions/parallelism, and template labels/annotations) and both
    the object metadata and pod-template annotations carry the exact script and
    template IDs. Any mismatch is an immutable collision and is never patched,
    deleted, or reused.
    """
    classification = classify_owned_resource(
        existing=existing,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=BOOTSTRAP_COMPONENT,
        accepted_version=accepted_version,
        owner=owner,
    )
    if classification is not OwnedClassification.MANAGED:
        return classification

    existing_spec = _field(existing, "spec")
    desired_spec = _field(desired_job, "spec")
    projected = _project_bootstrap_spec(existing_spec, desired_spec)
    if projected is None or projected != desired_spec:
        return OwnedClassification.COLLISION
    if not _matching_annotations(
        _field(_field(existing, "metadata"), "annotations"),
        _field(_field(desired_job, "metadata"), "annotations"),
    ):
        return OwnedClassification.COLLISION
    return OwnedClassification.MANAGED


@dataclass(frozen=True)
class BootstrapResourcePreflight:
    """Pure Coriolis-common bootstrap classification and apply-ordered bodies."""

    config_map_classification: OwnedClassification
    job_classification: OwnedClassification
    manifests: tuple[dict[str, Any], ...] = field(repr=False)


def preflight_common_bootstrap_resources(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    bootstrap_config_map: Any | None,
    bootstrap_job: Any | None,
) -> BootstrapResourcePreflight:
    """Classify the bootstrap ConfigMap and Job before rendering or building.

    The rendered script is produced first because both desired bodies and both
    classifiers depend on it. Any identity, immutable, data, or managed-spec
    drift is a collision and yields no writeable manifests.
    """
    resource_name = appliance_resource_name(appliance_name, BOOTSTRAP_COMPONENT)
    script = render_bootstrap_script(
        coriolis_api_host=appliance_resource_name(appliance_name, "coriolis-api"),
        rabbitmq_host=appliance_resource_name(appliance_name, "rabbitmq"),
        memcached_host=appliance_resource_name(appliance_name, "memcached"),
        database_host=appliance_resource_name(appliance_name, "mariadb"),
        keystone_host=appliance_resource_name(appliance_name, "keystone"),
    )
    job_body = build_common_bootstrap_job(
        appliance_name=appliance_name,
        namespace=namespace,
        accepted_version=accepted_version,
        owner=owner,
        script=script,
    )
    config_map_body = build_common_bootstrap_config_map(
        appliance_name=appliance_name,
        namespace=namespace,
        accepted_version=accepted_version,
        owner=owner,
        script=script,
    )
    config_map_classification = classify_common_bootstrap_config_map(
        existing=bootstrap_config_map,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        accepted_version=accepted_version,
        owner=owner,
        desired_config_map=config_map_body,
    )
    if config_map_classification is OwnedClassification.COLLISION:
        return BootstrapResourcePreflight(
            config_map_classification, OwnedClassification.COLLISION, ()
        )
    job_classification = classify_common_bootstrap_job(
        existing=bootstrap_job,
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        accepted_version=accepted_version,
        owner=owner,
        desired_job=job_body,
    )
    if job_classification is OwnedClassification.COLLISION:
        return BootstrapResourcePreflight(
            config_map_classification, job_classification, ()
        )
    return BootstrapResourcePreflight(
        config_map_classification,
        job_classification,
        (config_map_body, job_body),
    )
