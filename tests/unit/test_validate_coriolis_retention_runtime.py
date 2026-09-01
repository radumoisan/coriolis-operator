import base64
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "validate-coriolis-retention-runtime.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_coriolis_retention_runtime", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)

RELEASED_YAML = """auth_enabled: true

server:
  http_listen_port: 3100

limits_config:
  retention_period: 1h

compactor:
  compaction_interval: 15m
  retention_enabled: true
  retention_delete_delay: 2h
"""

DIAGNOSTIC_YAML = """auth_enabled: true

server:
  http_listen_port: 3100

limits_config:
  retention_period: 10m

compactor:
  compaction_interval: 1m
  retention_enabled: true
  retention_delete_delay: 5m
"""

CONFIGMAP_JSON = json.dumps({"metadata": {}, "data": {"loki.yaml": RELEASED_YAML}})

ALLOY_APP_COMPONENTS = frozenset(
    (
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
)


def _sha(value: str) -> str:
    return value * 64


def _validator(**kwargs: object) -> runtime.Validator:
    defaults: dict[str, object] = {
        "repository_root": Path("."),
        "context": "ctx",
        "namespace": "ns",
        "app_name": "acme",
    }
    defaults.update(kwargs)
    return runtime.Validator(**defaults)


def _recording_runner(
    calls: list[tuple[str, ...]], result: subprocess.CompletedProcess[str]
):
    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        calls.append(tuple(command))
        return result

    return runner


def _json_runner(calls: list[tuple[str, ...]], payload: object):
    return _recording_runner(
        calls,
        subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
    )


def test_config_replacement_preserves_surrounding_lines() -> None:
    replaced = runtime._replace_config_values(RELEASED_YAML, runtime.DIAGNOSTIC_CONFIG)
    assert replaced == DIAGNOSTIC_YAML
    assert runtime._parse_config_values(replaced) == runtime.DIAGNOSTIC_CONFIG


def test_config_parse_matches_released_values() -> None:
    assert runtime._parse_config_values(RELEASED_YAML) == runtime.RELEASED_CONFIG


def test_config_replacement_rejects_unreplaced_key() -> None:
    with pytest.raises(ValueError):
        runtime._replace_config_values(RELEASED_YAML, {"retention_period": "10m"})


def test_original_config_rejects_unexpected_values() -> None:
    validator = _validator()
    validator.runner = _json_runner(
        [],
        {
            "metadata": {},
            "data": {
                "loki.yaml": RELEASED_YAML.replace(
                    "retention_period: 1h", "retention_period: 2h"
                )
            },
        },
    )
    with pytest.raises(runtime.ValidationFailure, match="config-original"):
        validator._validate_original_config()


def test_patch_touches_only_configmap_data(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    validator = _validator(repository_root=tmp_path)
    validator.runner = _json_runner(calls, json.loads(CONFIGMAP_JSON))
    validator._validate_original_config()

    validator.runner = _recording_runner(
        calls, subprocess.CompletedProcess([], 0, "", "")
    )
    validator._patch_config()

    patch_call = next(command for command in calls if "patch" in command)
    patch_arg = patch_call[patch_call.index("--patch") + 1]
    patch = json.loads(patch_arg)
    assert set(patch) == {"data"}
    data = patch["data"]
    assert set(data) == {"loki.yaml"}
    assert data["loki.yaml"] == DIAGNOSTIC_YAML
    assert patch_call[patch_call.index("--type") + 1] == "merge"


def test_config_map_is_diagnostic_and_reverted(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    validator.runner = _json_runner([], json.loads(CONFIGMAP_JSON))
    validator._validate_original_config()

    validator.runner = _json_runner(
        [], {"metadata": {}, "data": {"loki.yaml": DIAGNOSTIC_YAML}}
    )
    assert validator._config_map_is_diagnostic() is True

    validator.runner = _json_runner(
        [],
        {"metadata": {}, "data": {"loki.yaml": RELEASED_YAML}},
    )
    assert validator._config_map_is_diagnostic() is False


def test_verify_config_api_requires_diagnostic(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    validator.config_key = "loki.yaml"
    validator.runner = _json_runner(
        [], {"metadata": {}, "data": {"loki.yaml": DIAGNOSTIC_YAML}}
    )
    validator._verify_config_api()

    validator.runner = _json_runner(
        [], {"metadata": {}, "data": {"loki.yaml": RELEASED_YAML}}
    )
    with pytest.raises(runtime.ValidationFailure, match="config-api"):
        validator._verify_config_api()


def test_inventory_parse_and_diff() -> None:
    pre = runtime._parse_inventory(f"a/b.f\t1\t10\t{_sha('0')}\n")
    assert len(pre) == 1
    assert pre[0].path == "a/b.f"

    post = runtime._parse_inventory(
        f"a/b.f\t10\t100\t{_sha('a')}\nc/d.f\t5\t200\t{_sha('b')}\n"
    )
    assert len(post) == 2
    candidates = runtime._inventory_diff(post, pre)
    assert [entry.path for entry in candidates] == ["c/d.f"]
    assert candidates[0].size == 5
    assert candidates[0].mtime == 200
    assert candidates[0].sha256 == _sha("b")


def test_inventory_parse_rejects_malformed_line() -> None:
    with pytest.raises(ValueError):
        runtime._parse_inventory("only-three\tparts\ttabs\n")


def test_inventory_script_scopes_to_tenant_and_metadata_only() -> None:
    script = runtime._inventory_script("coriolis-uid")
    assert "/loki/chunks/coriolis-uid" in script
    assert "find" in script
    assert "sha256sum" in script
    assert "-type f" in script
    assert "stat -c%s" in script
    assert "stat -c%Y" in script


def test_inventory_script_is_posix_portable() -> None:
    script = runtime._inventory_script("coriolis-uid")
    assert "sort -z" not in script
    assert "read -d" not in script
    assert "-print0" not in script
    assert "find /loki/chunks/coriolis-uid -type f | while read f" in script


def test_parse_inventory_sorts_deterministically() -> None:
    entries = runtime._parse_inventory(
        f"b/x\t1\t1\t{_sha('a')}\nb/a\t1\t1\t{_sha('b')}\n"
    )
    assert [entry.path for entry in entries] == ["b/a", "b/x"]


def test_inventory_exec_routes_to_observer(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    calls: list[tuple[str, ...]] = []
    validator.runner = _recording_runner(
        calls,
        subprocess.CompletedProcess([], 0, f"c/d.f\t1\t10\t{_sha('a')}\n", ""),
    )
    validator.tenant = "coriolis-uid"
    validator._capture_pre_inventory()

    exec_call = next(command for command in calls if "exec" in command)
    joined = " ".join(exec_call)
    assert "acme-retention-observer" in joined
    assert "acme-loki-0" not in joined
    assert validator._pre_inventory[0].path == "c/d.f"


def test_every_kubectl_command_is_scoped(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    validator = _validator(repository_root=tmp_path)

    commands = [
        validator._kubectl("get", "configmap", "acme-loki-config", "-o", "json"),
        validator._kubectl("patch", "configmap", "acme-loki-config"),
        validator._kubectl("delete", "pod", "acme-loki-0", "--wait=false"),
        validator._kubectl("exec", "acme-retention-observer", "--", "sh", "-c", "x"),
        validator._kubectl("get", "secret", "acme-logging-credentials", "-o", "json"),
        validator._kubectl("port-forward", "svc/acme-gateway", "1000:8080"),
        validator._kubectl("port-forward", "pod/acme-loki-0", "1000:3100"),
        validator._kubectl("get", "coriolisappliances", "acme", "-o", "json"),
        validator._kubectl(
            "delete", "pod", "acme-retention-observer", "--ignore-not-found=true"
        ),
        validator._observer_command(),
    ]
    for command in commands:
        assert command[0] == "kubectl"
        assert "--context" in command
        assert command[command.index("--context") + 1] == "ctx"
        assert "--namespace" in command
        assert command[command.index("--namespace") + 1] == "ns"

    validator.runner = _recording_runner(
        calls, subprocess.CompletedProcess([], 0, "", "")
    )
    validator._checked("x", validator._kubectl("get", "pod", "acme-loki-0"))
    assert calls[-1] == (
        "kubectl",
        "--context",
        "ctx",
        "--namespace",
        "ns",
        "get",
        "pod",
        "acme-loki-0",
    )


def test_secret_values_never_reach_commands_or_report(tmp_path: Path) -> None:
    read_password = "READ_SENTINEL_VALUE"
    write_password = "WRITE_SENTINEL_VALUE"
    calls: list[tuple[str, ...]] = []
    output: list[str] = []

    payload = {
        "data": {
            "read_password": base64.b64encode(read_password.encode()).decode(),
            "write_password": base64.b64encode(write_password.encode()).decode(),
        }
    }
    validator = _validator(repository_root=tmp_path, report=output.append)
    validator.runner = _json_runner(calls, payload)
    validator._read_secret()

    assert validator.credentials is not None
    assert validator.credentials.read_password == read_password
    assert validator.credentials.write_password == write_password

    joined = " ".join(" ".join(command) for command in calls)
    assert read_password not in joined
    assert write_password not in joined
    assert read_password not in "\n".join(output)
    assert write_password not in "\n".join(output)
    assert not any(
        value in repr(validator.credentials)
        for value in (read_password, write_password)
    )


def test_basic_auth_value_never_logs_password() -> None:
    auth = runtime._basic_auth_value("tenant", "PW_SENTINEL")
    assert "PW_SENTINEL" not in auth
    decoded = base64.b64decode(auth).decode()
    assert decoded == "tenant:PW_SENTINEL"


def test_failure_output_is_sanitized(tmp_path: Path) -> None:
    output: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 1, "RAW_STDOUT_SENTINEL", "RAW_STDERR_SENTINEL"
        )

    validator = _validator(repository_root=tmp_path, report=output.append)
    validator.runner = runner
    with pytest.raises(runtime.ValidationFailure, match="cr-uid"):
        validator._read_cr_uid()

    rendered = "\n".join(output)
    assert "RAW_STDOUT_SENTINEL" not in rendered
    assert "RAW_STDERR_SENTINEL" not in rendered


def test_run_reports_fail_stage_and_cleanup_notice(tmp_path: Path) -> None:
    output: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "SENTINEL", "SENTINEL")

    validator = _validator(repository_root=tmp_path, report=output.append)
    validator.runner = runner
    assert validator.run() == 1
    rendered = "\n".join(output)
    assert "FAIL cr-uid" in rendered
    assert "SENTINEL" not in rendered
    assert "SUMMARY retention failed" in rendered
    assert "caller-owned" in rendered
    assert "never restores it" in rendered


def test_delete_observer_reports_fixed_cleanup_on_success(tmp_path: Path) -> None:
    output: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        joined = " ".join(command)
        if "retention-observer" in joined and "delete" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "SENTINEL", "SENTINEL")

    validator = _validator(repository_root=tmp_path, report=output.append)
    validator.runner = runner
    assert validator.run() == 1
    rendered = "\n".join(output)
    assert "CLEANUP observer" in rendered
    assert "SENTINEL" not in rendered


def test_delete_observer_cannot_mask_failure(tmp_path: Path) -> None:
    output: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        joined = " ".join(command)
        if "retention-observer" in joined and "delete" in command:
            raise OSError("observer delete boom")
        return subprocess.CompletedProcess(command, 1, "SENTINEL", "SENTINEL")

    validator = _validator(repository_root=tmp_path, report=output.append)
    validator.runner = runner
    assert validator.run() == 1
    rendered = "\n".join(output)
    assert "FAIL cr-uid" in rendered
    assert "CLEANUP observer" not in rendered
    assert "boom" not in rendered
    assert "SENTINEL" not in rendered


def test_marker_push_body_uses_json_with_nanosecond_timestamp() -> None:
    body = runtime._marker_push_body("abc123", 1234.5)
    payload = json.loads(body)
    stream = payload["streams"][0]
    assert stream["stream"]["stream"] == "loki-retention"
    assert stream["stream"]["marker"] == "abc123"
    timestamp, line = stream["values"][0]
    assert timestamp == "1234500000000"
    assert line == "synthetic retention marker"


def test_push_marker_uses_json_and_keeps_credentials_in_memory(
    tmp_path: Path,
) -> None:
    validator = _validator(repository_root=tmp_path)
    validator.credentials = runtime._Credentials("READ_P", "WRITE_P")
    validator.tenant = "tenant"
    validator.wallclock = lambda: 1234.5
    captured: list[tuple[object, ...]] = []

    def fake_http(
        method: str,
        path: str,
        *,
        basic_user: str,
        basic_pass: str,
        body: bytes | None = None,
        content_type: str | None = None,
        content_encoding: str | None = None,
    ) -> tuple[int, bytes]:
        captured.append(
            (method, path, basic_user, basic_pass, body, content_type, content_encoding)
        )
        return 204, b""

    validator._http = fake_http
    validator._push_marker()

    method, path, user, password, body, content_type, encoding = captured[0]
    assert method == "POST"
    assert path == "/loki/api/v1/push"
    assert user == "tenant"
    assert password == "WRITE_P"
    assert content_type == "application/json"
    assert encoding is None
    payload = json.loads(body)
    assert payload["streams"][0]["stream"]["marker"] == validator._marker
    assert "WRITE_P" not in repr(body)


def test_config_loaded_verifies_exact_values_via_direct_forward(
    tmp_path: Path,
) -> None:
    validator = _validator(repository_root=tmp_path)
    validator.tenant = "tenant"
    opened: list[tuple[str, str, int]] = []
    closed: list[bool] = []
    validator._open_port_forward = lambda stage, target, port: opened.append(
        (stage, target, port)
    )
    validator._close_port_forward = lambda: closed.append(True)

    def http(method: str, path: str, **kwargs: object) -> tuple[int, bytes]:
        assert method == "GET"
        assert path == "/config"
        assert kwargs.get("tenant") == "tenant"
        assert "basic_user" not in kwargs
        assert "basic_pass" not in kwargs
        canonical = (
            DIAGNOSTIC_YAML.replace("10m", "10m0s")
            .replace("1m", "1m0s")
            .replace("5m", "5m0s")
        )
        return 200, canonical.encode()

    validator._http = http
    validator._config_loaded()

    assert opened == [("config-loaded", "pod/acme-loki-0", runtime.LOKI_PORT)]
    assert closed == [True]


def test_config_loaded_fails_on_bad_status_or_parse(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    validator.tenant = "tenant"
    validator._open_port_forward = lambda *a: None
    validator._close_port_forward = lambda: None
    validator._http = lambda *a, **k: (500, b"")
    with pytest.raises(runtime.ValidationFailure, match="config-loaded"):
        validator._config_loaded()

    validator2 = _validator(repository_root=tmp_path)
    validator2.tenant = "tenant"
    validator2._open_port_forward = lambda *a: None
    validator2._close_port_forward = lambda: None
    validator2._http = lambda *a, **k: (200, RELEASED_YAML.encode())
    with pytest.raises(runtime.ValidationFailure, match="config-loaded"):
        validator2._config_loaded()


def test_chunk_flush_posts_flush_direct_no_basic_closes(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    validator.tenant = "tenant"
    opened: list[tuple[str, str, int]] = []
    closed: list[bool] = []
    captured: list[tuple[str, str, dict[str, object]]] = []
    validator._open_port_forward = lambda stage, target, port: opened.append(
        (stage, target, port)
    )
    validator._close_port_forward = lambda: closed.append(True)

    def http(method: str, path: str, **kwargs: object) -> tuple[int, bytes]:
        captured.append((method, path, kwargs))
        assert method == "POST"
        assert path == "/flush"
        assert kwargs.get("tenant") == "tenant"
        assert kwargs.get("body") is None
        assert "basic_user" not in kwargs
        assert "basic_pass" not in kwargs
        return 204, b""

    validator._http = http
    validator._chunk_flush()

    assert opened == [("chunk-flush", "pod/acme-loki-0", runtime.LOKI_PORT)]
    assert captured == [("POST", "/flush", {"tenant": "tenant"})]
    assert closed == [True]


@pytest.mark.parametrize(
    ("status", "accepted"),
    [(200, True), (204, True), (301, False), (400, False), (500, False)],
)
def test_chunk_flush_only_accepts_2xx(
    tmp_path: Path, status: int, accepted: bool
) -> None:
    validator = _validator(repository_root=tmp_path)
    validator.tenant = "tenant"
    validator._open_port_forward = lambda *a: None
    validator._close_port_forward = lambda: None
    validator._http = lambda *a, **k: (status, b"")
    if accepted:
        validator._chunk_flush()
    else:
        with pytest.raises(runtime.ValidationFailure, match="chunk-flush"):
            validator._chunk_flush()


def test_chunk_flush_closes_on_failure(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    validator.tenant = "tenant"
    validator._open_port_forward = lambda *a: None
    closed: list[bool] = []
    validator._close_port_forward = lambda: closed.append(True)
    validator._http = lambda *a, **k: (500, b"")
    with pytest.raises(runtime.ValidationFailure, match="chunk-flush"):
        validator._chunk_flush()
    assert closed == [True]


def test_chunk_materialized_polls_until_candidates(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path, timeout=30)
    now = [0.0]
    validator.clock = lambda: now[0]
    validator.sleeper = lambda _: now.__setitem__(0, now[0] + 1)
    candidates = (runtime.InventoryEntry("c/d.f", 1, 10, _sha("a")),)
    queue = [(), candidates]
    validator._pre_inventory = ()
    validator._inventory_now = lambda stage: queue.pop(0)
    validator._chunk_materialized()
    assert validator._candidates == candidates
    assert validator._post_inventory == candidates


def test_chunk_materialized_timeout_bounded(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path, timeout=30)
    now = [0.0]
    validator.clock = lambda: now[0]
    validator.sleeper = lambda _: now.__setitem__(0, now[0] + 1)
    validator._inventory_now = lambda stage: ()
    validator._candidates = ()
    with pytest.raises(runtime.ValidationFailure, match="chunk-materialized"):
        validator._chunk_materialized()
    assert validator.clock() == 30
    assert validator._candidates == ()


def test_chunk_materialized_fails_sanitized_on_observer_parse(
    tmp_path: Path,
) -> None:
    validator = _validator(repository_root=tmp_path)

    def fail(stage: str) -> tuple[object, ...]:
        raise runtime.ValidationFailure("chunk-materialized")

    validator._inventory_now = fail
    with pytest.raises(runtime.ValidationFailure, match="chunk-materialized"):
        validator._chunk_materialized()


def test_query_persisted_requires_positive(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    validator._marker = "m"
    validator._pushed = 100.0
    validator.wallclock = lambda: 200.0
    validator._query_count = lambda marker, start, end: 0
    with pytest.raises(runtime.ValidationFailure, match="query-persisted"):
        validator._query_persisted()
    validator._query_count = lambda marker, start, end: 3
    validator._query_persisted()


def test_observer_overrides_security_and_readonly_mount() -> None:
    overrides = runtime._observer_overrides("acme")
    spec = overrides["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert spec["imagePullSecrets"] == [{"name": runtime.OBSERVER_IMAGE_PULL_SECRET}]
    pod_sc = spec["securityContext"]
    assert pod_sc["runAsNonRoot"] is True
    assert pod_sc["runAsUser"] == runtime.OBSERVER_RUN_UID
    assert pod_sc["runAsGroup"] == runtime.OBSERVER_RUN_UID
    assert pod_sc["fsGroup"] == runtime.OBSERVER_RUN_UID
    volume = spec["volumes"][0]
    assert volume["name"] == "loki-data"
    assert volume["persistentVolumeClaim"]["claimName"] == "acme-loki-data"

    container = spec["containers"][0]
    assert container["image"] == runtime.OBSERVER_IMAGE
    assert container["command"] == [
        "/bin/sh",
        "-c",
        f"sleep {runtime.OBSERVER_SLEEP_SECONDS}",
    ]
    assert runtime.OBSERVER_SLEEP_SECONDS >= 6 * 60 * 60
    assert container["imagePullPolicy"] == "IfNotPresent"
    csc = container["securityContext"]
    assert csc["runAsNonRoot"] is True
    assert csc["runAsUser"] == runtime.OBSERVER_RUN_UID
    assert csc["runAsGroup"] == runtime.OBSERVER_RUN_UID
    assert csc["allowPrivilegeEscalation"] is False
    assert csc["readOnlyRootFilesystem"] is True
    assert csc["capabilities"] == {"drop": ["ALL"]}
    mount = container["volumeMounts"][0]
    assert mount["name"] == "loki-data"
    assert mount["mountPath"] == "/loki"
    assert mount["readOnly"] is True

    assert (
        overrides["metadata"]["labels"][runtime.OBSERVER_LABEL_KEY]
        == runtime.OBSERVER_LABEL_VALUE
    )


def test_observer_component_label_not_in_alloy_allowlist() -> None:
    assert runtime.OBSERVER_LABEL_VALUE not in ALLOY_APP_COMPONENTS


def test_observer_command_is_scoped_run_pod(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    command = validator._observer_command()
    assert command[:5] == ["kubectl", "--context", "ctx", "--namespace", "ns"]
    assert "run" in command
    assert "acme-retention-observer" in command
    assert "--restart=Never" in command
    assert any(arg.startswith("--image=" + runtime.OBSERVER_IMAGE) for arg in command)
    override_arg = next(arg for arg in command if arg.startswith("--overrides="))
    overrides = json.loads(override_arg[len("--overrides=") :])
    assert overrides["spec"]["containers"][0]["name"] == "acme-retention-observer"
    assert "loki-0" not in " ".join(command)


def test_observer_tools_probe_is_silent_and_complete() -> None:
    command = runtime._observer_tools_command()
    for tool in runtime.OBSERVER_TOOLS:
        assert tool in command
    assert "command -v" in command
    assert ">/dev/null 2>&1" in command
    assert "cat" not in command


def test_pod_recreate_rejects_old_ready_until_new_uid(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    now = [0.0]
    commands: list[tuple[str, ...]] = []
    states = [
        ("old-uid", True),
        ("old-uid", True),
        ("new-uid", True),
    ]
    validator.clock = lambda: now[0]
    validator.sleeper = lambda _: now.__setitem__(0, now[0] + 1)
    validator.runner = _recording_runner(
        commands, subprocess.CompletedProcess([], 0, "", "")
    )
    validator._pod_state = lambda stage, pod: states.pop(0)
    validator._recreate_pod("pod-recreate")

    assert any("delete" in command for command in commands)
    delete_call = next(command for command in commands if "delete" in command)
    assert "--wait=false" in delete_call


def test_pod_recreate_times_out_without_replacement(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path, timeout=30)
    now = [0.0]
    validator.clock = lambda: now[0]
    validator.sleeper = lambda _: now.__setitem__(0, now[0] + 1)
    validator._pod_state = lambda stage, pod: ("old-uid", True)
    validator.runner = _recording_runner([], subprocess.CompletedProcess([], 0, "", ""))
    with pytest.raises(runtime.ValidationFailure, match="pod-recreate"):
        validator._recreate_pod("pod-recreate")


def test_close_port_forward_resets_stored_process(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)

    class FakeProc:
        def __init__(self) -> None:
            self.terminated = False
            self.killed = False

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: int) -> None:
            pass

        def kill(self) -> None:
            self.killed = True

    proc = FakeProc()
    validator._port_forward_proc = proc
    validator._close_port_forward()
    assert validator._port_forward_proc is None
    assert proc.terminated is True

    validator._close_port_forward()


def test_run_body_sequences_flush_materialize_persist(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path)
    calls: list[str] = []
    for name in (
        "_read_cr_uid",
        "_read_secret",
        "_validate_original_config",
        "_patch_config",
        "_verify_config_api",
        "_recreate_pod",
        "_config_loaded",
        "_create_observer",
        "_wait_observer_ready",
        "_verify_observer_tools",
        "_open_port_forward",
        "_capture_pre_inventory",
        "_push_marker",
        "_query_before",
        "_close_port_forward",
        "_chunk_flush",
        "_chunk_materialized",
        "_query_persisted",
        "_wait_and_assert_retention",
        "_candidate_count_stage",
        "_deletion_marker_count_stage",
    ):
        setattr(validator, name, lambda *a, _n=name, **k: calls.append(_n))

    validator._run_body()

    open_indices = [
        index for index, name in enumerate(calls) if name == "_open_port_forward"
    ]
    first_open, second_open = open_indices
    assert len(open_indices) == 2
    assert calls[-1] == "_close_port_forward"

    pre = calls.index("_capture_pre_inventory")
    push = calls.index("_push_marker")
    before = calls.index("_query_before")
    close = calls.index("_close_port_forward")
    flush = calls.index("_chunk_flush")
    materialized = calls.index("_chunk_materialized")
    persisted = calls.index("_query_persisted")
    retention = calls.index("_wait_and_assert_retention")

    assert (
        first_open
        < pre
        < push
        < before
        < close
        < flush
        < materialized
        < second_open
        < persisted
        < retention
    )

    assert (
        calls.index("_patch_config")
        < calls.index("_verify_config_api")
        < calls.index("_recreate_pod")
        < calls.index("_config_loaded")
        < calls.index("_create_observer")
        < calls.index("_wait_observer_ready")
        < calls.index("_verify_observer_tools")
        < first_open
    )


def test_count_output_is_numeric_and_sanitized(tmp_path: Path) -> None:
    output: list[str] = []
    validator = _validator(repository_root=tmp_path, report=output.append)
    validator._candidates = (runtime.InventoryEntry("a", 1, 1, _sha("a")),)
    validator._candidate_count_stage()
    validator.runner = _recording_runner(
        [], subprocess.CompletedProcess([], 0, "3\n", "")
    )
    validator._deletion_marker_count_stage()

    rendered = "\n".join(output)
    assert "PASS candidate-count 1" in rendered
    assert "PASS deletion-marker-count 3" in rendered
    assert "PASS marker-count" not in rendered


def test_deletion_marker_count_command_is_portable() -> None:
    command = runtime._deletion_marker_count_command()
    assert runtime.COMPACTOR_RETENTION_DIR in command
    assert "wc -l" in command
    assert "find" in command


def test_cli_requires_context_namespace_app(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        runtime.main([])
    assert error.value.code == 2
    assert "caller-owned" in runtime.CLEANUP_NOTICE
    assert "DIAGNOSTIC-ONLY" in runtime.CLEANUP_NOTICE
    assert "retention-observer" in runtime.CLEANUP_NOTICE


def test_cli_requires_run_acknowledgement(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as error:
        runtime.main(["--context", "c", "--namespace", "n", "--app-name", "a"])
    assert error.value.code == 2

    monkeypatch.setattr(runtime.Validator, "run", lambda self: 0)
    assert (
        runtime.main(["--run", "--context", "c", "--namespace", "n", "--app-name", "a"])
        == 0
    )


def test_retention_window_derives_from_diagnostic_values() -> None:
    validator = _validator()
    assert validator.retention_window == 900
    assert runtime._parse_duration("10m") == 600
    assert runtime._parse_duration("5m") == 300


def _secret_payload(
    *,
    uid: str = "secret-uid",
    resource_version: str = "10",
    read_password: str = "read-password",
    write_password: str = "write-password",
    owners: object = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "uid": uid,
        "resourceVersion": resource_version,
    }
    if owners is not None:
        metadata["ownerReferences"] = owners
    return {
        "metadata": metadata,
        "data": {
            "read_password": base64.b64encode(read_password.encode()).decode(),
            "write_password": base64.b64encode(write_password.encode()).decode(),
        },
    }


def _pvc_payload(
    *, uid: str = "pvc-uid", resource_version: str = "20", owners: object = None
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "uid": uid,
        "resourceVersion": resource_version,
    }
    if owners is not None:
        metadata["ownerReferences"] = owners
    return {"metadata": metadata}


def test_capture_cr_manifest_strips_status_and_managed_metadata(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path, mode="formal")
    payload = {
        "apiVersion": "coriolis.cloudbase.it/v1alpha1",
        "kind": "CoriolisAppliance",
        "metadata": {
            "name": "acme",
            "namespace": "ns",
            "uid": "old-uid",
            "resourceVersion": "99",
            "managedFields": [{"manager": "operator"}],
            "labels": {"unwanted": "metadata"},
        },
        "spec": {"version": "2603.4", "logging": {"retentionHours": 1}},
        "status": {"acceptedVersion": "2603.4"},
    }
    validator.runner = _json_runner([], payload)
    validator._capture_cr_manifest()

    assert validator._cr_manifest == {
        "apiVersion": "coriolis.cloudbase.it/v1alpha1",
        "kind": "CoriolisAppliance",
        "metadata": {"name": "acme", "namespace": "ns"},
        "spec": {"version": "2603.4", "logging": {"retentionHours": 1}},
    }
    assert validator._formal_version == "2603.4"


def test_formal_release_config_is_exact_and_loaded_semantically(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path, mode="formal")
    validator.runner = _json_runner(
        [], {"metadata": {}, "data": {"loki.yaml": RELEASED_YAML}}
    )
    validator._verify_release_config_api("config-release")

    loaded = RELEASED_YAML.replace("retention_period: 1h", "retention_period: 60m")
    loaded = loaded.replace("compaction_interval: 15m", "compaction_interval: 900s")
    loaded = loaded.replace(
        "retention_delete_delay: 2h", "retention_delete_delay: 120m"
    )
    validator._open_port_forward = lambda *a: None
    validator._close_port_forward = lambda: None
    validator._http = lambda *a, **k: (200, loaded.encode())
    validator._config_loaded(runtime.RELEASED_CONFIG, "config-release-loaded")


def test_create_cr_uses_sanitized_manifest_only_on_stdin(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path, mode="formal")
    validator._cr_manifest = {
        "apiVersion": "coriolis.cloudbase.it/v1alpha1",
        "kind": "CoriolisAppliance",
        "metadata": {"name": "acme", "namespace": "ns"},
        "spec": {"version": "2603.4"},
    }
    captured: list[tuple[tuple[str, ...], str]] = []

    def input_runner(
        command: object, data: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        captured.append((tuple(command), data))
        return subprocess.CompletedProcess(command, 0, "", "")

    validator.input_runner = input_runner
    validator._create_cr()

    command, manifest = captured[0]
    assert command[-3:] == ("create", "-f", "-")
    assert json.loads(manifest) == validator._cr_manifest
    assert "status" not in manifest
    assert "managedFields" not in manifest
    assert "old-uid" not in manifest


def test_retained_resources_require_identity_owner_and_credential_equality(
    tmp_path: Path,
) -> None:
    validator = _validator(repository_root=tmp_path, mode="formal")
    validator.credentials = runtime._Credentials("read-password", "write-password")
    snapshots = [_secret_payload(), _pvc_payload(), _secret_payload(), _pvc_payload()]

    def get_json(stage: str, *arguments: str) -> dict[str, object]:
        return snapshots.pop(0)

    validator._kubectl_json = get_json
    validator._capture_retained_resources()
    validator._verify_retained_resources()

    assert validator.credentials == runtime._Credentials(
        "read-password", "write-password"
    )
    assert validator._retained_secret == runtime._ResourceIdentity("secret-uid", "10")
    assert validator._retained_pvc == runtime._ResourceIdentity("pvc-uid", "20")

    with pytest.raises(runtime.ValidationFailure, match="retained-verified"):
        validator._resource_identity(
            _pvc_payload(owners=[{"uid": "owner"}]), "retained-verified"
        )


def test_wait_recreated_cr_ready_requires_new_uid_conditions_and_loki(
    tmp_path: Path,
) -> None:
    validator = _validator(repository_root=tmp_path, mode="formal")
    validator._old_cr_uid = "old-uid"
    validator._formal_version = "2603.4"
    now = [0.0]
    validator.clock = lambda: now[0]
    validator.sleeper = lambda _: now.__setitem__(0, now[0] + 1)
    payloads = [
        {
            "metadata": {"uid": "old-uid"},
            "status": {
                "acceptedVersion": "2603.4",
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "LoggingReady", "status": "True"},
                ],
            },
        },
        {
            "metadata": {"uid": "new-uid"},
            "status": {
                "acceptedVersion": "2603.4",
                "conditions": [
                    {"type": "Ready", "status": "True"},
                    {"type": "LoggingReady", "status": "True"},
                ],
            },
        },
    ]
    validator._kubectl_json = lambda stage, *arguments: payloads.pop(0)
    validator._pod_state = lambda stage, pod: ("loki-uid", True)
    validator._wait_recreated_cr_ready()

    assert validator.tenant == "coriolis-new-uid"
    assert validator.clock() == 1


def test_delete_and_wait_cr_absent_are_scoped_and_bounded(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path, mode="formal")
    now = [0.0]
    commands: list[tuple[str, ...]] = []
    results = [0, 0, 1]
    validator.clock = lambda: now[0]
    validator.sleeper = lambda _: now.__setitem__(0, now[0] + 1)

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, results.pop(0), "", "")

    validator.runner = runner
    validator._delete_cr()
    validator._wait_cr_absent()

    delete, get, absent = commands
    assert delete[5:8] == ("delete", "coriolisappliances", "acme")
    assert "--wait=false" in delete
    assert get[5:8] == ("get", "coriolisappliances", "acme")
    assert absent == get
    assert now[0] == 1


def test_direct_old_tenant_query_is_value_silent_and_closes(tmp_path: Path) -> None:
    validator = _validator(repository_root=tmp_path, mode="formal")
    validator._marker = "MARKER_SENTINEL"
    validator._pushed = 100.0
    validator.wallclock = lambda: 200.0
    opened: list[tuple[str, str, int]] = []
    closed: list[bool] = []
    validator._open_port_forward = lambda stage, target, port: opened.append(
        (stage, target, port)
    )
    validator._close_port_forward = lambda: closed.append(True)
    captured: list[dict[str, object]] = []

    def http(method: str, path: str, **kwargs: object) -> tuple[int, bytes]:
        captured.append(kwargs)
        assert method == "GET"
        assert path.startswith("/loki/api/v1/query_range?")
        return 200, b'{"data":{"result":[{"values":[["1","x"]]}]}}'

    validator._http = http
    assert validator._direct_query_count("coriolis-old", "old-query") == 1
    assert opened == [("old-query", "pod/acme-loki-0", runtime.LOKI_PORT)]
    assert closed == [True]
    assert captured == [{"tenant": "coriolis-old"}]


def test_formal_queries_and_three_hour_retention_window(tmp_path: Path) -> None:
    validator = _validator(
        repository_root=tmp_path, mode="formal", max_wait_minutes=240
    )
    validator._marker = "marker"
    validator._pushed = 100.0
    validator._old_tenant = "coriolis-old"
    validator._candidates = (runtime.InventoryEntry("a", 1, 1, _sha("a")),)
    assert validator.formal_retention_window == 3 * 60 * 60

    validator._query_count = lambda marker, start, end: 0
    validator._query_new_tenant_isolated()
    validator._query_count = lambda marker, start, end: 1
    with pytest.raises(runtime.ValidationFailure, match="query-new-tenant-isolated"):
        validator._query_new_tenant_isolated()

    validator._direct_query_count = lambda tenant, stage: 1
    validator._query_old_tenant_persisted()
    validator._direct_query_count = lambda tenant, stage: 0
    with pytest.raises(runtime.ValidationFailure, match="query-old-tenant-persisted"):
        validator._query_old_tenant_persisted()

    now = [100.0 + validator.formal_retention_window]
    validator.wallclock = lambda: now[0]
    validator._config_map_matches_exact = lambda expected: (
        expected == runtime.RELEASED_CONFIG
    )
    observed_tenants: list[str | None] = []
    validator._candidate_remaining = lambda candidates, tenant=None: (
        observed_tenants.append(tenant) or 0
    )
    validator._direct_query_count = lambda tenant, stage: 0
    validator._wait_and_assert_formal_retention()
    assert observed_tenants == ["coriolis-old"]


def test_formal_run_body_keeps_release_config_unpatched_and_orders_stages(
    tmp_path: Path,
) -> None:
    validator = _validator(repository_root=tmp_path, mode="formal")
    calls: list[str] = []
    for name in (
        "_read_cr_uid",
        "_read_secret",
        "_capture_cr_manifest",
        "_verify_release_config_api",
        "_config_loaded",
        "_create_observer",
        "_wait_observer_ready",
        "_verify_observer_tools",
        "_open_port_forward",
        "_capture_pre_inventory",
        "_push_marker",
        "_query_before",
        "_close_port_forward",
        "_chunk_flush",
        "_chunk_materialized",
        "_query_persisted",
        "_capture_retained_resources",
        "_delete_cr",
        "_wait_cr_absent",
        "_create_cr",
        "_wait_recreated_cr_ready",
        "_verify_retained_resources",
        "_query_new_tenant_isolated",
        "_query_old_tenant_persisted",
        "_wait_and_assert_formal_retention",
        "_candidate_count_stage",
        "_deletion_marker_count_stage",
    ):
        setattr(validator, name, lambda *a, _n=name, **k: calls.append(_n))
    validator._patch_config = lambda: pytest.fail("formal must not patch Loki")

    validator._run_formal_body()

    first_open = calls.index("_open_port_forward")
    pre = calls.index("_capture_pre_inventory")
    push = calls.index("_push_marker")
    before = calls.index("_query_before")
    close = calls.index("_close_port_forward")
    flush = calls.index("_chunk_flush")
    materialized = calls.index("_chunk_materialized")
    persisted = calls.index("_query_persisted")
    retained = calls.index("_capture_retained_resources")
    delete = calls.index("_delete_cr")
    absent = calls.index("_wait_cr_absent")
    create = calls.index("_create_cr")
    ready = calls.index("_wait_recreated_cr_ready")
    verified = calls.index("_verify_retained_resources")
    isolated = calls.index("_query_new_tenant_isolated")
    old_persisted = calls.index("_query_old_tenant_persisted")
    retention = calls.index("_wait_and_assert_formal_retention")

    assert (
        first_open
        < pre
        < push
        < before
        < close
        < flush
        < materialized
        < persisted
        < retained
        < delete
        < absent
        < create
        < ready
        < verified
        < isolated
        < old_persisted
        < retention
    )
    assert "_patch_config" not in calls
    assert calls[-1] == "_close_port_forward"


def test_formal_summary_and_cli_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    output: list[str] = []
    validator = _validator(mode="formal", report=output.append)
    validator._run_formal_body = lambda: None
    validator._delete_observer = lambda: None
    assert validator.run() == 0
    assert any(line.startswith("SUMMARY retention-formal passed") for line in output)

    monkeypatch.setattr(runtime.Validator, "run", lambda self: 0)
    assert (
        runtime.main(
            [
                "--run",
                "--mode",
                "formal",
                "--context",
                "c",
                "--namespace",
                "n",
                "--app-name",
                "a",
            ]
        )
        == 0
    )
    assert runtime._parse_duration("1h0m0s") == 3600
    assert runtime._parse_duration("10m0s") == 600
