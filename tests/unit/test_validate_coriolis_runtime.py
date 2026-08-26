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


def test_evidence_files_are_private_and_stage_runtime_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime.secrets, "token_urlsafe", lambda _: "test-secret")
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
    assert (paths.coriolis / "coriolis.conf").read_text() == (
        runtime.render_sensitive_coriolis_config(
            endpoints=runtime.SensitiveCoriolisEndpoints(
                rabbitmq_host="rabbitmq",
                memcached_host="memcached",
                database_host="mariadb",
                keystone_host="keystone",
            ),
            credentials=runtime.SensitiveCoriolisCredentials(
                rabbitmq_password="test-secret",
                coriolis_database_password="test-secret",
                coriolis_keystone_password="test-secret",
                temp_keypair_password="test-secret",
            ),
        )["coriolis.conf"]
    )


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
    assert runtime.WORKER_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/coriolis-worker:2603.4"
        "@sha256:ff30999d6e43709411f197b1b6b80dbce1d7e5498a27f869df93a061626ab2c9"
    )
    assert runtime.DEPLOYER_MANAGER_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/coriolis-deployer-manager:2603.4"
        "@sha256:a2a7091daf8e172b96fa0b48d19ffad285d7bfaad42fc7e8cd44a688f06f36aa"
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
    assert (
        runtime.DEPLOYER_MANAGER_COMMAND == "/usr/local/bin/coriolis-deployer-manager"
    )
    assert runtime.DEPLOYER_MANAGER_ARGS == (
        "--config-file=/etc/coriolis/coriolis.conf",
    )
    assert runtime.DEPLOYER_MANAGER_LOG_DIR == "/var/log/coriolis"
    assert runtime.SCHEDULER_RUN_AS_ID == 42434
    assert runtime.TRANSFER_CRON_RUN_AS_ID == 42434
    assert runtime.MINION_MANAGER_RUN_AS_ID == 42434
    assert runtime.DEPLOYER_MANAGER_RUN_AS_ID == 42434
    assert runtime.MESSAGING_PROBE_FILENAME == "coriolis_messaging_probe.py"
    assert runtime.WORKER_PROBE_FILENAME == "coriolis_worker_probe.py"
    assert runtime.WORKER_COMMAND == (
        "/usr/local/bin/coriolis-worker",
        "--worker-process-count",
        "1",
        "--config-file=/etc/coriolis/coriolis.conf",
    )


def _application_image_payload(
    *,
    entrypoint: str | None = runtime.SCHEDULER_ENTRYPOINT,
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
                    "Entrypoint": [entrypoint] if entrypoint is not None else None,
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


def test_worker_image_contract_uses_frozen_metadata(tmp_path: Path) -> None:
    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            _application_image_payload(
                entrypoint=runtime.WORKER_ENTRYPOINT,
                command=runtime.WORKER_IMAGE_COMMAND,
                ExposedPorts=None,
                Volumes=None,
                Healthcheck=None,
            ),
            "",
        )

    runtime.Validator(
        repository_root=tmp_path, timeout=1, runner=runner
    )._verify_worker_image_contract()


def test_deployer_manager_image_contract_uses_frozen_metadata(tmp_path: Path) -> None:
    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            _application_image_payload(
                entrypoint=runtime.DEPLOYER_MANAGER_ENTRYPOINT,
                command=runtime.DEPLOYER_MANAGER_IMAGE_COMMAND,
                ExposedPorts=None,
                Volumes=None,
                Healthcheck=None,
            ),
            "",
        )

    runtime.Validator(
        repository_root=tmp_path, timeout=1, runner=runner
    )._verify_deployer_manager_image_contract()


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
    assert "expect_worker = sys.argv[1:] == ['worker']" in source
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


