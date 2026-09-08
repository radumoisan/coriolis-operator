import base64
import contextlib
import importlib.util
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "validate-coriolis-natural-logs.py"
sys.path.insert(0, str(SCRIPT.parent.parent / "src"))
SPEC = importlib.util.spec_from_file_location("validate_coriolis_natural_logs", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)

SECRET = "read-secret/with?encoding"
WRITE = "write-secret/with?encoding"
KEYSTONE = "keystone-secret/with?encoding"
SIBLING = "unused-sibling-secret/with?encoding"
TOKEN = "token-value/with?encoding"
UID = "cr-uid-1234abcd"

BOOTSTRAP = "common-bootstrap-v3"
CATEGORY_LINE = "coriolis-bootstrap-provisioning complete"
COMPONENT_LABEL = runtime.COMPONENT_LABEL
APPLIANCE_ANNOTATION = runtime.APPLIANCE_NAME_ANNOTATION

PRODUCERS = runtime.REQUIRED_PRODUCERS
INFRA = ("loki", "alloy", "adaptor", "memcached")
BOUNDARY_CONTAINERS = ("memcached", "loki", "gateway", "alloy", "adaptor")


def _generic(component: str) -> str:
    return f"{component} steady natural producer record"


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


class Scenario:
    def __init__(self) -> None:
        self.missing_pods: set[str] = set()
        self.duplicate_pods: set[str] = set()
        self.bad_containers: set[str] = set()
        self.completed_non_bootstrap: set[str] = set()
        self.failed_bootstrap_retry = False
        self.deleting_extra: set[str] = set()
        self.not_ready: set[str] = set()
        self.logs_fail: set[str] = set()
        self.boundary_fail: set[str] = set()
        self.boundary_output: dict[str, str] = {}
        self.loki_bad_labels: set[str] = set()
        self.loki_no_match: set[str] = set()
        self.adaptor_missing_names: set[str] = set()
        self.adaptor_download_status: dict[str, int] = {}
        self.adaptor_no_match: set[str] = set()
        self.no_category = False
        self.leak: str | None = None
        self.bad_uid: Any = UID


def _pod(component: str, scenario: Scenario) -> dict[str, Any]:
    if component == "loki":
        containers = ["loki", "gateway"]
    elif component == "alloy":
        containers = ["alloy"]
    elif component == "adaptor":
        containers = ["adaptor"]
    else:
        containers = (
            ["sidecar"] if component in scenario.bad_containers else [component]
        )
    completed = component == BOOTSTRAP or component in scenario.completed_non_bootstrap
    phase = "Succeeded" if completed else "Running"
    ready = component not in scenario.not_ready
    return {
        "metadata": {
            "name": f"acme-{component}-abcde",
            "annotations": {APPLIANCE_ANNOTATION: "acme"},
        },
        "spec": {"containers": [{"name": name} for name in containers]},
        "status": {
            "phase": phase,
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "containerStatuses": [
                {"name": name, "ready": ready} for name in containers
            ],
        },
    }


def _pods_payload(component: str, scenario: Scenario) -> str:
    if component in scenario.missing_pods:
        items: list[dict[str, Any]] = []
    else:
        items = [_pod(component, scenario)]
        if component in scenario.duplicate_pods:
            duplicate = json.loads(json.dumps(items[0]))
            duplicate["metadata"]["name"] = f"acme-{component}-fghij"
            items.append(duplicate)
        if component == BOOTSTRAP and scenario.failed_bootstrap_retry:
            failed = json.loads(json.dumps(items[0]))
            failed["metadata"]["name"] = f"acme-{component}-failed"
            failed["status"]["phase"] = "Failed"
            items.append(failed)
        if component in scenario.deleting_extra:
            deleting = json.loads(json.dumps(items[0]))
            deleting["metadata"]["name"] = f"acme-{component}-deleting"
            deleting["metadata"]["deletionTimestamp"] = "2026-09-08T12:00:00Z"
            items.append(deleting)
    return json.dumps({"items": items})


