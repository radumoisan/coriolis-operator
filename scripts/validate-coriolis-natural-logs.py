#!/usr/bin/env python3
"""Value-silent natural-log correlation validation for a deployed appliance.

This read-only observer proves that naturally produced application log lines
correlate exactly across three surfaces for every required producer of an
already-deployed CoriolisAppliance:

1. Kubernetes main-container logs (bounded ``kubectl logs``);
2. Alloy/Loki retention (gateway ``/loki/api/v1/query_range`` through a local
   port-forward with exact stream-label verification);
3. The logs API adaptor history path (HTTPS ``/logs`` and bounded non-chunked
   ``/logs/<component>`` downloads).

Memcached and the logging infrastructure (loki, gateway, alloy, adaptor) are
exempt from required non-empty natural correlation; after their Ready checks
their exact selected containers still receive bounded ``kubectl logs`` calls
solely to prove Kubernetes log readability, and empty output is acceptable.

The validator never mutates cluster or application state. It never prints raw
log lines, digests, Loki bodies, Secret values, tokens, refs, or credentials;
every external response and command output is audited against a value
registry before processing and any registered secret is a hard failure.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
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

from coriolis_operator.common import (  # type: ignore[import-untyped]
    BOOTSTRAP_COMPONENT,
)
from coriolis_operator.logging import (  # type: ignore[import-untyped]
    ALLOY_APP_COLLECTION_COMPONENTS,
)

GATEWAY_PORT = 8080
TIMEOUT = 30
DEFAULT_SINCE_SECONDS = 3600
MIN_SINCE_SECONDS = 60
MAX_SINCE_SECONDS = 86400
LOKI_QUERY_LIMIT = 5000
MEMCACHED_COMPONENT = "memcached"
BOOTSTRAP_CATEGORY = "coriolis-bootstrap-"
APPLIANCE_LABEL = "coriolis.cloudbase.it/appliance"
COMPONENT_LABEL = "coriolis.cloudbase.it/component"

_STAGE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CRI_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?\s+(?:stdout|stderr)\s+[FP]?\s*"
)
_SYNTHETIC_PREFIXES = ("matrix-", "coriolis-reconnect-validator-")
_SYNTHETIC_MARKERS = ("synthetic retention marker",)
_KUBECTL_VERBS = frozenset({"get", "logs", "port-forward"})

# Historical synthetic qualification and live-stream surfaces are self-checked
# or exempt here; logging infrastructure is correlated by direct readiness.
LOGGING_INFRA_CONTAINERS: Mapping[str, tuple[str, ...]] = {
    "loki": ("loki", "gateway"),
    "alloy": ("alloy",),
    "adaptor": ("adaptor",),
}
# The exact allowlist this validator was written against. Any source drift in
# ALLOY_APP_COLLECTION_COMPONENTS or BOOTSTRAP_COMPONENT must fail at startup.
EXPECTED_PRODUCERS = (
    "mariadb",
    "rabbitmq",
    "keystone",
    "barbican-api",
    "barbican-worker",
    "common-bootstrap-v3",
    "coriolis-conductor",
    "coriolis-scheduler",
    "coriolis-transfer-cron",
    "coriolis-minion-manager",
    "coriolis-deployer-manager",
    "coriolis-worker",
    "coriolis-api",
    "coriolis-web",
)


def _required_producers() -> tuple[str, ...]:
    derived = tuple(
        component
        for component in ALLOY_APP_COLLECTION_COMPONENTS
        if component != MEMCACHED_COMPONENT
    )
    if (
        derived != EXPECTED_PRODUCERS
        or set(derived) != set(EXPECTED_PRODUCERS)
        or len(derived) != 14
        or MEMCACHED_COMPONENT in derived
        or BOOTSTRAP_COMPONENT not in derived
    ):
        raise ValidationFailure("producers")
    return derived


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


@dataclass(frozen=True)
class SecretRegistry:
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
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: int,
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
            error_body: bytes = error.read()
        except OSError:
            error_body = b""
        return HttpResponse(error.code, dict(error.headers.items()), error_body)
    except (OSError, urllib.error.URLError):
        raise ValidationFailure("http") from None


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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = clock() + timeout
        while clock() < deadline:
            try:
                with socket.create_connection(
                    ("127.0.0.1", local_port), timeout=1
                ) as probe:
                    del probe
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


def _natural_lines(text: str) -> list[str]:
    """Deterministically normalize physical lines into natural candidates."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _CRI_PREFIX.sub("", _ANSI.sub("", raw), count=1)
        line = _ANSI.sub("", line).strip()
        if not line or not any(character.isalpha() for character in line):
            continue
        if line.startswith(_SYNTHETIC_PREFIXES) or any(
            marker in line for marker in _SYNTHETIC_MARKERS
        ):
            continue
        lines.append(line)
    return lines


