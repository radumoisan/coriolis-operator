import base64
import contextlib
import importlib.util
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "validate-coriolis-logging-qualification.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_coriolis_logging_qualification", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
qualification = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = qualification
SPEC.loader.exec_module(qualification)


def test_manifests_pin_released_contract_and_distinct_hosts() -> None:
    app = qualification.application_manifest("qualification", "target")
    source = app["spec"]["source"]
    assert source["repoURL"] == "cr.virtomat.io/virtomat"
    assert source["chart"] == "coriolis/helm/coriolis-operator"
    assert source["targetRevision"] == "0.5.49"
    assert source["helm"]["releaseName"] == "qualification"
    assert source["helm"]["skipCrds"] is True
    assert "valuesObject" not in source["helm"]
    values = json.loads(source["helm"]["values"])
    assert values["image"]["tag"] == "0.5.49"
    assert values["image"]["pullPolicy"] == "IfNotPresent"
    assert values["imagePullSecrets"] == [{"name": "regcred"}]
    assert values["fullnameOverride"] == "coriolis-operator"
    assert values["containerSecurityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
    }
    assert values["liveness"] == {
        "port": 8080,
        "path": "/healthz",
        "initialDelaySeconds": 10,
        "periodSeconds": 10,
        "timeoutSeconds": 1,
        "failureThreshold": 3,
    }
    assert app["spec"]["destination"]["namespace"] == "target"
    assert app["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}

    appliance = qualification.appliance_manifest("app-a", "a.example.test")
    spec = appliance["spec"]
    assert appliance["apiVersion"] == "coriolis.cloudbase.it/v1alpha1"
    assert spec["profile"] == "core" and spec["version"] == "2603.4"
    assert spec["storage"]["mariadb"] == {
        "storageClassName": "local-path",
        "size": "10Gi",
    }
    assert spec["storage"]["rabbitmq"] == {
        "storageClassName": "local-path",
        "size": "1Gi",
    }
    assert spec["resources"]["mariadb"]["limits"] == {"cpu": "1", "memory": "1Gi"}
    assert spec["logging"]["retentionHours"] == 1
    assert spec["logging"]["storage"]["loki"] == {
        "storageClassName": "local-path",
        "size": "10Gi",
    }
    assert spec["logging"]["resources"]["loki"] == {
        "requests": {"cpu": "250m", "memory": "512Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    }
    assert spec["ingress"]["host"] == "a.example.test"
    assert spec["ingress"]["tls"] == {
        "mode": "certManager",
        "clusterIssuer": "letsencrypt",
    }
    fixture = qualification.tls_fixture_manifest("b.example.test", "a.example.test")
    assert fixture == {
        "apiVersion": "cert-manager.io/v1",
        "kind": "Certificate",
        "metadata": {"name": "logging-isolation-qualification-tls"},
        "spec": {
            "secretName": "logging-isolation-qualification-tls",
            "issuerRef": {"name": "letsencrypt", "kind": "ClusterIssuer"},
            "commonName": "a.example.test",
            "dnsNames": ["a.example.test", "b.example.test"],
        },
    }
    existing_secret = qualification.appliance_manifest(
        "app-a", "a.example.test", "logging-isolation-qualification-tls"
    )
    assert existing_secret["spec"]["ingress"]["tls"] == {
        "mode": "existingSecret",
        "tlsSecretName": "logging-isolation-qualification-tls",
    }


def _ready_application() -> dict[str, object]:
    return {"status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}}


def _ready_deployment() -> dict[str, object]:
    return {"status": {"readyReplicas": 1, "availableReplicas": 1}}


def _ready_appliance() -> dict[str, object]:
    return {
        "metadata": {"generation": 7},
        "status": {
            "acceptedVersion": "2603.4",
            "observedGeneration": 7,
            "conditions": [
                {"type": key, "status": "True"}
                for key in ("Accepted", "Reconciled", "Ready", "LoggingReady")
            ],
        },
    }


def _retention_output() -> str:
    stages = [
        f"PASS {stage} 0.000" for stage in sorted(qualification._RETENTION_STAGES)
    ]
    return (
        "\n".join(
            [
                *stages,
                "PASS candidate-count 1",
                "PASS deletion-marker-count 0",
                "CLEANUP observer",
                "SUMMARY retention-formal passed 0.000",
            ]
        )
        + "\n"
    )


def _workloads() -> dict[str, object]:
    return {
        "items": [
            {"metadata": {"name": "one"}, "status": {"replicas": 1, "readyReplicas": 1}}
        ]
    }


def test_lifecycle_is_scoped_value_silent_and_cleans_in_order() -> None:
    calls: list[tuple[str, ...]] = []
    command_timeouts: list[int] = []
    inputs: list[tuple[tuple[str, ...], str]] = []
    child_timeouts: list[int] = []
    output: list[str] = []
    secret = "TOP_SECRET_DO_NOT_PRINT"
    marker_values = iter(
        ("marker-a", "marker-b", "marker-c", "marker-d", "marker-e", "marker-f")
    )
    ws_markers = iter(("marker-c", "marker-d", "marker-e", "marker-f"))
    gateway_markers: dict[str, list[str]] = {"app-a": [], "app-b": []}
    ws_clients: list[object] = []
    http_urls: list[str] = []
    redirect_urls: list[str] = []
    network_stages: list[str] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        calls.append(tuple(command))
        command_timeouts.append(timeout)
        if "get" not in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if "--ignore-not-found" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        kind = command[command.index("get") + 1]
        if kind == "namespace":
            return subprocess.CompletedProcess(command, 0, "", "")
        if kind == "secret":
            name = command[command.index("secret") + 1]
            if (
                name == qualification.TLS_FIXTURE_NAME
                and "jsonpath={.metadata.name}" in command
            ):
                network_stages.append(f"secret:{name}")
                return subprocess.CompletedProcess(command, 0, name, "")
            encoded = base64.b64encode(secret.encode()).decode()
            data = (
                {"read_password": encoded, "write_password": encoded}
                if name.endswith("logging-credentials")
                else {"coriolis_keystone_password": encoded}
            )
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "metadata": {"name": "regcred", "namespace": "coriolis"},
                        "data": data,
                    }
                ),
                "",
            )
        if kind == "application":
            return subprocess.CompletedProcess(
                command, 0, json.dumps(_ready_application()), ""
            )
        if kind == "deployment":
            return subprocess.CompletedProcess(
                command, 0, json.dumps(_ready_deployment()), ""
            )
        if kind == "coriolisappliance":
            app = command[command.index("coriolisappliance") + 1]
            payload = _ready_appliance()
            payload["metadata"]["uid"] = f"uid-{app}"
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if kind == "deployments,statefulsets":
            return subprocess.CompletedProcess(command, 0, json.dumps(_workloads()), "")
        if kind == "pvc":
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"items": [{"spec": {"volumeName": "pv-target"}}]}),
                "",
            )
        if kind in {"ingress", "certificate"}:
            if kind == "certificate" and "--all-namespaces" not in command:
                network_stages.append(
                    f"certificate:{command[command.index('certificate') + 1]}"
                )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {
                            "status": {
                                "conditions": [{"type": "Ready", "status": "True"}]
                            }
                        }
                    ),
                    "",
                )
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"items": []}), ""
            )
        if kind == "storageclass":
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"metadata": {}}), ""
            )
        if kind == "clusterissuer":
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
                ),
                "",
            )
        if kind == "coriolisappliances":
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"items": []}), ""
            )
        if kind in {
            "deployments",
            "statefulsets",
            "services",
            "ingresses",
            "configmaps",
            "events",
            "certificates",
        }:
            return subprocess.CompletedProcess(
                command, 0, json.dumps({"items": []}), ""
            )
        if kind == "pods":
            if "-l" in command:
                if command[command.index("--namespace") + 1] == "ingress-nginx":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps(
                            {
                                "items": [
                                    {
                                        "metadata": {"name": "ingress-controller"},
                                        "spec": {
                                            "containers": [{"name": "controller"}]
                                        },
                                    }
                                ]
                            }
                        ),
                        "",
                    )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {
                            "items": [
                                {
                                    "status": {
                                        "containerStatuses": [
                                            {
                                                "name": "operator",
                                                "imageID": "registry@"
                                                + qualification.OPERATOR_DIGEST,
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    ),
                    "",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "audit-pod"},
                                "spec": {"containers": [{"name": "main"}]},
                            }
                        ]
                    }
                ),
                "",
            )
        if kind == "pv":
            if "-o" in command:
                return subprocess.CompletedProcess(
                    command, 0, json.dumps({"items": []}), ""
                )
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def input_runner(
        command: object, data: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        inputs.append((tuple(command), data))
        manifest = json.loads(data)
        if manifest["kind"] == "Certificate":
            network_stages.append("tls-fixture-apply")
        elif manifest["kind"] == "CoriolisAppliance":
            network_stages.append(f"{manifest['metadata']['name']}-apply")
        output = "secret/regcred configured" if '"kind":"Secret"' in data else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    def child(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        calls.append(tuple(command))
        child_timeouts.append(timeout)
        stdout = (
            "\n".join(qualification._LOGGING_CHILD_OUTPUT) + "\n"
            if "logging-runtime" in str(command)
            else _retention_output()
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def http(
        method: str,
        url: str,
        headers: object,
        body: bytes | None,
        timeout: int,
    ) -> qualification.HttpResponse:
        assert isinstance(headers, dict)
        http_urls.append(url)
        if url.endswith("/identity/auth/tokens"):
            host = url.split("/")[2]
            network_stages.append(f"identity:{host}")
            return qualification.HttpResponse(
                201, {"X-Subject-Token": f"token-{host}"}, b"{}"
            )
        if url.startswith("http://gateway-"):
            app = urllib.parse.urlsplit(url).netloc.removeprefix("gateway-")
            expected_tenant = f"coriolis-uid-{app}"
            authorization = headers.get("Authorization", "")
            decoded = base64.b64decode(authorization.removeprefix("Basic ")).decode()
            tenant, _, password = decoded.partition(":")
            if tenant != expected_tenant or password != secret:
                return qualification.HttpResponse(401, {}, b"")
            if method == "POST":
                assert body is not None
                marker = json.loads(body)["streams"][0]["stream"]["matrix_marker"]
                gateway_markers[app].append(marker)
                return qualification.HttpResponse(204, {}, b"")
            marker = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["query"][0]
            marker = marker.split('matrix_marker="', 1)[1].split('"', 1)[0]
            values = (
                [["1", f"matrix-{marker}"]] if marker in gateway_markers[app] else []
            )
            return qualification.HttpResponse(
                200, {}, json.dumps({"data": {"result": [{"values": values}]}}).encode()
            )
        parsed = urllib.parse.urlsplit(url)
        host = parsed.netloc
        app = "app-a" if host == "a.example.test" else "app-b"
        query = urllib.parse.parse_qs(parsed.query)
        token = f"token-{host}"
        if parsed.path == "/logs":
            if not headers and not query:
                return qualification.HttpResponse(401, {}, b"")
            if headers.get("X-Auth-Token") == "invalid-token":
                return qualification.HttpResponse(401, {}, b"")
            if headers.get("X-Auth-Token") == token or query.get("auth_token") == [
                token
            ]:
                return qualification.HttpResponse(
                    200, {}, b'{"logs":[{"log_name":"coriolis-api"}]}'
                )
            return qualification.HttpResponse(401, {}, b"")
        if parsed.path.endswith("unknown-component"):
            return qualification.HttpResponse(404, {}, b"")
        if query.get("start_date") == ["0"]:
            return qualification.HttpResponse(400, {}, b"")
        body = "\n".join(f"matrix-{marker}" for marker in gateway_markers[app]).encode()
        content = body or b"initial"
        headers_out = {
            "Cache-Control": "no-store",
            "Content-Disposition": "attachment; filename=coriolis-api.log",
        }
        if query.get("disable_chunked") == ["true"]:
            headers_out["Content-Length"] = str(len(content))
        return qualification.HttpResponse(200, headers_out, content)

    def redirect_http(url: str, timeout: int) -> qualification.HttpResponse:
        redirect_urls.append(url)
        host = urllib.parse.urlsplit(url).netloc
        network_stages.append(f"redirect:{host}")
        return qualification.HttpResponse(
            308, {"Location": f"https://{host}/logs"}, b""
        )

    @contextlib.contextmanager
    def forward(command: object, timeout: int, clock: object, sleeper: object):
        assert isinstance(command, list | tuple)
        target = next(item for item in command if item.startswith("svc/"))
        yield f"http://gateway-{target.removeprefix('svc/').removesuffix('-gateway')}"

    class FakeWs:
        def __init__(self, marker: str) -> None:
            self.marker = marker
            self.closed = False
            self.frames = [json.dumps({"message": f"matrix-{marker}"}), None]

        def recv(self) -> str | None:
            return self.frames.pop(0) if self.frames else None

        def close(self) -> None:
            self.closed = True

    def ws_factory(url: str, headers: object, timeout: int) -> object:
        host = urllib.parse.urlsplit(url).netloc
        assert host in {"a.example.test", "b.example.test"}
        assert headers == {"X-Auth-Token": f"token-{host}"}
        marker = next(ws_markers)
        client = FakeWs(marker)
        ws_clients.append(client)
        return client

    validator = qualification.Validator(
        context="ctx",
        namespace="target",
        application="operator-test",
        app_a="app-a",
        host_a="a.example.test",
        app_b="app-b",
        host_b="b.example.test",
        runner=runner,
        input_runner=input_runner,
        child_runner=child,
        http=http,
        forward=forward,
        ws_factory=ws_factory,
        marker_factory=lambda: next(marker_values),
        redirect_http=redirect_http,
        clock=lambda: 1.0,
        wallclock=lambda: 1000.0,
        sleeper=lambda _: None,
        report=output.append,
    )
    assert validator.run() == 0, "\n".join(output)
    assert output[-1] == "SUMMARY logging-formal passed 0.000"
    matrix_stages = [
        "matrix-certificate-a",
        "matrix-redirect-a",
        "matrix-identity-a",
        "matrix-certificate-b",
        "matrix-redirect-b",
        "matrix-identity-b",
        "matrix-isolation",
        "matrix-public-markers",
        "matrix-public-a",
        "matrix-wss-a",
        "matrix-public-b",
        "matrix-wss-b",
        "matrix-audit",
    ]
    assert [
        line.removeprefix("PASS ") for line in output if line.startswith("PASS matrix-")
    ] == matrix_stages
    assert network_stages == [
        "tls-fixture-apply",
        "certificate:logging-isolation-qualification-tls",
        "secret:logging-isolation-qualification-tls",
        "app-a-apply",
        "app-b-apply",
        "certificate:logging-isolation-qualification-tls",
        "redirect:a.example.test",
        "identity:a.example.test",
        "certificate:logging-isolation-qualification-tls",
        "redirect:b.example.test",
        "identity:b.example.test",
    ]
    assert output.index("PASS tls-fixture") < output.index("PASS app-a")
    assert output.index("PASS tls-fixture") < output.index("PASS app-b")
    applied = [json.loads(data) for _, data in inputs]
    assert [manifest for manifest in applied if manifest["kind"] == "Certificate"] == [
        qualification.tls_fixture_manifest("a.example.test", "b.example.test")
    ]
    appliances = [
        manifest for manifest in applied if manifest["kind"] == "CoriolisAppliance"
    ]
    assert [manifest["metadata"]["name"] for manifest in appliances] == [
        "app-a",
        "app-b",
    ]
    assert all(
        manifest["spec"]["ingress"]["tls"]
        == {
            "mode": "existingSecret",
            "tlsSecretName": qualification.TLS_FIXTURE_NAME,
        }
        for manifest in appliances
    )
    assert output.count("PASS final-audit") == 1
    assert output.index("PASS retention") < output.index("PASS final-audit")
    assert secret not in "\n".join(output)
    assert "token-a.example.test" not in "\n".join(output)
    assert all(url.startswith(("https://", "http://gateway-")) for url in http_urls)
    assert redirect_urls == [
        "http://a.example.test/logs",
        "http://b.example.test/logs",
    ]
    assert all(
        "--context" in command and command[command.index("--context") + 1] == "ctx"
        for command in calls
    )
    cluster_calls = [
        command
        for command in calls
        if command[0] == "kubectl" and "--namespace" not in command
    ]
    assert cluster_calls
    assert all("--namespace" not in command for command in cluster_calls)
    assert all(
        "--namespace" in command
        for command in calls
        if command[0] == "kubectl" and command not in cluster_calls
    )
    assert {"storageclass", "clusterissuer", "pv"} <= {
        command[command.index("get") + 1]
        for command in cluster_calls
        if "get" in command
    }
    assert any(
        "application" in command
        and "operator-test" in command
        and "--ignore-not-found" in command
        for command in calls
    )
    assert any(
        "pods" in command
        and "-l" in command
        and "app.kubernetes.io/instance=operator-test" in command
        for command in calls
    )
    assert command_timeouts and set(command_timeouts) == {qualification.COMMAND_TIMEOUT}
    assert child_timeouts == [
        qualification.RECONNECT_TIMEOUT,
        qualification.RETENTION_TIMEOUT,
    ]
    assert all(secret not in " ".join(command) for command in calls)
    assert all(secret not in data for _, data in inputs if "Secret" not in data)
    copied = [json.loads(data) for _, data in inputs if '"kind":"Secret"' in data]
    assert [item["metadata"]["name"] for item in copied] == [
        "regcred",
        "coriolis-appliance-registry",
    ]
    assert all(
        all(
            value == base64.b64encode(secret.encode()).decode()
            for value in item["data"].values()
        )
        for item in copied
    )
    deletes = [command for command in calls if "delete" in command]
    assert [command[command.index("delete") + 2] for command in deletes] == [
        "app-b",
        "app-a",
        "operator-test",
        "target",
    ]
    assert not any("--force" in command or "patch" in command for command in calls)
    assert not any(
        "delete" in command
        and any(kind in command for kind in ("pvc", "pv", "secret", "certificate"))
        for command in calls
    )
    child_calls = [command for command in calls if command[0] == sys.executable]
    assert child_calls[0][-7:] == (
        "--namespace",
        "target",
        "--app",
        "app-a",
        "--host",
        "a.example.test",
        "--run",
    )
    assert child_calls[1][-11:] == (
        "--namespace",
        "target",
        "--app-name",
        "app-a",
        "--mode",
        "formal",
        "--timeout",
        "300",
        "--max-wait-minutes",
        "210",
        "--run",
    )
    assert len(ws_clients) == 4 and len({id(client) for client in ws_clients}) == 4
    assert gateway_markers["app-a"] == ["marker-a", "marker-c", "marker-d"]
    assert gateway_markers["app-b"] == ["marker-b", "marker-e", "marker-f"]
    logs = [
        command
        for command in calls
        if "logs" in command and command[command.index("--namespace") + 1] == "target"
    ]
    assert logs and all("--tail=200" in command for command in logs)
    ingress_logs = [
        command
        for command in calls
        if command[0] == "kubectl"
        and "logs" in command
        and command[command.index("--namespace") + 1] == "ingress-nginx"
    ]
    assert ingress_logs
    assert all(
        command[command.index("-c") + 1] == "controller" and "--tail=5000" in command
        for command in ingress_logs
    )
    assert any(
        command[command.index("get") + 1] == "pods"
        and command[command.index("--namespace") + 1] == "ingress-nginx"
        and command[command.index("-l") + 1] == "app.kubernetes.io/component=controller"
        for command in calls
        if command[0] == "kubectl" and "get" in command and "-l" in command
    )
    audited = {
        command[command.index("get") + 1]
        for command in calls
        if command[0] == "kubectl" and "get" in command
    }
    assert {
        "coriolisappliances",
        "deployments",
        "statefulsets",
        "pods",
        "services",
        "ingresses",
        "configmaps",
        "events",
        "certificates",
    } <= audited
    assert not any(
        kind in str(command)
        for command in calls
        for kind in ("certificaterequest", "challenge")
    )
    downloads = [
        urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        for url in http_urls
        if "/logs/coriolis-api" in url and "disable_chunked" in url
    ]
    assert downloads
    assert all(
        query["start_date"] == ["460"] and query["end_date"] == ["1060"]
        for query in downloads
    )


def test_matrix_audits_secret_values_in_http_frames_and_logs() -> None:
    secret = "MATRIX_SECRET_DO_NOT_PRINT"
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        http=lambda method, url, headers, body, timeout: qualification.HttpResponse(
            200, {}, secret.encode()
        ),
        runner=lambda command, timeout: subprocess.CompletedProcess(
            command, 0, secret, ""
        ),
    )
    validator.registry.register_runtime_secret(secret)
    for action in (
        lambda: validator._http("GET", "https://a.test/logs", {}),
        lambda: validator._expect_ws(
            type("Ws", (), {"recv": lambda self: secret})(), "x"
        ),
        lambda: validator._checked(
            "matrix",
            validator._kubectl("ns", "logs", "pod", "-c", "main", "--tail=200"),
        ),
    ):
        try:
            action()
        except qualification.ValidationFailure as error:
            assert error.stage == "secret-leak"
        else:
            raise AssertionError("matrix audit accepted a registered secret")


def test_ingress_controller_audit_uses_exact_scope_and_detects_secret() -> None:
    secret = "INGRESS_TOKEN_DO_NOT_PRINT"
    calls: list[tuple[str, ...]] = []

    def runner(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        assert isinstance(command, list | tuple)
        calls.append(tuple(command))
        if "get" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "ingress-controller"},
                                "spec": {"containers": [{"name": "controller"}]},
                            }
                        ]
                    }
                ),
                "",
            )
        return subprocess.CompletedProcess(command, 0, secret, "")

    validator = qualification.Validator(
        context="ctx",
        namespace="target",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        runner=runner,
    )
    validator.registry.register_runtime_secret(secret)
    try:
        validator._audit_ingress_controller_logs()
    except qualification.ValidationFailure as error:
        assert error.stage == "secret-leak"
    else:
        raise AssertionError("ingress controller secret was not detected")
    assert calls == [
        (
            "kubectl",
            "--context",
            "ctx",
            "--namespace",
            "ingress-nginx",
            "get",
            "pods",
            "-l",
            "app.kubernetes.io/component=controller",
            "-o",
            "json",
        ),
        (
            "kubectl",
            "--context",
            "ctx",
            "--namespace",
            "ingress-nginx",
            "logs",
            "ingress-controller",
            "-c",
            "controller",
            "--tail=5000",
        ),
    ]


