import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "validate-keystone-runtime.py"
sys.path.insert(0, str(SCRIPT.parent.parent / "src"))
SPEC = importlib.util.spec_from_file_location("validate_keystone_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)


def test_evidence_files_are_private_and_keep_secrets_off_commands(
    tmp_path: Path,
) -> None:
    paths = runtime.create_evidence_files(tmp_path)

    assert paths.scratch.stat().st_mode & 0o777 == 0o700
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in paths.scratch.rglob("*")
        if path.is_file()
    )
    assert "keystone" in (paths.secret / "keystone.cnf").read_text()


def test_keystone_inputs_keep_database_and_admin_secrets_out_of_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runtime.secrets, "token_urlsafe", lambda size: "PASSWORD_SENTINEL:/?#[]@"
    )
    paths = runtime.create_evidence_files(tmp_path, "mariadb-sentinel")
    config = (paths.keystone / "keystone.conf").read_text()
    wrapper = (paths.keystone / "bootstrap.py").read_text()

    assert "PASSWORD_SENTINEL%3A%2F%3F%23%5B%5D%40" in config
    assert "mariadb-sentinel" in config
    assert "PASSWORD_SENTINEL" not in wrapper
    assert "admin_password" in wrapper
    for attribute in (
        "admin_username = 'admin'",
        "project_name = 'admin'",
        "admin_role_name = 'admin'",
        "region_id = 'RegionOne'",
        "service_name = 'keystone'",
        "public_url = 'http://keystone:5000/v3'",
        "internal_url = 'http://keystone:5000/v3'",
        "admin_url = 'http://keystone:5000/v3'",
        "immutable_roles = False",
    ):
        assert attribute in wrapper


def test_keystone_commands_use_files_and_hardened_runtime(tmp_path: Path) -> None:
    validator = runtime.Validator(repository_root=tmp_path, timeout=1)
    validator.paths = runtime.create_evidence_files(tmp_path)
    arguments = validator._keystone_arguments(
        container_name="keystone", keys_readonly=False
    )

    assert arguments[arguments.index("--user") + 1] == "42425:42425"
    assert arguments[arguments.index("--group-add") + 1] == "42400"
    assert "--read-only" in arguments
    assert "--cap-drop" in arguments
    assert "no-new-privileges" in arguments
    assert not any("type=bind" in argument for argument in arguments)
    assert any("uid=42425,gid=42425,mode=0700" in item for item in arguments)


def test_keystone_stages_keep_sentinels_out_of_commands_and_use_safe_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, tuple | list)
        commands.append(tuple(command))
        if "inspect" in command:
            return subprocess.CompletedProcess(command, 0, "true\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(runtime.secrets, "token_urlsafe", lambda size: "ADMIN_SENTINEL")
    validator = runtime.Validator(repository_root=tmp_path, timeout=1, runner=runner)
    validator.paths = runtime.create_evidence_files(tmp_path)
    validator._keystone_evidence()

    rendered = "\n".join(" ".join(command) for command in commands)
    assert "ADMIN_SENTINEL" not in rendered
    assert "auth-request.json" in rendered
    assert "--data-binary @/evidence/keystone/auth-request.json" in rendered
    assert "sha256sum" not in rendered
    assert (
        "keystone-manage --config-file /evidence/keystone/keystone.conf db_sync"
        in rendered
    )
    assert "db_sync --check" in rendered
    assert "--port 5000 -- --config-file" in rendered
    assert "find" in rendered and "600:42425:42425" in rendered


def test_database_stages_are_separate_defaults_first_and_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[str, ...]] = []
    output: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, tuple | list)
        commands.append(tuple(command))
        code = 1 if "reject-unrelated.sql" in " ".join(command) else 0
        if "inspect" in command:
            return subprocess.CompletedProcess(command, 0, "true\n", "")
        return subprocess.CompletedProcess(
            command, code, "SECRET_STDOUT", "SECRET_STDERR"
        )

    monkeypatch.setattr(
        runtime.secrets, "token_urlsafe", lambda size: "PASSWORD_SENTINEL"
    )
    validator = runtime.Validator(
        repository_root=tmp_path, timeout=1, runner=runner, report=output.append
    )
    validator.paths = runtime.create_evidence_files(tmp_path)
    validator._database_evidence()

    rendered = " ".join(" ".join(command) for command in commands)
    assert "SECRET_STDOUT" not in "\n".join(output)
    assert "SECRET_STDERR" not in "\n".join(output)
    assert "PASSWORD_SENTINEL" not in rendered
    assert "PASSWORD_SENTINEL" not in "\n".join(output)
    assert "create-database-repeat" in "\n".join(output)
    assert "mariadb --defaults-file=" in rendered
    assert (
        "--defaults-file=/evidence/secret/keystone.cnf --execute=SELECT 1" in rendered
    )
    rejection = next(
        command for command in commands if "reject-unrelated.sql" in " ".join(command)
    )
    assert "/evidence/secret/keystone.cnf" in " ".join(rejection)


