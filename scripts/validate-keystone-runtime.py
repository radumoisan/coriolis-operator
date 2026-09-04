#!/usr/bin/env python3
"""Run disposable local MariaDB and Keystone runtime evidence."""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from coriolis_operator.mariadb import (  # type: ignore[import-untyped]
    MARIADB_ADMIN_CNF_PATH,
    MARIADB_BOOTSTRAP_COMPLETE_MARKER,
    MARIADB_CONFIG_DIR,
    MARIADB_DATA_DIR,
    MARIADB_RUN_AS_ID,
    MARIADB_RUNTIME_DIR,
    MARIADB_SECRET_DIR,
    MARIADB_SUPPLEMENTAL_GROUP,
    SensitiveMariaDBCredentials,
    render_mariadb_config,
    render_sensitive_mariadb_config,
)

IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/mariadb-server:2023.1-ubuntu-jammy"
    "@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e"
)
KEYSTONE_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/keystone:2023.1-ubuntu-jammy"
    "@sha256:7c57962762f5e6fdb1a109097e8f3e2e5f6218ad9c09f10a585adb67ed245cf0"
)
KEYSTONE_ID = "42425"
KOLLA_GROUP = "42400"
PREFIX = "oc-keystone-db-evidence"
DEFAULT_TIMEOUT = 120
POLL_INTERVAL = 1.0
UNRELATED_DATABASE = "evidence_unrelated"

CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
Reporter = Callable[[str], None]


