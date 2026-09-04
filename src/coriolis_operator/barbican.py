"""Pure Barbican runtime settings and configuration rendering."""

import base64
import hashlib
import re
import secrets
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

BARBICAN_API_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/barbican-api:2023.1-ubuntu-jammy"
    "@sha256:a142a57761f708b241358383d6445ac5da4e05ae26a284369081cfb15cca8a60"
)
BARBICAN_WORKER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/barbican-worker:2023.1-ubuntu-jammy"
    "@sha256:ed907de778900b08f2645c9eeb82d48d8202ce6517cdb543d42db2e88ea642b5"
)
BARBICAN_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
BARBICAN_PORT = 9311
BARBICAN_REPLICAS = 1
BARBICAN_RUN_AS_ID = 42403
BARBICAN_SUPPLEMENTAL_GROUP = 42400
BARBICAN_TERMINATION_GRACE_PERIOD_SECONDS = 30
BARBICAN_RUNTIME_DIR = "/etc/barbican-runtime"
BARBICAN_TMP_DIR = "/tmp"
BARBICAN_API_STATE_DIR = "/var/lib/barbican"
BARBICAN_VASSALS_DIR = f"{BARBICAN_RUNTIME_DIR}/vassals"
BARBICAN_PASTE_PATH = f"{BARBICAN_RUNTIME_DIR}/barbican-api-paste.ini"
BARBICAN_VASSAL_PATH = f"{BARBICAN_VASSALS_DIR}/barbican-api.ini"
BARBICAN_POLICY_PATH = f"{BARBICAN_RUNTIME_DIR}/policy.yaml"
BARBICAN_DB_SYNC_PATH = f"{BARBICAN_RUNTIME_DIR}/db-sync.py"
BARBICAN_CONFIG_PATH = f"{BARBICAN_RUNTIME_DIR}/barbican.conf"
BARBICAN_HEALTHCHECK_DISABLE_PATH = f"{BARBICAN_API_STATE_DIR}/healthcheck_disable"
BARBICAN_VENV_PYTHON = "/var/lib/kolla/venv/bin/python3"
BARBICAN_API_COMMAND = (
    "/usr/bin/dumb-init",
    "--single-child",
    "--",
    "/var/lib/kolla/venv/bin/uwsgi",
    "--master",
    "--emperor",
    BARBICAN_VASSALS_DIR,
)
BARBICAN_WORKER_COMMAND = (
    "/usr/bin/dumb-init",
    "--single-child",
    "--",
    "/var/lib/kolla/venv/bin/barbican-worker",
    "--config-file",
    BARBICAN_CONFIG_PATH,
    "--nouse-syslog",
    "--log-dir=",
)
BARBICAN_DB_SYNC_COMMAND = (
    BARBICAN_VENV_PYTHON,
    BARBICAN_DB_SYNC_PATH,
)
BARBICAN_CONFIG_KEYS = frozenset(
    {
        "barbican-api-paste.ini",
        "barbican-api.ini",
        "policy.yaml",
        "db-sync.py",
    }
)
BARBICAN_SECRET_CONFIG_KEYS = frozenset({"barbican.conf"})
BARBICAN_HEALTH_PROBE = (
    "import http.client,sys; "
    "connection=http.client.HTTPConnection('127.0.0.1',9311,timeout=5); "
    "connection.request('GET','/healthcheck'); "
    "response=connection.getresponse(); "
    "sys.exit(0 if response.status == 200 else 1)"
)

_MICROVERSION_FILTER_FACTORY = (
    "barbican.api.middleware.microversion:MicroversionMiddleware.factory"
)
_CRYPTO_KEY_BYTES = 32
_CRYPTO_KEY_CHARACTERS = 44
_INVALID_CREDENTIALS_MESSAGE = "invalid sensitive Barbican configuration input"
_CRYPTO_KEY_GENERATION_FAILURE_MESSAGE = "Barbican crypto key generation failed"
_BUILTIN_DEFAULT_KEK_SHA256 = (
    "6c07ac6088d9daaab4cd867ae943c64042df0defda6c5758215a65cf1411dac0"
)
_DNS_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _is_builtin_default_kek(key: str) -> bool:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return digest == _BUILTIN_DEFAULT_KEK_SHA256