def _logs_stdout(component: str, scenario: Scenario) -> str:
    if scenario.leak in {"kubectl", "sibling-kubectl"}:
        leaked = SIBLING if scenario.leak == "sibling-kubectl" else SECRET
        return f"tail output with {leaked} embedded\n"
    lines = [f"2026-09-08T10:00:00.123456789Z stdout F {_generic(component)}\r\n"]
    if component == BOOTSTRAP and not scenario.no_category:
        lines.append(f"{CATEGORY_LINE}\r\n")
    return "".join(lines)


def _loki_body(component: str, scenario: Scenario) -> bytes:
    labels = {
        "namespace": "ns",
        "coriolis_appliance": "acme",
        "coriolis_component": component,
        "container": component,
        "pod": f"acme-{component}-abcde",
    }
    if component in scenario.loki_bad_labels:
        labels = dict(labels, coriolis_component="wrong-component")
    if component in scenario.loki_no_match:
        lines = [f"{component} unrelated drifting record"]
    else:
        lines = [_generic(component)]
        if component == BOOTSTRAP and not scenario.no_category:
            lines.append(CATEGORY_LINE)
    if scenario.leak in {"loki", "sibling-loki"}:
        leaked = SIBLING if scenario.leak == "sibling-loki" else SECRET
        lines.append(f"stream note {leaked}")
    return json.dumps(
        {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": labels,
                        "values": [["1757325600000000000", line] for line in lines],
                    }
                ],
            },
        }
    ).encode()


def _adaptor_list_body(scenario: Scenario) -> bytes:
    names = sorted(set(PRODUCERS) - scenario.adaptor_missing_names)
    body = json.dumps({"logs": [{"log_name": name} for name in names]})
    if scenario.leak in {"adaptor", "sibling-adaptor"}:
        leaked = SIBLING if scenario.leak == "sibling-adaptor" else SECRET
        body += f" trace {leaked}"
    return body.encode()


def _adaptor_download_body(component: str, scenario: Scenario) -> bytes:
    if component in scenario.adaptor_no_match:
        return f"{component} drifting history record\r\n".encode()
    lines = [f"{_generic(component)}\r\n"]
    if component == BOOTSTRAP and not scenario.no_category:
        lines.append(f"{CATEGORY_LINE}\r\n")
    return "".join(lines).encode()


