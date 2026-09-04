#!/usr/bin/env python3
"""Value-silent released-appliance Barbican runtime validation.

Validates one existing released CoriolisAppliance's Barbican runtime without
creating or deleting any Kubernetes resource and without printing any
credential, token, secret reference, response body, or command stderr.
Every stage prints exactly ``PASS <stage>`` or ``FAIL <stage>`` and the run
ends with ``SUMMARY barbican passed|failed <seconds>``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

BARBICAN_API_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/barbican-api:2023.1-ubuntu-jammy"
    "@sha256:a142a57761f708b241358383d6445ac5da4e05ae26a284369081cfb15cca8a60"
)
BARBICAN_WORKER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/barbican-worker:2023.1-ubuntu-jammy"
    "@sha256:ed907de778900b08f2645c9eeb82d48d8202ce6517cdb543d42db2e88ea642b5"
)
API_COMPONENT = "barbican-api"
WORKER_COMPONENT = "barbican-worker"
BARBICAN_PORT = 9311
BARBICAN_RUN_AS_ID = 42403
BARBICAN_SUPPLEMENTAL_GROUP = 42400
BARBICAN_TERMINATION_GRACE_PERIOD_SECONDS = 30
BARBICAN_RUNTIME_DIR = "/etc/barbican-runtime"
BARBICAN_TMP_DIR = "/tmp"
BARBICAN_API_STATE_DIR = "/var/lib/barbican"
BARBICAN_VASSALS_DIR = f"{BARBICAN_RUNTIME_DIR}/vassals"
BARBICAN_CONFIG_PATH = f"{BARBICAN_RUNTIME_DIR}/barbican.conf"
BARBICAN_DB_SYNC_PATH = f"{BARBICAN_RUNTIME_DIR}/db-sync.py"
BARBICAN_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
BARBICAN_API_COMMAND = [
    "/usr/bin/dumb-init",
    "--single-child",
    "--",
    "/var/lib/kolla/venv/bin/uwsgi",
    "--master",
    "--emperor",
    BARBICAN_VASSALS_DIR,
]
BARBICAN_WORKER_COMMAND = [
    "/usr/bin/dumb-init",
    "--single-child",
    "--",
    "/var/lib/kolla/venv/bin/barbican-worker",
    "--config-file",
    BARBICAN_CONFIG_PATH,
    "--nouse-syslog",
    "--log-dir=",
]
BARBICAN_DB_SYNC_COMMAND = [
    "/var/lib/kolla/venv/bin/python3",
    BARBICAN_DB_SYNC_PATH,
]
BARBICAN_HEALTH_PROBE = (
    "import http.client,sys; "
    "connection=http.client.HTTPConnection('127.0.0.1',9311,timeout=5); "
    "connection.request('GET','/healthcheck'); "
    "response=connection.getresponse(); "
    "sys.exit(0 if response.status == 200 else 1)"
)
BARBICAN_PROBE_COMMAND = [BARBICAN_DB_SYNC_COMMAND[0], "-c", BARBICAN_HEALTH_PROBE]
BARBICAN_PROBE_EXEC = {"command": BARBICAN_PROBE_COMMAND}
BARBICAN_API_STARTUP_PROBE = {
    "exec": BARBICAN_PROBE_EXEC,
    "periodSeconds": 2,
    "timeoutSeconds": 5,
    "failureThreshold": 30,
}
BARBICAN_API_READINESS_PROBE = {
    "exec": BARBICAN_PROBE_EXEC,
    "periodSeconds": 5,
    "timeoutSeconds": 5,
    "failureThreshold": 3,
    "successThreshold": 1,
}
BARBICAN_API_LIVENESS_PROBE = {
    "exec": BARBICAN_PROBE_EXEC,
    "periodSeconds": 10,
    "timeoutSeconds": 5,
    "failureThreshold": 6,
}
BARBICAN_API_PROBE_NAMES = ("startupProbe", "readinessProbe", "livenessProbe")
CONTAINER_SECURITY_CONTEXT = {
    "runAsNonRoot": True,
    "readOnlyRootFilesystem": True,
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]},
    "seccompProfile": {"type": "RuntimeDefault"},
}
CONFIG_KEYS = (
    "barbican-api-paste.ini",
    "barbican-api.ini",
    "policy.yaml",
    "db-sync.py",
)
BARBICAN_VASSAL_KEY = "barbican-api.ini"
BARBICAN_VASSAL_ITEM_PATH = "vassals/barbican-api.ini"
RETENTION_ANNOTATION = "coriolis.cloudbase.it/retention"
RETENTION_STATE_CREDENTIALS = "state-credentials"
CATALOG_INTERFACES = frozenset({"admin", "internal", "public"})
CRYPTO_KEY_BYTES = 32
CREDENTIAL_KEYS = frozenset(
    {
        "barbican_database_password",
        "barbican_keystone_password",
        "barbican_crypto_key",
    }
)
CONFIG_SECRET_KEY = "barbican.conf"
INGRESS_PATH = "/barbican(/|$)(.*)"
INGRESS_REWRITE_TARGET = "/$2"
USE_REGEX_ANNOTATION = "nginx.ingress.kubernetes.io/use-regex"
REWRITE_TARGET_ANNOTATION = "nginx.ingress.kubernetes.io/rewrite-target"
VALIDATOR_SECRET_NAME = "coriolis-barbican-runtime-validator"
VALIDATOR_SECRET_PAYLOAD = "coriolis-runtime-validator-text"
API_ROOT_PATH = "/barbican/v1/"
AUTH_PATH = "/identity/auth/tokens"
SECRETS_PATH = "/barbican/v1/secrets"
COMPONENT_LABEL = "coriolis.cloudbase.it/component"
APPLIANCE_LABEL = "coriolis.cloudbase.it/appliance"
DEFAULT_TIMEOUT = 30
MIN_TIMEOUT = 1
MAX_TIMEOUT = 300
DEFAULT_POLL_INTERVAL = 2.0
MIN_POLL_INTERVAL = 0.1
MAX_POLL_INTERVAL = 60.0
DEFAULT_DELETE_STATUSES = frozenset({"PENDING_DELETE", "DELETED"})
DNS_SUBDOMAIN_MAX_LENGTH = 253
DNS_LABEL_MAX_LENGTH = 63
NAME_HASH_LENGTH = 12
_STAGE = re.compile(r"^[a-z0-9-]+$")
_HOST = re.compile(
    r"^(?=.*\.)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)
_APP = re.compile(
    r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*"
)
_SECRET_ID = re.compile(r"^[0-9a-fA-F-]+$")


def _appliance_identity(appliance_name: str) -> str:
    """Reproduce the operator's label-safe appliance identity exactly."""
    if "." not in appliance_name and len(appliance_name) <= DNS_LABEL_MAX_LENGTH:
        return appliance_name
    name_hash = hashlib.sha256(appliance_name.encode()).hexdigest()[:NAME_HASH_LENGTH]
    suffix = f"-{name_hash}"
    prefix = appliance_name.replace(".", "-")[: DNS_LABEL_MAX_LENGTH - len(suffix)]
    return f"{prefix.rstrip('-')}{suffix}"