def _digests(lines: Sequence[str]) -> frozenset[str]:
    return frozenset(hashlib.sha256(line.encode("utf-8")).hexdigest() for line in lines)


class Validator:
    def __init__(
        self,
        *,
        context: str,
        namespace: str,
        app: str,
        host: str,
        since_seconds: int = DEFAULT_SINCE_SECONDS,
        timeout: int = TIMEOUT,
        runner: CommandRunner = _run,
        http: Callable[
            [str, str, Mapping[str, str], bytes | None, int], HttpResponse
        ] = _http,
        clock: Clock = time.monotonic,
        now: Clock = time.time,
        sleeper: Sleeper = time.sleep,
        report: Reporter = print,
        forward: Callable[
            [Sequence[str], int, Clock, Sleeper],
            contextlib.AbstractContextManager[str],
        ] = _port_forward,
    ) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (context, namespace, app, host)
        ):
            raise ValidationFailure("cli")
        if (
            isinstance(since_seconds, bool)
            or not isinstance(since_seconds, int)
            or not MIN_SINCE_SECONDS <= since_seconds <= MAX_SINCE_SECONDS
        ):
            raise ValidationFailure("cli")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise ValidationFailure("cli")
        self.context = context
        self.namespace = namespace
        self.app = app
        self.host = host
        self.since_seconds = since_seconds
        self.timeout = timeout
        self.runner = runner
        self.http = http
        self.clock = clock
        self.now = now
        self.sleeper = sleeper
        self._raw_report = report
        self.forward = forward
        self.producers = REQUIRED_PRODUCERS
        self.registry = SecretRegistry()
        self.credentials: Credentials | None = None
        self.tenant: str | None = None
        self.token: str | None = None
        self._k8s: dict[str, list[str]] = {}
        self._loki: dict[str, list[str]] = {}
        self._adaptor: dict[str, list[str]] = {}
        self._ready_pods: dict[str, str] = {}
        self._window_end: int | None = None

    def _report(self, status: str, stage: str) -> None:
        if status not in {"PASS", "FAIL"} or _STAGE.fullmatch(stage) is None:
            raise ValidationFailure("report")
        line = f"{status} {stage}"
        self.registry.audit(line)
        self._raw_report(line)

    def _summary(self, elapsed: float) -> None:
        line = f"SUMMARY natural-logs passed {int(elapsed)}"
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
        parts = list(command)
        prefix = [
            "kubectl",
            "--context",
            self.context,
            "--namespace",
            self.namespace,
        ]
        if parts[:5] != prefix:
            raise ValidationFailure(stage)
        if len(parts) < 6 or parts[5] not in _KUBECTL_VERBS:
            raise ValidationFailure("verbs")
        try:
            result = self.runner(parts, self.timeout)
        except Exception:
            raise ValidationFailure(stage) from None
        self.registry.audit(result.stdout)
        self.registry.audit(result.stderr)
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
    def _secret_values(payload: Mapping[str, object], stage: str) -> dict[str, str]:
        data = payload.get("data")
        if not isinstance(data, dict) or not data:
            raise ValidationFailure(stage)
        values: dict[str, str] = {}
        for key, encoded in data.items():
            if not isinstance(key, str) or not isinstance(encoded, str):
                raise ValidationFailure(stage)
            try:
                value = base64.b64decode(encoded, validate=True).decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                raise ValidationFailure(stage) from None
            if not value:
                raise ValidationFailure(stage)
            values[key] = value
        return values

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
        logging_values = self._secret_values(logging, "credentials")
        coriolis_values = self._secret_values(coriolis, "credentials")
        for value in (*logging_values.values(), *coriolis_values.values()):
            self.registry.register(value)
        try:
            credentials = Credentials(
                logging_values["read_password"],
                logging_values["write_password"],
                coriolis_values["coriolis_keystone_password"],
            )
        except KeyError:
            raise ValidationFailure("credentials") from None
        self.credentials = credentials

    def _authenticate(self) -> str:
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
        ).encode("utf-8")
        response = self.http(
            "POST",
            f"https://{self.host}/identity/auth/tokens",
            {"Content-Type": "application/json", "Accept": "application/json"},
            body,
            self.timeout,
        )
        self.registry.audit(response.headers)
        self.registry.audit(response.body)
        token = response.headers.get("X-Subject-Token") or response.headers.get(
            "x-subject-token"
        )
        if response.status != 201 or not isinstance(token, str) or not token:
            raise ValidationFailure("token")
        self.registry.register(token)
        self.token = token
        return token

    @staticmethod
    def _pod_field(pod: Mapping[str, object], *path: str) -> object:
        current: object = pod
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    @classmethod
    def _pod_ready(cls, pod: Mapping[str, object], containers: Sequence[str]) -> bool:
        conditions = cls._pod_field(pod, "status", "conditions")
        if not isinstance(conditions, list) or not any(
            isinstance(condition, dict)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        ):
            return False
        statuses = cls._pod_field(pod, "status", "containerStatuses")
        if not isinstance(statuses, list):
            return False
        return all(
            any(
                isinstance(item, dict)
                and item.get("name") == container
                and item.get("ready") is True
                for item in statuses
            )
            for container in containers
        )

    def _appliance_pods(self, stage: str, component: str) -> list[dict[str, object]]:
        selector = f"{APPLIANCE_LABEL}={self.app},{COMPONENT_LABEL}={component}"
        payload = self._json(stage, "pods", "-l", selector)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValidationFailure(stage)
        matches: list[dict[str, object]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValidationFailure(stage)
            labels = self._pod_field(item, "metadata", "labels")
            if (
                isinstance(labels, dict)
                and labels.get(APPLIANCE_LABEL) == self.app
                and labels.get(COMPONENT_LABEL) == component
            ):
                matches.append(item)
        return matches

    def _ready_infra(self) -> None:
        surfaces: dict[str, tuple[str, ...]] = {
            MEMCACHED_COMPONENT: (MEMCACHED_COMPONENT,),
            **LOGGING_INFRA_CONTAINERS,
        }
        for component, containers in surfaces.items():
            stage = "readiness"
            pods = self._appliance_pods(stage, component)
            if len(pods) != 1:
                raise ValidationFailure(stage)
            pod = pods[0]
            if self._pod_field(pod, "status", "phase") != "Running":
                raise ValidationFailure(stage)
            if not self._pod_ready(pod, containers):
                raise ValidationFailure(stage)
            name = self._pod_field(pod, "metadata", "name")
            if not isinstance(name, str) or not name:
                raise ValidationFailure(stage)
            self._ready_pods[component] = name

    def _boundary_logs(self, container: str) -> None:
        pod_key = "loki" if container == "gateway" else container
        name = self._ready_pods.get(pod_key)
        if not name:
            raise ValidationFailure("readiness")
        self._checked(
            f"logs-{container}",
            self._kubectl(
                "logs",
                name,
                "-c",
                container,
                f"--since={self.since_seconds}s",
                "--timestamps=false",
            ),
        )

    def _boundary_readability(self) -> None:
        self._boundary_logs(MEMCACHED_COMPONENT)
        self._report("PASS", "memcached-exempt")
        for container in ("loki", "gateway", "alloy", "adaptor"):
            self._boundary_logs(container)
        self._report("PASS", "logging-infrastructure")

    def _capture_producer_logs(self) -> None:
        for component in self.producers:
            stage = f"pod-{component}"
            pods = self._appliance_pods(stage, component)
            eligible: list[dict[str, object]] = []
            for pod in pods:
                deletion_timestamp = self._pod_field(
                    pod, "metadata", "deletionTimestamp"
                )
                if deletion_timestamp is not None:
                    continue
                phase = self._pod_field(pod, "status", "phase")
                completed = phase == "Succeeded"
                if phase == "Failed" and component == BOOTSTRAP_COMPONENT:
                    continue
                if completed and component != BOOTSTRAP_COMPONENT:
                    raise ValidationFailure(stage)
                if not completed and phase != "Running":
                    raise ValidationFailure(stage)
                containers = self._pod_field(pod, "spec", "containers")
                if not isinstance(containers, list):
                    raise ValidationFailure(stage)
                mains = [
                    container
                    for container in containers
                    if isinstance(container, dict)
                    and container.get("name") == component
                ]
                if len(mains) != 1:
                    raise ValidationFailure(stage)
                if not completed and not self._pod_ready(pod, (component,)):
                    raise ValidationFailure(stage)
                eligible.append(pod)
            if len(eligible) != 1:
                raise ValidationFailure(stage)
            name = self._pod_field(eligible[0], "metadata", "name")
            if not isinstance(name, str) or not name:
                raise ValidationFailure(stage)
            result = self._checked(
                f"logs-{component}",
                self._kubectl(
                    "logs",
                    name,
                    "-c",
                    component,
                    f"--since={self.since_seconds}s",
                    "--timestamps=false",
                ),
            )
            self._k8s[component] = _natural_lines(result.stdout)
        self._window_end = int(self.now())

    def _comparison_window(self, stage: str) -> tuple[int, int]:
        if self._window_end is None or self._window_end < self.since_seconds:
            raise ValidationFailure(stage)
        return self._window_end - self.since_seconds, self._window_end

    @staticmethod
    def _logql(component: str, namespace: str, app: str) -> str:
        return (
            f'{{namespace="{namespace}",coriolis_appliance="{app}",'
            f'coriolis_component="{component}",container="{component}"}}'
        )

    def _loki_correlate(self, base_url: str, component: str) -> None:
        if self.credentials is None or self.tenant is None:
            raise ValidationFailure("loki")
        stage = f"loki-{component}"
        start, end = self._comparison_window(stage)
        start_ns = start * 1_000_000_000
        end_ns = end * 1_000_000_000
        query = urllib.parse.urlencode(
            {
                "query": self._logql(component, self.namespace, self.app),
                "start": str(start_ns),
                "end": str(end_ns),
                "limit": str(LOKI_QUERY_LIMIT),
                "direction": "backward",
            }
        )
        basic = base64.b64encode(
            f"{self.tenant}:{self.credentials.read_password}".encode()
        ).decode("ascii")
        response = self.http(
            "GET",
            f"{base_url}/loki/api/v1/query_range?{query}",
            {"Authorization": f"Basic {basic}"},
            None,
            self.timeout,
        )
        self.registry.audit(response.headers)
        self.registry.audit(response.body)
        if response.status != 200:
            raise ValidationFailure(stage)
        try:
            payload = json.loads(response.body)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationFailure(stage) from None
        result = (
            payload.get("data", {}).get("result")
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict)
            else None
        )
        if not isinstance(result, list):
            raise ValidationFailure(stage)
        required = {
            "namespace": self.namespace,
            "coriolis_appliance": self.app,
            "coriolis_component": component,
            "container": component,
        }
        lines: list[str] = []
        for stream in result:
            if not isinstance(stream, dict) or not isinstance(
                stream.get("stream"), dict
            ):
                raise ValidationFailure(stage)
            labels = stream["stream"]
            if any(labels.get(key) != value for key, value in required.items()):
                raise ValidationFailure(stage)
            values = stream.get("values")
            if not isinstance(values, list):
                raise ValidationFailure(stage)
            for entry in values:
                if (
                    not isinstance(entry, list)
                    or len(entry) != 2
                    or not isinstance(entry[1], str)
                ):
                    raise ValidationFailure(stage)
                lines.append(entry[1])
        self._loki[component] = lines
        correlated = _digests(_natural_lines("\n".join(lines))) & _digests(
            self._k8s[component]
        )
        if not correlated:
            raise ValidationFailure(stage)

    def _adaptor_headers(self) -> dict[str, str]:
        if self.token is None:
            raise ValidationFailure("adaptor")
        return {"X-Auth-Token": self.token, "Accept": "application/json"}

    def _adaptor_list(self) -> None:
        response = self.http(
            "GET",
            f"https://{self.host}/logs",
            self._adaptor_headers(),
            None,
            self.timeout,
        )
        self.registry.audit(response.headers)
        self.registry.audit(response.body)
        if response.status != 200:
            raise ValidationFailure("adaptor")
        try:
            payload = json.loads(response.body)
            logs = payload.get("logs") if isinstance(payload, dict) else None
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationFailure("adaptor") from None
        if not isinstance(logs, list):
            raise ValidationFailure("adaptor")
        names: set[str] = set()
        for item in logs:
            if not isinstance(item, dict) or not isinstance(item.get("log_name"), str):
                raise ValidationFailure("adaptor")
            names.add(item["log_name"])
        if not set(self.producers) <= names:
            raise ValidationFailure("adaptor")

    def _adaptor_correlate(self, component: str) -> None:
        stage = f"adaptor-{component}"
        if self.token is None:
            raise ValidationFailure(stage)
        start, end = self._comparison_window(stage)
        headers = {"X-Auth-Token": self.token}
        query = urllib.parse.urlencode(
            {
                "start_date": str(start),
                "end_date": str(end),
                "disable_chunked": "true",
            }
        )
        response = self.http(
            "GET",
            f"https://{self.host}/logs/{component}?{query}",
            headers,
            None,
            self.timeout,
        )
        self.registry.audit(response.headers)
        self.registry.audit(response.body)
        if response.status != 200 or not response.body:
            raise ValidationFailure(stage)
        try:
            body = response.body.decode("utf-8")
        except UnicodeDecodeError:
            raise ValidationFailure(stage) from None
        lines = _natural_lines(body)
        if not lines:
            raise ValidationFailure(stage)
        self._adaptor[component] = lines
        correlated = (
            _digests(lines)
            & _digests(self._k8s[component])
            & _digests(_natural_lines("\n".join(self._loki[component])))
        )
        if not correlated:
            raise ValidationFailure(stage)

    def _bootstrap_category(self) -> None:
        component = BOOTSTRAP_COMPONENT
        wanted = {
            hashlib.sha256(line.encode("utf-8")).hexdigest()
            for line in self._k8s[component]
            if BOOTSTRAP_CATEGORY in line
        }
        correlated = (
            wanted
            & _digests(_natural_lines("\n".join(self._loki[component])))
            & _digests(self._adaptor[component])
        )
        if not correlated:
            raise ValidationFailure("bootstrap-category")

    def _run_body(self) -> None:
        self._report("PASS", "producers")
        self._read_tenant()
        self._report("PASS", "tenant")
        self._read_credentials()
        self._report("PASS", "credentials")
        self._authenticate()
        self._report("PASS", "token")
        self._ready_infra()
        self._report("PASS", "readiness")
        self._boundary_readability()
        self._capture_producer_logs()
        command = self._kubectl("port-forward", f"svc/{self.app}-gateway")
        with self.forward(command, self.timeout, self.clock, self.sleeper) as base_url:
            self._report("PASS", "gateway")
            for component in self.producers:
                self._loki_correlate(base_url, component)
        self._adaptor_list()
        self._report("PASS", "adaptor-list")
        for component in self.producers:
            self._adaptor_correlate(component)
            if component == BOOTSTRAP_COMPONENT:
                self._bootstrap_category()
            self._report("PASS", f"natural-{component}")

    def run(self) -> int:
        start = self.clock()
        try:
            self._run_body()
        except ValidationFailure as error:
            self._report("FAIL", error.stage)
            return 1
        except Exception:
            self._report("FAIL", "internal")
            return 1
        self._summary(self.clock() - start)
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
    parser.add_argument("--since-seconds", type=int, default=DEFAULT_SINCE_SECONDS)
    try:
        args = parser.parse_args(argv)
        if not args.run:
            raise ValidationFailure("cli")
        if not all(
            isinstance(value, str) and _DNS_LABEL.fullmatch(value)
            for value in (args.context, args.namespace, args.app)
        ):
            raise ValidationFailure("cli")
        if not isinstance(args.host, str) or not _HOST.fullmatch(args.host):
            raise ValidationFailure("cli")
        return Validator(
            context=args.context,
            namespace=args.namespace,
            app=args.app,
            host=args.host,
            since_seconds=args.since_seconds,
        ).run()
    except Exception:
        print("FAIL cli")
        return 2


REQUIRED_PRODUCERS = _required_producers()

if __name__ == "__main__":
    sys.exit(main())