def test_websocket_poll_handles_unrelated_timeout_and_duplicate_frames() -> None:
    now = [0.0]
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        command_timeout=1,
        clock=lambda: now.__setitem__(0, now[0] + 0.1) or now[0],
        sleeper=lambda _: None,
    )

    class Ws:
        def __init__(self, frames: list[str | None]) -> None:
            self.frames = frames

        def recv(self) -> str | None:
            return self.frames.pop(0) if self.frames else None

    class TimeoutWs(Ws):
        def __init__(self, frames: list[str | None]) -> None:
            super().__init__(frames)
            self.timeouts: list[float] = []

        def recv(self) -> str | None:
            if self.frames:
                return super().recv()
            raise TimeoutError

        def settimeout(self, timeout: float) -> None:
            self.timeouts.append(timeout)

    timed = TimeoutWs(
        [json.dumps({"message": "ordinary log"}), json.dumps({"message": "matrix-a"})]
    )
    validator._expect_ws(timed, "a")
    assert timed.timeouts == [0.2]
    try:
        validator._expect_ws(Ws([json.dumps({"message": "matrix-a"})] * 2), "a")
    except qualification.ValidationFailure as error:
        assert error.stage == "matrix"
    else:
        raise AssertionError("duplicate WebSocket marker was accepted")
    try:
        validator._expect_ws(TimeoutWs([]), "a")
    except qualification.ValidationFailure as error:
        assert error.stage == "matrix"
    else:
        raise AssertionError("WebSocket timeout before marker was accepted")


