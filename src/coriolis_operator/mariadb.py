"""Pure MariaDB runtime settings validation and configuration rendering."""

import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from kubernetes.utils.quantity import parse_quantity  # type: ignore[import-untyped]

MARIADB_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/mariadb-server:2023.1-ubuntu-jammy"
    "@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e"
)
MARIADB_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
MARIADB_REPLICAS = 1
MARIADB_RUN_AS_ID = 42434
MARIADB_SUPPLEMENTAL_GROUP = 42400
MARIADB_PVC_ACCESS_MODE = "ReadWriteOnce"
MARIADB_PVC_VOLUME_MODE = "Filesystem"
MARIADB_PVC_RETENTION_ANNOTATION = "coriolis.cloudbase.it/retention"
MARIADB_PVC_RETENTION_VALUE = "mariadb-data"
MARIADB_TERMINATION_GRACE_PERIOD_SECONDS = 30
MARIADB_BOOTSTRAP_SCHEMA_ANNOTATION = "coriolis.cloudbase.it/mariadb-bootstrap-schema"
MARIADB_BOOTSTRAP_SCHEMA_VALUE = "keystone-v1"
MARIADB_DATA_DIR = "/var/lib/mysql"
MARIADB_RUNTIME_DIR = "/run/mysqld"
MARIADB_SOCKET_PATH = f"{MARIADB_RUNTIME_DIR}/mariadb.sock"
MARIADB_PID_FILE = f"{MARIADB_RUNTIME_DIR}/mariadbd.pid"
MARIADB_CONFIG_DIR = "/etc/mariadb"
MARIADB_SECRET_DIR = "/etc/mariadb-secret"
MARIADB_MY_CNF_PATH = f"{MARIADB_CONFIG_DIR}/my.cnf"
MARIADB_ADMIN_CNF_PATH = f"{MARIADB_RUNTIME_DIR}/admin.cnf"
MARIADB_CORIOLIS_CNF_PATH = f"{MARIADB_RUNTIME_DIR}/coriolis.cnf"
MARIADB_BOOTSTRAP_SQL_PATH = f"{MARIADB_RUNTIME_DIR}/bootstrap.sql"
MARIADB_FIRST_INITIALIZATION_MARKER = f"{MARIADB_RUNTIME_DIR}/first-initialization"
MARIADB_BOOTSTRAP_COMPLETE_MARKER = f"{MARIADB_RUNTIME_DIR}/bootstrap-complete"
MARIADB_INSTALL_DB_COMMAND = (
    f"mariadb-install-db --datadir={MARIADB_DATA_DIR} "
    "--skip-test-db "
    "--auth-root-authentication-method=normal"
)
MARIADB_PASSWORDLESS_BOOTSTRAP_COMMAND = (
    "mariadb --socket="
    + MARIADB_SOCKET_PATH
    + " --user=root < "
    + MARIADB_BOOTSTRAP_SQL_PATH
)
MARIADB_ADMIN_BOOTSTRAP_COMMAND = (
    "mariadb --defaults-file="
    + MARIADB_ADMIN_CNF_PATH
    + " < "
    + MARIADB_BOOTSTRAP_SQL_PATH
)
MARIADB_PASSWORDLESS_QUERY_COMMAND = (
    "mariadb --socket=" + MARIADB_SOCKET_PATH + " --user=root --execute='SELECT 1'"
)
MARIADB_ADMIN_QUERY_COMMAND = (
    "mariadb --defaults-file=" + MARIADB_ADMIN_CNF_PATH + " --execute='SELECT 1'"
)
MARIADB_CONFIG_KEYS = frozenset({"my.cnf", "prepare-mariadb.sh", "start-mariadb.sh"})
MARIADB_SECRET_CONFIG_KEYS = frozenset({"admin.cnf", "coriolis.cnf", "bootstrap.sql"})

_INVALID_SETTINGS_MESSAGE = "invalid MariaDB settings"
_INVALID_CREDENTIALS_MESSAGE = "invalid sensitive MariaDB configuration input"
_RENDER_FAILURE_MESSAGE = "MariaDB configuration rendering failed"


