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
      explicit numeric non-root UID/GID, and no network, with a tmpfs at /tmp.
      A live probe is inspected to confirm the effective UID/GID and
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
7. The pinned conductor runs as the real long-running workload (direct
      `coriolis-conductor --config-file=/etc/coriolis/coriolis.conf`, entrypoint
      bypassed) and the exact API binary runs on the same internal network with
      the generated config. The pinned scheduler, transfer-cron, and
      minion-manager and deployer-manager binaries also run as long-running
      workloads with the same
      direct-command, hardened, private-network contract and no host ports or
      locks mounts. Container
      configuration is inspected to assert the direct command, exact image,
      UID/GID, read-only root, cap-drop, and no-new-privileges, internal
       network, readonly config mount plus writable isolated tmpfs mounts, and no host
      port. The API's unauthenticated `/v1` gate must return 401, then an
      authenticated secret-aware verifier proves the API->RabbitMQ RPC->conductor
      ->MariaDB path for `/v1/{project_id}/endpoints` without emitting any
      sensitive value. An admin-context messaging probe proves the scheduler RPC
      (including its initial read of the empty bootstrap MariaDB raising the
      expected `NoWorkerServiceError`), the transfer-cron RPC, and minion-manager
      diagnostics plus its empty-pool MariaDB read over RabbitMQ. A separate
      registration-only gate then proves one enabled/up worker, direct worker RPC,
      scheduler selection, and stable hashed service identity. The conductor,
      scheduler, transfer-cron, minion-manager, deployer-manager, and worker must
      remain running over a bounded stability interval, exit cleanly on
      SIGTERM/`docker stop`, restart, and recover after a RabbitMQ restart without
      being recreated. The fresh stack has no pending deployments; this evidence
      does not invoke deployer RPCs, deployments, provider workflows, or writes.

This script must never print credentials, DSNs, tokens, headers, bodies, raw
sensitive logs, or process environments. It prints sanitized PASS/FAIL stage
summaries only.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from coriolis_operator.api import (  # type: ignore[import-untyped]
    API_ARGS,
    API_COMMAND,
    API_CONFIG_DIR,
    API_IMAGE,
    API_LOCKS_DIR,
    API_LOG_DIR,
    API_PORT,
    API_PROTOCOL_PROBE,
    API_RUN_AS_ID,
)
from coriolis_operator.common import (  # type: ignore[import-untyped]
    BOOTSTRAP_CONFIG_DIR,
    BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR,
    BOOTSTRAP_INFRA_CREDENTIALS_DIR,
    BOOTSTRAP_SCRIPT_DIR,
    render_bootstrap_script,
)
from coriolis_operator.configuration import (  # type: ignore[import-untyped]
    KubernetesCoriolisRenderInputs,
    SensitiveCoriolisCredentials,
    SensitiveCoriolisEndpoints,
    render_coriolis_config,
    render_sensitive_coriolis_config,
)
from coriolis_operator.deployer_manager import (  # type: ignore[import-untyped]
    DEPLOYER_MANAGER_ARGS,
    DEPLOYER_MANAGER_COMMAND,
    DEPLOYER_MANAGER_CONFIG_DIR,
    DEPLOYER_MANAGER_IMAGE,
    DEPLOYER_MANAGER_LOG_DIR,
    DEPLOYER_MANAGER_RUN_AS_ID,
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
SCHEDULER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-scheduler:2603.4"
    "@sha256:45bea9e0bab4cac0fdddee6d3eac52006d12cf7de1e798e2949dd9ebc2a73c41"
)
TRANSFER_CRON_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-transfer-cron:2603.4"
    "@sha256:3a44d3b40ba92dff9217b8e7d6a7ca3e7a202efa2641c771ce9b2a3552b3ea9c"
)
MINION_MANAGER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-minion-manager:2603.4"
    "@sha256:1ea016dd967ce249a45cf9937701a45880f3b42f8146a93d1f5eb4f1d84e1fb9"
)
WORKER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-worker:2603.4"
    "@sha256:ff30999d6e43709411f197b1b6b80dbce1d7e5498a27f869df93a061626ab2c9"
)

CONDUCTOR_RUN_AS_ID = 42434
SCHEDULER_RUN_AS_ID = 42434
TRANSFER_CRON_RUN_AS_ID = 42434
MINION_MANAGER_RUN_AS_ID = 42434
KEYSTONE_ID = "42425"
KOLLA_GROUP = "42400"
CORIOLIS_SCHEMA_TABLES = ("migrate_version", "endpoint", "service", "region")

API_ALIAS = "coriolis-api"
API_VERSION_TAG = "2603.4"
STABILITY_INTERVAL = 20.0
# Full-stack qualification keeps bounded shutdown windows for the workloads
# that exceeded the historical 15-second bound while still requiring exit
# zero. The scheduler, deployer-manager, and worker retain 30 seconds; the
# conductor gets 45 seconds because its own internal shutdown consumes its
# roughly 30-second container grace and raced SIGKILL at the 30-second bound.
CONDUCTOR_STOP_TIMEOUT = 45
DEPLOYER_MANAGER_STOP_TIMEOUT = 30
SCHEDULER_STOP_TIMEOUT = 30
WORKER_STOP_TIMEOUT = 30