def test_gateway_marker_visibility_polls_without_repush() -> None:
    calls = [0, 1]
    sleeps: list[float] = []
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        clock=lambda: 0.0,
        sleeper=sleeps.append,
    )
    identity = qualification.MatrixIdentity("a", "a.test", "uid", "r", "w", "k", "t")
    validator._marker_count = lambda base_url, identity, marker: calls.pop(0)
    validator._wait_marker_visible("http://gateway-a", identity, "marker")
    assert sleeps == [qualification.POLL_SECONDS]


def test_matrix_stage_maps_generic_failure_and_preserves_secret_leak() -> None:
    output: list[str] = []
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        report=output.append,
    )

    def generic_failure() -> None:
        raise qualification.ValidationFailure("matrix")

    def secret_failure() -> None:
        raise qualification.ValidationFailure("secret-leak")

    try:
        validator._matrix_stage("matrix-identity-a", generic_failure)
    except qualification.ValidationFailure as error:
        assert error.stage == "matrix-identity-a"
    else:
        raise AssertionError("generic matrix failure was not mapped")
    try:
        validator._matrix_stage("matrix-audit", secret_failure)
    except qualification.ValidationFailure as error:
        assert error.stage == "secret-leak"
    else:
        raise AssertionError("secret leak was not preserved")
    assert output == []


