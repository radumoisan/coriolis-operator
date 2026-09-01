#!/usr/bin/env python3
"""Value-silent physical-retention validation for a disposable Loki.

This validator exercises the shortened physical-retention behaviour of an
already-ready disposable CoriolisAppliance's Loki without touching any shared
resource and without printing secret or payload values. Diagnostic mode
temporarily patches the Loki ConfigMap; formal mode validates the released
configuration while recreating only the CoriolisAppliance. Both modes use a
short-lived read-only observer Pod to capture metadata-only chunk inventory.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

CONFIG_KEYS = ("retention_period", "compaction_interval", "retention_delete_delay")
RELEASED_CONFIG = {
    "retention_period": "1h",
    "compaction_interval": "15m",
    "retention_delete_delay": "2h",
}
DIAGNOSTIC_CONFIG = {
    "retention_period": "10m",
    "compaction_interval": "1m",
    "retention_delete_delay": "5m",
}
_CONFIG_KEY_PATTERN = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>retention_period|compaction_interval|"
    r"retention_delete_delay):[ \t]*(?P<value>\S+)(?P<tail>[ \t]*)$"
)
GATEWAY_PORT = 8080
LOKI_PORT = 3100
CHUNKS_DIR = "/loki/chunks"
COMPACTOR_RETENTION_DIR = "/loki/compactor/retention"
OBSERVER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/nginx-unprivileged@sha256:"
    "9849698e95fe2b466e473ad8c452b1a812e08713af1514c61ece0aa77cc8e013"
)
OBSERVER_IMAGE_PULL_SECRET = "coriolis-appliance-registry"
OBSERVER_COMPONENT = "retention-observer"
OBSERVER_LABEL_KEY = "coriolis.cloudbase.it/component"
OBSERVER_LABEL_VALUE = "retention-observer"
OBSERVER_RUN_UID = 10001
OBSERVER_TOOLS = ("sh", "find", "stat", "sha256sum", "wc")
OBSERVER_SLEEP_SECONDS = 21600
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_WAIT_MINUTES = 25
POLL_INTERVAL = 5.0
# Caller-owned cleanup notice surfaced in --help and on failure.
CLEANUP_NOTICE = (
    "DIAGNOSTIC-ONLY mode patches the Loki ConfigMap and never restores it. FORMAL "
    "mode deletes and recreates only the CoriolisAppliance from its current "
    "spec. Both modes best-effort delete the read-only "
    "<app>-retention-observer Pod on exit. "
    "The final CoriolisAppliance, Application, and namespace are caller-owned "
    "and are never deleted by this validator."
)

CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
InputRunner = Callable[[Sequence[str], str, int], subprocess.CompletedProcess[str]]
Clock = Callable[[], float]
WallClock = Callable[[], float]
Sleeper = Callable[[float], None]
Reporter = Callable[[str], None]


class ValidationFailure(Exception):
    """A sanitized failure that identifies only a stable validation stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(f"validation failed: {stage}")
        self.stage = stage


@dataclass(frozen=True)
class InventoryEntry:
    """Metadata-only record of a chunk file; never carries payload."""

    path: str
    size: int
    mtime: int
    sha256: str


@dataclass(frozen=True, repr=False)
class _Credentials:
    """Read/write Loki passwords held only in process memory."""

    read_password: str
    write_password: str


@dataclass(frozen=True)
class _ResourceIdentity:
    uid: str
    resource_version: str


def _run(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), check=False, capture_output=True, text=True, timeout=timeout
    )


