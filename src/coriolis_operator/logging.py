"""Pure logging runtime settings validation and Loki+NGINX+Alloy manifest builders.

This module owns validated logging settings plus the pure builders for the
retained Loki credential Secret, the retained ownerless Loki data claim, the
owner-referenced Loki ConfigMap and gateway config Secret, the ClusterIP gateway
Service (exposing only NGINX), the one-replica Loki StatefulSet that runs the
Loki and NGINX gateway sidecar in the same Pod, and the operator-owned per-CR
Grafana Alloy collection contract. The Loki server binds only the loopback
address, so Loki is never directly exposed on the network; only the NGINX
gateway is reachable. Alloy runs one namespaced owner-referenced Deployment per
CR, reads app-container logs through the Kubernetes API, and pushes them only
to the per-CR gateway Service, tagging every stream with the full appliance
name independently of pod labels or annotations. The module also owns the
pure builders for the per-CR logs API adaptor Deployment, its ClusterIP
Service, and a dedicated owned Ingress routing the `/logs` and `/log-stream`
adaptor endpoints. Credential values, errors, and status must never disclose
secret material, so all sensitive mappings redact their representation.
"""

import re
import secrets
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import bcrypt
from kubernetes.utils.quantity import parse_quantity  # type: ignore[import-untyped]

from coriolis_operator.ingress import IngressSettings
from coriolis_operator.reconcile import (
    OwnedClassification,
    RetainedClassification,
    _deployment_ready,
    _encoded_secret_data,
    _pvc_ready,
    _stateful_set_ready,
    _validated_opaque_values,
    appliance_identity,
    appliance_resource_name,
    build_resource_metadata,
    classify_owned_resource,
    classify_retained_resource,
    validated_retained_secret_values,
)

_INVALID_SETTINGS_MESSAGE = "invalid logging settings"
_INVALID_CREDENTIALS_MESSAGE = "invalid retained logging credentials"
_GENERATION_FAILURE_MESSAGE = "logging credential generation failed"

LOGGING_RESOURCE_COMPONENTS = ("loki", "gateway", "alloy", "adaptor")

LOGGING_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
LOKI_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/loki"
    "@sha256:550d599ec4efacd8ebc0a5871766855057cba2bd0c669c0711d898c00d6d901f"
)
GATEWAY_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/nginx-unprivileged"
    "@sha256:9849698e95fe2b466e473ad8c452b1a812e08713af1514c61ece0aa77cc8e013"
)

LOKI_REPLICAS = 1
LOKI_RUN_AS_ID = 10001
NGINX_RUN_AS_ID = 101
LOKI_PORT = 3100
NGINX_PORT = 8080
LOKI_TERMINATION_GRACE_PERIOD_SECONDS = 30

LOKI_ENTRYPOINT = "/usr/bin/loki"
LOKI_CONFIG_DIR = "/etc/loki"
LOKI_CONFIG_PATH = f"{LOKI_CONFIG_DIR}/loki.yaml"
LOKI_DATA_DIR = "/loki"
LOKI_TMP_DIR = "/tmp"
LOKI_CONFIG_KEYS = frozenset({"loki.yaml"})

LOKI_PVC_ACCESS_MODE = "ReadWriteOnce"
LOKI_PVC_VOLUME_MODE = "Filesystem"
LOKI_PVC_RETENTION_VALUE = "loki-data"

GATEWAY_BINARY = "/usr/sbin/nginx"
GATEWAY_CONFIG_DIR = "/etc/nginx-gateway"
GATEWAY_NGINX_CONF_PATH = f"{GATEWAY_CONFIG_DIR}/nginx.conf"
GATEWAY_READ_HTPASSWD_PATH = f"{GATEWAY_CONFIG_DIR}/read.htpasswd"
GATEWAY_WRITE_HTPASSWD_PATH = f"{GATEWAY_CONFIG_DIR}/write.htpasswd"
GATEWAY_TMP_DIR = "/tmp"
GATEWAY_CONFIG_KEYS = frozenset({"nginx.conf", "read.htpasswd", "write.htpasswd"})
GATEWAY_READY_PATH = "/ready"
GATEWAY_LABEL_KEY = "coriolis.cloudbase.it/gateway"

LOGGING_CREDENTIALS_KEYS = frozenset(
    {
        "read_password",
        "write_password",
        "read_password_hash",
        "write_password_hash",
    }
)
LOGGING_CREDENTIALS_RETENTION_VALUE = "logging-credentials"

ALLOY_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/alloy"
    "@sha256:1eeba15ef3193438c72f66efd3d76f769c523a4c661db0fae6eddde906004bc8"
)
ALLOY_BINARY = "/bin/alloy"
ALLOY_RUN_AS_ID = 10001
ALLOY_HTTP_PORT = 12345
ALLOY_CONFIG_DIR = "/etc/alloy"
ALLOY_CONFIG_PATH = f"{ALLOY_CONFIG_DIR}/config.alloy"
ALLOY_DATA_DIR = "/var/lib/alloy/data"
ALLOY_TMP_DIR = "/tmp"
ALLOY_CREDENTIAL_MOUNT_DIR = "/etc/alloy/credentials"
ALLOY_CREDENTIAL_PATH = f"{ALLOY_CREDENTIAL_MOUNT_DIR}/write_password"
ALLOY_CONFIG_KEYS = frozenset({"config.alloy"})
ALLOY_READY_PATH = "/-/ready"
ALLOY_REPLICAS = 1
ALLOY_CONFIG_COMPONENT = "alloy-config"
ALLOY_SERVICE_ACCOUNT_COMPONENT = "alloy-sa"
ALLOY_ROLE_COMPONENT = "alloy-role"
ALLOY_ROLE_BINDING_COMPONENT = "alloy-rb"
ALLOY_DEPLOYMENT_COMPONENT = "alloy"

ALLOY_APP_COLLECTION_COMPONENTS = (
    "mariadb",
    "rabbitmq",
    "memcached",
    "keystone",
    "common-bootstrap",
    "coriolis-conductor",
    "coriolis-scheduler",
    "coriolis-transfer-cron",
    "coriolis-minion-manager",
    "coriolis-deployer-manager",
    "coriolis-worker",
    "coriolis-api",
    "coriolis-web",
)

ADAPTOR_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/logs-adaptor"
    "@sha256:100701724e228c616803d5764b20e0b31e665e305390455c5c2a62b0bb514237"
)
ADAPTOR_BINARY = "/app/.venv/bin/coriolis-logs-api-adaptor"
ADAPTOR_WORKDIR = "/app"
ADAPTOR_RUN_AS_ID = 10001
ADAPTOR_PORT = 8080
ADAPTOR_TMP_DIR = "/tmp"
ADAPTOR_CREDENTIAL_MOUNT_DIR = "/etc/coriolis-logs"
ADAPTOR_CREDENTIAL_PATH = f"{ADAPTOR_CREDENTIAL_MOUNT_DIR}/read_password"
ADAPTOR_READY_PATH = "/readyz"
ADAPTOR_HEALTH_PATH = "/healthz"
ADAPTOR_REPLICAS = 1
ADAPTOR_DEPLOYMENT_COMPONENT = "adaptor"
ADAPTOR_SERVICE_COMPONENT = "adaptor"
ADAPTOR_INGRESS_COMPONENT = "adaptor"
ADAPTOR_ENV_PREFIX = "CORIOLIS_LOGS_"
ADAPTOR_ADMIN_ROLES = "admin"
_ADAPTOR_INGRESS_ANNOTATIONS: Mapping[str, str] = MappingProxyType(
    {
        "nginx.ingress.kubernetes.io/enable-access-log": "false",
        "nginx.ingress.kubernetes.io/proxy-buffering": "off",
        "nginx.ingress.kubernetes.io/proxy-read-timeout": "3600",
        "nginx.ingress.kubernetes.io/proxy-send-timeout": "3600",
        "nginx.ingress.kubernetes.io/proxy-http-version": "1.1",
    }
)

