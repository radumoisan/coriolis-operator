import os
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from coriolis_operator.mariadb import (
    MARIADB_CONFIG_KEYS,
    MARIADB_IMAGE,
    MARIADB_SECRET_CONFIG_KEYS,
    SensitiveMariaDBConfig,
    SensitiveMariaDBCredentials,
    render_mariadb_config,
    render_sensitive_mariadb_config,
    resolve_mariadb_settings,
)


def _valid_settings() -> tuple[dict[str, object], dict[str, object]]:
    return (
        {"mariadb": {"storageClassName": "standard", "size": "10Gi"}},
        {
            "mariadb": {
                "requests": {"cpu": "500m", "memory": "512Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            }
        },
    )


def test_resolve_mariadb_settings_validates_quantities_without_mutating_input() -> None:
    storage, resources = _valid_settings()
    settings = resolve_mariadb_settings(storage=storage, resources=resources)

    assert settings.storage.storage_class_name == "standard"
    assert settings.storage.size == "10Gi"
    assert settings.resources.requests_cpu == "500m"
    assert settings.resources.requests_memory == "512Mi"
    assert settings.resources.limits_cpu == "1"
    assert settings.resources.limits_memory == "1Gi"
    assert storage == {"mariadb": {"storageClassName": "standard", "size": "10Gi"}}
    with pytest.raises(FrozenInstanceError):
        settings.storage.size = "20Gi"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("storage", "resources"),
    [
        (None, None),
        ({"mariadb": {"storageClassName": "standard"}}, _valid_settings()[1]),
        (_valid_settings()[0], {"mariadb": {"requests": {}}}),
        ([], _valid_settings()[1]),
        ({"mariadb": {"storageClassName": " ", "size": "1Gi"}}, _valid_settings()[1]),
        (
            {"mariadb": {"storageClassName": "bad\nname", "size": "1Gi"}},
            _valid_settings()[1],
        ),
        (
            {"mariadb": {"storageClassName": "standard", "size": "nope"}},
            _valid_settings()[1],
        ),
        (
            {"mariadb": {"storageClassName": "standard", "size": "0"}},
            _valid_settings()[1],
        ),
        (
            {"mariadb": {"storageClassName": "standard", "size": "-1Gi"}},
            _valid_settings()[1],
        ),
        (
            {"mariadb": {"storageClassName": "standard", "size": "NaN"}},
            _valid_settings()[1],
        ),
        (
            _valid_settings()[0],
            {
                "mariadb": {
                    "requests": {"cpu": "2", "memory": "1Gi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                }
            },
        ),
    ],
)
def test_resolve_mariadb_settings_rejects_invalid_input_without_value_leaks(
    storage: object, resources: object
) -> None:
    with pytest.raises(ValueError, match="^invalid MariaDB settings$"):
        resolve_mariadb_settings(storage=storage, resources=resources)


def test_rendered_mariadb_values_are_exactly_partitioned_and_follow_contract() -> None:
    non_sensitive = render_mariadb_config()
    credentials = SensitiveMariaDBCredentials(
        database_password="ADMIN_SENTINEL",
        coriolis_database_password="CORIOLIS_SENTINEL",
    )
    sensitive = render_sensitive_mariadb_config(credentials=credentials)

    assert set(non_sensitive) == MARIADB_CONFIG_KEYS
    assert set(sensitive) == MARIADB_SECRET_CONFIG_KEYS
    assert MARIADB_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/mariadb-server:2023.1-ubuntu-jammy"
        "@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e"
    )
    assert "wsrep_on=OFF" in non_sensitive["my.cnf"]
    assert "bind-address=0.0.0.0" in non_sensitive["my.cnf"]
    assert "log-error" not in non_sensitive["my.cnf"]
    assert (
        "mariadbd --defaults-file=/etc/mariadb/my.cnf --console"
        in non_sensitive["start-mariadb.sh"]
    )
    assert (
        "mariadb-install-db --datadir=/var/lib/mysql "
        "--skip-test-db "
        "--auth-root-authentication-method=normal"
        in non_sensitive["prepare-mariadb.sh"]
    )
    assert "install -d" not in non_sensitive["prepare-mariadb.sh"]
    start_script = non_sensitive["start-mariadb.sh"]
    assert "rm -f /run/mysqld/bootstrap-complete" in start_script
    assert "mariadb-admin" not in start_script
    assert 'kill -TERM "$mariadb_pid" 2>/dev/null || true' in start_script
    assert 'set +e\n    wait "$mariadb_pid"\n    exit $?' in start_script
    assert "ADMIN_SENTINEL" not in "".join(non_sensitive.values())
    assert "CORIOLIS_SENTINEL" not in "".join(non_sensitive.values())
    assert "ADMIN_SENTINEL" not in repr(credentials)
    assert "ADMIN_SENTINEL" not in repr(sensitive)
    assert str(sensitive) == "SensitiveMariaDBConfig(<redacted>)"
    with pytest.raises(FrozenInstanceError):
        credentials.database_password = "replacement"  # type: ignore[misc]


def test_start_script_recovers_marker_states_behaviorally(tmp_path: Path) -> None:
    def execute(
        *, script: Path, runtime: Path, state: Path, binaries: Path
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(script)],
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "LOG": str(state / "log"),
                "PATH": f"{binaries}:{os.environ['PATH']}",
                "RUNTIME": str(runtime),
                "STATE": str(state),
            },
            text=True,
            timeout=5,
        )

    def run_scenario(
        name: str,
        *,
        first_marker: bool,
        unsecured_root: bool,
        stale_completion: bool,
        fail_bootstrap: bool,
    ) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
        scenario = tmp_path / name
        runtime = scenario / "runtime"
        config = scenario / "config"
        state = scenario / "state"
        binaries = scenario / "bin"
        for path in (runtime, config, state, binaries):
            path.mkdir(parents=True)
        (runtime / "admin.cnf").touch()
        (runtime / "bootstrap.sql").touch()
        if first_marker:
            (runtime / "first-initialization").touch()
            (state / "expect-first-marker").touch()
        if unsecured_root:
            (state / "unsecured-root").touch()
        else:
            (state / "admin-ready").touch()
        if stale_completion:
            (runtime / "bootstrap-complete").touch()
        if fail_bootstrap:
            (state / "fail-bootstrap").touch()

        daemon = binaries / "mariadbd"
        daemon.write_text(
            "#!/bin/sh\n"
            'if [ -e "$RUNTIME/bootstrap-complete" ]; then\n'
            "    printf '%s\\n' daemon-saw-stale-completion >> \"$LOG\"\n"
            "    exit 1\n"
            "fi\n"
            "printf '%s\\n' daemon-started >> \"$LOG\"\n"
            "trap 'exit 0' TERM\n"
            'printf \'%s\\n\' "$$" > "$STATE/daemon.pid"\n'
            "while :; do sleep 1; done\n"
        )
        mariadb = binaries / "mariadb"
        mariadb.write_text(
            "#!/bin/sh\n"
            'while [ ! -e "$STATE/daemon.pid" ]; do sleep 0.01; done\n'
            'case "$*" in\n'
            '  *"--execute=SELECT 1"*)\n'
            '    case "$*" in\n'
            '      *"--user=root"*)\n'
            "        printf '%s\\n' query-passwordless >> \"$LOG\"\n"
            '        test -e "$STATE/unsecured-root"\n'
            "        ;;\n"
            "      *)\n"
            "        printf '%s\\n' query-admin >> \"$LOG\"\n"
            '        test -e "$STATE/admin-ready"\n'
            "        ;;\n"
            "    esac\n"
            "    exit $?\n"
            "    ;;\n"
            "  *)\n"
            '    case "$*" in\n'
            '      *"--user=root"*) mode=passwordless ;;\n'
            "      *) mode=admin ;;\n"
            "    esac\n"
            '    printf \'%s\\n\' "bootstrap-$mode" >> "$LOG"\n'
            '    if [ -e "$STATE/expect-first-marker" ] && '
            '[ ! -e "$RUNTIME/first-initialization" ]; then\n'
            "        printf '%s\\n' bootstrap-without-first-marker >> \"$LOG\"\n"
            "    fi\n"
            '    while [ ! -e "$STATE/daemon.pid" ]; do sleep 0.01; done\n'
            '    kill "$(cat "$STATE/daemon.pid")"\n'
            '    if [ -e "$STATE/fail-bootstrap" ]; then exit 1; fi\n'
            '    rm -f "$STATE/unsecured-root"\n'
            '    touch "$STATE/admin-ready"\n'
            "    ;;\n"
            "esac\n"
        )
        daemon.chmod(0o755)
        mariadb.chmod(0o755)

        script = scenario / "start-mariadb.sh"
        script.write_text(
            render_mariadb_config()["start-mariadb.sh"]
            .replace("/run/mysqld", str(runtime))
            .replace("/etc/mariadb", str(config))
        )
        result = execute(script=script, runtime=runtime, state=state, binaries=binaries)
        return result, runtime, (state / "log").read_text().splitlines()

    fresh, fresh_runtime, fresh_log = run_scenario(
        "fresh",
        first_marker=True,
        unsecured_root=True,
        stale_completion=False,
        fail_bootstrap=False,
    )
    assert fresh.returncode == 0
    assert fresh_log == [
        "daemon-started",
        "query-passwordless",
        "bootstrap-passwordless",
    ]
    assert not (fresh_runtime / "first-initialization").exists()
    assert (fresh_runtime / "bootstrap-complete").exists()

    fresh_state = fresh_runtime.parent / "state"
    fresh_state.joinpath("expect-first-marker").unlink()
    fresh_state.joinpath("daemon.pid").unlink()
    restart = execute(
        script=fresh_runtime.parent / "start-mariadb.sh",
        runtime=fresh_runtime,
        state=fresh_state,
        binaries=fresh_runtime.parent / "bin",
    )
    assert restart.returncode == 0
    restart_log = fresh_state.joinpath("log").read_text().splitlines()
    assert restart_log == fresh_log + [
        "daemon-started",
        "query-admin",
        "bootstrap-admin",
    ]
    assert "daemon-saw-stale-completion" not in restart_log
    assert not (fresh_runtime / "first-initialization").exists()
    assert (fresh_runtime / "bootstrap-complete").exists()

    recovery, recovery_runtime, recovery_log = run_scenario(
        "recovery",
        first_marker=True,
        unsecured_root=False,
        stale_completion=False,
        fail_bootstrap=False,
    )
    assert recovery.returncode == 0
    assert recovery_log == [
        "daemon-started",
        "query-passwordless",
        "query-admin",
        "bootstrap-admin",
    ]
    assert not (recovery_runtime / "first-initialization").exists()
    assert (recovery_runtime / "bootstrap-complete").exists()

    failure, failure_runtime, failure_log = run_scenario(
        "failure",
        first_marker=True,
        unsecured_root=True,
        stale_completion=False,
        fail_bootstrap=True,
    )
    assert failure.returncode != 0
    assert failure_log == [
        "daemon-started",
        "query-passwordless",
        "bootstrap-passwordless",
    ]
    assert (failure_runtime / "first-initialization").exists()
    assert not (failure_runtime / "bootstrap-complete").exists()


