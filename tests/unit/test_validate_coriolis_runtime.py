import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "validate-coriolis-bootstrap-runtime.py"
)
sys.path.insert(0, str(SCRIPT.parent.parent / "src"))
SPEC = importlib.util.spec_from_file_location("validate_coriolis_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)

_EXPECTED_RUNTIME_CONFIG_FILES = (
    "coriolis.conf",
    "api-paste.ini",
    "policy.yml",
    "vixdisklib.conf",
    "coriolis.release",
)


def _validator(tmp_path: Path) -> runtime.Validator:
    return runtime.Validator(repository_root=tmp_path, timeout=1)


def test_evidence_files_are_private_and_stage_runtime_config(tmp_path: Path) -> None:
    paths = runtime.create_evidence_files(tmp_path)

    assert paths.scratch.stat().st_mode & 0o777 == 0o700
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in paths.scratch.rglob("*")
        if path.is_file()
    )
    assert all(
        (paths.coriolis / name).exists() for name in _EXPECTED_RUNTIME_CONFIG_FILES
    )
    assert (paths.coriolis / "coriolis_rpc_probe.py").exists()


def test_rpc_probe_emits_only_fixed_markers_and_no_sensitive_origin() -> None:
    source = runtime.CORIOLIS_RPC_PROBE

    prints = re.findall(r"print\(([^)]*)\)", source)
    markers = [token.strip().strip("'\"") for token in prints]
    assert set(markers) == {"coriolis-rpc-ok", "CORIOLIS_RPC_FAIL"}

    for sensitive_fragment in (
        "X-Auth-Token",
        "password",
        "project_id",
        "token",
        "json.dumps",
        "headers",
        "environ",
        "read_text",
    ):
        assert not re.search(rf"print\([^)]*{re.escape(sensitive_fragment)}", source), (
            sensitive_fragment
        )


def test_application_constants_match_frozen_facts() -> None:
    assert runtime.SCHEDULER_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/coriolis-scheduler:2603.4"
        "@sha256:45bea9e0bab4cac0fdddee6d3eac52006d12cf7de1e798e2949dd9ebc2a73c41"
    )
    assert runtime.TRANSFER_CRON_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/coriolis-transfer-cron:2603.4"
        "@sha256:3a44d3b40ba92dff9217b8e7d6a7ca3e7a202efa2641c771ce9b2a3552b3ea9c"
    )
    assert runtime.MINION_MANAGER_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/coriolis-minion-manager:2603.4"
        "@sha256:1ea016dd967ce249a45cf9937701a45880f3b42f8146a93d1f5eb4f1d84e1fb9"
    )
    assert runtime.SCHEDULER_ENTRYPOINT == "/entrypoint.sh"
    assert runtime.TRANSFER_CRON_ENTRYPOINT == "/entrypoint.sh"
    assert runtime.MINION_MANAGER_ENTRYPOINT == "/entrypoint.sh"
    assert runtime.SCHEDULER_COMMAND == (
        "/usr/local/bin/coriolis-scheduler",
        "--config-file=/etc/coriolis/coriolis.conf",
    )
    assert runtime.TRANSFER_CRON_COMMAND == (
        "/usr/local/bin/coriolis-transfer-cron",
        "--config-file=/etc/coriolis/coriolis.conf",
    )
    assert runtime.MINION_MANAGER_COMMAND == (
        "/usr/local/bin/coriolis-minion-manager",
        "--config-file=/etc/coriolis/coriolis.conf",
    )
    assert runtime.SCHEDULER_RUN_AS_ID == 42434
    assert runtime.TRANSFER_CRON_RUN_AS_ID == 42434
    assert runtime.MINION_MANAGER_RUN_AS_ID == 42434
    assert runtime.MESSAGING_PROBE_FILENAME == "coriolis_messaging_probe.py"


def _application_image_payload(
    *,
    entrypoint: str = runtime.SCHEDULER_ENTRYPOINT,
    command: tuple[str, ...] = runtime.SCHEDULER_COMMAND,
    **metadata: object,
) -> str:
    return json.dumps(
        [
            {
                "Os": "linux",
                "Architecture": "amd64",
                "Config": {
                    "User": "",
                    "Entrypoint": [entrypoint],
                    "Cmd": list(command),
                    **metadata,
                },
            }
        ]
    )


def test_application_image_contract_accepts_empty_runtime_metadata(
    tmp_path: Path,
) -> None:
    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            _application_image_payload(ExposedPorts={}, Volumes=None, Healthcheck={}),
            "",
        )

    runtime.Validator(
        repository_root=tmp_path, timeout=1, runner=runner
    )._verify_application_image_contract(
        "image-contract",
        runtime.SCHEDULER_IMAGE,
        runtime.SCHEDULER_ENTRYPOINT,
        runtime.SCHEDULER_COMMAND,
    )


