import ast
import itertools
import sys
import types

import pytest

from coriolis_operator.common import (
    BOOTSTRAP_ACTIVE_DEADLINE_SECONDS,
    BOOTSTRAP_BACKOFF_LIMIT,
    BOOTSTRAP_BARBICAN_CREDENTIALS_DIR,
    BOOTSTRAP_BARBICAN_KEYSTONE_PASSWORD_PATH,
    BOOTSTRAP_COMPONENT,
    BOOTSTRAP_CONFIG_DIR,
    BOOTSTRAP_CONFIG_PATH,
    BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR,
    BOOTSTRAP_CORIOLIS_DATABASE_PASSWORD_PATH,
    BOOTSTRAP_CORIOLIS_KEYSTONE_PASSWORD_PATH,
    BOOTSTRAP_DBSYNC_TIMEOUT_SECONDS,
    BOOTSTRAP_IMAGE_PULL_SECRET_NAME,
    BOOTSTRAP_INFRA_CREDENTIALS_DIR,
    BOOTSTRAP_KEYSTONE_ADMIN_PASSWORD_PATH,
    BOOTSTRAP_RABBITMQ_PASSWORD_PATH,
    BOOTSTRAP_REVISION,
    BOOTSTRAP_SCRIPT_DIR,
    BOOTSTRAP_SCRIPT_FILENAME,
    BOOTSTRAP_SCRIPT_PATH,
    BOOTSTRAP_TERMINATION_GRACE_PERIOD_SECONDS,
    BOOTSTRAP_UID_GID,
    CONDUCTOR_IMAGE,
    render_bootstrap_script,
)

EXACT_CONDUCTOR_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-conductor:2603.4"
    "@sha256:27495f44fbb8b320098d0aa04cd9dcb2a4b432e57aa17417606efc5403ac09c7"
)

CORIOLIS_KEYSTONE_PASSWORD_SENTINEL = "coriolis-keystone-password-sentinel"
BARBICAN_KEYSTONE_PASSWORD_SENTINEL = "barbican-keystone-password-sentinel"

PASSWORD_FILE_SENTINELS = {
    BOOTSTRAP_CORIOLIS_KEYSTONE_PASSWORD_PATH: CORIOLIS_KEYSTONE_PASSWORD_SENTINEL,
    BOOTSTRAP_BARBICAN_KEYSTONE_PASSWORD_PATH: BARBICAN_KEYSTONE_PASSWORD_SENTINEL,
}

COMPATIBILITY_ROLE_NAMES = (
    "key-manager:service-admin",
    "creator",
    "observer",
    "audit",
)


def render(
    coriolis_api_host: str = "example-coriolis-api",
    barbican_host: str = "example-barbican",
    rabbitmq_host: str = "example-rabbitmq",
    memcached_host: str = "example-memcached",
    database_host: str = "example-mariadb",
    keystone_host: str = "example-keystone",
) -> str:
    return render_bootstrap_script(
        coriolis_api_host=coriolis_api_host,
        barbican_host=barbican_host,
        rabbitmq_host=rabbitmq_host,
        memcached_host=memcached_host,
        database_host=database_host,
        keystone_host=keystone_host,
    )


def extract_function(script: str, name: str, globals_: dict | None = None):
    tree = ast.parse(script)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            module = ast.Module(body=[node], type_ignores=[])
            namespace = dict(globals_ or {})
            exec(compile(module, f"<{name}>", "exec"), namespace)
            return namespace[name]
    raise AssertionError(f"function {name} not found in generated script")


def module_constant_globals(script: str) -> dict:
    tree = ast.parse(script)
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)]
    namespace: dict = {}
    exec(
        compile(ast.Module(body=assignments, type_ignores=[]), "<constants>", "exec"),
        namespace,
    )
    return namespace


class FakePath:
    def __init__(self, value) -> None:
        self.value = str(value)

    def read_text(self) -> str:
        try:
            return PASSWORD_FILE_SENTINELS[self.value]
        except KeyError:
            raise AssertionError(f"unexpected password path read: {self.value}")


class FakeResource:
    def __init__(self, id_, **attrs) -> None:
        self.id = id_
        self.__dict__.update(attrs)

    def __repr__(self) -> str:
        return f"<FakeResource id={self.id!r} name={getattr(self, 'name', None)!r}>"


class _Domains:
    def __init__(self, kc) -> None:
        self._kc = kc

    def list(self):
        return [self._kc.domain]