def test_messaging_probe_distinguishes_empty_and_registered_worker_phases(
    tmp_path: Path,
) -> None:
    validator = _validator(tmp_path)

    empty = validator._messaging_probe_arguments("empty-probe")[-1]
    registered = validator._messaging_probe_arguments(
        "registered-probe", expect_worker=True
    )[-1]

    assert empty.endswith(runtime.MESSAGING_PROBE_FILENAME)
    assert registered.endswith(f"{runtime.MESSAGING_PROBE_FILENAME} worker")


def test_worker_probe_uses_fixed_markers_and_registration_only_calls() -> None:
    source = runtime.CORIOLIS_WORKER_PROBE
    prints = re.findall(r"print\(([^)]*)\)", source)
    markers = [token.strip().strip("'\"") for token in prints]
    assert set(markers) == {"coriolis-worker-ok", "CORIOLIS_WORKER_FAIL"}
    assert "default_config_files=['/etc/coriolis/coriolis.conf']" in source
    assert "get_admin_context" in source
    assert "WorkerClient(host='coriolis-worker').get_service_status" in source
    assert "get_workers_for_specs(ctxt, enabled=True)" in source
    for required in ("coriolis-worker", "coriolis_worker", "enabled", "status", "UP"):
        assert required in source
    for prohibited in ("provider", "migration", "trust", "create_", "delete_"):
        assert prohibited not in source


def test_new_workload_resource_names_are_unique() -> None:
    resources = runtime.Resources("tok123")
    names = [
        resources.conductor_main,
        resources.scheduler_main,
        resources.transfer_cron_main,
        resources.minion_manager_main,
        resources.deployer_manager_main,
        resources.worker_main,
        resources.api_main,
        resources.messaging_probe,
        resources.rpc_probe,
        resources.worker_probe,
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
        "_stop_worker_graceful",
        "_stop_minion_manager_graceful",
        "_stop_deployer_manager_graceful",
        "_stop_transfer_cron_graceful",
        "_stop_scheduler_graceful",
        "_stop_conductor_graceful",
        "_start_conductor_again",
        "_start_scheduler_again",
        "_start_transfer_cron_again",
        "_start_minion_manager_again",
        "_start_deployer_manager_again",
        "_start_worker_again",
    ):
        monkeypatch.setattr(validator, method, lambda: None)
    monkeypatch.setattr(validator, "_stage", record_stage)

    validator._shutdown_restart_evidence()

    assert stages == [
        "stop-deployer-manager-graceful",
        "stop-worker-graceful",
        "stop-minion-manager-graceful",
        "stop-transfer-cron-graceful",
        "stop-scheduler-graceful",
        "stop-conductor-graceful",
        "start-conductor-again",
        "start-scheduler-again",
        "start-transfer-cron-again",
        "start-minion-manager-again",
        "start-deployer-manager-again",
        "start-worker-again",
    ]


def test_stop_graceful_timeouts_15_15_30_30_45_30_and_requires_exit_zero(
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
    validator._stop_deployer_manager_graceful()
    validator._stop_scheduler_graceful()
    validator._stop_conductor_graceful()
    validator._stop_worker_graceful()

    stop_commands = [
        command
        for command in captured
        if len(command) >= 2 and command[:2] == ("docker", "stop")
    ]
    assert len(stop_commands) == 6
    by_name = {
        command[command.index("--time") + 2]: command[command.index("--time") + 1]
        for command in stop_commands
    }
    assert by_name[validator.resources.transfer_cron_main] == "15"
    assert by_name[validator.resources.minion_manager_main] == "15"
    assert by_name[validator.resources.scheduler_main] == "30"
    assert by_name[validator.resources.conductor_main] == "45"
    assert by_name[validator.resources.deployer_manager_main] == "30"
    assert by_name[validator.resources.worker_main] == "30"

    exit_checks = [
        command for command in captured if "--format={{.State.ExitCode}}" in command
    ]
    assert len(exit_checks) == 6
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


def test_deployer_manager_runtime_arguments_allow_tmp_and_log_writes(
    tmp_path: Path,
) -> None:
    validator = _validator(tmp_path)
    args = validator._deployer_manager_runtime_arguments("oc-deployer-manager")

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
        f"dst={runtime.DEPLOYER_MANAGER_CONFIG_DIR},readonly"
    ) in rendered
    assert args.count("--tmpfs") == 2
    assert "/tmp:rw,noexec,nosuid,size=64m" in rendered
    assert (
        f"{runtime.DEPLOYER_MANAGER_LOG_DIR}:rw,noexec,nosuid,size=64m,"
        "uid=42434,gid=42434,mode=0700"
    ) in rendered
    assert args[args.index("--entrypoint") + 1] == runtime.DEPLOYER_MANAGER_COMMAND
    assert args[args.index(runtime.DEPLOYER_MANAGER_IMAGE) + 1 :] == list(
        runtime.DEPLOYER_MANAGER_ARGS
    )