def _make(
    scenario: Scenario | None = None,
) -> tuple[
    runtime.Validator,
    list[str],
    list[tuple[str, ...]],
    list[tuple[str, str]],
    list[str],
]:
    scenario = scenario or Scenario()
    output: list[str] = []
    calls: list[tuple[str, ...]] = []
    requests: list[tuple[str, str]] = []
    actions: list[str] = []
    clock_values = iter([0.0] + [12.5] * 50)

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        parts = [str(part) for part in command]
        calls.append(tuple(parts))
        result: tuple[int, str, str] = (0, "{}", "")
        if "coriolisappliance" in parts:
            metadata = {} if scenario.bad_uid is None else {"uid": scenario.bad_uid}
            result = (0, json.dumps({"metadata": metadata}), "")
        elif "secret" in parts:
            if any(part.endswith("logging-credentials") for part in parts):
                data = {
                    "read_password": _encoded(SECRET),
                    "write_password": _encoded(WRITE),
                }
            else:
                data = {
                    "coriolis_keystone_password": _encoded(KEYSTONE),
                    "temp_keypair_password": _encoded(SIBLING),
                }
            result = (0, json.dumps({"data": data}), "")
        elif "pods" in parts:
            selector = next(
                part for part in parts if part.startswith(f"{COMPONENT_LABEL}=")
            )
            result = (0, _pods_payload(selector.split("=", 1)[1], scenario), "")
        elif "logs" in parts:
            container = parts[parts.index("-c") + 1]
            if container in BOUNDARY_CONTAINERS:
                if container in scenario.boundary_fail:
                    result = (1, "", "error: container is not initialized")
                else:
                    result = (0, scenario.boundary_output.get(container, ""), "")
            elif container in scenario.logs_fail:
                result = (1, "", "error: container is not initilized")
            else:
                result = (0, _logs_stdout(container, scenario), "")
        return subprocess.CompletedProcess(parts, result[0], result[1], result[2])

    def http(
        method: str, url: str, headers: object, body: bytes | None, timeout: int
    ) -> runtime.HttpResponse:
        assert isinstance(headers, dict)
        requests.append((method, url))
        if method == "POST" and url.endswith("/identity/auth/tokens"):
            auth_headers = {"X-Subject-Token": TOKEN}
            if scenario.leak == "auth":
                auth_headers["X-Leak"] = SECRET
            return runtime.HttpResponse(201, auth_headers, b"{}")
        if "/loki/api/v1/query_range" in url:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0]
            component = re.search(r'coriolis_component="([^"]+)"', query).group(1)
            return runtime.HttpResponse(200, {}, _loki_body(component, scenario))
        path = urllib.parse.urlparse(url).path
        if path == "/logs":
            return runtime.HttpResponse(200, {}, _adaptor_list_body(scenario))
        component = path.removeprefix("/logs/")
        status = scenario.adaptor_download_status.get(component, 200)
        payload = b"" if status != 200 else _adaptor_download_body(component, scenario)
        return runtime.HttpResponse(status, {}, payload)

    @contextlib.contextmanager
    def forward(command: object, timeout: int, clock: object, sleeper: object):
        assert isinstance(command, list | tuple)
        calls.append(tuple(str(part) for part in command))
        actions.append("open")
        try:
            yield "http://127.0.0.1:18080"
        finally:
            actions.append("close")

    validator = runtime.Validator(
        context="ctx",
        namespace="ns",
        app="acme",
        host="logs.example.test",
        runner=runner,
        http=http,
        clock=lambda: next(clock_values),
        now=lambda: 1_800_000_000.0,
        sleeper=lambda seconds: None,
        report=output.append,
        forward=forward,
    )
    return validator, output, calls, requests, actions


def test_required_producers_are_exactly_fourteen_with_explicit_boundaries() -> None:
    assert PRODUCERS == runtime.EXPECTED_PRODUCERS
    assert set(PRODUCERS) == set(runtime.EXPECTED_PRODUCERS)
    assert len(PRODUCERS) == 14
    assert BOOTSTRAP in PRODUCERS
    assert runtime.BOOTSTRAP_COMPONENT == BOOTSTRAP
    assert "memcached" not in PRODUCERS
    assert "memcached" in runtime.ALLOY_APP_COLLECTION_COMPONENTS
    assert set(runtime.LOGGING_INFRA_CONTAINERS) == {"loki", "alloy", "adaptor"}
    assert set(runtime.LOGGING_INFRA_CONTAINERS["loki"]) == {"loki", "gateway"}
    assert PRODUCERS.count(BOOTSTRAP) == 1
    for component in (*PRODUCERS, *INFRA):
        assert component not in runtime._SYNTHETIC_PREFIXES


def test_success_emits_deterministic_fixed_stages_and_summary() -> None:
    validator, output, _, _, actions = _make()
    assert validator.run() == 0
    expected = [
        "PASS producers",
        "PASS tenant",
        "PASS credentials",
        "PASS token",
        "PASS readiness",
        "PASS memcached-exempt",
        "PASS logging-infrastructure",
        "PASS gateway",
        "PASS adaptor-list",
        *[f"PASS natural-{component}" for component in PRODUCERS],
        "SUMMARY natural-logs passed 12",
    ]
    assert output == expected
    assert re.fullmatch(r"SUMMARY natural-logs passed \d+", output[-1])
    assert actions == ["open", "close"]