class _Projects:
    def __init__(self, kc) -> None:
        self._kc = kc

    def list(self, domain=None):
        return (
            [p for p in self._kc.all_projects if p.domain_id == domain]
            if domain is not None
            else list(self._kc.all_projects)
        )

    def create(self, name, domain):
        project = FakeResource(self._kc.next_id(), name=name, domain_id=domain)
        self._kc.all_projects.append(project)
        return project


class _Users:
    def __init__(self, kc) -> None:
        self._kc = kc

    def _find(self, id_):
        return next(u for u in self._kc.all_users if u.id == id_)

    def list(self, domain=None):
        return (
            [u for u in self._kc.all_users if u.domain_id == domain]
            if domain is not None
            else list(self._kc.all_users)
        )

    def create(self, name, password, domain, enabled, default_project):
        user = FakeResource(
            self._kc.next_id(),
            name=name,
            domain_id=domain,
            enabled=enabled,
            default_project_id=default_project,
            password=password,
        )
        self._kc.all_users.append(user)
        return user

    def update(self, id_, **attrs):
        user = self._find(id_)
        self._kc.update_calls.append(("user", id_))
        if "password" in attrs:
            user.password = attrs["password"]
        if "enabled" in attrs:
            user.enabled = attrs["enabled"]
        if "default_project" in attrs:
            user.default_project_id = attrs["default_project"]

    def get(self, id_):
        return self._find(id_)


class _Roles:
    def __init__(self, kc) -> None:
        self._kc = kc

    def list(self):
        return list(self._kc.all_roles)

    def create(self, name):
        role = FakeResource(self._kc.next_id(), name=name)
        self._kc.all_roles.append(role)
        return role

    def grant(self, role, user, project):
        self._kc.all_assignments.append(
            FakeResource(
                self._kc.next_id(),
                role={"id": role},
                user={"id": user},
                project={"id": project},
            )
        )


class _RoleAssignments:
    def __init__(self, kc) -> None:
        self._kc = kc

    def list(self, user=None, project=None):
        matches = list(self._kc.all_assignments)
        if user is not None:
            matches = [a for a in matches if a.user["id"] == user]
        if project is not None:
            matches = [a for a in matches if a.project["id"] == project]
        return matches


class _Services:
    def __init__(self, kc) -> None:
        self._kc = kc

    def _find(self, id_):
        return next(s for s in self._kc.all_services if s.id == id_)

    def list(self):
        return list(self._kc.all_services)

    def create(self, name, type, description):
        service = FakeResource(
            self._kc.next_id(), name=name, type=type, description=description
        )
        self._kc.all_services.append(service)
        return service

    def update(self, id_, **attrs):
        service = self._find(id_)
        self._kc.update_calls.append(("service", id_))
        if "name" in attrs:
            service.name = attrs["name"]
        if "type" in attrs:
            service.type = attrs["type"]
        if "description" in attrs:
            service.description = attrs["description"]

    def get(self, id_):
        return self._find(id_)


class _Endpoints:
    def __init__(self, kc) -> None:
        self._kc = kc

    def _find(self, id_):
        return next(e for e in self._kc.all_endpoints if e.id == id_)

    def list(self, service=None):
        return (
            [e for e in self._kc.all_endpoints if e.service_id == service]
            if service is not None
            else list(self._kc.all_endpoints)
        )

    def create(self, service, interface, url, region):
        endpoint = FakeResource(
            self._kc.next_id(),
            service_id=service,
            interface=interface,
            url=url,
            region=region,
        )
        self._kc.all_endpoints.append(endpoint)
        return endpoint

    def update(self, id_, **attrs):
        endpoint = self._find(id_)
        self._kc.update_calls.append(("endpoint", id_))
        if "service" in attrs:
            endpoint.service_id = attrs["service"]
        if "interface" in attrs:
            endpoint.interface = attrs["interface"]
        if "url" in attrs:
            endpoint.url = attrs["url"]
        if "region" in attrs:
            endpoint.region = attrs["region"]


class FakeKeystone:
    def __init__(self) -> None:
        self.domain = FakeResource("domain-default", name="Default")
        self.admin_role = FakeResource("role-admin", name="admin")
        self.all_projects = []
        self.all_users = []
        self.all_roles = [self.admin_role]
        self.all_assignments = []
        self.all_services = []
        self.all_endpoints = []
        self.update_calls = []
        self._ids = itertools.count(1000)
        self.domains = _Domains(self)
        self.projects = _Projects(self)
        self.users = _Users(self)
        self.roles = _Roles(self)
        self.role_assignments = _RoleAssignments(self)
        self.services = _Services(self)
        self.endpoints = _Endpoints(self)

    def next_id(self) -> str:
        return f"fake-{next(self._ids)}"


