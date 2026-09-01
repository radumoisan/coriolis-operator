#!/usr/bin/env python3
"""Value-silent lifecycle qualification for two disposable Coriolis appliances."""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_NAMESPACE = "coriolis"
ARGO_NAMESPACE = "argocd"
OPERATOR_NAME = "coriolis-operator"
OPERATOR_DIGEST = (
    "sha256:68eacc65ce877065d6850c7888eb06a3a09f15254da206ed8168851fab31d44e"
)
TLS_FIXTURE_NAME = "logging-isolation-qualification-tls"
COMMAND_TIMEOUT = 30
SETUP_TIMEOUT = 20 * 60
CLEANUP_TIMEOUT = 15 * 60
RECONNECT_TIMEOUT = 5 * 60
RETENTION_TIMEOUT = 4 * 60 * 60
# 3h exact retention eligibility plus a 30m polling allowance, leaving the
# 4h parent window ~30m of margin; the prior direct formal run passed at ~185.9m.
RETENTION_CHILD_MAX_WAIT_MINUTES = 210
# Per-command CLI timeout inside the retention child; 300s covers the measured
# 63-110s recreation plus a 60s collision retry.
RETENTION_CHILD_COMMAND_TIMEOUT = 5 * 60
POLL_SECONDS = 2.0
_STAGE = re.compile(r"^[a-z0-9-]+$")
_RETENTION_SUMMARY = re.compile(r"^SUMMARY retention-formal passed \d+\.\d{3}$")
_RETENTION_STAGE = re.compile(r"^PASS ([a-z0-9-]+) \d+\.\d{3}$")
_CHILD_FAILURE = re.compile(r"FAIL ([a-z0-9-]+)")
_LOGGING_CHILD_OUTPUT = (
    "PASS tenant",
    "PASS credentials",
    "PASS token",
    "PASS readiness",
    "PASS gateway-restarts",
    "PASS record-a",
    "PASS worker-terminate",
    "PASS record-b",
    "PASS continuity",
    "PASS complete",
)
_RETENTION_STAGES = frozenset(
    {
        "cr-uid",
        "secret",
        "cr-manifest",
        "config-release",
        "config-release-loaded",
        "observer-create",
        "observer-ready",
        "observer-tools",
        "port-forward",
        "inventory-pre-push",
        "marker-push",
        "query-before",
        "port-forward-close",
        "chunk-flush",
        "chunk-materialized",
        "query-persisted",
        "retained-resources",
        "cr-delete",
        "cr-absent",
        "cr-create",
        "cr-ready",
        "retained-verified",
        "config-release-recreated",
        "config-release-loaded-recreated",
        "query-new-tenant-isolated",
        "query-old-tenant-persisted",
        "formal-retention",
    }
)
_REQUIRED_RETENTION_STAGES = _RETENTION_STAGES - {"port-forward"}

CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
InputRunner = Callable[[Sequence[str], str, int], subprocess.CompletedProcess[str]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
Reporter = Callable[[str], None]


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, repr=False)
class MatrixIdentity:
    app: str
    host: str
    uid: str
    read_password: str
    write_password: str
    keystone_password: str
    token: str


HttpClient = Callable[[str, str, Mapping[str, str], bytes | None, int], HttpResponse]
WsFactory = Callable[[str, Mapping[str, str], int], object]
Forward = Callable[
    [Sequence[str], int, Clock, Sleeper], contextlib.AbstractContextManager[str]
]
MarkerFactory = Callable[[], str]
RedirectClient = Callable[[str, int], HttpResponse]