def test_minion_manager_image_contract_uses_frozen_metadata(tmp_path: Path) -> None:
    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            _application_image_payload(
                entrypoint=runtime.MINION_MANAGER_ENTRYPOINT,
                command=runtime.MINION_MANAGER_COMMAND,
                ExposedPorts=None,
                Volumes=None,
                Healthcheck=None,
            ),
            "",
        )

    runtime.Validator(
        repository_root=tmp_path, timeout=1, runner=runner
    )._verify_minion_manager_image_contract()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ExposedPorts", {"80/tcp": {}}),
        ("Volumes", {"/var/lib/coriolis": {}}),
        ("Healthcheck", {"Test": ["CMD", "true"]}),
    ],
)
def test_application_image_contract_rejects_runtime_metadata(
    tmp_path: Path, field: str, value: object
) -> None:
    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, _application_image_payload(**{field: value}), ""
        )

    with pytest.raises(runtime.ValidationFailure, match="image-contract"):
        runtime.Validator(
            repository_root=tmp_path, timeout=1, runner=runner
        )._verify_application_image_contract(
            "image-contract",
            runtime.SCHEDULER_IMAGE,
            runtime.SCHEDULER_ENTRYPOINT,
            runtime.SCHEDULER_COMMAND,
        )


def test_messaging_probe_staged_privately_and_emits_only_fixed_markers(
    tmp_path: Path,
) -> None:
    paths = runtime.create_evidence_files(tmp_path)
    probe = paths.coriolis / runtime.MESSAGING_PROBE_FILENAME
    assert probe.exists()
    assert probe.stat().st_mode & 0o777 == 0o600

    source = runtime.CORIOLIS_MESSAGING_PROBE
    prints = re.findall(r"print\(([^)]*)\)", source)
    markers = [token.strip().strip("'\"") for token in prints]
    assert set(markers) == {"coriolis-messaging-ok", "CORIOLIS_MESSAGING_FAIL"}

    for sensitive_fragment in (
        "password",
        "diagnostics",
        "packages",
        "hostname",
        "environ",
        "read_text",
        "exception",
        "get_diagnostics_info",
    ):
        assert not re.search(rf"print\([^)]*{re.escape(sensitive_fragment)}", source), (
            sensitive_fragment
        )


def test_messaging_probe_uses_generated_config_only_and_admin_context() -> None:
    source = runtime.CORIOLIS_MESSAGING_PROBE
    assert "default_config_files=['/etc/coriolis/coriolis.conf']" in source
    assert "get_admin_context" in source
    assert "SchedulerClient" in source
    assert "TransferCronClient" in source
    assert "MinionManagerClient" in source
    assert "get_workers_for_specs" in source
    assert "NoWorkerServiceError" in source
    assert "get_diagnostics" in source
    assert "get_minion_pools" in source
    for prohibited_call in (
        "create_minion_pool",
        "allocate_minion_pool",
        "refresh_minion_pool",
        "deallocate_minion_pool",
        "delete_minion_pool",
    ):
        assert prohibited_call not in source


def test_new_workload_resource_names_are_unique() -> None:
    resources = runtime.Resources("tok123")
    names = [
        resources.conductor_main,
        resources.scheduler_main,
        resources.transfer_cron_main,
        resources.minion_manager_main,
        resources.api_main,
        resources.messaging_probe,
        resources.rpc_probe,
        resources.rabbitmq_main,
        resources.memcached_main,
        resources.network,
    ]
    assert len(set(names)) == len(names)


def test_shutdown_restarts_workloads_in_safe_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = _validator(tmp_path)
    stages: list[str] = []

    def record_stage(name: str, action: object) -> None:
        assert callable(action)
        stages.append(name)

    for method in (
        "_stop_minion_manager_graceful",
        "_stop_transfer_cron_graceful",
        "_stop_scheduler_graceful",
        "_stop_conductor_graceful",
        "_start_conductor_again",
        "_start_scheduler_again",
        "_start_transfer_cron_again",
        "_start_minion_manager_again",
    ):
        monkeypatch.setattr(validator, method, lambda: None)
    monkeypatch.setattr(validator, "_stage", record_stage)

    validator._shutdown_restart_evidence()

    assert stages == [
        "stop-minion-manager-graceful",
        "stop-transfer-cron-graceful",
        "stop-scheduler-graceful",
        "stop-conductor-graceful",
        "start-conductor-again",
        "start-scheduler-again",
        "start-transfer-cron-again",
        "start-minion-manager-again",
    ]


