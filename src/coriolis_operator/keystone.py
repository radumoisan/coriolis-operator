"""Pure Keystone runtime settings and sensitive configuration rendering."""

import base64
import json
import secrets
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

KEYSTONE_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/keystone:2023.1-ubuntu-jammy"
    "@sha256:7c57962762f5e6fdb1a109097e8f3e2e5f6218ad9c09f10a585adb67ed245cf0"
)
KEYSTONE_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
KEYSTONE_PORT = 5000
KEYSTONE_REPLICAS = 1
KEYSTONE_RUN_AS_ID = 42425
KEYSTONE_SUPPLEMENTAL_GROUP = 42400
KEYSTONE_TERMINATION_GRACE_PERIOD_SECONDS = 30
KEYSTONE_CONFIG_DIR = "/etc/keystone"
KEYSTONE_SECRET_DIR = "/etc/keystone-secret"
KEYSTONE_RUNTIME_CONFIG_DIR = "/etc/keystone/runtime"
KEYSTONE_FERNET_KEYS_DIR = "/etc/keystone/fernet-keys"
KEYSTONE_CREDENTIAL_KEYS_DIR = "/etc/keystone/credential-keys"
KEYSTONE_CONFIG_PATH = f"{KEYSTONE_RUNTIME_CONFIG_DIR}/keystone.conf"
KEYSTONE_AUTH_REQUEST_PATH = f"{KEYSTONE_RUNTIME_CONFIG_DIR}/auth-request.json"
KEYSTONE_BOOTSTRAP_PATH = f"{KEYSTONE_RUNTIME_CONFIG_DIR}/bootstrap.py"
KEYSTONE_ADMIN_PASSWORD_PATH = f"{KEYSTONE_RUNTIME_CONFIG_DIR}/admin-password"
KEYSTONE_CONFIG_KEYS = frozenset({"bootstrap.py"})
KEYSTONE_SECRET_CONFIG_KEYS = frozenset({"keystone.conf", "auth-request.json"})
KEYSTONE_KEY_KEYS = frozenset({"0", "1"})

_INVALID_CREDENTIALS_MESSAGE = "invalid sensitive Keystone configuration input"
_KEY_GENERATION_FAILURE_MESSAGE = "Keystone key generation failed"


@dataclass(frozen=True)
class SensitiveKeystoneCredentials:
    """Keystone database and admin values hidden from representations."""

    database_password: str = field(repr=False)
    admin_password: str = field(repr=False)


class SensitiveKeystoneConfig(Mapping[str, str]):
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
        return "SensitiveKeystoneConfig(<redacted>)"

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
    if host.strip() != host or any(character in host for character in "/:@[]?#"):
        raise _invalid_credentials()
    return host


def generate_keystone_keys(
    byte_factory: Callable[[int], bytes] = secrets.token_bytes,
) -> dict[str, str]:
    """Generate independent, exact-size URL-safe repository key values."""
    try:
        values = {key: byte_factory(32) for key in sorted(KEYSTONE_KEY_KEYS)}
        if any(
            type(value) is not bytes or len(value) != 32 for value in values.values()
        ):
            raise ValueError
        encoded = {
            key: base64.urlsafe_b64encode(value).decode("ascii")
            for key, value in values.items()
        }
        if any(len(value) != 44 or "\n" in value for value in encoded.values()):
            raise ValueError
        return encoded
    except Exception:
        raise ValueError(_KEY_GENERATION_FAILURE_MESSAGE) from None


def render_keystone_config(*, keystone_host: object) -> dict[str, str]:
    """Return the non-sensitive file-only Keystone bootstrap wrapper."""
    host = _host(keystone_host)
    endpoint = f"http://{host}:{KEYSTONE_PORT}/v3"
    return {
        "bootstrap.py": f"""from pathlib import Path
from keystone import server
from keystone.cmd.bootstrap import Bootstrapper
config = {KEYSTONE_CONFIG_PATH!r}
server.configure(config_files=[config])
bootstrapper = Bootstrapper()
password_file = Path({KEYSTONE_ADMIN_PASSWORD_PATH!r})
bootstrapper.admin_password = password_file.read_text().strip()
bootstrapper.admin_username = 'admin'
bootstrapper.project_name = 'admin'
bootstrapper.admin_role_name = 'admin'
bootstrapper.region_id = 'RegionOne'
bootstrapper.service_name = 'keystone'
bootstrapper.public_url = {endpoint!r}
bootstrapper.internal_url = {endpoint!r}
bootstrapper.admin_url = {endpoint!r}
bootstrapper.immutable_roles = False
bootstrapper.bootstrap()
"""
    }


def render_sensitive_keystone_config(
    *, database_host: object, keystone_host: object, credentials: object
) -> SensitiveKeystoneConfig:
    """Render the file-only database and probe authentication configuration."""
    if type(credentials) is not SensitiveKeystoneCredentials:
        raise _invalid_credentials()
    try:
        host = _host(database_host)
        _host(keystone_host)
        database_password = _credential(credentials.database_password)
        admin_password = _credential(credentials.admin_password)
        return SensitiveKeystoneConfig(
            {
                "keystone.conf": (
                    "[DEFAULT]\nuse_stderr = true\nuse_syslog = false\n"
                    "[database]\nconnection = mysql+pymysql://keystone:"
                    f"{quote(database_password, safe='')}@{host}:3306/keystone\n"
                    "[token]\nprovider = fernet\n"
                    f"[fernet_tokens]\nkey_repository = {KEYSTONE_FERNET_KEYS_DIR}\n"
                    f"[fernet_receipts]\nkey_repository = {KEYSTONE_FERNET_KEYS_DIR}\n"
                    "[credential]\nprovider = fernet\n"
                    f"key_repository = {KEYSTONE_CREDENTIAL_KEYS_DIR}\n"
                    "[cache]\nenabled = false\n"
                ),
                "auth-request.json": json.dumps(
                    {
                        "auth": {
                            "identity": {
                                "methods": ["password"],
                                "password": {
                                    "user": {
                                        "name": "admin",
                                        "domain": {"name": "Default"},
                                        "password": admin_password,
                                    }
                                },
                            },
                            "scope": {
                                "project": {
                                    "name": "admin",
                                    "domain": {"name": "Default"},
                                }
                            },
                        }
                    }
                ),
            }
        )
    except ValueError:
        raise _invalid_credentials() from None
    except Exception:
        raise _invalid_credentials() from None