class ValidationFailure(Exception):
    """A sanitized failure that identifies only its stable validation stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"validation failed: {stage}")
        self.stage = stage


@dataclass(frozen=True)
class EvidencePaths:
    scratch: Path
    public: Path
    secret: Path
    stages: Path
    keystone: Path


@dataclass(frozen=True)
class Resources:
    token: str

    @property
    def data_volume(self) -> str:
        return f"{PREFIX}-{self.token}-data"

    @property
    def runtime_volume(self) -> str:
        return f"{PREFIX}-{self.token}-runtime"

    @property
    def public_volume(self) -> str:
        return f"{PREFIX}-{self.token}-public"

    @property
    def secret_volume(self) -> str:
        return f"{PREFIX}-{self.token}-secret"

    @property
    def stages_volume(self) -> str:
        return f"{PREFIX}-{self.token}-stages"

    @property
    def network(self) -> str:
        return f"{PREFIX}-{self.token}-network"

    @property
    def keystone_config_volume(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-config"

    @property
    def fernet_volume(self) -> str:
        return f"{PREFIX}-{self.token}-fernet"

    @property
    def credential_volume(self) -> str:
        return f"{PREFIX}-{self.token}-credential"

    @property
    def prepare_container(self) -> str:
        return f"{PREFIX}-{self.token}-prepare"

    @property
    def staging_container(self) -> str:
        return f"{PREFIX}-{self.token}-staging"

    @property
    def main_container(self) -> str:
        return f"{PREFIX}-{self.token}-mariadb"

    @property
    def keystone_key_prepare_container(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-key-prepare"

    @property
    def keystone_one_shot_container(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-one-shot"

    @property
    def keystone_probe_container(self) -> str:
        return f"{PREFIX}-{self.token}-keystone-probe"

    @property
    def keystone_main_container(self) -> str:
        return f"{PREFIX}-{self.token}-keystone"


def _run(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), check=False, capture_output=True, text=True, timeout=timeout
    )


def _sql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _option_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def create_evidence_files(
    repository_root: Path, mariadb_hostname: str = "mariadb"
) -> EvidencePaths:
    """Create the only on-host files used by the disposable evidence run."""
    scratch = Path(tempfile.mkdtemp(prefix=f"{PREFIX}-", dir=repository_root))
    try:
        scratch.chmod(0o700)
        public = scratch / "public"
        secret_dir = scratch / "secret"
        stages = scratch / "stages"
        keystone = scratch / "keystone"
        for directory in (public, secret_dir, stages, keystone):
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)

        root_password = secrets.token_urlsafe(32)
        coriolis_password = secrets.token_urlsafe(32)
        keystone_password = secrets.token_urlsafe(32)
        barbican_password = secrets.token_urlsafe(32)
        admin_password = secrets.token_urlsafe(32)
        for name, content in render_mariadb_config().items():
            _write_private(public / name, content)
        credentials = SensitiveMariaDBCredentials(
            database_password=root_password,
            coriolis_database_password=coriolis_password,
            keystone_database_password=keystone_password,
            barbican_database_password=barbican_password,
        )
        for name, content in render_sensitive_mariadb_config(
            credentials=credentials
        ).items():
            _write_private(secret_dir / name, content)
        _write_private(
            secret_dir / "keystone.cnf",
            '[client]\nuser=keystone\npassword="'
            + _option_escape(keystone_password)
            + '"\nhost=127.0.0.1\nport=3306\n',
        )
        escaped = _sql_escape(keystone_password)
        sql = {
            "create-database.sql": "CREATE DATABASE IF NOT EXISTS keystone;\n",
            "create-user.sql": (
                f"CREATE USER IF NOT EXISTS 'keystone'@'%' IDENTIFIED BY '{escaped}';\n"
            ),
            "set-password.sql": (
                f"ALTER USER 'keystone'@'%' IDENTIFIED BY '{escaped}';\n"
            ),
            "grant-keystone.sql": (
                "GRANT ALL PRIVILEGES ON keystone.* TO 'keystone'@'%';\n"
            ),
            "flush.sql": "FLUSH PRIVILEGES;\n",
            "create-table.sql": (
                "CREATE TABLE keystone.evidence (id INT); "
                "DROP TABLE keystone.evidence;\n"
            ),
            "reject-unrelated.sql": f"CREATE DATABASE {UNRELATED_DATABASE};\n",
            "cleanup-unrelated.sql": f"DROP DATABASE IF EXISTS {UNRELATED_DATABASE};\n",
        }
        for name, content in sql.items():
            _write_private(stages / name, content)
        encoded_keystone_password = quote(keystone_password, safe="")
        _write_private(
            keystone / "keystone.conf",
            "[DEFAULT]\nuse_stderr = true\nuse_syslog = false\n"
            "[database]\nconnection = mysql+pymysql://keystone:"
            f"{encoded_keystone_password}@{mariadb_hostname}:3306/keystone\n"
            "[token]\nprovider = fernet\n"
            "[fernet_tokens]\nkey_repository = /etc/keystone/fernet-keys\n"
            "[fernet_receipts]\nkey_repository = /etc/keystone/fernet-keys\n"
            "[credential]\nprovider = fernet\n"
            "key_repository = /etc/keystone/credential-keys\n"
            "[cache]\nenabled = false\n",
        )
        _write_private(keystone / "admin-password", admin_password + "\n")
        _write_private(
            keystone / "auth-request.json",
            json.dumps(
                {
                    "auth": {
                        "identity": {
                            "methods": ["password"],
                            "password": {
                                "user": {
                                    "name": "admin",
                                    "domain": {"name": "Default"},
                                    "password": admin_password,
                                }
                            },
                        },
                        "scope": {
                            "project": {
                                "name": "admin",
                                "domain": {"name": "Default"},
                            }
                        },
                    }
                }
            ),
        )
        _write_private(
            keystone / "bootstrap.py",
            "from pathlib import Path\n"
            "from keystone import server\n"
            "from keystone.cmd.bootstrap import Bootstrapper\n"
            "config = '/evidence/keystone/keystone.conf'\n"
            "server.configure(config_files=[config])\n"
            "bootstrapper = Bootstrapper()\n"
            "password_file = Path('/evidence/keystone/admin-password')\n"
            "bootstrapper.admin_password = password_file.read_text().strip()\n"
            "bootstrapper.admin_username = 'admin'\n"
            "bootstrapper.project_name = 'admin'\n"
            "bootstrapper.admin_role_name = 'admin'\n"
            "bootstrapper.region_id = 'RegionOne'\n"
            "bootstrapper.service_name = 'keystone'\n"
            "bootstrapper.public_url = 'http://keystone:5000/v3'\n"
            "bootstrapper.internal_url = 'http://keystone:5000/v3'\n"
            "bootstrapper.admin_url = 'http://keystone:5000/v3'\n"
            "bootstrapper.immutable_roles = False\n"
            "bootstrapper.bootstrap()\n",
        )
        return EvidencePaths(
            scratch=scratch,
            public=public,
            secret=secret_dir,
            stages=stages,
            keystone=keystone,
        )
    except Exception:
        shutil.rmtree(scratch, ignore_errors=True)
        raise


class Validator:
    def __init__(
        self,
        *,
        repository_root: Path,
        timeout: int,
        runner: CommandRunner = _run,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        report: Reporter = print,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.timeout = timeout
        self.runner = runner
        self.clock = clock
        self.sleeper = sleeper
        self.report = report
        self.resources = Resources(secrets.token_hex(8))
        self.paths: EvidencePaths | None = None

    def _checked(self, stage: str, command: Sequence[str]) -> None:
        try:
            result = self.runner(command, self.timeout)
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)

    def _stage(self, name: str, action: Callable[[], None]) -> None:
        started = self.clock()
        action()
        self.report(f"PASS {name} {self.clock() - started:.3f}")

    def _docker(self, *arguments: str) -> list[str]:
        return ["docker", *arguments]

    def _root_sql(self, stage: str, filename: str) -> None:
        self._checked(
            stage,
            self._docker(
                "exec",
                self.resources.main_container,
                "/bin/sh",
                "-c",
                f"mariadb --defaults-file={MARIADB_ADMIN_CNF_PATH} "
                f"< /evidence/stages/{filename}",
            ),
        )

    def _keystone_sql(self, stage: str, filename: str) -> None:
        self._checked(
            stage,
            self._docker(
                "exec",
                self.resources.main_container,
                "/bin/sh",
                "-c",
                "mariadb --defaults-file=/evidence/secret/keystone.cnf "
                f"< /evidence/stages/{filename}",
            ),
        )

    def _prepare(self) -> None:
        self._checked(
            "prepare-mounts",
            self._docker(
                "run",
                "--name",
                self.resources.prepare_container,
                "--rm",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,src={self.resources.data_volume},dst={MARIADB_DATA_DIR}",
                "--mount",
                f"type=volume,src={self.resources.runtime_volume},dst={MARIADB_RUNTIME_DIR}",
                IMAGE,
                "/bin/sh",
                "-c",
                "chown -R 42434:42434 /var/lib/mysql /run/mysqld",
            ),
        )

    def _stage_inputs(self) -> None:
        assert self.paths is not None
        self._checked(
            "stage-inputs",
            self._docker(
                "run",
                "--name",
                self.resources.staging_container,
                "--rm",
                "--user",
                "0:0",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "FOWNER",
                "--cap-add",
                "DAC_OVERRIDE",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=bind,src={self.paths.public},dst=/source/public,readonly",
                "--mount",
                f"type=bind,src={self.paths.secret},dst=/source/secret,readonly",
                "--mount",
                f"type=bind,src={self.paths.stages},dst=/source/stages,readonly",
                "--mount",
                f"type=bind,src={self.paths.keystone},dst=/source/keystone,readonly",
                "--mount",
                f"type=volume,src={self.resources.public_volume},dst=/evidence-public",
                "--mount",
                f"type=volume,src={self.resources.secret_volume},dst=/evidence-secret",
                "--mount",
                f"type=volume,src={self.resources.stages_volume},dst=/evidence-stages",
                "--mount",
                f"type=volume,src={self.resources.keystone_config_volume},dst=/evidence-keystone",
                IMAGE,
                "/bin/sh",
                "-c",
                "set -eu; "
                "cp -a /source/public/. /evidence-public/; "
                "cp -a /source/secret/. /evidence-secret/; "
                "cp -a /source/stages/. /evidence-stages/; "
                "cp -a /source/keystone/. /evidence-keystone/; "
                "chown -R 42434:42434 /evidence-public /evidence-secret "
                "/evidence-stages; "
                "chown -R 42425:42425 /evidence-keystone; "
                "chmod 0700 /evidence-public /evidence-secret /evidence-stages; "
                "chmod 0700 /evidence-keystone; "
                "chmod 0400 /evidence-public/my.cnf /evidence-secret/* "
                "/evidence-stages/*; "
                "chmod 0400 /evidence-keystone/keystone.conf "
                "/evidence-keystone/admin-password "
                "/evidence-keystone/auth-request.json; "
                "chmod 0500 /evidence-public/prepare-mariadb.sh "
                "/evidence-public/start-mariadb.sh /evidence-keystone/bootstrap.py",
            ),
        )

    def _keystone_arguments(
        self,
        *,
        container_name: str,
        keys_readonly: bool,
        detached: bool = False,
        network_alias: bool = False,
    ) -> list[str]:
        arguments = ["run", "--name", container_name]
        if detached:
            arguments.append("--detach")
        if network_alias:
            arguments.extend(["--network-alias", "keystone"])
        arguments.extend(
            [
                "--network",
                self.resources.network,
                "--user",
                f"{KEYSTONE_ID}:{KEYSTONE_ID}",
                "--group-add",
                KOLLA_GROUP,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,src={self.resources.keystone_config_volume},"
                "dst=/evidence/keystone,readonly",
                "--mount",
                f"type=volume,src={self.resources.fernet_volume},"
                f"dst=/etc/keystone/fernet-keys{',readonly' if keys_readonly else ''}",
                "--mount",
                f"type=volume,src={self.resources.credential_volume},"
                "dst=/etc/keystone/credential-keys"
                f"{',readonly' if keys_readonly else ''}",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--tmpfs",
                "/run:rw,noexec,nosuid,size=16m,uid=42425,gid=42425,mode=0700",
                "--tmpfs",
                "/var/lib/keystone:rw,noexec,nosuid,size=16m,uid=42425,gid=42425,mode=0700",
                "--tmpfs",
                "/var/log/kolla:rw,noexec,nosuid,size=16m,uid=42425,gid=42425,mode=0700",
                KEYSTONE_IMAGE,
            ]
        )
        return arguments

    def _prepare_keystone_keys(self) -> None:
        self._checked(
            "prepare-keystone-key-mounts",
            self._docker(
                "run",
                "--name",
                self.resources.keystone_key_prepare_container,
                "--rm",
                "--user",
                "0:0",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "CHOWN",
                "--cap-add",
                "FOWNER",
                "--cap-add",
                "DAC_OVERRIDE",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,src={self.resources.fernet_volume},dst=/etc/keystone/fernet-keys",
                "--mount",
                f"type=volume,src={self.resources.credential_volume},dst=/etc/keystone/credential-keys",
                KEYSTONE_IMAGE,
                "/bin/sh",
                "-c",
                "chown 42425:42425 /etc/keystone/fernet-keys "
                "/etc/keystone/credential-keys; "
                "chmod 0700 /etc/keystone/fernet-keys /etc/keystone/credential-keys",
            ),
        )

    def _keystone_one_shot(self, stage: str, *command: str) -> None:
        arguments = self._keystone_arguments(
            container_name=self.resources.keystone_one_shot_container,
            keys_readonly=False,
        )
        arguments.insert(1, "--rm")
        self._checked(
            stage,
            self._docker(
                *arguments,
                *command,
            ),
        )

    def _keystone_exit_stage(self, container: str) -> str:
        try:
            result = self.runner(self._docker("logs", container), self.timeout)
        except (OSError, subprocess.SubprocessError):
            return "keystone-container-exited"
        captured = f"{result.stdout}\n{result.stderr}".lower()
        categories = (
            (("permission denied",), "keystone-container-exited-permission"),
            (("read-only file system",), "keystone-container-exited-read-only"),
            (
                ("access denied", "authentication failed"),
                "keystone-container-exited-authentication",
            ),
            (("no such file", "not found"), "keystone-container-exited-missing-path"),
            (("cannot write", "can't create/write"), "keystone-container-exited-write"),
        )
        for markers, stage in categories:
            if any(marker in captured for marker in markers):
                return stage
        return "keystone-container-exited"

    def _verify_keystone_keys(self) -> None:
        check = (
            "set -eu; "
            "for repo in /etc/keystone/fernet-keys /etc/keystone/credential-keys; do "
            '[ "$(find "$repo" -mindepth 1 -maxdepth 1 -type f | wc -l)" -eq 2 ]; '
            '[ -f "$repo/0" ] && [ -f "$repo/1" ]; '
            "[ \"$(stat -c '%a:%u:%g' \"$repo/0\")\" = '600:42425:42425' ]; "
            "[ \"$(stat -c '%a:%u:%g' \"$repo/1\")\" = '600:42425:42425' ]; "
            "done"
        )
        self._keystone_one_shot("verify-keystone-key-metadata", "/bin/sh", "-c", check)

    def _start_keystone(self) -> None:
        self._checked(
            "start-keystone-wsgi",
            self._docker(
                *self._keystone_arguments(
                    container_name=self.resources.keystone_main_container,
                    keys_readonly=True,
                    detached=True,
                    network_alias=True,
                ),
                "/var/lib/kolla/venv/bin/keystone-wsgi-public",
                "--host",
                "0.0.0.0",
                "--port",
                "5000",
                "--",
                "--config-file",
                "/evidence/keystone/keystone.conf",
            ),
        )

    def _probe_keystone(self, stage: str) -> None:
        deadline = self.clock() + self.timeout
        probe_command = (
            "status=$(curl --silent --show-error --output /tmp/body "
            "--write-out '%{http_code}' "
            'http://keystone:5000/v3); [ "$status" = 200 ]'
        )
        while self.clock() < deadline:
            try:
                result = self.runner(
                    self._docker(
                        *self._keystone_arguments(
                            container_name=self.resources.keystone_probe_container,
                            keys_readonly=True,
                        ),
                        "/bin/sh",
                        "-c",
                        probe_command,
                    ),
                    self.timeout,
                )
                running = self.runner(
                    self._docker(
                        "inspect",
                        "--format={{.State.Running}}",
                        self.resources.keystone_main_container,
                    ),
                    self.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure(stage) from None
            if running.returncode != 0 or running.stdout.strip() != "true":
                raise ValidationFailure(
                    self._keystone_exit_stage(self.resources.keystone_main_container)
                )
            if result.returncode == 0:
                self._checked(
                    "remove-keystone-probe",
                    self._docker("rm", self.resources.keystone_probe_container),
                )
                return
            self._checked(
                "remove-keystone-probe",
                self._docker("rm", "-f", self.resources.keystone_probe_container),
            )
            self.sleeper(POLL_INTERVAL)
        raise ValidationFailure(stage)

    def _authenticate_keystone(self, stage: str) -> None:
        auth_command = (
            "status=$(curl --silent --show-error --output /tmp/body "
            "--dump-header /tmp/headers "
            "--write-out '%{http_code}' --header 'Content-Type: application/json' "
            "--data-binary @/evidence/keystone/auth-request.json "
            "http://keystone:5000/v3/auth/tokens); "
            "[ \"$status\" = 201 ] && grep -qi '^X-Subject-Token: .\\+' /tmp/headers"
        )
        self._keystone_one_shot(stage, "/bin/sh", "-c", auth_command)

    def _keystone_evidence(self) -> None:
        for suffix in ("", "-repeat"):
            self._stage(
                f"keystone-db-sync{suffix}",
                lambda: self._keystone_one_shot(
                    "keystone-db-sync",
                    "keystone-manage",
                    "--config-file",
                    "/evidence/keystone/keystone.conf",
                    "db_sync",
                ),
            )
        self._stage(
            "keystone-db-sync-check",
            lambda: self._keystone_one_shot(
                "keystone-db-sync-check",
                "keystone-manage",
                "--config-file",
                "/evidence/keystone/keystone.conf",
                "db_sync",
                "--check",
            ),
        )
        for command, name in (
            ("fernet_setup", "fernet-setup"),
            ("credential_setup", "credential-setup"),
        ):
            for suffix in ("", "-repeat"):

                def setup(command: str = command, name: str = name) -> None:
                    self._keystone_one_shot(
                        f"keystone-{name}",
                        "keystone-manage",
                        "--config-file",
                        "/evidence/keystone/keystone.conf",
                        command,
                        "--keystone-user",
                        "keystone",
                        "--keystone-group",
                        "keystone",
                    )

                self._stage(
                    f"keystone-{name}{suffix}",
                    setup,
                )
        self._stage("verify-keystone-key-metadata", self._verify_keystone_keys)
        for suffix in ("", "-repeat"):
            self._stage(
                f"keystone-bootstrap{suffix}",
                lambda: self._keystone_one_shot(
                    "keystone-bootstrap",
                    "/var/lib/kolla/venv/bin/python",
                    "/evidence/keystone/bootstrap.py",
                ),
            )
        self._stage("start-keystone-wsgi", self._start_keystone)
        self._stage("keystone-v3", lambda: self._probe_keystone("keystone-v3"))
        self._stage(
            "keystone-auth", lambda: self._authenticate_keystone("keystone-auth")
        )
        self._stage(
            "stop-keystone",
            lambda: self._checked(
                "stop-keystone",
                self._docker(
                    "stop", "--time", "30", self.resources.keystone_main_container
                ),
            ),
        )
        self._stage(
            "remove-keystone",
            lambda: self._checked(
                "remove-keystone",
                self._docker("rm", self.resources.keystone_main_container),
            ),
        )
        self._stage("restart-keystone-wsgi", self._start_keystone)
        self._stage(
            "keystone-v3-restart", lambda: self._probe_keystone("keystone-v3-restart")
        )
        self._stage(
            "keystone-auth-restart",
            lambda: self._authenticate_keystone("keystone-auth-restart"),
        )
        self._stage(
            "stop-keystone-restart",
            lambda: self._checked(
                "stop-keystone-restart",
                self._docker(
                    "stop", "--time", "30", self.resources.keystone_main_container
                ),
            ),
        )

    def _runtime_arguments(
        self, detached: bool, *, container_name: str | None = None, remove: bool = False
    ) -> list[str]:
        assert self.paths is not None
        arguments = [
            "run",
            "--name",
            container_name or self.resources.main_container,
        ]
        if remove:
            arguments.append("--rm")
        if detached:
            arguments.append("--detach")
        arguments.extend(
            [
                "--network",
                self.resources.network,
                "--user",
                f"{MARIADB_RUN_AS_ID}:{MARIADB_RUN_AS_ID}",
                "--group-add",
                str(MARIADB_SUPPLEMENTAL_GROUP),
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=volume,src={self.resources.public_volume},"
                f"dst={MARIADB_CONFIG_DIR},readonly",
                "--mount",
                f"type=volume,src={self.resources.secret_volume},"
                f"dst={MARIADB_SECRET_DIR},readonly",
                "--mount",
                f"type=volume,src={self.resources.stages_volume},"
                "dst=/evidence/stages,readonly",
                "--mount",
                f"type=volume,src={self.resources.secret_volume},"
                "dst=/evidence/secret,readonly",
                "--mount",
                f"type=volume,src={self.resources.data_volume},dst={MARIADB_DATA_DIR}",
                "--mount",
                f"type=volume,src={self.resources.runtime_volume},dst={MARIADB_RUNTIME_DIR}",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                IMAGE,
            ]
        )
        return arguments

    def _start(self) -> None:
        self._checked(
            "start-mariadb",
            self._docker(
                *self._runtime_arguments(True),
                "/bin/sh",
                "-c",
                f"exec {MARIADB_CONFIG_DIR}/start-mariadb.sh",
            ),
        )

    def _prepare_runtime(self) -> None:
        self._checked(
            "prepare-runtime",
            self._docker(
                *self._runtime_arguments(
                    False,
                    container_name=self.resources.prepare_container,
                    remove=True,
                ),
                "/bin/sh",
                f"{MARIADB_CONFIG_DIR}/prepare-mariadb.sh",
            ),
        )

    def _container_exit_stage(self) -> str:
        try:
            result = self.runner(
                self._docker("logs", self.resources.main_container), self.timeout
            )
        except (OSError, subprocess.SubprocessError):
            return "database-container-exited"
        captured = f"{result.stdout}\n{result.stderr}".lower()
        if "permission denied" in captured:
            permission_paths = (
                ("/var/lib/mysql", "database-container-exited-permission-data"),
                ("/run/mysqld", "database-container-exited-permission-runtime"),
                ("/tmp", "database-container-exited-permission-tmp"),
                ("/etc/mariadb", "database-container-exited-permission-config"),
                ("/etc/mysql", "database-container-exited-permission-config"),
                ("/var/log", "database-container-exited-permission-logs"),
            )
            for marker, stage in permission_paths:
                if marker in captured:
                    return stage
            return "database-container-exited-permission"
        categories = (
            (("read-only file system",), "database-container-exited-read-only"),
            (("error 1064", "syntax error"), "database-container-exited-sql-syntax"),
            (("access denied",), "database-container-exited-authentication"),
            (
                ("unknown option", "unknown variable"),
                "database-container-exited-configuration",
            ),
            (
                ("can't create/write", "cannot create", "cannot write"),
                "database-container-exited-write",
            ),
            (
                ("no such file or directory", "not found"),
                "database-container-exited-missing-path",
            ),
            (("address already in use",), "database-container-exited-port"),
        )
        for markers, stage in categories:
            if any(marker in captured for marker in markers):
                return stage
        return "database-container-exited"

    def _poll_ready(self) -> None:
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            try:
                running = self.runner(
                    self._docker(
                        "inspect",
                        "--format={{.State.Running}}",
                        self.resources.main_container,
                    ),
                    self.timeout,
                )
                if running.returncode != 0 or running.stdout.strip() != "true":
                    raise ValidationFailure(self._container_exit_stage())
                marker = self.runner(
                    self._docker(
                        "exec",
                        self.resources.main_container,
                        "test",
                        "-f",
                        MARIADB_BOOTSTRAP_COMPLETE_MARKER,
                    ),
                    self.timeout,
                )
                query = self.runner(
                    self._docker(
                        "exec",
                        self.resources.main_container,
                        "mariadb",
                        f"--defaults-file={MARIADB_ADMIN_CNF_PATH}",
                        "--execute=SELECT 1",
                    ),
                    self.timeout,
                )
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure("database-ready") from None
            if marker.returncode == 0 and query.returncode == 0:
                return
            self.sleeper(POLL_INTERVAL)
        raise ValidationFailure("database-ready")

    def _database_evidence(self) -> None:
        self._stage("database-ready", self._poll_ready)
        for suffix in ("", "-repeat"):
            self._stage(
                f"create-database{suffix}",
                lambda: self._root_sql("create-database", "create-database.sql"),
            )
            self._stage(
                f"create-user{suffix}",
                lambda: self._root_sql("create-user", "create-user.sql"),
            )
            self._stage(
                f"set-user-password{suffix}",
                lambda: self._root_sql("set-user-password", "set-password.sql"),
            )
            self._stage(
                f"grant-keystone-only{suffix}",
                lambda: self._root_sql("grant-keystone-only", "grant-keystone.sql"),
            )
            self._stage(
                f"flush-privileges{suffix}",
                lambda: self._root_sql("flush-privileges", "flush.sql"),
            )
        self._stage(
            "keystone-tcp-auth",
            lambda: self._checked(
                "keystone-tcp-auth",
                self._docker(
                    "exec",
                    self.resources.main_container,
                    "mariadb",
                    "--defaults-file=/evidence/secret/keystone.cnf",
                    "--execute=SELECT 1",
                ),
            ),
        )
        self._stage(
            "keystone-table",
            lambda: self._keystone_sql("keystone-table", "create-table.sql"),
        )
        rejection_failed = False
        try:
            self._keystone_sql("reject-unrelated-database", "reject-unrelated.sql")
        except ValidationFailure:
            rejection_failed = True
            self._stage("reject-unrelated-database", lambda: None)
        finally:
            self._stage(
                "cleanup-unrelated-database",
                lambda: self._root_sql(
                    "cleanup-unrelated-database", "cleanup-unrelated.sql"
                ),
            )
        if not rejection_failed:
            raise ValidationFailure("reject-unrelated-database")

    def _cleanup(self) -> None:
        for resource_type, name in (
            ("container", self.resources.main_container),
            ("container", self.resources.prepare_container),
            ("container", self.resources.staging_container),
            ("container", self.resources.keystone_main_container),
            ("container", self.resources.keystone_probe_container),
            ("container", self.resources.keystone_one_shot_container),
            ("container", self.resources.keystone_key_prepare_container),
            ("network", self.resources.network),
            ("volume", self.resources.runtime_volume),
            ("volume", self.resources.data_volume),
            ("volume", self.resources.public_volume),
            ("volume", self.resources.secret_volume),
            ("volume", self.resources.stages_volume),
            ("volume", self.resources.keystone_config_volume),
            ("volume", self.resources.fernet_volume),
            ("volume", self.resources.credential_volume),
        ):
            try:
                self.runner(self._docker(resource_type, "rm", "-f", name), self.timeout)
            except (OSError, subprocess.SubprocessError):
                self.report(f"CLEANUP {resource_type} {name}")
        if self.paths is not None:
            shutil.rmtree(self.paths.scratch, ignore_errors=True)

    def run(self) -> int:
        started = self.clock()
        failure: ValidationFailure | None = None
        try:
            self.paths = create_evidence_files(
                self.repository_root, self.resources.main_container
            )
            self._stage(
                "docker-cli-daemon",
                lambda: self._checked("docker-cli-daemon", self._docker("info")),
            )
            self._stage(
                "image-available",
                lambda: self._checked(
                    "image-available", self._docker("image", "pull", IMAGE)
                ),
            )
            self._stage(
                "keystone-image-available",
                lambda: self._checked(
                    "keystone-image-available",
                    self._docker("image", "pull", KEYSTONE_IMAGE),
                ),
            )
            for stage, command in (
                (
                    "data-volume",
                    self._docker("volume", "create", self.resources.data_volume),
                ),
                (
                    "runtime-volume",
                    self._docker("volume", "create", self.resources.runtime_volume),
                ),
                (
                    "public-volume",
                    self._docker("volume", "create", self.resources.public_volume),
                ),
                (
                    "secret-volume",
                    self._docker("volume", "create", self.resources.secret_volume),
                ),
                (
                    "stages-volume",
                    self._docker("volume", "create", self.resources.stages_volume),
                ),
                (
                    "keystone-config-volume",
                    self._docker(
                        "volume", "create", self.resources.keystone_config_volume
                    ),
                ),
                (
                    "keystone-fernet-volume",
                    self._docker("volume", "create", self.resources.fernet_volume),
                ),
                (
                    "keystone-credential-volume",
                    self._docker("volume", "create", self.resources.credential_volume),
                ),
                (
                    "private-network",
                    self._docker(
                        "network",
                        "create",
                        "--driver",
                        "bridge",
                        "--internal",
                        self.resources.network,
                    ),
                ),
            ):

                def run_stage(
                    command: Sequence[str] = command, stage: str = stage
                ) -> None:
                    self._checked(stage, command)

                self._stage(stage, run_stage)
            self._stage("stage-inputs", self._stage_inputs)
            self._stage("prepare-mounts", self._prepare)
            self._stage("prepare-keystone-key-mounts", self._prepare_keystone_keys)
            self._stage("prepare-runtime", self._prepare_runtime)
            self._stage("start-mariadb", self._start)
            self._database_evidence()
            self._stage(
                "stop-mariadb",
                lambda: self._checked(
                    "stop-mariadb",
                    self._docker("stop", "--time", "30", self.resources.main_container),
                ),
            )
            self._stage(
                "remove-mariadb",
                lambda: self._checked(
                    "remove-mariadb", self._docker("rm", self.resources.main_container)
                ),
            )
            self._stage(
                "replace-runtime-volume",
                lambda: self._checked(
                    "replace-runtime-volume",
                    self._docker("volume", "rm", self.resources.runtime_volume),
                ),
            )
            self._stage(
                "replace-runtime-volume",
                lambda: self._checked(
                    "replace-runtime-volume",
                    self._docker("volume", "create", self.resources.runtime_volume),
                ),
            )
            self._stage("prepare-mounts-persisted", self._prepare)
            self._stage("prepare-runtime-persisted", self._prepare_runtime)
            self._stage("start-mariadb-persisted", self._start)
            self._database_evidence()
            self._keystone_evidence()
            self._stage(
                "stop-mariadb-persisted",
                lambda: self._checked(
                    "stop-mariadb-persisted",
                    self._docker("stop", "--time", "30", self.resources.main_container),
                ),
            )
        except ValidationFailure as error:
            failure = error
        except Exception:
            failure = ValidationFailure("internal")
        finally:
            self._cleanup()
        elapsed = self.clock() - started
        if failure is not None:
            self.report(f"FAIL {failure.stage}")
            self.report(f"SUMMARY runtime failed {elapsed:.3f}")
            return 1
        self.report(f"SUMMARY runtime passed {elapsed:.3f}")
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Disposable local MariaDB and Keystone runtime evidence."
    )
    parser.add_argument(
        "--run", action="store_true", help="perform the disposable local evidence"
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="stage timeout in seconds"
    )
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("--run is required; this performs disposable runtime evidence")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return Validator(
        repository_root=Path(__file__).resolve().parents[1], timeout=args.timeout
    ).run()


if __name__ == "__main__":
    sys.exit(main())