def test_stop_graceful_timeouts_15_15_15_30_and_requires_exit_zero(
    tmp_path: Path,
) -> None:
    captured: list[tuple[str, ...]] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, tuple | list)
        captured.append(tuple(command))
        if "--format={{.State.ExitCode}}" in " ".join(command):
            return subprocess.CompletedProcess(command, 0, "0\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    validator = runtime.Validator(repository_root=tmp_path, timeout=1, runner=runner)
    validator._stop_transfer_cron_graceful()
    validator._stop_minion_manager_graceful()
    validator._stop_scheduler_graceful()
    validator._stop_conductor_graceful()

    stop_commands = [
        command
        for command in captured
        if len(command) >= 2 and command[:2] == ("docker", "stop")
    ]
    assert len(stop_commands) == 4
    by_name = {
        command[command.index("--time") + 2]: command[command.index("--time") + 1]
        for command in stop_commands
    }
    assert by_name[validator.resources.transfer_cron_main] == "15"
    assert by_name[validator.resources.minion_manager_main] == "15"
    assert by_name[validator.resources.scheduler_main] == "15"
    assert by_name[validator.resources.conductor_main] == "30"

    exit_checks = [
        command for command in captured if "--format={{.State.ExitCode}}" in command
    ]
    assert len(exit_checks) == 4
    assert all(
        validator.runner(command, 1).stdout.strip() == "0" for command in exit_checks
    )


def test_scheduler_runtime_arguments_harden_no_ports_and_no_locks(
    tmp_path: Path,
) -> None:
    validator = _validator(tmp_path)
    args = validator._scheduler_runtime_arguments("oc-scheduler")

    rendered = " ".join(args)
    assert args[args.index("--user") + 1] == "42434:42434"
    assert "--detach" in args
    assert "--read-only" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in args
    assert "--network-alias" not in args
    assert "HOME=/tmp" in args
    assert "PYTHONDONTWRITEBYTECODE=1" in args
    assert "-p" not in args and "--publish" not in args
    assert "PortBindings" not in rendered
    assert "type=bind" not in rendered
    assert runtime.API_LOCKS_DIR not in rendered
    assert "/opt/coriolis/locks" not in rendered
    assert (
        f"type=volume,src={validator.resources.coriolis_config_volume},"
        f"dst={runtime.API_CONFIG_DIR},readonly"
    ) in rendered
    assert "/tmp:rw,noexec,nosuid,size=64m" in rendered
    assert f"{runtime.API_LOG_DIR}:rw,noexec,nosuid,size=64m" in rendered
    assert args[args.index("--entrypoint") + 1] == runtime.SCHEDULER_COMMAND[0]
    assert args[args.index(runtime.SCHEDULER_IMAGE) + 1 :] == list(
        runtime.SCHEDULER_COMMAND[1:]
    )


def test_transfer_cron_runtime_arguments_harden_no_ports_and_no_locks(
    tmp_path: Path,
) -> None:
    validator = _validator(tmp_path)
    args = validator._transfer_cron_runtime_arguments("oc-transfer-cron")

    rendered = " ".join(args)
    assert args[args.index("--user") + 1] == "42434:42434"
    assert "--detach" in args
    assert "--read-only" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in args
    assert "--network-alias" not in args
    assert "type=bind" not in rendered
    assert runtime.API_LOCKS_DIR not in rendered
    assert "-p" not in args and "--publish" not in args
    assert (
        f"type=volume,src={validator.resources.coriolis_config_volume},"
        f"dst={runtime.API_CONFIG_DIR},readonly"
    ) in rendered
    assert f"{runtime.API_LOG_DIR}:rw,noexec,nosuid,size=64m" in rendered
    assert args[args.index("--entrypoint") + 1] == runtime.TRANSFER_CRON_COMMAND[0]
    assert args[args.index(runtime.TRANSFER_CRON_IMAGE) + 1 :] == list(
        runtime.TRANSFER_CRON_COMMAND[1:]
    )


def test_minion_manager_runtime_arguments_harden_no_ports_and_no_locks(
    tmp_path: Path,
) -> None:
    validator = _validator(tmp_path)
    args = validator._minion_manager_runtime_arguments("oc-minion-manager")

    rendered = " ".join(args)
    assert args[args.index("--user") + 1] == "42434:42434"
    assert "--detach" in args
    assert "--read-only" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in args
    assert "--network-alias" not in args
    assert "type=bind" not in rendered
    assert runtime.API_LOCKS_DIR not in rendered
    assert "-p" not in args and "--publish" not in args
    assert (
        f"type=volume,src={validator.resources.coriolis_config_volume},"
        f"dst={runtime.API_CONFIG_DIR},readonly"
    ) in rendered
    assert "/tmp:rw,noexec,nosuid,size=64m" in rendered
    assert f"{runtime.API_LOG_DIR}:rw,noexec,nosuid,size=64m" in rendered
    assert args[args.index("--entrypoint") + 1] == runtime.MINION_MANAGER_COMMAND[0]
    assert args[args.index(runtime.MINION_MANAGER_IMAGE) + 1 :] == list(
        runtime.MINION_MANAGER_COMMAND[1:]
    )


def _inspect_payload(
    validator: runtime.Validator,
    run_as_id: int,
    *,
    mounts: list[dict[str, object]] | None = None,
    tmpfs: dict[str, str] | None = None,
) -> str:
    return json.dumps(
        [
            {
                "Config": {
                    "Image": runtime.SCHEDULER_IMAGE,
                    "User": f"{run_as_id}:{run_as_id}",
                    "Entrypoint": [runtime.SCHEDULER_COMMAND[0]],
                    "Cmd": list(runtime.SCHEDULER_COMMAND[1:]),
                },
                "HostConfig": {
                    "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges"],
                    "NetworkMode": validator.resources.network,
                    "PortBindings": None,
                    "Mounts": mounts
                    or [{"Target": runtime.API_CONFIG_DIR, "ReadOnly": True}],
                    "Tmpfs": tmpfs
                    or {
                        "/tmp": "rw,noexec,nosuid,size=64m",
                        runtime.API_LOG_DIR: "rw,noexec,nosuid,size=64m",
                    },
                },
            }
        ]
    )