def test_worker_runtime_arguments_are_privileged_and_registration_only(
    tmp_path: Path,
) -> None:
    validator = _validator(tmp_path)
    args = validator._worker_runtime_arguments("oc-worker")
    rendered = " ".join(args)

    assert args[args.index("--hostname") + 1] == runtime.WORKER_HOSTNAME
    assert args[args.index("--user") + 1] == "0:0"
    assert "--privileged" in args
    assert "--read-only" in args
    assert args[args.index("--network") + 1] == validator.resources.network
    assert "--network-alias" not in args
    assert "--publish" not in args and "-p" not in args
    assert "type=bind" not in rendered
    assert args.count("--mount") == 1
    assert (
        f"type=volume,src={validator.resources.coriolis_config_volume},"
        f"dst={runtime.API_CONFIG_DIR},readonly"
    ) in rendered
    assert args.count("--tmpfs") == 3
    for path in ("/tmp", runtime.API_LOG_DIR, "/opt/coriolis/export"):
        assert path in rendered
    assert args[args.index("--entrypoint") + 1] == runtime.WORKER_COMMAND[0]
    assert args[args.index(runtime.WORKER_IMAGE) + 1 :] == list(
        runtime.WORKER_COMMAND[1:]
    )


def _worker_inspect_payload(
    validator: runtime.Validator,
    *,
    privileged: bool = True,
    ports: object = None,
    mounts: list[dict[str, object]] | None = None,
    tmpfs: dict[str, str] | None = None,
    binds: object = None,
) -> str:
    return json.dumps(
        [
            {
                "Config": {
                    "Image": runtime.WORKER_IMAGE,
                    "Hostname": runtime.WORKER_HOSTNAME,
                    "User": "0:0",
                    "Entrypoint": [runtime.WORKER_COMMAND[0]],
                    "Cmd": list(runtime.WORKER_COMMAND[1:]),
                },
                "HostConfig": {
                    "Privileged": privileged,
                    "ReadonlyRootfs": True,
                    "NetworkMode": validator.resources.network,
                    "PortBindings": ports,
                    "Binds": binds,
                    "Devices": None,
                    "DeviceRequests": None,
                    "VolumesFrom": None,
                    "Mounts": mounts
                    or [
                        {
                            "Type": "volume",
                            "Source": validator.resources.coriolis_config_volume,
                            "Target": runtime.API_CONFIG_DIR,
                            "ReadOnly": True,
                        }
                    ],
                    "Tmpfs": tmpfs
                    or {
                        "/tmp": "rw,noexec,nosuid,size=64m",
                        runtime.API_LOG_DIR: "rw,noexec,nosuid,size=64m",
                        "/opt/coriolis/export": "rw,noexec,nosuid,size=64m",
                    },
                },
                "NetworkSettings": {"Networks": {validator.resources.network: {}}},
            }
        ]
    )


def test_worker_inspector_accepts_exact_privileged_contract(tmp_path: Path) -> None:
    validator = _validator(tmp_path)
    validator.runner = lambda command, timeout: subprocess.CompletedProcess(
        command, 0, _worker_inspect_payload(validator), ""
    )
    validator._inspect_worker_runtime()


