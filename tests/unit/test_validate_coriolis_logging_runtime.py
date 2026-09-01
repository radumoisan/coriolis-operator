import base64
import contextlib
import importlib.util
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "validate-coriolis-logging-runtime.py"
SPEC = importlib.util.spec_from_file_location(
    "validate_coriolis_logging_runtime", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)

SECRET = "secret-value/with?encoding"
TOKEN = "token-value/with?encoding"
UID = "cr-uid-123"


def _encoded(value: str = SECRET) -> str:
    return base64.b64encode(value.encode()).decode()


def _pod(restarts: int = 0) -> str:
    return json.dumps(
        {
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [
                    {"name": "loki", "ready": True, "restartCount": 0},
                    {"name": "gateway", "ready": True, "restartCount": restarts},
                    {"name": "adaptor", "ready": True, "restartCount": 0},
                ],
            }
        }
    )


def _deployment() -> str:
    return json.dumps(
        {
            "status": {
                "availableReplicas": 1,
                "readyReplicas": 1,
                "updatedReplicas": 1,
            }
        }
    )


class FakeWs:
    def __init__(self, records: list[str | None]) -> None:
        self.records = iter(records)
        self.closed = False
        self.close_calls = 0

    def recv(self) -> str | None:
        return next(self.records, None)

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1


def _record(marker: str) -> str:
    return json.dumps({"message": f"coriolis-reconnect-validator-{marker}"})