def named(items, name):
    return [item for item in items if item.name == name]


def keystone_script_env():
    script = render()
    markers = []

    def fail(marker):
        markers.append(marker)
        raise SystemExit(1)

    globals_ = {
        **module_constant_globals(script),
        "fail": fail,
        "Path": FakePath,
    }
    return script, globals_, markers


def run_bootstrap_keystone(kc) -> list:
    script, globals_, markers = keystone_script_env()
    extract_function(script, "bootstrap_keystone", globals_)(kc)
    return markers


def run_verify_keystone(kc) -> list:
    script, globals_, markers = keystone_script_env()
    extract_function(script, "verify_keystone", globals_)(kc)
    return markers


def run_keystone_function_expect_failure(function_name: str, kc) -> list:
    script, globals_, markers = keystone_script_env()
    with pytest.raises(SystemExit):
        extract_function(script, function_name, globals_)(kc)
    return markers


def install_fake_keystoneauth(monkeypatch, token="fake-keystone-token"):
    state = {"token": token}

    class Password:
        instances = []

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            Password.instances.append(self)

        def get_token(self, session):
            return state["token"]

    class Session:
        instances = []

        def __init__(self, auth, timeout) -> None:
            self.auth = auth
            self.timeout = timeout
            Session.instances.append(self)

    root = types.ModuleType("keystoneauth1")
    identity = types.ModuleType("keystoneauth1.identity")
    v3 = types.ModuleType("keystoneauth1.identity.v3")
    session_module = types.ModuleType("keystoneauth1.session")
    v3.Password = Password
    identity.v3 = v3
    root.identity = identity
    root.session = session_module
    session_module.Session = Session
    monkeypatch.setitem(sys.modules, "keystoneauth1", root)
    monkeypatch.setitem(sys.modules, "keystoneauth1.identity", identity)
    monkeypatch.setitem(sys.modules, "keystoneauth1.identity.v3", v3)
    monkeypatch.setitem(sys.modules, "keystoneauth1.session", session_module)
    Password.instances.clear()
    Session.instances.clear()
    return {"state": state, "Password": Password, "Session": Session}


def test_common_bootstrap_constants_are_exact() -> None:
    assert CONDUCTOR_IMAGE == EXACT_CONDUCTOR_IMAGE
    assert BOOTSTRAP_REVISION == "v3"
    assert BOOTSTRAP_UID_GID == 42434
    assert BOOTSTRAP_COMPONENT == "common-bootstrap-v3"
    assert BOOTSTRAP_COMPONENT == f"common-bootstrap-{BOOTSTRAP_REVISION}"
    assert BOOTSTRAP_IMAGE_PULL_SECRET_NAME == "coriolis-appliance-registry"
    assert BOOTSTRAP_BACKOFF_LIMIT == 2
    assert BOOTSTRAP_ACTIVE_DEADLINE_SECONDS == 600
    assert BOOTSTRAP_DBSYNC_TIMEOUT_SECONDS == 120
    assert BOOTSTRAP_TERMINATION_GRACE_PERIOD_SECONDS == 30


def test_common_bootstrap_paths_are_exact() -> None:
    assert BOOTSTRAP_CONFIG_PATH == "/etc/coriolis/coriolis.conf"
    assert BOOTSTRAP_SCRIPT_FILENAME == "bootstrap.py"
    assert BOOTSTRAP_SCRIPT_PATH == "/etc/coriolis-bootstrap/bootstrap.py"
    assert BOOTSTRAP_INFRA_CREDENTIALS_DIR == "/etc/coriolis-bootstrap-infra"
    assert BOOTSTRAP_KEYSTONE_ADMIN_PASSWORD_PATH == (
        "/etc/coriolis-bootstrap-infra/keystone-admin-password"
    )
    assert BOOTSTRAP_RABBITMQ_PASSWORD_PATH == (
        "/etc/coriolis-bootstrap-infra/rabbitmq-password"
    )
    assert BOOTSTRAP_CORIOLIS_KEYSTONE_PASSWORD_PATH == (
        "/etc/coriolis-bootstrap-coriolis/coriolis-keystone-password"
    )
    assert BOOTSTRAP_CORIOLIS_DATABASE_PASSWORD_PATH == (
        "/etc/coriolis-bootstrap-coriolis/coriolis-database-password"
    )
    assert BOOTSTRAP_BARBICAN_KEYSTONE_PASSWORD_PATH == (
        "/etc/coriolis-bootstrap-barbican/barbican-keystone-password"
    )