_META_NAMESPACE = "__meta_kubernetes_namespace"
_META_POD_NAME = "__meta_kubernetes_pod_name"
_META_POD_CONTAINER_NAME = "__meta_kubernetes_pod_container_name"
_META_POD_COMPONENT_LABEL = (
    "__meta_kubernetes_pod_label_coriolis_cloudbase_it_component"
)
_META_POD_STREAM = "__stream__"

_SEVERITY_KEYWORDS = (
    "DEBUG",
    "TRACE",
    "NOTICE",
    "INFO",
    "WARN",
    "WARNING",
    "ERR",
    "ERROR",
    "CRIT",
    "CRITICAL",
    "FATAL",
)
_SEVERITY_PREFIX_RE = f"^(?P<level>{'|'.join(_SEVERITY_KEYWORDS)})\\b"

_LOKI_UPSTREAM_ADDRESS = "127.0.0.1"
_TENANT_SAFE_RE = re.compile(r"[a-z0-9](?:[-a-z0-9_.]*[a-z0-9])?")


@dataclass(frozen=True)
class LoggingStorageSettings:
    """Validated immutable Loki persistent-volume settings."""

    storage_class_name: str
    size: str


@dataclass(frozen=True)
class LoggingResourceSettings:
    """Validated immutable logging component resource quantity strings."""

    requests_cpu: str
    requests_memory: str
    limits_cpu: str
    limits_memory: str


@dataclass(frozen=True)
class LoggingSettings:
    """Complete, validated logging runtime settings for later manifest builders."""

    retention_hours: int
    storage: LoggingStorageSettings
    resources: Mapping[str, LoggingResourceSettings]

    def __post_init__(self) -> None:
        object.__setattr__(self, "resources", MappingProxyType(dict(self.resources)))


@dataclass(frozen=True)
class SensitiveLoggingCredentials:
    """Retained Loki credential values hidden from representations."""

    read_password: str = field(repr=False)
    write_password: str = field(repr=False)
    read_password_hash: str = field(repr=False)
    write_password_hash: str = field(repr=False)

    def __repr__(self) -> str:
        return "SensitiveLoggingCredentials(<redacted>)"

    __str__ = __repr__


class SensitiveLoggingGatewayConfig(Mapping[str, str]):
    """Gateway config Secret mapping whose representation is always redacted."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return "SensitiveLoggingGatewayConfig(<redacted>)"

    __str__ = __repr__


def _invalid_settings() -> ValueError:
    return ValueError(_INVALID_SETTINGS_MESSAGE)


def _invalid_credentials() -> ValueError:
    return ValueError(_INVALID_CREDENTIALS_MESSAGE)


def _required_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _invalid_settings()
    return value


def _required_string(value: object) -> str:
    if type(value) is not str or not value:
        raise _invalid_settings()
    return value


def _credential(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise _invalid_credentials()
    return value


def _validated_tenant(value: object) -> str:
    tenant = _credential(value)
    if ":" in tenant or not _TENANT_SAFE_RE.fullmatch(tenant):
        raise _invalid_credentials()
    return tenant


def _validated_retention_hours(value: object) -> int:
    if type(value) is not int or value < 1:
        raise _invalid_settings()
    return value


def _validated_storage_class_name(value: object) -> str:
    storage_class_name = _required_string(value)
    if storage_class_name.strip() != storage_class_name or any(
        unicodedata.category(character) == "Cc" for character in storage_class_name
    ):
        raise _invalid_settings()
    return storage_class_name


def _validated_quantity(value: object) -> tuple[str, Decimal]:
    quantity = _required_string(value)
    if quantity.strip() != quantity:
        raise _invalid_settings()
    try:
        parsed = parse_quantity(quantity)
    except (ArithmeticError, TypeError, ValueError):
        raise _invalid_settings() from None
    if not isinstance(parsed, Decimal) or not parsed.is_finite() or parsed <= 0:
        raise _invalid_settings()
    return quantity, parsed


def _validated_component_resources(
    component_values: object,
) -> LoggingResourceSettings:
    component_resources = _required_mapping(component_values)
    requests = _required_mapping(component_resources.get("requests"))
    limits = _required_mapping(component_resources.get("limits"))
    requests_cpu, parsed_requests_cpu = _validated_quantity(requests.get("cpu"))
    requests_memory, parsed_requests_memory = _validated_quantity(
        requests.get("memory")
    )
    limits_cpu, parsed_limits_cpu = _validated_quantity(limits.get("cpu"))
    limits_memory, parsed_limits_memory = _validated_quantity(limits.get("memory"))
    if (
        parsed_requests_cpu > parsed_limits_cpu
        or parsed_requests_memory > parsed_limits_memory
    ):
        raise _invalid_settings()
    return LoggingResourceSettings(
        requests_cpu, requests_memory, limits_cpu, limits_memory
    )


def resolve_logging_settings(spec_logging: object) -> LoggingSettings:
    """Validate complete logging CR input without mutating caller mappings."""
    logging_values = _required_mapping(spec_logging)
    retention_hours = _validated_retention_hours(logging_values.get("retentionHours"))
    storage_values = _required_mapping(logging_values.get("storage"))
    loki_storage = _required_mapping(storage_values.get("loki"))
    storage_class_name = _validated_storage_class_name(
        loki_storage.get("storageClassName")
    )
    size, _ = _validated_quantity(loki_storage.get("size"))
    resources_values = _required_mapping(logging_values.get("resources"))
    components = {
        component: _validated_component_resources(resources_values.get(component))
        for component in LOGGING_RESOURCE_COMPONENTS
    }

    return LoggingSettings(
        retention_hours=retention_hours,
        storage=LoggingStorageSettings(storage_class_name, size),
        resources=components,
    )


def _bcrypt_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def generate_logging_credentials(
    *,
    password_factory: Callable[[int], str] = secrets.token_urlsafe,
    hash_factory: Callable[[str], str] = _bcrypt_hash,
) -> dict[str, str]:
    """Generate independent random read/write passwords and their hashes."""
    read_password = password_factory(32)
    write_password = password_factory(32)
    values = {
        "read_password": read_password,
        "write_password": write_password,
        "read_password_hash": hash_factory(read_password),
        "write_password_hash": hash_factory(write_password),
    }
    if any(
        not isinstance(value, str) or not value or "\n" in value
        for value in values.values()
    ):
        raise ValueError(_GENERATION_FAILURE_MESSAGE)
    return values


def decode_logging_credentials(values: object) -> SensitiveLoggingCredentials:
    """Validate exact retained credential keys and return redacted values."""
    if not isinstance(values, Mapping):
        raise _invalid_credentials()
    if set(values) != LOGGING_CREDENTIALS_KEYS:
        raise _invalid_credentials()
    return SensitiveLoggingCredentials(
        read_password=_credential(values.get("read_password")),
        write_password=_credential(values.get("write_password")),
        read_password_hash=_credential(values.get("read_password_hash")),
        write_password_hash=_credential(values.get("write_password_hash")),
    )


def logging_tenant(cr_uid: str) -> str:
    """Return the current Loki tenant derived from an appliance CR UID."""
    return f"coriolis-{_validated_tenant(cr_uid)}"


def _render_loki_yaml(retention_hours: int) -> str:
    return f"""auth_enabled: true