@pytest.mark.parametrize(
    ("privileged", "ports", "mounts", "tmpfs", "binds"),
    [
        (False, None, None, None, None),
        (True, {"80/tcp": [{"HostPort": "80"}]}, None, None, None),
        (
            True,
            None,
            [
                {"Target": runtime.API_CONFIG_DIR, "ReadOnly": True},
                {"Target": "/x", "ReadOnly": True},
            ],
            None,
            None,
        ),
        (True, None, None, {"/tmp": "rw", runtime.API_LOG_DIR: "rw"}, None),
        (True, None, None, None, ["/host:/container"]),
    ],
)
def test_worker_inspector_rejects_extra_surfaces(
    tmp_path: Path,
    privileged: bool,
    ports: object,
    mounts: list[dict[str, object]] | None,
    tmpfs: dict[str, str] | None,
    binds: object,
) -> None:
    validator = _validator(tmp_path)
    validator.runner = lambda command, timeout: subprocess.CompletedProcess(
        command,
        0,
        _worker_inspect_payload(
            validator,
            privileged=privileged,
            ports=ports,
            mounts=mounts,
            tmpfs=tmpfs,
            binds=binds,
        ),
        "",
    )
    with pytest.raises(runtime.ValidationFailure, match="inspect-worker"):
        validator._inspect_worker_runtime()


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


def test_worker_provider_module_roots_freeze_frozen_sets() -> None:
    assert runtime.WORKER_PROVIDER_MODULE_ROOTS == (
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
    assert runtime.WORKER_EXCLUDED_PROVIDER_MODULE_ROOTS == (
        "coriolis_provider_oracle_vm",
        "coriolis_provider_opc",
        "coriolis_provider_nutanix",
        "coriolis_provider_cloudstack",
    )
    assert not set(runtime.WORKER_PROVIDER_MODULE_ROOTS) & set(
        runtime.WORKER_EXCLUDED_PROVIDER_MODULE_ROOTS
    )


def test_worker_provider_probe_source_is_no_import_no_output(tmp_path: Path) -> None:
    validator = runtime.Validator(repository_root=tmp_path, timeout=1)
    source = validator._worker_provider_probe_source()

    assert "print(" not in source
    assert "import importlib.util as _u" in source
    assert "find_spec" in source
    assert "'" not in source
    for root in runtime.WORKER_PROVIDER_MODULE_ROOTS:
        assert f'find_spec("{root}") is None' in source
    for root in runtime.WORKER_EXCLUDED_PROVIDER_MODULE_ROOTS:
        assert f'find_spec("{root}") is not None' in source
    for prohibited in ("import coriolis", "from coriolis", "os.", "subprocess"):
        assert prohibited not in source


def test_worker_provider_image_contract_hardens_and_sanitizes_failure(
    tmp_path: Path,
) -> None:
    captured: list[tuple[str, ...]] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, tuple | list)
        captured.append(tuple(command))
        return subprocess.CompletedProcess(command, 1, "", "")

    validator = runtime.Validator(repository_root=tmp_path, timeout=1, runner=runner)
    with pytest.raises(
        runtime.ValidationFailure, match="worker-provider-image-contract"
    ):
        validator._verify_worker_provider_image_contract()

    assert len(captured) == 1
    command = captured[0]
    assert command[:2] == ("docker", "run")
    assert "--rm" in command
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in command
    assert command[command.index("--entrypoint") + 1] == "/bin/sh"
    assert runtime.WORKER_IMAGE in command
    shell = command[-1]
    assert shell.startswith("set -eu; python3 -c '")
    assert "find_spec" in shell
    assert shell.count("'") == 2


def test_worker_provider_image_contract_passes_on_zero_exit(
    tmp_path: Path,
) -> None:
    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "", "")

    runtime.Validator(
        repository_root=tmp_path, timeout=1, runner=runner
    )._verify_worker_provider_image_contract()


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
