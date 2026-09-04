import base64
import copy
import importlib.util
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from coriolis_operator.reconcile import (
    appliance_identity,
    appliance_resource_name,
)

SCRIPT = Path(__file__).parents[2] / "scripts" / "validate-barbican-runtime.py"
SPEC = importlib.util.spec_from_file_location("validate_barbican_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)

CONTEXT = "ctx"
NAMESPACE = "coriolis"
APP = "acme"
DOTTED_APP = "my.appliance.example.test"
LONG_APP = "q" * 50
LONG_DOTTED_APP = ("alpha-bravo-charlie." * 8).rstrip(".") + ".delta.example.test"
HOST = "appliance.example.test"
VERSION = "2603.5"
APPLIANCE_UID = "11111111-1111-1111-1111-111111111111"
DB_PASSWORD = "database-password/with?encoding"
KEYSTONE_PASSWORD = "keystone-password/with?encoding"
CRYPTO_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
TOKEN = "subject-token/with?encoding"
SECRET_ID = "6f9d2c1a-0b3e-4c5d-9e7f-1a2b3c4d5e6f"
BASELINE_ID = "00000000-0000-0000-0000-000000000000"
API_DEPLOYMENT_UID = "aaaaaaaa-0000-0000-0000-000000000001"
WORKER_DEPLOYMENT_UID = "aaaaaaaa-0000-0000-0000-000000000002"
API_POD_UID = "bbbbbbbb-0000-0000-0000-000000000001"
WORKER_POD_UID = "bbbbbbbb-0000-0000-0000-000000000002"
APPLIANCE_LABEL = "coriolis.cloudbase.it/appliance"
COMPONENT_LABEL = "coriolis.cloudbase.it/component"
BARBICAN_COMPONENTS = (
    "barbican-credentials",
    "barbican-config",
    "barbican-config-secret",
    "barbican-api",
    "barbican-worker",
)

PASS_STAGES = [
    "appliance",
    "credentials",
    "config-map",
    "config-secret",
    "service",
    "api-deployment",
    "worker-deployment",
    "workloads",
    "ingress",
    "endpoint",
    "token",
    "secret-list",
    "secret-create",
    "secret-active",
    "secret-read",
    "secret-verify",
    "secret-delete",
    "stability",
]
SENTINELS = (DB_PASSWORD, KEYSTONE_PASSWORD, CRYPTO_KEY, TOKEN, SECRET_ID)
EXPECTED_AUTH_BODY = {
    "auth": {
        "identity": {
            "methods": ["password"],
            "password": {
                "user": {
                    "name": "barbican",
                    "domain": {"name": "Default"},
                    "password": KEYSTONE_PASSWORD,
                }
            },
        },
        "scope": {"project": {"name": "service", "domain": {"name": "Default"}}},
    }
}
EXPECTED_CREATE_BODY = {
    "name": runtime.VALIDATOR_SECRET_NAME,
    "payload": runtime.VALIDATOR_SECRET_PAYLOAD,
    "payload_content_type": "text/plain",
}


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _config_map_items() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "path": (
                runtime.BARBICAN_VASSAL_ITEM_PATH
                if key == runtime.BARBICAN_VASSAL_KEY
                else key
            ),
            "mode": 292,
        }
        for key in sorted(runtime.CONFIG_KEYS)
    ]


def _pod(
    component: str, uid: str, *, restarts: int = 0, db_sync_exit: int = 0
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "conditions": [{"type": "Ready", "status": "True"}],
        "containerStatuses": [
            {
                "name": component,
                "ready": True,
                "restartCount": restarts,
                "image": runtime.BARBICAN_API_IMAGE,
            }
        ],
    }
    if component == runtime.API_COMPONENT:
        status["initContainerStatuses"] = [
            {
                "name": "db-sync",
                "ready": True,
                "restartCount": 0,
                "state": {"terminated": {"exitCode": db_sync_exit}},
            }
        ]
    return {
        "metadata": {"name": f"{component}-7d9-xk2lp", "uid": uid},
        "status": status,
    }


