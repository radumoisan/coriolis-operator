#!/usr/bin/env python3
"""Value-silent live-tail reconnect validation for an existing appliance."""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

GATEWAY_PORT = 8080
TIMEOUT = 30
COMPONENT = "coriolis-api"
_STAGE = re.compile(r"^[a-z0-9-]+$")

CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
Reporter = Callable[[str], None]


class ValidationFailure(Exception):
    """A stable, non-sensitive failure stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


@dataclass(frozen=True, repr=False)
class Credentials:
    read_password: str
    write_password: str
    keystone_password: str


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass
class CredentialRegistry:
    """Reject accidental reporting of secrets and common encoded variants."""

    forms: set[str] = field(default_factory=set)

    def register(self, value: str) -> None:
        if not value:
            raise ValidationFailure("credentials")
        encoded = base64.b64encode(value.encode()).decode("ascii")
        self.forms.update(
            {
                value,
                encoded,
                base64.urlsafe_b64encode(value.encode()).decode("ascii"),
                urllib.parse.quote(value, safe=""),
                urllib.parse.quote_plus(value, safe=""),
                json.dumps(value),
                base64.b64encode(json.dumps(value).encode()).decode("ascii"),
            }
        )

    def audit(self, content: object) -> None:
        text = str(content)
        if any(form and form in text for form in self.forms):
            raise ValidationFailure("secret-leak")


def _run(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), check=False, capture_output=True, text=True, timeout=timeout
    )


def _http(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: int
) -> HttpResponse:
    request = urllib.request.Request(
        url, data=body, headers=dict(headers), method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(
                response.status, dict(response.headers.items()), response.read()
            )
    except urllib.error.HTTPError as error:
        try:
            body = error.read()
        except OSError:
            body = b""
        return HttpResponse(error.code, dict(error.headers.items()), body)
    except (OSError, urllib.error.URLError):
        raise ValidationFailure("http") from None


def _open_ws(url: str, headers: Mapping[str, str], timeout: int) -> object:
    try:
        import websocket

        return websocket.create_connection(
            url,
            header=[f"{key}: {value}" for key, value in headers.items()],
            timeout=timeout,
        )
    except Exception:
        raise ValidationFailure("websocket") from None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextlib.contextmanager
def _port_forward(
    command: Sequence[str], timeout: int, clock: Clock, sleeper: Sleeper
) -> Iterator[str]:
    local_port = _free_port()
    proc = subprocess.Popen(
        [*command, f"{local_port}:{GATEWAY_PORT}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = clock() + timeout
        while clock() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=1):
                    yield f"http://127.0.0.1:{local_port}"
                    return
            except OSError:
                if proc.poll() is not None:
                    raise ValidationFailure("gateway-forward")
                sleeper(0.2)
        raise ValidationFailure("gateway-forward")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            try:
                proc.kill()
            except OSError:
                pass


class Validator:
    def __init__(
        self,
        *,
        context: str,
        namespace: str,
        app: str,
        host: str,
        timeout: int = TIMEOUT,
        runner: CommandRunner = _run,
        http: Callable[
            [str, str, Mapping[str, str], bytes | None, int], HttpResponse
        ] = _http,
        ws_factory: Callable[[str, Mapping[str, str], int], object] = _open_ws,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        report: Reporter = print,
        forward: Callable[
            [Sequence[str], int, Clock, Sleeper], contextlib.AbstractContextManager[str]
        ] = _port_forward,
    ) -> None:
        self.context = context
        self.namespace = namespace
        self.app = app
        self.host = host
        self.timeout = timeout
        self.runner = runner
        self.http = http
        self.ws_factory = ws_factory
        self.clock = clock
        self.sleeper = sleeper
        self._raw_report = report
        self.forward = forward
        self.credentials: Credentials | None = None
        self.tenant: str | None = None
        self.registry = CredentialRegistry()
        self._gateway_restarts = 0
        self._seen: set[str] = set()

    def _report(self, status: str, stage: str) -> None:
        line = f"{status} {stage}"
        if status not in {"PASS", "FAIL"} or _STAGE.fullmatch(stage) is None:
            raise ValidationFailure("report")
        self.registry.audit(line)
        self._raw_report(line)

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
        except Exception:
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)
        return result

    def _json(self, stage: str, *arguments: str) -> dict[str, object]:
        result = self._checked(stage, self._kubectl("get", *arguments, "-o", "json"))
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise ValidationFailure(stage) from None
        if not isinstance(payload, dict):
            raise ValidationFailure(stage)
        return payload

    @staticmethod
    def _secret_value(payload: Mapping[str, object], key: str, stage: str) -> str:
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get(key), str):
            raise ValidationFailure(stage)
        try:
            value = base64.b64decode(data[key], validate=True).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            raise ValidationFailure(stage) from None
        if not value:
            raise ValidationFailure(stage)
        return value

    def _read_tenant(self) -> None:
        appliance = self._json("tenant", "coriolisappliance", self.app)
        metadata = appliance.get("metadata")
        uid = metadata.get("uid") if isinstance(metadata, dict) else None
        if not isinstance(uid, str) or not uid:
            raise ValidationFailure("tenant")
        self.tenant = f"coriolis-{uid}"

    def _read_credentials(self) -> None:
        logging = self._json("credentials", "secret", f"{self.app}-logging-credentials")
        coriolis = self._json(
            "credentials", "secret", f"{self.app}-coriolis-credentials"
        )
        credentials = Credentials(
            self._secret_value(logging, "read_password", "credentials"),
            self._secret_value(logging, "write_password", "credentials"),
            self._secret_value(coriolis, "coriolis_keystone_password", "credentials"),
        )
        for value in (
            credentials.read_password,
            credentials.write_password,
            credentials.keystone_password,
        ):
            self.registry.register(value)
        self.credentials = credentials

    def _token(self) -> str:
        if self.credentials is None:
            raise ValidationFailure("token")
        body = json.dumps(
            {
                "auth": {
                    "identity": {
                        "methods": ["password"],
                        "password": {
                            "user": {
                                "name": "coriolis",
                                "domain": {"name": "Default"},
                                "password": self.credentials.keystone_password,
                            }
                        },
                    },
                    "scope": {
                        "project": {"name": "service", "domain": {"name": "Default"}}
                    },
                }
            }
        ).encode()
        response = self.http(
            "POST",
            f"https://{self.host}/identity/auth/tokens",
            {"Content-Type": "application/json", "Accept": "application/json"},
            body,
            self.timeout,
        )
        token = response.headers.get("X-Subject-Token") or response.headers.get(
            "x-subject-token"
        )
        if response.status != 201 or not isinstance(token, str) or not token:
            raise ValidationFailure("token")
        self.registry.register(token)
        return token

    @staticmethod
    def _pod_ready(payload: Mapping[str, object], container: str | None = None) -> bool:
        status = payload.get("status")
        if not isinstance(status, dict):
            return False
        conditions = status.get("conditions")
        if not isinstance(conditions, list) or not any(
            isinstance(condition, dict)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        ):
            return False
        if container is None:
            return True
        statuses = status.get("containerStatuses")
        return isinstance(statuses, list) and any(
            isinstance(item, dict)
            and item.get("name") == container
            and item.get("ready") is True
            for item in statuses
        )

    @staticmethod
    def _restart_count(payload: Mapping[str, object], container: str) -> int:
        status = payload.get("status")
        statuses = status.get("containerStatuses") if isinstance(status, dict) else None
        if not isinstance(statuses, list):
            raise ValidationFailure("gateway-restarts")
        for item in statuses:
            if isinstance(item, dict) and item.get("name") == container:
                count = item.get("restartCount")
                if isinstance(count, int) and count >= 0:
                    return count
        raise ValidationFailure("gateway-restarts")

    def _ready(self) -> None:
        loki = self._json("readiness", "pod", f"{self.app}-loki-0")
        adaptor = self._json("readiness", "deployment", f"{self.app}-adaptor")
        if not self._pod_ready(loki, "loki") or not self._pod_ready(loki, "gateway"):
            raise ValidationFailure("readiness")
        status = adaptor.get("status")
        if not isinstance(status, dict) or any(
            status.get(key) != 1
            for key in ("availableReplicas", "readyReplicas", "updatedReplicas")
        ):
            raise ValidationFailure("readiness")

    def _capture_gateway_restarts(self) -> None:
        pod = self._json("gateway-restarts", "pod", f"{self.app}-loki-0")
        self._gateway_restarts = self._restart_count(pod, "gateway")

    def _assert_gateway_restarts(self) -> None:
        pod = self._json("gateway-restarts", "pod", f"{self.app}-loki-0")
        if self._restart_count(pod, "gateway") != self._gateway_restarts:
            raise ValidationFailure("gateway-restarts")

    def _ws_url(self) -> str:
        query = urllib.parse.urlencode({"app_name": COMPONENT, "severity": "6"})
        return f"wss://{self.host}/log-stream?{query}"

    def _push_body(self, marker: str) -> bytes:
        timestamp = str(time.time_ns())
        payload = {
            "streams": [
                {
                    "stream": {
                        "namespace": self.namespace,
                        "coriolis_appliance": self.app,
                        "coriolis_component": COMPONENT,
                        "pod": f"{self.app}-{COMPONENT}-validator",
                        "container": COMPONENT,
                        "severity": "INFO",
                    },
                    "values": [[timestamp, f"coriolis-reconnect-validator-{marker}"]],
                }
            ]
        }
        return json.dumps(payload, separators=(",", ":")).encode()

    def _push(self, base_url: str, marker: str) -> None:
        if self.credentials is None or self.tenant is None:
            raise ValidationFailure("push")
        basic = base64.b64encode(
            f"{self.tenant}:{self.credentials.write_password}".encode()
        ).decode("ascii")
        response = self.http(
            "POST",
            f"{base_url}/loki/api/v1/push",
            {"Authorization": f"Basic {basic}", "Content-Type": "application/json"},
            self._push_body(marker),
            self.timeout,
        )
        if response.status != 204:
            raise ValidationFailure("push")

    @staticmethod
    def _worker_kill_script() -> str:
        return (
            "master=$(cat /tmp/nginx.pid) || exit 1; "
            "case $master in ''|*[!0-9]*) exit 1;; esac; "
            '[ "$(cat /proc/$master/comm 2>/dev/null)" = nginx ] || exit 1; '
            "children=$(cat /proc/$master/task/$master/children) || exit 1; "
            "set -- $children; [ $# -eq 1 ] || exit 1; worker=$1; "
            "case $worker in ''|*[!0-9]*|1|$master) exit 1;; esac; "
            '[ "$(cat /proc/$worker/comm 2>/dev/null)" = nginx ] || exit 1; '
            "kill -TERM $worker"
        )

    def _terminate_worker(self) -> None:
        self._checked(
            "worker-terminate",
            self._kubectl(
                "exec",
                f"{self.app}-loki-0",
                "-c",
                "gateway",
                "--",
                "sh",
                "-c",
                self._worker_kill_script(),
            ),
        )

    def _expect(self, ws: object, marker: str, stage: str) -> None:
        deadline = self.clock() + self.timeout
        wanted = f"coriolis-reconnect-validator-{marker}"
        while self.clock() < deadline:
            try:
                message = getattr(ws, "recv")()
            except Exception:
                raise ValidationFailure(stage) from None
            if message is None:
                self.sleeper(0.05)
                continue
            try:
                value = json.loads(message)
                received = value.get("message") if isinstance(value, dict) else None
            except (TypeError, json.JSONDecodeError):
                raise ValidationFailure(stage) from None
            if not isinstance(received, str) or not received.startswith(
                "coriolis-reconnect-validator-"
            ):
                continue
            if received in self._seen:
                raise ValidationFailure(stage)
            expected = {"A", "B", "C"}
            suffix = received.rsplit("-", 1)[-1]
            if suffix not in expected or received != wanted:
                raise ValidationFailure(stage)
            self._seen.add(received)
            return
        raise ValidationFailure(stage)

    @staticmethod
    def _client_open(ws: object) -> bool:
        return (
            getattr(ws, "closed", False) is not True
            and getattr(ws, "connected", True) is not False
        )

    def _no_duplicate(self, ws: object) -> None:
        """Reject a queued replay of A, B, or C without reopening the client."""
        setter = getattr(ws, "settimeout", None)
        if callable(setter):
            setter(0.2)
        deadline = self.clock() + 1.0
        while self.clock() < deadline:
            try:
                message = getattr(ws, "recv")()
            except Exception:
                if not self._client_open(ws):
                    raise ValidationFailure("continuity") from None
                continue
            if message is None:
                continue
            try:
                value = json.loads(message)
                received = value.get("message") if isinstance(value, dict) else None
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(received, str) or not received.startswith(
                "coriolis-reconnect-validator-"
            ):
                continue
            if received in self._seen:
                raise ValidationFailure("continuity")
            raise ValidationFailure("continuity")
        if not self._client_open(ws):
            raise ValidationFailure("continuity")

    def _run_body(self) -> None:
        self._read_tenant()
        self._report("PASS", "tenant")
        self._read_credentials()
        self._report("PASS", "credentials")
        token = self._token()
        self._report("PASS", "token")
        self._ready()
        self._report("PASS", "readiness")
        self._capture_gateway_restarts()
        self._report("PASS", "gateway-restarts")
        ws = self.ws_factory(self._ws_url(), {"X-Auth-Token": token}, self.timeout)
        try:
            command = self._kubectl("port-forward", f"svc/{self.app}-gateway")
            with self.forward(
                command, self.timeout, self.clock, self.sleeper
            ) as base_url:
                self._push(base_url, "A")
                self._expect(ws, "A", "record-a")
                self._report("PASS", "record-a")
                self._terminate_worker()
                self._push(base_url, "B")
                self._report("PASS", "worker-terminate")
                self._expect(ws, "B", "record-b")
                self._report("PASS", "record-b")
                self._push(base_url, "C")
                self._expect(ws, "C", "record-c")
            self._no_duplicate(ws)
            if not self._client_open(ws) or len(self._seen) != 3:
                raise ValidationFailure("continuity")
            self._report("PASS", "continuity")
        finally:
            try:
                getattr(ws, "close")()
            except Exception:
                pass
        self._assert_gateway_restarts()
        self._ready()
        self._report("PASS", "complete")

    def run(self) -> int:
        try:
            self._run_body()
        except ValidationFailure as error:
            self._report("FAIL", error.stage)
            return 1
        except Exception:
            self._report("FAIL", "internal")
            return 1
        return 0


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ValidationFailure("cli")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SilentParser(add_help=False)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--app", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--run", action="store_true")
    try:
        args = parser.parse_args(argv)
        if not args.run or not all(
            isinstance(value, str) and value
            for value in (args.context, args.namespace, args.app, args.host)
        ):
            raise ValidationFailure("cli")
        return Validator(
            context=args.context,
            namespace=args.namespace,
            app=args.app,
            host=args.host,
        ).run()
    except Exception:
        print("FAIL cli")
        return 2


if __name__ == "__main__":
    sys.exit(main())