server:
  http_listen_address: {_LOKI_UPSTREAM_ADDRESS}
  http_listen_port: {LOKI_PORT}
  grpc_listen_address: {_LOKI_UPSTREAM_ADDRESS}
  grpc_listen_port: 9095
  log_level: warn

common:
  path_prefix: {LOKI_DATA_DIR}
  replication_factor: 1
  ring:
    instance_addr: {_LOKI_UPSTREAM_ADDRESS}
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: {LOKI_DATA_DIR}/chunks
      rules_directory: {LOKI_DATA_DIR}/rules

limits_config:
  retention_period: {retention_hours}h

schema_config:
  configs:
    - from: 2024-01-01
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h

compactor:
  working_directory: {LOKI_DATA_DIR}/compactor
  compaction_interval: 15m
  retention_enabled: true
  retention_delete_delay: 2h
  delete_request_store: filesystem
  compactor_ring:
    kvstore:
      store: inmemory

analytics:
  reporting_enabled: false
"""


def render_loki_config(*, settings: LoggingSettings) -> dict[str, str]:
    """Return the credential-free Loki ConfigMap values."""
    return {"loki.yaml": _render_loki_yaml(settings.retention_hours)}


def _render_nginx_conf(tenant: str) -> str:
    return f"""worker_processes 1;
pid {GATEWAY_TMP_DIR}/nginx.pid;
error_log stderr warn;

events {{
    worker_connections 1024;
}}

http {{
    access_log off;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    server_tokens off;

    client_body_temp_path {GATEWAY_TMP_DIR}/client_body;
    proxy_temp_path {GATEWAY_TMP_DIR}/proxy;
    fastcgi_temp_path {GATEWAY_TMP_DIR}/fastcgi;
    uwsgi_temp_path {GATEWAY_TMP_DIR}/uwsgi;
    scgi_temp_path {GATEWAY_TMP_DIR}/scgi;

    map $http_upgrade $connection_upgrade {{
        default upgrade;
        ''      close;
    }}

    server {{
        listen {NGINX_PORT};
        server_name _;

        proxy_set_header X-Scope-OrgID {tenant};
        proxy_set_header Authorization "";
        proxy_set_header Host $host;

        location = {GATEWAY_READY_PATH} {{
            proxy_pass http://{_LOKI_UPSTREAM_ADDRESS}:{LOKI_PORT}{GATEWAY_READY_PATH};
        }}

        location = /loki/api/v1/push {{
            auth_basic "write";
            auth_basic_user_file {GATEWAY_WRITE_HTPASSWD_PATH};
            proxy_pass http://{_LOKI_UPSTREAM_ADDRESS}:{LOKI_PORT};
        }}

        location = /loki/api/v1/series {{
            auth_basic "read";
            auth_basic_user_file {GATEWAY_READ_HTPASSWD_PATH};
            proxy_pass http://{_LOKI_UPSTREAM_ADDRESS}:{LOKI_PORT};
        }}

        location = /loki/api/v1/query_range {{
            auth_basic "read";
            auth_basic_user_file {GATEWAY_READ_HTPASSWD_PATH};
            proxy_pass http://{_LOKI_UPSTREAM_ADDRESS}:{LOKI_PORT};
        }}

        location = /loki/api/v1/tail {{
            auth_basic "read";
            auth_basic_user_file {GATEWAY_READ_HTPASSWD_PATH};
            proxy_pass http://{_LOKI_UPSTREAM_ADDRESS}:{LOKI_PORT};
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_buffering off;
            proxy_read_timeout 3600s;
        }}

        location / {{
            return 404;
        }}
    }}
}}
"""


def render_gateway_config(
    *, credentials: object, tenant: object
) -> SensitiveLoggingGatewayConfig:
    """Render the hashed gateway auth files and NGINX config for the tenant."""
    if type(credentials) is not SensitiveLoggingCredentials:
        raise _invalid_credentials()
    try:
        tenant_value = _validated_tenant(tenant)
        read_hash = _credential(credentials.read_password_hash)
        write_hash = _credential(credentials.write_password_hash)
        return SensitiveLoggingGatewayConfig(
            {
                "nginx.conf": _render_nginx_conf(tenant_value),
                "read.htpasswd": f"{tenant_value}:{read_hash}\n",
                "write.htpasswd": f"{tenant_value}:{write_hash}\n",
            }
        )
    except ValueError:
        raise _invalid_credentials() from None


def build_logging_credentials_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    retention: str,
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the ownerless retained logging credential Secret apply body."""
    component = "logging-credentials"
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
            _validated_opaque_values(values, LOGGING_CREDENTIALS_KEYS, resource_name)
        ),
    }


def build_loki_data_pvc(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    settings: LoggingSettings,
) -> dict[str, Any]:
    """Build the ownerless retained Loki data claim."""
    component = "loki-data"
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": build_resource_metadata(
            resource_name=appliance_resource_name(appliance_name, component),
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            retention=LOKI_PVC_RETENTION_VALUE,
        ),
        "spec": {
            "storageClassName": settings.storage.storage_class_name,
            "accessModes": [LOKI_PVC_ACCESS_MODE],
            "volumeMode": LOKI_PVC_VOLUME_MODE,
            "resources": {"requests": {"storage": settings.storage.size}},
        },
    }


def build_loki_config_map(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced Loki configuration ConfigMap."""
    component = "loki-config"
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
        "data": _validated_opaque_values(values, LOKI_CONFIG_KEYS, resource_name),
    }


def build_gateway_config_secret(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced gateway config Secret apply body."""
    component = "gateway-config"
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
            _validated_opaque_values(values, GATEWAY_CONFIG_KEYS, resource_name)
        ),
    }