def _appliance_resource_name(appliance_name: str, component: str) -> str:
    """Reproduce the operator's label-safe component resource name exactly."""
    desired_name = f"{appliance_name}-{component}"
    if "." not in appliance_name and len(desired_name) <= DNS_LABEL_MAX_LENGTH:
        return desired_name
    name_hash = hashlib.sha256(desired_name.encode()).hexdigest()[:NAME_HASH_LENGTH]
    suffix = f"-{name_hash}-{component}"
    prefix = appliance_name.replace(".", "-")[: DNS_LABEL_MAX_LENGTH - len(suffix)]
    return f"{prefix.rstrip('-')}{suffix}"


CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]
Reporter = Callable[[str], None]


class ValidationFailure(Exception):
    """A stable, non-sensitive failure stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


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
    """Perform one TLS-verified public request; no insecure mode exists."""
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
            error_body = error.read()
        except OSError:
            error_body = b""
        return HttpResponse(error.code, dict(error.headers.items()), error_body)
    except (OSError, urllib.error.URLError):
        raise ValidationFailure("http") from None


def _last_path_segment(value: str, stage: str) -> str:
    path = urllib.parse.urlparse(value).path
    segment = path.rsplit("/", 1)[-1]
    if _SECRET_ID.fullmatch(segment) is None:
        raise ValidationFailure(stage)
    return segment


class Validator:
    def __init__(
        self,
        *,
        context: str,
        namespace: str,
        app: str,
        host: str,
        timeout: int = DEFAULT_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        runner: CommandRunner = _run,
        http: Callable[
            [str, str, Mapping[str, str], bytes | None, int], HttpResponse
        ] = _http,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        report: Reporter = print,
    ) -> None:
        self.context = context
        self.namespace = namespace
        self.app = app
        self.identity = _appliance_identity(app)
        self.host = host
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.runner = runner
        self.http = http
        self.clock = clock
        self.sleeper = sleeper
        self._raw_report = report
        self.registry = CredentialRegistry()
        self.version = ""
        self.appliance_uid = ""
        self.keystone_password = ""
        self.token = ""
        self.created_id = ""
        self.created_deleted = True
        self.baseline_ids: set[str] = set()
        self._identities: dict[str, object] = {}

    def _report(self, status: str, stage: str) -> None:
        line = f"{status} {stage}"
        if status not in {"PASS", "FAIL"} or _STAGE.fullmatch(stage) is None:
            raise ValidationFailure("report")
        self.registry.audit(line)
        self._raw_report(line)

    def _name(self, component: str) -> str:
        return _appliance_resource_name(self.app, component)

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

    def _request(
        self,
        stage: str,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpResponse:
        merged = dict(headers or {})
        try:
            return self.http(
                method, f"https://{self.host}{path}", merged, body, self.timeout
            )
        except ValidationFailure:
            raise ValidationFailure(stage) from None
        except Exception:
            raise ValidationFailure(stage) from None

    @staticmethod
    def _header(response: HttpResponse, name: str) -> str:
        for key, value in response.headers.items():
            if isinstance(key, str) and key.lower() == name.lower():
                if isinstance(value, str) and value:
                    return value
        return ""

    def _body_json(self, stage: str, response: HttpResponse) -> dict[str, object]:
        try:
            payload = json.loads(response.body)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValidationFailure(stage) from None
        if not isinstance(payload, dict):
            raise ValidationFailure(stage)
        return payload

    def _metadata(
        self, stage: str, payload: Mapping[str, object], name: str
    ) -> dict[str, object]:
        metadata = payload.get("metadata")
        if (
            not isinstance(metadata, dict)
            or metadata.get("name") != name
            or metadata.get("namespace") != self.namespace
        ):
            raise ValidationFailure(stage)
        return metadata

    def _expected_labels(self, component: str) -> dict[str, str]:
        return {
            "app.kubernetes.io/name": "coriolis",
            "app.kubernetes.io/instance": self.identity,
            "app.kubernetes.io/version": self.version,
            "app.kubernetes.io/component": component,
            "app.kubernetes.io/part-of": "coriolis-appliance",
            "app.kubernetes.io/managed-by": "coriolis-operator",
            APPLIANCE_LABEL: self.identity,
            COMPONENT_LABEL: component,
        }

    def _owned(
        self, stage: str, payload: Mapping[str, object], component: str
    ) -> dict[str, object]:
        metadata = self._metadata(stage, payload, self._name(component))
        if metadata.get("labels") != self._expected_labels(component):
            raise ValidationFailure(stage)
        owners = metadata.get("ownerReferences")
        if not isinstance(owners, list) or len(owners) != 1:
            raise ValidationFailure(stage)
        owner = owners[0]
        if (
            not isinstance(owner, dict)
            or owner.get("kind") != "CoriolisAppliance"
            or owner.get("name") != self.app
            or owner.get("uid") != self.appliance_uid
            or owner.get("controller") is not True
        ):
            raise ValidationFailure(stage)
        return metadata

    @staticmethod
    def _secret_data(
        stage: str, payload: Mapping[str, object], expected: frozenset[str]
    ) -> dict[str, str]:
        if payload.get("type") != "Opaque":
            raise ValidationFailure(stage)
        data = payload.get("data")
        if not isinstance(data, dict) or set(data) != expected:
            raise ValidationFailure(stage)
        values: dict[str, str] = {}
        for key, encoded in data.items():
            if not isinstance(encoded, str):
                raise ValidationFailure(stage)
            try:
                value = base64.b64decode(encoded, validate=True).decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                raise ValidationFailure(stage) from None
            if not value:
                raise ValidationFailure(stage)
            values[key] = value
        return values

    def _appliance(self) -> None:
        payload = self._json("appliance", "coriolisappliance", self.app)
        metadata = self._metadata("appliance", payload, self.app)
        uid = metadata.get("uid")
        spec = payload.get("spec")
        status = payload.get("status")
        version = spec.get("version") if isinstance(spec, dict) else None
        if (
            not isinstance(uid, str)
            or not uid
            or not isinstance(version, str)
            or not version
        ):
            raise ValidationFailure("appliance")
        if not isinstance(status, dict) or status.get("acceptedVersion") != version:
            raise ValidationFailure("appliance")
        conditions = status.get("conditions")
        if not isinstance(conditions, list) or not any(
            isinstance(condition, dict)
            and condition.get("type") == "Ready"
            and condition.get("status") == "True"
            and condition.get("reason") == "RuntimeReady"
            for condition in conditions
        ):
            raise ValidationFailure("appliance")
        self.version = version
        self.appliance_uid = uid

    @staticmethod
    def _crypto_key_ok(key: str) -> bool:
        try:
            raw = base64.b64decode(key, altchars=b"-_", validate=True)
        except Exception:
            return False
        return (
            len(raw) == CRYPTO_KEY_BYTES
            and base64.urlsafe_b64encode(raw).decode("ascii") == key
        )

    def _credentials(self) -> None:
        name = self._name("barbican-credentials")
        payload = self._json("credentials", "secret", name)
        metadata = self._metadata("credentials", payload, name)
        if metadata.get("labels") != self._expected_labels("barbican-credentials"):
            raise ValidationFailure("credentials")
        if metadata.get("ownerReferences") not in (None, []):
            raise ValidationFailure("credentials")
        annotations = metadata.get("annotations")
        retention = (
            annotations.get(RETENTION_ANNOTATION)
            if isinstance(annotations, dict)
            else None
        )
        if retention != RETENTION_STATE_CREDENTIALS:
            raise ValidationFailure("credentials")
        values = self._secret_data("credentials", payload, CREDENTIAL_KEYS)
        if not self._crypto_key_ok(values["barbican_crypto_key"]):
            raise ValidationFailure("credentials")
        for value in values.values():
            self.registry.register(value)
        self.keystone_password = values["barbican_keystone_password"]

    def _config_map(self) -> None:
        payload = self._json("config-map", "configmap", self._name("barbican-config"))
        self._owned("config-map", payload, "barbican-config")
        data = payload.get("data")
        if not isinstance(data, dict) or set(data) != set(CONFIG_KEYS):
            raise ValidationFailure("config-map")
        for value in data.values():
            if not isinstance(value, str):
                raise ValidationFailure("config-map")
            self.registry.audit(value)

    def _config_secret(self) -> None:
        payload = self._json(
            "config-secret", "secret", self._name("barbican-config-secret")
        )
        self._owned("config-secret", payload, "barbican-config-secret")
        data = payload.get("data")
        if (
            payload.get("type") != "Opaque"
            or not isinstance(data, dict)
            or set(data) != {CONFIG_SECRET_KEY}
        ):
            raise ValidationFailure("config-secret")

    def _service(self) -> None:
        payload = self._json("service", "service", self._name(API_COMPONENT))
        self._owned("service", payload, API_COMPONENT)
        spec = payload.get("spec")
        if not isinstance(spec, dict) or spec.get("type") != "ClusterIP":
            raise ValidationFailure("service")
        if spec.get("selector") != {
            APPLIANCE_LABEL: self.identity,
            COMPONENT_LABEL: API_COMPONENT,
        }:
            raise ValidationFailure("service")
        if spec.get("ports") != [
            {
                "name": API_COMPONENT,
                "protocol": "TCP",
                "port": BARBICAN_PORT,
                "targetPort": BARBICAN_PORT,
            }
        ]:
            raise ValidationFailure("service")

    def _volumes_ok(self, volumes: object, *, with_state: bool) -> bool:
        if not isinstance(volumes, list):
            return False
        names = {"config", "tmp", "state"} if with_state else {"config", "tmp"}
        if len(volumes) != len(names):
            return False
        by_name: dict[str, dict[str, object]] = {}
        for volume in volumes:
            if not isinstance(volume, dict) or not isinstance(volume.get("name"), str):
                return False
            by_name[str(volume["name"])] = volume
        if set(by_name) != names:
            return False
        tmp = by_name["tmp"].get("emptyDir")
        if not isinstance(tmp, dict) or tmp.get("medium") != "Memory":
            return False
        if any(
            by_name[name].get("persistentVolumeClaim") is not None for name in names
        ):
            return False
        if with_state and not isinstance(by_name["state"].get("emptyDir"), dict):
            return False
        projected = by_name["config"].get("projected")
        if not isinstance(projected, dict):
            return False
        sources = projected.get("sources")
        if not isinstance(sources, list) or len(sources) != 2:
            return False
        first, second = sources
        config_map = first.get("configMap") if isinstance(first, dict) else None
        secret = second.get("secret") if isinstance(second, dict) else None
        if (
            not isinstance(config_map, dict)
            or config_map.get("name") != self._name("barbican-config")
            or not isinstance(secret, dict)
            or secret.get("name") != self._name("barbican-config-secret")
        ):
            return False
        expected_items = [
            {
                "key": key,
                "path": (
                    BARBICAN_VASSAL_ITEM_PATH if key == BARBICAN_VASSAL_KEY else key
                ),
                "mode": 0o444,
            }
            for key in sorted(CONFIG_KEYS)
        ]
        actual_items = [
            {item_key: item.get(item_key) for item_key in ("key", "path", "mode")}
            for item in config_map.get("items") or []
            if isinstance(item, dict)
        ]
        if actual_items != expected_items:
            return False
        return secret.get("items") == [
            {"key": CONFIG_SECRET_KEY, "path": CONFIG_SECRET_KEY, "mode": 0o440}
        ]

    @staticmethod
    def _contract(actual: object, expected: Mapping[str, object]) -> bool:
        return isinstance(actual, dict) and all(
            actual.get(key) == value for key, value in expected.items()
        )

    def _pod_spec_ok(self, spec: object, component: str) -> bool:
        if not isinstance(spec, dict):
            return False
        if (
            spec.get("serviceAccountName") not in (None, "", "default")
            or spec.get("serviceAccount")
            or spec.get("hostIPC") is True
            or spec.get("hostAliases")
            or spec.get("shareProcessNamespace") is True
            or spec.get("imagePullSecrets")
            != [{"name": BARBICAN_IMAGE_PULL_SECRET_NAME}]
            or spec.get("automountServiceAccountToken") is not False
            or spec.get("enableServiceLinks") is not False
            or spec.get("terminationGracePeriodSeconds")
            != BARBICAN_TERMINATION_GRACE_PERIOD_SECONDS
            or spec.get("hostNetwork") is True
            or spec.get("hostPID") is True
        ):
            return False
        pod_security = {
            "runAsUser": BARBICAN_RUN_AS_ID,
            "runAsGroup": BARBICAN_RUN_AS_ID,
            "fsGroup": BARBICAN_RUN_AS_ID,
            "fsGroupChangePolicy": "OnRootMismatch",
            "supplementalGroups": [BARBICAN_SUPPLEMENTAL_GROUP],
        }
        if not self._contract(spec.get("securityContext"), pod_security):
            return False
        if not self._volumes_ok(
            spec.get("volumes"), with_state=component == API_COMPONENT
        ):
            return False
        mounts: list[dict[str, object]] = [
            {"name": "config", "mountPath": BARBICAN_RUNTIME_DIR, "readOnly": True},
            {"name": "tmp", "mountPath": BARBICAN_TMP_DIR},
        ]
        if component == API_COMPONENT:
            mounts.append({"name": "state", "mountPath": BARBICAN_API_STATE_DIR})
        image = (
            BARBICAN_API_IMAGE if component == API_COMPONENT else BARBICAN_WORKER_IMAGE
        )
        command = (
            BARBICAN_API_COMMAND
            if component == API_COMPONENT
            else BARBICAN_WORKER_COMMAND
        )
        container: dict[str, object] = {
            "name": component,
            "image": image,
            "command": command,
            "securityContext": CONTAINER_SECURITY_CONTEXT,
            "volumeMounts": mounts,
        }
        if component == API_COMPONENT:
            container["ports"] = [
                {
                    "name": API_COMPONENT,
                    "containerPort": BARBICAN_PORT,
                    "protocol": "TCP",
                }
            ]
        containers = spec.get("containers")
        if (
            not isinstance(containers, list)
            or len(containers) != 1
            or not self._contract(containers[0], container)
        ):
            return False
        assert isinstance(containers[0], dict)
        if component == API_COMPONENT and not (
            self._contract(
                containers[0].get("startupProbe"), BARBICAN_API_STARTUP_PROBE
            )
            and self._contract(
                containers[0].get("readinessProbe"), BARBICAN_API_READINESS_PROBE
            )
            and self._contract(
                containers[0].get("livenessProbe"), BARBICAN_API_LIVENESS_PROBE
            )
        ):
            return False
        if component != API_COMPONENT and (
            containers[0].get("ports")
            or any(containers[0].get(name) for name in BARBICAN_API_PROBE_NAMES)
        ):
            return False
        init_expected: dict[str, object] = {
            "name": "db-sync",
            "image": BARBICAN_API_IMAGE,
            "command": BARBICAN_DB_SYNC_COMMAND,
            "securityContext": CONTAINER_SECURITY_CONTEXT,
            "volumeMounts": mounts[:2],
        }
        inits = spec.get("initContainers")
        if component == API_COMPONENT:
            if (
                not isinstance(inits, list)
                or len(inits) != 1
                or not self._contract(inits[0], init_expected)
            ):
                return False
        elif inits not in (None, []):
            return False
        runtime_containers: list[dict[str, object]] = [containers[0]]
        runtime_containers.extend(
            item for item in (inits or []) if isinstance(item, dict)
        )
        if any(
            item.get(key)
            for item in runtime_containers
            for key in ("env", "envFrom", "stdin", "tty", "volumeDevices")
        ):
            return False
        return True

    def _deployment(self, component: str) -> None:
        stage = "api-deployment" if component == API_COMPONENT else "worker-deployment"
        payload = self._json(stage, "deployment", self._name(component))
        self._owned(stage, payload, component)
        spec = payload.get("spec")
        if not isinstance(spec, dict) or spec.get("replicas") != 1:
            raise ValidationFailure(stage)
        strategy = spec.get("strategy")
        if not self._contract(strategy, {"type": "Recreate"}) or (
            isinstance(strategy, dict) and strategy.get("rollingUpdate") is not None
        ):
            raise ValidationFailure(stage)
        if spec.get("selector") != {
            "matchLabels": {APPLIANCE_LABEL: self.identity, COMPONENT_LABEL: component}
        }:
            raise ValidationFailure(stage)
        template = spec.get("template")
        if not isinstance(template, dict) or not self._contract(
            template.get("metadata"), {"labels": self._expected_labels(component)}
        ):
            raise ValidationFailure(stage)
        if not self._pod_spec_ok(template.get("spec"), component):
            raise ValidationFailure(stage)

    def _api_deployment(self) -> None:
        self._deployment(API_COMPONENT)

    def _worker_deployment(self) -> None:
        self._deployment(WORKER_COMPONENT)

    def _deployment_ready(self, stage: str, component: str) -> str:
        """Return the deployment UID for a fully ready single replica."""
        payload = self._json(stage, "deployment", self._name(component))
        metadata = payload.get("metadata")
        uid = metadata.get("uid") if isinstance(metadata, dict) else None
        status = payload.get("status")
        if (
            not isinstance(uid, str)
            or not uid
            or not isinstance(status, dict)
            or any(
                status.get(key) != 1
                for key in (
                    "availableReplicas",
                    "readyReplicas",
                    "updatedReplicas",
                )
            )
        ):
            raise ValidationFailure(stage)
        return uid

    def _pod(self, stage: str, component: str) -> dict[str, object]:
        payload = self._json(
            stage,
            "pods",
            "--selector",
            f"{APPLIANCE_LABEL}={self.identity},{COMPONENT_LABEL}={component}",
        )
        items = payload.get("items")
        if (
            not isinstance(items, list)
            or len(items) != 1
            or not isinstance(items[0], dict)
        ):
            raise ValidationFailure(stage)
        return items[0]

    @staticmethod
    def _pod_readiness_ok(pod: Mapping[str, object]) -> bool:
        """Return True when the single pod reports readiness (any restart count)."""
        status = pod.get("status")
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
        containers = status.get("containerStatuses")
        return (
            isinstance(containers, list)
            and bool(containers)
            and all(
                isinstance(item, dict)
                and item.get("ready") is True
                and isinstance(item.get("restartCount"), int)
                for item in containers
            )
        )

    @staticmethod
    def _pod_restarts(stage: str, pod: Mapping[str, object]) -> int:
        status = pod.get("status")
        containers = (
            status.get("containerStatuses") if isinstance(status, dict) else None
        )
        if not isinstance(containers, list) or not all(
            isinstance(item, dict) and isinstance(item.get("restartCount"), int)
            for item in containers
        ):
            raise ValidationFailure(stage)
        return sum(int(item["restartCount"]) for item in containers)

    @staticmethod
    def _db_sync_ok(pod: Mapping[str, object]) -> bool:
        status = pod.get("status")
        inits = (
            status.get("initContainerStatuses") if isinstance(status, dict) else None
        )
        if not isinstance(inits, list) or len(inits) != 1:
            return False
        item = inits[0]
        if not isinstance(item, dict) or item.get("name") != "db-sync":
            return False
        state = item.get("state")
        terminated = state.get("terminated") if isinstance(state, dict) else None
        return isinstance(terminated, dict) and terminated.get("exitCode") == 0

    def _workloads(self) -> None:
        for component in (API_COMPONENT, WORKER_COMPONENT):
            deployment_uid = self._deployment_ready("workloads", component)
            pod = self._pod("workloads", component)
            metadata = pod.get("metadata")
            pod_uid = metadata.get("uid") if isinstance(metadata, dict) else None
            if not isinstance(pod_uid, str) or not pod_uid:
                raise ValidationFailure("workloads")
            restarts = self._pod_restarts("workloads", pod)
            if not self._pod_readiness_ok(pod) or restarts != 0:
                raise ValidationFailure("workloads")
            self._identities[f"deployment-{component}"] = deployment_uid
            self._identities[f"pod-{component}"] = pod_uid
            self._identities[f"restarts-{component}"] = restarts
            if component == API_COMPONENT and not self._db_sync_ok(pod):
                raise ValidationFailure("workloads")

    def _stability(self) -> None:
        self.sleeper(self.poll_interval)
        for component in (API_COMPONENT, WORKER_COMPONENT):
            deployment_uid = str(self._identities[f"deployment-{component}"])
            pod_uid = str(self._identities[f"pod-{component}"])
            restarts = int(self._identities[f"restarts-{component}"])
            if self._deployment_ready("stability", component) != deployment_uid:
                raise ValidationFailure("stability")
            pod = self._pod("stability", component)
            metadata = pod.get("metadata")
            current_pod_uid = (
                metadata.get("uid") if isinstance(metadata, dict) else None
            )
            if current_pod_uid != pod_uid:
                raise ValidationFailure("stability")
            if not self._pod_readiness_ok(pod):
                raise ValidationFailure("stability")
            if self._pod_restarts("stability", pod) != restarts:
                raise ValidationFailure("stability")

    def _ingress(self) -> None:
        payload = self._json("ingress", "ingress", self._name(API_COMPONENT))
        metadata = self._owned("ingress", payload, API_COMPONENT)
        annotations = metadata.get("annotations")
        if (
            not isinstance(annotations, dict)
            or annotations.get(REWRITE_TARGET_ANNOTATION) != INGRESS_REWRITE_TARGET
            or annotations.get(USE_REGEX_ANNOTATION) != "true"
        ):
            raise ValidationFailure("ingress")
        spec = payload.get("spec")
        rules = spec.get("rules") if isinstance(spec, dict) else None
        if (
            not isinstance(rules, list)
            or len(rules) != 1
            or not isinstance(rules[0], dict)
        ):
            raise ValidationFailure("ingress")
        http = rules[0].get("http")
        paths = http.get("paths") if isinstance(http, dict) else None
        if (
            not isinstance(paths, list)
            or len(paths) != 1
            or not isinstance(paths[0], dict)
        ):
            raise ValidationFailure("ingress")
        entry = paths[0]
        if (
            entry.get("path") != INGRESS_PATH
            or entry.get("pathType") != "ImplementationSpecific"
        ):
            raise ValidationFailure("ingress")
        backend = entry.get("backend")
        service = backend.get("service") if isinstance(backend, dict) else None
        if service != {
            "name": self._name(API_COMPONENT),
            "port": {"number": BARBICAN_PORT},
        }:
            raise ValidationFailure("ingress")

    def _endpoint(self) -> None:
        response = self._request(
            "endpoint", "GET", API_ROOT_PATH, headers={"Accept": "application/json"}
        )
        if response.status != 401:
            raise ValidationFailure("endpoint")

    def _token(self) -> None:
        body = json.dumps(
            {
                "auth": {
                    "identity": {
                        "methods": ["password"],
                        "password": {
                            "user": {
                                "name": "barbican",
                                "domain": {"name": "Default"},
                                "password": self.keystone_password,
                            }
                        },
                    },
                    "scope": {
                        "project": {"name": "service", "domain": {"name": "Default"}}
                    },
                }
            }
        ).encode()
        response = self._request(
            "token",
            "POST",
            AUTH_PATH,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=body,
        )
        token = self._header(response, "X-Subject-Token")
        if response.status != 201 or not token:
            raise ValidationFailure("token")
        self.registry.register(token)
        payload = self._body_json("token", response)
        record = payload.get("token")
        methods = record.get("methods") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict)
            or not isinstance(methods, list)
            or "password" not in methods
        ):
            raise ValidationFailure("token")
        project = record.get("project")
        domain = project.get("domain") if isinstance(project, dict) else None
        if (
            not isinstance(project, dict)
            or project.get("name") != "service"
            or not isinstance(domain, dict)
            or domain.get("name") != "Default"
        ):
            raise ValidationFailure("token")
        internal_url = f"http://{self._name(API_COMPONENT)}:{BARBICAN_PORT}"
        catalog = record.get("catalog")
        services = [
            entry
            for entry in (catalog if isinstance(catalog, list) else [])
            if isinstance(entry, dict) and entry.get("type") == "key-manager"
        ]
        if len(services) != 1 or services[0].get("name") != "barbican":
            raise ValidationFailure("token")
        endpoints = services[0].get("endpoints")
        if not isinstance(endpoints, list) or not endpoints:
            raise ValidationFailure("token")
        interfaces = set()
        for endpoint in endpoints:
            if (
                not isinstance(endpoint, dict)
                or endpoint.get("url") != internal_url
                or endpoint.get("region") != "RegionOne"
            ):
                raise ValidationFailure("token")
            interfaces.add(endpoint.get("interface"))
        if interfaces != set(CATALOG_INTERFACES):
            raise ValidationFailure("token")
        self.token = token

    def _authorized(self, stage: str, method: str, path: str) -> HttpResponse:
        if not self.token:
            raise ValidationFailure(stage)
        return self._request(stage, method, path, headers={"X-Auth-Token": self.token})

    @staticmethod
    def _secret_ids(stage: str, payload: Mapping[str, object]) -> set[str]:
        entries = payload.get("secrets")
        if not isinstance(entries, list):
            raise ValidationFailure(stage)
        ids: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(
                entry.get("secret_ref"), str
            ):
                raise ValidationFailure(stage)
            ids.add(_last_path_segment(str(entry["secret_ref"]), stage))
        return ids

    def _list(self, stage: str) -> set[str]:
        response = self._authorized(stage, "GET", SECRETS_PATH)
        if response.status != 200:
            raise ValidationFailure(stage)
        return self._secret_ids(stage, self._body_json(stage, response))

    def _baseline(self) -> None:
        self.baseline_ids = self._list("secret-list")

    def _create(self) -> None:
        body = json.dumps(
            {
                "name": VALIDATOR_SECRET_NAME,
                "payload": VALIDATOR_SECRET_PAYLOAD,
                "payload_content_type": "text/plain",
            }
        ).encode()
        response = self._request(
            "secret-create",
            "POST",
            SECRETS_PATH,
            headers={
                "X-Auth-Token": self.token,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body=body,
        )
        if response.status != 201:
            raise ValidationFailure("secret-create")
        location = self._header(response, "Location")
        reference = self._body_json("secret-create", response).get("secret_ref")
        if not location or not isinstance(reference, str) or not reference:
            raise ValidationFailure("secret-create")
        self.registry.register(reference)
        created = _last_path_segment(reference, "secret-create")
        if created != _last_path_segment(location, "secret-create"):
            raise ValidationFailure("secret-create")
        self.registry.register(created)
        self.created_id = created
        self.created_deleted = False

    def _secret_path(self) -> str:
        return f"{SECRETS_PATH}/{self.created_id}"

    def _active(self) -> None:
        if not self.created_id:
            raise ValidationFailure("secret-active")
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            response = self._authorized("secret-active", "GET", self._secret_path())
            if response.status == 200:
                status = self._body_json("secret-active", response).get("status")
                if status == "ACTIVE":
                    return
                if status not in ("PENDING_CREATION", "CREATING"):
                    raise ValidationFailure("secret-active")
            elif response.status != 404:
                raise ValidationFailure("secret-active")
            self.sleeper(self.poll_interval)
        raise ValidationFailure("secret-active")

    def _read(self) -> None:
        if not self.created_id:
            raise ValidationFailure("secret-read")
        response = self._authorized(
            "secret-read", "GET", f"{self._secret_path()}/payload"
        )
        if response.status != 200 or response.body != VALIDATOR_SECRET_PAYLOAD.encode():
            raise ValidationFailure("secret-read")

    def _verify(self) -> None:
        if not self.created_id:
            raise ValidationFailure("secret-verify")
        if self._list("secret-verify") != self.baseline_ids | {self.created_id}:
            raise ValidationFailure("secret-verify")

    def _await_deletion(self, stage: str) -> None:
        """Poll the secret detail until the Barbican delete is terminally confirmed."""
        if not self.created_id:
            raise ValidationFailure(stage)
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            response = self._authorized(stage, "GET", self._secret_path())
            if response.status in (404, 410):
                return
            if response.status == 200:
                status = self._body_json(stage, response).get("status")
                if isinstance(status, str) and status in DEFAULT_DELETE_STATUSES:
                    self.sleeper(self.poll_interval)
                    continue
            raise ValidationFailure(stage)
        raise ValidationFailure(stage)

    def _delete(self) -> None:
        if not self.created_id:
            raise ValidationFailure("secret-delete")
        response = self._authorized("secret-delete", "DELETE", self._secret_path())
        if response.status not in (200, 202, 204):
            raise ValidationFailure("secret-delete")
        self._await_deletion("secret-delete")
        if self._list("secret-delete") != self.baseline_ids:
            raise ValidationFailure("secret-delete")
        self.created_deleted = True

    def _cleanup(self) -> bool:
        """Best-effort delete; True only after acceptance and confirmed absence."""
        if not (self.created_id and not self.created_deleted and self.token):
            return True
        try:
            response = self._request(
                "cleanup",
                "DELETE",
                self._secret_path(),
                headers={"X-Auth-Token": self.token},
            )
            if response.status not in (200, 202, 204):
                return False
            self._await_deletion("cleanup")
            if self._list("cleanup") != self.baseline_ids:
                return False
            self.created_deleted = True
            return True
        except Exception:
            return False

    def _run_body(self) -> None:
        self._appliance()
        self._report("PASS", "appliance")
        self._credentials()
        self._report("PASS", "credentials")
        self._config_map()
        self._report("PASS", "config-map")
        self._config_secret()
        self._report("PASS", "config-secret")
        self._service()
        self._report("PASS", "service")
        self._api_deployment()
        self._report("PASS", "api-deployment")
        self._worker_deployment()
        self._report("PASS", "worker-deployment")
        self._workloads()
        self._report("PASS", "workloads")
        self._ingress()
        self._report("PASS", "ingress")
        self._endpoint()
        self._report("PASS", "endpoint")
        self._token()
        self._report("PASS", "token")
        self._baseline()
        self._report("PASS", "secret-list")
        self._create()
        self._report("PASS", "secret-create")
        self._active()
        self._report("PASS", "secret-active")
        self._read()
        self._report("PASS", "secret-read")
        self._verify()
        self._report("PASS", "secret-verify")
        self._delete()
        self._report("PASS", "secret-delete")
        self._stability()
        self._report("PASS", "stability")

    def _summary(self, outcome: str, started: float) -> None:
        line = f"SUMMARY barbican {outcome} {self.clock() - started:.3f}"
        self.registry.audit(line)
        self._raw_report(line)

    def run(self) -> int:
        started = self.clock()
        failure: ValidationFailure | None = None
        try:
            self._run_body()
        except ValidationFailure as error:
            failure = error
        except Exception:
            failure = ValidationFailure("internal")
        if failure is None:
            self._summary("passed", started)
            return 0
        self._report("FAIL", failure.stage)
        if not self._cleanup():
            self._report("FAIL", "cleanup")
        self._summary("failed", started)
        return 1


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ValidationFailure("cli")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SilentParser(add_help=False)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--app-name", required=True, dest="app_name")
    parser.add_argument("--host", required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    try:
        args = parser.parse_args(argv)
        if not args.run:
            raise ValidationFailure("cli")
        if not all(
            isinstance(value, str) and value
            for value in (args.context, args.namespace, args.app_name, args.host)
        ):
            raise ValidationFailure("cli")
        if (
            _APP.fullmatch(args.app_name) is None
            or len(args.app_name) > DNS_SUBDOMAIN_MAX_LENGTH
            or _HOST.fullmatch(args.host) is None
        ):
            raise ValidationFailure("cli")
        if not MIN_TIMEOUT <= args.timeout <= MAX_TIMEOUT:
            raise ValidationFailure("cli")
        if not MIN_POLL_INTERVAL <= args.poll_interval <= MAX_POLL_INTERVAL:
            raise ValidationFailure("cli")
        return Validator(
            context=args.context,
            namespace=args.namespace,
            app=args.app_name,
            host=args.host,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
        ).run()
    except Exception:
        print("FAIL cli")
        return 2


if __name__ == "__main__":
    sys.exit(main())