def test_failure_is_sanitized_and_cleanup_is_attempted(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    output: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, tuple | list)
        calls.append(tuple(command))
        return subprocess.CompletedProcess(command, 1, "SENTINEL_OUT", "SENTINEL_ERR")

    validator = runtime.Validator(
        repository_root=tmp_path, timeout=1, runner=runner, report=output.append
    )
    assert validator.run() == 1
    text = "\n".join(output)
    assert "SENTINEL_OUT" not in text
    assert "SENTINEL_ERR" not in text
    assert any(command[1:3] == ("container", "rm") for command in calls)
    assert any(command[1:3] == ("volume", "rm") for command in calls)
    cleanup = "\n".join(
        " ".join(command) for command in calls if " rm " in f" {' '.join(command)} "
    )
    for resource in (
        validator.resources.keystone_main_container,
        validator.resources.keystone_probe_container,
        validator.resources.keystone_one_shot_container,
        validator.resources.keystone_key_prepare_container,
        validator.resources.keystone_config_volume,
        validator.resources.fernet_volume,
        validator.resources.credential_volume,
    ):
        assert resource in cleanup


def test_unrelated_database_must_be_rejected_and_cleanup_stage_runs(
    tmp_path: Path,
) -> None:
    output: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, tuple | list)
        joined = " ".join(command)
        code = 1 if "reject-unrelated.sql" in joined else 0
        if "inspect" in command:
            return subprocess.CompletedProcess(command, 0, "true\n", "")
        return subprocess.CompletedProcess(command, code, "", "")

    validator = runtime.Validator(
        repository_root=tmp_path, timeout=1, runner=runner, report=output.append
    )
    validator.paths = runtime.create_evidence_files(tmp_path)
    validator._database_evidence()
    assert "PASS reject-unrelated-database" in "\n".join(output)
    assert "PASS cleanup-unrelated-database" in "\n".join(output)


def test_cli_requires_run() -> None:
    with pytest.raises(SystemExit) as error:
        runtime.main([])
    assert error.value.code == 2


def test_runtime_uses_kolla_group_only_as_supplemental(tmp_path: Path) -> None:
    validator = runtime.Validator(repository_root=tmp_path, timeout=1)
    validator.paths = runtime.create_evidence_files(tmp_path)

    arguments = validator._runtime_arguments(detached=True)

    user_index = arguments.index("--user")
    group_index = arguments.index("--group-add")
    assert arguments[user_index + 1] == "42434:42434"
    assert arguments[group_index + 1] == "42400"
    assert not any("type=bind" in argument for argument in arguments)


def test_staging_keeps_secret_values_out_of_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, tuple | list)
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        runtime.secrets, "token_urlsafe", lambda size: "STAGING_SECRET_SENTINEL"
    )
    validator = runtime.Validator(repository_root=tmp_path, timeout=1, runner=runner)
    validator.paths = runtime.create_evidence_files(tmp_path)

    validator._stage_inputs()

    rendered = " ".join(" ".join(command) for command in commands)
    assert "STAGING_SECRET_SENTINEL" not in rendered
    assert "type=bind" in rendered
    assert "readonly" in rendered
    assert "chmod 0400" in rendered
    assert "chmod 0500" in rendered


def test_runtime_prepare_is_separate_from_daemon_start(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, tuple | list)
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    validator = runtime.Validator(repository_root=tmp_path, timeout=1, runner=runner)
    validator.paths = runtime.create_evidence_files(tmp_path)

    validator._prepare_runtime()
    validator._start()

    prepared, started = (" ".join(command) for command in commands)
    assert "prepare-mariadb.sh" in prepared
    assert "start-mariadb.sh" not in prepared
    assert "start-mariadb.sh" in started
    assert "prepare-mariadb.sh" not in started


def test_exit_log_classification_never_surfaces_captured_output(tmp_path: Path) -> None:
    output: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, tuple | list)
        if "inspect" in command:
            return subprocess.CompletedProcess(command, 0, "false\n", "")
        if "logs" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "PASSWORD_SENTINEL permission denied SECRET_STDOUT",
                "SECRET_STDERR",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    validator = runtime.Validator(
        repository_root=tmp_path,
        timeout=1,
        runner=runner,
        report=output.append,
    )

    with pytest.raises(runtime.ValidationFailure) as error:
        validator._poll_ready()

    assert error.value.stage == "database-container-exited-permission"
    rendered = f"{error.value}\n" + "\n".join(output)
    assert "PASSWORD_SENTINEL" not in rendered
    assert "SECRET_STDOUT" not in rendered
    assert "SECRET_STDERR" not in rendered