def build_gateway_service(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the ClusterIP gateway Service exposing only the NGINX port."""
    component = "gateway"
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
                GATEWAY_LABEL_KEY: component,
            },
            "ports": [
                {
                    "name": component,
                    "protocol": "TCP",
                    "port": NGINX_PORT,
                    "targetPort": NGINX_PORT,
                }
            ],
        },
    }


def _logging_container_security_context(run_as_id: int) -> dict[str, Any]:
    return {
        "runAsNonRoot": True,
        "readOnlyRootFilesystem": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "seccompProfile": {"type": "RuntimeDefault"},
        "runAsUser": run_as_id,
        "runAsGroup": run_as_id,
    }


def _gateway_probe(
    *,
    period_seconds: int,
    timeout_seconds: int,
    failure_threshold: int,
    success_threshold: int | None = None,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "httpGet": {"path": GATEWAY_READY_PATH, "port": NGINX_PORT},
        "periodSeconds": period_seconds,
        "timeoutSeconds": timeout_seconds,
        "failureThreshold": failure_threshold,
    }
    if success_threshold is not None:
        probe["successThreshold"] = success_threshold
    return probe


def build_loki_stateful_set(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    settings: LoggingSettings,
) -> dict[str, Any]:
    """Build the one-replica Loki StatefulSet with the NGINX gateway sidecar."""
    component = "loki"
    resource_name = appliance_resource_name(appliance_name, component)
    gateway_service_name = appliance_resource_name(appliance_name, "gateway")
    data_claim_name = appliance_resource_name(appliance_name, "loki-data")
    config_map_name = appliance_resource_name(appliance_name, "loki-config")
    gateway_secret_name = appliance_resource_name(appliance_name, "gateway-config")
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
    pod_labels = dict(metadata["labels"])
    pod_labels[GATEWAY_LABEL_KEY] = "gateway"
    loki_resources = settings.resources["loki"]
    gateway_resources = settings.resources["gateway"]
    loki_container = {
        "name": "loki",
        "image": LOKI_IMAGE,
        "imagePullPolicy": "Always",
        "command": [LOKI_ENTRYPOINT],
        "args": [f"-config.file={LOKI_CONFIG_PATH}"],
        "ports": [{"name": "http", "containerPort": LOKI_PORT, "protocol": "TCP"}],
        "resources": {
            "requests": {
                "cpu": loki_resources.requests_cpu,
                "memory": loki_resources.requests_memory,
            },
            "limits": {
                "cpu": loki_resources.limits_cpu,
                "memory": loki_resources.limits_memory,
            },
        },
        "securityContext": _logging_container_security_context(LOKI_RUN_AS_ID),
        "volumeMounts": [
            {"name": "data", "mountPath": LOKI_DATA_DIR},
            {"name": "tmp", "mountPath": LOKI_TMP_DIR},
            {"name": "config", "mountPath": LOKI_CONFIG_DIR, "readOnly": True},
        ],
        "startupProbe": _gateway_probe(
            period_seconds=5, timeout_seconds=3, failure_threshold=30
        ),
        "readinessProbe": _gateway_probe(
            period_seconds=10,
            timeout_seconds=3,
            failure_threshold=3,
            success_threshold=1,
        ),
        "livenessProbe": _gateway_probe(
            period_seconds=10, timeout_seconds=3, failure_threshold=6
        ),
    }
    gateway_container = {
        "name": "gateway",
        "image": GATEWAY_IMAGE,
        "imagePullPolicy": "Always",
        "command": [GATEWAY_BINARY],
        "args": [
            "-c",
            GATEWAY_NGINX_CONF_PATH,
            "-g",
            "daemon off;",
        ],
        "ports": [{"name": "gateway", "containerPort": NGINX_PORT, "protocol": "TCP"}],
        "resources": {
            "requests": {
                "cpu": gateway_resources.requests_cpu,
                "memory": gateway_resources.requests_memory,
            },
            "limits": {
                "cpu": gateway_resources.limits_cpu,
                "memory": gateway_resources.limits_memory,
            },
        },
        "securityContext": _logging_container_security_context(NGINX_RUN_AS_ID),
        "volumeMounts": [
            {
                "name": "gateway-config",
                "mountPath": GATEWAY_CONFIG_DIR,
                "readOnly": True,
            },
            {"name": "gateway-tmp", "mountPath": GATEWAY_TMP_DIR},
        ],
        "startupProbe": _gateway_probe(
            period_seconds=5, timeout_seconds=3, failure_threshold=30
        ),
        "readinessProbe": _gateway_probe(
            period_seconds=10,
            timeout_seconds=3,
            failure_threshold=3,
            success_threshold=1,
        ),
        "livenessProbe": _gateway_probe(
            period_seconds=10, timeout_seconds=3, failure_threshold=6
        ),
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": metadata,
        "spec": {
            "serviceName": gateway_service_name,
            "replicas": LOKI_REPLICAS,
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "imagePullSecrets": [{"name": LOGGING_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "fsGroup": LOKI_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "terminationGracePeriodSeconds": (
                        LOKI_TERMINATION_GRACE_PERIOD_SECONDS
                    ),
                    "containers": [loki_container, gateway_container],
                    "volumes": [
                        {
                            "name": "data",
                            "persistentVolumeClaim": {"claimName": data_claim_name},
                        },
                        {"name": "tmp", "emptyDir": {"medium": "Memory"}},
                        {
                            "name": "config",
                            "configMap": {
                                "name": config_map_name,
                                "items": [
                                    {
                                        "key": "loki.yaml",
                                        "path": "loki.yaml",
                                        "mode": 0o444,
                                    }
                                ],
                            },
                        },
                        {
                            "name": "gateway-config",
                            "secret": {
                                "secretName": gateway_secret_name,
                                "items": [
                                    {"key": key, "path": key, "mode": 0o444}
                                    for key in sorted(GATEWAY_CONFIG_KEYS)
                                ],
                            },
                        },
                        {
                            "name": "gateway-tmp",
                            "emptyDir": {"medium": "Memory"},
                        },
                    ],
                },
            },
        },
    }


def render_alloy_config(
    *,
    namespace: str,
    appliance_name: str,
    gateway_service_name: str,
    basic_auth_username: str,
    credentials_path: str = ALLOY_CREDENTIAL_PATH,
) -> str:
    """Render the per-CR Alloy collection config for the appliance gateway.

    The config restricts API discovery to the CR namespace and to an exact
    server-side label selector for the appliance plus the app-component
    allowlist (logging components excluded), relabels entries to namespace,
    full coriolis_appliance (as a static replacement of the validated full
    appliance_name, independent of any pod label or annotation), component,
    pod, container, and stream only when the API source supplies a truthful
    stream value, parses a recognized severity prefix into the ``severity``
    label only when valid, and pushes solely to the per-CR gateway Service with
    Basic auth over the retained write credential. No tenant header is sent
    because the gateway owns tenant routing.
    """
    identity = appliance_identity(appliance_name)
    app_components = ", ".join(ALLOY_APP_COLLECTION_COMPONENTS)
    selector = (
        f"coriolis.cloudbase.it/appliance={identity}"
        ",coriolis.cloudbase.it/component in ("
        f"{app_components})"
    )
    return f"""discovery.kubernetes "pods" {{
  role = "pod"
  namespaces {{
    names = ["{namespace}"]
  }}
  selectors {{
    role = "pod"
    label = "{selector}"
  }}
}}

discovery.relabel "pods" {{
  targets = discovery.kubernetes.pods.targets
  rule {{
    source_labels = ["{_META_NAMESPACE}"]
    target_label = "namespace"
  }}
  rule {{
    replacement = "{appliance_name}"
    target_label = "coriolis_appliance"
  }}
  rule {{
    source_labels = ["{_META_POD_COMPONENT_LABEL}"]
    target_label = "coriolis_component"
  }}
  rule {{
    source_labels = ["{_META_POD_NAME}"]
    target_label = "pod"
  }}
  rule {{
    source_labels = ["{_META_POD_CONTAINER_NAME}"]
    target_label = "container"
  }}
  rule {{
    source_labels = ["{_META_POD_STREAM}"]
    regex = ".+"
    target_label = "stream"
  }}
}}

loki.source.kubernetes "pods" {{
  targets = discovery.relabel.pods.output
  forward_to = [loki.process.pods.receiver]
}}

loki.process "pods" {{
  stage.regex {{
    source = "line"
    expression = `{_SEVERITY_PREFIX_RE}`
  }}
  stage.labels {{
    values = {{
      severity = "level",
    }}
  }}
  forward_to = [loki.write.gateway.receiver]
}}

loki.write "gateway" {{
  endpoint {{
    url = "http://{gateway_service_name}:{NGINX_PORT}/loki/api/v1/push"
    basic_auth {{
      username = "{basic_auth_username}"
      password_file = "{credentials_path}"
    }}
  }}
}}
"""


def build_alloy_config_map(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    values: Mapping[str, str],
) -> dict[str, Any]:
    """Build the owner-referenced Alloy configuration ConfigMap."""
    component = ALLOY_CONFIG_COMPONENT
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
        "data": _validated_opaque_values(values, ALLOY_CONFIG_KEYS, resource_name),
    }


def build_alloy_service_account(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the dedicated namespaced Alloy ServiceAccount."""
    component = ALLOY_SERVICE_ACCOUNT_COMPONENT
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": build_resource_metadata(
            resource_name=appliance_resource_name(appliance_name, component),
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        ),
    }