def test_success_output_contains_no_raw_secrets_tokens_or_uids() -> None:
    validator, output, _, _, _ = _make()
    assert validator.run() == 0
    rendered = "\n".join(output)
    for value in (SECRET, WRITE, KEYSTONE, TOKEN, UID, CATEGORY_LINE):
        assert value not in rendered
        assert _encoded(value) not in rendered


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--context", "ctx", "--namespace", "ns", "--app", "acme", "--host", "h"],
        [
            "--run",
            "--context",
            "ctx",
            "--namespace",
            "ns",
            "--app",
            "Bad_App",
            "--host",
            "h",
        ],
        [
            "--run",
            "--context",
            "ctx",
            "--namespace",
            "ns",
            "--app",
            "acme",
            "--host",
            "host;rm -rf",
        ],
        [
            "--run",
            "--context",
            "ctx x",
            "--namespace",
            "ns",
            "--app",
            "acme",
            "--host",
            "h",
        ],
        pytest.param(
            [
                "--run",
                "--context",
                "ctx",
                "--namespace",
                "ns",
                "--app",
                "acme",
                "--host",
                "h",
                "--since-seconds",
                "30",
            ],
            id="window-below-minimum",
        ),
        pytest.param(
            [
                "--run",
                "--context",
                "ctx",
                "--namespace",
                "ns",
                "--app",
                "acme",
                "--host",
                "h",
                "--since-seconds",
                str(runtime.MAX_SINCE_SECONDS + 1),
            ],
            id="window-above-maximum",
        ),
        ["--run"],
    ],
)
def test_cli_guards_are_fixed_and_silent(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert runtime.main(argv) == 2
    assert capsys.readouterr().out == "FAIL cli\n"


def test_window_boundaries_are_inclusive_and_source_bound() -> None:
    assert runtime.DEFAULT_SINCE_SECONDS == 3600
    assert runtime.MIN_SINCE_SECONDS <= runtime.DEFAULT_SINCE_SECONDS
    assert runtime.MAX_SINCE_SECONDS <= 86400


@pytest.mark.parametrize(
    ("since_seconds", "stage"),
    [(30, "cli"), (runtime.MAX_SINCE_SECONDS + 1, "cli"), (True, "cli"), (0, "cli")],
)
def test_constructor_rejects_unsafe_windows(since_seconds: object, stage: str) -> None:
    with pytest.raises(runtime.ValidationFailure, match=f"^{stage}$"):
        runtime.Validator(
            context="ctx",
            namespace="ns",
            app="acme",
            host="h",
            since_seconds=since_seconds,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain message here\n", ["plain message here"]),
        ("alpha line\r\nbeta line\r\n", ["alpha line", "beta line"]),
        ("\x1b[31mred message\x1b[0m\n", ["red message"]),
        (
            "2026-09-08T10:00:00.123456789Z stdout F inside message\n",
            ["inside message"],
        ),
        (
            "2026-09-08T10:00:00.123456789+02:00 stderr P part message\n",
            ["part message"],
        ),
        ("   \n\n", []),
        ("12345 ---- ==== \n", []),
        ("matrix-injected-marker\n", []),
        ("coriolis-reconnect-validator-A\n", []),
        ("replaying synthetic retention marker now\n", []),
        ("\x1b[0m2026-09-08T10:00:00Z stdout F trimmed   \n", ["trimmed"]),
    ],
)
def test_normalization_is_deterministic(raw: str, expected: list[str]) -> None:
    assert runtime._natural_lines(raw) == expected


def test_digests_are_sha256_hex_and_stable() -> None:
    digests = runtime._digests(["hello"])
    assert digests == frozenset(
        {"2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"}
    )


def test_cri_stderr_line_correlates_with_stored_message() -> None:
    pod = runtime._natural_lines(
        "2026-09-08T10:00:00.123456789Z stderr F useful service record\n"
    )
    stored = runtime._natural_lines("useful service record\n")

    assert runtime._digests(pod) == runtime._digests(stored)