CONDUCTOR_ENTRYPOINT = "/entrypoint.sh"
CONDUCTOR_COMMAND = (
    "/usr/local/bin/coriolis-conductor",
    "--config-file=/etc/coriolis/coriolis.conf",
)
SCHEDULER_ENTRYPOINT = "/entrypoint.sh"
SCHEDULER_COMMAND = (
    "/usr/local/bin/coriolis-scheduler",
    "--config-file=/etc/coriolis/coriolis.conf",
)
TRANSFER_CRON_ENTRYPOINT = "/entrypoint.sh"
TRANSFER_CRON_COMMAND = (
    "/usr/local/bin/coriolis-transfer-cron",
    "--config-file=/etc/coriolis/coriolis.conf",
)
MINION_MANAGER_ENTRYPOINT = "/entrypoint.sh"
MINION_MANAGER_COMMAND = (
    "/usr/local/bin/coriolis-minion-manager",
    "--config-file=/etc/coriolis/coriolis.conf",
)
WORKER_ENTRYPOINT = "/entrypoint.sh"
WORKER_IMAGE_COMMAND = (
    "/usr/local/bin/coriolis-worker",
    "--config-file=/etc/coriolis/coriolis.conf",
)
WORKER_COMMAND = (
    "/usr/local/bin/coriolis-worker",
    "--worker-process-count",
    "1",
    "--config-file=/etc/coriolis/coriolis.conf",
)
WORKER_HOSTNAME = "coriolis-worker"
WORKER_TOPIC = "coriolis_worker"
WORKER_PROBE_FILENAME = "coriolis_worker_probe.py"
# Exact 2603.4 worker-qualified provider module roots. The active Kubernetes
# provider class mappings reference only these roots, and the oracle-vm/opc/
# nutanix/cloudstack roots are excluded. Probed via find_spec (never imported).
WORKER_PROVIDER_MODULE_ROOTS = (
    "coriolis_provider_openstack",
    "coriolis_provider_vhi",
    "coriolis_provider_azure",
    "coriolis_provider_scvmm",
    "coriolis_provider_vmware_vsphere",
    "coriolis_provider_aws",
    "coriolis_provider_metal",
    "coriolis_provider_ovirt_olvm",
    "coriolis_provider_ovirt_rhev",
    "coriolis_provider_oci",
    "coriolis_provider_opca",
    "coriolis_provider_o3c",
    "coriolis_provider_kubevirt",
    "coriolis_provider_harvester",
    "coriolis_provider_lxd",
    "coriolis_provider_proxmox",
    "coriolis_provider_libvirt",
)
WORKER_EXCLUDED_PROVIDER_MODULE_ROOTS = (
    "coriolis_provider_oracle_vm",
    "coriolis_provider_opc",
    "coriolis_provider_nutanix",
    "coriolis_provider_cloudstack",
)
DEPLOYER_MANAGER_ENTRYPOINT = "/entrypoint.sh"
DEPLOYER_MANAGER_IMAGE_COMMAND = (DEPLOYER_MANAGER_COMMAND, *DEPLOYER_MANAGER_ARGS)
MESSAGING_PROBE_FILENAME = "coriolis_messaging_probe.py"
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

    @property
    def conductor_main(self) -> str:
        return f"{PREFIX}-{self.token}-conductor"

    @property
    def scheduler_main(self) -> str:
        return f"{PREFIX}-{self.token}-scheduler"

    @property
    def transfer_cron_main(self) -> str:
        return f"{PREFIX}-{self.token}-transfer-cron"

    @property
    def minion_manager_main(self) -> str:
        return f"{PREFIX}-{self.token}-minion-manager"

    @property
    def deployer_manager_main(self) -> str:
        return f"{PREFIX}-{self.token}-deployer-manager"

    @property
    def worker_main(self) -> str:
        return f"{PREFIX}-{self.token}-worker"

    @property
    def messaging_probe(self) -> str:
        return f"{PREFIX}-{self.token}-messaging-probe"

    @property
    def worker_probe(self) -> str:
        return f"{PREFIX}-{self.token}-worker-probe"

    @property
    def api_main(self) -> str:
        return f"{PREFIX}-{self.token}-api"

    @property
    def rpc_probe(self) -> str:
        return f"{PREFIX}-{self.token}-rpc-probe"


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

# Authenticated API->RabbitMQ RPC->conductor->MariaDB path. This script reads
# the already-generated Coriolis Keystone password from the mounted private
# evidence file, authenticates to the `service` project, resolves the project
# ID, and calls GET /v1/{project_id}/endpoints on the in-network API alias. It
# emits only fixed success/failure markers: never credentials, tokens, project
# IDs, URL catalogs, headers, response bodies, config, or environment.

CORIOLIS_RPC_PROBE = """import http.client
import json
import sys
from pathlib import Path
from keystoneauth1.identity import v3
from keystoneauth1 import session as ks_session

def fail():
    print('CORIOLIS_RPC_FAIL')
    sys.exit(1)

try:
    password = Path(
        '/evidence/coriolis-secret/coriolis-keystone-password').read_text().strip()
    auth = v3.Password(auth_url='http://keystone:5000/v3',
        username='coriolis', password=password, project_name='service',
        user_domain_name='Default', project_domain_name='Default')
    session = ks_session.Session(auth=auth)
    token = session.get_token()
    project_id = auth.get_project_id(session)
    if not token or not project_id:
        fail()
    connection = http.client.HTTPConnection('coriolis-api', 7667, timeout=60)
    connection.request('GET', '/v1/%s/endpoints' % project_id,
        headers={'X-Auth-Token': token, 'Accept': 'application/json'})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    if response.status != 200:
        fail()
    payload = json.loads(body)
    if not isinstance(payload, dict):
        fail()
    if not isinstance(payload.get('endpoints'), list):
        fail()
    print('coriolis-rpc-ok')
except Exception:
    fail()
"""

# Authenticated Coriolis admin-context RPC probe over the generated config
# only. It calls the scheduler diagnostics, then the scheduler's
# `get_workers_for_specs` and requires the expected `NoWorkerServiceError`
# (proving the scheduler read the empty bootstrap MariaDB), then the
# transfer-cron diagnostics (proving that workload's RPC server started after
# its post-fork conductor schedule load completed against the empty DB), then
# minion-manager diagnostics and an empty-pool read (proving its RPC and direct
# MariaDB path without invoking refresh, provider, trust, or write operations).
# It emits only fixed success/failure markers: never exceptions, diagnostics
# data, context, config, environment, or credentials.

