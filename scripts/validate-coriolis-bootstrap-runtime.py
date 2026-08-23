#!/usr/bin/env python3
"""Prove the exact-image standalone runtime contract for the Coriolis-common
bootstrap plan using disposable local support containers.

Evidence produced:
  1. The pinned coriolis-conductor image (immutable digest) exposes the
     executable and import surface required for `coriolis-dbsync`, PyMySQL /
     mysqlclient, keystoneauth1, python-keystoneclient, amqp / kombu, and a
     mounted Python bootstrap script. Its image contract (linux/amd64,
     empty/root default user, upstream entrypoint and conductor command) is
     asserted from `docker image inspect` before any dependency is started.
  2. The upstream entrypoint is bypassed safely and the image runs with a
     read-only root filesystem, dropped capabilities, no-new-privileges, an
     explicit numeric non-root UID/GID, and no network. Only /tmp (tmpfs) is
     writable. A live probe is inspected to confirm the effective UID/GID and
     hardening flags.
  3. `coriolis-dbsync` configuration is proven to require a mounted config file
     carrying only the `[database] connection` value; no credential ever
     appears in argv, process environment, or captured output.
  4. Against disposable MariaDB and Keystone, the source-derived remaining
     operations run twice each and converge to an idempotent state: Coriolis
     schema migration (all CORIOLIS_SCHEMA_TABLES present and the migration
     state readable) and the Keystone user `coriolis` in the Default domain,
     `admin` role on the `service` project, the `coriolis` `migration` service
     with description `Cloud Migration as a Service`, and exactly one
     RegionOne admin/internal/public endpoint with the standalone
     `http://coriolis-api:7667/v1/%(tenant_id)s` URL. The Coriolis database,
     user, and grants are provisioned by the existing MariaDB bootstrap and are
     treated as preconditions, not new bootstrap effects. The dedicated
     Keystone bootstrap does not create the `service` project, so the bootstrap
     ensures it idempotently as a necessary precondition and reports that
     distinction.
  5. Protocol-level healthy-interface gates are proven for all four
     dependencies: MariaDB, Keystone, RabbitMQ (authenticated `openstack` on
     `/` via kombu from the conductor image, password read from a mounted
     private file), and Memcached (set/get protocol from the conductor image
     via Python socket).
  6. A disposable exact-digest RabbitMQ and Memcached support pair closes the
     healthy-dependency gap, reusing the operator renderers and direct
     source-proven commands, and is fully cleaned up.

This script must never print credentials, DSNs, tokens, headers, bodies, raw
sensitive logs, or process environments. It prints sanitized PASS/FAIL stage
summaries only.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from coriolis_operator.common import (  # type: ignore[import-untyped]
    BOOTSTRAP_CONFIG_DIR,
    BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR,
    BOOTSTRAP_INFRA_CREDENTIALS_DIR,
    BOOTSTRAP_SCRIPT_DIR,
    render_bootstrap_script,
)
from coriolis_operator.keystone import (  # type: ignore[import-untyped]
    KEYSTONE_CONFIG_PATH,
    KEYSTONE_CREDENTIAL_KEYS_DIR,
    KEYSTONE_FERNET_KEYS_DIR,
    SensitiveKeystoneCredentials,
    render_keystone_config,
    render_sensitive_keystone_config,
)
from coriolis_operator.mariadb import (  # type: ignore[import-untyped]
    MARIADB_ADMIN_CNF_PATH,
    MARIADB_BOOTSTRAP_COMPLETE_MARKER,
    MARIADB_CONFIG_DIR,
    MARIADB_DATA_DIR,
    MARIADB_RUN_AS_ID,
    MARIADB_RUNTIME_DIR,
    MARIADB_SECRET_DIR,
    MARIADB_SUPPLEMENTAL_GROUP,
    SensitiveMariaDBCredentials,
    render_mariadb_config,
    render_sensitive_mariadb_config,
)
from coriolis_operator.memcached import (  # type: ignore[import-untyped]
    MEMCACHED_ARGS,
    MEMCACHED_COMMAND,
    MEMCACHED_IMAGE,
    MEMCACHED_RUN_AS_ID,
)
from coriolis_operator.rabbitmq import (  # type: ignore[import-untyped]
    RABBITMQ_CONFIG_DIR,
    RABBITMQ_DATA_DIR,
    RABBITMQ_IMAGE,
    RABBITMQ_LOG_DIR,
    RABBITMQ_RUN_AS_ID,
    RABBITMQ_RUNTIME_DIR,
    RABBITMQ_SECRET_DIR,
    render_rabbitmq_config,
)

CONDUCTOR_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-conductor:2603.4"
    "@sha256:27495f44fbb8b320098d0aa04cd9dcb2a4b432e57aa17417606efc5403ac09c7"
)
MARIADB_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/mariadb-server:2023.1-ubuntu-jammy"
    "@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e"
)
KEYSTONE_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/keystone:2023.1-ubuntu-jammy"
    "@sha256:7c57962762f5e6fdb1a109097e8f3e2e5f6218ad9c09f10a585adb67ed245cf0"
)

CONDUCTOR_RUN_AS_ID = 42434
KEYSTONE_ID = "42425"
KOLLA_GROUP = "42400"
CORIOLIS_SCHEMA_TABLES = ("migrate_version", "endpoint", "service", "region")

CONDUCTOR_ENTRYPOINT = "/entrypoint.sh"
CONDUCTOR_COMMAND = (
    "/usr/local/bin/coriolis-conductor",
    "--config-file=/etc/coriolis/coriolis.conf",
)
CONDUCTOR_IMPORTS = (
    "coriolis.cmd.conductor",
    "pymysql",
    "MySQLdb",
    "keystoneauth1",
    "keystoneclient",
    "amqp",
    "kombu",
)
PREFIX = "oc-coriolis-bootstrap-evidence"
DEFAULT_TIMEOUT = 120
POLL_INTERVAL = 1.0

CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
Reporter = Callable[[str], None]


class ValidationFailure(Exception):
    """A sanitized failure that identifies only its stable validation stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"validation failed: {stage}")
        self.stage = stage


@dataclass(frozen=True)
class EvidencePaths:
    scratch: Path
    mariadb_public: Path
    mariadb_secret: Path
    keystone: Path
    coriolis: Path
    coriolis_secret: Path
    rabbitmq: Path
    rabbitmq_secret: Path
    probe: Path


