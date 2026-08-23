import ast

import pytest

from coriolis_operator.common import (
    BOOTSTRAP_ACTIVE_DEADLINE_SECONDS,
    BOOTSTRAP_BACKOFF_LIMIT,
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


def render(
    coriolis_api_host: str = "example-coriolis-api",
    rabbitmq_host: str = "example-rabbitmq",
    memcached_host: str = "example-memcached",
    database_host: str = "example-mariadb",
    keystone_host: str = "example-keystone",
) -> str:
    return render_bootstrap_script(
        coriolis_api_host=coriolis_api_host,
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


def test_common_bootstrap_constants_are_exact() -> None:
    assert CONDUCTOR_IMAGE == EXACT_CONDUCTOR_IMAGE
    assert BOOTSTRAP_REVISION == "v1"
    assert BOOTSTRAP_UID_GID == 42434
    assert BOOTSTRAP_COMPONENT == "common-bootstrap-v1"
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


def test_common_bootstrap_mount_paths_are_top_level_and_non_overlapping() -> None:
    paths = {
        "/etc/coriolis",
        "/etc/coriolis-bootstrap",
        "/etc/coriolis-bootstrap-infra",
        "/etc/coriolis-bootstrap-coriolis",
        "/tmp",
    }
    assert len(paths) == 5
    assert BOOTSTRAP_CONFIG_DIR == "/etc/coriolis"
    assert BOOTSTRAP_SCRIPT_DIR == "/etc/coriolis-bootstrap"
    assert BOOTSTRAP_INFRA_CREDENTIALS_DIR == "/etc/coriolis-bootstrap-infra"
    assert BOOTSTRAP_CORIOLIS_CREDENTIALS_DIR == "/etc/coriolis-bootstrap-coriolis"
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
    assert "['coriolis-dbsync', '--config-file=/etc/coriolis/coriolis.conf']" in script
    assert "stdout=subprocess.DEVNULL" in script
    assert "stderr=subprocess.DEVNULL" in script
    assert "DBSYNC_TIMEOUT_SECONDS = 120" in script
    assert "timeout=DBSYNC_TIMEOUT_SECONDS" in script
    assert "timeout=600" not in script


def test_render_bootstrap_script_gives_coriolis_session_an_explicit_timeout() -> None:
    script = render()
    assert "session = ks_session.Session(auth=coriolis_auth, timeout=10)" in script
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
        "__RABBITMQ_HOST__",
        "__MEMCACHED_HOST__",
        "__DATABASE_HOST__",
        "__KEYSTONE_HOST__",
        "__KEYSTONE_ADMIN_PASSWORD_PATH__",
        "__RABBITMQ_PASSWORD_PATH__",
        "__CORIOLIS_KEYSTONE_PASSWORD_PATH__",
        "__CORIOLIS_DATABASE_PASSWORD_PATH__",
    ):
        assert placeholder not in script


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


def test_render_bootstrap_script_rejects_invalid_secondary_host() -> None:
    with pytest.raises(ValueError):
        render(rabbitmq_host="bad.host")


@pytest.mark.parametrize("valid", ["example", "example-rabbitmq", "a1b2c3", "a" * 63])
def test_render_bootstrap_script_accepts_single_dns_labels(valid: str) -> None:
    script = render(coriolis_api_host=valid)
    assert valid in script


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