CORIOLIS_MESSAGING_PROBE = """import sys
from oslo_config import cfg

def fail():
    print('CORIOLIS_MESSAGING_FAIL')
    sys.exit(1)

try:
    from coriolis import exception
    from coriolis.context import get_admin_context
    from coriolis.minion_manager.rpc.client import MinionManagerClient
    from coriolis.scheduler.rpc.client import SchedulerClient
    from coriolis.transfer_cron.rpc.client import TransferCronClient

    cfg.CONF([], project='coriolis',
             default_config_files=['/etc/coriolis/coriolis.conf'])
    ctxt = get_admin_context()
    expect_worker = sys.argv[1:] == ['worker']
    scheduler = SchedulerClient()
    scheduler.get_diagnostics(ctxt)
    try:
        workers = scheduler.get_workers_for_specs(ctxt)
    except exception.NoWorkerServiceError:
        if expect_worker:
            fail()
    else:
        if not expect_worker or len(workers) != 1:
            fail()
    transfer_cron = TransferCronClient()
    transfer_cron.get_diagnostics(ctxt)
    minion_manager = MinionManagerClient()
    minion_manager.get_diagnostics(ctxt)
    if minion_manager.get_minion_pools(ctxt) != []:
        fail()
    print('coriolis-messaging-ok')
except Exception:
    fail()
"""