def test_common_bootstrap_mount_paths_are_top_level_and_non_overlapping() -> None:
    paths = {
        "/etc/coriolis",
        "/etc/coriolis-bootstrap",
        "/etc/coriolis-bootstrap-infra",
        "/etc/coriolis-bootstrap-coriolis",
        "/etc/coriolis-bootstrap-barbican",
        "/tmp",
    }
    assert len(paths) == 6
    assert BOOTSTRAP_CONFIG_DIR == "/etc/coriolis"
    assert BOOTSTRAP_SCRIPT_DIR == "/etc/coriolis-bootstrap"
    assert BOOTSTRAP_INFRA_CREDENTIALS_DIR == "/etc/coriolis-bootstrap-infra"
    assert BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR == "/etc/coriolis-bootstrap-coriolis"
    assert BOOTSTRAP_BARBICAN_CREDENTIALS_DIR == "/etc/coriolis-bootstrap-barbican"
    for path in paths:
        assert not any(
            path != other and other.startswith(path + "/") for other in paths
        ), f"nested mount path: {path}"


def test_render_bootstrap_script_is_deterministic_with_exact_api_dns() -> None:
    assert render() == render()
    assert (
        "ENDPOINT_URL = 'http://example-coriolis-api:7667/v1/%(tenant_id)s'" in render()
    )
    assert "http://example-coriolis-api:7667/v1/%(tenant_id)s" in render()


def test_render_bootstrap_script_embeds_exact_barbican_internal_dns() -> None:
    assert "BARBICAN_ENDPOINT_URL = 'http://example-barbican:9311'" in render()
    assert "BARBICAN_ENDPOINT_URL = 'http://barbican-cluster-ip:9311'" in render(
        barbican_host="barbican-cluster-ip"
    )
    assert render() != render(barbican_host="other-barbican")


def test_render_bootstrap_script_embeds_dependency_hosts_and_paths() -> None:
    script = render()

    assert "RABBITMQ_HOST = 'example-rabbitmq'" in script
    assert "MEMCACHED_HOST = 'example-memcached'" in script
    assert "DATABASE_HOST = 'example-mariadb'" in script
    assert "KEYSTONE_HOST = 'example-keystone'" in script
    assert "KEYSTONE_AUTH_URL = 'http://' + KEYSTONE_HOST + ':5000/v3'" in script
    assert (
        "KEYSTONE_ADMIN_PASSWORD_PATH = "
        "'/etc/coriolis-bootstrap-infra/keystone-admin-password'" in script
    )
    assert (
        "RABBITMQ_PASSWORD_PATH = "
        "'/etc/coriolis-bootstrap-infra/rabbitmq-password'" in script
    )
    assert (
        "CORIOLIS_KEYSTONE_PASSWORD_PATH = "
        "'/etc/coriolis-bootstrap-coriolis/coriolis-keystone-password'" in script
    )
    assert (
        "CORIOLIS_DATABASE_PASSWORD_PATH = "
        "'/etc/coriolis-bootstrap-coriolis/coriolis-database-password'" in script
    )
    assert (
        "BARBICAN_KEYSTONE_PASSWORD_PATH = "
        "'/etc/coriolis-bootstrap-barbican/barbican-keystone-password'" in script
    )
    assert "'--config-file=/etc/coriolis/coriolis.conf'," in script
    assert "'--nouse-syslog'," in script
    assert "'--log-dir='," in script
    assert "stdout=subprocess.DEVNULL" in script
    assert "stderr=subprocess.DEVNULL" in script
    assert "DBSYNC_TIMEOUT_SECONDS = 120" in script
    assert "timeout=DBSYNC_TIMEOUT_SECONDS" in script
    assert "timeout=600" not in script


def test_render_bootstrap_script_gives_service_sessions_explicit_timeouts() -> None:
    script = render()
    assert "session = ks_session.Session(auth=coriolis_auth, timeout=10)" in script
    assert "session = ks_session.Session(auth=barbican_auth, timeout=10)" in script
    assert "session = ks_session.Session(auth=auth, timeout=10)" in script