def test_fast_and_formal_modes_branch_retention_and_cleanup() -> None:
    def run_mode(mode: str) -> tuple[list[str], list[str], list[tuple[str, ...]]]:
        output: list[str] = []
        events: list[str] = []
        children: list[tuple[str, ...]] = []
        validator = qualification.Validator(
            context="ctx",
            namespace="ns",
            application="app",
            app_a="a",
            host_a="a.test",
            app_b="b",
            host_b="b.test",
            mode=mode,
            clock=lambda: 1.0,
            report=output.append,
        )
        validator._preflight = lambda: None
        validator._create_namespace = lambda: setattr(
            validator, "namespace_created", True
        )
        validator._copy_secret = lambda name: None
        validator._apply = lambda stage, namespace, manifest: None
        validator._wait = lambda stage, timeout, check: None
        validator._record_pvs = lambda: None
        validator._matrix = lambda: events.append("matrix")

        def child(
            stage: str,
            command: tuple[str, ...],
            timeout: int,
            valid: object,
        ) -> None:
            events.append(stage)
            children.append((stage, *command, str(timeout)))

        validator._run_child = child
        validator._audit_matrix_surfaces = lambda: events.append("audit")
        validator._cleanup = lambda: events.append("cleanup")
        assert validator.run() == 0
        return output, events, children

    fast_output, fast_events, fast_children = run_mode("fast")
    assert fast_events == ["matrix", "reconnect", "audit", "cleanup"]
    assert fast_output[-1] == "SUMMARY logging-fast passed 0.000"
    assert "PASS retention" not in fast_output
    assert not [entry for entry in fast_children if entry[0] == "retention"]

    formal_output, formal_events, formal_children = run_mode("formal")
    assert formal_events == ["matrix", "reconnect", "retention", "audit", "cleanup"]
    assert formal_output[-1] == "SUMMARY logging-formal passed 0.000"
    retention = [entry for entry in formal_children if entry[0] == "retention"]
    assert len(retention) == 1
    command = retention[0][1:-1]
    assert command[command.index("--max-wait-minutes") + 1] == "210"
    assert command[command.index("--timeout") + 1] == "300"
    assert retention[0][-1] == str(qualification.RETENTION_TIMEOUT)
    assert qualification.RETENTION_CHILD_MAX_WAIT_MINUTES == 210
    assert qualification.RETENTION_CHILD_COMMAND_TIMEOUT == 5 * 60
    assert qualification.RETENTION_TIMEOUT == 4 * 60 * 60