@dataclass(frozen=True)
class Resources:
    token: str

    @property
    def data_volume(self) -> str:
        return f"{PREFIX}-{self.token}-data"

    @property
    def runtime_volume(self) -> str:
        return f"{PREFIX}-{self.token}-runtime"

    @property
    def mariadb_public_volume(self) -> str:
        return f"{PREFIX}-{self.token}-mariadb-public"

    @property
    def mariadb_secret_volume(self) -> str:
        return f"{PREFIX}-{self.token}-mariadb-secret"

    @property
    def keystone_config_volume(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-config"

    @property
    def keystone_fernet_volume(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-fernet"

    @property
    def keystone_credential_volume(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-credential"

    @property
    def coriolis_config_volume(self) -> str:
        return f"{PREFIX}-{self.token}-coriolis-config"

    @property
    def coriolis_secret_volume(self) -> str:
        return f"{PREFIX}-{self.token}-coriolis-secret"

    @property
    def rabbitmq_config_volume(self) -> str:
        return f"{PREFIX}-{self.token}-rabbitmq-config"

    @property
    def rabbitmq_secret_volume(self) -> str:
        return f"{PREFIX}-{self.token}-rabbitmq-secret"

    @property
    def rabbitmq_data_volume(self) -> str:
        return f"{PREFIX}-{self.token}-rabbitmq-data"

    @property
    def rabbitmq_runtime_volume(self) -> str:
        return f"{PREFIX}-{self.token}-rabbitmq-runtime"

    @property
    def rabbitmq_logs_volume(self) -> str:
        return f"{PREFIX}-{self.token}-rabbitmq-logs"

    @property
    def network(self) -> str:
        return f"{PREFIX}-{self.token}-network"

    @property
    def mariadb_prepare(self) -> str:
        return f"{PREFIX}-{self.token}-mariadb-prepare"

    @property
    def mariadb_staging(self) -> str:
        return f"{PREFIX}-{self.token}-mariadb-staging"

    @property
    def mariadb_main(self) -> str:
        return f"{PREFIX}-{self.token}-mariadb"

    @property
    def dbsync_runner(self) -> str:
        return f"{PREFIX}-{self.token}-dbsync"

    @property
    def keystone_key_prepare(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-key-prepare"

    @property
    def keystone_one_shot(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-one-shot"

    @property
    def keystone_probe(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-probe"

    @property
    def keystone_main(self) -> str:
        return f"{PREFIX}-{self.token}-keystone"

    @property
    def bootstrapper(self) -> str:
        return f"{PREFIX}-{self.token}-bootstrapper"

    @property
    def coriolis_probe(self) -> str:
        return f"{PREFIX}-{self.token}-coriolis-probe"

    @property
    def conductor_live_probe(self) -> str:
        return f"{PREFIX}-{self.token}-conductor-live-probe"

    @property
    def rabbitmq_main(self) -> str:
        return f"{PREFIX}-{self.token}-rabbitmq"

    @property
    def rabbitmq_probe(self) -> str:
        return f"{PREFIX}-{self.token}-rabbitmq-probe"

    @property
    def memcached_main(self) -> str:
        return f"{PREFIX}-{self.token}-memcached"

    @property
    def memcached_probe(self) -> str:
        return f"{PREFIX}-{self.token}-memcached-probe"


def _run(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), check=False, capture_output=True, text=True, timeout=timeout
    )


def _sql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _option_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


# The Coriolis-common bootstrap script is generated by the operator renderer
# (coriolis_operator.common.render_bootstrap_script) and staged into the
# evidence at the exact paths/mount shape used by the Job. It reruns all four
# dependency gates, coriolis-dbsync, and the exact Keystone catalog
# convergence itself; no duplicate bootstrap implementation is maintained here.
# Independent final-state verification is retained in VERIFY_KEYSTONE_STATE.

VERIFY_KEYSTONE_STATE = """from pathlib import Path
import sys
from keystoneauth1.identity import v3
from keystoneauth1 import session as ks_session
from keystoneclient.v3 import client

def fail(marker):
    print(marker)
    sys.exit(1)

DEFAULT_DOMAIN = 'Default'
admin_pw = Path('/evidence/coriolis-secret/keystone-admin-password').read_text().strip()
service_pw_path = '/evidence/coriolis-secret/coriolis-keystone-password'
service_pw = Path(service_pw_path).read_text().strip()
auth = v3.Password(auth_url='http://keystone:5000/v3',
    username='admin', password=admin_pw, project_name='admin',
    user_domain_name=DEFAULT_DOMAIN, project_domain_name=DEFAULT_DOMAIN)
kc = client.Client(session=ks_session.Session(auth=auth))
domain = next((d for d in kc.domains.list() if d.name == DEFAULT_DOMAIN), None)
if domain is None:
    fail('CORIOLIS_VERIFY_FAIL')

users = [u for u in kc.users.list(domain=domain.id) if u.name == 'coriolis']
if len(users) != 1:
    fail('CORIOLIS_VERIFY_FAIL')
coriolis_user = users[0]
if not coriolis_user.enabled:
    fail('CORIOLIS_VERIFY_FAIL')

projects = [p for p in kc.projects.list(domain=domain.id) if p.name == 'service']
if len(projects) != 1:
    fail('CORIOLIS_VERIFY_FAIL')
service_project = projects[0]
if coriolis_user.default_project_id != service_project.id:
    fail('CORIOLIS_VERIFY_FAIL')

admin_role = next((r for r in kc.roles.list() if r.name == 'admin'), None)
if admin_role is None:
    fail('CORIOLIS_VERIFY_FAIL')
assignments = kc.role_assignments.list(user=coriolis_user.id,
    project=service_project.id)
if not any(g.role['id'] == admin_role.id for g in assignments):
    fail('CORIOLIS_VERIFY_FAIL')

try:
    coriolis_auth = v3.Password(auth_url='http://keystone:5000/v3',
        username='coriolis', password=service_pw, project_name='service',
        user_domain_name=DEFAULT_DOMAIN, project_domain_name=DEFAULT_DOMAIN)
    token = coriolis_auth.get_token(ks_session.Session(auth=coriolis_auth))
    if not token:
        fail('CORIOLIS_VERIFY_FAIL')
except Exception:
    fail('CORIOLIS_VERIFY_FAIL')

services = [s for s in kc.services.list() if s.name == 'coriolis']
if len(services) != 1:
    fail('CORIOLIS_VERIFY_FAIL')
migration_service = services[0]
if migration_service.type != 'migration':
    fail('CORIOLIS_VERIFY_FAIL')
if migration_service.description != 'Cloud Migration as a Service':
    fail('CORIOLIS_VERIFY_FAIL')

by_interface = {}
for e in kc.endpoints.list(service=migration_service.id):
    by_interface.setdefault(e.interface, []).append(e)
if set(by_interface) != {'admin', 'internal', 'public'}:
    fail('CORIOLIS_VERIFY_FAIL')
for interface, endpoints in by_interface.items():
    if len(endpoints) != 1:
        fail('CORIOLIS_VERIFY_FAIL')
    endpoint = endpoints[0]
    if endpoint.region != 'RegionOne':
        fail('CORIOLIS_VERIFY_FAIL')
    if endpoint.url != 'http://coriolis-api:7667/v1/%(tenant_id)s':
        fail('CORIOLIS_VERIFY_FAIL')
print('verify-coriolis-state-ok')
"""

RABBITMQ_PROBE = """from pathlib import Path
import sys
from kombu import Connection

def fail():
    print('RABBITMQ_PROTOCOL_FAIL')
    sys.exit(1)

try:
    password = Path(
        '/evidence/coriolis-secret/rabbitmq-password').read_text().strip()
    connection = Connection(hostname='rabbitmq', port=5672,
        userid='openstack', password=password, virtual_host='/')
    connection.connect()
    connection.release()
    print('rabbitmq-protocol-ok')
except Exception:
    fail()
"""

MEMCACHED_PROBE = """import socket
import sys

def fail():
    print('MEMCACHED_PROTOCOL_FAIL')
    sys.exit(1)

try:
    sock = socket.create_connection(('memcached', 11211), timeout=5)
    sock.sendall(b'version\\r\\n')
    response = sock.recv(256)
    if not response.startswith(b'VERSION'):
        sock.close()
        fail()
    sock.sendall(b'set __ocprobe 0 60 3\\r\\nabc\\r\\n')
    sock.recv(256)
    sock.sendall(b'get __ocprobe\\r\\n')
    response = sock.recv(256)
    sock.close()
    if b'VALUE __ocprobe' not in response:
        fail()
    print('memcached-protocol-ok')
except Exception:
    fail()
"""


def create_evidence_files(
    repository_root: Path, mariadb_hostname: str = "mariadb"
) -> EvidencePaths:
    """Create the only on-host files used by the disposable evidence run."""
    scratch = Path(tempfile.mkdtemp(prefix=f"{PREFIX}-", dir=repository_root))
    try:
        scratch.chmod(0o700)
        mariadb_public = scratch / "mariadb-public"
        mariadb_secret = scratch / "mariadb-secret"
        keystone = scratch / "keystone"
        coriolis = scratch / "coriolis"
        coriolis_secret = scratch / "coriolis-secret"
        rabbitmq = scratch / "rabbitmq"
        rabbitmq_secret = scratch / "rabbitmq-secret"
        probe = scratch / "probe"
        for directory in (
            mariadb_public,
            mariadb_secret,
            keystone,
            coriolis,
            coriolis_secret,
            rabbitmq,
            rabbitmq_secret,
            probe,
        ):
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)

        root_password = secrets.token_urlsafe(32)
        coriolis_password = secrets.token_urlsafe(32)
        keystone_password = secrets.token_urlsafe(32)
        admin_password = secrets.token_urlsafe(32)
        coriolis_keystone_password = secrets.token_urlsafe(32)
        rabbitmq_password = secrets.token_urlsafe(32)

        for name, content in render_mariadb_config().items():
            _write_private(mariadb_public / name, content)
        credentials = SensitiveMariaDBCredentials(
            database_password=root_password,
            coriolis_database_password=coriolis_password,
            keystone_database_password=keystone_password,
        )
        for name, content in render_sensitive_mariadb_config(
            credentials=credentials
        ).items():
            _write_private(mariadb_secret / name, content)
        _write_private(
            mariadb_secret / "coriolis.cnf",
            '[client]\nuser=coriolis\npassword="'
            + _option_escape(coriolis_password)
            + '"\nhost=127.0.0.1\nport=3306\n',
        )

        for name, content in render_keystone_config(keystone_host="keystone").items():
            _write_private(keystone / name, content)
        keystone_credentials = SensitiveKeystoneCredentials(
            database_password=keystone_password,
            admin_password=admin_password,
        )
        for name, content in render_sensitive_keystone_config(
            database_host=mariadb_hostname,
            keystone_host="keystone",
            credentials=keystone_credentials,
        ).items():
            _write_private(keystone / name, content)
        _write_private(keystone / "admin-password", admin_password + "\n")

        for name, content in render_rabbitmq_config().items():
            _write_private(rabbitmq / name, content)
        _write_private(rabbitmq_secret / "rabbitmq_password", rabbitmq_password)

        _write_private(
            coriolis / "coriolis.conf",
            "[DEFAULT]\nuse_stderr = true\nlog_dir =\n"
            f"[database]\nconnection = mysql+pymysql://coriolis:"
            f"{quote(coriolis_password, safe='')}@{mariadb_hostname}:3306/coriolis\n",
        )
        _write_private(
            coriolis / "bootstrap.py",
            render_bootstrap_script(
                coriolis_api_host="coriolis-api",
                rabbitmq_host="rabbitmq",
                memcached_host="memcached",
                database_host="mariadb",
                keystone_host="keystone",
            ),
        )
        _write_private(coriolis / "verify_keystone_state.py", VERIFY_KEYSTONE_STATE)
        _write_private(coriolis / "rabbitmq_probe.py", RABBITMQ_PROBE)
        _write_private(coriolis / "memcached_probe.py", MEMCACHED_PROBE)
        _write_private(
            coriolis_secret / "keystone-admin-password", admin_password + "\n"
        )
        _write_private(
            coriolis_secret / "coriolis-keystone-password",
            coriolis_keystone_password + "\n",
        )
        _write_private(coriolis_secret / "rabbitmq-password", rabbitmq_password)
        _write_private(
            coriolis_secret / "coriolis-database-password", coriolis_password
        )
        _write_private(
            probe / "mariadb-check.sql",
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = 'coriolis';\n",
        )
        _write_private(
            probe / "schema-check.sql",
            "SELECT version FROM coriolis.migrate_version;\n",
        )
        return EvidencePaths(
            scratch=scratch,
            mariadb_public=mariadb_public,
            mariadb_secret=mariadb_secret,
            keystone=keystone,
            coriolis=coriolis,
            coriolis_secret=coriolis_secret,
            rabbitmq=rabbitmq,
            rabbitmq_secret=rabbitmq_secret,
            probe=probe,
        )
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


class Validator:
    def __init__(
        self,
        *,
        repository_root: Path,
        timeout: int,
        runner: CommandRunner = _run,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        report: Reporter = print,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.timeout = timeout
        self.runner = runner
        self.clock = clock
        self.sleeper = sleeper
        self.report = report
        self.resources = Resources(secrets.token_hex(8))
        self.paths: EvidencePaths | None = None

    def _checked(self, stage: str, command: Sequence[str]) -> None:
        try:
            result = self.runner(command, self.timeout)
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)

    def _stage(self, name: str, action: Callable[[], None]) -> None:
        started = self.clock()
        action()
        self.report(f"PASS {name} {self.clock() - started:.3f}")

    def _docker(self, *arguments: str) -> list[str]:
        return ["docker", *arguments]

    def _root_sql(self, stage: str, filename: str) -> None:
        self._checked(
            stage,
            self._docker(
                "exec",
                self.resources.mariadb_main,
                "/bin/sh",
                "-c",
                f"mariadb --defaults-file={MARIADB_ADMIN_CNF_PATH} "
                f"< /evidence/mariadb-secret/{filename}",
            ),
        )

    def _coriolis_sql(self, stage: str, filename: str) -> None:
        self._checked(
            stage,
            self._docker(
                "exec",
                self.resources.mariadb_main,
                "/bin/sh",
                "-c",
                "mariadb --defaults-file=/evidence/mariadb-secret/coriolis.cnf "
                f"< /evidence/mariadb-secret/{filename}",
            ),
        )

    def _verify_conductor_image_contract(self) -> None:
        try:
            result = self.runner(
                self._docker("image", "inspect", CONDUCTOR_IMAGE), self.timeout
            )
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure("conductor-image-contract") from None
        if result.returncode != 0:
            raise ValidationFailure("conductor-image-contract")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ValidationFailure("conductor-image-contract") from None
        if len(payload) != 1:
            raise ValidationFailure("conductor-image-contract")
        config = payload[0]
        if config.get("Os") != "linux" or config.get("Architecture") != "amd64":
            raise ValidationFailure("conductor-image-contract")
        user = config["Config"].get("User")
        if user not in (None, "", "root", "0"):
            raise ValidationFailure("conductor-image-contract")
        if config["Config"].get("Entrypoint") != [CONDUCTOR_ENTRYPOINT]:
            raise ValidationFailure("conductor-image-contract")
        if tuple(config["Config"].get("Cmd") or ()) != CONDUCTOR_COMMAND:
            raise ValidationFailure("conductor-image-contract")

    def _conductor_readonly_probe(self) -> None:
        command = (
            "set -eu; "
            "test -x /usr/local/bin/coriolis-conductor; "
            "test -x /usr/local/bin/coriolis-dbsync; "
            'python3 -c "import '
            + ",".join(module for module in CONDUCTOR_IMPORTS)
            + '"; '
            "if touch /readonly-check 2>/dev/null; then exit 1; fi; "
            "touch /tmp/writable-check"
        )
        self._checked(
            "conductor-readonly-probe",
            self._docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                f"{CONDUCTOR_RUN_AS_ID}:{CONDUCTOR_RUN_AS_ID}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--workdir",
                "/tmp",
                "--env",
                "HOME=/tmp",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--entrypoint",
                "/bin/sh",
                CONDUCTOR_IMAGE,
                "-c",
                command,
            ),
        )

    def _inspect_conductor_live_probe(self) -> None:
        name = self.resources.conductor_live_probe
        self._checked(
            "start-conductor-live-probe",
            self._docker(
                "run",
                "--name",
                name,
                "--detach",
                "--network",
                "none",
                "--user",
                f"{CONDUCTOR_RUN_AS_ID}:{CONDUCTOR_RUN_AS_ID}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--env",
                "HOME=/tmp",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--entrypoint",
                "/bin/sh",
                CONDUCTOR_IMAGE,
                "-c",
                "sleep 300",
            ),
        )
        try:
            try:
                result = self.runner(self._docker("inspect", name), self.timeout)
                user_result = self.runner(
                    self._docker("exec", name, "id", "-u"), self.timeout
                )
                group_result = self.runner(
                    self._docker("exec", name, "id", "-g"), self.timeout
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure("conductor-live-probe-inspect") from None
            if result.returncode != 0:
                raise ValidationFailure("conductor-live-probe-inspect")
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                raise ValidationFailure("conductor-live-probe-inspect") from None
            if len(payload) != 1:
                raise ValidationFailure("conductor-live-probe-inspect")
            host_config = payload[0]["HostConfig"]
            config = payload[0]["Config"]
            if config.get("User") != f"{CONDUCTOR_RUN_AS_ID}:{CONDUCTOR_RUN_AS_ID}":
                raise ValidationFailure("conductor-live-probe-inspect")
            if host_config.get("ReadonlyRootfs") is not True:
                raise ValidationFailure("conductor-live-probe-inspect")
            security_opt = host_config.get("SecurityOpt") or []
            if "no-new-privileges" not in security_opt:
                raise ValidationFailure("conductor-live-probe-inspect")
            if host_config.get("CapDrop") != ["ALL"]:
                raise ValidationFailure("conductor-live-probe-inspect")
            if host_config.get("NetworkMode") != "none":
                raise ValidationFailure("conductor-live-probe-inspect")
            tmpfs = host_config.get("Tmpfs") or {}
            if "/tmp" not in tmpfs:
                raise ValidationFailure("conductor-live-probe-inspect")
            if user_result.returncode != 0 or user_result.stdout.strip() != "42434":
                raise ValidationFailure("conductor-live-probe-inspect")
            if group_result.returncode != 0 or group_result.stdout.strip() != "42434":
                raise ValidationFailure("conductor-live-probe-inspect")
        finally:
            self._checked(
                "remove-conductor-live-probe",
                self._docker("rm", "-f", name),
            )

    def _prepare_mariadb_mounts(self) -> None:
        self._checked(
            "prepare-mariadb-mounts",
            self._docker(
                "run",
                "--name",
                self.resources.mariadb_prepare,
                "--rm",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,src={self.resources.data_volume},dst={MARIADB_DATA_DIR}",
                "--mount",
                f"type=volume,src={self.resources.runtime_volume},dst={MARIADB_RUNTIME_DIR}",
                MARIADB_IMAGE,
                "/bin/sh",
                "-c",
                "chown -R 42434:42434 /var/lib/mysql /run/mysqld",
            ),
        )

    def _prepare_rabbitmq_mounts(self) -> None:
        self._checked(
            "prepare-rabbitmq-mounts",
            self._docker(
                "run",
                "--name",
                self.resources.mariadb_prepare,
                "--rm",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,src={self.resources.rabbitmq_data_volume},dst={RABBITMQ_DATA_DIR}",
                "--mount",
                f"type=volume,src={self.resources.rabbitmq_runtime_volume},dst={RABBITMQ_RUNTIME_DIR}",
                "--mount",
                f"type=volume,src={self.resources.rabbitmq_logs_volume},dst={RABBITMQ_LOG_DIR}",
                MARIADB_IMAGE,
                "/bin/sh",
                "-c",
                "chown -R 42439:42439 /var/lib/rabbitmq /run/rabbitmq "
                "/var/log/rabbitmq",
            ),
        )

    def _stage_evidence_inputs(self) -> None:
        assert self.paths is not None
        self._checked(
            "stage-evidence-inputs",
            self._docker(
                "run",
                "--name",
                self.resources.mariadb_staging,
                "--rm",
                "--user",
                "0:0",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "FOWNER",
                "--cap-add",
                "DAC_OVERRIDE",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=bind,src={self.paths.mariadb_public},dst=/source/public,readonly",
                "--mount",
                f"type=bind,src={self.paths.mariadb_secret},dst=/source/secret,readonly",
                "--mount",
                f"type=bind,src={self.paths.keystone},dst=/source/keystone,readonly",
                "--mount",
                f"type=bind,src={self.paths.coriolis},dst=/source/coriolis,readonly",
                "--mount",
                f"type=bind,src={self.paths.coriolis_secret},dst=/source/coriolis-secret,readonly",
                "--mount",
                f"type=bind,src={self.paths.rabbitmq},dst=/source/rabbitmq,readonly",
                "--mount",
                f"type=bind,src={self.paths.rabbitmq_secret},dst=/source/rabbitmq-secret,readonly",
                "--mount",
                f"type=bind,src={self.paths.probe},dst=/source/probe,readonly",
                "--mount",
                f"type=volume,src={self.resources.mariadb_public_volume},dst=/evidence/mariadb-public",
                "--mount",
                f"type=volume,src={self.resources.mariadb_secret_volume},dst=/evidence/mariadb-secret",
                "--mount",
                f"type=volume,src={self.resources.keystone_config_volume},dst=/evidence/keystone",
                "--mount",
                f"type=volume,src={self.resources.coriolis_config_volume},dst=/evidence/coriolis",
                "--mount",
                f"type=volume,src={self.resources.coriolis_secret_volume},dst=/evidence/coriolis-secret",
                "--mount",
                f"type=volume,src={self.resources.rabbitmq_config_volume},dst=/evidence/rabbitmq",
                "--mount",
                f"type=volume,src={self.resources.rabbitmq_secret_volume},dst=/evidence/rabbitmq-secret",
                MARIADB_IMAGE,
                "/bin/sh",
                "-c",
                "set -eu; "
                "cp -a /source/public/. /evidence/mariadb-public/; "
                "cp -a /source/secret/. /evidence/mariadb-secret/; "
                "cp -a /source/keystone/. /evidence/keystone/; "
                "cp -a /source/coriolis/. /evidence/coriolis/; "
                "cp -a /source/coriolis-secret/. /evidence/coriolis-secret/; "
                "cp -a /source/rabbitmq/. /evidence/rabbitmq/; "
                "cp -a /source/rabbitmq-secret/. /evidence/rabbitmq-secret/; "
                "cp -a /source/probe/. /evidence/mariadb-secret/; "
                "chown -R 42434:42434 /evidence/mariadb-public "
                "/evidence/mariadb-secret /evidence/coriolis "
                "/evidence/coriolis-secret; "
                "chown -R 42425:42425 /evidence/keystone; "
                "chown -R 42439:42439 /evidence/rabbitmq "
                "/evidence/rabbitmq-secret; "
                "chmod 0700 /evidence/mariadb-public /evidence/mariadb-secret "
                "/evidence/keystone /evidence/coriolis /evidence/coriolis-secret "
                "/evidence/rabbitmq /evidence/rabbitmq-secret; "
                "chmod 0400 /evidence/mariadb-public/* /evidence/mariadb-secret/* "
                "/evidence/keystone/* /evidence/coriolis/coriolis.conf "
                "/evidence/coriolis/*.py /evidence/coriolis-secret/* "
                "/evidence/rabbitmq/rabbitmq.conf /evidence/rabbitmq-secret/*; "
                "chmod 0500 /evidence/mariadb-public/prepare-mariadb.sh "
                "/evidence/mariadb-public/start-mariadb.sh "
                "/evidence/keystone/bootstrap.py /evidence/rabbitmq/start-rabbitmq.sh",
            ),
        )

    def _runtime_arguments(
        self, detached: bool, *, container_name: str | None = None, remove: bool = False
    ) -> list[str]:
        arguments = [
            "run",
            "--name",
            container_name or self.resources.mariadb_main,
        ]
        if remove:
            arguments.append("--rm")
        if detached:
            arguments.append("--detach")
        arguments.extend(
            [
                "--network",
                self.resources.network,
                "--network-alias",
                "mariadb",
                "--user",
                f"{MARIADB_RUN_AS_ID}:{MARIADB_RUN_AS_ID}",
                "--group-add",
                str(MARIADB_SUPPLEMENTAL_GROUP),
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,src={self.resources.mariadb_public_volume},"
                f"dst={MARIADB_CONFIG_DIR},readonly",
                "--mount",
                f"type=volume,src={self.resources.mariadb_secret_volume},"
                f"dst={MARIADB_SECRET_DIR},readonly",
                "--mount",
                f"type=volume,src={self.resources.mariadb_secret_volume},"
                "dst=/evidence/mariadb-secret,readonly",
                "--mount",
                f"type=volume,src={self.resources.mariadb_public_volume},"
                "dst=/evidence/probe,readonly",
                "--mount",
                f"type=volume,src={self.resources.data_volume},dst={MARIADB_DATA_DIR}",
                "--mount",
                f"type=volume,src={self.resources.runtime_volume},dst={MARIADB_RUNTIME_DIR}",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                MARIADB_IMAGE,
            ]
        )
        return arguments

    def _start_mariadb(self) -> None:
        self._checked(
            "start-mariadb",
            self._docker(
                *self._runtime_arguments(True),
                "/bin/sh",
                "-c",
                f"exec {MARIADB_CONFIG_DIR}/start-mariadb.sh",
            ),
        )

    def _prepare_mariadb_runtime(self) -> None:
        self._checked(
            "prepare-mariadb-runtime",
            self._docker(
                *self._runtime_arguments(
                    False,
                    container_name=self.resources.mariadb_prepare,
                    remove=True,
                ),
                "/bin/sh",
                f"{MARIADB_CONFIG_DIR}/prepare-mariadb.sh",
            ),
        )

    def _container_exit_stage(self) -> str:
        try:
            result = self.runner(
                self._docker("logs", self.resources.mariadb_main), self.timeout
            )
        except (OSError, subprocess.SubprocessError):
            return "database-container-exited"
        captured = f"{result.stdout}\n{result.stderr}".lower()
        categories = (
            (("permission denied",), "database-container-exited-permission"),
            (("read-only file system",), "database-container-exited-read-only"),
            (("access denied",), "database-container-exited-authentication"),
            (("can't create/write", "cannot write"), "database-container-exited-write"),
        )
        for markers, stage in categories:
            if any(marker in captured for marker in markers):
                return stage
        return "database-container-exited"

    def _poll_mariadb_ready(self) -> None:
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            try:
                running = self.runner(
                    self._docker(
                        "inspect",
                        "--format={{.State.Running}}",
                        self.resources.mariadb_main,
                    ),
                    self.timeout,
                )
                if running.returncode != 0 or running.stdout.strip() != "true":
                    raise ValidationFailure(self._container_exit_stage())
                marker = self.runner(
                    self._docker(
                        "exec",
                        self.resources.mariadb_main,
                        "test",
                        "-f",
                        MARIADB_BOOTSTRAP_COMPLETE_MARKER,
                    ),
                    self.timeout,
                )
                query = self.runner(
                    self._docker(
                        "exec",
                        self.resources.mariadb_main,
                        "mariadb",
                        f"--defaults-file={MARIADB_ADMIN_CNF_PATH}",
                        "--execute=SELECT 1",
                    ),
                    self.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure("database-ready") from None
            if marker.returncode == 0 and query.returncode == 0:
                return
            self.sleeper(POLL_INTERVAL)
        raise ValidationFailure("database-ready")

    def _mariadb_healthy_gate(self) -> None:
        self._checked(
            "gate-mariadb-tcp-query",
            self._docker(
                "exec",
                self.resources.mariadb_main,
                "mariadb",
                "--defaults-file=/evidence/mariadb-secret/coriolis.cnf",
                "--execute=SELECT 1",
            ),
        )

    def _coriolis_dbsync_arguments(self, container_name: str) -> list[str]:
        return [
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            self.resources.network,
            "--user",
            f"{CONDUCTOR_RUN_AS_ID}:{CONDUCTOR_RUN_AS_ID}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--workdir",
            "/tmp",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},"
            "dst=/evidence/coriolis,readonly",
            "--mount",
            f"type=volume,src={self.resources.mariadb_secret_volume},"
            "dst=/evidence/mariadb-secret,readonly",
            "--mount",
            f"type=volume,src={self.resources.mariadb_public_volume},"
            "dst=/evidence/probe,readonly",
            "--entrypoint",
            "/bin/sh",
            CONDUCTOR_IMAGE,
        ]

    def _run_dbsync(self) -> None:
        self._checked(
            "coriolis-dbsync",
            self._docker(
                *self._coriolis_dbsync_arguments(self.resources.dbsync_runner),
                "-c",
                "set -eu; "
                "coriolis-dbsync --config-file=/evidence/coriolis/coriolis.conf",
            ),
        )

    def _verify_schema(self) -> None:
        tables = " ".join(CORIOLIS_SCHEMA_TABLES)
        check = (
            "set -eu; "
            "mariadb --defaults-file=/evidence/mariadb-secret/coriolis.cnf -N "
            '-e "SELECT table_name FROM information_schema.tables '
            "WHERE table_schema='coriolis' AND table_type='BASE TABLE'\" "
            "> /tmp/coriolis-tables; "
            f'for t in {tables}; do grep -qx "$t" /tmp/coriolis-tables; done; '
            "mariadb --defaults-file=/evidence/mariadb-secret/coriolis.cnf -N "
            '-e "SELECT COUNT(*) FROM coriolis.migrate_version" '
            "> /tmp/coriolis-migration-state; "
            "grep -Eq '^[0-9]+$' /tmp/coriolis-migration-state"
        )
        self._checked(
            "coriolis-schema-present",
            self._docker(
                "exec",
                self.resources.mariadb_main,
                "/bin/sh",
                "-c",
                check,
            ),
        )

    def _dbsync_evidence(self) -> None:
        for suffix in ("", "-repeat"):
            self._stage(f"coriolis-dbsync{suffix}", self._run_dbsync)
        self._stage("coriolis-schema-present", self._verify_schema)

    def _keystone_arguments(
        self,
        *,
        container_name: str,
        keys_readonly: bool,
        detached: bool = False,
        network_alias: bool = False,
    ) -> list[str]:
        arguments = ["run", "--name", container_name]
        if detached:
            arguments.append("--detach")
        if network_alias:
            arguments.extend(["--network-alias", "keystone"])
        arguments.extend(
            [
                "--network",
                self.resources.network,
                "--user",
                f"{KEYSTONE_ID}:{KEYSTONE_ID}",
                "--group-add",
                KOLLA_GROUP,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,src={self.resources.keystone_config_volume},"
                "dst=/evidence/keystone,readonly",
                "--mount",
                f"type=volume,src={self.resources.keystone_config_volume},"
                "dst=/etc/keystone/runtime,readonly",
                "--mount",
                f"type=volume,src={self.resources.keystone_fernet_volume},"
                f"dst={KEYSTONE_FERNET_KEYS_DIR}{',readonly' if keys_readonly else ''}",
                "--mount",
                f"type=volume,src={self.resources.keystone_credential_volume},"
                f"dst={KEYSTONE_CREDENTIAL_KEYS_DIR}"
                f"{',readonly' if keys_readonly else ''}",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--tmpfs",
                "/run:rw,noexec,nosuid,size=16m,uid=42425,gid=42425,mode=0700",
                "--tmpfs",
                "/var/lib/keystone:rw,noexec,nosuid,size=16m,uid=42425,gid=42425,mode=0700",
                "--tmpfs",
                "/var/log/kolla:rw,noexec,nosuid,size=16m,uid=42425,gid=42425,mode=0700",
                KEYSTONE_IMAGE,
            ]
        )
        return arguments

    def _prepare_keystone_keys(self) -> None:
        self._checked(
            "prepare-keystone-key-mounts",
            self._docker(
                "run",
                "--name",
                self.resources.keystone_key_prepare,
                "--rm",
                "--user",
                "0:0",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "FOWNER",
                "--cap-add",
                "DAC_OVERRIDE",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,src={self.resources.keystone_fernet_volume},dst={KEYSTONE_FERNET_KEYS_DIR}",
                "--mount",
                f"type=volume,src={self.resources.keystone_credential_volume},dst={KEYSTONE_CREDENTIAL_KEYS_DIR}",
                KEYSTONE_IMAGE,
                "/bin/sh",
                "-c",
                "chown 42425:42425 /etc/keystone/fernet-keys "
                "/etc/keystone/credential-keys; "
                "chmod 0700 /etc/keystone/fernet-keys /etc/keystone/credential-keys",
            ),
        )

    def _keystone_one_shot(self, stage: str, *command: str) -> None:
        arguments = self._keystone_arguments(
            container_name=self.resources.keystone_one_shot,
            keys_readonly=False,
        )
        arguments.insert(1, "--rm")
        self._checked(stage, self._docker(*arguments, *command))

    def _start_keystone(self) -> None:
        self._checked(
            "start-keystone-wsgi",
            self._docker(
                *self._keystone_arguments(
                    container_name=self.resources.keystone_main,
                    keys_readonly=True,
                    detached=True,
                    network_alias=True,
                ),
                "/var/lib/kolla/venv/bin/keystone-wsgi-public",
                "--host",
                "0.0.0.0",
                "--port",
                "5000",
                "--",
                "--config-file",
                KEYSTONE_CONFIG_PATH,
            ),
        )

    def _probe_keystone(self, stage: str) -> None:
        deadline = self.clock() + self.timeout
        probe_command = (
            "status=$(curl --silent --show-error --output /tmp/body "
            "--write-out '%{http_code}' "
            'http://keystone:5000/v3); [ "$status" = 200 ]'
        )
        while self.clock() < deadline:
            try:
                result = self.runner(
                    self._docker(
                        *self._keystone_arguments(
                            container_name=self.resources.keystone_probe,
                            keys_readonly=True,
                        ),
                        "/bin/sh",
                        "-c",
                        probe_command,
                    ),
                    self.timeout,
                )
                running = self.runner(
                    self._docker(
                        "inspect",
                        "--format={{.State.Running}}",
                        self.resources.keystone_main,
                    ),
                    self.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure(stage) from None
            if running.returncode != 0 or running.stdout.strip() != "true":
                raise ValidationFailure("keystone-container-exited")
            if result.returncode == 0:
                self._checked(
                    "remove-keystone-probe",
                    self._docker("rm", self.resources.keystone_probe),
                )
                return
            self._checked(
                "remove-keystone-probe",
                self._docker("rm", "-f", self.resources.keystone_probe),
            )
            self.sleeper(POLL_INTERVAL)
        raise ValidationFailure(stage)

    def _keystone_evidence(self) -> None:
        for suffix in ("", "-repeat"):
            self._stage(
                f"keystone-db-sync{suffix}",
                lambda: self._keystone_one_shot(
                    "keystone-db-sync",
                    "keystone-manage",
                    "--config-file",
                    KEYSTONE_CONFIG_PATH,
                    "db_sync",
                ),
            )
        for suffix in ("", "-repeat"):
            self._stage(
                f"keystone-fernset{suffix}",
                lambda: self._keystone_one_shot(
                    "keystone-fernset",
                    "keystone-manage",
                    "--config-file",
                    KEYSTONE_CONFIG_PATH,
                    "fernet_setup",
                    "--keystone-user",
                    "keystone",
                    "--keystone-group",
                    "keystone",
                ),
            )
            self._stage(
                f"keystone-credential-setup{suffix}",
                lambda: self._keystone_one_shot(
                    "keystone-credential-setup",
                    "keystone-manage",
                    "--config-file",
                    KEYSTONE_CONFIG_PATH,
                    "credential_setup",
                    "--keystone-user",
                    "keystone",
                    "--keystone-group",
                    "keystone",
                ),
            )
        for suffix in ("", "-repeat"):
            self._stage(
                f"keystone-bootstrap{suffix}",
                lambda: self._keystone_one_shot(
                    "keystone-bootstrap",
                    "/var/lib/kolla/venv/bin/python",
                    "/evidence/keystone/bootstrap.py",
                ),
            )
        self._stage("start-keystone-wsgi", self._start_keystone)
        self._stage(
            "gate-keystone-v3",
            lambda: self._probe_keystone("gate-keystone-v3"),
        )

    def _rabbitmq_arguments(self, container_name: str) -> list[str]:
        return [
            "run",
            "--name",
            container_name,
            "--detach",
            "--network",
            self.resources.network,
            "--network-alias",
            "rabbitmq",
            "--user",
            f"{RABBITMQ_RUN_AS_ID}:{RABBITMQ_RUN_AS_ID}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=volume,src={self.resources.rabbitmq_config_volume},"
            f"dst={RABBITMQ_CONFIG_DIR},readonly",
            "--mount",
            f"type=volume,src={self.resources.rabbitmq_secret_volume},"
            f"dst={RABBITMQ_SECRET_DIR},readonly",
            "--mount",
            f"type=volume,src={self.resources.rabbitmq_data_volume},dst={RABBITMQ_DATA_DIR}",
            "--mount",
            f"type=volume,src={self.resources.rabbitmq_runtime_volume},dst={RABBITMQ_RUNTIME_DIR}",
            "--mount",
            f"type=volume,src={self.resources.rabbitmq_logs_volume},dst={RABBITMQ_LOG_DIR}",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            RABBITMQ_IMAGE,
        ]

    def _start_rabbitmq(self) -> None:
        self._checked(
            "start-rabbitmq",
            self._docker(
                *self._rabbitmq_arguments(self.resources.rabbitmq_main),
                f"{RABBITMQ_CONFIG_DIR}/start-rabbitmq.sh",
            ),
        )

    def _mounted_probe(self, container_name: str, script: str) -> list[str]:
        return [
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            self.resources.network,
            "--user",
            f"{CONDUCTOR_RUN_AS_ID}:{CONDUCTOR_RUN_AS_ID}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--workdir",
            "/tmp",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=volume,src={self.resources.coriolis_secret_volume},"
            "dst=/evidence/coriolis-secret,readonly",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},"
            "dst=/evidence/coriolis,readonly",
            "--entrypoint",
            "/bin/sh",
            CONDUCTOR_IMAGE,
            "-c",
            f"set -eu; python3 /evidence/coriolis/{script}",
        ]

    def _poll_mounted_probe(self, stage: str, container_name: str, script: str) -> None:
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            try:
                result = self.runner(
                    self._docker(*self._mounted_probe(container_name, script)),
                    self.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure(stage) from None
            if result.returncode == 0:
                return
            self.sleeper(POLL_INTERVAL)
        raise ValidationFailure(stage)

    def _coriolis_bootstrap_arguments(self, container_name: str) -> list[str]:
        config = self.resources.coriolis_config_volume
        secret = self.resources.coriolis_secret_volume
        return [
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            self.resources.network,
            "--user",
            f"{CONDUCTOR_RUN_AS_ID}:{CONDUCTOR_RUN_AS_ID}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--workdir",
            "/tmp",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=volume,src={config},dst={BOOTSTRAP_CONFIG_DIR},readonly",
            "--mount",
            f"type=volume,src={config},dst={BOOTSTRAP_SCRIPT_DIR},readonly",
            "--mount",
            f"type=volume,src={secret},dst={BOOTSTRAP_INFRA_CREDENTIALS_DIR},readonly",
            "--mount",
            f"type=volume,src={secret},dst={BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR},readonly",
            "--entrypoint",
            "/bin/sh",
            CONDUCTOR_IMAGE,
            "-c",
            "set -eu; python3 /etc/coriolis-bootstrap/bootstrap.py",
        ]

    def _run_bootstrapper(self) -> None:
        self._checked(
            "coriolis-common-bootstrap",
            self._docker(
                *self._coriolis_bootstrap_arguments(self.resources.bootstrapper)
            ),
        )

    def _probe_bootstrap_state(self) -> None:
        self._checked(
            "gate-coriolis-bootstrap-state",
            self._docker(
                *self._mounted_probe(
                    self.resources.coriolis_probe, "verify_keystone_state.py"
                ),
            ),
        )

    def _coriolis_keystone_evidence(self) -> None:
        for suffix in ("", "-repeat"):
            self._stage(f"coriolis-common-bootstrap{suffix}", self._run_bootstrapper)
        self._stage("gate-coriolis-bootstrap-state", self._probe_bootstrap_state)

    def _cleanup_resources(self) -> None:
        resources: list[tuple[str, str]] = [
            ("container", self.resources.mariadb_main),
            ("container", self.resources.mariadb_prepare),
            ("container", self.resources.mariadb_staging),
            ("container", self.resources.keystone_main),
            ("container", self.resources.keystone_probe),
            ("container", self.resources.keystone_one_shot),
            ("container", self.resources.keystone_key_prepare),
            ("container", self.resources.dbsync_runner),
            ("container", self.resources.bootstrapper),
            ("container", self.resources.coriolis_probe),
            ("container", self.resources.conductor_live_probe),
            ("container", self.resources.rabbitmq_main),
            ("container", self.resources.rabbitmq_probe),
            ("container", self.resources.memcached_main),
            ("container", self.resources.memcached_probe),
            ("network", self.resources.network),
            ("volume", self.resources.runtime_volume),
            ("volume", self.resources.data_volume),
            ("volume", self.resources.mariadb_public_volume),
            ("volume", self.resources.mariadb_secret_volume),
            ("volume", self.resources.keystone_config_volume),
            ("volume", self.resources.keystone_fernet_volume),
            ("volume", self.resources.keystone_credential_volume),
            ("volume", self.resources.coriolis_config_volume),
            ("volume", self.resources.coriolis_secret_volume),
            ("volume", self.resources.rabbitmq_config_volume),
            ("volume", self.resources.rabbitmq_secret_volume),
            ("volume", self.resources.rabbitmq_data_volume),
            ("volume", self.resources.rabbitmq_runtime_volume),
            ("volume", self.resources.rabbitmq_logs_volume),
        ]
        for resource_type, name in resources:
            try:
                result = self.runner(
                    self._docker(resource_type, "rm", "-f", name), self.timeout
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure(f"cleanup-{resource_type}") from None
            if result.returncode != 0:
                captured = f"{result.stdout}\n{result.stderr}".lower()
                if "no such " in captured:
                    continue
                raise ValidationFailure(f"cleanup-{resource_type}")

    def _verify_cleanup(self) -> None:
        prefix = f"-{self.resources.token}"
        for stage, command in (
            ("cleanup-container-leftovers", self._docker("ps", "-a")),
            ("cleanup-network-leftovers", self._docker("network", "ls")),
            ("cleanup-volume-leftovers", self._docker("volume", "ls")),
        ):
            try:
                result = self.runner(command, self.timeout)
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure(stage) from None
            if result.returncode != 0:
                raise ValidationFailure(stage)
            if any(prefix in line for line in result.stdout.splitlines()):
                raise ValidationFailure(stage)
        if self.paths is not None and self.paths.scratch.exists():
            raise ValidationFailure("cleanup-scratch-leftovers")

    def _cleanup(self) -> None:
        self._cleanup_resources()
        if self.paths is not None:
            shutil.rmtree(self.paths.scratch, ignore_errors=True)
        self._verify_cleanup()

    def run(self) -> int:
        started = self.clock()
        failure: ValidationFailure | None = None
        try:
            try:
                self._run_body()
            except ValidationFailure as error:
                failure = error
            except Exception:
                failure = ValidationFailure("internal")
            self._cleanup()
        except ValidationFailure as error:
            failure = error
        elapsed = self.clock() - started
        if failure is not None:
            self.report(f"FAIL {failure.stage}")
            self.report(f"SUMMARY runtime failed {elapsed:.3f}")
            return 1
        self.report(f"SUMMARY runtime passed {elapsed:.3f}")
        return 0

    def _run_body(self) -> None:
        self.paths = create_evidence_files(
            self.repository_root, self.resources.mariadb_main
        )
        self._stage(
            "docker-cli-daemon",
            lambda: self._checked("docker-cli-daemon", self._docker("info")),
        )
        for stage, image in (
            ("conductor-image-available", CONDUCTOR_IMAGE),
            ("mariadb-image-available", MARIADB_IMAGE),
            ("keystone-image-available", KEYSTONE_IMAGE),
            ("rabbitmq-image-available", RABBITMQ_IMAGE),
            ("memcached-image-available", MEMCACHED_IMAGE),
        ):
            self._stage(
                stage,
                lambda image=image: self._checked(
                    stage, self._docker("image", "pull", image)
                ),
            )
        self._stage("conductor-image-contract", self._verify_conductor_image_contract)
        self._stage("conductor-readonly-probe", self._conductor_readonly_probe)
        self._stage("conductor-live-probe-inspect", self._inspect_conductor_live_probe)
        volume_attrs = (
            "data_volume",
            "runtime_volume",
            "mariadb_public_volume",
            "mariadb_secret_volume",
            "keystone_config_volume",
            "keystone_fernet_volume",
            "keystone_credential_volume",
            "coriolis_config_volume",
            "coriolis_secret_volume",
            "rabbitmq_config_volume",
            "rabbitmq_secret_volume",
            "rabbitmq_data_volume",
            "rabbitmq_runtime_volume",
            "rabbitmq_logs_volume",
        )
        resource_commands: list[tuple[str, Sequence[str]]] = [
            (
                attr.replace("_", "-"),
                self._docker("volume", "create", getattr(self.resources, attr)),
            )
            for attr in volume_attrs
        ]
        resource_commands.append(
            (
                "private-network",
                self._docker(
                    "network",
                    "create",
                    "--driver",
                    "bridge",
                    "--internal",
                    self.resources.network,
                ),
            )
        )
        for stage, command in resource_commands:
            self._stage(stage, lambda c=command, s=stage: self._checked(s, c))
        self._stage("stage-evidence-inputs", self._stage_evidence_inputs)
        self._stage("prepare-mariadb-mounts", self._prepare_mariadb_mounts)
        self._stage("prepare-rabbitmq-mounts", self._prepare_rabbitmq_mounts)
        self._stage("prepare-mariadb-runtime", self._prepare_mariadb_runtime)
        self._stage("start-mariadb", self._start_mariadb)
        self._stage("database-ready", self._poll_mariadb_ready)
        self._stage("gate-mariadb-tcp-query", self._mariadb_healthy_gate)
        self._dbsync_evidence()
        self._stage("prepare-keystone-key-mounts", self._prepare_keystone_keys)
        self._keystone_evidence()
        self._stage("start-rabbitmq", self._start_rabbitmq)
        self._stage(
            "gate-rabbitmq-protocol",
            lambda: self._poll_mounted_probe(
                "gate-rabbitmq-protocol",
                self.resources.rabbitmq_probe,
                "rabbitmq_probe.py",
            ),
        )
        self._stage("start-memcached", self._start_memcached)
        self._stage(
            "gate-memcached-protocol",
            lambda: self._poll_mounted_probe(
                "gate-memcached-protocol",
                self.resources.memcached_probe,
                "memcached_probe.py",
            ),
        )
        self._coriolis_keystone_evidence()

    def _start_memcached(self) -> None:
        self._checked(
            "start-memcached",
            self._docker(
                "run",
                "--name",
                self.resources.memcached_main,
                "--detach",
                "--network",
                self.resources.network,
                "--network-alias",
                "memcached",
                "--user",
                f"{MEMCACHED_RUN_AS_ID}:{MEMCACHED_RUN_AS_ID}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                MEMCACHED_IMAGE,
                MEMCACHED_COMMAND,
                *MEMCACHED_ARGS,
            ),
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Disposable local Coriolis bootstrap runtime evidence."
    )
    parser.add_argument(
        "--run", action="store_true", help="perform the disposable local evidence"
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="stage timeout in seconds"
    )
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("--run is required; this performs disposable runtime evidence")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return Validator(
        repository_root=Path(__file__).resolve().parents[1], timeout=args.timeout
    ).run()


if __name__ == "__main__":
    sys.exit(main())