def test_render_bootstrap_script_has_no_sensitive_or_raw_output_paths() -> None:
    script = render()
    for forbidden in (
        "sys.stdout",
        "sys.stderr",
        "stderr.write",
        "stdout.write",
        "traceback",
        "print(e)",
        "except Exception as e",
        "print((",
    ):
        assert forbidden not in script


def test_render_bootstrap_script_has_no_placeholder_or_secret_interpolation() -> None:
    script = render()

    for placeholder in (
        "__CORIOLIS_API_HOST__",
        "__BARBICAN_HOST__",
        "__RABBITMQ_HOST__",
        "__MEMCACHED_HOST__",
        "__DATABASE_HOST__",
        "__KEYSTONE_HOST__",
        "__KEYSTONE_ADMIN_PASSWORD_PATH__",
        "__RABBITMQ_PASSWORD_PATH__",
        "__CORIOLIS_KEYSTONE_PASSWORD_PATH__",
        "__CORIOLIS_DATABASE_PASSWORD_PATH__",
        "__BARBICAN_KEYSTONE_PASSWORD_PATH__",
    ):
        assert placeholder not in script

    for sentinel in (
        CORIOLIS_KEYSTONE_PASSWORD_SENTINEL,
        BARBICAN_KEYSTONE_PASSWORD_SENTINEL,
    ):
        assert sentinel not in script


def test_render_bootstrap_script_is_valid_python() -> None:
    compile(render(), "bootstrap.py", "exec")


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "UPPER",
        "a/b",
        "a@b",
        "a:b",
        "a[?b#c",
        'a"b',
        "a\\b",
        "a b",
        "café",
        "a.b",
        "a_b",
        "-start",
        "end-",
        "a" * 64,
        "__CORIOLIS_API_HOST__",
        "__BARBICAN_HOST__",
    ],
)
def test_render_bootstrap_script_rejects_invalid_hosts_without_leaking(
    invalid: str,
) -> None:
    with pytest.raises(ValueError) as excinfo:
        render(coriolis_api_host=invalid)
    assert str(excinfo.value) == "invalid Coriolis-common bootstrap input"
    if invalid:
        assert invalid not in str(excinfo.value)


@pytest.mark.parametrize(
    "host_argument",
    [
        "coriolis_api_host",
        "barbican_host",
        "rabbitmq_host",
        "memcached_host",
        "database_host",
        "keystone_host",
    ],
)
def test_render_bootstrap_script_rejects_invalid_host_for_every_argument(
    host_argument: str,
) -> None:
    with pytest.raises(ValueError) as excinfo:
        render(**{host_argument: "bad.host"})
    assert str(excinfo.value) == "invalid Coriolis-common bootstrap input"
    assert "bad.host" not in str(excinfo.value)


@pytest.mark.parametrize("valid", ["example", "example-rabbitmq", "a1b2c3", "a" * 63])
def test_render_bootstrap_script_accepts_single_dns_labels(valid: str) -> None:
    script = render(coriolis_api_host=valid)
    assert valid in script


@pytest.mark.parametrize("valid", ["example-barbican", "a1b2c3", "a" * 63])
def test_render_bootstrap_script_accepts_valid_barbican_hosts(valid: str) -> None:
    script = render(barbican_host=valid)
    assert f"BARBICAN_ENDPOINT_URL = 'http://{valid}:9311'" in script


def test_render_bootstrap_script_emits_only_fixed_markers() -> None:
    script = render()
    success_markers = (
        "coriolis-bootstrap-dependencies-ok",
        "coriolis-bootstrap-dbsync-ok",
        "coriolis-bootstrap-converged",
        "coriolis-bootstrap-verified",
    )
    for marker in success_markers:
        assert f"print('{marker}')" in script
    assert "fail('coriolis-bootstrap-failed')" in script