def _run_with_input(
    command: Sequence[str], data: str, timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=data,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _parse_duration(value: str) -> int:
    matches = tuple(re.finditer(r"(\d+)([hms])", value))
    if not matches or "".join(match.group(0) for match in matches) != value:
        raise ValueError(f"unparseable duration {value!r}")
    return sum(
        int(match.group(1)) * {"s": 1, "m": 60, "h": 3600}[match.group(2)]
        for match in matches
    )


def _duration_configs_equal(
    actual: Mapping[str, str], expected: Mapping[str, str]
) -> bool:
    try:
        return all(
            _parse_duration(actual[key]) == _parse_duration(expected[key])
            for key in CONFIG_KEYS
        )
    except (KeyError, ValueError):
        return False


def _parse_config_values(text: str) -> dict[str, str]:
    """Return the three retention keys' values from a Loki config text."""
    values: dict[str, str] = {}
    for key in CONFIG_KEYS:
        match = re.search(rf"(?m)^[ \t]*{key}:[ \t]*([^\s#][^\s]*)$", text)
        if not match:
            raise ValueError(f"missing config key {key}")
        values[key] = match.group(1)
    return values


def _replace_config_values(text: str, replacements: Mapping[str, str]) -> str:
    """Replace the three retention values, preserving surrounding formatting."""
    replaced: set[str] = set()
    lines: list[str] = []
    for line in text.splitlines():
        match = _CONFIG_KEY_PATTERN.match(line)
        if match and match.group("key") in replacements:
            lines.append(
                f"{match.group('indent')}{match.group('key')}: "
                f"{replacements[match.group('key')]}"
            )
            replaced.add(match.group("key"))
        else:
            lines.append(line)
    if replaced != set(CONFIG_KEYS):
        raise ValueError("config keys not replaced")
    trailing = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + trailing


def _inventory_script(tenant: str) -> str:
    """Return a metadata-only POSIX inventory command (path, size, mtime, sha256)."""
    chunk_dir = f"{CHUNKS_DIR}/{tenant}"
    quoted = shlex.quote(chunk_dir)
    return (
        f"if [ -d {quoted} ]; then "
        f"find {quoted} -type f | while read f; do "
        f's=$(stat -c%s "$f" 2>/dev/null); '
        f'm=$(stat -c%Y "$f" 2>/dev/null); '
        f'h=$(sha256sum "$f" 2>/dev/null); h=${{h%% *}}; '
        f'printf \'%s\\t%s\\t%s\\t%s\\n\' "${{f#{quoted}/}}" "$s" "$m" "$h"; '
        f"done; fi"
    )


def _deletion_marker_count_command() -> str:
    """Return a POSIX command emitting the compactor retention marker-file count."""
    quoted = shlex.quote(COMPACTOR_RETENTION_DIR)
    return f"if [ -d {quoted} ]; then find {quoted} -type f | wc -l; else echo 0; fi"


def _observer_tools_command() -> str:
    """Return a silent probe confirming observer tools exist (no file content)."""
    tools = " ".join(OBSERVER_TOOLS)
    return f'for t in {tools}; do command -v "$t" >/dev/null 2>&1 || exit 1; done'


def _observer_overrides(app_name: str) -> dict[str, object]:
    """Return the kubectl run overrides for the disposable read-only observer Pod."""
    pod_name = f"{app_name}-retention-observer"
    return {
        "metadata": {"labels": {OBSERVER_LABEL_KEY: OBSERVER_LABEL_VALUE}},
        "spec": {
            "automountServiceAccountToken": False,
            "imagePullSecrets": [{"name": OBSERVER_IMAGE_PULL_SECRET}],
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": OBSERVER_RUN_UID,
                "runAsGroup": OBSERVER_RUN_UID,
                "fsGroup": OBSERVER_RUN_UID,
            },
            "volumes": [
                {
                    "name": "loki-data",
                    "persistentVolumeClaim": {"claimName": f"{app_name}-loki-data"},
                }
            ],
            "containers": [
                {
                    "name": pod_name,
                    "image": OBSERVER_IMAGE,
                    "command": [
                        "/bin/sh",
                        "-c",
                        f"sleep {OBSERVER_SLEEP_SECONDS}",
                    ],
                    "imagePullPolicy": "IfNotPresent",
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": OBSERVER_RUN_UID,
                        "runAsGroup": OBSERVER_RUN_UID,
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [
                        {
                            "name": "loki-data",
                            "mountPath": "/loki",
                            "readOnly": True,
                        }
                    ],
                }
            ],
        },
    }


def _parse_inventory(text: str) -> tuple[InventoryEntry, ...]:
    entries: list[InventoryEntry] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            raise ValueError("malformed inventory line")
        path, size_text, mtime_text, sha256 = parts
        size = int(size_text)
        mtime = int(mtime_text)
        if (
            not path
            or size < 0
            or mtime <= 0
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        ):
            raise ValueError("invalid inventory metadata")
        entries.append(InventoryEntry(path, size, mtime, sha256))
    entries.sort(key=lambda entry: (entry.path, entry.size, entry.mtime, entry.sha256))
    return tuple(entries)


def _inventory_diff(
    post: Sequence[InventoryEntry], pre: Sequence[InventoryEntry]
) -> tuple[InventoryEntry, ...]:
    """Return post-flush entries absent from the pre-push inventory (candidates)."""
    pre_paths = {entry.path for entry in pre}
    return tuple(entry for entry in post if entry.path not in pre_paths)


def _basic_auth_value(user: str, password: str) -> str:
    """Return a Basic auth value without logging the password."""
    raw = f"{user}:{password}".encode()
    return base64.b64encode(raw).decode("ascii")


