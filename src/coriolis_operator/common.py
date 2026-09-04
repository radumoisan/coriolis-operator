"""Pure Coriolis-common bootstrap constants and script rendering.

This module owns the exact pinned conductor image, the immutable bootstrap
revision, the numeric non-root identity, the projected credential file paths,
and the deterministic non-sensitive bootstrap-script renderer. It must never
interpolate a credential, DSN, token, or other sensitive value.
"""

from __future__ import annotations

import re

CONDUCTOR_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-conductor:2603.4"
    "@sha256:27495f44fbb8b320098d0aa04cd9dcb2a4b432e57aa17417606efc5403ac09c7"
)
BOOTSTRAP_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
BOOTSTRAP_REVISION = "v3"
BOOTSTRAP_UID_GID = 42434
BOOTSTRAP_COMPONENT = f"common-bootstrap-{BOOTSTRAP_REVISION}"
BOOTSTRAP_BACKOFF_LIMIT = 2
BOOTSTRAP_ACTIVE_DEADLINE_SECONDS = 600
BOOTSTRAP_DBSYNC_TIMEOUT_SECONDS = 120
BOOTSTRAP_TERMINATION_GRACE_PERIOD_SECONDS = 30

BOOTSTRAP_CONFIG_DIR = "/etc/coriolis"
BOOTSTRAP_CONFIG_PATH = f"{BOOTSTRAP_CONFIG_DIR}/coriolis.conf"
BOOTSTRAP_SCRIPT_DIR = "/etc/coriolis-bootstrap"
BOOTSTRAP_SCRIPT_FILENAME = "bootstrap.py"
BOOTSTRAP_SCRIPT_PATH = f"{BOOTSTRAP_SCRIPT_DIR}/{BOOTSTRAP_SCRIPT_FILENAME}"
BOOTSTRAP_INFRA_CREDENTIALS_DIR = "/etc/coriolis-bootstrap-infra"
BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR = "/etc/coriolis-bootstrap-coriolis"
BOOTSTRAP_BARBICAN_CREDENTIALS_DIR = "/etc/coriolis-bootstrap-barbican"
BOOTSTRAP_KEYSTONE_ADMIN_PASSWORD_PATH = (
    f"{BOOTSTRAP_INFRA_CREDENTIALS_DIR}/keystone-admin-password"
)
BOOTSTRAP_RABBITMQ_PASSWORD_PATH = (
    f"{BOOTSTRAP_INFRA_CREDENTIALS_DIR}/rabbitmq-password"
)
BOOTSTRAP_CORIOLIS_KEYSTONE_PASSWORD_PATH = (
    f"{BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR}/coriolis-keystone-password"
)
BOOTSTRAP_CORIOLIS_DATABASE_PASSWORD_PATH = (
    f"{BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR}/coriolis-database-password"
)
BOOTSTRAP_BARBICAN_KEYSTONE_PASSWORD_PATH = (
    f"{BOOTSTRAP_BARBICAN_CREDENTIALS_DIR}/barbican-keystone-password"
)
BOOTSTRAP_TEMPLATE_ANNOTATION = "coriolis.cloudbase.it/bootstrap-template-id"
BOOTSTRAP_SCRIPT_ANNOTATION = "coriolis.cloudbase.it/bootstrap-script-id"

_INVALID_HOST_MESSAGE = "invalid Coriolis-common bootstrap input"
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?")
_API_HOST_PLACEHOLDER = "__CORIOLIS_API_HOST__"
_BARBICAN_HOST_PLACEHOLDER = "__BARBICAN_HOST__"
_RABBITMQ_HOST_PLACEHOLDER = "__RABBITMQ_HOST__"
_MEMCACHED_HOST_PLACEHOLDER = "__MEMCACHED_HOST__"
_DATABASE_HOST_PLACEHOLDER = "__DATABASE_HOST__"
_KEYSTONE_HOST_PLACEHOLDER = "__KEYSTONE_HOST__"
_ADMIN_PASSWORD_PLACEHOLDER = "__KEYSTONE_ADMIN_PASSWORD_PATH__"
_RABBITMQ_PASSWORD_PLACEHOLDER = "__RABBITMQ_PASSWORD_PATH__"
_CORIOLIS_PASSWORD_PLACEHOLDER = "__CORIOLIS_KEYSTONE_PASSWORD_PATH__"
_DATABASE_PASSWORD_PLACEHOLDER = "__CORIOLIS_DATABASE_PASSWORD_PATH__"
_BARBICAN_PASSWORD_PLACEHOLDER = "__BARBICAN_KEYSTONE_PASSWORD_PATH__"
_DBSYNC_TIMEOUT_PLACEHOLDER = "__DBSYNC_TIMEOUT_SECONDS__"