def test_all_network_queries_share_one_observation_window() -> None:
    validator, _, _, requests, _ = _make()
    assert validator.run() == 0

    expected_start = 1_800_000_000 - validator.since_seconds
    expected_end = 1_800_000_000
    for method, url in requests:
        if method != "GET" or urllib.parse.urlparse(url).path == "/logs":
            continue
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "/loki/api/v1/query_range" in url:
            assert query["start"] == [str(expected_start * 1_000_000_000)]
            assert query["end"] == [str(expected_end * 1_000_000_000)]
        elif "/logs/" in url:
            assert query["start_date"] == [str(expected_start)]
            assert query["end_date"] == [str(expected_end)]


def test_missing_pod_fails_its_producer_stage() -> None:
    scenario = Scenario()
    scenario.missing_pods = {"mariadb"}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL pod-mariadb"


def test_missing_main_container_fails_its_producer_stage() -> None:
    scenario = Scenario()
    scenario.bad_containers = {"keystone"}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL pod-keystone"


def test_duplicate_eligible_pods_are_rejected_as_ambiguous() -> None:
    scenario = Scenario()
    scenario.duplicate_pods = {"rabbitmq"}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL pod-rabbitmq"


def test_failed_bootstrap_retry_is_ignored_when_successful_pod_exists() -> None:
    scenario = Scenario()
    scenario.failed_bootstrap_retry = True
    validator, output, _, _, _ = _make(scenario)

    assert validator.run() == 0
    assert f"PASS natural-{BOOTSTRAP}" in output


def test_deleting_rollout_pod_is_ignored_when_ready_pod_exists() -> None:
    scenario = Scenario()
    scenario.deleting_extra = {"coriolis-api"}
    validator, output, _, _, _ = _make(scenario)

    assert validator.run() == 0
    assert "PASS natural-coriolis-api" in output


def test_completed_pod_for_non_bootstrap_producer_is_rejected() -> None:
    scenario = Scenario()
    scenario.completed_non_bootstrap = {"coriolis-api"}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL pod-coriolis-api"


def test_failing_logs_command_fails_producer_stage() -> None:
    scenario = Scenario()
    scenario.logs_fail = {"coriolis-web"}
    validator, output, _, _, actions = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL logs-coriolis-web"
    assert actions == []


def test_loki_label_mismatch_is_not_trusted_and_fails() -> None:
    scenario = Scenario()
    scenario.loki_bad_labels = {"barbican-api"}
    validator, output, _, _, actions = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL loki-barbican-api"
    assert actions == ["open", "close"]


def test_loki_line_without_exact_digest_intersection_fails() -> None:
    scenario = Scenario()
    scenario.loki_no_match = {"barbican-worker"}
    validator, output, _, _, actions = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL loki-barbican-worker"
    assert actions == ["open", "close"]


def test_adaptor_list_missing_producer_name_fails() -> None:
    scenario = Scenario()
    scenario.adaptor_missing_names = {"coriolis-web"}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL adaptor"
    assert not any(line.startswith("PASS natural-") for line in output)


def test_adaptor_missing_download_fails_that_producer() -> None:
    scenario = Scenario()
    scenario.adaptor_download_status = {"coriolis-api": 404}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL adaptor-coriolis-api"
    assert "PASS natural-coriolis-api" not in output


def test_adaptor_line_without_intersection_fails_that_producer() -> None:
    scenario = Scenario()
    scenario.adaptor_no_match = {"coriolis-scheduler"}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL adaptor-coriolis-scheduler"


def test_memcached_not_ready_fails_readiness_despite_log_exemption() -> None:
    scenario = Scenario()
    scenario.not_ready = {"memcached"}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL readiness"
    assert "memcached" not in PRODUCERS


@pytest.mark.parametrize("component", ["loki", "alloy", "adaptor"])
def test_logging_infra_not_ready_fails_readiness_without_ingestion(
    component: str,
) -> None:
    scenario = Scenario()
    scenario.not_ready = {component}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL readiness"
    assert component not in PRODUCERS


