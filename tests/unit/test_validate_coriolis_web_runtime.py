import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "validate-coriolis-web-runtime.py"
SPEC = importlib.util.spec_from_file_location("validate_coriolis_web_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)


def _validator(tmp_path: Path, **kwargs: object) -> runtime.Validator:
    return runtime.Validator(repository_root=tmp_path, timeout=30, **kwargs)


def _image_payload(**changes: object) -> str:
    config: dict[str, object] = {
        "User": "",
        "Entrypoint": list(runtime.ENTRYPOINT),
        "Cmd": None,
        "WorkingDir": runtime.WORKDIR,
        "ExposedPorts": {runtime.PORT: {}},
        "Volumes": None,
        "Healthcheck": None,
    }
    config.update(changes)
    return json.dumps([{"Os": "linux", "Architecture": "amd64", "Config": config}])


def _runtime_payload(
    validator: runtime.Validator,
    *,
    running: bool = True,
    exit_code: int = 0,
    restart_count: int = 0,
    **changes: object,
) -> str:
    config: dict[str, object] = {
        "Image": runtime.IMAGE,
        "User": "0:0",
        "Entrypoint": list(runtime.ENTRYPOINT),
        "Cmd": None,
        "WorkingDir": runtime.WORKDIR,
        "Env": ["BIND=0.0.0.0", "PATH=/usr/local/bin"],
    }
    host: dict[str, object] = {
        "ReadonlyRootfs": False,
        "Privileged": False,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges"],
        "NetworkMode": "none",
        "PortBindings": None,
        "Binds": None,
        "Devices": None,
        "DeviceRequests": None,
        "VolumesFrom": None,
    }
    config.update(changes.pop("config", {}))
    host.update(changes.pop("host", {}))
    payload = {
        "Config": config,
        "HostConfig": host,
        "Mounts": changes.pop("mounts", []),
        "RestartCount": restart_count,
        "State": {"Running": running, "ExitCode": exit_code},
    }
    payload.update(changes)
    return json.dumps([payload])


def test_runtime_command_preserves_entrypoint_and_hardens_without_surfaces(
    tmp_path: Path,
) -> None:
    validator = _validator(tmp_path)
    command = validator._runtime_arguments()

    assert command[:2] == ["docker", "run"]
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--user") + 1] == "0:0"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert command[command.index("--env") + 1] == "BIND=0.0.0.0"
    assert command[-1] == runtime.IMAGE
    assert "--entrypoint" not in command
    assert "--privileged" not in command
    assert "--read-only" not in command
    assert not {"--publish", "-p", "--mount", "--volume", "--device"} & set(command)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ExposedPorts", {"80/tcp": {}}),
        ("Cmd", ["coriolis-web"]),
        ("WorkingDir", "/tmp"),
        ("Healthcheck", {"Test": ["CMD", "true"]}),
    ],
)
def test_image_contract_rejects_non_frozen_metadata(
    tmp_path: Path, field: str, value: object
) -> None:
    validator = _validator(tmp_path)
    validator.runner = lambda command, timeout: subprocess.CompletedProcess(
        command, 0, _image_payload(**{field: value}), ""
    )

    with pytest.raises(runtime.ValidationFailure, match="image-contract"):
        validator._verify_image_contract()


def test_live_contract_rejects_hardening_and_environment_regressions(
    tmp_path: Path,
) -> None:
    validator = _validator(tmp_path)
    validator.runner = lambda command, timeout: subprocess.CompletedProcess(
        command,
        0,
        _runtime_payload(
            validator,
            config={"Env": ["BIND=0.0.0.0", "CA_FINGERPRINT=sentinel"]},
            host={"ReadonlyRootfs": True, "SecurityOpt": ["seccomp=unconfined"]},
        ),
        "",
    )

    with pytest.raises(runtime.ValidationFailure, match="inspect-runtime"):
        validator._inspect_runtime()