@dataclass(frozen=True)
class MariaDBStorageSettings:
    """Validated immutable MariaDB persistent-volume settings."""

    storage_class_name: str
    size: str


@dataclass(frozen=True)
class MariaDBResourceSettings:
    """Validated immutable MariaDB resource quantity strings."""

    requests_cpu: str
    requests_memory: str
    limits_cpu: str
    limits_memory: str


@dataclass(frozen=True)
class MariaDBSettings:
    """Complete, validated MariaDB runtime settings for later manifest builders."""

    storage: MariaDBStorageSettings
    resources: MariaDBResourceSettings


@dataclass(frozen=True)
class SensitiveMariaDBCredentials:
    """Retained MariaDB credentials whose values are hidden from representations."""

    database_password: str = field(repr=False)
    coriolis_database_password: str = field(repr=False)
    keystone_database_password: str = field(repr=False)


class SensitiveMariaDBConfig(Mapping[str, str]):
    """Secret data mapping whose representation never contains credential values."""

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
        return "SensitiveMariaDBConfig(<redacted>)"

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


def resolve_mariadb_settings(*, storage: object, resources: object) -> MariaDBSettings:
    """Validate complete MariaDB CR input without mutating caller-owned mappings."""
    storage_values = _required_mapping(storage)
    resources_values = _required_mapping(resources)
    maria_storage = _required_mapping(storage_values.get("mariadb"))
    maria_resources = _required_mapping(resources_values.get("mariadb"))
    requests = _required_mapping(maria_resources.get("requests"))
    limits = _required_mapping(maria_resources.get("limits"))

    storage_class_name = _validated_storage_class_name(
        maria_storage.get("storageClassName")
    )
    size, _ = _validated_quantity(maria_storage.get("size"))
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

    return MariaDBSettings(
        storage=MariaDBStorageSettings(
            storage_class_name=storage_class_name, size=size
        ),
        resources=MariaDBResourceSettings(
            requests_cpu=requests_cpu,
            requests_memory=requests_memory,
            limits_cpu=limits_cpu,
            limits_memory=limits_memory,
        ),
    )