def test_missing_appliance_uid_fails_tenant_silently() -> None:
    scenario = Scenario()
    scenario.bad_uid = None
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output == ["PASS producers", "FAIL tenant"]
    assert UID not in "\n".join(output)


def test_bootstrap_category_absent_fails_after_other_correlations() -> None:
    scenario = Scenario()
    scenario.no_category = True
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL bootstrap-category"
    assert "PASS natural-mariadb" in output
    assert f"PASS natural-{BOOTSTRAP}" not in output


@pytest.mark.parametrize("surface", ["kubectl", "loki", "adaptor", "auth"])
def test_registered_secret_on_any_audited_surface_fails_without_echo(
    surface: str,
) -> None:
    scenario = Scenario()
    scenario.leak = surface
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL secret-leak"
    rendered = "\n".join(output)
    for value in (SECRET, WRITE, KEYSTONE, TOKEN):
        assert value not in rendered
        assert _encoded(value) not in rendered
        assert urllib.parse.quote(value, safe="") not in rendered


@pytest.mark.parametrize(
    "surface", ["sibling-kubectl", "sibling-loki", "sibling-adaptor"]
)
def test_unused_sibling_secret_is_registered_and_never_echoed(surface: str) -> None:
    scenario = Scenario()
    scenario.leak = surface
    validator, output, _, _, _ = _make(scenario)

    assert validator.run() == 1
    assert output[-1] == "FAIL secret-leak"
    rendered = "\n".join(output)
    assert SIBLING not in rendered
    assert _encoded(SIBLING) not in rendered


def test_only_read_only_kubectl_verbs_are_ever_issued() -> None:
    validator, _, calls, _, _ = _make()
    assert validator.run() == 0
    assert calls
    for command in calls:
        assert command[:5] == ("kubectl", "--context", "ctx", "--namespace", "ns")
        assert command[5] in {"get", "logs", "port-forward"}
        assert not set(command) & {"apply", "create", "patch", "delete", "exec"}


def test_verb_guard_rejects_mutation_before_execution() -> None:
    validator, _, calls, _, _ = _make()
    with pytest.raises(runtime.ValidationFailure, match="^verbs$"):
        validator._checked("guard", validator._kubectl("delete", "pod", "acme-api"))
    assert calls == []


def test_http_surface_is_get_only_except_one_token_post() -> None:
    validator, _, _, requests, _ = _make()
    assert validator.run() == 0
    posts = [(method, url) for method, url in requests if method != "GET"]
    assert posts == [("POST", "https://logs.example.test/identity/auth/tokens")]
    for method, url in requests:
        if method == "GET":
            assert url.startswith(("http://127.0.0.1:", "https://logs.example.test/"))