def test_inspect_runtime_parameterizes_run_as_and_writable_paths(
    tmp_path: Path,
) -> None:
    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, _inspect_payload(validator, 42434), ""
        )

    validator = runtime.Validator(repository_root=tmp_path, timeout=1, runner=runner)
    validator._inspect_runtime(
        "inspect-scheduler",
        validator.resources.scheduler_main,
        runtime.SCHEDULER_IMAGE,
        runtime.SCHEDULER_COMMAND[0],
        runtime.SCHEDULER_COMMAND[1:],
        run_as_id=42434,
        writable_paths=("/tmp", runtime.API_LOG_DIR),
    )

    with pytest.raises(runtime.ValidationFailure):
        validator._inspect_runtime(
            "inspect-scheduler",
            validator.resources.scheduler_main,
            runtime.SCHEDULER_IMAGE,
            runtime.SCHEDULER_COMMAND[0],
            runtime.SCHEDULER_COMMAND[1:],
            run_as_id=99999,
            writable_paths=("/tmp", runtime.API_LOG_DIR),
        )

    with pytest.raises(runtime.ValidationFailure):
        validator._inspect_runtime(
            "inspect-scheduler",
            validator.resources.scheduler_main,
            runtime.SCHEDULER_IMAGE,
            runtime.SCHEDULER_COMMAND[0],
            runtime.SCHEDULER_COMMAND[1:],
            run_as_id=42434,
            writable_paths=("/tmp", runtime.API_LOG_DIR, runtime.API_LOCKS_DIR),
        )


@pytest.mark.parametrize(
    ("mounts", "tmpfs"),
    [
        (
            [
                {"Target": runtime.API_CONFIG_DIR, "ReadOnly": True},
                {"Target": runtime.API_LOCKS_DIR, "ReadOnly": False},
            ],
            None,
        ),
        (
            None,
            {
                "/tmp": "rw,noexec,nosuid,size=64m",
                runtime.API_LOG_DIR: "rw,noexec,nosuid,size=64m",
                runtime.API_LOCKS_DIR: "rw,noexec,nosuid,size=64m",
            },
        ),
    ],
)
def test_inspect_runtime_rejects_extra_runtime_surfaces(
    tmp_path: Path,
    mounts: list[dict[str, object]] | None,
    tmpfs: dict[str, str] | None,
) -> None:
    validator = _validator(tmp_path)

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            _inspect_payload(validator, 42434, mounts=mounts, tmpfs=tmpfs),
            "",
        )

    validator.runner = runner
    with pytest.raises(runtime.ValidationFailure, match="inspect-scheduler"):
        validator._inspect_runtime(
            "inspect-scheduler",
            validator.resources.scheduler_main,
            runtime.SCHEDULER_IMAGE,
            runtime.SCHEDULER_COMMAND[0],
            runtime.SCHEDULER_COMMAND[1:],
            run_as_id=42434,
            writable_paths=("/tmp", runtime.API_LOG_DIR),
        )


def test_image_pull_failure_retains_scheduler_stage(tmp_path: Path) -> None:
    validator = _validator(tmp_path)

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            int(command[-1] == runtime.SCHEDULER_IMAGE),
            "",
            "",
        )

    validator.runner = runner
    with pytest.raises(runtime.ValidationFailure, match="scheduler-image-available"):
        validator._run_body()