@dataclass(frozen=True)
class SensitiveBarbicanCredentials:
    """Retained Barbican values hidden from representations."""

    database_password: str = field(repr=False)
    keystone_password: str = field(repr=False)
    rabbitmq_password: str = field(repr=False)
    crypto_key: str = field(repr=False)


class SensitiveBarbicanConfig(Mapping[str, str]):
    """Secret values mapping whose representation is always redacted."""

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
        return "SensitiveBarbicanConfig(<redacted>)"

    __str__ = __repr__


def _invalid_credentials() -> ValueError:
    return ValueError(_INVALID_CREDENTIALS_MESSAGE)


def _credential(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise _invalid_credentials()
    return value


def _host(value: object) -> str:
    host = _credential(value)
    if not _DNS_LABEL_PATTERN.fullmatch(host):
        raise _invalid_credentials()
    return host


def _crypto_key(value: object) -> str:
    key = _credential(value)
    if len(key) != _CRYPTO_KEY_CHARACTERS or _is_builtin_default_kek(key):
        raise _invalid_credentials()
    try:
        raw = base64.urlsafe_b64decode(key)
    except Exception:
        raise _invalid_credentials() from None
    if (
        len(raw) != _CRYPTO_KEY_BYTES
        or base64.urlsafe_b64encode(raw).decode("ascii") != key
    ):
        raise _invalid_credentials()
    return key


def _ini_credential(value: object) -> str:
    candidate = _credential(value)
    if candidate != candidate.strip() or candidate[0] in {"#", ";"}:
        raise _invalid_credentials()
    return candidate


def validate_retained_barbican_values(
    *,
    database_password: object,
    keystone_password: object,
    crypto_key: object,
) -> None:
    """Semantically validate retained Barbican values without exposing them.

    Rejects INI-unsafe credentials (leading/trailing whitespace or an
    INI-comment-leading ``#``/``;``) and structurally invalid crypto keys.
    Generated URL-safe values remain valid. Raises only the constant
    message; no value is returned or included in any error.
    """
    try:
        _ini_credential(database_password)
        _ini_credential(keystone_password)
        _ini_credential(_crypto_key(crypto_key))
    except ValueError:
        raise _invalid_credentials() from None


def generate_barbican_crypto_key(
    byte_factory: Callable[[int], bytes] = secrets.token_bytes,
) -> str:
    """Generate one exact-size URL-safe base64 simple_crypto master kek."""
    try:
        raw = byte_factory(_CRYPTO_KEY_BYTES)
        if type(raw) is not bytes or len(raw) != _CRYPTO_KEY_BYTES:
            raise ValueError
        encoded = base64.urlsafe_b64encode(raw).decode("ascii")
        if (
            len(encoded) != _CRYPTO_KEY_CHARACTERS
            or "\n" in encoded
            or _is_builtin_default_kek(encoded)
        ):
            raise ValueError
        return encoded
    except Exception:
        raise ValueError(_CRYPTO_KEY_GENERATION_FAILURE_MESSAGE) from None


def render_barbican_config() -> dict[str, str]:
    """Return the deterministic credential-free Barbican ConfigMap assets."""
    paste = f"""[composite:main]
use = egg:Paste#urlmap
/: barbican_version
/healthcheck: healthcheck
/v1: barbican-api-keystone

[pipeline:barbican_version]
pipeline = cors http_proxy_to_wsgi microversion versionapp

[pipeline:barbican-api-keystone]
pipeline = cors http_proxy_to_wsgi authtoken context microversion apiapp

[app:apiapp]
paste.app_factory = barbican.api.app:create_main_app

[app:versionapp]
paste.app_factory = barbican.api.app:create_version_app

[filter:cors]
paste.filter_factory = oslo_middleware.cors:filter_factory
oslo_config_project = barbican

[filter:http_proxy_to_wsgi]
paste.filter_factory = oslo_middleware:HTTPProxyToWSGI.factory

[filter:authtoken]
paste.filter_factory = keystonemiddleware.auth_token:filter_factory

[filter:context]
paste.filter_factory = barbican.api.middleware.context:ContextMiddleware.factory

[filter:microversion]
paste.filter_factory = {_MICROVERSION_FILTER_FACTORY}

[app:healthcheck]
paste.app_factory = oslo_middleware:Healthcheck.app_factory
backends = disable_by_file
disable_by_file_path = {BARBICAN_HEALTHCHECK_DISABLE_PATH}
"""
    vassal = f"""[uwsgi]
socket = :{BARBICAN_PORT}
protocol = http
processes = {BARBICAN_REPLICAS}
lazy = true
vacuum = true
no-default-app = true
memory-report = true
plugins = python
paste = config:{BARBICAN_PASTE_PATH}
pyargv = --config-file={BARBICAN_CONFIG_PATH}
add-header = Connection: close
"""
    policy = """\"creator\": \"role:admin or role:member\"
\"observer\": \"role:admin or role:reader\"
"""
    db_sync = f"""from barbican.common import config
from barbican.model.migration import commands

conf = config.new_config()
config.parse_args(
    conf,
    args=[],
    default_config_files=[{BARBICAN_CONFIG_PATH!r}],
)
commands.upgrade(to_version='head', sql_url=conf.sql_connection)
"""
    return {
        "barbican-api-paste.ini": paste,
        "barbican-api.ini": vassal,
        "policy.yaml": policy,
        "db-sync.py": db_sync,
    }


def render_sensitive_barbican_config(
    *,
    database_host: object,
    rabbitmq_host: object,
    keystone_host: object,
    barbican_host: object,
    credentials: object,
) -> SensitiveBarbicanConfig:
    """Render the file-only Barbican configuration with retained secrets."""
    if type(credentials) is not SensitiveBarbicanCredentials:
        raise _invalid_credentials()
    try:
        database = _host(database_host)
        rabbitmq = _host(rabbitmq_host)
        keystone = _host(keystone_host)
        barbican = _host(barbican_host)
        database_password = _credential(credentials.database_password)
        keystone_password = _credential(credentials.keystone_password)
        rabbitmq_password = _credential(credentials.rabbitmq_password)
        crypto_key = _crypto_key(credentials.crypto_key)
        sql_connection = (
            "mysql+pymysql://barbican:"
            f"{quote(database_password, safe='')}@{database}:3306/barbican"
        )
        rabbitmq_credentials = quote(rabbitmq_password, safe="")
        transport_url = f"rabbit://openstack:{rabbitmq_credentials}@{rabbitmq}:5672/"
        keystone_url = f"http://{keystone}:5000/v3"
        return SensitiveBarbicanConfig(
            {
                "barbican.conf": f"""[DEFAULT]
use_stderr = true
use_syslog = false
host_href = http://{barbican}:{BARBICAN_PORT}
db_auto_create = False
sql_connection = {sql_connection}
transport_url = {transport_url}

[secretstore]
namespace = barbican.secretstore.plugin
enabled_secretstore_plugins = store_crypto

[crypto]
namespace = barbican.crypto.plugin
enabled_crypto_plugins = simple_crypto

[simple_crypto_plugin]
kek = {crypto_key}

[keystone_notifications]
enable = False

[keystone_authtoken]
service_type = key-manager
www_authenticate_uri = {keystone_url}
auth_url = {keystone_url}
auth_type = password
username = barbican
password = {keystone_password}
project_name = service
project_domain_name = Default
user_domain_name = Default
region_name = RegionOne

[oslo_messaging_notifications]
driver = noop

[oslo_messaging_rabbit]
heartbeat_in_pthread = false

[oslo_middleware]
enable_proxy_headers_parsing = True

[oslo_policy]
policy_file = {BARBICAN_POLICY_PATH}
"""
            }
        )
    except ValueError:
        raise _invalid_credentials() from None
    except Exception:
        raise _invalid_credentials() from None