def test_child_output_and_cleanup_failure_are_silent_and_do_not_force() -> None:
    output: list[str] = []
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        report=output.append,
    )
    assert validator._logging_child_output(list(qualification._LOGGING_CHILD_OUTPUT))
    assert not validator._logging_child_output(["PASS complete", "secret"])
    assert validator._retention_child_output(_retention_output().splitlines())
    assert not validator._retention_child_output(
        ["PASS unknown-stage 1.000", "SUMMARY retention-formal passed 1.000"]
    )

    calls: list[tuple[str, ...]] = []
    validator.namespace_created = True
    validator.created_apps = ["a", "b"]
    validator.cleanup_timeout = 0
    validator.runner = lambda command, timeout: (
        calls.append(tuple(command))
        or subprocess.CompletedProcess(command, 0, "present", "")
    )
    assert validator.run() == 1
    assert output == ["FAIL cleanup-app-b"]
    assert not any(
        "--force" in command or "patch" in command or "finalizer" in command
        for command in calls
    )
    assert not any("delete" in command and "a" in command for command in calls)


def test_secret_in_child_output_becomes_a_fixed_failure_without_leakage() -> None:
    secret = "CHILD_SECRET_DO_NOT_PRINT"
    output: list[str] = []
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        child_runner=lambda command, timeout: subprocess.CompletedProcess(
            command, 0, secret, ""
        ),
        report=output.append,
    )
    validator.registry.register_secret(base64.b64encode(secret.encode()).decode())

    def fail_with_secret() -> None:
        validator._run_child("reconnect", ["child"], 1, lambda lines: True)

    validator._run_body = fail_with_secret
    validator._cleanup = lambda: None
    assert validator.run() == 1
    assert output == ["FAIL secret-leak"]
    assert secret not in "\n".join(output)


