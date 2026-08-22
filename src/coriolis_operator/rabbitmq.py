"""Pure RabbitMQ runtime settings validation and configuration rendering."""

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from kubernetes.utils.quantity import parse_quantity  # type: ignore[import-untyped]

RABBITMQ_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/rabbitmq:2023.1-ubuntu-jammy"
    "@sha256:a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a"
)
RABBITMQ_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
RABBITMQ_REPLICAS = 1
RABBITMQ_RUN_AS_ID = 42439
RABBITMQ_PORT = 5672
RABBITMQ_PVC_ACCESS_MODE = "ReadWriteOnce"
RABBITMQ_PVC_VOLUME_MODE = "Filesystem"
RABBITMQ_PVC_RETENTION_VALUE = "rabbitmq-data"
RABBITMQ_TERMINATION_GRACE_PERIOD_SECONDS = 60
RABBITMQ_DATA_DIR = "/var/lib/rabbitmq"
RABBITMQ_RUNTIME_DIR = "/run/rabbitmq"
RABBITMQ_LOG_DIR = "/var/log/rabbitmq"
RABBITMQ_CONFIG_DIR = "/etc/rabbitmq"
RABBITMQ_SECRET_DIR = "/etc/rabbitmq-secret"
RABBITMQ_PASSWORD_PATH = f"{RABBITMQ_SECRET_DIR}/rabbitmq_password"
RABBITMQ_SERVER = "/usr/sbin/rabbitmq-server"
RABBITMQ_DIAGNOSTICS = "/usr/sbin/rabbitmq-diagnostics"
RABBITMQ_CONFIG_KEYS = frozenset({"rabbitmq.conf", "start-rabbitmq.sh"})

_INVALID_SETTINGS_MESSAGE = "invalid RabbitMQ settings"


@dataclass(frozen=True)
class RabbitMQStorageSettings:
    """Validated immutable RabbitMQ persistent-volume settings."""

    storage_class_name: str
    size: str


@dataclass(frozen=True)
class RabbitMQResourceSettings:
    """Validated immutable RabbitMQ resource quantity strings."""

    requests_cpu: str
    requests_memory: str
    limits_cpu: str
    limits_memory: str


@dataclass(frozen=True)
class RabbitMQSettings:
    """Complete, validated RabbitMQ runtime settings."""

    storage: RabbitMQStorageSettings
    resources: RabbitMQResourceSettings


def _invalid_settings() -> ValueError:
    return ValueError(_INVALID_SETTINGS_MESSAGE)


def _required_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _invalid_settings()
    return value


def _required_string(value: object) -> str:
    if type(value) is not str or not value:
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


def resolve_rabbitmq_settings(
    *, storage: object, resources: object
) -> RabbitMQSettings:
    """Validate complete RabbitMQ CR input without mutating caller mappings."""
    storage_values = _required_mapping(storage)
    resources_values = _required_mapping(resources)
    rabbit_storage = _required_mapping(storage_values.get("rabbitmq"))
    rabbit_resources = _required_mapping(resources_values.get("rabbitmq"))
    requests = _required_mapping(rabbit_resources.get("requests"))
    limits = _required_mapping(rabbit_resources.get("limits"))

    storage_class_name = _validated_storage_class_name(
        rabbit_storage.get("storageClassName")
    )
    size, _ = _validated_quantity(rabbit_storage.get("size"))
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

    return RabbitMQSettings(
        storage=RabbitMQStorageSettings(storage_class_name, size),
        resources=RabbitMQResourceSettings(
            requests_cpu, requests_memory, limits_cpu, limits_memory
        ),
    )


def render_rabbitmq_config() -> dict[str, str]:
    """Return the fixed credential-free ConfigMap values for RabbitMQ."""
    return {
        "rabbitmq.conf": f"""listeners.tcp.1 = 0.0.0.0:{RABBITMQ_PORT}
loopback_users.guest = true
load_definitions = {RABBITMQ_RUNTIME_DIR}/definitions.json
log.console = true
log.file = false
""",
        "start-rabbitmq.sh": (
            "#!/bin/sh\n"
            "set -eu\n"
            f"salt_file={RABBITMQ_RUNTIME_DIR}/salt\n"
            "umask 077\n"
            'dd if=/dev/urandom of="$salt_file" bs=4 count=1 2>/dev/null\n'
            "{\n"
            '    printf \'%s\' \'{"users":[{"name":"openstack","password_hash":"\'\n'
            '    { cat "$salt_file"; { cat "$salt_file"; '
            f"cat {RABBITMQ_PASSWORD_PATH}; }} | openssl dgst -sha256 -binary; "
            "} | base64 -w0\n"
            "    printf '%s\\n' '\",\"hashing_algorithm\":"
            '"rabbit_password_hashing_sha256","tags":[]}],"vhosts":'
            '[{"name":"/"}],"permissions":[{"user":"openstack",'
            '"vhost":"/","configure":".*","write":".*",'
            '"read":".*"}]}\'\n'
            f"}} > {RABBITMQ_RUNTIME_DIR}/definitions.json\n"
            f"chmod 0600 {RABBITMQ_RUNTIME_DIR}/definitions.json\n"
            'rm "$salt_file"\n'
            f"exec {RABBITMQ_SERVER}\n"
        ),
    }