def _marker_push_body(marker: str, timestamp_seconds: float) -> bytes:
    """Return the Loki JSON push payload for a unique synthetic marker stream."""
    payload = {
        "streams": [
            {
                "stream": {"stream": "loki-retention", "marker": marker},
                "values": [
                    [
                        str(int(timestamp_seconds * 1_000_000_000)),
                        "synthetic retention marker",
                    ]
                ],
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")


class Validator:
    def __init__(
        self,
        *,
        repository_root: Path,
        context: str,
        namespace: str,
        app_name: str,
        mode: str = "diagnostic",
        timeout: int = DEFAULT_TIMEOUT,
        max_wait_minutes: int = DEFAULT_MAX_WAIT_MINUTES,
        poll_interval: float = POLL_INTERVAL,
        runner: CommandRunner = _run,
        input_runner: InputRunner = _run_with_input,
        clock: Clock = time.monotonic,
        wallclock: WallClock = time.time,
        sleeper: Sleeper = time.sleep,
        report: Reporter = print,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.context = context
        self.namespace = namespace
        self.app_name = app_name
        self.mode = mode
        self.timeout = timeout
        self.max_wait_seconds = max_wait_minutes * 60
        self.poll_interval = poll_interval
        self.runner = runner
        self.input_runner = input_runner
        self.clock = clock
        self.wallclock = wallclock
        self.sleeper = sleeper
        self.report = report
        self.tenant = ""
        self.credentials: _Credentials | None = None
        self.config_key: str | None = None
        self._original_config_text = ""
        self._marker = ""
        self._pushed = 0.0
        self._pre_inventory: tuple[InventoryEntry, ...] = ()
        self._post_inventory: tuple[InventoryEntry, ...] = ()
        self._candidates: tuple[InventoryEntry, ...] = ()
        self._local_port = 0
        self._port_forward_proc: subprocess.Popen[str] | None = None
        self._old_tenant = ""
        self._old_cr_uid = ""
        self._cr_manifest: dict[str, object] | None = None
        self._formal_version = ""
        self._retained_secret: _ResourceIdentity | None = None
        self._retained_pvc: _ResourceIdentity | None = None
        self._retained_credentials: _Credentials | None = None

    @property
    def retention_window(self) -> int:
        return _parse_duration(DIAGNOSTIC_CONFIG["retention_period"]) + _parse_duration(
            DIAGNOSTIC_CONFIG["retention_delete_delay"]
        )

    @property
    def formal_retention_window(self) -> int:
        return _parse_duration(RELEASED_CONFIG["retention_period"]) + _parse_duration(
            RELEASED_CONFIG["retention_delete_delay"]
        )

    def _kubectl(self, *arguments: str) -> list[str]:
        return [
            "kubectl",
            "--context",
            self.context,
            "--namespace",
            self.namespace,
            *arguments,
        ]

    def _checked(
        self, stage: str, command: Sequence[str]
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self.runner(command, self.timeout)
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)
        return result

    def _checked_input(self, stage: str, command: Sequence[str], data: str) -> None:
        try:
            result = self.input_runner(command, data, self.timeout)
        except (OSError, subprocess.SubprocessError):
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)

    def _kubectl_json(self, stage: str, *get_arguments: str) -> dict[str, object]:
        result = self._checked(
            stage, self._kubectl("get", *get_arguments, "-o", "json")
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ValidationFailure(stage) from None
        if not isinstance(payload, dict):
            raise ValidationFailure(stage)
        return payload

    def _stage(self, name: str, action: Callable[[], None]) -> None:
        started = self.clock()
        action()
        self.report(f"PASS {name} {self.clock() - started:.3f}")

    def _observer_pod_name(self) -> str:
        return f"{self.app_name}-retention-observer"

    def _read_cr_uid(self) -> None:
        payload = self._kubectl_json("cr-uid", "coriolisappliances", self.app_name)
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ValidationFailure("cr-uid")
        uid = metadata.get("uid")
        if not isinstance(uid, str) or not uid:
            raise ValidationFailure("cr-uid")
        self.tenant = f"coriolis-{uid}"
        self._old_tenant = self.tenant
        self._old_cr_uid = uid

    def _credentials_from_secret(
        self, payload: Mapping[str, object], stage: str
    ) -> _Credentials:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValidationFailure(stage)
        try:
            read_password = base64.b64decode(
                data["read_password"], validate=True
            ).decode("utf-8")
            write_password = base64.b64decode(
                data["write_password"], validate=True
            ).decode("utf-8")
        except (KeyError, TypeError, ValueError, base64.binascii.Error):
            raise ValidationFailure(stage) from None
        if not read_password or not write_password:
            raise ValidationFailure(stage)
        return _Credentials(read_password, write_password)

    def _read_secret(self) -> None:
        payload = self._kubectl_json(
            "secret", "secret", f"{self.app_name}-logging-credentials"
        )
        self.credentials = self._credentials_from_secret(payload, "secret")

    def _resource_identity(
        self, payload: Mapping[str, object], stage: str
    ) -> _ResourceIdentity:
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            raise ValidationFailure(stage)
        uid = metadata.get("uid")
        resource_version = metadata.get("resourceVersion")
        owners = metadata.get("ownerReferences")
        if (
            not isinstance(uid, str)
            or not uid
            or not isinstance(resource_version, str)
            or not resource_version
            or owners not in (None, [])
        ):
            raise ValidationFailure(stage)
        return _ResourceIdentity(uid, resource_version)

    def _capture_cr_manifest(self) -> None:
        payload = self._kubectl_json("cr-manifest", "coriolisappliances", self.app_name)
        metadata = payload.get("metadata")
        spec = payload.get("spec")
        api_version = payload.get("apiVersion")
        kind = payload.get("kind")
        if (
            not isinstance(metadata, dict)
            or not isinstance(spec, dict)
            or not isinstance(api_version, str)
            or not api_version
            or not isinstance(kind, str)
            or not kind
            or metadata.get("name") != self.app_name
            or metadata.get("namespace") != self.namespace
        ):
            raise ValidationFailure("cr-manifest")
        version = spec.get("version")
        if not isinstance(version, str) or not version:
            raise ValidationFailure("cr-manifest")
        try:
            copied_spec = json.loads(json.dumps(spec))
        except (TypeError, ValueError):
            raise ValidationFailure("cr-manifest") from None
        if not isinstance(copied_spec, dict):
            raise ValidationFailure("cr-manifest")
        self._cr_manifest = {
            "apiVersion": api_version,
            "kind": kind,
            "metadata": {"name": self.app_name, "namespace": self.namespace},
            "spec": copied_spec,
        }
        self._formal_version = version

    def _config_map(self, stage: str) -> dict[str, object]:
        payload = self._kubectl_json(stage, "configmap", f"{self.app_name}-loki-config")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValidationFailure(stage)
        return data

    def _locate_config_entry(
        self, data: Mapping[str, object], stage: str = "config-original"
    ) -> tuple[str, str]:
        for key, value in data.items():
            if not isinstance(value, str):
                continue
            if all(
                re.search(rf"(?m)^[ \t]*{name}:[ \t]*\S+$", value)
                for name in CONFIG_KEYS
            ):
                return key, value
        raise ValidationFailure(stage)

    def _validate_original_config(self) -> None:
        data = self._config_map("config-original")
        config_key, config_text = self._locate_config_entry(data)
        if _parse_config_values(config_text) != RELEASED_CONFIG:
            raise ValidationFailure("config-original")
        self.config_key = config_key
        self._original_config_text = config_text

    def _patch_config(self) -> None:
        if self.config_key is None:
            raise ValidationFailure("config-patch")
        new_text = _replace_config_values(self._original_config_text, DIAGNOSTIC_CONFIG)
        if _parse_config_values(new_text) != DIAGNOSTIC_CONFIG:
            raise ValidationFailure("config-patch")
        patch = json.dumps({"data": {self.config_key: new_text}})
        self._checked(
            "config-patch",
            self._kubectl(
                "patch",
                "configmap",
                f"{self.app_name}-loki-config",
                "--type",
                "merge",
                "--patch",
                patch,
            ),
        )

    def _config_map_text(self, stage: str) -> str:
        """Return the Loki config text from the ConfigMap API data."""
        data = self._config_map(stage)
        if self.config_key is None:
            raise ValidationFailure(stage)
        value = data.get(self.config_key)
        if not isinstance(value, str):
            raise ValidationFailure(stage)
        return value

    def _config_map_is_diagnostic(self) -> bool:
        try:
            return (
                _parse_config_values(self._config_map_text("config-reverted"))
                == DIAGNOSTIC_CONFIG
            )
        except (ValidationFailure, ValueError):
            return False

    def _verify_config_api(self) -> None:
        try:
            values = _parse_config_values(self._config_map_text("config-api"))
        except (ValidationFailure, ValueError):
            raise ValidationFailure("config-api") from None
        if values != DIAGNOSTIC_CONFIG:
            raise ValidationFailure("config-api")

    def _verify_release_config_api(self, stage: str) -> None:
        data = self._config_map(stage)
        config_key, config_text = self._locate_config_entry(data, stage)
        try:
            values = _parse_config_values(config_text)
        except ValueError:
            raise ValidationFailure(stage) from None
        if values != RELEASED_CONFIG:
            raise ValidationFailure(stage)
        self.config_key = config_key

    def _config_map_matches_exact(self, expected: Mapping[str, str]) -> bool:
        try:
            return (
                _parse_config_values(self._config_map_text("config-reverted"))
                == expected
            )
        except (ValidationFailure, ValueError):
            return False

    def _pod_state(self, stage: str, pod_name: str) -> tuple[str, bool]:
        """Return (uid, ready) for a named pod, tolerating a transient absence."""
        try:
            payload = self._kubectl_json(stage, "pod", pod_name)
        except ValidationFailure:
            return "", False
        metadata = payload.get("metadata")
        uid = metadata.get("uid") if isinstance(metadata, dict) else None
        if not isinstance(uid, str) or not uid:
            return "", False
        ready = False
        status = payload.get("status")
        if isinstance(status, dict):
            for condition in status.get("conditions") or []:
                if isinstance(condition, dict) and condition.get("type") == "Ready":
                    ready = condition.get("status") == "True"
        return uid, ready

    def _recreate_pod(self, stage: str) -> None:
        loki_pod = f"{self.app_name}-loki-0"
        old_uid, _ = self._pod_state(stage, loki_pod)
        if not old_uid:
            raise ValidationFailure(stage)
        self._checked(
            stage,
            self._kubectl("delete", "pod", loki_pod, "--wait=false"),
        )
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            new_uid, ready = self._pod_state(stage, loki_pod)
            if new_uid and new_uid != old_uid and ready:
                return
            self.sleeper(self.poll_interval)
        raise ValidationFailure(stage)

    def _observer_command(self) -> list[str]:
        return self._kubectl(
            "run",
            self._observer_pod_name(),
            "--image=" + OBSERVER_IMAGE,
            "--restart=Never",
            "--overrides=" + json.dumps(_observer_overrides(self.app_name)),
        )

    def _create_observer(self) -> None:
        self._checked("observer-create", self._observer_command())

    def _wait_observer_ready(self) -> None:
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            _, ready = self._pod_state("observer-ready", self._observer_pod_name())
            if ready:
                return
            self.sleeper(self.poll_interval)
        raise ValidationFailure("observer-ready")

    def _verify_observer_tools(self) -> None:
        self._checked(
            "observer-tools",
            self._kubectl(
                "exec",
                self._observer_pod_name(),
                "--",
                "sh",
                "-c",
                _observer_tools_command(),
            ),
        )

    def _observer_exec(
        self, stage: str, script: str
    ) -> subprocess.CompletedProcess[str]:
        """Run a script in the observer Pod (never in the Loki container)."""
        return self._checked(
            stage,
            self._kubectl("exec", self._observer_pod_name(), "--", "sh", "-c", script),
        )

    def _free_local_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _port_open(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            return False

    def _open_port_forward(self, stage: str, target: str, remote_port: int) -> None:
        local_port = self._free_local_port()
        command = self._kubectl("port-forward", target, f"{local_port}:{remote_port}")
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self._local_port = local_port
        self._port_forward_proc = proc
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            if self._port_open(local_port):
                return
            if proc.poll() is not None:
                raise ValidationFailure(stage)
            self.sleeper(self.poll_interval)
        raise ValidationFailure(stage)

    def _close_port_forward(self) -> None:
        proc = self._port_forward_proc
        self._port_forward_proc = None
        if proc is None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=self.timeout)
        except subprocess.SubprocessError:
            try:
                proc.kill()
            except OSError:
                pass

    def _http(
        self,
        method: str,
        path: str,
        *,
        basic_user: str | None = None,
        basic_pass: str | None = None,
        tenant: str | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        content_encoding: str | None = None,
    ) -> tuple[int, bytes]:
        url = f"http://127.0.0.1:{self._local_port}{path}"
        headers: dict[str, str] = {}
        if basic_user is not None and basic_pass is not None:
            headers["Authorization"] = "Basic " + _basic_auth_value(
                basic_user, basic_pass
            )
        if tenant:
            headers["X-Scope-OrgID"] = tenant
        if content_type:
            headers["Content-Type"] = content_type
        if content_encoding:
            headers["Content-Encoding"] = content_encoding
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            try:
                error.read()
            except Exception:
                pass
            return error.code, b""
        except urllib.error.URLError:
            raise ValidationFailure("http") from None

    def _config_loaded(
        self,
        expected: Mapping[str, str] = DIAGNOSTIC_CONFIG,
        stage: str = "config-loaded",
    ) -> None:
        """Verify Loki's /config reflects expected values via a direct forward."""
        self._open_port_forward(stage, f"pod/{self.app_name}-loki-0", LOKI_PORT)
        try:
            try:
                status, body = self._http("GET", "/config", tenant=self.tenant)
            except ValidationFailure:
                raise ValidationFailure(stage) from None
        finally:
            self._close_port_forward()
        if status != 200:
            raise ValidationFailure(stage)
        try:
            text = body.decode("utf-8", "replace")
            values = _parse_config_values(text)
        except (ValueError, UnicodeDecodeError):
            raise ValidationFailure(stage) from None
        if not _duration_configs_equal(values, expected):
            raise ValidationFailure(stage)

    def _push_marker(self) -> None:
        if self.credentials is None:
            raise ValidationFailure("marker-push")
        self._marker = secrets.token_hex(8)
        now = self.wallclock()
        self._pushed = now
        body = _marker_push_body(self._marker, now)
        status, _ = self._http(
            "POST",
            "/loki/api/v1/push",
            basic_user=self.tenant,
            basic_pass=self.credentials.write_password,
            body=body,
            content_type="application/json",
        )
        if status != 204:
            raise ValidationFailure("marker-push")

    def _query_path(self, marker: str, start: float, end: float) -> str:
        query = '{stream="loki-retention", marker=' + json.dumps(marker) + "}"
        params = urllib.parse.urlencode(
            {
                "query": query,
                "limit": "5000",
                "direction": "BACKWARD",
                "start": str(int(start * 1_000_000_000)),
                "end": str(int(end * 1_000_000_000)),
            }
        )
        return "/loki/api/v1/query_range?" + params

    def _parse_query_count(self, body: bytes, stage: str) -> int:
        try:
            payload = json.loads(body)
            result = payload["data"]["result"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValidationFailure(stage) from None
        if not isinstance(result, list):
            raise ValidationFailure(stage)
        return sum(
            len(stream.get("values") or [])
            for stream in result
            if isinstance(stream, dict)
        )

    def _query_count(self, marker: str, start: float, end: float) -> int:
        if self.credentials is None:
            raise ValidationFailure("query")
        status, body = self._http(
            "GET",
            self._query_path(marker, start, end),
            basic_user=self.tenant,
            basic_pass=self.credentials.read_password,
        )
        if status != 200:
            raise ValidationFailure("query")
        return self._parse_query_count(body, "query")

    def _direct_query_count(self, tenant: str, stage: str) -> int:
        self._open_port_forward(stage, f"pod/{self.app_name}-loki-0", LOKI_PORT)
        try:
            try:
                status, body = self._http(
                    "GET",
                    self._query_path(self._marker, self._pushed, self.wallclock() + 60),
                    tenant=tenant,
                )
            except ValidationFailure:
                raise ValidationFailure(stage) from None
        finally:
            self._close_port_forward()
        if status != 200:
            raise ValidationFailure(stage)
        return self._parse_query_count(body, stage)

    def _query_before(self) -> None:
        count = self._query_count(self._marker, self._pushed, self.wallclock() + 60)
        if count <= 0:
            raise ValidationFailure("query-before")

    def _inventory_now(
        self, stage: str, tenant: str | None = None
    ) -> tuple[InventoryEntry, ...]:
        result = self._observer_exec(stage, _inventory_script(tenant or self.tenant))
        try:
            return _parse_inventory(result.stdout)
        except ValueError:
            raise ValidationFailure(stage) from None

    def _capture_pre_inventory(self) -> None:
        self._pre_inventory = self._inventory_now("inventory-pre-push")

    def _candidate_remaining(
        self, candidates: Sequence[InventoryEntry], tenant: str | None = None
    ) -> int:
        current = self._inventory_now("retention", tenant)
        current_paths = {entry.path for entry in current}
        return sum(1 for entry in candidates if entry.path in current_paths)

    def _chunk_flush(self) -> None:
        """POST Loki's /flush via a direct forward; accept only 2xx, close after."""
        self._open_port_forward("chunk-flush", f"pod/{self.app_name}-loki-0", LOKI_PORT)
        try:
            try:
                status, _ = self._http("POST", "/flush", tenant=self.tenant)
            except ValidationFailure:
                raise ValidationFailure("chunk-flush") from None
        finally:
            self._close_port_forward()
        if status < 200 or status >= 300:
            raise ValidationFailure("chunk-flush")

    def _chunk_materialized(self) -> None:
        """Poll the observer until post minus pre inventory is nonempty."""
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            post = self._inventory_now("chunk-materialized")
            candidates = _inventory_diff(post, self._pre_inventory)
            if candidates:
                self._post_inventory = post
                self._candidates = candidates
                return
            self.sleeper(self.poll_interval)
        raise ValidationFailure("chunk-materialized")

    def _query_persisted(self) -> None:
        count = self._query_count(self._marker, self._pushed, self.wallclock() + 60)
        if count <= 0:
            raise ValidationFailure("query-persisted")

    def _wait_and_assert_retention(self) -> None:
        candidates = self._candidates
        eligible = self._pushed + self.retention_window
        deadline = self._pushed + self.max_wait_seconds
        while self.wallclock() < deadline:
            if not self._config_map_is_diagnostic():
                raise ValidationFailure("config-reverted")
            if self.wallclock() >= eligible:
                remaining = self._candidate_remaining(candidates)
                count = self._query_count(
                    self._marker, self._pushed, self.wallclock() + 60
                )
                if remaining == 0 and count == 0:
                    return
            self.sleeper(self.poll_interval)
        raise ValidationFailure("retention")

    def _candidate_count_stage(self) -> None:
        self.report(f"PASS candidate-count {len(self._candidates)}")

    def _deletion_marker_count_stage(self) -> None:
        count = 0
        try:
            result = self._observer_exec(
                "deletion-marker-count", _deletion_marker_count_command()
            )
            count = int(result.stdout.strip().splitlines()[-1])
        except (ValidationFailure, ValueError, IndexError):
            self.report("OBS deletion-marker-count unavailable")
            return
        if count < 0:
            self.report("OBS deletion-marker-count unavailable")
            return
        self.report(f"PASS deletion-marker-count {count}")

    def _delete_observer(self) -> None:
        try:
            result = self.runner(
                self._kubectl(
                    "delete",
                    "pod",
                    self._observer_pod_name(),
                    "--ignore-not-found=true",
                ),
                self.timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return
        if result.returncode == 0:
            self.report("CLEANUP observer")

    def _capture_retained_resources(self) -> None:
        secret = self._kubectl_json(
            "retained-resources",
            "secret",
            f"{self.app_name}-logging-credentials",
        )
        pvc = self._kubectl_json(
            "retained-resources", "pvc", f"{self.app_name}-loki-data"
        )
        credentials = self._credentials_from_secret(secret, "retained-resources")
        if self.credentials is None or credentials != self.credentials:
            raise ValidationFailure("retained-resources")
        self._retained_secret = self._resource_identity(secret, "retained-resources")
        self._retained_pvc = self._resource_identity(pvc, "retained-resources")
        self._retained_credentials = credentials

    def _delete_cr(self) -> None:
        self._checked(
            "cr-delete",
            self._kubectl(
                "delete",
                "coriolisappliances",
                self.app_name,
                "--wait=false",
            ),
        )

    def _wait_cr_absent(self) -> None:
        deadline = self.clock() + self.timeout
        command = self._kubectl(
            "get", "coriolisappliances", self.app_name, "-o", "json"
        )
        while self.clock() < deadline:
            try:
                result = self.runner(command, self.timeout)
            except (OSError, subprocess.SubprocessError):
                raise ValidationFailure("cr-absent") from None
            if result.returncode != 0:
                return
            self.sleeper(self.poll_interval)
        raise ValidationFailure("cr-absent")

    def _create_cr(self) -> None:
        if self._cr_manifest is None:
            raise ValidationFailure("cr-create")
        try:
            manifest = json.dumps(self._cr_manifest)
        except (TypeError, ValueError):
            raise ValidationFailure("cr-create") from None
        self._checked_input("cr-create", self._kubectl("create", "-f", "-"), manifest)

    def _cr_ready(self, payload: Mapping[str, object]) -> tuple[str, bool]:
        metadata = payload.get("metadata")
        status = payload.get("status")
        if not isinstance(metadata, dict) or not isinstance(status, dict):
            return "", False
        uid = metadata.get("uid")
        conditions = status.get("conditions")
        if not isinstance(uid, str) or not uid or not isinstance(conditions, list):
            return "", False
        states = {
            condition.get("type"): condition.get("status")
            for condition in conditions
            if isinstance(condition, dict)
        }
        ready = (
            uid != self._old_cr_uid
            and status.get("acceptedVersion") == self._formal_version
            and states.get("Ready") == "True"
            and states.get("LoggingReady") == "True"
        )
        return uid, ready

    def _wait_recreated_cr_ready(self) -> None:
        deadline = self.clock() + self.timeout
        loki_pod = f"{self.app_name}-loki-0"
        while self.clock() < deadline:
            try:
                payload = self._kubectl_json(
                    "cr-ready", "coriolisappliances", self.app_name
                )
            except ValidationFailure:
                self.sleeper(self.poll_interval)
                continue
            uid, cr_ready = self._cr_ready(payload)
            _, loki_ready = self._pod_state("cr-ready", loki_pod)
            if uid and cr_ready and loki_ready:
                self.tenant = f"coriolis-{uid}"
                return
            self.sleeper(self.poll_interval)
        raise ValidationFailure("cr-ready")

    def _verify_retained_resources(self) -> None:
        if (
            self._retained_secret is None
            or self._retained_pvc is None
            or self._retained_credentials is None
        ):
            raise ValidationFailure("retained-verified")
        secret = self._kubectl_json(
            "retained-verified",
            "secret",
            f"{self.app_name}-logging-credentials",
        )
        pvc = self._kubectl_json(
            "retained-verified", "pvc", f"{self.app_name}-loki-data"
        )
        credentials = self._credentials_from_secret(secret, "retained-verified")
        if (
            self._resource_identity(secret, "retained-verified")
            != self._retained_secret
            or self._resource_identity(pvc, "retained-verified") != self._retained_pvc
            or credentials != self._retained_credentials
        ):
            raise ValidationFailure("retained-verified")
        self.credentials = credentials

    def _query_new_tenant_isolated(self) -> None:
        count = self._query_count(self._marker, self._pushed, self.wallclock() + 60)
        if count != 0:
            raise ValidationFailure("query-new-tenant-isolated")

    def _query_old_tenant_persisted(self) -> None:
        if not self._old_tenant:
            raise ValidationFailure("query-old-tenant-persisted")
        if (
            self._direct_query_count(self._old_tenant, "query-old-tenant-persisted")
            <= 0
        ):
            raise ValidationFailure("query-old-tenant-persisted")

    def _wait_and_assert_formal_retention(self) -> None:
        if not self._old_tenant:
            raise ValidationFailure("formal-retention")
        eligible = self._pushed + self.formal_retention_window
        deadline = self._pushed + self.max_wait_seconds
        while self.wallclock() < deadline:
            if not self._config_map_matches_exact(RELEASED_CONFIG):
                raise ValidationFailure("config-reverted")
            if self.wallclock() >= eligible:
                remaining = self._candidate_remaining(
                    self._candidates, self._old_tenant
                )
                count = self._direct_query_count(self._old_tenant, "formal-retention")
                if remaining == 0 and count == 0:
                    return
            self.sleeper(self.poll_interval)
        raise ValidationFailure("formal-retention")

    def _run_body(self) -> None:
        self._stage("cr-uid", self._read_cr_uid)
        self._stage("secret", self._read_secret)
        self._stage("config-original", self._validate_original_config)
        self._stage("config-patch", self._patch_config)
        self._stage("config-api", self._verify_config_api)
        self._stage("pod-recreate", lambda: self._recreate_pod("pod-recreate"))
        self._stage("config-loaded", self._config_loaded)
        self._stage("observer-create", self._create_observer)
        self._stage("observer-ready", self._wait_observer_ready)
        self._stage("observer-tools", self._verify_observer_tools)
        self._stage(
            "port-forward",
            lambda: self._open_port_forward(
                "port-forward", f"svc/{self.app_name}-gateway", GATEWAY_PORT
            ),
        )
        try:
            self._stage("inventory-pre-push", self._capture_pre_inventory)
            self._stage("marker-push", self._push_marker)
            self._stage("query-before", self._query_before)
            self._stage("port-forward-close", self._close_port_forward)
            self._stage("chunk-flush", self._chunk_flush)
            self._stage("chunk-materialized", self._chunk_materialized)
            self._stage(
                "port-forward",
                lambda: self._open_port_forward(
                    "port-forward", f"svc/{self.app_name}-gateway", GATEWAY_PORT
                ),
            )
            self._stage("query-persisted", self._query_persisted)
            self._stage("retention", self._wait_and_assert_retention)
            self._candidate_count_stage()
            self._deletion_marker_count_stage()
        finally:
            self._close_port_forward()

    def _run_formal_body(self) -> None:
        self._stage("cr-uid", self._read_cr_uid)
        self._stage("secret", self._read_secret)
        self._stage("cr-manifest", self._capture_cr_manifest)
        self._stage(
            "config-release",
            lambda: self._verify_release_config_api("config-release"),
        )
        self._stage(
            "config-release-loaded",
            lambda: self._config_loaded(RELEASED_CONFIG, "config-release-loaded"),
        )
        self._stage("observer-create", self._create_observer)
        self._stage("observer-ready", self._wait_observer_ready)
        self._stage("observer-tools", self._verify_observer_tools)
        self._stage(
            "port-forward",
            lambda: self._open_port_forward(
                "port-forward", f"svc/{self.app_name}-gateway", GATEWAY_PORT
            ),
        )
        try:
            self._stage("inventory-pre-push", self._capture_pre_inventory)
            self._stage("marker-push", self._push_marker)
            self._stage("query-before", self._query_before)
            self._stage("port-forward-close", self._close_port_forward)
            self._stage("chunk-flush", self._chunk_flush)
            self._stage("chunk-materialized", self._chunk_materialized)
            self._stage(
                "port-forward",
                lambda: self._open_port_forward(
                    "port-forward", f"svc/{self.app_name}-gateway", GATEWAY_PORT
                ),
            )
            self._stage("query-persisted", self._query_persisted)
            self._stage("retained-resources", self._capture_retained_resources)
            self._stage("port-forward-close", self._close_port_forward)
            self._stage("cr-delete", self._delete_cr)
            self._stage("cr-absent", self._wait_cr_absent)
            self._stage("cr-create", self._create_cr)
            self._stage("cr-ready", self._wait_recreated_cr_ready)
            self._stage("retained-verified", self._verify_retained_resources)
            self._stage(
                "config-release-recreated",
                lambda: self._verify_release_config_api("config-release-recreated"),
            )
            self._stage(
                "config-release-loaded-recreated",
                lambda: self._config_loaded(
                    RELEASED_CONFIG, "config-release-loaded-recreated"
                ),
            )
            self._stage(
                "port-forward",
                lambda: self._open_port_forward(
                    "port-forward", f"svc/{self.app_name}-gateway", GATEWAY_PORT
                ),
            )
            self._stage("query-new-tenant-isolated", self._query_new_tenant_isolated)
            self._stage("port-forward-close", self._close_port_forward)
            self._stage("query-old-tenant-persisted", self._query_old_tenant_persisted)
            self._stage("formal-retention", self._wait_and_assert_formal_retention)
            self._candidate_count_stage()
            self._deletion_marker_count_stage()
        finally:
            self._close_port_forward()

    def run(self) -> int:
        started = self.clock()
        failure: ValidationFailure | None = None
        try:
            if self.mode == "formal":
                self._run_formal_body()
            else:
                self._run_body()
        except ValidationFailure as error:
            failure = error
        except Exception:
            failure = ValidationFailure("internal")
        finally:
            self._delete_observer()
        elapsed = self.clock() - started
        summary = "retention-formal" if self.mode == "formal" else "retention"
        if failure is not None:
            self.report(f"FAIL {failure.stage}")
            self.report(CLEANUP_NOTICE)
            self.report(f"SUMMARY {summary} failed {elapsed:.3f}")
            return 1
        self.report(f"SUMMARY {summary} passed {elapsed:.3f}")
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Value-silent physical-retention validation against a "
        "disposable CoriolisAppliance Loki.",
        epilog=CLEANUP_NOTICE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--context", required=True, help="kubectl context")
    parser.add_argument("--namespace", required=True, help="namespace")
    parser.add_argument(
        "--app-name", required=True, help="CoriolisAppliance resource name"
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="acknowledge and perform the mutating disposable retention validation",
    )
    parser.add_argument(
        "--mode",
        choices=("diagnostic", "formal"),
        default="diagnostic",
        help="validation mode; formal preserves released Loki retention timings",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--max-wait-minutes",
        type=int,
        default=DEFAULT_MAX_WAIT_MINUTES,
        help="upper bound (minutes) for the retention poll window",
    )
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL)
    args = parser.parse_args(argv)
    if not args.run:
        parser.error(
            "--run is required; this performs a mutating disposable diagnostic"
        )
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.max_wait_minutes <= 0:
        parser.error("--max-wait-minutes must be positive")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be positive")
    return Validator(
        repository_root=Path(__file__).resolve().parents[1],
        context=args.context,
        namespace=args.namespace,
        app_name=args.app_name,
        mode=args.mode,
        timeout=args.timeout,
        max_wait_minutes=args.max_wait_minutes,
        poll_interval=args.poll_interval,
    ).run()


if __name__ == "__main__":
    sys.exit(main())