def _validated_host(value: object) -> str:
    if type(value) is not str or not value:
        raise ValueError(_INVALID_HOST_MESSAGE)
    if len(value) > 63 or not _DNS_LABEL_RE.fullmatch(value):
        raise ValueError(_INVALID_HOST_MESSAGE)
    return value


_BOOTSTRAP_SCRIPT_TEMPLATE = """from pathlib import Path
import socket
import subprocess
import sys
import time

RABBITMQ_HOST = '__RABBITMQ_HOST__'
MEMCACHED_HOST = '__MEMCACHED_HOST__'
DATABASE_HOST = '__DATABASE_HOST__'
KEYSTONE_HOST = '__KEYSTONE_HOST__'
KEYSTONE_ADMIN_PASSWORD_PATH = '__KEYSTONE_ADMIN_PASSWORD_PATH__'
RABBITMQ_PASSWORD_PATH = '__RABBITMQ_PASSWORD_PATH__'
CORIOLIS_KEYSTONE_PASSWORD_PATH = '__CORIOLIS_KEYSTONE_PASSWORD_PATH__'
CORIOLIS_DATABASE_PASSWORD_PATH = '__CORIOLIS_DATABASE_PASSWORD_PATH__'
BARBICAN_KEYSTONE_PASSWORD_PATH = '__BARBICAN_KEYSTONE_PASSWORD_PATH__'
ENDPOINT_URL = 'http://__CORIOLIS_API_HOST__:7667/v1/%(tenant_id)s'
BARBICAN_ENDPOINT_URL = 'http://__BARBICAN_HOST__:9311'
KEYSTONE_AUTH_URL = 'http://' + KEYSTONE_HOST + ':5000/v3'
DEPENDENCY_DEADLINE_SECONDS = 300
DEPENDENCY_SLEEP_SECONDS = 2
DBSYNC_TIMEOUT_SECONDS = __DBSYNC_TIMEOUT_SECONDS__


def fail(marker):
    print(marker)
    sys.exit(1)


def gate_mariadb():
    import pymysql
    password = Path(CORIOLIS_DATABASE_PASSWORD_PATH).read_text().strip()
    db = pymysql.connect(
        host=DATABASE_HOST,
        port=3306,
        user='coriolis',
        password=password,
        database='coriolis',
        connect_timeout=10,
        read_timeout=10,
    )
    try:
        cursor = db.cursor()
        cursor.execute('SELECT 1')
        cursor.fetchone()
    finally:
        db.close()


def gate_rabbitmq():
    from kombu import Connection
    password = Path(RABBITMQ_PASSWORD_PATH).read_text().strip()
    connection = Connection(
        hostname=RABBITMQ_HOST,
        port=5672,
        userid='openstack',
        password=password,
        virtual_host='/',
        connect_timeout=10,
    )
    connection.connect()
    connection.release()


def gate_memcached():
    sock = socket.create_connection((MEMCACHED_HOST, 11211), timeout=10)
    try:
        sock.sendall(b'version\\r\\n')
        response = sock.recv(256)
        if not response.startswith(b'VERSION'):
            raise RuntimeError
        sock.sendall(b'set __coriolisprobe 0 60 3\\r\\nabc\\r\\n')
        sock.recv(256)
        sock.sendall(b'get __coriolisprobe\\r\\n')
        response = sock.recv(256)
        if b'VALUE __coriolisprobe' not in response:
            raise RuntimeError
    finally:
        sock.close()


def gate_keystone_admin():
    from keystoneauth1.identity import v3
    from keystoneauth1 import session as ks_session
    from keystoneclient.v3 import client as ks_client
    password = Path(KEYSTONE_ADMIN_PASSWORD_PATH).read_text().strip()
    auth = v3.Password(
        auth_url=KEYSTONE_AUTH_URL,
        username='admin',
        password=password,
        project_name='admin',
        user_domain_name='Default',
        project_domain_name='Default',
    )
    session = ks_session.Session(auth=auth, timeout=10)
    token = auth.get_token(session)
    if not token:
        raise RuntimeError
    return ks_client.Client(session=session)


def wait_for_dependencies():
    deadline = time.monotonic() + DEPENDENCY_DEADLINE_SECONDS
    while True:
        try:
            gate_mariadb()
            gate_rabbitmq()
            gate_memcached()
            return gate_keystone_admin()
        except Exception:
            if time.monotonic() >= deadline:
                fail('coriolis-bootstrap-failed')
            time.sleep(DEPENDENCY_SLEEP_SECONDS)


def run_dbsync():
    result = subprocess.run(
        [
            'coriolis-dbsync',
            '--config-file=/etc/coriolis/coriolis.conf',
            '--nouse-syslog',
            '--log-dir=',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=DBSYNC_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        fail('coriolis-bootstrap-failed')


def bootstrap_keystone(kc):
    default_domain = next((d for d in kc.domains.list() if d.name == 'Default'), None)
    if default_domain is None:
        fail('coriolis-bootstrap-failed')

    def unique(matches):
        if len(matches) > 1:
            fail('coriolis-bootstrap-failed')
        return matches[0] if matches else None

    def ensure_project(name):
        existing = unique([p for p in kc.projects.list(domain=default_domain.id)
            if p.name == name])
        if existing is not None:
            return existing
        return kc.projects.create(name=name, domain=default_domain.id)

    def ensure_service_user(name, password_path, service_project):
        password = Path(password_path).read_text().strip()
        existing = unique([u for u in kc.users.list(domain=default_domain.id)
            if u.name == name])
        if existing is None:
            return kc.users.create(
                name=name, password=password, domain=default_domain.id,
                enabled=True, default_project=service_project.id,
            )
        kc.users.update(existing.id, password=password, enabled=True,
            default_project=service_project.id)
        return kc.users.get(existing.id)

    def ensure_admin(user, service_project, admin_role):
        assigned = [g for g in kc.role_assignments.list(user=user.id,
            project=service_project.id) if g.role['id'] == admin_role.id]
        if not assigned:
            kc.roles.grant(role=admin_role.id, user=user.id,
                project=service_project.id)

    def ensure_service(name, service_type, description):
        existing = unique([s for s in kc.services.list() if s.name == name])
        if existing is None:
            return kc.services.create(name=name, type=service_type,
                description=description)
        kc.services.update(existing.id, name=name, type=service_type,
            description=description)
        return kc.services.get(existing.id)

    def ensure_endpoints(service, url):
        by_interface = {}
        for endpoint in kc.endpoints.list(service=service.id):
            by_interface.setdefault(endpoint.interface, []).append(endpoint)
        for interface in ('admin', 'internal', 'public'):
            existing = unique(by_interface.get(interface, []))
            if existing is None:
                kc.endpoints.create(service=service.id, interface=interface,
                    url=url, region='RegionOne')
            else:
                kc.endpoints.update(existing.id, service=service.id,
                    interface=interface, url=url, region='RegionOne')

    service_project = ensure_project('service')

    coriolis_user = ensure_service_user('coriolis',
        CORIOLIS_KEYSTONE_PASSWORD_PATH, service_project)
    barbican_user = ensure_service_user('barbican',
        BARBICAN_KEYSTONE_PASSWORD_PATH, service_project)

    admin_role = next((r for r in kc.roles.list() if r.name == 'admin'), None)
    if admin_role is None:
        fail('coriolis-bootstrap-failed')
    ensure_admin(coriolis_user, service_project, admin_role)
    ensure_admin(barbican_user, service_project, admin_role)

    for role_name in ('key-manager:service-admin', 'creator', 'observer', 'audit'):
        if unique([r for r in kc.roles.list() if r.name == role_name]) is None:
            kc.roles.create(name=role_name)

    migration_service = ensure_service('coriolis', 'migration',
        'Cloud Migration as a Service')
    key_manager_service = ensure_service('barbican', 'key-manager',
        'Barbican Key Management Service')
    ensure_endpoints(migration_service, ENDPOINT_URL)
    ensure_endpoints(key_manager_service, BARBICAN_ENDPOINT_URL)


def verify_keystone(kc):
    from keystoneauth1.identity import v3
    from keystoneauth1 import session as ks_session
    default_domain = next((d for d in kc.domains.list() if d.name == 'Default'), None)
    if default_domain is None:
        fail('coriolis-bootstrap-failed')
    users = [u for u in kc.users.list(domain=default_domain.id)
        if u.name == 'coriolis']
    if len(users) != 1 or not users[0].enabled:
        fail('coriolis-bootstrap-failed')
    coriolis_user = users[0]
    users = [u for u in kc.users.list(domain=default_domain.id)
        if u.name == 'barbican']
    if len(users) != 1 or not users[0].enabled:
        fail('coriolis-bootstrap-failed')
    barbican_user = users[0]
    projects = [p for p in kc.projects.list(domain=default_domain.id)
        if p.name == 'service']
    if len(projects) != 1:
        fail('coriolis-bootstrap-failed')
    service_project = projects[0]
    if coriolis_user.default_project_id != service_project.id:
        fail('coriolis-bootstrap-failed')
    if barbican_user.default_project_id != service_project.id:
        fail('coriolis-bootstrap-failed')
    admin_role = next((r for r in kc.roles.list() if r.name == 'admin'), None)
    if admin_role is None:
        fail('coriolis-bootstrap-failed')
    assignments = kc.role_assignments.list(user=coriolis_user.id,
        project=service_project.id)
    if not any(g.role['id'] == admin_role.id for g in assignments):
        fail('coriolis-bootstrap-failed')
    assignments = kc.role_assignments.list(user=barbican_user.id,
        project=service_project.id)
    if not any(g.role['id'] == admin_role.id for g in assignments):
        fail('coriolis-bootstrap-failed')
    for role_name in ('key-manager:service-admin', 'creator', 'observer', 'audit'):
        if len([r for r in kc.roles.list() if r.name == role_name]) != 1:
            fail('coriolis-bootstrap-failed')

    coriolis_password = Path(CORIOLIS_KEYSTONE_PASSWORD_PATH).read_text().strip()
    coriolis_auth = v3.Password(
        auth_url=KEYSTONE_AUTH_URL,
        username='coriolis',
        password=coriolis_password,
        project_name='service',
        user_domain_name='Default',
        project_domain_name='Default',
    )
    session = ks_session.Session(auth=coriolis_auth, timeout=10)
    coriolis_token = coriolis_auth.get_token(session)
    if not coriolis_token:
        fail('coriolis-bootstrap-failed')

    barbican_password = Path(BARBICAN_KEYSTONE_PASSWORD_PATH).read_text().strip()
    barbican_auth = v3.Password(
        auth_url=KEYSTONE_AUTH_URL,
        username='barbican',
        password=barbican_password,
        project_name='service',
        user_domain_name='Default',
        project_domain_name='Default',
    )
    session = ks_session.Session(auth=barbican_auth, timeout=10)
    barbican_token = barbican_auth.get_token(session)
    if not barbican_token:
        fail('coriolis-bootstrap-failed')

    services = [s for s in kc.services.list() if s.name == 'coriolis']
    if len(services) != 1:
        fail('coriolis-bootstrap-failed')
    migration_service = services[0]
    if migration_service.type != 'migration':
        fail('coriolis-bootstrap-failed')
    if migration_service.description != 'Cloud Migration as a Service':
        fail('coriolis-bootstrap-failed')

    by_interface = {}
    for endpoint in kc.endpoints.list(service=migration_service.id):
        by_interface.setdefault(endpoint.interface, []).append(endpoint)
    if set(by_interface) != {'admin', 'internal', 'public'}:
        fail('coriolis-bootstrap-failed')
    for interface, endpoints in by_interface.items():
        if len(endpoints) != 1:
            fail('coriolis-bootstrap-failed')
        endpoint = endpoints[0]
        if endpoint.region != 'RegionOne':
            fail('coriolis-bootstrap-failed')
        if endpoint.url != ENDPOINT_URL:
            fail('coriolis-bootstrap-failed')

    services = [s for s in kc.services.list() if s.name == 'barbican']
    if len(services) != 1:
        fail('coriolis-bootstrap-failed')
    key_manager_service = services[0]
    if key_manager_service.type != 'key-manager':
        fail('coriolis-bootstrap-failed')
    if key_manager_service.description != 'Barbican Key Management Service':
        fail('coriolis-bootstrap-failed')

    by_interface = {}
    for endpoint in kc.endpoints.list(service=key_manager_service.id):
        by_interface.setdefault(endpoint.interface, []).append(endpoint)
    if set(by_interface) != {'admin', 'internal', 'public'}:
        fail('coriolis-bootstrap-failed')
    for interface, endpoints in by_interface.items():
        if len(endpoints) != 1:
            fail('coriolis-bootstrap-failed')
        endpoint = endpoints[0]
        if endpoint.region != 'RegionOne':
            fail('coriolis-bootstrap-failed')
        if endpoint.url != BARBICAN_ENDPOINT_URL:
            fail('coriolis-bootstrap-failed')


def main():
    kc = wait_for_dependencies()
    print('coriolis-bootstrap-dependencies-ok')
    run_dbsync()
    print('coriolis-bootstrap-dbsync-ok')
    bootstrap_keystone(kc)
    print('coriolis-bootstrap-converged')
    verify_keystone(kc)
    print('coriolis-bootstrap-verified')


try:
    main()
except SystemExit:
    raise
except Exception:
    fail('coriolis-bootstrap-failed')
"""