def build_alloy_role(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the narrow namespaced Role for API-based pod log collection."""
    component = ALLOY_ROLE_COMPONENT
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": build_resource_metadata(
            resource_name=appliance_resource_name(appliance_name, component),
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        ),
        "rules": [
            {
                "apiGroups": [""],
                "resources": ["pods"],
                "verbs": ["get", "list", "watch"],
            },
            {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
        ],
    }


def build_alloy_role_binding(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind the Alloy Role to the dedicated Alloy ServiceAccount."""
    role_name = appliance_resource_name(appliance_name, ALLOY_ROLE_COMPONENT)
    service_account_name = appliance_resource_name(
        appliance_name, ALLOY_SERVICE_ACCOUNT_COMPONENT
    )
    component = ALLOY_ROLE_BINDING_COMPONENT
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": build_resource_metadata(
            resource_name=appliance_resource_name(appliance_name, component),
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        ),
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": role_name,
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": service_account_name,
                "namespace": namespace,
            }
        ],
    }


def _alloy_probe(
    *,
    period_seconds: int,
    timeout_seconds: int,
    failure_threshold: int,
    success_threshold: int | None = None,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "httpGet": {"path": ALLOY_READY_PATH, "port": ALLOY_HTTP_PORT},
        "periodSeconds": period_seconds,
        "timeoutSeconds": timeout_seconds,
        "failureThreshold": failure_threshold,
    }
    if success_threshold is not None:
        probe["successThreshold"] = success_threshold
    return probe


def build_alloy_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    settings: LoggingSettings,
) -> dict[str, Any]:
    """Build the hardened one-replica Alloy Deployment for one appliance CR."""
    component = ALLOY_DEPLOYMENT_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    config_map_name = appliance_resource_name(appliance_name, ALLOY_CONFIG_COMPONENT)
    credentials_secret_name = appliance_resource_name(
        appliance_name, LOGGING_CREDENTIALS_RETENTION_VALUE
    )
    service_account_name = appliance_resource_name(
        appliance_name, ALLOY_SERVICE_ACCOUNT_COMPONENT
    )
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
    pod_labels = dict(metadata["labels"])
    alloy_resources = settings.resources["alloy"]
    alloy_container = {
        "name": component,
        "image": ALLOY_IMAGE,
        "imagePullPolicy": "Always",
        "command": [ALLOY_BINARY],
        "args": [
            "run",
            ALLOY_CONFIG_PATH,
            f"--server.http.listen-addr=0.0.0.0:{ALLOY_HTTP_PORT}",
            f"--storage.path={ALLOY_DATA_DIR}",
            "--disable-reporting=true",
        ],
        "ports": [
            {"name": component, "containerPort": ALLOY_HTTP_PORT, "protocol": "TCP"}
        ],
        "resources": {
            "requests": {
                "cpu": alloy_resources.requests_cpu,
                "memory": alloy_resources.requests_memory,
            },
            "limits": {
                "cpu": alloy_resources.limits_cpu,
                "memory": alloy_resources.limits_memory,
            },
        },
        "securityContext": _logging_container_security_context(ALLOY_RUN_AS_ID),
        "volumeMounts": [
            {"name": "config", "mountPath": ALLOY_CONFIG_DIR, "readOnly": True},
            {
                "name": "credentials",
                "mountPath": ALLOY_CREDENTIAL_MOUNT_DIR,
                "readOnly": True,
            },
            {"name": "storage", "mountPath": ALLOY_DATA_DIR},
            {"name": "tmp", "mountPath": ALLOY_TMP_DIR},
        ],
        "startupProbe": _alloy_probe(
            period_seconds=5, timeout_seconds=3, failure_threshold=30
        ),
        "readinessProbe": _alloy_probe(
            period_seconds=10,
            timeout_seconds=3,
            failure_threshold=3,
            success_threshold=1,
        ),
        "livenessProbe": _alloy_probe(
            period_seconds=10, timeout_seconds=3, failure_threshold=6
        ),
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": ALLOY_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "serviceAccountName": service_account_name,
                    "automountServiceAccountToken": True,
                    "enableServiceLinks": False,
                    "imagePullSecrets": [{"name": LOGGING_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "fsGroup": ALLOY_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "containers": [alloy_container],
                    "volumes": [
                        {
                            "name": "config",
                            "configMap": {
                                "name": config_map_name,
                                "items": [
                                    {
                                        "key": "config.alloy",
                                        "path": "config.alloy",
                                        "mode": 0o444,
                                    }
                                ],
                            },
                        },
                        {
                            "name": "credentials",
                            "secret": {
                                "secretName": credentials_secret_name,
                                "items": [
                                    {
                                        "key": "write_password",
                                        "path": "write_password",
                                        "mode": 0o444,
                                    }
                                ],
                            },
                        },
                        {"name": "storage", "emptyDir": {}},
                        {"name": "tmp", "emptyDir": {}},
                    ],
                },
            },
        },
    }