def _validated_credential(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise _invalid_credentials()
    return value


def _escape_option_value(value: str) -> str:
    return "".join(
        "\\\\" if character == "\\" else '\\"' if character == '"' else character
        for character in value
    )


def _escape_sql_string(value: str) -> str:
    return "".join(
        "\\\\" if character == "\\" else "\\'" if character == "'" else character
        for character in value
    )


def render_mariadb_config() -> dict[str, str]:
    """Return the fixed credential-free ConfigMap values for MariaDB."""
    return {
        "my.cnf": f"""[mariadbd]
wsrep_on=OFF
datadir={MARIADB_DATA_DIR}
socket={MARIADB_SOCKET_PATH}
pid-file={MARIADB_PID_FILE}
bind-address=0.0.0.0
port=3306
max_allowed_packet=64M
innodb_log_file_size=256M
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
default_storage_engine=InnoDB
""",
        "prepare-mariadb.sh": f"""#!/bin/sh
set -eu
for file in admin.cnf coriolis.cnf bootstrap.sql; do
    install -m 0600 {MARIADB_SECRET_DIR}/$file {MARIADB_RUNTIME_DIR}/$file
done
if [ ! -d {MARIADB_DATA_DIR}/mysql ]; then
    {MARIADB_INSTALL_DB_COMMAND}
    touch {MARIADB_FIRST_INITIALIZATION_MARKER}
fi
""",
        "start-mariadb.sh": f"""#!/bin/sh
set -eu
rm -f {MARIADB_BOOTSTRAP_COMPLETE_MARKER}
mariadbd --defaults-file={MARIADB_MY_CNF_PATH} --console &
mariadb_pid=$!
terminate() {{
    kill -TERM "$mariadb_pid" 2>/dev/null || true
    set +e
    wait "$mariadb_pid"
    exit $?
}}
trap terminate TERM
if [ -f {MARIADB_FIRST_INITIALIZATION_MARKER} ]; then
    first_initialization_uses_passwordless_root=false
    while :; do
        if {MARIADB_PASSWORDLESS_QUERY_COMMAND}; then
            first_initialization_uses_passwordless_root=true
            break
        fi
        if {MARIADB_ADMIN_QUERY_COMMAND}; then
            break
        fi
        if ! kill -0 "$mariadb_pid"; then
            wait "$mariadb_pid"
            exit $?
        fi
        sleep 1
    done
    if [ "$first_initialization_uses_passwordless_root" = true ]; then
        {MARIADB_PASSWORDLESS_BOOTSTRAP_COMMAND}
    else
        {MARIADB_ADMIN_BOOTSTRAP_COMMAND}
    fi
    rm {MARIADB_FIRST_INITIALIZATION_MARKER}
else
    until {MARIADB_ADMIN_QUERY_COMMAND}; do
        if ! kill -0 "$mariadb_pid"; then
            wait "$mariadb_pid"
            exit $?
        fi
        sleep 1
    done
    {MARIADB_ADMIN_BOOTSTRAP_COMMAND}
fi
touch {MARIADB_BOOTSTRAP_COMPLETE_MARKER}
wait "$mariadb_pid"
""",
    }


def render_sensitive_mariadb_config(
    *, credentials: SensitiveMariaDBCredentials
) -> SensitiveMariaDBConfig:
    """Return credential-redacted Secret values rendered from retained passwords."""
    if type(credentials) is not SensitiveMariaDBCredentials:
        raise _invalid_credentials()
    database_password = _validated_credential(credentials.database_password)
    coriolis_database_password = _validated_credential(
        credentials.coriolis_database_password
    )
    keystone_database_password = _validated_credential(
        credentials.keystone_database_password
    )
    try:
        return SensitiveMariaDBConfig(
            {
                "admin.cnf": (
                    '[client]\nuser=root\npassword="'
                    f'{_escape_option_value(database_password)}"\n'
                    f"socket={MARIADB_SOCKET_PATH}\n"
                ),
                "coriolis.cnf": (
                    '[client]\nuser=coriolis\npassword="'
                    f'{_escape_option_value(coriolis_database_password)}"\n'
                    "host=127.0.0.1\nport=3306\n"
                ),
                "bootstrap.sql": (
                    "ALTER USER 'root'@'localhost' IDENTIFIED BY '"
                    f"{_escape_sql_string(database_password)}';\n"
                    "CREATE DATABASE IF NOT EXISTS coriolis CHARACTER SET utf8mb4 "
                    "COLLATE utf8mb4_unicode_ci;\n"
                    "CREATE USER IF NOT EXISTS 'coriolis'@'%' IDENTIFIED BY '"
                    f"{_escape_sql_string(coriolis_database_password)}';\n"
                    "ALTER USER 'coriolis'@'%' IDENTIFIED BY '"
                    f"{_escape_sql_string(coriolis_database_password)}';\n"
                    "GRANT ALL PRIVILEGES ON coriolis.* TO 'coriolis'@'%';\n"
                    "CREATE DATABASE IF NOT EXISTS keystone CHARACTER SET utf8mb4 "
                    "COLLATE utf8mb4_unicode_ci;\n"
                    "CREATE USER IF NOT EXISTS 'keystone'@'%' IDENTIFIED BY '"
                    f"{_escape_sql_string(keystone_database_password)}';\n"
                    "ALTER USER 'keystone'@'%' IDENTIFIED BY '"
                    f"{_escape_sql_string(keystone_database_password)}';\n"
                    "GRANT ALL PRIVILEGES ON keystone.* TO 'keystone'@'%';\n"
                    "FLUSH PRIVILEGES;\n"
                ),
            }
        )
    except Exception:
        raise ValueError(_RENDER_FAILURE_MESSAGE) from None