def test_bootstrap_keystone_creates_full_coriolis_and_barbican_state() -> None:
    kc = FakeKeystone()
    assert run_bootstrap_keystone(kc) == []

    assert len(named(kc.all_projects, "service")) == 1
    service_project = named(kc.all_projects, "service")[0]
    assert service_project.domain_id == kc.domain.id

    assert len(named(kc.all_users, "coriolis")) == 1
    assert len(named(kc.all_users, "barbican")) == 1
    coriolis_user = named(kc.all_users, "coriolis")[0]
    barbican_user = named(kc.all_users, "barbican")[0]
    assert coriolis_user.enabled is True
    assert barbican_user.enabled is True
    assert coriolis_user.default_project_id == service_project.id
    assert barbican_user.default_project_id == service_project.id
    assert coriolis_user.password == CORIOLIS_KEYSTONE_PASSWORD_SENTINEL
    assert barbican_user.password == BARBICAN_KEYSTONE_PASSWORD_SENTINEL

    admin_grants = [a for a in kc.all_assignments if a.role["id"] == kc.admin_role.id]
    granted_users = {a.user["id"] for a in admin_grants}
    assert granted_users == {coriolis_user.id, barbican_user.id}
    assert all(a.project["id"] == service_project.id for a in admin_grants)

    for role_name in COMPATIBILITY_ROLE_NAMES:
        assert len(named(kc.all_roles, role_name)) == 1

    migration_services = named(kc.all_services, "coriolis")
    key_manager_services = named(kc.all_services, "barbican")
    assert len(migration_services) == 1
    assert len(key_manager_services) == 1
    assert migration_services[0].type == "migration"
    assert migration_services[0].description == "Cloud Migration as a Service"
    assert key_manager_services[0].type == "key-manager"
    assert key_manager_services[0].description == "Barbican Key Management Service"

    script_globals = module_constant_globals(render())
    for service, url in (
        (migration_services[0], script_globals["ENDPOINT_URL"]),
        (key_manager_services[0], script_globals["BARBICAN_ENDPOINT_URL"]),
    ):
        endpoints = kc.endpoints.list(service=service.id)
        assert {e.interface for e in endpoints} == {"admin", "internal", "public"}
        assert len(endpoints) == 3
        for endpoint in endpoints:
            assert endpoint.url == url
            assert endpoint.region == "RegionOne"

    assert kc.update_calls == []


def test_bootstrap_keystone_is_idempotent_on_second_run() -> None:
    kc = FakeKeystone()
    assert run_bootstrap_keystone(kc) == []
    state = {
        "projects": list(kc.all_projects),
        "users": list(kc.all_users),
        "roles": list(kc.all_roles),
        "assignments": list(kc.all_assignments),
        "services": list(kc.all_services),
        "endpoints": list(kc.all_endpoints),
    }
    kc.update_calls.clear()

    assert run_bootstrap_keystone(kc) == []

    assert kc.all_projects == state["projects"]
    assert kc.all_users == state["users"]
    assert kc.all_roles == state["roles"]
    assert kc.all_assignments == state["assignments"]
    assert kc.all_services == state["services"]
    assert kc.all_endpoints == state["endpoints"]
    updated_kinds = {kind for kind, _ in kc.update_calls}
    assert {"user", "service", "endpoint"} <= updated_kinds


@pytest.mark.parametrize("duplicate_kind", ["barbican_user", "creator_role"])
def test_bootstrap_keystone_rejects_duplicate_state(duplicate_kind: str) -> None:
    kc = FakeKeystone()
    if duplicate_kind == "barbican_user":
        for _ in range(2):
            kc.users.create(
                name="barbican",
                password="stale",
                domain=kc.domain.id,
                enabled=True,
                default_project="other-project",
            )
    else:
        kc.roles.create(name="creator")
        kc.roles.create(name="creator")

    markers = run_keystone_function_expect_failure("bootstrap_keystone", kc)
    assert markers == ["coriolis-bootstrap-failed"]


def test_bootstrap_keystone_repairs_drift_and_verify_accepts(monkeypatch) -> None:
    kc = FakeKeystone()
    assert run_bootstrap_keystone(kc) == []

    barbican_user = named(kc.all_users, "barbican")[0]
    barbican_user.enabled = False
    barbican_user.default_project_id = "wrong-project"
    key_manager_service = named(kc.all_services, "barbican")[0]
    key_manager_service.type = "wrong-type"
    kc.all_endpoints = [
        e
        for e in kc.all_endpoints
        if not (e.service_id == key_manager_service.id and e.interface == "public")
    ]
    creator = named(kc.all_roles, "creator")[0]
    kc.all_roles.remove(creator)

    install_fake_keystoneauth(monkeypatch)
    markers = run_keystone_function_expect_failure("verify_keystone", kc)
    assert markers == ["coriolis-bootstrap-failed"]

    assert run_bootstrap_keystone(kc) == []
    assert run_verify_keystone(kc) == []