def _adaptor_env(
    *,
    appliance_name: str,
    namespace: str,
    cr_uid: str,
    keystone_url: str,
    gateway_url: str,
) -> list[dict[str, str]]:
    """Return the non-sensitive adaptor environment referencing validated inputs."""
    return [
        {"name": f"{ADAPTOR_ENV_PREFIX}KEYSTONE_URL", "value": keystone_url},
        {"name": f"{ADAPTOR_ENV_PREFIX}ADMIN_ROLES", "value": ADAPTOR_ADMIN_ROLES},
        {
            "name": f"{ADAPTOR_ENV_PREFIX}COMPONENTS",
            "value": ",".join(ALLOY_APP_COLLECTION_COMPONENTS),
        },
        {"name": f"{ADAPTOR_ENV_PREFIX}LOKI_GATEWAY_URL", "value": gateway_url},
        {
            "name": f"{ADAPTOR_ENV_PREFIX}LOKI_PASSWORD_FILE",
            "value": ADAPTOR_CREDENTIAL_PATH,
        },
        {"name": f"{ADAPTOR_ENV_PREFIX}CR_UID", "value": cr_uid},
        {"name": f"{ADAPTOR_ENV_PREFIX}NAMESPACE", "value": namespace},
        {"name": f"{ADAPTOR_ENV_PREFIX}APPLIANCE_NAME", "value": appliance_name},
        {"name": "HOME", "value": "/tmp"},
        {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
    ]


def _adaptor_probe(
    *,
    path: str,
    period_seconds: int,
    timeout_seconds: int,
    failure_threshold: int,
    success_threshold: int | None = None,
) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "httpGet": {"path": path, "port": ADAPTOR_PORT},
        "periodSeconds": period_seconds,
        "timeoutSeconds": timeout_seconds,
        "failureThreshold": failure_threshold,
    }
    if success_threshold is not None:
        probe["successThreshold"] = success_threshold
    return probe


def build_adaptor_deployment(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    settings: LoggingSettings,
    cr_uid: str,
) -> dict[str, Any]:
    """Build the hardened one-replica per-CR logs API adaptor Deployment.

    The container runs as UID/GID 10001 with a read-only root, dropped
    capabilities, no privilege escalation, and no service account. Its only
    mounted Secret is the retained ``read_password`` credential mounted
    read-only at a fixed file so the adaptor never holds a write credential.
    All environment values are non-sensitive and every user-derived input is
    validated through the shared naming and tenant validators.
    """
    logging_tenant(cr_uid)
    component = ADAPTOR_DEPLOYMENT_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    keystone_url = (
        f"http://{appliance_resource_name(appliance_name, 'keystone')}:5000/v3"
    )
    gateway_url = f"http://{appliance_resource_name(appliance_name, 'gateway')}:8080"
    credentials_secret_name = appliance_resource_name(
        appliance_name, LOGGING_CREDENTIALS_RETENTION_VALUE
    )
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
    pod_labels = dict(metadata["labels"])
    adaptor_resources = settings.resources["adaptor"]
    adaptor_container = {
        "name": component,
        "image": ADAPTOR_IMAGE,
        "imagePullPolicy": "Always",
        "command": [ADAPTOR_BINARY],
        "workingDir": ADAPTOR_WORKDIR,
        "env": _adaptor_env(
            appliance_name=appliance_name,
            namespace=namespace,
            cr_uid=cr_uid,
            keystone_url=keystone_url,
            gateway_url=gateway_url,
        ),
        "ports": [
            {
                "name": component,
                "containerPort": ADAPTOR_PORT,
                "protocol": "TCP",
            }
        ],
        "resources": {
            "requests": {
                "cpu": adaptor_resources.requests_cpu,
                "memory": adaptor_resources.requests_memory,
            },
            "limits": {
                "cpu": adaptor_resources.limits_cpu,
                "memory": adaptor_resources.limits_memory,
            },
        },
        "securityContext": _logging_container_security_context(ADAPTOR_RUN_AS_ID),
        "volumeMounts": [
            {
                "name": "credentials",
                "mountPath": ADAPTOR_CREDENTIAL_MOUNT_DIR,
                "readOnly": True,
            },
            {"name": "tmp", "mountPath": ADAPTOR_TMP_DIR},
        ],
        "startupProbe": _adaptor_probe(
            path=ADAPTOR_READY_PATH,
            period_seconds=5,
            timeout_seconds=3,
            failure_threshold=30,
        ),
        "readinessProbe": _adaptor_probe(
            path=ADAPTOR_READY_PATH,
            period_seconds=10,
            timeout_seconds=3,
            failure_threshold=3,
            success_threshold=1,
        ),
        "livenessProbe": _adaptor_probe(
            path=ADAPTOR_HEALTH_PATH,
            period_seconds=10,
            timeout_seconds=3,
            failure_threshold=6,
        ),
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": metadata,
        "spec": {
            "replicas": ADAPTOR_REPLICAS,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": pod_labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "imagePullSecrets": [{"name": LOGGING_IMAGE_PULL_SECRET_NAME}],
                    "securityContext": {
                        "fsGroup": ADAPTOR_RUN_AS_ID,
                        "fsGroupChangePolicy": "OnRootMismatch",
                    },
                    "containers": [adaptor_container],
                    "volumes": [
                        {
                            "name": "credentials",
                            "secret": {
                                "secretName": credentials_secret_name,
                                "items": [
                                    {
                                        "key": "read_password",
                                        "path": "read_password",
                                        "mode": 0o444,
                                    }
                                ],
                            },
                        },
                        {"name": "tmp", "emptyDir": {"medium": "Memory"}},
                    ],
                },
            },
        },
    }