def _log_commands(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [command for command in calls if command[5] == "logs"]


def test_logs_commands_are_bounded_and_target_the_main_container() -> None:
    validator, _, calls, _, _ = _make()
    assert validator.run() == 0
    producer_logs = _log_commands(calls)[len(BOUNDARY_CONTAINERS) :]
    assert len(producer_logs) == len(PRODUCERS)
    for command, component in zip(producer_logs, PRODUCERS):
        assert command[command.index("-c") + 1] == component
        assert f"--since={validator.since_seconds}s" in command
        assert "--timestamps=false" in command


def test_boundary_logs_use_exact_ready_pods_and_bounded_containers() -> None:
    validator, output, calls, _, _ = _make()
    assert validator.run() == 0
    boundary_logs = _log_commands(calls)[: len(BOUNDARY_CONTAINERS)]
    assert [command[command.index("-c") + 1] for command in boundary_logs] == list(
        BOUNDARY_CONTAINERS
    )
    assert [command[6] for command in boundary_logs] == [
        "acme-memcached-abcde",
        "acme-loki-abcde",
        "acme-loki-abcde",
        "acme-alloy-abcde",
        "acme-adaptor-abcde",
    ]
    for command in boundary_logs:
        assert f"--since={validator.since_seconds}s" in command
        assert "--timestamps=false" in command
    assert "PASS memcached-exempt" in output
    assert "PASS logging-infrastructure" in output


def test_boundary_stages_follow_readiness_before_producer_capture() -> None:
    validator, output, _, _, _ = _make()
    assert validator.run() == 0
    assert output.index("PASS readiness") + 1 == output.index("PASS memcached-exempt")
    assert output.index("PASS memcached-exempt") + 1 == output.index(
        "PASS logging-infrastructure"
    )
    assert output.index("PASS logging-infrastructure") < output.index("PASS gateway")
    assert output.index("PASS gateway") < output.index("PASS natural-mariadb")


def test_boundary_empty_output_passes_and_is_never_correlated() -> None:
    scenario = Scenario()
    scenario.boundary_output = {container: "" for container in BOUNDARY_CONTAINERS}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 0
    assert "PASS logging-infrastructure" in output
    for component in (*BOUNDARY_CONTAINERS, *INFRA):
        assert f"PASS natural-{component}" not in output


def test_boundary_nonzero_command_fails_before_its_explicit_pass() -> None:
    scenario = Scenario()
    scenario.boundary_fail = {"gateway"}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL logs-gateway"
    assert "PASS memcached-exempt" in output
    assert "PASS logging-infrastructure" not in output


@pytest.mark.parametrize("container", BOUNDARY_CONTAINERS)
def test_boundary_secret_output_fails_as_secret_leak_without_echo(
    container: str,
) -> None:
    scenario = Scenario()
    scenario.boundary_output = {container: f"tail note {SECRET} embedded"}
    validator, output, _, _, _ = _make(scenario)
    assert validator.run() == 1
    assert output[-1] == "FAIL secret-leak"
    rendered = "\n".join(output)
    assert SECRET not in rendered
    assert _encoded(SECRET) not in rendered
    if container != "memcached":
        assert "PASS memcached-exempt" in rendered
    assert "PASS logging-infrastructure" not in rendered


def test_port_forward_targets_gateway_service_and_always_closes() -> None:
    scenario = Scenario()
    scenario.loki_no_match = {"coriolis-conductor"}
    validator, _, calls, _, actions = _make(scenario)
    assert validator.run() == 1
    assert actions == ["open", "close"]
    forward = next(command for command in calls if command[5] == "port-forward")
    assert forward[6] == "svc/acme-gateway"


def test_summary_reporter_validates_stage_shape_and_audit() -> None:
    validator, output, _, _, _ = _make()
    assert validator.run() == 0
    assert all(
        re.fullmatch(r"(PASS|FAIL) [a-z0-9][a-z0-9-]*", line)
        or re.fullmatch(r"SUMMARY natural-logs passed \d+", line)
        for line in output
    )


def test_report_rejects_unsafe_stage_names() -> None:
    validator, output, _, _, _ = _make()
    with pytest.raises(runtime.ValidationFailure, match="^report$"):
        validator._report("PASS", "unsafe stage")
    with pytest.raises(runtime.ValidationFailure, match="^report$"):
        validator._report("MAYBE", "ok-stage")
    assert output == []


def test_registry_audit_blocks_every_registered_encoding_form() -> None:
    registry = runtime.SecretRegistry()
    registry.register(SECRET)
    for form in registry.forms:
        with pytest.raises(runtime.ValidationFailure, match="secret-leak"):
            registry.audit(f"payload {form} tail")


def test_credentials_dataclass_and_validator_never_repr_secrets() -> None:
    credentials = runtime.Credentials(SECRET, WRITE, KEYSTONE)
    assert SECRET not in repr(credentials)
    validator, _, _, _, _ = _make()
    assert SECRET not in repr(validator)
    assert KEYSTONE not in repr(validator.credentials)