def test_verify_keystone_accepts_bootstrapped_state(monkeypatch) -> None:
    kc = FakeKeystone()
    assert run_bootstrap_keystone(kc) == []

    fakes = install_fake_keystoneauth(monkeypatch)
    assert run_verify_keystone(kc) == []

    passwords = fakes["Password"].instances
    assert [p.kwargs["username"] for p in passwords] == ["coriolis", "barbican"]
    assert passwords[0].kwargs["password"] == CORIOLIS_KEYSTONE_PASSWORD_SENTINEL
    assert passwords[1].kwargs["password"] == BARBICAN_KEYSTONE_PASSWORD_SENTINEL
    for password_auth in passwords:
        assert password_auth.kwargs["auth_url"] == "http://example-keystone:5000/v3"
        assert password_auth.kwargs["project_name"] == "service"
        assert password_auth.kwargs["user_domain_name"] == "Default"
        assert password_auth.kwargs["project_domain_name"] == "Default"
    assert all(s.timeout == 10 for s in fakes["Session"].instances)
    assert len(fakes["Session"].instances) == 2


@pytest.mark.parametrize("failure_case", ["coriolis_disabled", "duplicate_endpoint"])
def test_verify_keystone_fails_closed_with_fixed_marker(
    monkeypatch, failure_case: str
) -> None:
    kc = FakeKeystone()
    assert run_bootstrap_keystone(kc) == []

    if failure_case == "coriolis_disabled":
        named(kc.all_users, "coriolis")[0].enabled = False
    else:
        key_manager_service = named(kc.all_services, "barbican")[0]
        kc.endpoints.create(
            service=key_manager_service.id,
            interface="public",
            url="http://example-barbican:9311",
            region="RegionOne",
        )

    install_fake_keystoneauth(monkeypatch)
    markers = run_keystone_function_expect_failure("verify_keystone", kc)
    assert markers == ["coriolis-bootstrap-failed"]


@pytest.mark.parametrize(
    "failure_case",
    [
        "barbican_disabled",
        "barbican_wrong_project",
        "barbican_missing_admin",
        "missing_compatibility_role",
        "duplicate_compatibility_role",
        "barbican_wrong_service_type",
        "barbican_wrong_description",
        "barbican_endpoint_wrong_url",
        "barbican_endpoint_wrong_region",
        "barbican_missing_interface",
        "barbican_authentication_rejected",
        "coriolis_authentication_rejected",
    ],
)
def test_verify_keystone_rejects_each_barbican_and_coriolis_violation(
    monkeypatch, failure_case: str
) -> None:
    kc = FakeKeystone()
    assert run_bootstrap_keystone(kc) == []

    key_manager_service = named(kc.all_services, "barbican")[0]
    if failure_case == "barbican_disabled":
        named(kc.all_users, "barbican")[0].enabled = False
    elif failure_case == "barbican_wrong_project":
        named(kc.all_users, "barbican")[0].default_project_id = "elsewhere"
    elif failure_case == "barbican_missing_admin":
        kc.all_assignments = [
            a
            for a in kc.all_assignments
            if a.user["id"] != named(kc.all_users, "barbican")[0].id
        ]
    elif failure_case == "missing_compatibility_role":
        kc.all_roles = [r for r in kc.all_roles if r.name != "observer"]
    elif failure_case == "duplicate_compatibility_role":
        kc.roles.create(name="audit")
        kc.roles.create(name="duplicate-holder")
        kc.all_roles.append(FakeResource("audit-extra", name="audit"))
    elif failure_case == "barbican_wrong_service_type":
        key_manager_service.type = "wrong-type"
    elif failure_case == "barbican_wrong_description":
        key_manager_service.description = "wrong description"
    elif failure_case == "barbican_endpoint_wrong_url":
        internal = next(
            e
            for e in kc.endpoints.list(service=key_manager_service.id)
            if e.interface == "internal"
        )
        internal.url = "http://wrong-barbican:9311"
    elif failure_case == "barbican_endpoint_wrong_region":
        kc.endpoints.list(service=key_manager_service.id)[0].region = "RegionTwo"
    elif failure_case == "barbican_missing_interface":
        admin = next(
            e
            for e in kc.endpoints.list(service=key_manager_service.id)
            if e.interface == "admin"
        )
        kc.all_endpoints.remove(admin)
    else:
        fakes = install_fake_keystoneauth(monkeypatch)
        if failure_case == "barbican_authentication_rejected":
            original_get_token = fakes["Password"].get_token

            def selective_get_token(self, session):
                if self.kwargs["username"] == "barbican":
                    return None
                return original_get_token(self, session)

            fakes["Password"].get_token = selective_get_token
        else:
            original_get_token = fakes["Password"].get_token

            def selective_get_token(self, session):
                if self.kwargs["username"] == "coriolis":
                    return None
                return original_get_token(self, session)

            fakes["Password"].get_token = selective_get_token
        markers = run_keystone_function_expect_failure("verify_keystone", kc)
        assert markers == ["coriolis-bootstrap-failed"]
        return

    install_fake_keystoneauth(monkeypatch)
    markers = run_keystone_function_expect_failure("verify_keystone", kc)
    assert markers == ["coriolis-bootstrap-failed"]