def test_sensitive_rendering_escapes_credentials_and_redacts_errors() -> None:
    credentials = SensitiveMariaDBCredentials(
        database_password="admin\\\"'",
        coriolis_database_password="coriolis\\'",
    )
    values = render_sensitive_mariadb_config(credentials=credentials)

    assert values["admin.cnf"].splitlines()[2] == 'password="admin\\\\\\"\'"'
    assert "IDENTIFIED BY 'admin\\\\\"\\'';" in values["bootstrap.sql"]
    with pytest.raises(
        ValueError, match="^invalid sensitive MariaDB configuration input$"
    ):
        render_sensitive_mariadb_config(credentials=None)  # type: ignore[arg-type]
    with pytest.raises(
        ValueError, match="^invalid sensitive MariaDB configuration input$"
    ):
        render_sensitive_mariadb_config(
            credentials=SensitiveMariaDBCredentials(
                database_password="", coriolis_database_password="secret"
            )
        )
    assert "admin" not in repr(SensitiveMariaDBConfig({"x": "secret"}))


@pytest.mark.parametrize("control", ["\n", "\r", "\0", "\t", "\x1a"])
def test_sensitive_rendering_rejects_control_characters_without_value_leaks(
    control: str,
) -> None:
    sentinel = f"CONTROL_SENTINEL{control}"

    with pytest.raises(ValueError) as error:
        render_sensitive_mariadb_config(
            credentials=SensitiveMariaDBCredentials(
                database_password=sentinel, coriolis_database_password="safe-password"
            )
        )

    assert str(error.value) == "invalid sensitive MariaDB configuration input"
    assert sentinel not in str(error.value)