def _validator(
    *,
    records: list[str | None] | None = None,
    restarts: tuple[int, int] = (0, 0),
    output: list[str] | None = None,
    calls: list[tuple[str, ...]] | None = None,
    uid: object = UID,
    basic_auth: list[str] | None = None,
) -> tuple[runtime.Validator, FakeWs, list[tuple[str, ...]]]:
    received = FakeWs(records or [_record("A"), _record("B"), _record("C")])
    recorded = calls if calls is not None else []
    pod_calls = 0
    now = [0.0]

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        nonlocal pod_calls
        assert isinstance(command, list | tuple)
        recorded.append(tuple(command))
        if "coriolisappliance" in command:
            metadata = {} if uid is None else {"uid": uid}
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"metadata": metadata}), ""
            )
        if "secret" in command:
            secret = (
                {"data": {"read_password": _encoded(), "write_password": _encoded()}}
                if any(str(part).endswith("logging-credentials") for part in command)
                else {"data": {"coriolis_keystone_password": _encoded()}}
            )
            return subprocess.CompletedProcess(command, 0, json.dumps(secret), "")
        if "pod" in command:
            pod_calls += 1
            restart = restarts[0] if pod_calls <= 2 else restarts[1]
            return subprocess.CompletedProcess(command, 0, _pod(restart), "")
        if "deployment" in command:
            return subprocess.CompletedProcess(command, 0, _deployment(), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def http(
        method: str, url: str, headers: object, body: bytes | None, timeout: int
    ) -> runtime.HttpResponse:
        if method == "POST" and url.endswith("/auth/tokens"):
            return runtime.HttpResponse(201, {"X-Subject-Token": TOKEN}, b"{}")
        assert isinstance(headers, dict)
        authorization = headers["Authorization"]
        assert isinstance(authorization, str) and authorization.startswith("Basic ")
        if basic_auth is not None:
            basic_auth.append(
                base64.b64decode(authorization.removeprefix("Basic ")).decode()
            )
        assert body is not None
        payload = json.loads(body)
        assert payload["streams"][0]["stream"]["coriolis_appliance"] == "acme"
        return runtime.HttpResponse(204, {}, b"")

    @contextlib.contextmanager
    def forward(command: object, timeout: int, clock: object, sleeper: object):
        assert isinstance(command, list | tuple)
        recorded.append(tuple(command))
        yield "http://127.0.0.1:8080"

    validator = runtime.Validator(
        context="ctx",
        namespace="ns",
        app="acme",
        host="logs.example.test",
        runner=runner,
        http=http,
        ws_factory=lambda url, headers, timeout: received,
        clock=lambda: now.__setitem__(0, now[0] + 0.1) or now[0],
        sleeper=lambda seconds: None,
        report=(output if output is not None else []).append,
        forward=forward,
    )
    return validator, received, recorded


def test_success_streams_a_b_c_once_on_one_client_and_targets_only_worker() -> None:
    output: list[str] = []
    basic_auth: list[str] = []
    validator, ws, calls = _validator(
        records=[
            _record("A"),
            _record("B"),
            _record("C"),
            json.dumps({"message": "unrelated log record"}),
        ],
        output=output,
        basic_auth=basic_auth,
    )
    ws_opens: list[object] = []
    actions: list[str] = []
    validator.ws_factory = lambda url, headers, timeout: ws_opens.append(url) or ws
    original_forward = validator.forward
    original_push = validator._push
    original_expect = validator._expect
    original_terminate = validator._terminate_worker

    @contextlib.contextmanager
    def forward(command: object, timeout: int, clock: object, sleeper: object):
        actions.append("forward-established")
        with original_forward(command, timeout, clock, sleeper) as base_url:
            yield base_url
        actions.append("forward-closed")

    def push(base_url: str, marker: str) -> None:
        actions.append(f"{marker}-push")
        original_push(base_url, marker)

    def expect(client: object, marker: str, stage: str) -> None:
        original_expect(client, marker, stage)
        actions.append(f"{marker}-receive")

    def terminate() -> None:
        actions.append("worker-kill")
        original_terminate()

    validator.forward = forward
    validator._push = push
    validator._expect = expect
    validator._terminate_worker = terminate

    assert validator.run() == 0
    assert output[-1] == "PASS complete"
    assert ws.close_calls == 1
    assert len(ws_opens) == 1
    assert SECRET not in "\n".join(output)
    assert TOKEN not in "\n".join(output)
    assert UID not in "\n".join(output)
    assert basic_auth == [f"coriolis-{UID}:{SECRET}"] * 3
    assert (
        "kubectl",
        "--context",
        "ctx",
        "--namespace",
        "ns",
        "get",
        "coriolisappliance",
        "acme",
        "-o",
        "json",
    ) in calls
    assert sum("port-forward" in command for command in calls) == 1
    assert actions == [
        "forward-established",
        "A-push",
        "A-receive",
        "worker-kill",
        "B-push",
        "B-receive",
        "C-push",
        "C-receive",
        "forward-closed",
    ]
    kill = next(command for command in calls if "exec" in command)
    assert kill[:6] == ("kubectl", "--context", "ctx", "--namespace", "ns", "exec")
    assert kill[kill.index("-c") + 1] == "gateway"
    script = kill[-1]
    assert "/tmp/nginx.pid" in script
    assert "/proc/$master/task/$master/children" in script
    assert "[ $# -eq 1 ]" in script
    assert '"$(cat /proc/$master/comm 2>/dev/null)" = nginx' in script
    assert '"$(cat /proc/$worker/comm 2>/dev/null)" = nginx' in script
    assert script.count("kill -TERM $worker") == 1
    assert script.count("kill ") == 1
    assert "kill -TERM $master" not in script
    assert "delete" not in kill and "rollout" not in kill


def test_worker_kill_script_allows_pid1_master_and_rejects_unsafe_pids() -> None:
    script = runtime.Validator._worker_kill_script()
    # Master PID 1 is no longer rejected syntactically; only comm check gates it.
    assert "''|*[!0-9]*|1)" not in script
    assert "case $master in ''|*[!0-9]*) exit 1;; esac" in script
    # Empty/non-numeric master is still rejected before any /proc read.
    assert script.index("case $master") < script.index("/proc/$master/comm")
    # Worker PID 1 or the master itself remain rejected.
    assert "case $worker in ''|*[!0-9]*|1|$master) exit 1;; esac" in script
    assert script.index("case $worker") < script.index("/proc/$worker/comm")
    # Exactly-one child guard and single worker-targeted signal only.
    assert "set -- $children" in script
    assert "[ $# -eq 1 ] || exit 1" in script
    assert "ps " not in script
    assert "while" not in script and "for " not in script
    assert "$master)" in script
    assert script.rstrip().endswith("kill -TERM $worker")


@pytest.mark.parametrize("uid", [None, "", 7])
def test_missing_or_malformed_appliance_uid_fails_silently(uid: object) -> None:
    output: list[str] = []
    validator, _, _ = _validator(uid=uid, output=output)

    assert validator.run() == 1
    assert output == ["FAIL tenant"]
    assert UID not in "\n".join(output)


@pytest.mark.parametrize(
    ("records", "stage"),
    [
        ([_record("A"), _record("A"), _record("B"), _record("C")], "record-b"),
        ([_record("A"), _record("C")], "record-b"),
        ([_record("A"), _record("B")], "record-c"),
        ([_record("A"), _record("B"), _record("C"), _record("C")], "continuity"),
        ([_record("A"), _record("B"), _record("C"), _record("D")], "continuity"),
    ],
)
def test_duplicate_missing_or_out_of_order_records_fail(
    records: list[str], stage: str
) -> None:
    output: list[str] = []
    validator, _, _ = _validator(records=records, output=output)

    assert validator.run() == 1
    assert output[-1] == f"FAIL {stage}"


def test_closed_client_fails_continuity() -> None:
    output: list[str] = []
    validator, ws, _ = _validator(output=output)
    original = ws.recv

    def recv() -> str | None:
        value = original()
        if value == _record("C"):
            ws.closed = True
        return value

    ws.recv = recv  # type: ignore[method-assign]
    assert validator.run() == 1
    assert output[-1] == "FAIL continuity"


def test_gateway_restart_delta_fails() -> None:
    output: list[str] = []
    validator, _, _ = _validator(restarts=(0, 1), output=output)

    assert validator.run() == 1
    assert output[-1] == "FAIL gateway-restarts"


@pytest.mark.parametrize("failure", ["subprocess", "exception"])
def test_secret_never_appears_in_failure_output(failure: str) -> None:
    output: list[str] = []
    validator, _, _ = _validator(output=output)
    if failure == "subprocess":
        original_runner = validator.runner

        def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
            if "pod" in command:
                return subprocess.CompletedProcess(
                    command, 1, SECRET, base64.b64encode(SECRET.encode()).decode()
                )
            return original_runner(command, timeout)

        validator.runner = runner
    else:
        validator.ws_factory = lambda url, headers, timeout: (_ for _ in ()).throw(
            RuntimeError(SECRET)
        )

    assert validator.run() == 1
    rendered = "\n".join(output)
    assert SECRET not in rendered
    assert base64.b64encode(SECRET.encode()).decode() not in rendered
    assert urllib.parse.quote(SECRET, safe="") not in rendered
    assert json.dumps(SECRET) not in rendered


def test_registry_blocks_secret_in_success_or_failure_report() -> None:
    registry = runtime.CredentialRegistry()
    registry.register(SECRET)
    for form in registry.forms:
        with pytest.raises(runtime.ValidationFailure, match="secret-leak"):
            registry.audit(f"PASS {form}")


def test_cli_output_is_fixed_and_silent(capsys: pytest.CaptureFixture[str]) -> None:
    assert runtime.main(["--context", SECRET]) == 2
    assert capsys.readouterr().out == "FAIL cli\n"