def test_endpoint_probes_are_fixed_in_container_and_never_print_bodies(
    tmp_path: Path,
) -> None:
    validator = _validator(tmp_path)
    for expectation, path, method, status in (
        ("root", "/", "GET", "status === 200"),
        ("config-true", "/api/config", "GET", "firstLaunch(payload) === true"),
        ("fingerprint", "/proxy/metal-hub/fingerprint", "GET", "status === 500"),
        ("first-launch", "/api/config/first-launch", "POST", "status === 200"),
        ("config-false", "/api/config", "GET", "firstLaunch(payload) === false"),
    ):
        source = validator._probe_source(expectation)
        assert path in source
        assert json.dumps(method) in source
        assert status in source
        assert "print(" not in source
        assert "console." not in source
    assert json.dumps(runtime.FIRST_LAUNCH_BODY) in validator._probe_source(
        "first-launch"
    )
    assert runtime.FIRST_LAUNCH_BODY == '{"isFirstLaunch":false}'

    config_source = validator._probe_source("config-true")
    assert "relativeUrls" not in config_source
    assert (
        "const services=value&&value.config&&value.config.servicesUrls" in config_source
    )
    assert (
        "!services||typeof services!=='object'||Array.isArray(services)"
        in config_source
    )
    for key, value in (
        ("keystone", "/identity"),
        ("barbican", "/barbican"),
        ("coriolis", "/coriolis"),
        ("coriolisLogs", "/logs"),
        ("coriolisLogStreamBaseUrl", ""),
        ("coriolisLicensing", "/licensing"),
        ("metalhub", "/metal-hub"),
    ):
        assert f"services.{key}==='{value}'" in config_source
    assert "cloudbaseEmailEndpoint" not in config_source
    assert "value.isFirstLaunch" in config_source
    assert "first_launch" not in config_source
    assert "value.firstLaunch" not in config_source
    assert "JSON.parse(data)" in config_source

    root_source = validator._probe_source("root")
    assert "res.resume()" in root_source
    assert "JSON.parse" not in root_source


def test_live_contract_rejects_new_forbidden_environment_name(tmp_path: Path) -> None:
    validator = _validator(tmp_path)
    validator.runner = lambda command, timeout: subprocess.CompletedProcess(
        command,
        0,
        _runtime_payload(
            validator,
            config={"Env": ["BIND=0.0.0.0", "CORIOLIS_LICENSING_BASE_URL=sentinel"]},
        ),
        "",
    )

    with pytest.raises(runtime.ValidationFailure, match="inspect-runtime"):
        validator._inspect_runtime()


def test_stop_accepts_only_upstream_exit_one_within_bound(tmp_path: Path) -> None:
    now = [0.0]
    commands: list[tuple[str, ...]] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        commands.append(tuple(command))
        if command[1] == "stop":
            now[0] += 0.7
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(
            command, 0, _runtime_payload(validator, running=False, exit_code=1), ""
        )

    validator = _validator(tmp_path, runner=runner, clock=lambda: now[0])
    validator._stop()

    stop = next(command for command in commands if command[1] == "stop")
    assert stop[stop.index("--time") + 1] == "15"
    assert runtime.STOP_COMPLETION_BOUND == 5.0


def test_stop_rejects_exit_zero_and_slow_stop(tmp_path: Path) -> None:
    validator = _validator(
        tmp_path,
        runner=lambda command, timeout: subprocess.CompletedProcess(
            command, 0, _runtime_payload(validator, running=False, exit_code=0), ""
        ),
    )
    with pytest.raises(runtime.ValidationFailure, match="stop-runtime-exit-code"):
        validator._stop()

    now = [0.0]

    def slow_runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        if command[1] == "stop":
            now[0] = 6.0
        return subprocess.CompletedProcess(command, 0, "", "")

    validator = _validator(tmp_path, runner=slow_runner, clock=lambda: now[0])
    with pytest.raises(runtime.ValidationFailure, match="stop-runtime-slow"):
        validator._stop()


def test_cleanup_is_attempted_on_failure_and_output_is_sanitized(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    output: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        calls.append(tuple(command))
        if command[1:3] == ["image", "pull"]:
            return subprocess.CompletedProcess(
                command, 1, "TOKEN_SENTINEL", "BODY_SENTINEL"
            )
        if command[1] in {"ps", "network", "volume"}:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(
            command, 0, "TOKEN_SENTINEL", "BODY_SENTINEL"
        )

    validator = _validator(tmp_path, runner=runner, report=output.append)
    assert validator.run() == 1
    rendered = "\n".join(output)
    assert "TOKEN_SENTINEL" not in rendered
    assert "BODY_SENTINEL" not in rendered
    assert any(command[1:3] == ("container", "rm") for command in calls)
    assert any(command[1] == "network" for command in calls)
    assert any(command[1] == "volume" for command in calls)


def test_cleanup_leftovers_fail_without_printing_names(tmp_path: Path) -> None:
    output: list[str] = []
    validator = _validator(tmp_path, report=output.append)
    validator.runner = lambda command, timeout: subprocess.CompletedProcess(
        command,
        0,
        f"{runtime.PREFIX}-leftover" if command[1] == "ps" else "",
        "",
    )

    with pytest.raises(runtime.ValidationFailure, match="cleanup-container-leftovers"):
        validator._verify_cleanup()
    assert runtime.PREFIX not in "\n".join(output)


def test_cleanup_succeeds_without_creating_other_resource_types(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    validator = _validator(tmp_path)

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    validator.runner = runner
    validator._cleanup()
    validator._verify_cleanup()

    assert calls[0][1:3] == ("container", "rm")
    assert all("login" not in command for command in calls)


def test_cli_requires_run() -> None:
    with pytest.raises(SystemExit) as error:
        runtime.main([])
    assert error.value.code == 2