class ValidationFailure(Exception):
    """A stable, non-sensitive failure stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


@dataclass
class SecretRegistry:
    """Tracks values that must never be included in a report or command."""

    forms: set[str] = field(default_factory=set)

    def _register_forms(self, value: str) -> None:
        self.forms.update(
            {
                value,
                base64.b64encode(value.encode()).decode("ascii"),
                base64.urlsafe_b64encode(value.encode()).decode("ascii"),
                urllib.parse.quote(value, safe=""),
                urllib.parse.quote_plus(value, safe=""),
                json.dumps(value),
                base64.b64encode(json.dumps(value).encode()).decode("ascii"),
            }
        )

    def register_secret(self, encoded_value: object) -> None:
        """Register only a Secret data entry and safe renderings of its value."""
        if not isinstance(encoded_value, str) or not encoded_value:
            raise ValidationFailure("secret-copy")
        values = {encoded_value}
        try:
            decoded = base64.b64decode(encoded_value, validate=True).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            decoded = encoded_value
        values.add(decoded)
        try:
            payload = json.loads(decoded)
        except (TypeError, json.JSONDecodeError):
            payload = None

        def leaves(value: object) -> Iterator[str]:
            if isinstance(value, Mapping):
                for item in value.values():
                    yield from leaves(item)
            elif isinstance(value, list):
                for item in value:
                    yield from leaves(item)
            elif isinstance(value, str):
                yield value

        values.update(leaves(payload))
        for value in values:
            self._register_forms(value)

    def register_runtime_secret(self, value: object) -> None:
        if not isinstance(value, str) or not value:
            raise ValidationFailure("token")
        self._register_forms(value)

    def audit(self, value: object) -> None:
        text = str(value)
        if any(form in text for form in self.forms):
            raise ValidationFailure("secret-leak")


def _run(command: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), check=False, capture_output=True, text=True, timeout=timeout
    )


def _run_input(
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
            body = error.read()
        except OSError:
            body = b""
        return HttpResponse(error.code, dict(error.headers.items()), body)
    except (OSError, urllib.error.URLError):
        raise ValidationFailure("matrix") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        del request, file, code, message, headers, new_url
        return None


def _http_redirect(url: str, timeout: int) -> HttpResponse:
    request = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
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
        raise ValidationFailure("matrix") from None


def _open_ws(url: str, headers: Mapping[str, str], timeout: int) -> object:
    try:
        import websocket

        return websocket.create_connection(
            url,
            header=[f"{key}: {value}" for key, value in headers.items()],
            timeout=timeout,
        )
    except Exception:
        raise ValidationFailure("matrix") from None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextlib.contextmanager
def _port_forward(
    command: Sequence[str], timeout: int, clock: Clock, sleeper: Sleeper
) -> Iterator[str]:
    port = _free_port()
    proc = subprocess.Popen(
        [*command, f"{port}:8080"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = clock() + timeout
        while clock() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    yield f"http://127.0.0.1:{port}"
                    return
            except OSError:
                if proc.poll() is not None:
                    raise ValidationFailure("matrix")
                sleeper(0.2)
        raise ValidationFailure("matrix")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            try:
                proc.kill()
            except OSError:
                pass


def _resources() -> dict[str, object]:
    return {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    }


def appliance_manifest(
    name: str, host: str, tls_secret_name: str | None = None
) -> dict[str, object]:
    """Build the released disposable appliance contract without writing a file."""
    return {
        "apiVersion": "coriolis.cloudbase.it/v1alpha1",
        "kind": "CoriolisAppliance",
        "metadata": {"name": name},
        "spec": {
            "profile": "core",
            "version": "2603.4",
            "storage": {
                "mariadb": {"storageClassName": "local-path", "size": "10Gi"},
                "rabbitmq": {"storageClassName": "local-path", "size": "1Gi"},
            },
            "resources": {"mariadb": _resources(), "rabbitmq": _resources()},
            "ingress": {
                "host": host,
                "ingressClassName": "nginx",
                "tls": (
                    {"mode": "existingSecret", "tlsSecretName": tls_secret_name}
                    if tls_secret_name
                    else {"mode": "certManager", "clusterIssuer": "letsencrypt"}
                ),
            },
            "logging": {
                "retentionHours": 1,
                "storage": {"loki": {"storageClassName": "local-path", "size": "10Gi"}},
                "resources": {
                    "loki": _resources(),
                    "gateway": {
                        "requests": {"cpu": "100m", "memory": "32Mi"},
                        "limits": {"cpu": "1", "memory": "64Mi"},
                    },
                    "alloy": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "500m", "memory": "512Mi"},
                    },
                    "adaptor": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "500m", "memory": "512Mi"},
                    },
                },
            },
        },
    }


def tls_fixture_manifest(host_a: str, host_b: str) -> dict[str, object]:
    return {
        "apiVersion": "cert-manager.io/v1",
        "kind": "Certificate",
        "metadata": {"name": TLS_FIXTURE_NAME},
        "spec": {
            "secretName": TLS_FIXTURE_NAME,
            "issuerRef": {"name": "letsencrypt", "kind": "ClusterIssuer"},
            "commonName": host_b,
            "dnsNames": sorted({host_a, host_b}),
        },
    }


_TLS_FIXTURE_RATE_LIMITED = "tls-fixture-certificate-rate-limited"
_TLS_FIXTURE_DNS = "tls-fixture-certificate-dns"
_TLS_FIXTURE_ISSUER = "tls-fixture-certificate-issuer"
_TLS_FIXTURE_PENDING = "tls-fixture-certificate-pending"
_TLS_RATE_LIMIT_SIGNATURES = (
    "ratelimited",
    "rate limited",
    "rate-limit",
    "too many certificates",
    "exact set of identifiers",
    "429",
)
_TLS_DNS_FAILURE_STATES = frozenset({"errored", "invalid", "expired"})
_TLS_DNS_MARKERS = ("dns", "propagation", "resolver", "txt")
_TLS_DNS_FAILURE_EVIDENCE = (
    "fail",
    "couldn't",
    "could not",
    "error",
    "ran out",
    "exceeded",
    "did not",
)


_REASON_KEYS = frozenset({"reason", "message", "lastFailureReason"})


def _tls_status_strings(value: object) -> Iterator[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, str) and key in _REASON_KEYS:
                yield item
            else:
                yield from _tls_status_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _tls_status_strings(item)


def _tls_ready_condition_status(payload: object) -> str:
    if not isinstance(payload, Mapping):
        return ""
    status = payload.get("status")
    conditions = status.get("conditions") if isinstance(status, Mapping) else None
    if not isinstance(conditions, list):
        return ""
    for condition in conditions:
        if isinstance(condition, Mapping) and condition.get("type") == "Ready":
            value = condition.get("status")
            return value if isinstance(value, str) else ""
    return ""


def _classify_tls_fixture_failure(
    certificate: object,
    certificate_requests: Sequence[object],
    orders: Sequence[object],
    challenges: Sequence[object],
    cluster_issuer: object,
) -> str:
    """Map cert-manager evidence to one fixed diagnostic category.

    Only whitelisted signatures are matched internally; raw reason text is
    never returned or reported.
    """
    corpus = " ".join(
        text
        for payload in (
            certificate,
            *certificate_requests,
            *orders,
            *challenges,
            cluster_issuer,
        )
        for text in _tls_status_strings(payload)
    ).lower()
    if any(signature in corpus for signature in _TLS_RATE_LIMIT_SIGNATURES):
        return _TLS_FIXTURE_RATE_LIMITED
    for challenge in challenges:
        spec = challenge.get("spec") if isinstance(challenge, Mapping) else None
        status = challenge.get("status") if isinstance(challenge, Mapping) else None
        spec = spec if isinstance(spec, Mapping) else {}
        status = status if isinstance(status, Mapping) else {}
        if spec.get("type") != "dns-01" and not isinstance(spec.get("dnsName"), str):
            continue
        state = status.get("state")
        if isinstance(state, str) and state.lower() in _TLS_DNS_FAILURE_STATES:
            return _TLS_FIXTURE_DNS
        reason = str(status.get("reason", "")).lower()
        if any(marker in reason for marker in _TLS_DNS_MARKERS) and any(
            evidence in reason for evidence in _TLS_DNS_FAILURE_EVIDENCE
        ):
            return _TLS_FIXTURE_DNS
    if _tls_ready_condition_status(cluster_issuer) != "True":
        return _TLS_FIXTURE_ISSUER
    if (
        not orders
        and not challenges
        and any(
            _tls_ready_condition_status(request) == "False"
            for request in certificate_requests
        )
    ):
        return _TLS_FIXTURE_ISSUER
    return _TLS_FIXTURE_PENDING


def operator_values() -> dict[str, object]:
    """Return the released Helm override values for the disposable operator."""
    return {
        "image": {
            "repository": "cr.virtomat.io/virtomat/coriolis/operator",
            "tag": "0.5.49",
            "pullPolicy": "IfNotPresent",
        },
        "imagePullSecrets": [{"name": "regcred"}],
        "fullnameOverride": OPERATOR_NAME,
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "podSecurityContext": {"runAsNonRoot": True},
        "containerSecurityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
        },
        "liveness": {
            "port": 8080,
            "path": "/healthz",
            "initialDelaySeconds": 10,
            "periodSeconds": 10,
            "timeoutSeconds": 1,
            "failureThreshold": 3,
        },
    }


def application_manifest(application: str, namespace: str) -> dict[str, object]:
    """Build the pinned Argo CD Application for the released operator."""
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": application},
        "spec": {
            "project": "default",
            "source": {
                "repoURL": "cr.virtomat.io/virtomat",
                "chart": "coriolis/helm/coriolis-operator",
                "targetRevision": "0.5.49",
                "helm": {
                    "releaseName": application,
                    "skipCrds": True,
                    "values": json.dumps(operator_values(), separators=(",", ":")),
                },
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": namespace,
            },
            "syncPolicy": {"automated": {"prune": True, "selfHeal": True}},
        },
    }


class Validator:
    def __init__(
        self,
        *,
        context: str,
        namespace: str,
        application: str,
        app_a: str,
        host_a: str,
        app_b: str,
        host_b: str,
        mode: str = "formal",
        command_timeout: int = COMMAND_TIMEOUT,
        setup_timeout: int = SETUP_TIMEOUT,
        cleanup_timeout: int = CLEANUP_TIMEOUT,
        reconnect_timeout: int = RECONNECT_TIMEOUT,
        retention_timeout: int = RETENTION_TIMEOUT,
        runner: CommandRunner = _run,
        input_runner: InputRunner = _run_input,
        child_runner: CommandRunner = _run,
        http: HttpClient = _http,
        ws_factory: WsFactory = _open_ws,
        forward: Forward = _port_forward,
        marker_factory: MarkerFactory = lambda: secrets.token_hex(12),
        redirect_http: RedirectClient = _http_redirect,
        clock: Clock = time.monotonic,
        wallclock: Clock = time.time,
        sleeper: Sleeper = time.sleep,
        report: Reporter = print,
    ) -> None:
        self.context = context
        self.namespace = namespace
        self.application = application
        self.app_a = app_a
        self.host_a = host_a
        self.app_b = app_b
        self.host_b = host_b
        if mode not in {"fast", "formal"}:
            raise ValidationFailure("cli")
        self.mode = mode
        self.command_timeout = command_timeout
        self.setup_timeout = setup_timeout
        self.cleanup_timeout = cleanup_timeout
        self.reconnect_timeout = reconnect_timeout
        self.retention_timeout = retention_timeout
        self.runner = runner
        self.input_runner = input_runner
        self.child_runner = child_runner
        self.http = http
        self.ws_factory = ws_factory
        self.forward = forward
        self.marker_factory = marker_factory
        self.redirect_http = redirect_http
        self.clock = clock
        self.wallclock = wallclock
        self.sleeper = sleeper
        self._raw_report = report
        self.registry = SecretRegistry()
        self.namespace_created = False
        self.created_apps: list[str] = []
        self.recorded_pvs: set[str] = set()

    def _report(self, status: str, stage: str) -> None:
        if status not in {"PASS", "FAIL"} or _STAGE.fullmatch(stage) is None:
            raise ValidationFailure("report")
        line = f"{status} {stage}"
        self.registry.audit(line)
        self._raw_report(line)

    def _summary(self, elapsed: float) -> None:
        line = f"SUMMARY logging-{self.mode} passed {elapsed:.3f}"
        self.registry.audit(line)
        self._raw_report(line)

    def _kubectl(self, namespace: str, *arguments: str) -> list[str]:
        return [
            "kubectl",
            "--context",
            self.context,
            "--namespace",
            namespace,
            *arguments,
        ]

    def _cluster_kubectl(self, *arguments: str) -> list[str]:
        return ["kubectl", "--context", self.context, *arguments]

    def _checked(
        self, stage: str, command: Sequence[str]
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self.runner(command, self.command_timeout)
            self.registry.audit(result.stdout)
            self.registry.audit(result.stderr)
        except ValidationFailure:
            raise
        except Exception:
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)
        return result

    def _apply(
        self, stage: str, namespace: str, manifest: Mapping[str, object]
    ) -> None:
        data = json.dumps(manifest, separators=(",", ":"))
        try:
            result = self.input_runner(
                self._kubectl(namespace, "apply", "-f", "-"),
                data,
                self.command_timeout,
            )
            self.registry.audit(result.stdout)
            self.registry.audit(result.stderr)
        except ValidationFailure:
            raise
        except Exception:
            raise ValidationFailure(stage) from None
        if result.returncode != 0:
            raise ValidationFailure(stage)

    def _json(self, stage: str, namespace: str, *arguments: str) -> dict[str, object]:
        result = self._checked(
            stage, self._kubectl(namespace, "get", *arguments, "-o", "json")
        )
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise ValidationFailure(stage) from None
        if not isinstance(payload, dict):
            raise ValidationFailure(stage)
        return payload

    def _cluster_json(self, stage: str, *arguments: str) -> dict[str, object]:
        result = self._checked(
            stage, self._cluster_kubectl("get", *arguments, "-o", "json")
        )
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise ValidationFailure(stage) from None
        if not isinstance(payload, dict):
            raise ValidationFailure(stage)
        return payload

    @staticmethod
    def _mapping(value: object) -> Mapping[str, object]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _resource_items(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, Mapping)]

    def _wait(self, stage: str, timeout: int, check: Callable[[], bool]) -> None:
        deadline = self.clock() + timeout
        while True:
            try:
                if check():
                    return
            except ValidationFailure as error:
                if error.stage == "secret-leak":
                    raise
            except Exception:
                pass
            if self.clock() >= deadline:
                raise ValidationFailure(stage)
            self.sleeper(POLL_SECONDS)

    def _create_namespace(self) -> None:
        result = self._checked(
            "namespace-fresh",
            self._kubectl(
                self.namespace, "get", "namespace", self.namespace, "--ignore-not-found"
            ),
        )
        if result.stdout.strip():
            raise ValidationFailure("namespace-fresh")
        self._apply(
            "namespace-create",
            self.namespace,
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": self.namespace},
            },
        )
        self.namespace_created = True

    def _application_absent(self) -> bool:
        result = self._checked(
            "preflight",
            self._kubectl(
                ARGO_NAMESPACE,
                "get",
                "application",
                self.application,
                "--ignore-not-found",
            ),
        )
        return not result.stdout.strip()

    def _cluster_issuer_ready(self) -> bool:
        payload = self._cluster_json("preflight", "clusterissuer", "letsencrypt")
        conditions = self._mapping(payload.get("status")).get("conditions")
        return isinstance(conditions, list) and any(
            isinstance(condition, Mapping)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        )

    def _fresh_pvs(self) -> bool:
        payload = self._cluster_json("preflight", "pv")
        items = payload.get("items")
        if not isinstance(items, list):
            return False
        for item in items:
            if not isinstance(item, Mapping):
                return False
            claim_ref = self._mapping(self._mapping(item.get("spec")).get("claimRef"))
            if claim_ref.get("namespace") == self.namespace:
                return False
        return True

    def _preflight(self) -> None:
        if (
            not self._shared_ready()
            or not self._application_absent()
            or not self._hosts_absent()
        ):
            raise ValidationFailure("preflight")
        self._cluster_json("preflight", "storageclass", "local-path")
        if not self._cluster_issuer_ready() or not self._fresh_pvs():
            raise ValidationFailure("preflight")

    def _copy_secret(self, name: str) -> None:
        try:
            result = self.runner(
                self._kubectl(SOURCE_NAMESPACE, "get", "secret", name, "-o", "json"),
                self.command_timeout,
            )
        except Exception:
            raise ValidationFailure("secret-copy") from None
        if result.returncode != 0:
            raise ValidationFailure("secret-copy")
        try:
            source = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise ValidationFailure("secret-copy") from None
        if not isinstance(source, Mapping):
            raise ValidationFailure("secret-copy")
        data = source.get("data")
        if not isinstance(data, Mapping) or not all(
            isinstance(v, str) for v in data.values()
        ):
            raise ValidationFailure("secret-copy")
        for value in data.values():
            self.registry.register_secret(value)
        manifest: dict[str, object] = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name},
            "type": source.get("type", "Opaque"),
            "data": dict(data),
        }
        self._apply("secret-copy", self.namespace, manifest)

    def _application_ready(self) -> bool:
        payload = self._json(
            "application-ready", ARGO_NAMESPACE, "application", self.application
        )
        status = self._mapping(payload.get("status"))
        sync = self._mapping(status.get("sync"))
        health = self._mapping(status.get("health"))
        return sync.get("status") == "Synced" and health.get("status") == "Healthy"

    def _deployment_ready(self) -> bool:
        payload = self._json(
            "operator-ready", self.namespace, "deployment", OPERATOR_NAME
        )
        status = self._mapping(payload.get("status"))
        ready = status.get("readyReplicas")
        return (
            isinstance(ready, int)
            and ready > 0
            and status.get("availableReplicas") == ready
        )

    def _operator_digest_ready(self) -> bool:
        payload = self._json(
            "operator-digest",
            self.namespace,
            "pods",
            "-l",
            f"app.kubernetes.io/instance={self.application}",
        )
        items = payload.get("items")
        if (
            not isinstance(items, list)
            or len(items) != 1
            or not isinstance(items[0], Mapping)
        ):
            return False
        statuses = self._mapping(items[0].get("status")).get("containerStatuses")
        if not isinstance(statuses, list):
            return False
        image_ids = [
            status.get("imageID")
            for status in statuses
            if isinstance(status, Mapping) and status.get("name") == "operator"
        ]
        if len(image_ids) != 1 or not isinstance(image_ids[0], str):
            return False
        match = re.search(r"sha256:[0-9a-f]{64}", image_ids[0])
        return match is not None and match.group(0) == OPERATOR_DIGEST

    def _appliance_ready(self, name: str) -> bool:
        payload = self._json(
            "appliance-ready", self.namespace, "coriolisappliance", name
        )
        metadata = self._mapping(payload.get("metadata"))
        status = self._mapping(payload.get("status"))
        generation = metadata.get("generation")
        if (
            not isinstance(generation, int)
            or status.get("observedGeneration") != generation
            or status.get("acceptedVersion") != "2603.4"
        ):
            return False
        conditions = status.get("conditions")
        if not isinstance(conditions, list):
            return False
        observed = {
            item.get("type"): item.get("status")
            for item in conditions
            if isinstance(item, Mapping)
        }
        return all(
            observed.get(condition) == "True"
            for condition in ("Accepted", "Reconciled", "Ready", "LoggingReady")
        )

    def _workloads_ready(self) -> bool:
        payload = self._json(
            "workloads-ready", self.namespace, "deployments,statefulsets"
        )
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return False
        for item in items:
            if not isinstance(item, Mapping):
                return False
            # Deliberately use only identity and status, never desired replica specs.
            metadata = self._mapping(item.get("metadata"))
            status = self._mapping(item.get("status"))
            if not isinstance(metadata.get("name"), str):
                return False
            replicas = status.get("replicas")
            ready = status.get("readyReplicas")
            if not isinstance(replicas, int) or replicas < 1 or ready != replicas:
                return False
        return True

    def _record_pvs(self) -> None:
        payload = self._json("pvc-inventory", self.namespace, "pvc")
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValidationFailure("pvc-inventory")
        for item in items:
            if not isinstance(item, Mapping):
                continue
            volume_name = self._mapping(item.get("spec")).get("volumeName")
            if isinstance(volume_name, str) and volume_name:
                self.recorded_pvs.add(volume_name)

    def _secret_field(self, payload: Mapping[str, object], key: str) -> str:
        data = self._mapping(payload.get("data"))
        encoded = data.get(key)
        if not isinstance(encoded, str) or not encoded:
            raise ValidationFailure("matrix")
        self.registry.register_secret(encoded)
        try:
            value = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            raise ValidationFailure("matrix") from None
        if not value:
            raise ValidationFailure("matrix")
        return value

    def _matrix_secret(self, name: str) -> dict[str, object]:
        try:
            result = self.runner(
                self._kubectl(self.namespace, "get", "secret", name, "-o", "json"),
                self.command_timeout,
            )
        except Exception:
            raise ValidationFailure("matrix") from None
        if result.returncode != 0:
            raise ValidationFailure("matrix")
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise ValidationFailure("matrix") from None
        if not isinstance(payload, dict):
            raise ValidationFailure("matrix")
        return payload

    def _matrix_identity(self, app: str, host: str) -> MatrixIdentity:
        appliance = self._json("matrix", self.namespace, "coriolisappliance", app)
        uid = self._mapping(appliance.get("metadata")).get("uid")
        if not isinstance(uid, str) or not uid:
            raise ValidationFailure("matrix")
        logging = self._matrix_secret(f"{app}-logging-credentials")
        coriolis = self._matrix_secret(f"{app}-coriolis-credentials")
        read_password = self._secret_field(logging, "read_password")
        write_password = self._secret_field(logging, "write_password")
        keystone_password = self._secret_field(coriolis, "coriolis_keystone_password")
        body = json.dumps(
            {
                "auth": {
                    "identity": {
                        "methods": ["password"],
                        "password": {
                            "user": {
                                "name": "coriolis",
                                "domain": {"name": "Default"},
                                "password": keystone_password,
                            }
                        },
                    },
                    "scope": {
                        "project": {"name": "service", "domain": {"name": "Default"}}
                    },
                }
            },
            separators=(",", ":"),
        ).encode()
        response = self._http(
            "POST",
            f"https://{host}/identity/auth/tokens",
            {"Content-Type": "application/json", "Accept": "application/json"},
            body,
        )
        token = response.headers.get("X-Subject-Token") or response.headers.get(
            "x-subject-token"
        )
        if response.status != 201 or not isinstance(token, str) or not token:
            raise ValidationFailure("matrix")
        self.registry.register_runtime_secret(token)
        return MatrixIdentity(
            app, host, uid, read_password, write_password, keystone_password, token
        )

    def _http(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> HttpResponse:
        try:
            response = self.http(method, url, headers, body, self.command_timeout)
            self.registry.audit(response.headers)
            self.registry.audit(response.body)
        except ValidationFailure:
            raise
        except Exception:
            raise ValidationFailure("matrix") from None
        return response

    def _certificate_ready(self, name: str) -> bool:
        payload = self._json("matrix", self.namespace, "certificate", name)
        conditions = self._mapping(payload.get("status")).get("conditions")
        return isinstance(conditions, list) and any(
            isinstance(condition, Mapping)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            for condition in conditions
        )

    def _tls_fixture_secret_exists(self) -> bool:
        result = self._checked(
            "tls-fixture",
            self._kubectl(
                self.namespace,
                "get",
                "secret",
                TLS_FIXTURE_NAME,
                "-o",
                "jsonpath={.metadata.name}",
            ),
        )
        return result.stdout.strip() == TLS_FIXTURE_NAME

    def _diagnose_tls_fixture_failure(self) -> str:
        """Classify the fixture certificate wait failure before evidence is gone.

        Reads are namespace-scoped cert-manager evidence plus the referenced
        ClusterIssuer; any read problem preserves a secret leak and otherwise
        falls back to the conservative pending category.
        """
        try:
            certificate = self._json(
                "tls-fixture-certificate",
                self.namespace,
                "certificate",
                TLS_FIXTURE_NAME,
            )
            certificate_requests = self._resource_items(
                self._json(
                    "tls-fixture-certificate",
                    self.namespace,
                    "certificaterequest",
                )
            )
            orders = self._resource_items(
                self._json("tls-fixture-certificate", self.namespace, "order")
            )
            challenges = self._resource_items(
                self._json("tls-fixture-certificate", self.namespace, "challenge")
            )
            cluster_issuer = self._cluster_json(
                "tls-fixture-certificate",
                "clusterissuer",
                "letsencrypt",
            )
        except ValidationFailure as error:
            if error.stage == "secret-leak":
                raise
            return _TLS_FIXTURE_PENDING
        except Exception:
            return _TLS_FIXTURE_PENDING
        return _classify_tls_fixture_failure(
            certificate, certificate_requests, orders, challenges, cluster_issuer
        )

    @staticmethod
    def _headers(response: HttpResponse) -> dict[str, str]:
        return {key.lower(): value for key, value in response.headers.items()}

    def _logs_url(self, path: str, query: Mapping[str, str] | None = None) -> str:
        suffix = f"?{urllib.parse.urlencode(query)}" if query else ""
        return f"https://{path}{suffix}"

    def _list_logs(
        self, identity: MatrixIdentity, headers: Mapping[str, str]
    ) -> HttpResponse:
        return self._http("GET", self._logs_url(f"{identity.host}/logs"), headers)

    def _redirect_contract(self, host: str) -> None:
        try:
            response = self.redirect_http(f"http://{host}/logs", self.command_timeout)
            self.registry.audit(response.headers)
            self.registry.audit(response.body)
        except ValidationFailure:
            raise
        except Exception:
            raise ValidationFailure("matrix") from None
        location = self._headers(response).get("location", "")
        if response.status != 308 or not location.startswith(f"https://{host}/"):
            raise ValidationFailure("matrix")

    def _public_contract(
        self, identity: MatrixIdentity, download_bounds: Mapping[str, str]
    ) -> None:
        if self._list_logs(identity, {}).status != 401:
            raise ValidationFailure("matrix")
        header = {"X-Auth-Token": identity.token}
        listed = self._list_logs(identity, header)
        if listed.status != 200:
            raise ValidationFailure("matrix")
        try:
            logs = json.loads(listed.body).get("logs")
            names = [item.get("log_name") for item in logs if isinstance(item, Mapping)]
        except (AttributeError, TypeError, json.JSONDecodeError):
            raise ValidationFailure("matrix") from None
        if (
            not names
            or any(not isinstance(name, str) for name in names)
            or names != sorted(names)
            or "coriolis-api" not in names
        ):
            raise ValidationFailure("matrix")
        query = {"auth_type": "keystone", "auth_token": identity.token}
        if (
            self._http("GET", self._logs_url(f"{identity.host}/logs", query), {}).status
            != 200
        ):
            raise ValidationFailure("matrix")
        invalid = {"X-Auth-Token": "invalid-token"}
        if (
            self._http(
                "GET", self._logs_url(f"{identity.host}/logs", query), invalid
            ).status
            != 401
        ):
            raise ValidationFailure("matrix")
        self._audit_ingress_controller_logs()
        if (
            self._http(
                "GET", self._logs_url(f"{identity.host}/logs/unknown-component"), header
            ).status
            != 404
        ):
            raise ValidationFailure("matrix")
        overlong = {"start_date": "0", "end_date": "9999999999"}
        if (
            self._http(
                "GET",
                self._logs_url(f"{identity.host}/logs/coriolis-api", overlong),
                header,
            ).status
            != 400
        ):
            raise ValidationFailure("matrix")
        streamed = self._http(
            "GET",
            self._logs_url(
                f"{identity.host}/logs/coriolis-api",
                {**download_bounds, "disable_chunked": "false"},
            ),
            header,
        )
        whole = self._http(
            "GET",
            self._logs_url(
                f"{identity.host}/logs/coriolis-api",
                {**download_bounds, "disable_chunked": "true"},
            ),
            header,
        )
        if (
            streamed.status != 200
            or whole.status != 200
            or not streamed.body
            or streamed.body != whole.body
        ):
            raise ValidationFailure("matrix")
        stream_headers = self._headers(streamed)
        whole_headers = self._headers(whole)
        disposition = whole_headers.get("content-disposition", "")
        if (
            stream_headers.get("cache-control") != "no-store"
            or whole_headers.get("cache-control") != "no-store"
            or not disposition.startswith("attachment;")
            or "coriolis-api" not in disposition
            or "content-length" in stream_headers
            or whole_headers.get("content-length") != str(len(whole.body))
        ):
            raise ValidationFailure("matrix")

    @staticmethod
    def _basic(tenant: str, password: str) -> str:
        return base64.b64encode(f"{tenant}:{password}".encode()).decode("ascii")

    def _gateway(
        self,
        base_url: str,
        method: str,
        path: str,
        tenant: str,
        password: str,
        body: bytes | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        headers = {
            "Authorization": f"Basic {self._basic(tenant, password)}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        return self._http(method, f"{base_url}{path}", headers, body)

    def _push_marker(
        self, base_url: str, identity: MatrixIdentity, marker: str
    ) -> None:
        payload = {
            "streams": [
                {
                    "stream": {
                        "namespace": self.namespace,
                        "coriolis_appliance": identity.app,
                        "coriolis_component": "coriolis-api",
                        "pod": f"{identity.app}-matrix-coriolis-api",
                        "container": "coriolis-api",
                        "severity": "INFO",
                        "matrix_marker": marker,
                    },
                    "values": [[str(time.time_ns()), f"matrix-{marker}"]],
                }
            ]
        }
        response = self._gateway(
            base_url,
            "POST",
            "/loki/api/v1/push",
            f"coriolis-{identity.uid}",
            identity.write_password,
            json.dumps(payload, separators=(",", ":")).encode(),
        )
        if response.status != 204:
            raise ValidationFailure("matrix")

    def _marker_count(
        self,
        base_url: str,
        identity: MatrixIdentity,
        marker: str,
        spoof_tenant: str | None = None,
    ) -> int:
        query = urllib.parse.urlencode({"query": f'{{matrix_marker="{marker}"}}'})
        headers = {"X-Scope-OrgID": spoof_tenant} if spoof_tenant else None
        response = self._gateway(
            base_url,
            "GET",
            f"/loki/api/v1/query_range?{query}",
            f"coriolis-{identity.uid}",
            identity.read_password,
            extra_headers=headers,
        )
        if response.status != 200:
            raise ValidationFailure("matrix")
        try:
            streams = json.loads(response.body)["data"]["result"]
            return sum(
                len(item.get("values", []))
                for item in streams
                if isinstance(item, Mapping)
            )
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValidationFailure("matrix") from None

    def _gateway_isolation(
        self, identities: Sequence[MatrixIdentity]
    ) -> tuple[str, str]:
        first, second = identities
        markers = (self.marker_factory(), self.marker_factory())
        if (
            not all(isinstance(marker, str) and marker for marker in markers)
            or markers[0] == markers[1]
        ):
            raise ValidationFailure("matrix")
        for identity, own, foreign, other in (
            (first, markers[0], markers[1], second),
            (second, markers[1], markers[0], first),
        ):
            command = self._kubectl(
                self.namespace, "port-forward", f"svc/{identity.app}-gateway"
            )
            with self.forward(
                command, self.command_timeout, self.clock, self.sleeper
            ) as base_url:
                self._push_marker(base_url, identity, own)
                self._wait_marker_visible(base_url, identity, own)
                if self._marker_count(base_url, identity, foreign) != 0:
                    raise ValidationFailure("matrix")
                cross = self._gateway(
                    base_url,
                    "GET",
                    "/loki/api/v1/query_range?query=%7B%7D",
                    f"coriolis-{other.uid}",
                    other.read_password,
                )
                if cross.status != 401:
                    raise ValidationFailure("matrix")
                if (
                    self._marker_count(base_url, identity, own, f"coriolis-{other.uid}")
                    != 1
                    or self._marker_count(
                        base_url, identity, foreign, f"coriolis-{other.uid}"
                    )
                    != 0
                ):
                    raise ValidationFailure("matrix")
        return markers

    def _wait_marker_visible(
        self, base_url: str, identity: MatrixIdentity, marker: str
    ) -> None:
        deadline = self.clock() + self.setup_timeout
        attempts = 0
        while attempts < 100:
            attempts += 1
            count = self._marker_count(base_url, identity, marker)
            if count == 1:
                return
            if count > 1 or self.clock() >= deadline:
                raise ValidationFailure("matrix")
            self.sleeper(POLL_SECONDS)
        raise ValidationFailure("matrix")

    def _public_markers(
        self,
        identities: Sequence[MatrixIdentity],
        markers: Sequence[str],
        download_bounds: Mapping[str, str],
    ) -> bool:
        for identity, own, foreign in (
            (identities[0], markers[0], markers[1]),
            (identities[1], markers[1], markers[0]),
        ):
            response = self._http(
                "GET",
                self._logs_url(
                    f"{identity.host}/logs/coriolis-api",
                    {**download_bounds, "disable_chunked": "true"},
                ),
                {"X-Auth-Token": identity.token},
            )
            text = response.body.decode("utf-8", "replace")
            if (
                response.status != 200
                or text.count(f"matrix-{own}") != 1
                or f"matrix-{foreign}" in text
            ):
                return False
        return True

    def _expect_ws(self, ws: object, marker: str) -> None:
        expected = f"matrix-{marker}"
        seen = False
        deadline = self.clock() + self.command_timeout
        duplicate_deadline: float | None = None
        attempts = 0
        while self.clock() < (duplicate_deadline or deadline) and attempts < 100:
            attempts += 1
            try:
                frame = getattr(ws, "recv")()
            except Exception:
                if (
                    getattr(ws, "closed", False) is True
                    or getattr(ws, "connected", True) is False
                ):
                    raise ValidationFailure("matrix") from None
                if seen:
                    return
                raise ValidationFailure("matrix") from None
            if frame is None:
                if (
                    getattr(ws, "closed", False) is True
                    or getattr(ws, "connected", True) is False
                ):
                    raise ValidationFailure("matrix")
                if seen:
                    return
                self.sleeper(0.05)
                continue
            try:
                self.registry.audit(frame)
                message = json.loads(frame).get("message")
            except (TypeError, json.JSONDecodeError):
                raise ValidationFailure("matrix") from None
            if not isinstance(message, str):
                continue
            if message == expected:
                if seen:
                    raise ValidationFailure("matrix")
                seen = True
                setter = getattr(ws, "settimeout", None)
                if callable(setter):
                    try:
                        setter(0.2)
                    except Exception:
                        raise ValidationFailure("matrix") from None
                duplicate_deadline = self.clock() + 0.2
                continue
            if message.startswith("matrix-"):
                raise ValidationFailure("matrix")
        if (
            not seen
            or getattr(ws, "closed", False) is True
            or getattr(ws, "connected", True) is False
        ):
            raise ValidationFailure("matrix")

    def _client_reconnect(self, identity: MatrixIdentity) -> None:
        command = self._kubectl(
            self.namespace, "port-forward", f"svc/{identity.app}-gateway"
        )
        with self.forward(
            command, self.command_timeout, self.clock, self.sleeper
        ) as base_url:
            clients: list[object] = []
            for _ in range(2):
                marker = self.marker_factory()
                if not isinstance(marker, str) or not marker:
                    raise ValidationFailure("matrix")
                url = self._logs_url(
                    f"{identity.host}/log-stream",
                    {"app_name": "coriolis-api", "severity": "6"},
                ).replace("https://", "wss://", 1)
                ws = self.ws_factory(
                    url, {"X-Auth-Token": identity.token}, self.command_timeout
                )
                clients.append(ws)
                try:
                    self._push_marker(base_url, identity, marker)
                    self._expect_ws(ws, marker)
                finally:
                    try:
                        getattr(ws, "close")()
                    except Exception:
                        pass
            if clients[0] is clients[1]:
                raise ValidationFailure("matrix")

    def _audit_matrix_surfaces(self) -> None:
        for kind in (
            "coriolisappliances",
            "deployments",
            "statefulsets",
            "pods",
            "services",
            "ingresses",
            "configmaps",
            "events",
            "certificates",
        ):
            self._json("matrix", self.namespace, kind)
        pods = self._json("matrix", self.namespace, "pods").get("items")
        self._audit_pod_logs(self.namespace, pods, 200)
        self._audit_ingress_controller_logs()

    def _audit_ingress_controller_logs(self) -> None:
        pods = self._json(
            "matrix",
            "ingress-nginx",
            "pods",
            "-l",
            "app.kubernetes.io/component=controller",
        ).get("items")
        if not isinstance(pods, list) or not pods:
            raise ValidationFailure("matrix")
        self._audit_pod_logs("ingress-nginx", pods, 5000)

    def _audit_pod_logs(self, namespace: str, pods: object, tail: int) -> None:
        if not isinstance(pods, list):
            raise ValidationFailure("matrix")
        for pod in pods:
            if not isinstance(pod, Mapping):
                raise ValidationFailure("matrix")
            name = self._mapping(pod.get("metadata")).get("name")
            spec = self._mapping(pod.get("spec"))
            main_containers = spec.get("containers", [])
            init_containers = spec.get("initContainers", [])
            if (
                not isinstance(name, str)
                or not isinstance(main_containers, list)
                or not isinstance(init_containers, list)
            ):
                raise ValidationFailure("matrix")
            containers = main_containers + init_containers
            if not all(isinstance(item, Mapping) for item in containers):
                raise ValidationFailure("matrix")
            for container in containers:
                container_name = container.get("name")
                if not isinstance(container_name, str) or not container_name:
                    raise ValidationFailure("matrix")
                self._checked(
                    "matrix",
                    self._kubectl(
                        namespace,
                        "logs",
                        name,
                        "-c",
                        container_name,
                        f"--tail={tail}",
                    ),
                )

    def _matrix_stage(self, stage: str, action: Callable[[], None]) -> None:
        try:
            action()
        except ValidationFailure as error:
            if error.stage == "secret-leak":
                raise
            if error.stage == "matrix":
                raise ValidationFailure(stage) from None
            raise
        self._report("PASS", stage)

    def _matrix(self) -> None:
        identity_a: MatrixIdentity | None = None
        identity_b: MatrixIdentity | None = None
        markers: tuple[str, str] | None = None

        self._matrix_stage(
            "matrix-certificate-a",
            lambda: self._wait(
                "matrix",
                self.setup_timeout,
                lambda: self._certificate_ready(TLS_FIXTURE_NAME),
            ),
        )
        self._matrix_stage(
            "matrix-redirect-a", lambda: self._redirect_contract(self.host_a)
        )

        def read_identity_a() -> None:
            nonlocal identity_a
            identity_a = self._matrix_identity(self.app_a, self.host_a)

        self._matrix_stage("matrix-identity-a", read_identity_a)
        self._matrix_stage(
            "matrix-certificate-b",
            lambda: self._wait(
                "matrix",
                self.setup_timeout,
                lambda: self._certificate_ready(TLS_FIXTURE_NAME),
            ),
        )
        self._matrix_stage(
            "matrix-redirect-b", lambda: self._redirect_contract(self.host_b)
        )

        def read_identity_b() -> None:
            nonlocal identity_b
            identity_b = self._matrix_identity(self.app_b, self.host_b)

        self._matrix_stage("matrix-identity-b", read_identity_b)
        if identity_a is None or identity_b is None:
            raise ValidationFailure("matrix-identity")
        identities = (identity_a, identity_b)

        def isolation() -> None:
            nonlocal markers
            markers = self._gateway_isolation(identities)

        self._matrix_stage("matrix-isolation", isolation)
        if markers is None:
            raise ValidationFailure("matrix-isolation")
        end = int(self.wallclock()) + 60
        download_bounds = {"start_date": str(end - 600), "end_date": str(end)}
        self._matrix_stage(
            "matrix-public-markers",
            lambda: self._wait(
                "matrix",
                self.setup_timeout,
                lambda: self._public_markers(identities, markers, download_bounds),
            ),
        )
        self._matrix_stage(
            "matrix-public-a",
            lambda: self._public_contract(identities[0], download_bounds),
        )
        self._matrix_stage(
            "matrix-wss-a", lambda: self._client_reconnect(identities[0])
        )
        self._matrix_stage(
            "matrix-public-b",
            lambda: self._public_contract(identities[1], download_bounds),
        )
        self._matrix_stage(
            "matrix-wss-b", lambda: self._client_reconnect(identities[1])
        )
        self._matrix_stage("matrix-audit", self._audit_matrix_surfaces)

    def _run_child(
        self,
        stage: str,
        command: Sequence[str],
        timeout: int,
        valid: Callable[[list[str]], bool],
    ) -> None:
        try:
            result = self.child_runner(command, timeout)
            self.registry.audit(result.stdout)
            self.registry.audit(result.stderr)
        except ValidationFailure:
            raise
        except Exception:
            raise ValidationFailure(stage) from None
        lines = result.stdout.splitlines()
        if result.returncode != 0 or result.stderr or not valid(lines):
            child_stage = ""
            if result.returncode != 0 and not result.stderr and lines:
                failures = [
                    match
                    for line in lines
                    if (match := _CHILD_FAILURE.fullmatch(line)) is not None
                ]
                if len(failures) == 1:
                    child_stage = failures[0].group(1)
            if child_stage and _STAGE.fullmatch(f"{stage}-{child_stage}"):
                raise ValidationFailure(f"{stage}-{child_stage}")
            raise ValidationFailure(stage)

    def _logging_child_output(self, lines: list[str]) -> bool:
        return tuple(lines) == _LOGGING_CHILD_OUTPUT

    def _retention_child_output(self, lines: list[str]) -> bool:
        if not lines or _RETENTION_SUMMARY.fullmatch(lines[-1]) is None:
            return False
        seen_stages: set[str] = set()
        candidate_count = False
        marker_count = False
        observer_cleanup = False
        for line in lines[:-1]:
            stage = _RETENTION_STAGE.fullmatch(line)
            if stage is not None and stage.group(1) in _RETENTION_STAGES:
                seen_stages.add(stage.group(1))
                continue
            if re.fullmatch(r"PASS candidate-count \d+", line):
                candidate_count = True
                continue
            if re.fullmatch(r"PASS deletion-marker-count \d+", line):
                marker_count = True
                continue
            if line == "CLEANUP observer" and not observer_cleanup:
                observer_cleanup = True
                continue
            return False
        return (
            _REQUIRED_RETENTION_STAGES <= seen_stages
            and candidate_count
            and marker_count
        )

    def _delete_and_wait(
        self, stage: str, namespace: str, kind: str, name: str
    ) -> None:
        self._checked(
            stage,
            self._kubectl(
                namespace,
                "delete",
                kind,
                name,
                "--ignore-not-found=true",
                "--wait=false",
            ),
        )

        def absent() -> bool:
            result = self.runner(
                self._kubectl(namespace, "get", kind, name, "--ignore-not-found"),
                self.command_timeout,
            )
            self.registry.audit(result.stdout)
            self.registry.audit(result.stderr)
            return result.returncode == 0 and not result.stdout.strip()

        self._wait(stage, self.cleanup_timeout, absent)

    def _pv_absent(self, name: str) -> bool:
        result = self.runner(
            self._cluster_kubectl("get", "pv", name, "--ignore-not-found"),
            self.command_timeout,
        )
        self.registry.audit(result.stdout)
        self.registry.audit(result.stderr)
        return result.returncode == 0 and not result.stdout.strip()

    def _hosts_absent(self) -> bool:
        for kind in ("ingress", "certificate"):
            payload = self._cluster_json("cleanup-hosts", kind, "--all-namespaces")
            for item in payload.get("items", []):
                if not isinstance(item, Mapping):
                    continue
                spec = self._mapping(item.get("spec"))
                hosts = {spec.get("commonName")}
                hosts.update(
                    spec.get("dnsNames", [])
                    if isinstance(spec.get("dnsNames"), list)
                    else []
                )
                for rule in (
                    spec.get("rules", []) if isinstance(spec.get("rules"), list) else []
                ):
                    if isinstance(rule, Mapping):
                        hosts.add(rule.get("host"))
                if self.host_a in hosts or self.host_b in hosts:
                    return False
        return True

    def _shared_ready(self) -> bool:
        app = self._json("shared-ready", ARGO_NAMESPACE, "application", "coriolis")
        status = self._mapping(app.get("status"))
        sync = self._mapping(status.get("sync"))
        health = self._mapping(status.get("health"))
        if sync.get("status") != "Synced" or health.get("status") != "Healthy":
            return False
        deployment = self._json(
            "shared-ready", SOURCE_NAMESPACE, "deployment", OPERATOR_NAME
        )
        deployment_status = self._mapping(deployment.get("status"))
        ready = deployment_status.get("readyReplicas")
        return (
            isinstance(ready, int)
            and ready > 0
            and deployment_status.get("availableReplicas") == ready
        )

    def _cleanup(self) -> None:
        if not self.namespace_created:
            return
        self._delete_and_wait(
            "cleanup-app-b", self.namespace, "coriolisappliance", self.app_b
        )
        self._delete_and_wait(
            "cleanup-app-a", self.namespace, "coriolisappliance", self.app_a
        )
        self._delete_and_wait(
            "cleanup-application", ARGO_NAMESPACE, "application", self.application
        )
        self._delete_and_wait(
            "cleanup-namespace", self.namespace, "namespace", self.namespace
        )
        for pv in sorted(self.recorded_pvs):
            self._wait(
                "cleanup-pv", self.cleanup_timeout, lambda pv=pv: self._pv_absent(pv)
            )
        self._wait("cleanup-hosts", self.cleanup_timeout, self._hosts_absent)
        self._wait("shared-ready", self.cleanup_timeout, self._shared_ready)
        self._report("PASS", "cleanup")

    def _run_body(self) -> None:
        self._preflight()
        self._create_namespace()
        self._report("PASS", "namespace")
        self._copy_secret("regcred")
        self._copy_secret("coriolis-appliance-registry")
        self._report("PASS", "secrets")
        self._apply(
            "tls-fixture-apply",
            self.namespace,
            tls_fixture_manifest(self.host_a, self.host_b),
        )
        try:
            self._wait(
                "tls-fixture-certificate",
                self.setup_timeout,
                lambda: self._certificate_ready(TLS_FIXTURE_NAME),
            )
        except ValidationFailure as error:
            if error.stage != "tls-fixture-certificate":
                raise
            raise ValidationFailure(self._diagnose_tls_fixture_failure()) from None
        self._wait(
            "tls-fixture-secret", self.setup_timeout, self._tls_fixture_secret_exists
        )
        self._report("PASS", "tls-fixture")
        self._apply(
            "application-apply",
            ARGO_NAMESPACE,
            application_manifest(self.application, self.namespace),
        )
        self._wait("application-ready", self.setup_timeout, self._application_ready)
        self._wait("operator-ready", self.setup_timeout, self._deployment_ready)
        self._wait("operator-digest", self.setup_timeout, self._operator_digest_ready)
        self._report("PASS", "operator")
        self._apply(
            "app-a-apply",
            self.namespace,
            appliance_manifest(self.app_a, self.host_a, TLS_FIXTURE_NAME),
        )
        self.created_apps.append(self.app_a)
        self._wait(
            "app-a-ready", self.setup_timeout, lambda: self._appliance_ready(self.app_a)
        )
        self._wait("app-a-workloads", self.setup_timeout, self._workloads_ready)
        self._record_pvs()
        self._report("PASS", "app-a")
        self._apply(
            "app-b-apply",
            self.namespace,
            appliance_manifest(self.app_b, self.host_b, TLS_FIXTURE_NAME),
        )
        self.created_apps.append(self.app_b)
        self._wait(
            "app-b-ready", self.setup_timeout, lambda: self._appliance_ready(self.app_b)
        )
        self._wait("app-b-workloads", self.setup_timeout, self._workloads_ready)
        self._record_pvs()
        self._report("PASS", "app-b")
        self._matrix()
        scripts = Path(__file__).resolve().parent
        self._run_child(
            "reconnect",
            [
                sys.executable,
                str(scripts / "validate-coriolis-logging-runtime.py"),
                "--context",
                self.context,
                "--namespace",
                self.namespace,
                "--app",
                self.app_a,
                "--host",
                self.host_a,
                "--run",
            ],
            self.reconnect_timeout,
            self._logging_child_output,
        )
        self._report("PASS", "reconnect")
        if self.mode == "formal":
            self._run_child(
                "retention",
                [
                    sys.executable,
                    str(scripts / "validate-coriolis-retention-runtime.py"),
                    "--context",
                    self.context,
                    "--namespace",
                    self.namespace,
                    "--app-name",
                    self.app_a,
                    "--mode",
                    "formal",
                    "--timeout",
                    str(RETENTION_CHILD_COMMAND_TIMEOUT),
                    "--max-wait-minutes",
                    str(RETENTION_CHILD_MAX_WAIT_MINUTES),
                    "--run",
                ],
                self.retention_timeout,
                self._retention_child_output,
            )
            self._report("PASS", "retention")
            self._record_pvs()
        self._audit_matrix_surfaces()
        self._report("PASS", "final-audit")

    def run(self) -> int:
        started = self.clock()
        failure: ValidationFailure | None = None
        try:
            self._run_body()
        except ValidationFailure as error:
            failure = error
        except BaseException:
            failure = ValidationFailure("internal")
        finally:
            try:
                self._cleanup()
            except ValidationFailure as error:
                failure = error
            except BaseException:
                failure = ValidationFailure("cleanup")
        if failure is not None:
            self._report("FAIL", failure.stage)
            return 1
        self._summary(self.clock() - started)
        return 0


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ValidationFailure("cli")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SilentParser(add_help=False)
    for argument in (
        "--context",
        "--namespace",
        "--application",
        "--app-a",
        "--host-a",
        "--app-b",
        "--host-b",
    ):
        parser.add_argument(argument, required=True)
    parser.add_argument("--mode", choices=("fast", "formal"), required=True)
    parser.add_argument("--run", action="store_true")
    try:
        args = parser.parse_args(argv)
        values = (
            args.context,
            args.namespace,
            args.application,
            args.app_a,
            args.host_a,
            args.app_b,
            args.host_b,
        )
        if not args.run or not all(
            isinstance(value, str) and value for value in values
        ):
            raise ValidationFailure("cli")
        if args.app_a == args.app_b or args.host_a == args.host_b:
            raise ValidationFailure("cli")
        values = vars(args)
        del values["run"]
        return Validator(**values).run()
    except Exception:
        print("FAIL cli")
        return 2


if __name__ == "__main__":
    sys.exit(main())