def _failing_child_validator(
    returncode: int,
    stdout: str,
    stderr: str = "",
) -> qualification.Validator:
    def child(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        child_runner=child,
        report=lambda line: None,
    )


def test_run_child_propagates_fixed_child_failure_stage() -> None:
    reported: list[str] = []
    validator = _failing_child_validator(
        1, "PASS tenant\nPASS record-a\nFAIL record-b\n"
    )
    validator._raw_report = reported.append
    try:
        validator._run_child("reconnect", ["child"], 1, lambda lines: True)
    except qualification.ValidationFailure as error:
        assert error.stage == "reconnect-record-b"
    else:
        raise AssertionError("fixed child failure stage was not propagated")
    assert reported == []
    assert qualification._STAGE.fullmatch("reconnect-record-b")


def _retention_failure_envelope(fail_line: str = "FAIL formal-retention") -> str:
    return "\n".join(
        [
            "PASS cr-uid 0.012",
            "PASS query-persisted 88.250",
            "PASS retained-resources 3.500",
            fail_line,
            "DIAGNOSTIC-ONLY mode patches the Loki ConfigMap and never restores it.",
            "The final CoriolisAppliance, Application, and namespace are caller-owned",
            "and are never deleted by this validator.",
            "SUMMARY retention-formal failed 185.900",
            "",
        ]
    )


def test_run_child_propagates_retention_failure_envelope_stage() -> None:
    reported: list[str] = []
    validator = _failing_child_validator(1, _retention_failure_envelope())
    validator._raw_report = reported.append
    try:
        validator._run_child(
            "retention", ["child"], 1, validator._retention_child_output
        )
    except qualification.ValidationFailure as error:
        assert error.stage == "retention-formal-retention"
    else:
        raise AssertionError("retention child failure envelope was not propagated")
    assert reported == []
    assert qualification._STAGE.fullmatch("retention-formal-retention")


def test_run_child_keeps_generic_stage_for_ambiguous_retention_failures() -> None:
    ambiguous_cases: list[tuple[str, str]] = [
        (
            "FAIL formal-retention\nFAIL config-release\n",
            "multiple valid FAIL lines",
        ),
        (
            "FAIL formal_retention\n",
            "dynamic underscore stage inside envelope",
        ),
        (
            "FAIL formal-retention 185.900\n",
            "trailing timing after stage",
        ),
    ]
    for fail_line, reason in ambiguous_cases:
        envelope = _retention_failure_envelope(fail_line)
        validator = _failing_child_validator(1, envelope)
        try:
            validator._run_child(
                "retention", ["child"], 1, validator._retention_child_output
            )
        except qualification.ValidationFailure as error:
            assert error.stage == "retention", reason
        else:
            raise AssertionError(f"ambiguous child failure collapsed: {reason}")


def test_run_child_audits_secrets_before_propagating_failure_stage() -> None:
    validator = _failing_child_validator(
        1, "PASS tenant\nCHILD_TOKEN_VALUE\nFAIL record-b\n"
    )
    validator.registry.register_runtime_secret("CHILD_TOKEN_VALUE")
    try:
        validator._run_child("reconnect", ["child"], 1, lambda lines: True)
    except qualification.ValidationFailure as error:
        assert error.stage == "secret-leak"
    else:
        raise AssertionError("registered Secret in child output was not detected")