class FakeKubernetes:
    def __init__(self, app: str = APP) -> None:
        self.app = app
        self.identity = appliance_identity(app)
        self.api_restarts = 0
        self.db_sync_exit = 0
        self.stability_pod_drift = False
        self.stability_restart_drift = False
        self.deployment_rv_drift = False
        self.pod_calls = {runtime.API_COMPONENT: 0, runtime.WORKER_COMPONENT: 0}
        self.deployment_calls = {runtime.API_COMPONENT: 0, runtime.WORKER_COMPONENT: 0}
        self.commands: list[tuple[str, ...]] = []
        self.appliance: dict[str, Any] = {
            "metadata": {"name": app, "namespace": NAMESPACE, "uid": APPLIANCE_UID},
            "spec": {"version": VERSION},
            "status": {
                "acceptedVersion": VERSION,
                "conditions": [
                    {
                        "type": "Ready",
                        "status": "True",
                        "reason": "RuntimeReady",
                    }
                ],
            },
        }
        self.credentials: dict[str, Any] = {
            "metadata": {
                "name": self.name("barbican-credentials"),
                "namespace": NAMESPACE,
                "labels": self._labels("barbican-credentials"),
                "annotations": {runtime.RETENTION_ANNOTATION: "state-credentials"},
            },
            "type": "Opaque",
            "data": {
                "barbican_database_password": _encoded(DB_PASSWORD),
                "barbican_keystone_password": _encoded(KEYSTONE_PASSWORD),
                "barbican_crypto_key": _encoded(CRYPTO_KEY),
            },
        }
        self.config_map: dict[str, Any] = {
            "metadata": self._owned_metadata("barbican-config"),
            "data": {key: f"value-{key}" for key in runtime.CONFIG_KEYS},
        }
        self.config_secret: dict[str, Any] = {
            "metadata": self._owned_metadata("barbican-config-secret"),
            "type": "Opaque",
            "data": {"barbican.conf": _encoded("[DEFAULT]")},
        }
        self.service: dict[str, Any] = {
            "metadata": self._owned_metadata(runtime.API_COMPONENT),
            "spec": {
                "type": "ClusterIP",
                "selector": {
                    APPLIANCE_LABEL: self.identity,
                    COMPONENT_LABEL: runtime.API_COMPONENT,
                },
                "ports": [
                    {
                        "name": runtime.API_COMPONENT,
                        "protocol": "TCP",
                        "port": runtime.BARBICAN_PORT,
                        "targetPort": runtime.BARBICAN_PORT,
                    }
                ],
            },
        }
        self.api_deployment = self._deployment(
            runtime.API_COMPONENT, API_DEPLOYMENT_UID
        )
        self.worker_deployment = self._deployment(
            runtime.WORKER_COMPONENT, WORKER_DEPLOYMENT_UID
        )
        self.ingress: dict[str, Any] = {
            "metadata": {
                **self._owned_metadata(runtime.API_COMPONENT),
                "annotations": {
                    runtime.REWRITE_TARGET_ANNOTATION: "/$2",
                    runtime.USE_REGEX_ANNOTATION: "true",
                },
            },
            "spec": {
                "rules": [
                    {
                        "http": {
                            "paths": [
                                {
                                    "path": runtime.INGRESS_PATH,
                                    "pathType": "ImplementationSpecific",
                                    "backend": {
                                        "service": {
                                            "name": self.name(runtime.API_COMPONENT),
                                            "port": {"number": runtime.BARBICAN_PORT},
                                        }
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        }

    def name(self, component: str) -> str:
        return appliance_resource_name(self.app, component)

    def _labels(self, component: str) -> dict[str, str]:
        return {
            "app.kubernetes.io/name": "coriolis",
            "app.kubernetes.io/instance": self.identity,
            "app.kubernetes.io/version": VERSION,
            "app.kubernetes.io/component": component,
            "app.kubernetes.io/part-of": "coriolis-appliance",
            "app.kubernetes.io/managed-by": "coriolis-operator",
            APPLIANCE_LABEL: self.identity,
            COMPONENT_LABEL: component,
        }

    def _owner(self) -> list[dict[str, Any]]:
        return [
            {
                "apiVersion": "coriolis.cloudbase.it/v1alpha1",
                "kind": "CoriolisAppliance",
                "name": self.app,
                "uid": APPLIANCE_UID,
                "controller": True,
            }
        ]

    def _owned_metadata(self, component: str) -> dict[str, Any]:
        return {
            "name": self.name(component),
            "namespace": NAMESPACE,
            "labels": self._labels(component),
            "ownerReferences": self._owner(),
        }

    def _pod_spec(self, component: str) -> dict[str, Any]:
        api = component == runtime.API_COMPONENT
        mounts = [
            {
                "name": "config",
                "mountPath": runtime.BARBICAN_RUNTIME_DIR,
                "readOnly": True,
            },
            {"name": "tmp", "mountPath": runtime.BARBICAN_TMP_DIR},
        ]
        volumes: list[dict[str, Any]] = [
            {
                "name": "config",
                "projected": {
                    "sources": [
                        {
                            "configMap": {
                                "name": self.name("barbican-config"),
                                "items": _config_map_items(),
                            }
                        },
                        {
                            "secret": {
                                "name": self.name("barbican-config-secret"),
                                "items": [
                                    {
                                        "key": "barbican.conf",
                                        "path": "barbican.conf",
                                        "mode": 288,
                                    }
                                ],
                            }
                        },
                    ]
                },
            },
            {"name": "tmp", "emptyDir": {"medium": "Memory"}},
        ]
        if api:
            volumes.append({"name": "state", "emptyDir": {}})
        container: dict[str, Any] = {
            "name": component,
            "image": (
                runtime.BARBICAN_API_IMAGE if api else runtime.BARBICAN_WORKER_IMAGE
            ),
            "command": (
                list(runtime.BARBICAN_API_COMMAND)
                if api
                else list(runtime.BARBICAN_WORKER_COMMAND)
            ),
            "securityContext": copy.deepcopy(runtime.CONTAINER_SECURITY_CONTEXT),
            "volumeMounts": mounts
            + (
                [{"name": "state", "mountPath": runtime.BARBICAN_API_STATE_DIR}]
                if api
                else []
            ),
            "resources": {},
            "imagePullPolicy": "IfNotPresent",
            "terminationMessagePath": "/dev/termination-log",
        }
        if api:
            container["ports"] = [
                {
                    "name": runtime.API_COMPONENT,
                    "containerPort": runtime.BARBICAN_PORT,
                    "protocol": "TCP",
                }
            ]
            container["startupProbe"] = copy.deepcopy(
                runtime.BARBICAN_API_STARTUP_PROBE
            )
            container["readinessProbe"] = copy.deepcopy(
                runtime.BARBICAN_API_READINESS_PROBE
            )
            container["livenessProbe"] = copy.deepcopy(
                runtime.BARBICAN_API_LIVENESS_PROBE
            )
        spec: dict[str, Any] = {
            "imagePullSecrets": [{"name": runtime.BARBICAN_IMAGE_PULL_SECRET_NAME}],
            "securityContext": {
                "runAsUser": runtime.BARBICAN_RUN_AS_ID,
                "runAsGroup": runtime.BARBICAN_RUN_AS_ID,
                "fsGroup": runtime.BARBICAN_RUN_AS_ID,
                "fsGroupChangePolicy": "OnRootMismatch",
                "supplementalGroups": [runtime.BARBICAN_SUPPLEMENTAL_GROUP],
            },
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "terminationGracePeriodSeconds": (
                runtime.BARBICAN_TERMINATION_GRACE_PERIOD_SECONDS
            ),
            "containers": [container],
            "volumes": volumes,
            "dnsPolicy": "ClusterFirst",
            "schedulerName": "default-scheduler",
        }
        if api:
            spec["initContainers"] = [
                {
                    "name": "db-sync",
                    "image": runtime.BARBICAN_API_IMAGE,
                    "command": list(runtime.BARBICAN_DB_SYNC_COMMAND),
                    "securityContext": copy.deepcopy(
                        runtime.CONTAINER_SECURITY_CONTEXT
                    ),
                    "volumeMounts": mounts,
                    "resources": {},
                }
            ]
        return spec

    def _deployment(self, component: str, uid: str) -> dict[str, Any]:
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                **self._owned_metadata(component),
                "uid": uid,
                "resourceVersion": "100",
            },
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {
                    "matchLabels": {
                        APPLIANCE_LABEL: self.identity,
                        COMPONENT_LABEL: component,
                    }
                },
                "template": {
                    "metadata": {"labels": self._labels(component)},
                    "spec": self._pod_spec(component),
                },
            },
            "status": {
                "availableReplicas": 1,
                "readyReplicas": 1,
                "updatedReplicas": 1,
            },
        }

    def _deployment_payload(self, component: str) -> dict[str, Any]:
        self.deployment_calls[component] += 1
        deployment = (
            self.api_deployment
            if component == runtime.API_COMPONENT
            else self.worker_deployment
        )
        if self.deployment_rv_drift and self.deployment_calls[component] >= 2:
            drifted = copy.deepcopy(deployment)
            drifted["metadata"]["resourceVersion"] = str(
                100 + self.deployment_calls[component]
            )
            return drifted
        return deployment

    def _pod_payload(self, component: str) -> dict[str, Any]:
        self.pod_calls[component] += 1
        drifted_call = self.pod_calls[component] >= 2
        uid = (
            f"changed-{component}"
            if self.stability_pod_drift and drifted_call
            else (API_POD_UID if component == runtime.API_COMPONENT else WORKER_POD_UID)
        )
        if self.stability_pod_drift and drifted_call:
            restarts = 1
        elif self.stability_restart_drift and drifted_call:
            restarts = 1
        elif component == runtime.API_COMPONENT:
            restarts = self.api_restarts
        else:
            restarts = 0
        return _pod(component, uid, restarts=restarts, db_sync_exit=self.db_sync_exit)

    def runner(self, command: Any, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list)
        self.commands.append(tuple(command))
        joined = " ".join(str(part) for part in command)
        payload: dict[str, Any] | None = None
        if "coriolisappliance" in command:
            payload = self.appliance
        elif self.name("barbican-credentials") in joined:
            payload = self.credentials
        elif self.name("barbican-config-secret") in joined:
            payload = self.config_secret
        elif self.name("barbican-config") in joined:
            payload = self.config_map
        elif "deployment" in command:
            payload = self._deployment_payload(
                runtime.API_COMPONENT
                if self.name(runtime.API_COMPONENT) in joined
                else runtime.WORKER_COMPONENT
            )
        elif command[command.index("get") + 1 :][:1] == ["pods"]:
            selector = next(
                item
                for item in command
                if isinstance(item, str)
                and item.startswith(f"{APPLIANCE_LABEL}={self.identity},")
            )
            component = selector.split(f"{COMPONENT_LABEL}=")[-1]
            payload = {"items": [self._pod_payload(component)]}
        elif "service" in command:
            payload = self.service
        elif "ingress" in command:
            payload = self.ingress
        if payload is None:
            return subprocess.CompletedProcess(command, 1, "", "unknown")
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")


class FakeHttp:
    def __init__(self, app: str = APP) -> None:
        self.app = app
        self.api_name = appliance_resource_name(app, runtime.API_COMPONENT)
        self.internal_url = f"http://{self.api_name}:{runtime.BARBICAN_PORT}"
        self.created = False
        self.deleted = False
        self.active_status = "ACTIVE"
        self.auth_status = 201
        self.endpoint_status = 401
        self.post_status = 201
        self.delete_status = 204
        self.read_status = 200
        self.catalog_url = self.internal_url
        self.catalog_interfaces: tuple[str, ...] = ("admin", "internal", "public")
        self.delete_pending_polls = 1
        self.delete_gone_status = 410
        self.deleted_detail_status: str | None = None
        self.requests: list[tuple[str, str, dict[str, str], bytes | None]] = []
        self.secret_ref = f"http://{self.api_name}/v1/secrets/{SECRET_ID}"

    @property
    def delete_detail_path(self) -> str:
        return f"{runtime.SECRETS_PATH}/{SECRET_ID}"

    def _token_body(self) -> bytes:
        return json.dumps(
            {
                "token": {
                    "methods": ["password"],
                    "project": {
                        "name": "service",
                        "domain": {"name": "Default"},
                    },
                    "catalog": [
                        {
                            "type": "key-manager",
                            "name": "barbican",
                            "endpoints": [
                                {
                                    "interface": interface,
                                    "url": self.catalog_url,
                                    "region": "RegionOne",
                                }
                                for interface in self.catalog_interfaces
                            ],
                        }
                    ],
                }
            }
        ).encode()

    def _list_body(self) -> bytes:
        refs = [BASELINE_ID]
        if self.created and not self.deleted:
            refs.append(SECRET_ID)
        return json.dumps(
            {
                "secrets": [
                    {"secret_ref": f"http://api/v1/secrets/{ref}"} for ref in refs
                ]
            }
        ).encode()

    def __call__(
        self,
        method: str,
        url: str,
        headers: Any,
        body: bytes | None,
        timeout: int,
    ) -> runtime.HttpResponse:
        assert isinstance(headers, dict)
        assert url.startswith(f"https://{HOST}/"), "TLS-only public egress"
        path = urllib.parse.urlparse(url).path
        self.requests.append((method, path, dict(headers), body))
        if method == "POST" and path == runtime.AUTH_PATH:
            assert headers == {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            assert body is not None
            assert json.loads(body) == EXPECTED_AUTH_BODY
            return runtime.HttpResponse(
                self.auth_status, {"X-Subject-Token": TOKEN}, self._token_body()
            )
        if path == runtime.API_ROOT_PATH:
            return runtime.HttpResponse(self.endpoint_status, {}, b"{}")
        if path == runtime.SECRETS_PATH:
            if method == "GET":
                assert headers == {"X-Auth-Token": TOKEN}
                return runtime.HttpResponse(200, {}, self._list_body())
            assert headers == {
                "X-Auth-Token": TOKEN,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            assert body is not None
            assert json.loads(body) == EXPECTED_CREATE_BODY
            self.created = True
            return runtime.HttpResponse(
                self.post_status,
                {"Location": self.secret_ref},
                json.dumps({"secret_ref": self.secret_ref}).encode(),
            )
        detail = self.delete_detail_path
        if path == detail:
            if method == "DELETE":
                assert headers == {"X-Auth-Token": TOKEN}
                if self.delete_status in (200, 202, 204):
                    self.deleted = True
                return runtime.HttpResponse(self.delete_status, {}, b"")
            if self.deleted:
                if self.deleted_detail_status is not None:
                    return runtime.HttpResponse(
                        200,
                        {},
                        json.dumps({"status": self.deleted_detail_status}).encode(),
                    )
                if self.delete_pending_polls > 0:
                    self.delete_pending_polls -= 1
                    return runtime.HttpResponse(
                        200, {}, json.dumps({"status": "PENDING_DELETE"}).encode()
                    )
                return runtime.HttpResponse(self.delete_gone_status, {}, b"{}")
            return runtime.HttpResponse(
                200, {}, json.dumps({"status": self.active_status}).encode()
            )
        if path == f"{detail}/payload":
            if self.deleted:
                return runtime.HttpResponse(404, {}, b"")
            if self.read_status != 200:
                return runtime.HttpResponse(self.read_status, {}, b"")
            return runtime.HttpResponse(
                200, {}, runtime.VALIDATOR_SECRET_PAYLOAD.encode()
            )
        raise AssertionError(f"unhandled request {method} {path}")


def _validator(
    app: str = APP,
    **overrides: Any,
) -> tuple[
    runtime.Validator, FakeKubernetes, FakeHttp, list[str], list[tuple[float, int]]
]:
    kubernetes = FakeKubernetes(app)
    http = FakeHttp(app)
    output: list[str] = []
    sleeps: list[tuple[float, int]] = []
    now = [0.0]
    validator = runtime.Validator(
        context=CONTEXT,
        namespace=NAMESPACE,
        app=app,
        host=HOST,
        runner=kubernetes.runner,
        http=http,
        clock=lambda: now.__setitem__(0, now[0] + 0.1) or now[0],
        sleeper=lambda seconds: sleeps.append((seconds, len(output))),
        report=output.append,
    )
    apply_mutations = overrides.pop("mutations", None)
    assert not overrides
    if apply_mutations is not None:
        apply_mutations(kubernetes, http)
    return validator, kubernetes, http, output, sleeps


def _forms(value: str) -> set[str]:
    registry = runtime.CredentialRegistry()
    registry.register(value)
    return {form for form in registry.forms if form}


def _assert_silent(output: list[str], commands: list[tuple[str, ...]]) -> None:
    rendered_output = "\n".join(output)
    rendered_commands = "\n".join(" ".join(command) for command in commands)
    for sentinel in SENTINELS:
        assert sentinel not in rendered_output
        assert sentinel not in rendered_commands
        for form in _forms(sentinel):
            assert form not in rendered_output
            assert form not in rendered_commands


def test_success_reports_every_stage_and_cleans_up() -> None:
    validator, kubernetes, http, output, sleeps = _validator()

    assert validator.run() == 0
    assert output[: len(PASS_STAGES)] == [f"PASS {stage}" for stage in PASS_STAGES]
    assert re.fullmatch(r"SUMMARY barbican passed \d+\.\d{3}", output[-1])
    assert len(output) == len(PASS_STAGES) + 1
    assert http.created and http.deleted
    assert http.delete_pending_polls == 0
    assert validator.created_deleted
    assert sleeps == [
        (runtime.DEFAULT_POLL_INTERVAL, len(PASS_STAGES) - 2),
        (runtime.DEFAULT_POLL_INTERVAL, len(PASS_STAGES) - 1),
    ]
    _assert_silent(output, kubernetes.commands)
    assert (
        "kubectl",
        "--context",
        CONTEXT,
        "--namespace",
        NAMESPACE,
        "get",
        "pods",
        "--selector",
        f"{APPLIANCE_LABEL}={APP},{COMPONENT_LABEL}={runtime.API_COMPONENT}",
        "-o",
        "json",
    ) in kubernetes.commands
    assert (
        "kubectl",
        "--context",
        CONTEXT,
        "--namespace",
        NAMESPACE,
        "get",
        "coriolisappliance",
        APP,
        "-o",
        "json",
    ) in kubernetes.commands
    assert (
        "kubectl",
        "--context",
        CONTEXT,
        "--namespace",
        NAMESPACE,
        "get",
        "secret",
        f"{APP}-barbican-credentials",
        "-o",
        "json",
    ) in kubernetes.commands
    auth = next(
        request
        for request in http.requests
        if request[0] == "POST" and request[1] == runtime.AUTH_PATH
    )
    assert auth[2] == {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    assert auth[3] is not None
    assert json.loads(auth[3]) == EXPECTED_AUTH_BODY
    create = next(
        request
        for request in http.requests
        if request[0] == "POST" and request[1] == runtime.SECRETS_PATH
    )
    assert create[2] == {
        "X-Auth-Token": TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    assert create[3] is not None
    assert json.loads(create[3]) == EXPECTED_CREATE_BODY
    listed = next(
        request
        for request in http.requests
        if request[0] == "GET" and request[1] == runtime.SECRETS_PATH
    )
    assert listed[2] == {"X-Auth-Token": TOKEN}
    assert all(TOKEN not in url for _, url, _, _ in http.requests)
    assert all(
        request[1].startswith("/") and not request[1].startswith("//")
        for request in http.requests
    )
    assert not any(
        "delete" in command or "create" in command
        for command in kubernetes.commands
        if "kubectl" in command
    )


def test_success_confirms_asynchronous_delete_before_passing() -> None:
    validator, _, http, _, _ = _validator()

    assert validator.run() == 0
    detail = http.delete_detail_path
    sequence = [(method, path) for method, path, _, _ in http.requests]
    delete_index = sequence.index(("DELETE", detail))
    after_delete = sequence[delete_index + 1 :]
    assert after_delete[0] == ("GET", detail)
    assert after_delete[1] == ("GET", detail)
    assert ("GET", runtime.SECRETS_PATH) in after_delete[2:]
    assert http.delete_pending_polls == 0


@pytest.mark.parametrize("delete_status", [200, 202, 204])
def test_accepted_delete_response_statuses_pass(delete_status: int) -> None:
    validator, _, http, output, _ = _validator(
        mutations=lambda k, h: setattr(h, "delete_status", delete_status)
    )

    assert validator.run() == 0
    assert output[-2] == "PASS stability"
    assert http.deleted


@pytest.mark.parametrize("gone_status", [404, 410])
def test_delete_poll_accepts_404_and_410_as_terminal(gone_status: int) -> None:
    validator, _, http, output, _ = _validator(
        mutations=lambda k, h: setattr(h, "delete_gone_status", gone_status)
    )

    assert validator.run() == 0
    assert "PASS secret-delete" in output
    assert http.deleted


def test_delete_poll_rejects_unsettled_status_and_cleanup_fails() -> None:
    def mutate(k: FakeKubernetes, h: FakeHttp) -> None:
        h.delete_pending_polls = 0
        h.deleted_detail_status = "ACTIVE"

    validator, kubernetes, http, output, _ = _validator(mutations=mutate)

    assert validator.run() == 1
    assert output[-3:] == ["FAIL secret-delete", "FAIL cleanup", output[-1]]
    assert re.fullmatch(r"SUMMARY barbican failed \d+\.\d{3}", output[-1])
    assert validator.created_deleted is False
    _assert_silent(output, kubernetes.commands)


def test_delete_polling_timeout_fails_then_reports_cleanup_failure() -> None:
    validator, _, http, output, _ = _validator(
        mutations=lambda k, h: setattr(h, "delete_pending_polls", 10_000)
    )

    assert validator.run() == 1
    assert output[-3:] == [
        "FAIL secret-delete",
        "FAIL cleanup",
        output[-1],
    ]
    assert re.fullmatch(r"SUMMARY barbican failed \d+\.\d{3}", output[-1])
    assert http.deleted


@pytest.mark.parametrize(
    ("mutations", "stage"),
    [
        (
            lambda k, h: k.appliance["status"]["conditions"][0].update(
                {"reason": "Reconciling"}
            ),
            "appliance",
        ),
        (
            lambda k, h: k.credentials["data"].update({"extra": _encoded("x")}),
            "credentials",
        ),
        (
            lambda k, h: k.credentials["metadata"].update(
                {"ownerReferences": k._owner()}
            ),
            "credentials",
        ),
        (
            lambda k, h: k.config_map["data"].update({"barbican.conf": "credential"}),
            "config-map",
        ),
        (
            lambda k, h: k.config_secret["data"].update({"extra": _encoded("x")}),
            "config-secret",
        ),
        (
            lambda k, h: k.service["spec"]["ports"][0].update({"port": 1234}),
            "service",
        ),
        (
            lambda k, h: k.api_deployment["spec"]["template"]["spec"]["containers"][
                0
            ].update({"image": "wrong-image@sha256:0" * 64}),
            "api-deployment",
        ),
        (
            lambda k, h: k.worker_deployment["spec"]["template"]["spec"].update(
                {"automountServiceAccountToken": True}
            ),
            "worker-deployment",
        ),
        (lambda k, h: setattr(k, "db_sync_exit", 1), "workloads"),
        (lambda k, h: setattr(k, "api_restarts", 1), "workloads"),
        (
            lambda k, h: k.ingress["spec"]["rules"][0]["http"]["paths"][0].update(
                {"path": "/wrong(/|$)(.*)"}
            ),
            "ingress",
        ),
        (
            lambda k, h: k.ingress["metadata"]["annotations"].update(
                {runtime.REWRITE_TARGET_ANNOTATION: "/$1"}
            ),
            "ingress",
        ),
        (
            lambda k, h: k.credentials["metadata"]["annotations"].update(
                {runtime.RETENTION_ANNOTATION: "released"}
            ),
            "credentials",
        ),
        (
            lambda k, h: k.credentials["data"].update(
                {"barbican_crypto_key": _encoded("not-a-canonical-kek!")}
            ),
            "credentials",
        ),
        (
            lambda k, h: k.credentials["metadata"]["labels"].pop(
                "app.kubernetes.io/part-of"
            ),
            "credentials",
        ),
        (
            lambda k, h: k.api_deployment["spec"]["template"]["spec"]["volumes"][0][
                "projected"
            ]["sources"][0]["configMap"]["items"][1].update(
                {"path": "barbican-api.ini"}
            ),
            "api-deployment",
        ),
        (
            lambda k, h: k.api_deployment["spec"]["template"]["spec"]["containers"][0][
                "startupProbe"
            ].update({"periodSeconds": 3}),
            "api-deployment",
        ),
        (
            lambda k, h: k.api_deployment["spec"]["template"]["spec"]["containers"][0][
                "startupProbe"
            ]["exec"]["command"].__setitem__(0, "/usr/bin/python3"),
            "api-deployment",
        ),
        (
            lambda k, h: k.api_deployment["spec"]["template"]["spec"]["containers"][
                0
            ].update({"env": [{"name": "LEAK", "value": "x"}]}),
            "api-deployment",
        ),
        (
            lambda k, h: k.api_deployment["spec"]["template"]["spec"].update(
                {"serviceAccountName": "harbor-runner"}
            ),
            "api-deployment",
        ),
        (
            lambda k, h: k.worker_deployment["spec"]["template"]["spec"]["containers"][
                0
            ].update({"readinessProbe": {"tcpSocket": {"port": 9311}}}),
            "worker-deployment",
        ),
        (lambda k, h: setattr(h, "endpoint_status", 200), "endpoint"),
        (lambda k, h: setattr(h, "auth_status", 401), "token"),
        (lambda k, h: setattr(h, "catalog_url", "http://elsewhere:9311"), "token"),
        (
            lambda k, h: setattr(h, "catalog_interfaces", ("internal", "public")),
            "token",
        ),
        (lambda k, h: setattr(h, "read_status", 404), "secret-read"),
        (lambda k, h: setattr(h, "active_status", "DELETED"), "secret-active"),
        (lambda k, h: setattr(k, "stability_pod_drift", True), "stability"),
        (lambda k, h: setattr(k, "stability_restart_drift", True), "stability"),
    ],
)
def test_failures_report_only_the_fixed_stage(mutations: Any, stage: str) -> None:
    validator, kubernetes, http, output, _ = _validator(mutations=mutations)

    assert validator.run() == 1
    assert output[-2:] == [
        f"FAIL {stage}",
        output[-1],
    ]
    assert re.fullmatch(r"SUMMARY barbican failed \d+\.\d{3}", output[-1])
    assert output.count(f"FAIL {stage}") == 1
    assert "FAIL cleanup" not in output
    _assert_silent(output, kubernetes.commands)


def test_failure_after_creation_confirms_cleanup_before_summary() -> None:
    validator, kubernetes, http, output, _ = _validator(
        mutations=lambda k, h: setattr(h, "read_status", 404)
    )

    assert validator.run() == 1
    assert output[-2:] == ["FAIL secret-read", output[-1]]
    assert http.created and http.deleted
    assert http.delete_pending_polls == 0
    assert validator.created_deleted
    _assert_silent(output, kubernetes.commands)


def test_failed_cleanup_reports_stage_then_cleanup_then_failed_summary() -> None:
    def mutate(k: FakeKubernetes, h: FakeHttp) -> None:
        h.read_status = 404
        h.delete_status = 500

    validator, kubernetes, http, output, _ = _validator(mutations=mutate)

    assert validator.run() == 1
    assert output[-3:] == ["FAIL secret-read", "FAIL cleanup", output[-1]]
    assert re.fullmatch(r"SUMMARY barbican failed \d+\.\d{3}", output[-1])
    assert not http.deleted
    assert not validator.created_deleted
    assert all(
        re.fullmatch(r"(PASS|FAIL) [a-z0-9-]+|SUMMARY barbican failed \d+\.\d{3}", line)
        for line in output
    )
    _assert_silent(output, kubernetes.commands)


def test_successful_run_deletes_created_secret_once() -> None:
    validator, _, http, _, _ = _validator()
    validator.run()
    deletes = [request for request in http.requests if request[0] == "DELETE"]
    assert len(deletes) == 1


def test_cleanup_deletes_via_http_never_prints_values() -> None:
    validator, kubernetes, http, output, _ = _validator(
        mutations=lambda k, h: setattr(h, "active_status", "DESTROYED")
    )

    assert validator.run() == 1
    assert output[-2] == "FAIL secret-active"
    assert http.created and http.deleted
    assert [
        (request[0], request[1]) for request in http.requests if request[0] == "DELETE"
    ] == [("DELETE", f"{runtime.SECRETS_PATH}/{SECRET_ID}")]
    _assert_silent(output, kubernetes.commands)


def test_stability_ignores_deployment_resource_version_drift() -> None:
    validator, _, _, output, _ = _validator(
        mutations=lambda k, h: setattr(k, "deployment_rv_drift", True)
    )

    assert validator.run() == 0
    assert "PASS stability" in output


def test_probe_interpreter_matches_db_sync_interpreter() -> None:
    assert runtime.BARBICAN_PROBE_COMMAND[0] == "/var/lib/kolla/venv/bin/python3"
    assert runtime.BARBICAN_PROBE_COMMAND[0] == runtime.BARBICAN_DB_SYNC_COMMAND[0]
    for probe in (
        runtime.BARBICAN_API_STARTUP_PROBE,
        runtime.BARBICAN_API_READINESS_PROBE,
        runtime.BARBICAN_API_LIVENESS_PROBE,
    ):
        assert probe["exec"]["command"] == runtime.BARBICAN_PROBE_COMMAND


@pytest.mark.parametrize("app", [APP, DOTTED_APP, LONG_APP, LONG_DOTTED_APP])
def test_name_helpers_reproduce_canonical_reconcile_helpers(app: str) -> None:
    assert runtime._appliance_identity(app) == appliance_identity(app)
    assert len(runtime._appliance_identity(app)) <= runtime.DNS_LABEL_MAX_LENGTH
    for component in BARBICAN_COMPONENTS:
        reproduced = runtime._appliance_resource_name(app, component)
        canonical = appliance_resource_name(app, component)
        assert reproduced == canonical
        assert len(reproduced) <= runtime.DNS_LABEL_MAX_LENGTH
        assert re.fullmatch(r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?", reproduced)


@pytest.mark.parametrize("app", [DOTTED_APP, LONG_APP, LONG_DOTTED_APP])
def test_success_with_long_or_dotted_app_name(app: str) -> None:
    validator, kubernetes, http, output, _ = _validator(app)

    assert validator.identity == appliance_identity(app)
    assert validator.run() == 0
    assert output[: len(PASS_STAGES)] == [f"PASS {stage}" for stage in PASS_STAGES]
    assert re.fullmatch(r"SUMMARY barbican passed \d+\.\d{3}", output[-1])
    assert http.created and http.deleted
    _assert_silent(output, kubernetes.commands)
    assert (
        "kubectl",
        "--context",
        CONTEXT,
        "--namespace",
        NAMESPACE,
        "get",
        "deployment",
        appliance_resource_name(app, runtime.API_COMPONENT),
        "-o",
        "json",
    ) in kubernetes.commands
    assert any(
        isinstance(item, str)
        and item.startswith(f"{APPLIANCE_LABEL}={appliance_identity(app)},")
        for command in kubernetes.commands
        for item in command
    )
    auth = next(
        request
        for request in http.requests
        if request[0] == "POST" and request[1] == runtime.AUTH_PATH
    )
    assert json.loads(auth[3]) == EXPECTED_AUTH_BODY


@pytest.mark.parametrize("value", SENTINELS)
def test_registry_blocks_every_encoded_form(value: str) -> None:
    registry = runtime.CredentialRegistry()
    registry.register(value)
    for form in _forms(value):
        with pytest.raises(runtime.ValidationFailure, match="secret-leak"):
            registry.audit(f"PASS {form}")


def _cli_arguments(**overrides: Any) -> list[str]:
    values: dict[str, Any] = {
        "--context": CONTEXT,
        "--namespace": NAMESPACE,
        "--app-name": APP,
        "--host": HOST,
        "--run": True,
    }
    values.update(overrides)
    arguments: list[str] = []
    for key, value in values.items():
        if value is None:
            continue
        arguments.append(key)
        if value is True:
            continue
        if isinstance(value, list):
            arguments.extend(str(item) for item in value)
        else:
            arguments.append(str(value))
    return arguments


@pytest.mark.parametrize(
    "overrides",
    [
        {"--run": None},
        {"--context": ""},
        {"--app-name": "Bad Name"},
        {"--app-name": "app."},
        {"--app-name": "-lead"},
        {"--app-name": "a" * 254},
        {"--host": "https://appliance.example.test"},
        {"--host": "no dots here"},
        {"--timeout": [0]},
        {"--timeout": [9999]},
        {"--poll-interval": [0.0]},
        {"--poll-interval": [500.0]},
        {},
    ],
    ids=[
        "missing-run",
        "empty-context",
        "bad-app",
        "trailing-dot-app",
        "leading-hyphen-app",
        "too-long-app",
        "scheme-host",
        "invalid-host",
        "timeout-low",
        "timeout-high",
        "poll-low",
        "poll-high",
        "missing-arguments",
    ],
)
def test_cli_is_silent_and_rejected(
    overrides: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = _cli_arguments(**overrides) if overrides else []

    assert runtime.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == "FAIL cli\n"
    assert captured.err == ""


@pytest.mark.parametrize("app", [DOTTED_APP, LONG_APP, "a" * 253])
def test_cli_accepts_dns_subdomain_app_names(
    app: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(self: runtime.Validator) -> int:
        captured["app"] = self.app
        return 0

    monkeypatch.setattr(runtime.Validator, "run", fake_run)

    assert runtime.main(_cli_arguments(**{"--app-name": app})) == 0
    assert captured == {"app": app}


def test_cli_accepts_bounded_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(self: runtime.Validator) -> int:
        captured["timeout"] = self.timeout
        captured["poll_interval"] = self.poll_interval
        return 0

    monkeypatch.setattr(runtime.Validator, "run", fake_run)

    assert (
        runtime.main(_cli_arguments(**{"--timeout": [120], "--poll-interval": [1.5]}))
        == 0
    )
    assert captured == {"timeout": 120, "poll_interval": 1.5}