def build_adaptor_service(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the ClusterIP adaptor Service selecting the adaptor pods."""
    component = ADAPTOR_SERVICE_COMPONENT
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
                    "port": ADAPTOR_PORT,
                    "targetPort": ADAPTOR_PORT,
                }
            ],
        },
    }


def _adaptor_ingress_annotations() -> dict[str, str]:
    """Return the adaptor Ingress annotations (access log off, no CORS/rewrite)."""
    return dict(_ADAPTOR_INGRESS_ANNOTATIONS)


def build_adaptor_ingress(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    settings: IngressSettings,
) -> dict[str, Any]:
    """Build the dedicated adaptor Ingress with direct no-rewrite log routes.

    The Ingress shares the resolved host, ingress class, and TLS Secret with the
    other appliance Ingresses but intentionally omits the cert-manager ownership
    annotation so the primary web Ingress remains the certificate owner. Routes
    `/logs` and `/log-stream` forward directly (no rewrite) to the adaptor
    Service, ingress access logging is disabled, and the proxy/WebSocket
    timeouts and buffering are tuned without enabling permissive CORS.
    """
    component = ADAPTOR_INGRESS_COMPONENT
    resource_name = appliance_resource_name(appliance_name, component)
    metadata = build_resource_metadata(
        resource_name=resource_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=component,
        accepted_version=accepted_version,
        owner=owner,
    )
    metadata["annotations"] = dict(metadata["annotations"]) | (
        _adaptor_ingress_annotations()
    )
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": metadata,
        "spec": {
            "ingressClassName": settings.ingress_class_name,
            "tls": [{"hosts": [settings.host], "secretName": settings.tls_secret_name}],
            "rules": [
                {
                    "host": settings.host,
                    "http": {
                        "paths": [
                            {
                                "path": "/logs",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": resource_name,
                                        "port": {"number": ADAPTOR_PORT},
                                    }
                                },
                            },
                            {
                                "path": "/log-stream",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": resource_name,
                                        "port": {"number": ADAPTOR_PORT},
                                    }
                                },
                            },
                        ]
                    },
                }
            ],
        },
    }


LOGGING_CREDENTIALS_KEY = "credentials-secret"
LOGGING_LOKI_PVC_KEY = "loki-pvc"
LOGGING_LOKI_CONFIG_KEY = "loki-config"
LOGGING_GATEWAY_CONFIG_SECRET_KEY = "gateway-config-secret"
LOGGING_GATEWAY_SERVICE_KEY = "gateway-service"
LOGGING_LOKI_STATEFUL_SET_KEY = "loki"
LOGGING_ALLOY_CONFIG_KEY = "alloy-config"
LOGGING_ALLOY_SERVICE_ACCOUNT_KEY = "alloy-sa"
LOGGING_ALLOY_ROLE_KEY = "alloy-role"
LOGGING_ALLOY_ROLE_BINDING_KEY = "alloy-rb"
LOGGING_ALLOY_DEPLOYMENT_KEY = "alloy"
LOGGING_ADAPTOR_DEPLOYMENT_KEY = "adaptor-deployment"
LOGGING_ADAPTOR_SERVICE_KEY = "adaptor-service"
LOGGING_ADAPTOR_INGRESS_KEY = "adaptor-ingress"

LOGGING_LOKI_PHASE_KEYS = (
    LOGGING_CREDENTIALS_KEY,
    LOGGING_LOKI_PVC_KEY,
    LOGGING_LOKI_CONFIG_KEY,
    LOGGING_GATEWAY_CONFIG_SECRET_KEY,
    LOGGING_GATEWAY_SERVICE_KEY,
    LOGGING_LOKI_STATEFUL_SET_KEY,
)
LOGGING_ALLOY_PHASE_KEYS = (
    LOGGING_ALLOY_CONFIG_KEY,
    LOGGING_ALLOY_SERVICE_ACCOUNT_KEY,
    LOGGING_ALLOY_ROLE_KEY,
    LOGGING_ALLOY_ROLE_BINDING_KEY,
    LOGGING_ALLOY_DEPLOYMENT_KEY,
)
LOGGING_ADAPTOR_PHASE_KEYS = (
    LOGGING_ADAPTOR_SERVICE_KEY,
    LOGGING_ADAPTOR_DEPLOYMENT_KEY,
)
LOGGING_ADAPTOR_INGRESS_PHASE_KEYS = (LOGGING_ADAPTOR_INGRESS_KEY,)

LOGGING_PHASE_KEYS = (
    LOGGING_LOKI_PHASE_KEYS
    + LOGGING_ALLOY_PHASE_KEYS
    + LOGGING_ADAPTOR_PHASE_KEYS
    + LOGGING_ADAPTOR_INGRESS_PHASE_KEYS
)


@dataclass(frozen=True)
class LoggingExistingResources:
    """Immutable, fully redacted input of every observed logging object.

    Every field is excluded from representations so a retained credentials
    Secret or gateway config Secret body can never leak through this container.
    """

    credentials_secret: Any | None = field(repr=False, default=None)
    loki_pvc: Any | None = field(repr=False, default=None)
    loki_config_map: Any | None = field(repr=False, default=None)
    gateway_config_secret: Any | None = field(repr=False, default=None)
    gateway_service: Any | None = field(repr=False, default=None)
    loki_stateful_set: Any | None = field(repr=False, default=None)
    alloy_config_map: Any | None = field(repr=False, default=None)
    alloy_service_account: Any | None = field(repr=False, default=None)
    alloy_role: Any | None = field(repr=False, default=None)
    alloy_role_binding: Any | None = field(repr=False, default=None)
    alloy_deployment: Any | None = field(repr=False, default=None)
    adaptor_deployment: Any | None = field(repr=False, default=None)
    adaptor_service: Any | None = field(repr=False, default=None)
    adaptor_ingress: Any | None = field(repr=False, default=None)

    def loki_phase_ready(self) -> bool:
        """Return whether the observed Loki stack satisfies its readiness gate."""
        return (
            self.loki_pvc is not None
            and _pvc_ready(self.loki_pvc)
            and _stateful_set_ready(self.loki_stateful_set)
            and self.gateway_service is not None
        )

    def alloy_phase_ready(self) -> bool:
        """Return whether the observed Alloy Deployment is ready."""
        return _deployment_ready(self.alloy_deployment)

    def adaptor_phase_ready(self) -> bool:
        """Return whether the observed adaptor process and Service are ready."""
        return (
            _deployment_ready(self.adaptor_deployment)
            and self.adaptor_service is not None
        )

    def adaptor_ingress_ready(self) -> bool:
        """Return whether the observed adaptor route and process are ready."""
        return self.adaptor_ingress is not None and self.adaptor_phase_ready()

    def operational_ready(self) -> bool:
        """Return whether the whole observed logging stack is ready."""
        return (
            self.loki_phase_ready()
            and self.alloy_phase_ready()
            and self.adaptor_ingress_ready()
        )


@dataclass(frozen=True)
class LoggingPreflight:
    """Pure, fail-closed preflight result for every logging resource.

    ``classifications`` is keyed by logical key (the adaptor Deployment,
    Service, and Ingress intentionally share a Kubernetes name across kinds).
    ``manifests`` and ``credentials`` are excluded from representations so no
    Secret or gateway config body leaks through ``repr``.
    """

    classifications: Mapping[str, RetainedClassification | OwnedClassification]
    manifests: Mapping[str, dict[str, Any]] = field(repr=False)
    credentials: Mapping[str, str] | None = field(repr=False, default=None)
    collision_resource_name: str | None = field(repr=False, default=None)

    def loki_phase(self) -> tuple[dict[str, Any], ...]:
        """Return the apply-ordered Loki phase manifests (empty on collision)."""
        return tuple(
            self.manifests[key]
            for key in LOGGING_LOKI_PHASE_KEYS
            if key in self.manifests
        )

    def alloy_phase(self) -> tuple[dict[str, Any], ...]:
        """Return the apply-ordered Alloy phase manifests (empty on collision)."""
        return tuple(
            self.manifests[key]
            for key in LOGGING_ALLOY_PHASE_KEYS
            if key in self.manifests
        )

    def adaptor_phase(self) -> tuple[dict[str, Any], ...]:
        """Return the apply-ordered adaptor Service/Deployment manifests."""
        return tuple(
            self.manifests[key]
            for key in LOGGING_ADAPTOR_PHASE_KEYS
            if key in self.manifests
        )

    def adaptor_ingress_phase(self) -> tuple[dict[str, Any], ...]:
        """Return the apply-ordered adaptor Ingress manifests."""
        return tuple(
            self.manifests[key]
            for key in LOGGING_ADAPTOR_INGRESS_PHASE_KEYS
            if key in self.manifests
        )


def preflight_logging_resources(
    *,
    appliance_name: str,
    namespace: str,
    accepted_version: str,
    owner: Mapping[str, Any],
    cr_uid: str,
    settings: LoggingSettings,
    ingress_settings: IngressSettings,
    existing: LoggingExistingResources,
    password_factory: Callable[[int], str] = secrets.token_urlsafe,
    hash_factory: Callable[[str], str] = _bcrypt_hash,
) -> LoggingPreflight:
    """Classify every logging resource before building any desired body.

    Retained resources are classified by exact ownerless retained identity, and
    any retained collision aborts before credentials are parsed. Resolved
    credentials are generated once when absent, reused from the existing
    retained Secret otherwise, and never read from a colliding Secret. Every
    owned manifest is then built and classified against the exact owner
    metadata, so a collision in any future phase is reported before any phase
    can write. Caller objects are never mutated and representations never leak
    Secret or config bodies.
    """
    classifications: dict[str, RetainedClassification | OwnedClassification] = {}

    credentials_name = appliance_resource_name(
        appliance_name, LOGGING_CREDENTIALS_RETENTION_VALUE
    )
    credentials_classification = classify_retained_resource(
        existing=existing.credentials_secret,
        resource_name=credentials_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component=LOGGING_CREDENTIALS_RETENTION_VALUE,
        accepted_version=accepted_version,
        retention=LOGGING_CREDENTIALS_RETENTION_VALUE,
    )
    classifications[LOGGING_CREDENTIALS_KEY] = credentials_classification
    if credentials_classification is RetainedClassification.COLLISION:
        return LoggingPreflight(
            classifications, {}, None, collision_resource_name=credentials_name
        )

    pvc_name = appliance_resource_name(appliance_name, "loki-data")
    pvc_classification = classify_retained_resource(
        existing=existing.loki_pvc,
        resource_name=pvc_name,
        namespace=namespace,
        appliance_name=appliance_name,
        component="loki-data",
        accepted_version=accepted_version,
        retention=LOKI_PVC_RETENTION_VALUE,
    )
    classifications[LOGGING_LOKI_PVC_KEY] = pvc_classification
    if pvc_classification is RetainedClassification.COLLISION:
        return LoggingPreflight(
            classifications, {}, None, collision_resource_name=pvc_name
        )

    if credentials_classification is RetainedClassification.ABSENT:
        credentials_values = generate_logging_credentials(
            password_factory=password_factory, hash_factory=hash_factory
        )
    else:
        credentials_values = validated_retained_secret_values(
            existing=existing.credentials_secret,
            expected_keys=LOGGING_CREDENTIALS_KEYS,
        )
    credentials = decode_logging_credentials(credentials_values)

    tenant = logging_tenant(cr_uid)
    gateway_config = render_gateway_config(credentials=credentials, tenant=tenant)
    loki_config = render_loki_config(settings=settings)
    alloy_render = render_alloy_config(
        namespace=namespace,
        appliance_name=appliance_name,
        gateway_service_name=appliance_resource_name(appliance_name, "gateway"),
        basic_auth_username=tenant,
    )

    manifests: dict[str, dict[str, Any]] = {
        LOGGING_CREDENTIALS_KEY: build_logging_credentials_secret(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            retention=LOGGING_CREDENTIALS_RETENTION_VALUE,
            values=credentials_values,
        ),
        LOGGING_LOKI_PVC_KEY: build_loki_data_pvc(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            settings=settings,
        ),
        LOGGING_LOKI_CONFIG_KEY: build_loki_config_map(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
            values=loki_config,
        ),
        LOGGING_GATEWAY_CONFIG_SECRET_KEY: build_gateway_config_secret(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
            values=gateway_config,
        ),
        LOGGING_GATEWAY_SERVICE_KEY: build_gateway_service(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
        ),
        LOGGING_LOKI_STATEFUL_SET_KEY: build_loki_stateful_set(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
            settings=settings,
        ),
        LOGGING_ALLOY_CONFIG_KEY: build_alloy_config_map(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
            values={"config.alloy": alloy_render},
        ),
        LOGGING_ALLOY_SERVICE_ACCOUNT_KEY: build_alloy_service_account(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
        ),
        LOGGING_ALLOY_ROLE_KEY: build_alloy_role(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
        ),
        LOGGING_ALLOY_ROLE_BINDING_KEY: build_alloy_role_binding(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
        ),
        LOGGING_ALLOY_DEPLOYMENT_KEY: build_alloy_deployment(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
            settings=settings,
        ),
        LOGGING_ADAPTOR_DEPLOYMENT_KEY: build_adaptor_deployment(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
            settings=settings,
            cr_uid=cr_uid,
        ),
        LOGGING_ADAPTOR_SERVICE_KEY: build_adaptor_service(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
        ),
        LOGGING_ADAPTOR_INGRESS_KEY: build_adaptor_ingress(
            appliance_name=appliance_name,
            namespace=namespace,
            accepted_version=accepted_version,
            owner=owner,
            settings=ingress_settings,
        ),
    }

    owned_items = (
        (LOGGING_LOKI_CONFIG_KEY, "loki-config", existing.loki_config_map),
        (
            LOGGING_GATEWAY_CONFIG_SECRET_KEY,
            "gateway-config",
            existing.gateway_config_secret,
        ),
        (LOGGING_GATEWAY_SERVICE_KEY, "gateway", existing.gateway_service),
        (LOGGING_LOKI_STATEFUL_SET_KEY, "loki", existing.loki_stateful_set),
        (LOGGING_ALLOY_CONFIG_KEY, ALLOY_CONFIG_COMPONENT, existing.alloy_config_map),
        (
            LOGGING_ALLOY_SERVICE_ACCOUNT_KEY,
            ALLOY_SERVICE_ACCOUNT_COMPONENT,
            existing.alloy_service_account,
        ),
        (LOGGING_ALLOY_ROLE_KEY, ALLOY_ROLE_COMPONENT, existing.alloy_role),
        (
            LOGGING_ALLOY_ROLE_BINDING_KEY,
            ALLOY_ROLE_BINDING_COMPONENT,
            existing.alloy_role_binding,
        ),
        (
            LOGGING_ALLOY_DEPLOYMENT_KEY,
            ALLOY_DEPLOYMENT_COMPONENT,
            existing.alloy_deployment,
        ),
        (
            LOGGING_ADAPTOR_DEPLOYMENT_KEY,
            ADAPTOR_DEPLOYMENT_COMPONENT,
            existing.adaptor_deployment,
        ),
        (
            LOGGING_ADAPTOR_SERVICE_KEY,
            ADAPTOR_SERVICE_COMPONENT,
            existing.adaptor_service,
        ),
        (
            LOGGING_ADAPTOR_INGRESS_KEY,
            ADAPTOR_INGRESS_COMPONENT,
            existing.adaptor_ingress,
        ),
    )
    for logical_key, component, observed in owned_items:
        resource_name = appliance_resource_name(appliance_name, component)
        classification = classify_owned_resource(
            existing=observed,
            resource_name=resource_name,
            namespace=namespace,
            appliance_name=appliance_name,
            component=component,
            accepted_version=accepted_version,
            owner=owner,
        )
        classifications[logical_key] = classification
        if classification is OwnedClassification.COLLISION:
            return LoggingPreflight(
                classifications, {}, None, collision_resource_name=resource_name
            )

    return LoggingPreflight(classifications, manifests, credentials_values)