def test_wait_for_dependencies_retries_transient_failures() -> None:
    script = render()
    sleep_calls = []
    call_counts = {"gates": 0}

    class FakeTime:
        def monotonic(self):
            return 0.0

        def sleep(self, seconds):
            sleep_calls.append(seconds)

    def gate_keystone_admin():
        call_counts["gates"] += 1
        if call_counts["gates"] < 3:
            raise RuntimeError("transient dependency unavailable")
        return "keystone-client"

    namespace = {
        "time": FakeTime(),
        "fail": lambda marker: pytest.fail(f"unexpected failure marker: {marker}"),
        "gate_mariadb": lambda: None,
        "gate_rabbitmq": lambda: None,
        "gate_memcached": lambda: None,
        "gate_keystone_admin": gate_keystone_admin,
        "DEPENDENCY_DEADLINE_SECONDS": 300,
        "DEPENDENCY_SLEEP_SECONDS": 2,
    }
    wait_for_dependencies = extract_function(script, "wait_for_dependencies", namespace)

    result = wait_for_dependencies()

    assert result == "keystone-client"
    assert call_counts["gates"] == 3
    assert sleep_calls == [2, 2]


def test_wait_for_dependencies_shares_one_window_across_all_gates() -> None:
    script = render()
    sleep_calls = []
    attempts = {"mariadb": 0, "rabbitmq": 0, "memcached": 0, "keystone": 0}

    class FakeTime:
        def monotonic(self):
            return 0.0

        def sleep(self, seconds):
            sleep_calls.append(seconds)

    def transient(name):
        def gate():
            attempts[name] += 1
            if attempts[name] < 2:
                raise RuntimeError("transient")

        return gate

    namespace = {
        "time": FakeTime(),
        "fail": lambda marker: pytest.fail(f"unexpected failure marker: {marker}"),
        "gate_mariadb": transient("mariadb"),
        "gate_rabbitmq": transient("rabbitmq"),
        "gate_memcached": transient("memcached"),
        "gate_keystone_admin": transient("keystone"),
        "DEPENDENCY_DEADLINE_SECONDS": 300,
        "DEPENDENCY_SLEEP_SECONDS": 2,
    }
    wait_for_dependencies = extract_function(script, "wait_for_dependencies", namespace)

    wait_for_dependencies()

    assert attempts["mariadb"] == 5
    assert attempts["rabbitmq"] == 4
    assert attempts["memcached"] == 3
    assert attempts["keystone"] == 2
    assert sleep_calls == [2] * 4


def test_wait_for_dependencies_deadline_emits_only_fixed_marker() -> None:
    script = render()
    markers = []
    sleep_calls = []

    class FakeTime:
        def __init__(self) -> None:
            self.value = 0.0

        def monotonic(self):
            self.value += 100.0
            return self.value

        def sleep(self, seconds):
            sleep_calls.append(seconds)

    def fail(marker):
        markers.append(marker)
        raise SystemExit(1)

    def gate_mariadb():
        raise RuntimeError("dependency never ready")

    namespace = {
        "time": FakeTime(),
        "fail": fail,
        "gate_mariadb": gate_mariadb,
        "gate_rabbitmq": lambda: None,
        "gate_memcached": lambda: None,
        "gate_keystone_admin": lambda: "keystone-client",
        "DEPENDENCY_DEADLINE_SECONDS": 300,
        "DEPENDENCY_SLEEP_SECONDS": 2,
    }
    wait_for_dependencies = extract_function(script, "wait_for_dependencies", namespace)

    with pytest.raises(SystemExit):
        wait_for_dependencies()

    assert markers == ["coriolis-bootstrap-failed"]
    assert sleep_calls == [2, 2]