def render_bootstrap_script(
    *,
    coriolis_api_host: object,
    barbican_host: object,
    rabbitmq_host: object,
    memcached_host: object,
    database_host: object,
    keystone_host: object,
) -> str:
    """Return the deterministic non-sensitive Coriolis-common bootstrap script."""
    return (
        _BOOTSTRAP_SCRIPT_TEMPLATE.replace(
            _API_HOST_PLACEHOLDER, _validated_host(coriolis_api_host)
        )
        .replace(_BARBICAN_HOST_PLACEHOLDER, _validated_host(barbican_host))
        .replace(_RABBITMQ_HOST_PLACEHOLDER, _validated_host(rabbitmq_host))
        .replace(_MEMCACHED_HOST_PLACEHOLDER, _validated_host(memcached_host))
        .replace(_DATABASE_HOST_PLACEHOLDER, _validated_host(database_host))
        .replace(_KEYSTONE_HOST_PLACEHOLDER, _validated_host(keystone_host))
        .replace(_ADMIN_PASSWORD_PLACEHOLDER, BOOTSTRAP_KEYSTONE_ADMIN_PASSWORD_PATH)
        .replace(_RABBITMQ_PASSWORD_PLACEHOLDER, BOOTSTRAP_RABBITMQ_PASSWORD_PATH)
        .replace(
            _CORIOLIS_PASSWORD_PLACEHOLDER, BOOTSTRAP_CORIOLIS_KEYSTONE_PASSWORD_PATH
        )
        .replace(
            _DATABASE_PASSWORD_PLACEHOLDER,
            BOOTSTRAP_CORIOLIS_DATABASE_PASSWORD_PATH,
        )
        .replace(
            _BARBICAN_PASSWORD_PLACEHOLDER, BOOTSTRAP_BARBICAN_KEYSTONE_PASSWORD_PATH
        )
        .replace(_DBSYNC_TIMEOUT_PLACEHOLDER, str(BOOTSTRAP_DBSYNC_TIMEOUT_SECONDS))
    )