def test_run_child_keeps_generic_stage_for_untrusted_child_failures() -> None:
    generic_cases: list[tuple[int, str, str]] = [
        (1, "FAIL Record-B\n", "uppercase stage"),
        (1, "FAIL record_b\n", "underscore stage"),
        (1, "FAIL rec/ord\n", "path separators"),
        (1, "FAIL ../../etc\n", "dynamic traversal"),
        (1, "FAILrecord-b\n", "missing separator"),
        (1, "failed record-b\n", "wrong keyword"),
        (1, "note: FAIL record-b\n", "prefixed line"),
        (1, "PASS continuity\n", "missing failure line"),
        (1, "", "empty output"),
        (1, "FAIL record-b\n", "stderr noise with a valid failure line"),
        (0, "FAIL record-b\n", "zero exit with a failure line"),
    ]
    for returncode, stdout, reason in generic_cases:
        stderr = "unexpected noise" if "stderr" in reason else ""
        validator = _failing_child_validator(returncode, stdout, stderr)
        try:
            validator._run_child("reconnect", ["child"], 1, lambda lines: False)
        except qualification.ValidationFailure as error:
            assert error.stage == "reconnect", reason
        else:
            raise AssertionError(f"child failure collapsed to success: {reason}")


def test_run_child_generic_stage_covers_timeout_and_success_mismatch() -> None:
    def explode(command: object, timeout: int) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["child"], timeout)

    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        child_runner=explode,
    )
    try:
        validator._run_child("reconnect", ["child"], 1, lambda lines: True)
    except qualification.ValidationFailure as error:
        assert error.stage == "reconnect"
    else:
        raise AssertionError("child timeout was not reported as the parent stage")

    matched = _failing_child_validator(
        0, "\n".join(qualification._LOGGING_CHILD_OUTPUT) + "\n"
    )
    matched._run_child("reconnect", ["child"], 1, matched._logging_child_output)
    mismatched = _failing_child_validator(0, "PASS tenant\n", "leaked stderr")
    try:
        mismatched._run_child(
            "reconnect", ["child"], 1, mismatched._logging_child_output
        )
    except qualification.ValidationFailure as error:
        assert error.stage == "reconnect"
    else:
        raise AssertionError("success-output mismatch was not reported")


def test_appliance_readiness_requires_generation_and_accepted_version() -> None:
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
    )
    payload = _ready_appliance()
    validator._json = lambda stage, namespace, *arguments: payload
    assert validator._appliance_ready("a")

    payload["status"]["observedGeneration"] = 6
    assert not validator._appliance_ready("a")
    payload["status"]["observedGeneration"] = 7
    payload["status"]["acceptedVersion"] = "2603.5"
    assert not validator._appliance_ready("a")


def _tls_fixture_order(reason: str) -> dict[str, object]:
    return {"status": {"state": "invalid", "reason": reason}}


def _tls_fixture_dns_challenge(state: str, reason: str) -> dict[str, object]:
    return {
        "spec": {"type": "dns-01", "dnsName": "a.test"},
        "status": {"state": state, "reason": reason},
    }


def _tls_fixture_ready_issuer() -> dict[str, object]:
    return {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}


def _classify_tls_fixture(
    *,
    certificate: dict[str, object] | None = None,
    requests: tuple[dict[str, object], ...] = (),
    orders: tuple[dict[str, object], ...] = (),
    challenges: tuple[dict[str, object], ...] = (),
    issuer: dict[str, object] | None = None,
) -> str:
    return qualification._classify_tls_fixture_failure(
        certificate
        or {
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "Transient"}
                ]
            }
        },
        list(requests),
        list(orders),
        list(challenges),
        issuer if issuer is not None else _tls_fixture_ready_issuer(),
    )


def test_tls_fixture_classifier_maps_four_fixed_categories() -> None:
    rate_reason = (
        "Error finalizing Order: 429 urn:ietf:params:acme:error:rateLimited: "
        "too many certificates already issued for the exact set of identifiers"
    )
    assert _classify_tls_fixture(orders=(_tls_fixture_order(rate_reason),)) == (
        "tls-fixture-certificate-rate-limited"
    )
    assert (
        _classify_tls_fixture(
            orders=(_tls_fixture_order(rate_reason),),
            challenges=(_tls_fixture_dns_challenge("invalid", "Ran out of DNS time"),),
            issuer={"status": {"conditions": [{"type": "Ready", "status": "False"}]}},
        )
        == "tls-fixture-certificate-rate-limited"
    )
    assert (
        _classify_tls_fixture(
            challenges=(
                _tls_fixture_dns_challenge(
                    "errored", "Ran out of DNS propagation and checking delay time"
                ),
            )
        )
        == "tls-fixture-certificate-dns"
    )
    assert (
        _classify_tls_fixture(
            challenges=(
                _tls_fixture_dns_challenge(
                    "pending",
                    "Failed to propagate DNS record: resolver did not return TXT",
                ),
            )
        )
        == "tls-fixture-certificate-dns"
    )
    failed_request = {
        "status": {
            "conditions": [{"type": "Ready", "status": "False", "reason": "Failed"}]
        }
    }
    assert (
        _classify_tls_fixture(
            requests=(failed_request,),
            issuer={"status": {"conditions": [{"type": "Ready", "status": "False"}]}},
        )
        == "tls-fixture-certificate-issuer"
    )
    assert _classify_tls_fixture(requests=(failed_request,)) == (
        "tls-fixture-certificate-issuer"
    )
    assert (
        _classify_tls_fixture(
            challenges=(
                _tls_fixture_dns_challenge(
                    "pending", "Waiting for DNS-01 challenge propagation"
                ),
            )
        )
        == "tls-fixture-certificate-pending"
    )
    assert _classify_tls_fixture() == "tls-fixture-certificate-pending"
    for category in (
        "tls-fixture-certificate-rate-limited",
        "tls-fixture-certificate-dns",
        "tls-fixture-certificate-issuer",
        "tls-fixture-certificate-pending",
    ):
        assert qualification._STAGE.fullmatch(category)