# Registration-only worker probe. It uses the generated config and an admin
# context, but never performs provider, migration, trust, or write operations.
CORIOLIS_WORKER_PROBE = """import sys
from oslo_config import cfg

def fail():
    print('CORIOLIS_WORKER_FAIL')
    sys.exit(1)

try:
    from coriolis.context import get_admin_context
    from coriolis.scheduler.rpc.client import SchedulerClient
    from coriolis.worker.rpc.client import WorkerClient

    cfg.CONF([], project='coriolis',
             default_config_files=['/etc/coriolis/coriolis.conf'])
    ctxt = get_admin_context()
    status = WorkerClient(host='coriolis-worker').get_service_status(ctxt)
    if (status.get('host') != 'coriolis-worker'
            or status.get('topic') != 'coriolis_worker'):
        fail()
    workers = SchedulerClient().get_workers_for_specs(ctxt, enabled=True)
    if len(workers) != 1:
        fail()
    worker = workers[0]
    field = (worker.get if isinstance(worker, dict)
             else lambda name: getattr(worker, name))
    if (field('host') != 'coriolis-worker'
            or field('binary') != 'coriolis-worker'
            or field('topic') != 'coriolis_worker'
            or field('enabled') is not True
            or field('status') != 'UP'):
        fail()
    print('coriolis-worker-ok')
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
        temp_keypair_password = secrets.token_urlsafe(32)

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

        coriolis_config = render_sensitive_coriolis_config(
            endpoints=SensitiveCoriolisEndpoints(
                rabbitmq_host="rabbitmq",
                memcached_host="memcached",
                database_host=mariadb_hostname,
                keystone_host="keystone",
            ),
            credentials=SensitiveCoriolisCredentials(
                rabbitmq_password=rabbitmq_password,
                coriolis_database_password=coriolis_password,
                coriolis_keystone_password=coriolis_keystone_password,
                temp_keypair_password=temp_keypair_password,
            ),
        )
        _write_private(coriolis / "coriolis.conf", coriolis_config["coriolis.conf"])
        runtime_config = render_coriolis_config(
            inputs=KubernetesCoriolisRenderInputs(
                bind_address="0.0.0.0",
                coriolis_port=API_PORT,
                coriolis_config_dir=API_CONFIG_DIR,
                coriolis_vmware_vix_disklib_log_dir="/var/log/coriolis/vmware-root",
                endpoints=SensitiveCoriolisEndpoints(
                    rabbitmq_host="rabbitmq",
                    memcached_host="memcached",
                    database_host=mariadb_hostname,
                    keystone_host="keystone",
                ),
            ),
            accepted_version=API_VERSION_TAG,
        )
        for name, content in runtime_config.items():
            _write_private(coriolis / name, content)
        _write_private(coriolis / "coriolis_rpc_probe.py", CORIOLIS_RPC_PROBE)
        _write_private(coriolis / MESSAGING_PROBE_FILENAME, CORIOLIS_MESSAGING_PROBE)
        _write_private(coriolis / WORKER_PROBE_FILENAME, CORIOLIS_WORKER_PROBE)
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
        self.worker_service_uuid: str | None = None

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

    def _verify_application_image_contract(
        self, stage: str, image: str, entrypoint: str | None, command: Sequence[str]
    ) -> None:
        """Assert linux/amd64, an empty/root image User, exact requested image
        metadata, and no exposed runtime surfaces for an application image."""
        try:
            result = self.runner(self._docker("image", "inspect", image), self.timeout)
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ValidationFailure(stage) from None
        if len(payload) != 1:
            raise ValidationFailure(stage)
        image_config = payload[0]
        if (
            image_config.get("Os") != "linux"
            or image_config.get("Architecture") != "amd64"
        ):
            raise ValidationFailure(stage)
        config = image_config.get("Config")
        if not isinstance(config, dict):
            raise ValidationFailure(stage)
        user = config.get("User")
        if user not in (None, "", "root", "0"):
            raise ValidationFailure(stage)
        if entrypoint is not None and config.get("Entrypoint") != [entrypoint]:
            raise ValidationFailure(stage)
        if tuple(config.get("Cmd") or ()) != tuple(command):
            raise ValidationFailure(stage)
        if any(
            config.get(field) for field in ("ExposedPorts", "Volumes", "Healthcheck")
        ):
            raise ValidationFailure(stage)

    def _verify_conductor_image_contract(self) -> None:
        self._verify_application_image_contract(
            "conductor-image-contract",
            CONDUCTOR_IMAGE,
            CONDUCTOR_ENTRYPOINT,
            CONDUCTOR_COMMAND,
        )

    def _verify_scheduler_image_contract(self) -> None:
        self._verify_application_image_contract(
            "scheduler-image-contract",
            SCHEDULER_IMAGE,
            SCHEDULER_ENTRYPOINT,
            SCHEDULER_COMMAND,
        )

    def _verify_transfer_cron_image_contract(self) -> None:
        self._verify_application_image_contract(
            "transfer-cron-image-contract",
            TRANSFER_CRON_IMAGE,
            TRANSFER_CRON_ENTRYPOINT,
            TRANSFER_CRON_COMMAND,
        )

    def _verify_minion_manager_image_contract(self) -> None:
        self._verify_application_image_contract(
            "minion-manager-image-contract",
            MINION_MANAGER_IMAGE,
            MINION_MANAGER_ENTRYPOINT,
            MINION_MANAGER_COMMAND,
        )

    def _verify_worker_image_contract(self) -> None:
        self._verify_application_image_contract(
            "worker-image-contract",
            WORKER_IMAGE,
            WORKER_ENTRYPOINT,
            WORKER_IMAGE_COMMAND,
        )

    def _worker_provider_probe_source(self) -> str:
        """Build the no-import/no-write provider-module probe source.

        Uses `importlib.util.find_spec` only; never imports or invokes a
        provider. Double-quoted string literals keep the source safe to embed
        in a single-quoted shell argument, and it emits no output.
        """
        source = "import importlib.util as _u\n"
        source += "".join(
            f'if _u.find_spec("{root}") is None: raise SystemExit(1)\n'
            for root in WORKER_PROVIDER_MODULE_ROOTS
        )
        source += "".join(
            f'if _u.find_spec("{root}") is not None: raise SystemExit(1)\n'
            for root in WORKER_EXCLUDED_PROVIDER_MODULE_ROOTS
        )
        return source

    def _verify_worker_provider_image_contract(self) -> None:
        self._checked(
            "worker-provider-image-contract",
            self._docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--entrypoint",
                "/bin/sh",
                WORKER_IMAGE,
                "-c",
                f"set -eu; python3 -c '{self._worker_provider_probe_source()}'",
            ),
        )

    def _verify_deployer_manager_image_contract(self) -> None:
        self._verify_application_image_contract(
            "deployer-manager-image-contract",
            DEPLOYER_MANAGER_IMAGE,
            DEPLOYER_MANAGER_ENTRYPOINT,
            DEPLOYER_MANAGER_IMAGE_COMMAND,
        )

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
                "coriolis-dbsync --config-file=/evidence/coriolis/coriolis.conf "
                "--nouse-syslog --log-dir=",
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

    def _conductor_runtime_arguments(self, container_name: str) -> list[str]:
        return [
            "run",
            "--name",
            container_name,
            "--detach",
            "--network",
            self.resources.network,
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
            "--tmpfs",
            f"{API_LOG_DIR}:rw,noexec,nosuid,size=64m,uid={CONDUCTOR_RUN_AS_ID},gid={CONDUCTOR_RUN_AS_ID},mode=0700",
            "--tmpfs",
            f"{API_LOCKS_DIR}:rw,noexec,nosuid,size=16m,uid={CONDUCTOR_RUN_AS_ID},gid={CONDUCTOR_RUN_AS_ID},mode=0700",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},dst={API_CONFIG_DIR},readonly",
            "--entrypoint",
            CONDUCTOR_COMMAND[0],
            CONDUCTOR_IMAGE,
            *CONDUCTOR_COMMAND[1:],
        ]

    def _api_runtime_arguments(self, container_name: str) -> list[str]:
        return [
            "run",
            "--name",
            container_name,
            "--detach",
            "--network",
            self.resources.network,
            "--network-alias",
            API_ALIAS,
            "--user",
            f"{API_RUN_AS_ID}:{API_RUN_AS_ID}",
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
            "--tmpfs",
            f"{API_LOG_DIR}:rw,noexec,nosuid,size=64m,uid={API_RUN_AS_ID},gid={API_RUN_AS_ID},mode=0700",
            "--tmpfs",
            f"{API_LOCKS_DIR}:rw,noexec,nosuid,size=16m,uid={API_RUN_AS_ID},gid={API_RUN_AS_ID},mode=0700",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},dst={API_CONFIG_DIR},readonly",
            "--entrypoint",
            API_COMMAND,
            API_IMAGE,
            *API_ARGS,
        ]

    def _start_conductor(self) -> None:
        self._checked(
            "start-conductor",
            self._docker(
                *self._conductor_runtime_arguments(self.resources.conductor_main)
            ),
        )

    def _start_api(self) -> None:
        self._checked(
            "start-api",
            self._docker(*self._api_runtime_arguments(self.resources.api_main)),
        )

    def _scheduler_runtime_arguments(self, container_name: str) -> list[str]:
        return [
            "run",
            "--name",
            container_name,
            "--detach",
            "--network",
            self.resources.network,
            "--user",
            f"{SCHEDULER_RUN_AS_ID}:{SCHEDULER_RUN_AS_ID}",
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
            "--tmpfs",
            f"{API_LOG_DIR}:rw,noexec,nosuid,size=64m,uid={SCHEDULER_RUN_AS_ID},gid={SCHEDULER_RUN_AS_ID},mode=0700",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},dst={API_CONFIG_DIR},readonly",
            "--entrypoint",
            SCHEDULER_COMMAND[0],
            SCHEDULER_IMAGE,
            *SCHEDULER_COMMAND[1:],
        ]

    def _transfer_cron_runtime_arguments(self, container_name: str) -> list[str]:
        return [
            "run",
            "--name",
            container_name,
            "--detach",
            "--network",
            self.resources.network,
            "--user",
            f"{TRANSFER_CRON_RUN_AS_ID}:{TRANSFER_CRON_RUN_AS_ID}",
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
            "--tmpfs",
            f"{API_LOG_DIR}:rw,noexec,nosuid,size=64m,uid={TRANSFER_CRON_RUN_AS_ID},gid={TRANSFER_CRON_RUN_AS_ID},mode=0700",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},dst={API_CONFIG_DIR},readonly",
            "--entrypoint",
            TRANSFER_CRON_COMMAND[0],
            TRANSFER_CRON_IMAGE,
            *TRANSFER_CRON_COMMAND[1:],
        ]

    def _minion_manager_runtime_arguments(self, container_name: str) -> list[str]:
        return [
            "run",
            "--name",
            container_name,
            "--detach",
            "--network",
            self.resources.network,
            "--user",
            f"{MINION_MANAGER_RUN_AS_ID}:{MINION_MANAGER_RUN_AS_ID}",
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
            "--tmpfs",
            f"{API_LOG_DIR}:rw,noexec,nosuid,size=64m,uid={MINION_MANAGER_RUN_AS_ID},gid={MINION_MANAGER_RUN_AS_ID},mode=0700",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},dst={API_CONFIG_DIR},readonly",
            "--entrypoint",
            MINION_MANAGER_COMMAND[0],
            MINION_MANAGER_IMAGE,
            *MINION_MANAGER_COMMAND[1:],
        ]

    def _deployer_manager_runtime_arguments(self, container_name: str) -> list[str]:
        return [
            "run",
            "--name",
            container_name,
            "--detach",
            "--network",
            self.resources.network,
            "--user",
            f"{DEPLOYER_MANAGER_RUN_AS_ID}:{DEPLOYER_MANAGER_RUN_AS_ID}",
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
            "--tmpfs",
            f"{DEPLOYER_MANAGER_LOG_DIR}:rw,noexec,nosuid,size=64m,uid={DEPLOYER_MANAGER_RUN_AS_ID},gid={DEPLOYER_MANAGER_RUN_AS_ID},mode=0700",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},"
            f"dst={DEPLOYER_MANAGER_CONFIG_DIR},readonly",
            "--entrypoint",
            DEPLOYER_MANAGER_COMMAND,
            DEPLOYER_MANAGER_IMAGE,
            *DEPLOYER_MANAGER_ARGS,
        ]

    def _start_scheduler(self) -> None:
        self._checked(
            "start-scheduler",
            self._docker(
                *self._scheduler_runtime_arguments(self.resources.scheduler_main)
            ),
        )

    def _start_transfer_cron(self) -> None:
        self._checked(
            "start-transfer-cron",
            self._docker(
                *self._transfer_cron_runtime_arguments(
                    self.resources.transfer_cron_main
                )
            ),
        )

    def _start_minion_manager(self) -> None:
        self._checked(
            "start-minion-manager",
            self._docker(
                *self._minion_manager_runtime_arguments(
                    self.resources.minion_manager_main
                )
            ),
        )

    def _start_deployer_manager(self) -> None:
        self._checked(
            "start-deployer-manager",
            self._docker(
                *self._deployer_manager_runtime_arguments(
                    self.resources.deployer_manager_main
                )
            ),
        )

    def _worker_runtime_arguments(self, container_name: str) -> list[str]:
        return [
            "run",
            "--name",
            container_name,
            "--detach",
            "--network",
            self.resources.network,
            "--hostname",
            WORKER_HOSTNAME,
            "--user",
            "0:0",
            "--privileged",
            "--read-only",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--tmpfs",
            f"{API_LOG_DIR}:rw,noexec,nosuid,size=64m",
            "--tmpfs",
            "/opt/coriolis/export:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},"
            f"dst={API_CONFIG_DIR},readonly",
            "--entrypoint",
            WORKER_COMMAND[0],
            WORKER_IMAGE,
            *WORKER_COMMAND[1:],
        ]

    def _start_worker(self) -> None:
        self._checked(
            "start-worker",
            self._docker(*self._worker_runtime_arguments(self.resources.worker_main)),
        )

    def _messaging_probe_arguments(
        self, container_name: str, *, expect_worker: bool = False
    ) -> list[str]:
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
            "--tmpfs",
            f"{API_LOG_DIR}:rw,noexec,nosuid,size=64m,uid={CONDUCTOR_RUN_AS_ID},gid={CONDUCTOR_RUN_AS_ID},mode=0700",
            "--mount",
            f"type=volume,src={self.resources.coriolis_config_volume},dst={API_CONFIG_DIR},readonly",
            "--entrypoint",
            "/bin/sh",
            CONDUCTOR_IMAGE,
            "-c",
            f"set -eu; python3 {API_CONFIG_DIR}/{MESSAGING_PROBE_FILENAME}"
            + (" worker" if expect_worker else ""),
        ]

    def _poll_messaging_probe(self, stage: str, *, expect_worker: bool = False) -> None:
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            try:
                result = self.runner(
                    self._docker(
                        *self._messaging_probe_arguments(
                            self.resources.messaging_probe,
                            expect_worker=expect_worker,
                        )
                    ),
                    self.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure(stage) from None
            if result.returncode == 0:
                return
            self.sleeper(POLL_INTERVAL)
        raise ValidationFailure(stage)

    def _poll_worker_probe(self, stage: str) -> None:
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            if not self._container_running(self.resources.worker_main):
                raise ValidationFailure("worker-container-exited")
            try:
                result = self.runner(
                    self._docker(
                        *self._messaging_probe_arguments(self.resources.worker_probe)[
                            :-2
                        ],
                        "-c",
                        f"set -eu; python3 {API_CONFIG_DIR}/{WORKER_PROBE_FILENAME}",
                    ),
                    self.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure(stage) from None
            if result.returncode == 0:
                return
            self.sleeper(POLL_INTERVAL)
        raise ValidationFailure(stage)

    def _container_running(self, name: str) -> bool:
        try:
            result = self.runner(
                self._docker("inspect", "--format={{.State.Running}}", name),
                self.timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _inspect_runtime(
        self,
        stage: str,
        name: str,
        image: str,
        entrypoint: str,
        command: Sequence[str],
        *,
        run_as_id: int,
        writable_paths: Sequence[str],
    ) -> None:
        try:
            result = self.runner(self._docker("inspect", name), self.timeout)
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ValidationFailure(stage) from None
        if len(payload) != 1:
            raise ValidationFailure(stage)
        container = payload[0]
        config = container["Config"]
        host_config = container["HostConfig"]
        if config.get("Image") != image:
            raise ValidationFailure(stage)
        if config.get("User") != f"{run_as_id}:{run_as_id}":
            raise ValidationFailure(stage)
        if tuple(config.get("Entrypoint") or ()) != (entrypoint,):
            raise ValidationFailure(stage)
        if tuple(config.get("Cmd") or ()) != tuple(command):
            raise ValidationFailure(stage)
        if host_config.get("ReadonlyRootfs") is not True:
            raise ValidationFailure(stage)
        if host_config.get("CapDrop") != ["ALL"]:
            raise ValidationFailure(stage)
        if "no-new-privileges" not in (host_config.get("SecurityOpt") or []):
            raise ValidationFailure(stage)
        if host_config.get("NetworkMode") != self.resources.network:
            raise ValidationFailure(stage)
        if host_config.get("PortBindings"):
            raise ValidationFailure(stage)
        mounts = host_config.get("Mounts") or []
        if len(mounts) != 1 or (
            mounts[0].get("Target") != API_CONFIG_DIR
            or mounts[0].get("ReadOnly") is not True
        ):
            raise ValidationFailure(stage)
        tmpfs = host_config.get("Tmpfs") or {}
        if set(tmpfs) != set(writable_paths):
            raise ValidationFailure(stage)

    def _inspect_worker_runtime(self) -> None:
        stage = "inspect-worker"
        try:
            result = self.runner(
                self._docker("inspect", self.resources.worker_main), self.timeout
            )
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)
        try:
            payload = json.loads(result.stdout)
            if len(payload) != 1:
                raise ValueError
            container = payload[0]
            config = container["Config"]
            host_config = container["HostConfig"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValidationFailure(stage) from None
        if (
            config.get("Image") != WORKER_IMAGE
            or config.get("Hostname") != WORKER_HOSTNAME
            or config.get("User") != "0:0"
            or tuple(config.get("Entrypoint") or ()) != (WORKER_COMMAND[0],)
            or tuple(config.get("Cmd") or ()) != WORKER_COMMAND[1:]
            or host_config.get("Privileged") is not True
            or host_config.get("ReadonlyRootfs") is not True
            or host_config.get("NetworkMode") != self.resources.network
            or host_config.get("PortBindings")
            or host_config.get("Binds")
            or host_config.get("Devices")
            or host_config.get("DeviceRequests")
            or host_config.get("VolumesFrom")
        ):
            raise ValidationFailure(stage)
        mounts = host_config.get("Mounts") or []
        if len(mounts) != 1 or (
            mounts[0].get("Type") not in (None, "volume")
            or mounts[0].get("Source")
            not in (None, self.resources.coriolis_config_volume)
            or mounts[0].get("Target") != API_CONFIG_DIR
            or mounts[0].get("ReadOnly") is not True
        ):
            raise ValidationFailure(stage)
        tmpfs = host_config.get("Tmpfs") or {}
        if set(tmpfs) != {"/tmp", API_LOG_DIR, "/opt/coriolis/export"}:
            raise ValidationFailure(stage)
        networks = (container.get("NetworkSettings") or {}).get("Networks") or {}
        if set(networks) != {self.resources.network}:
            raise ValidationFailure(stage)

    def _worker_service_uuid(self, stage: str) -> str:
        query = (
            "SELECT SHA2(id, 256) FROM coriolis.service "
            "WHERE host='coriolis-worker' AND `binary`='coriolis-worker' "
            "AND topic='coriolis_worker';"
        )
        try:
            result = self.runner(
                self._docker(
                    "exec",
                    self.resources.mariadb_main,
                    "mariadb",
                    f"--defaults-file={MARIADB_ADMIN_CNF_PATH}",
                    "--batch",
                    "--skip-column-names",
                    f"--execute={query}",
                ),
                self.timeout,
            )
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        rows = result.stdout.splitlines() if result.returncode == 0 else []
        if len(rows) != 1 or re.fullmatch(r"[0-9a-f]{64}", rows[0].strip()) is None:
            raise ValidationFailure(stage)
        return rows[0].strip()

    def _gate_api_unauthenticated(self) -> None:
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            try:
                result = self.runner(
                    self._docker(
                        "exec",
                        self.resources.api_main,
                        "python3",
                        "-c",
                        API_PROTOCOL_PROBE,
                    ),
                    self.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure("gate-api-unauthenticated") from None
            if not self._container_running(self.resources.api_main):
                raise ValidationFailure("api-container-exited")
            if result.returncode == 0:
                return
            self.sleeper(POLL_INTERVAL)
        raise ValidationFailure("gate-api-unauthenticated")

    def _gate_conductor_rpc(self, stage: str) -> None:
        self._poll_mounted_probe(
            stage, self.resources.rpc_probe, "coriolis_rpc_probe.py"
        )

    def _workload_stability(self) -> None:
        workloads = (
            (self.resources.conductor_main, "conductor-stability"),
            (self.resources.scheduler_main, "scheduler-stability"),
            (self.resources.transfer_cron_main, "transfer-cron-stability"),
            (self.resources.minion_manager_main, "minion-manager-stability"),
            (self.resources.deployer_manager_main, "deployer-manager-stability"),
            (self.resources.worker_main, "worker-stability"),
        )
        deadline = self.clock() + STABILITY_INTERVAL
        while self.clock() < deadline:
            for name, stage in workloads:
                if not self._container_running(name):
                    raise ValidationFailure(stage)
            self.sleeper(POLL_INTERVAL)
        for name, stage in workloads:
            if not self._container_running(name):
                raise ValidationFailure(stage)

    def _stop_workload_graceful(
        self, stage: str, name: str, stop_timeout_seconds: int
    ) -> None:
        self._checked(
            stage,
            self._docker("stop", "--time", str(stop_timeout_seconds), name),
        )
        try:
            result = self.runner(
                self._docker("inspect", "--format={{.State.ExitCode}}", name),
                self.timeout,
            )
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        exit_code = result.stdout.strip()
        if result.returncode != 0 or exit_code != "0":
            suffix = exit_code if exit_code.isdigit() else "unknown"
            raise ValidationFailure(f"{stage}-exit-{suffix}")

    def _stop_conductor_graceful(self) -> None:
        self._stop_workload_graceful(
            "stop-conductor-graceful",
            self.resources.conductor_main,
            CONDUCTOR_STOP_TIMEOUT,
        )

    def _stop_scheduler_graceful(self) -> None:
        self._stop_workload_graceful(
            "stop-scheduler-graceful",
            self.resources.scheduler_main,
            SCHEDULER_STOP_TIMEOUT,
        )

    def _stop_transfer_cron_graceful(self) -> None:
        self._stop_workload_graceful(
            "stop-transfer-cron-graceful", self.resources.transfer_cron_main, 15
        )

    def _stop_minion_manager_graceful(self) -> None:
        self._stop_workload_graceful(
            "stop-minion-manager-graceful", self.resources.minion_manager_main, 15
        )

    def _stop_deployer_manager_graceful(self) -> None:
        self._stop_workload_graceful(
            "stop-deployer-manager-graceful",
            self.resources.deployer_manager_main,
            DEPLOYER_MANAGER_STOP_TIMEOUT,
        )

    def _stop_worker_graceful(self) -> None:
        self._stop_workload_graceful(
            "stop-worker-graceful", self.resources.worker_main, WORKER_STOP_TIMEOUT
        )

    def _start_conductor_again(self) -> None:
        self._checked(
            "start-conductor-again",
            self._docker("start", self.resources.conductor_main),
        )

    def _start_scheduler_again(self) -> None:
        self._checked(
            "start-scheduler-again",
            self._docker("start", self.resources.scheduler_main),
        )

    def _start_transfer_cron_again(self) -> None:
        self._checked(
            "start-transfer-cron-again",
            self._docker("start", self.resources.transfer_cron_main),
        )

    def _start_minion_manager_again(self) -> None:
        self._checked(
            "start-minion-manager-again",
            self._docker("start", self.resources.minion_manager_main),
        )

    def _start_deployer_manager_again(self) -> None:
        self._checked(
            "start-deployer-manager-again",
            self._docker("start", self.resources.deployer_manager_main),
        )

    def _start_worker_again(self) -> None:
        self._checked(
            "start-worker-again", self._docker("start", self.resources.worker_main)
        )

    def _container_id(self, stage: str, name: str) -> str:
        try:
            result = self.runner(
                self._docker("inspect", "--format={{.Id}}", name), self.timeout
            )
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0 or not result.stdout.strip():
            raise ValidationFailure(stage)
        return result.stdout.strip()

    def _restart_rabbitmq(self) -> None:
        self._checked(
            "restart-rabbitmq", self._docker("restart", self.resources.rabbitmq_main)
        )

    def _rabbitmq_recovery_evidence(self) -> None:
        identities = (
            (self.resources.conductor_main, "conductor-identity"),
            (self.resources.scheduler_main, "scheduler-identity"),
            (self.resources.transfer_cron_main, "transfer-cron-identity"),
            (self.resources.minion_manager_main, "minion-manager-identity"),
            (self.resources.deployer_manager_main, "deployer-manager-identity"),
            (self.resources.worker_main, "worker-identity"),
        )
        recreated_stages = {
            self.resources.conductor_main: "conductor-recreated-on-rabbitmq-restart",
            self.resources.scheduler_main: "scheduler-recreated-on-rabbitmq-restart",
            self.resources.transfer_cron_main: (
                "transfer-cron-recreated-on-rabbitmq-restart"
            ),
            self.resources.minion_manager_main: (
                "minion-manager-recreated-on-rabbitmq-restart"
            ),
            self.resources.deployer_manager_main: (
                "deployer-manager-recreated-on-rabbitmq-restart"
            ),
            self.resources.worker_main: "worker-recreated-on-rabbitmq-restart",
        }
        before = {name: self._container_id(stage, name) for name, stage in identities}
        self._stage("restart-rabbitmq", self._restart_rabbitmq)
        self._stage(
            "gate-rabbitmq-protocol-recovery",
            lambda: self._poll_mounted_probe(
                "gate-rabbitmq-protocol-recovery",
                self.resources.rabbitmq_probe,
                "rabbitmq_probe.py",
            ),
        )
        self._stage(
            "gate-conductor-rpc-rabbitmq-recovery",
            lambda: self._gate_conductor_rpc("gate-conductor-rpc-rabbitmq-recovery"),
        )
        self._stage(
            "gate-messaging-probe-rabbitmq-recovery",
            lambda: self._poll_messaging_probe(
                "gate-messaging-probe-rabbitmq-recovery", expect_worker=True
            ),
        )
        self._stage(
            "gate-worker-probe-rabbitmq-recovery",
            lambda: self._poll_worker_probe("gate-worker-probe-rabbitmq-recovery"),
        )
        if self.worker_service_uuid != self._worker_service_uuid(
            "worker-service-identity-rabbitmq"
        ):
            raise ValidationFailure("worker-service-identity-rabbitmq")
        for name, stage in identities:
            if self._container_id(stage, name) != before[name]:
                raise ValidationFailure(recreated_stages[name])

    def _runtime_evidence(self) -> None:
        self._stage("start-conductor", self._start_conductor)
        self._stage("start-scheduler", self._start_scheduler)
        self._stage("start-transfer-cron", self._start_transfer_cron)
        self._stage("start-minion-manager", self._start_minion_manager)
        self._stage("start-deployer-manager", self._start_deployer_manager)
        self._stage("start-api", self._start_api)
        self._stage(
            "inspect-conductor",
            lambda: self._inspect_runtime(
                "inspect-conductor",
                self.resources.conductor_main,
                CONDUCTOR_IMAGE,
                CONDUCTOR_COMMAND[0],
                CONDUCTOR_COMMAND[1:],
                run_as_id=CONDUCTOR_RUN_AS_ID,
                writable_paths=("/tmp", API_LOG_DIR, API_LOCKS_DIR),
            ),
        )
        self._stage(
            "inspect-scheduler",
            lambda: self._inspect_runtime(
                "inspect-scheduler",
                self.resources.scheduler_main,
                SCHEDULER_IMAGE,
                SCHEDULER_COMMAND[0],
                SCHEDULER_COMMAND[1:],
                run_as_id=SCHEDULER_RUN_AS_ID,
                writable_paths=("/tmp", API_LOG_DIR),
            ),
        )
        self._stage(
            "inspect-transfer-cron",
            lambda: self._inspect_runtime(
                "inspect-transfer-cron",
                self.resources.transfer_cron_main,
                TRANSFER_CRON_IMAGE,
                TRANSFER_CRON_COMMAND[0],
                TRANSFER_CRON_COMMAND[1:],
                run_as_id=TRANSFER_CRON_RUN_AS_ID,
                writable_paths=("/tmp", API_LOG_DIR),
            ),
        )
        self._stage(
            "inspect-minion-manager",
            lambda: self._inspect_runtime(
                "inspect-minion-manager",
                self.resources.minion_manager_main,
                MINION_MANAGER_IMAGE,
                MINION_MANAGER_COMMAND[0],
                MINION_MANAGER_COMMAND[1:],
                run_as_id=MINION_MANAGER_RUN_AS_ID,
                writable_paths=("/tmp", API_LOG_DIR),
            ),
        )
        self._stage(
            "inspect-deployer-manager",
            lambda: self._inspect_runtime(
                "inspect-deployer-manager",
                self.resources.deployer_manager_main,
                DEPLOYER_MANAGER_IMAGE,
                DEPLOYER_MANAGER_COMMAND,
                DEPLOYER_MANAGER_ARGS,
                run_as_id=DEPLOYER_MANAGER_RUN_AS_ID,
                writable_paths=("/tmp", DEPLOYER_MANAGER_LOG_DIR),
            ),
        )
        self._stage(
            "inspect-api",
            lambda: self._inspect_runtime(
                "inspect-api",
                self.resources.api_main,
                API_IMAGE,
                API_COMMAND,
                API_ARGS,
                run_as_id=API_RUN_AS_ID,
                writable_paths=("/tmp", API_LOG_DIR, API_LOCKS_DIR),
            ),
        )
        self._stage("gate-api-unauthenticated", self._gate_api_unauthenticated)
        self._stage(
            "gate-conductor-rpc",
            lambda: self._gate_conductor_rpc("gate-conductor-rpc"),
        )
        self._stage(
            "gate-messaging-probe",
            lambda: self._poll_messaging_probe("gate-messaging-probe"),
        )
        self._stage("start-worker", self._start_worker)
        self._stage("inspect-worker", self._inspect_worker_runtime)
        self._stage(
            "gate-worker-probe",
            lambda: self._poll_worker_probe("gate-worker-probe"),
        )
        self.worker_service_uuid = self._worker_service_uuid("worker-service-identity")
        self._stage("workload-stability", self._workload_stability)
        self._stage(
            "gate-conductor-rpc-after-stability",
            lambda: self._gate_conductor_rpc("gate-conductor-rpc-after-stability"),
        )
        self._stage(
            "gate-messaging-probe-after-stability",
            lambda: self._poll_messaging_probe(
                "gate-messaging-probe-after-stability", expect_worker=True
            ),
        )
        self._shutdown_restart_evidence()
        if self.worker_service_uuid != self._worker_service_uuid(
            "worker-service-identity-restart"
        ):
            raise ValidationFailure("worker-service-identity-restart")
        self._stage(
            "gate-conductor-rpc-after-restart",
            lambda: self._gate_conductor_rpc("gate-conductor-rpc-after-restart"),
        )
        self._stage(
            "gate-messaging-probe-after-restart",
            lambda: self._poll_messaging_probe(
                "gate-messaging-probe-after-restart", expect_worker=True
            ),
        )
        self._stage(
            "gate-worker-probe-after-restart",
            lambda: self._poll_worker_probe("gate-worker-probe-after-restart"),
        )
        self._rabbitmq_recovery_evidence()

    def _shutdown_restart_evidence(self) -> None:
        self._stage(
            "stop-deployer-manager-graceful", self._stop_deployer_manager_graceful
        )
        self._stage("stop-worker-graceful", self._stop_worker_graceful)
        self._stage("stop-minion-manager-graceful", self._stop_minion_manager_graceful)
        self._stage("stop-transfer-cron-graceful", self._stop_transfer_cron_graceful)
        self._stage("stop-scheduler-graceful", self._stop_scheduler_graceful)
        self._stage("stop-conductor-graceful", self._stop_conductor_graceful)
        self._stage("start-conductor-again", self._start_conductor_again)
        self._stage("start-scheduler-again", self._start_scheduler_again)
        self._stage("start-transfer-cron-again", self._start_transfer_cron_again)
        self._stage("start-minion-manager-again", self._start_minion_manager_again)
        self._stage("start-deployer-manager-again", self._start_deployer_manager_again)
        self._stage("start-worker-again", self._start_worker_again)

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
            ("container", self.resources.conductor_main),
            ("container", self.resources.scheduler_main),
            ("container", self.resources.transfer_cron_main),
            ("container", self.resources.minion_manager_main),
            ("container", self.resources.deployer_manager_main),
            ("container", self.resources.worker_main),
            ("container", self.resources.api_main),
            ("container", self.resources.rpc_probe),
            ("container", self.resources.messaging_probe),
            ("container", self.resources.worker_probe),
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
            ("api-image-available", API_IMAGE),
            ("scheduler-image-available", SCHEDULER_IMAGE),
            ("transfer-cron-image-available", TRANSFER_CRON_IMAGE),
            ("minion-manager-image-available", MINION_MANAGER_IMAGE),
            ("worker-image-available", WORKER_IMAGE),
            ("deployer-manager-image-available", DEPLOYER_MANAGER_IMAGE),
            ("mariadb-image-available", MARIADB_IMAGE),
            ("keystone-image-available", KEYSTONE_IMAGE),
            ("rabbitmq-image-available", RABBITMQ_IMAGE),
            ("memcached-image-available", MEMCACHED_IMAGE),
        ):
            self._stage(
                stage,
                lambda stage=stage, image=image: self._checked(
                    stage, self._docker("image", "pull", image)
                ),
            )
        self._stage("conductor-image-contract", self._verify_conductor_image_contract)
        self._stage("scheduler-image-contract", self._verify_scheduler_image_contract)
        self._stage(
            "transfer-cron-image-contract", self._verify_transfer_cron_image_contract
        )
        self._stage(
            "minion-manager-image-contract", self._verify_minion_manager_image_contract
        )
        self._stage("worker-image-contract", self._verify_worker_image_contract)
        self._stage(
            "worker-provider-image-contract",
            self._verify_worker_provider_image_contract,
        )
        self._stage(
            "deployer-manager-image-contract",
            self._verify_deployer_manager_image_contract,
        )
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
        self._runtime_evidence()

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