def test_tls_fixture_certificate_failure_classifies_before_cleanup() -> None:
    output: list[str] = []
    events: list[str] = []
    raw_reason = (
        "Error finalizing Order: 429 urn:ietf:params:acme:error:rateLimited: "
        "too many certificates already issued for the exact set of identifiers"
    )
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        clock=lambda: 1.0,
        sleeper=lambda _: None,
        report=output.append,
    )

    def wait(stage: str, timeout: int, check: object) -> None:
        del timeout, check
        if stage == "tls-fixture-certificate":
            raise qualification.ValidationFailure(stage)

    def namespace_reader(stage: str, namespace: str, *arguments: str) -> dict:
        del stage
        events.append("diagnose")
        assert namespace == "ns"
        if arguments[0] == "order":
            return {"items": [{"status": {"state": "invalid", "reason": raw_reason}}]}
        if arguments[0] == "certificate":
            return {"status": {"conditions": [{"type": "Ready", "status": "False"}]}}
        return {"items": []}

    def cluster_reader(stage: str, *arguments: str) -> dict:
        del stage, arguments
        events.append("diagnose")
        return _tls_fixture_ready_issuer()

    validator._preflight = lambda: None
    validator._create_namespace = lambda: setattr(validator, "namespace_created", True)
    validator._copy_secret = lambda name: None
    validator._apply = lambda stage, namespace, manifest: None
    validator._wait = wait
    validator._json = namespace_reader
    validator._cluster_json = cluster_reader
    validator._cleanup = lambda: events.append("cleanup")
    assert validator.run() == 1
    assert output == [
        "PASS namespace",
        "PASS secrets",
        "FAIL tls-fixture-certificate-rate-limited",
    ]
    assert events == ["diagnose"] * 5 + ["cleanup"]
    joined = "\n".join(output)
    assert raw_reason not in joined
    assert "429" not in joined
    assert "rateLimited" not in joined


def test_tls_fixture_diagnosis_read_failures_fall_back_to_pending() -> None:
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
    )
    for failure in ("matrix", "preflight"):

        def reader(stage: str, namespace: str, *arguments: str) -> dict:
            del stage, namespace, arguments
            raise qualification.ValidationFailure(failure)

        validator._json = reader
        assert (
            validator._diagnose_tls_fixture_failure()
            == "tls-fixture-certificate-pending"
        )

    def leak_reader(stage: str, namespace: str, *arguments: str) -> dict:
        del stage, namespace, arguments
        raise qualification.ValidationFailure("secret-leak")

    validator._json = leak_reader
    try:
        validator._diagnose_tls_fixture_failure()
    except qualification.ValidationFailure as error:
        assert error.stage == "secret-leak"
    else:
        raise AssertionError("diagnosis replaced a secret leak with a generic failure")


def test_secret_registry_ignores_metadata_but_audits_encoded_data() -> None:
    registry = qualification.SecretRegistry()
    secret = "password/with?encoding"
    encoded = base64.b64encode(secret.encode()).decode()
    registry.register_secret(encoded)
    registry.audit("secret/regcred configured")
    registry.audit('{"metadata":{"name":"regcred"}}')
    for exposed in (
        secret,
        encoded,
        base64.urlsafe_b64encode(secret.encode()).decode(),
        "password%2Fwith%3Fencoding",
        '"password/with?encoding"',
    ):
        try:
            registry.audit(exposed)
        except qualification.ValidationFailure as error:
            assert error.stage == "secret-leak"
        else:
            raise AssertionError("registered Secret value was not detected")
    docker_leaf = "docker-auth-secret"
    docker_host = "registry.example.test"
    docker_payload = json.dumps({"auths": {docker_host: {"auth": docker_leaf}}})
    registry.register_secret(base64.b64encode(docker_payload.encode()).decode())
    registry.audit(docker_host)
    try:
        registry.audit(docker_leaf)
    except qualification.ValidationFailure as error:
        assert error.stage == "secret-leak"
    else:
        raise AssertionError("nested Docker config secret was not detected")


def test_successful_cleanup_preserves_the_original_failure_stage() -> None:
    output: list[str] = []
    validator = qualification.Validator(
        context="ctx",
        namespace="ns",
        application="app",
        app_a="a",
        host_a="a.test",
        app_b="b",
        host_b="b.test",
        report=output.append,
    )

    def fail_setup() -> None:
        raise qualification.ValidationFailure("app-a-ready")

    validator._run_body = fail_setup
    validator._cleanup = lambda: None
    assert validator.run() == 1
    assert output == ["FAIL app-a-ready"]


def test_cli_is_fixed_and_requires_run_and_distinct_values(capsys: object) -> None:
    assert qualification.main(["--context", "ctx"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "FAIL cli\n"
    assert (
        qualification.main(
            [
                "--context",
                "ctx",
                "--namespace",
                "ns",
                "--application",
                "x",
                "--app-a",
                "same",
                "--host-a",
                "a",
                "--app-b",
                "same",
                "--host-b",
                "b",
                "--run",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == "FAIL cli\n"
