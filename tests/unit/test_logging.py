import base64
import copy
from dataclasses import FrozenInstanceError
from typing import Any

import bcrypt
import pytest

from coriolis_operator.ingress import IngressSettings
from coriolis_operator.logging import (
    ADAPTOR_BINARY,
    ADAPTOR_CREDENTIAL_MOUNT_DIR,
    ADAPTOR_CREDENTIAL_PATH,
    ADAPTOR_ENV_PREFIX,
    ADAPTOR_IMAGE,
    ADAPTOR_PORT,
    ADAPTOR_RUN_AS_ID,
    ALLOY_APP_COLLECTION_COMPONENTS,
    ALLOY_BINARY,
    ALLOY_CREDENTIAL_MOUNT_DIR,
    ALLOY_CREDENTIAL_PATH,
    ALLOY_DATA_DIR,
    ALLOY_DATA_PARENT_DIR,
    ALLOY_HTTP_PORT,
    ALLOY_IMAGE,
    ALLOY_READY_PATH,
    ALLOY_RUN_AS_ID,
    GATEWAY_CONFIG_KEYS,
    GATEWAY_IMAGE,
    GATEWAY_READY_PATH,
    LOGGING_ADAPTOR_DEPLOYMENT_KEY,
    LOGGING_ADAPTOR_INGRESS_KEY,
    LOGGING_ADAPTOR_SERVICE_KEY,
    LOGGING_ALLOY_CONFIG_KEY,
    LOGGING_ALLOY_DEPLOYMENT_KEY,
    LOGGING_ALLOY_ROLE_BINDING_KEY,
    LOGGING_ALLOY_ROLE_KEY,
    LOGGING_ALLOY_SERVICE_ACCOUNT_KEY,
    LOGGING_CREDENTIALS_KEY,
    LOGGING_CREDENTIALS_KEYS,
    LOGGING_GATEWAY_CONFIG_SECRET_KEY,
    LOGGING_GATEWAY_SERVICE_KEY,
    LOGGING_IMAGE_PULL_SECRET_NAME,
    LOGGING_LOKI_CONFIG_KEY,
    LOGGING_LOKI_PVC_KEY,
    LOGGING_LOKI_STATEFUL_SET_KEY,
    LOGGING_PHASE_KEYS,
    LOGGING_RESOURCE_COMPONENTS,
    LOKI_CONFIG_KEYS,
    LOKI_IMAGE,
    LoggingExistingResources,
    LoggingPreflight,
    LoggingResourceSettings,
    LoggingSettings,
    LoggingStorageSettings,
    SensitiveLoggingCredentials,
    SensitiveLoggingGatewayConfig,
    build_adaptor_deployment,
    build_adaptor_ingress,
    build_adaptor_service,
    build_alloy_config_map,
    build_alloy_deployment,
    build_alloy_role,
    build_alloy_role_binding,
    build_alloy_service_account,
    build_gateway_config_secret,
    build_gateway_service,
    build_logging_credentials_secret,
    build_loki_config_map,
    build_loki_data_pvc,
    build_loki_stateful_set,
    decode_logging_credentials,
    generate_logging_credentials,
    logging_tenant,
    preflight_logging_resources,
    render_alloy_config,
    render_gateway_config,
    render_loki_config,
    resolve_logging_settings,
)
from coriolis_operator.reconcile import (
    OwnedClassification,
    RetainedClassification,
    appliance_identity,
)


def _valid_settings() -> dict[str, object]:
    return {
        "retentionHours": 24,
        "storage": {"loki": {"storageClassName": "loki-storage", "size": "10Gi"}},
        "resources": {
            "loki": {
                "requests": {"cpu": "250m", "memory": "512Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            },
            "gateway": {
                "requests": {"cpu": "25m", "memory": "32Mi"},
                "limits": {"cpu": "100m", "memory": "64Mi"},
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
    }


def test_resolve_logging_settings_resolves_explicit_values_without_mutation() -> None:
    settings = resolve_logging_settings(_valid_settings())

    assert settings.retention_hours == 24
    assert settings.storage.storage_class_name == "loki-storage"
    assert settings.storage.size == "10Gi"
    assert settings.resources["loki"].requests_cpu == "250m"
    assert settings.resources["loki"].requests_memory == "512Mi"
    assert settings.resources["loki"].limits_cpu == "1"
    assert settings.resources["loki"].limits_memory == "1Gi"
    assert settings.resources["gateway"].requests_cpu == "25m"
    assert settings.resources["gateway"].requests_memory == "32Mi"
    assert settings.resources["gateway"].limits_cpu == "100m"
    assert settings.resources["gateway"].limits_memory == "64Mi"
    assert settings.resources["alloy"].requests_cpu == "100m"
    assert settings.resources["alloy"].requests_memory == "128Mi"
    assert settings.resources["alloy"].limits_cpu == "500m"
    assert settings.resources["alloy"].limits_memory == "512Mi"
    assert settings.resources["adaptor"].requests_cpu == "100m"
    assert settings.resources["adaptor"].requests_memory == "128Mi"
    assert settings.resources["adaptor"].limits_cpu == "500m"
    assert settings.resources["adaptor"].limits_memory == "512Mi"
    assert set(settings.resources) == set(LOGGING_RESOURCE_COMPONENTS)
    assert isinstance(settings, LoggingSettings)
    assert _valid_settings()["retentionHours"] == 24


def test_resolve_logging_settings_returns_frozen_immutable_values() -> None:
    settings = resolve_logging_settings(_valid_settings())

    with pytest.raises(FrozenInstanceError):
        settings.storage.size = "20Gi"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        settings.resources["loki"].limits_cpu = "2"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        settings.resources = {  # type: ignore[misc]
            "loki": settings.resources["loki"]
        }


@pytest.mark.parametrize(
    "logging",
    [
        None,
        {},
        {"retentionHours": 0, "storage": {}, "resources": {}},
        {"retentionHours": -1, "storage": {}, "resources": {}},
        {"retentionHours": True, "storage": {}, "resources": {}},
        {"retentionHours": 1.5, "storage": {}, "resources": {}},
        {"retentionHours": "24", "storage": {}, "resources": {}},
        {"retentionHours": 24},
        {
            "retentionHours": 24,
            "storage": {"loki": {"storageClassName": "standard", "size": "0"}},
            "resources": {},
        },
        {
            "retentionHours": 24,
            "storage": {"loki": {"storageClassName": " ", "size": "10Gi"}},
            "resources": {},
        },
        {
            "retentionHours": 24,
            "storage": {"loki": {"storageClassName": "bad\nname", "size": "10Gi"}},
            "resources": {},
        },
        {
            "retentionHours": 24,
            "storage": {"loki": {"storageClassName": "standard", "size": "nope"}},
            "resources": {},
        },
        {
            "retentionHours": 24,
            "storage": {"loki": {"storageClassName": "standard", "size": "-1Gi"}},
            "resources": {},
        },
        {
            "retentionHours": 24,
            "storage": {"loki": {"storageClassName": "standard", "size": "10Gi"}},
            "resources": {"loki": {}},
        },
    ],
)
def test_resolve_logging_settings_rejects_invalid_input_without_value_leaks(
    logging: object,
) -> None:
    with pytest.raises(ValueError, match="^invalid logging settings$"):
        resolve_logging_settings(logging)


@pytest.mark.parametrize("component", ["loki", "gateway", "alloy", "adaptor"])
def test_resolve_logging_settings_requires_every_resource_component(
    component: str,
) -> None:
    logging = _valid_settings()
    del logging["resources"][component]  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="^invalid logging settings$"):
        resolve_logging_settings(logging)


@pytest.mark.parametrize("component", ["loki", "gateway", "alloy", "adaptor"])
def test_resolve_logging_settings_rejects_requests_exceeding_limits(
    component: str,
) -> None:
    logging = _valid_settings()
    logging["resources"][component]["requests"] = {  # type: ignore[index]
        "cpu": "2",
        "memory": "1Gi",
    }
    logging["resources"][component]["limits"] = {  # type: ignore[index]
        "cpu": "1",
        "memory": "512Mi",
    }

    with pytest.raises(ValueError, match="^invalid logging settings$"):
        resolve_logging_settings(logging)


@pytest.mark.parametrize("component", ["loki", "gateway", "alloy", "adaptor"])
def test_resolve_logging_settings_rejects_missing_requests_or_limits(
    component: str,
) -> None:
    for resource_type in ("requests", "limits"):
        logging = copy.deepcopy(_valid_settings())
        del logging["resources"][component][resource_type]  # type: ignore[index]

        with pytest.raises(ValueError, match="^invalid logging settings$"):
            resolve_logging_settings(logging)


def _settings(retention_hours: int = 24) -> LoggingSettings:
    return LoggingSettings(
        retention_hours=retention_hours,
        storage=LoggingStorageSettings("loki-storage", "10Gi"),
        resources={
            "loki": LoggingResourceSettings("250m", "512Mi", "1", "1Gi"),
            "gateway": LoggingResourceSettings("25m", "32Mi", "100m", "64Mi"),
            "alloy": LoggingResourceSettings("100m", "128Mi", "500m", "512Mi"),
            "adaptor": LoggingResourceSettings("100m", "128Mi", "500m", "512Mi"),
        },
    )


def _owner() -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "Coriolis",
        "name": "appliance",
        "uid": "123e4567-e89b-12d3-a456-426614174000",
    }


def _credentials() -> SensitiveLoggingCredentials:
    return SensitiveLoggingCredentials(
        read_password="read-pass",
        write_password="write-pass",
        read_password_hash="read-hash-value",
        write_password_hash="write-hash-value",
    )


def _tenant() -> str:
    return "coriolis-123e4567-e89b-12d3-a456-426614174000"


def test_generate_logging_credentials_uses_injected_factories() -> None:
    calls: list[int] = []

    def password_factory(size: int) -> str:
        calls.append(size)
        return f"token-{len(calls)}"

    def hash_factory(password: str) -> str:
        return f"hash-of-{password}"

    values = generate_logging_credentials(
        password_factory=password_factory, hash_factory=hash_factory
    )

    assert set(values) == LOGGING_CREDENTIALS_KEYS
    assert values["read_password"] == "token-1"
    assert values["write_password"] == "token-2"
    assert values["read_password_hash"] == "hash-of-token-1"
    assert values["write_password_hash"] == "hash-of-token-2"
    assert calls == [32, 32]


def test_generate_logging_credentials_default_hashes_verify_with_bcrypt() -> None:
    values = generate_logging_credentials()
    for password_key, hash_key in (
        ("read_password", "read_password_hash"),
        ("write_password", "write_password_hash"),
    ):
        password = values[password_key]
        encoded = values[hash_key].encode("utf-8")
        assert bcrypt.checkpw(password.encode("utf-8"), encoded)


def test_generate_logging_credentials_rejects_bad_factory_output() -> None:
    with pytest.raises(ValueError, match="^logging credential generation failed$"):
        generate_logging_credentials(password_factory=lambda _: "")
    with pytest.raises(ValueError, match="^logging credential generation failed$"):
        generate_logging_credentials(hash_factory=lambda _: "bad\nhash")


def test_decode_logging_credentials_requires_exact_keys_and_redacts() -> None:
    values = {
        "read_password": "read-pass",
        "write_password": "write-pass",
        "read_password_hash": "read-hash",
        "write_password_hash": "write-hash",
    }
    credentials = decode_logging_credentials(values)

    assert isinstance(credentials, SensitiveLoggingCredentials)
    assert credentials.read_password == "read-pass"
    assert credentials.write_password == "write-pass"
    assert credentials.read_password_hash == "read-hash"
    assert credentials.write_password_hash == "write-hash"
    assert "read-pass" not in repr(credentials)
    assert "write-hash" not in repr(credentials)
    assert "<redacted>" in repr(credentials)


@pytest.mark.parametrize(
    "values",
    [
        {},
        None,
        "not-a-mapping",
        42,
        {
            "read_password": "r",
            "write_password": "w",
            "read_password_hash": "rh",
        },
        {
            "read_password": "r",
            "write_password": "w",
            "read_password_hash": "rh",
            "write_password_hash": "wh",
            "extra": "x",
        },
        {
            "read_password": "r\nr",
            "write_password": "w",
            "read_password_hash": "rh",
            "write_password_hash": "wh",
        },
    ],
)
def test_decode_logging_credentials_rejects_invalid_input_without_leaks(
    values: object,
) -> None:
    with pytest.raises(ValueError, match="^invalid retained logging credentials$"):
        decode_logging_credentials(values)


def test_logging_tenant_is_derived_and_safe() -> None:
    assert logging_tenant("123e4567-e89b-12d3-a456-426614174000") == _tenant()
    with pytest.raises(ValueError, match="^invalid retained logging credentials$"):
        logging_tenant("bad:uid")
    with pytest.raises(ValueError, match="^invalid retained logging credentials$"):
        logging_tenant("bad uid")


def test_render_loki_config_24h_sample_is_deterministic_and_bounded() -> None:
    config = render_loki_config(settings=_settings(retention_hours=24))
    rendered = config["loki.yaml"]

    assert set(config) == LOKI_CONFIG_KEYS
    assert rendered == (
        "auth_enabled: true\n"
        "\n"
        "server:\n"
        "  http_listen_address: 127.0.0.1\n"
        "  http_listen_port: 3100\n"
        "  grpc_listen_address: 127.0.0.1\n"
        "  grpc_listen_port: 9095\n"
        "  log_level: warn\n"
        "\n"
        "frontend:\n"
        "  address: 127.0.0.1\n"
        "\n"
        "common:\n"
        "  path_prefix: /loki\n"
        "  replication_factor: 1\n"
        "  ring:\n"
        "    instance_addr: 127.0.0.1\n"
        "    kvstore:\n"
        "      store: inmemory\n"
        "  storage:\n"
        "    filesystem:\n"
        "      chunks_directory: /loki/chunks\n"
        "      rules_directory: /loki/rules\n"
        "\n"
        "limits_config:\n"
        "  retention_period: 24h\n"
        "\n"
        "schema_config:\n"
        "  configs:\n"
        "    - from: 2024-01-01\n"
        "      store: tsdb\n"
        "      object_store: filesystem\n"
        "      schema: v13\n"
        "      index:\n"
        "        prefix: index_\n"
        "        period: 24h\n"
        "\n"
        "compactor:\n"
        "  working_directory: /loki/compactor\n"
        "  compaction_interval: 15m\n"
        "  retention_enabled: true\n"
        "  retention_delete_delay: 2h\n"
        "  delete_request_store: filesystem\n"
        "  compactor_ring:\n"
        "    kvstore:\n"
        "      store: inmemory\n"
        "\n"
        "analytics:\n"
        "  reporting_enabled: false\n"
    )


def test_render_loki_config_reflects_retention_hours_only() -> None:
    rendered = render_loki_config(settings=_settings(retention_hours=168))["loki.yaml"]
    assert "  retention_period: 168h\n" in rendered
    assert "  retention_period: 24h\n" not in rendered


def test_render_gateway_config_has_exact_locations_auth_and_header() -> None:
    config = render_gateway_config(credentials=_credentials(), tenant=_tenant())
    nginx = config["nginx.conf"]

    assert set(config) == GATEWAY_CONFIG_KEYS
    assert "access_log off;" in nginx
    assert "error_log stderr warn;" in nginx
    assert f"proxy_set_header X-Scope-OrgID {_tenant()};" in nginx
    assert 'proxy_set_header Authorization "";' in nginx
    assert f"location = {GATEWAY_READY_PATH} {{" in nginx
    assert "location = /loki/api/v1/push {" in nginx
    assert "location = /loki/api/v1/series {" in nginx
    assert "location = /loki/api/v1/query_range {" in nginx
    assert "location = /loki/api/v1/tail {" in nginx
    assert "location / {\n            return 404;" in nginx
    assert "auth_basic_user_file /etc/nginx-gateway/read.htpasswd;" in nginx
    assert "auth_basic_user_file /etc/nginx-gateway/write.htpasswd;" in nginx
    assert "proxy_pass http://127.0.0.1:3100/ready;" in nginx
    assert "proxy_pass http://127.0.0.1:3100;" in nginx
    assert "proxy_http_version 1.1;" in nginx
    assert "proxy_set_header Upgrade $http_upgrade;" in nginx
    assert "proxy_set_header Connection $connection_upgrade;" in nginx
    assert "proxy_buffering off;" in nginx
    assert "proxy_read_timeout 3600s;" in nginx


def test_render_gateway_config_uses_retained_hashes_not_passwords() -> None:
    config = render_gateway_config(credentials=_credentials(), tenant=_tenant())

    assert config["read.htpasswd"] == f"{_tenant()}:read-hash-value\n"
    assert config["write.htpasswd"] == f"{_tenant()}:write-hash-value\n"
    assert "read-pass" not in config["nginx.conf"]
    assert "read-pass" not in config["read.htpasswd"]
    assert "write-pass" not in config["write.htpasswd"]


def test_render_gateway_config_redacts_and_rejects_invalid_input() -> None:
    config = render_gateway_config(credentials=_credentials(), tenant=_tenant())
    assert repr(config) == "SensitiveLoggingGatewayConfig(<redacted>)"
    assert str(config) == "SensitiveLoggingGatewayConfig(<redacted>)"
    assert "read-pass" not in repr(config)
    assert "write-hash-value" not in repr(config)
    assert isinstance(config, SensitiveLoggingGatewayConfig)

    with pytest.raises(ValueError, match="^invalid retained logging credentials$"):
        render_gateway_config(credentials="not-typed", tenant=_tenant())
    with pytest.raises(ValueError, match="^invalid retained logging credentials$"):
        render_gateway_config(credentials=_credentials(), tenant="bad:tenant")


def test_build_logging_credentials_secret_is_ownerless_and_retained() -> None:
    values = {
        "read_password": "r",
        "write_password": "w",
        "read_password_hash": "rh",
        "write_password_hash": "wh",
    }
    secret = build_logging_credentials_secret(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        retention="logging-credentials",
        values=values,
    )

    assert secret["apiVersion"] == "v1"
    assert secret["kind"] == "Secret"
    assert secret["type"] == "Opaque"
    assert secret["metadata"]["name"] == "appliance-logging-credentials"
    assert secret["metadata"]["namespace"] == "coriolis"
    assert "ownerReferences" not in secret["metadata"]
    assert (
        secret["metadata"]["annotations"]["coriolis.cloudbase.it/retention"]
        == "logging-credentials"
    )
    assert set(secret["data"]) == LOGGING_CREDENTIALS_KEYS
    for key, value in values.items():
        assert secret["data"][key] == base64.b64encode(value.encode()).decode()


def test_build_loki_data_pvc_is_ownerless_retained_rwo() -> None:
    pvc = build_loki_data_pvc(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        settings=_settings(),
    )

    assert pvc["kind"] == "PersistentVolumeClaim"
    assert "ownerReferences" not in pvc["metadata"]
    assert (
        pvc["metadata"]["annotations"]["coriolis.cloudbase.it/retention"] == "loki-data"
    )
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert pvc["spec"]["volumeMode"] == "Filesystem"
    assert pvc["spec"]["storageClassName"] == "loki-storage"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "10Gi"


def test_build_loki_config_map_is_owned() -> None:
    values = render_loki_config(settings=_settings())
    config_map = build_loki_config_map(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        values=values,
    )

    assert config_map["kind"] == "ConfigMap"
    assert config_map["metadata"]["name"] == "appliance-loki-config"
    assert config_map["metadata"]["ownerReferences"][0]["uid"] == _owner()["uid"]
    assert config_map["data"] == values


def test_build_gateway_config_secret_is_owned_and_encoded() -> None:
    config = render_gateway_config(credentials=_credentials(), tenant=_tenant())
    secret = build_gateway_config_secret(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        values=config,
    )

    assert secret["kind"] == "Secret"
    assert secret["metadata"]["name"] == "appliance-gateway-config"
    assert secret["metadata"]["ownerReferences"][0]["uid"] == _owner()["uid"]
    assert set(secret["data"]) == GATEWAY_CONFIG_KEYS
    for key, value in config.items():
        assert secret["data"][key] == base64.b64encode(value.encode()).decode()


def test_build_gateway_service_exposes_only_nginx_and_selects_gateway() -> None:
    service = build_gateway_service(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )

    assert service["kind"] == "Service"
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"]["coriolis.cloudbase.it/appliance"] == "appliance"
    assert service["spec"]["selector"]["coriolis.cloudbase.it/gateway"] == "gateway"
    ports = service["spec"]["ports"]
    assert [port["port"] for port in ports] == [8080]
    assert [port["targetPort"] for port in ports] == [8080]
    assert all(port["protocol"] == "TCP" for port in ports)
    assert 3100 not in [port["port"] for port in ports]


def test_build_loki_stateful_set_contract() -> None:
    stateful_set = build_loki_stateful_set(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
    )

    assert stateful_set["kind"] == "StatefulSet"
    assert stateful_set["metadata"]["name"] == "appliance-loki"
    assert stateful_set["spec"]["replicas"] == 1
    assert stateful_set["spec"]["serviceName"] == "appliance-gateway"
    selector = stateful_set["spec"]["selector"]["matchLabels"]
    assert selector["coriolis.cloudbase.it/component"] == "loki"

    pod_spec = stateful_set["spec"]["template"]["spec"]
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["enableServiceLinks"] is False
    assert pod_spec["imagePullSecrets"] == [{"name": LOGGING_IMAGE_PULL_SECRET_NAME}]
    assert pod_spec["securityContext"] == {
        "fsGroup": 10001,
        "fsGroupChangePolicy": "OnRootMismatch",
    }

    images = {container["image"] for container in pod_spec["containers"]}
    assert images == {LOKI_IMAGE, GATEWAY_IMAGE}
    for container in pod_spec["containers"]:
        assert container["imagePullPolicy"] == "Always"
        security = container["securityContext"]
        assert security["runAsNonRoot"] is True
        assert security["readOnlyRootFilesystem"] is True
        assert security["allowPrivilegeEscalation"] is False
        assert security["capabilities"] == {"drop": ["ALL"]}
        assert security["seccompProfile"] == {"type": "RuntimeDefault"}

    loki = next(c for c in pod_spec["containers"] if c["name"] == "loki")
    gateway = next(c for c in pod_spec["containers"] if c["name"] == "gateway")
    assert loki["securityContext"]["runAsUser"] == 10001
    assert loki["securityContext"]["runAsGroup"] == 10001
    assert gateway["securityContext"]["runAsUser"] == 101
    assert gateway["securityContext"]["runAsGroup"] == 101

    loki_mounts = {mount["name"] for mount in loki["volumeMounts"]}
    assert loki_mounts == {"data", "config", "tmp"}
    gateway_mounts = {mount["name"] for mount in gateway["volumeMounts"]}
    assert gateway_mounts == {"gateway-config", "gateway-tmp"}

    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
    assert (
        volumes["data"]["persistentVolumeClaim"]["claimName"] == "appliance-loki-data"
    )
    assert volumes["config"]["configMap"]["name"] == "appliance-loki-config"
    assert (
        volumes["gateway-config"]["secret"]["secretName"] == "appliance-gateway-config"
    )
    assert volumes["tmp"]["emptyDir"] == {"medium": "Memory"}
    assert volumes["gateway-tmp"]["emptyDir"] == {"medium": "Memory"}

    gateway_ports = {port["containerPort"] for port in gateway["ports"]}
    assert gateway_ports == {8080}
    loki_ports = {port["containerPort"] for port in loki["ports"]}
    assert loki_ports == {3100}

    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert probe in gateway
        assert gateway[probe]["httpGet"] == {
            "path": GATEWAY_READY_PATH,
            "port": 8080,
        }
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert probe in loki
        assert loki[probe]["httpGet"] == {
            "path": GATEWAY_READY_PATH,
            "port": 8080,
        }


def test_build_loki_stateful_set_loki_owns_pvc_and_gateway_has_only_auth_config() -> (
    None
):
    stateful_set = build_loki_stateful_set(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
    )
    pod_spec = stateful_set["spec"]["template"]["spec"]
    loki = next(c for c in pod_spec["containers"] if c["name"] == "loki")
    gateway = next(c for c in pod_spec["containers"] if c["name"] == "gateway")

    loki_data_mount = next(m for m in loki["volumeMounts"] if m["name"] == "data")
    assert loki_data_mount["mountPath"] == "/loki"
    assert all(m["name"] != "data" for m in gateway["volumeMounts"]), (
        "NGINX gateway must not mount the Loki PVC"
    )

    gateway_config_mount = next(
        m for m in gateway["volumeMounts"] if m["name"] == "gateway-config"
    )
    assert gateway_config_mount["readOnly"] is True
    gateway_tmp_mount = next(
        m for m in gateway["volumeMounts"] if m["name"] == "gateway-tmp"
    )
    assert gateway_tmp_mount["mountPath"] == "/tmp"

    pod_labels = stateful_set["spec"]["template"]["metadata"]["labels"]
    assert pod_labels["coriolis.cloudbase.it/component"] == "loki"
    assert pod_labels["coriolis.cloudbase.it/gateway"] == "gateway"


def test_build_loki_stateful_set_probes_are_independent_objects() -> None:
    stateful_set = build_loki_stateful_set(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
    )
    pod_spec = stateful_set["spec"]["template"]["spec"]
    loki = next(c for c in pod_spec["containers"] if c["name"] == "loki")
    gateway = next(c for c in pod_spec["containers"] if c["name"] == "gateway")

    probe_names = ("startupProbe", "readinessProbe", "livenessProbe")
    for name in probe_names:
        assert loki[name] is not gateway[name]
        assert loki[name]["httpGet"] is not gateway[name]["httpGet"]
    for name in probe_names:
        for other in probe_names:
            if name is other:
                continue
            assert loki[name] is not loki[other]
            assert gateway[name] is not gateway[other]
    for container in (loki, gateway):
        for name in probe_names:
            probe = container[name]
            assert probe["httpGet"]["port"] == 8080
            assert probe["httpGet"]["path"] == GATEWAY_READY_PATH


def _alloy_gateway() -> str:
    return "appliance-gateway"


def _alloy_render() -> str:
    return render_alloy_config(
        namespace="coriolis",
        appliance_name="appliance",
        gateway_service_name=_alloy_gateway(),
        basic_auth_username=_tenant(),
    )


def test_render_alloy_config_restricts_namespace_and_app_allowlist() -> None:
    config = _alloy_render()

    assert 'names = ["coriolis"]' in config
    assert (
        "coriolis.cloudbase.it/appliance=appliance"
        ",coriolis.cloudbase.it/component in ("
        + ", ".join(ALLOY_APP_COLLECTION_COMPONENTS)
        + ")"
    ) in config
    for component in ALLOY_APP_COLLECTION_COMPONENTS:
        assert component in config
    for excluded in ("loki", "gateway", "alloy", "adaptor"):
        assert f"component in ({excluded}" not in config
        assert f", {excluded})" not in config


def test_render_alloy_config_relabels_exact_output_labels() -> None:
    config = _alloy_render()

    assert 'target_label = "namespace"' in config
    assert 'replacement = "appliance"' in config
    assert 'target_label = "coriolis_appliance"' in config
    assert 'target_label = "coriolis_component"' in config
    assert 'target_label = "pod"' in config
    assert 'target_label = "container"' in config
    assert 'regex = ".+"' in config
    assert 'target_label = "stream"' in config
    assert "coriolis_cloudbase_it_appliance_name" not in config


def test_render_alloy_config_emits_full_name_for_hashed_identity() -> None:
    config = render_alloy_config(
        namespace="coriolis",
        appliance_name="example.site",
        gateway_service_name=_alloy_gateway(),
        basic_auth_username=_tenant(),
    )

    identity = appliance_identity("example.site")
    assert identity != "example.site"
    assert f"coriolis.cloudbase.it/appliance={identity}," in config
    assert 'replacement = "example.site"' in config
    assert 'target_label = "coriolis_appliance"' in config
    assert "coriolis_cloudbase_it_appliance_name" not in config
    assert "__meta_kubernetes_pod_annotation_" not in config


def test_render_alloy_config_severity_only_from_recognized_prefix() -> None:
    config = _alloy_render()

    assert "stage.regex {" in config
    assert 'source = "line"' in config
    assert 'severity = "level"' in config
    assert "stage.labels {" in config


def test_render_alloy_config_pushes_to_gateway_with_no_tenant_header() -> None:
    config = _alloy_render()

    assert "http://appliance-gateway:8080/loki/api/v1/push" in config
    assert f'username = "{_tenant()}"' in config
    assert f'password_file = "{ALLOY_CREDENTIAL_PATH}"' in config
    assert "X-Scope-OrgID" not in config
    assert "tenant_id" not in config


def test_build_alloy_config_map_is_owned() -> None:
    values = {"config.alloy": _alloy_render()}
    config_map = build_alloy_config_map(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        values=values,
    )

    assert config_map["kind"] == "ConfigMap"
    assert config_map["metadata"]["name"] == "appliance-alloy-config"
    assert config_map["metadata"]["ownerReferences"][0]["uid"] == _owner()["uid"]
    assert config_map["data"] == values


def test_build_alloy_role_is_namespaced_and_narrow() -> None:
    role = build_alloy_role(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )

    assert role["kind"] == "Role"
    assert role["apiVersion"] == "rbac.authorization.k8s.io/v1"
    assert role["metadata"]["name"] == "appliance-alloy-role"
    assert role["metadata"]["namespace"] == "coriolis"
    assert "clusterRole" not in role
    assert role["rules"] == [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]},
        {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
    ]


def test_build_alloy_role_binding_binds_sa_to_role_in_namespace() -> None:
    binding = build_alloy_role_binding(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )

    assert binding["kind"] == "RoleBinding"
    assert binding["metadata"]["name"] == "appliance-alloy-rb"
    assert binding["metadata"]["namespace"] == "coriolis"
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": "appliance-alloy-role",
    }
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "appliance-alloy-sa",
            "namespace": "coriolis",
        }
    ]


def test_build_alloy_service_account_is_dedicated_and_owned() -> None:
    service_account = build_alloy_service_account(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )

    assert service_account["kind"] == "ServiceAccount"
    assert service_account["metadata"]["name"] == "appliance-alloy-sa"
    assert service_account["metadata"]["namespace"] == "coriolis"
    assert service_account["metadata"]["ownerReferences"][0]["uid"] == _owner()["uid"]


def test_build_alloy_deployment_is_hardened_and_owned() -> None:
    deployment = build_alloy_deployment(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
    )

    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "appliance-alloy"
    assert deployment["metadata"]["ownerReferences"][0]["uid"] == _owner()["uid"]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    selector = deployment["spec"]["selector"]["matchLabels"]
    assert selector["coriolis.cloudbase.it/component"] == "alloy"

    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"] == "appliance-alloy-sa"
    assert pod_spec["automountServiceAccountToken"] is True
    assert pod_spec["enableServiceLinks"] is False
    assert pod_spec["imagePullSecrets"] == [{"name": LOGGING_IMAGE_PULL_SECRET_NAME}]
    assert pod_spec["securityContext"] == {
        "fsGroup": ALLOY_RUN_AS_ID,
        "fsGroupChangePolicy": "OnRootMismatch",
    }
    assert "hostPath" not in repr(pod_spec["volumes"])
    assert "persistentVolumeClaim" not in repr(pod_spec["volumes"])
    assert "nodeSelector" not in pod_spec

    container = pod_spec["containers"][0]
    assert len(pod_spec["containers"]) == 1
    assert container["name"] == "alloy"
    assert container["image"] == ALLOY_IMAGE
    assert container["imagePullPolicy"] == "Always"
    assert container["command"] == [ALLOY_BINARY]
    assert "--server.http.listen-addr=0.0.0.0:12345" in container["args"]
    assert "--storage.path=/var/lib/alloy/data" in container["args"]

    security = container["securityContext"]
    assert security["runAsNonRoot"] is True
    assert security["readOnlyRootFilesystem"] is True
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"] == {"drop": ["ALL"]}
    assert security["seccompProfile"] == {"type": "RuntimeDefault"}
    assert security["runAsUser"] == ALLOY_RUN_AS_ID
    assert security["runAsGroup"] == ALLOY_RUN_AS_ID
    assert "envFrom" not in container

    resources = container["resources"]
    assert resources["requests"] == {"cpu": "100m", "memory": "128Mi"}
    assert resources["limits"] == {"cpu": "500m", "memory": "512Mi"}

    assert {port["containerPort"] for port in container["ports"]} == {ALLOY_HTTP_PORT}

    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    assert set(mounts) == {"config", "credentials", "storage", "tmp"}
    assert mounts["config"]["mountPath"] == "/etc/alloy"
    assert mounts["config"]["readOnly"] is True
    assert mounts["credentials"]["mountPath"] == ALLOY_CREDENTIAL_MOUNT_DIR
    assert mounts["credentials"]["readOnly"] is True
    assert mounts["storage"]["mountPath"] == ALLOY_DATA_PARENT_DIR
    assert mounts["tmp"]["mountPath"] == "/tmp"

    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert container[probe]["httpGet"] == {
            "path": ALLOY_READY_PATH,
            "port": ALLOY_HTTP_PORT,
        }


def test_build_alloy_deployment_mounts_only_write_credential_item() -> None:
    deployment = build_alloy_deployment(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
    )
    pod_spec = deployment["spec"]["template"]["spec"]

    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
    assert set(volumes) == {"config", "credentials", "storage", "tmp"}
    assert volumes["config"]["configMap"]["name"] == "appliance-alloy-config"
    config_items = volumes["config"]["configMap"]["items"]
    assert config_items == [
        {"key": "config.alloy", "path": "config.alloy", "mode": 0o444}
    ]

    credentials_secret = volumes["credentials"]["secret"]
    assert credentials_secret["secretName"] == "appliance-logging-credentials"
    credential_items = volumes["credentials"]["secret"]["items"]
    assert credential_items == [
        {"key": "write_password", "path": "write_password", "mode": 0o444}
    ]

    assert volumes["storage"]["emptyDir"] == {}
    assert volumes["tmp"]["emptyDir"] == {}


def test_build_alloy_deployment_mounts_writable_parent_for_readonly_root() -> None:
    deployment = build_alloy_deployment(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
    )
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    pod_spec = deployment["spec"]["template"]["spec"]

    storage_mount = next(m for m in container["volumeMounts"] if m["name"] == "storage")
    assert storage_mount["mountPath"] == ALLOY_DATA_PARENT_DIR
    assert storage_mount["mountPath"] != ALLOY_DATA_DIR
    assert ALLOY_DATA_DIR.startswith(f"{ALLOY_DATA_PARENT_DIR}/")
    assert f"--storage.path={ALLOY_DATA_DIR}" in container["args"]

    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
    assert volumes["storage"]["emptyDir"] == {}


def test_alloy_resources_are_distinct_per_resource() -> None:
    config_map = build_alloy_config_map(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        values={"config.alloy": _alloy_render()},
    )
    service_account = build_alloy_service_account(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    role = build_alloy_role(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    binding = build_alloy_role_binding(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    deployment = build_alloy_deployment(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
    )

    names = {
        "alloy-config": config_map["metadata"]["name"],
        "alloy-sa": service_account["metadata"]["name"],
        "alloy-role": role["metadata"]["name"],
        "alloy-rb": binding["metadata"]["name"],
        "alloy": deployment["metadata"]["name"],
    }
    assert names == {
        "alloy-config": "appliance-alloy-config",
        "alloy-sa": "appliance-alloy-sa",
        "alloy-role": "appliance-alloy-role",
        "alloy-rb": "appliance-alloy-rb",
        "alloy": "appliance-alloy",
    }
    assert len(set(names.values())) == len(names)


def _cr_uid() -> str:
    return "123e4567-e89b-12d3-a456-426614174000"


def _ingress_settings() -> IngressSettings:
    return IngressSettings(
        host="coriolis.app.cloudbase.wiki",
        ingress_class_name="nginx",
        tls_mode="existingSecret",
        tls_secret_name="coriolis-tls",
        annotations={},
    )


def _adaptor_deployment() -> dict[str, Any]:
    return build_adaptor_deployment(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
        cr_uid=_cr_uid(),
    )


def test_build_adaptor_deployment_is_hardened_and_owned() -> None:
    deployment = _adaptor_deployment()

    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "appliance-adaptor"
    assert deployment["metadata"]["ownerReferences"][0]["uid"] == _owner()["uid"]
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"] == {"type": "Recreate"}
    selector = deployment["spec"]["selector"]["matchLabels"]
    assert selector["coriolis.cloudbase.it/component"] == "adaptor"

    pod_spec = deployment["spec"]["template"]["spec"]
    assert "serviceAccountName" not in pod_spec
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["enableServiceLinks"] is False
    assert pod_spec["imagePullSecrets"] == [{"name": LOGGING_IMAGE_PULL_SECRET_NAME}]
    assert pod_spec["securityContext"] == {
        "fsGroup": ADAPTOR_RUN_AS_ID,
        "fsGroupChangePolicy": "OnRootMismatch",
    }
    assert "hostPath" not in repr(pod_spec["volumes"])
    assert "persistentVolumeClaim" not in repr(pod_spec["volumes"])
    assert "nodeSelector" not in pod_spec

    container = pod_spec["containers"][0]
    assert len(pod_spec["containers"]) == 1
    assert container["name"] == "adaptor"
    assert container["image"] == ADAPTOR_IMAGE
    assert container["imagePullPolicy"] == "Always"
    assert container["command"] == [ADAPTOR_BINARY]
    assert container["workingDir"] == "/app"
    assert "envFrom" not in container

    security = container["securityContext"]
    assert security["runAsNonRoot"] is True
    assert security["readOnlyRootFilesystem"] is True
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"] == {"drop": ["ALL"]}
    assert security["seccompProfile"] == {"type": "RuntimeDefault"}
    assert security["runAsUser"] == ADAPTOR_RUN_AS_ID
    assert security["runAsGroup"] == ADAPTOR_RUN_AS_ID

    resources = container["resources"]
    assert resources["requests"] == {"cpu": "100m", "memory": "128Mi"}
    assert resources["limits"] == {"cpu": "500m", "memory": "512Mi"}

    assert {port["containerPort"] for port in container["ports"]} == {ADAPTOR_PORT}
    assert all(port["name"] == "adaptor" for port in container["ports"])

    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    assert set(mounts) == {"credentials", "tmp"}
    assert mounts["credentials"]["mountPath"] == ADAPTOR_CREDENTIAL_MOUNT_DIR
    assert mounts["credentials"]["readOnly"] is True
    assert mounts["tmp"]["mountPath"] == "/tmp"

    assert container["startupProbe"]["httpGet"] == {
        "path": "/readyz",
        "port": ADAPTOR_PORT,
    }
    assert container["readinessProbe"]["httpGet"] == {
        "path": "/readyz",
        "port": ADAPTOR_PORT,
    }
    assert container["livenessProbe"]["httpGet"] == {
        "path": "/healthz",
        "port": ADAPTOR_PORT,
    }


def test_build_adaptor_deployment_env_contract_is_non_sensitive() -> None:
    deployment = _adaptor_deployment()
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {entry["name"]: entry["value"] for entry in container["env"]}

    assert env == {
        f"{ADAPTOR_ENV_PREFIX}KEYSTONE_URL": "http://appliance-keystone:5000/v3",
        f"{ADAPTOR_ENV_PREFIX}ADMIN_ROLES": "admin",
        f"{ADAPTOR_ENV_PREFIX}COMPONENTS": ",".join(ALLOY_APP_COLLECTION_COMPONENTS),
        f"{ADAPTOR_ENV_PREFIX}LOKI_GATEWAY_URL": "http://appliance-gateway:8080",
        f"{ADAPTOR_ENV_PREFIX}LOKI_PASSWORD_FILE": ADAPTOR_CREDENTIAL_PATH,
        f"{ADAPTOR_ENV_PREFIX}CR_UID": _cr_uid(),
        f"{ADAPTOR_ENV_PREFIX}NAMESPACE": "coriolis",
        f"{ADAPTOR_ENV_PREFIX}APPLIANCE_NAME": "appliance",
        "HOME": "/tmp",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert f"{ADAPTOR_ENV_PREFIX}WRITE_PASSWORD" not in env
    assert all(entry.keys() == {"name", "value"} for entry in container["env"])
    assert all(
        "read_password" not in name and "write_password" not in name for name in env
    )


def test_build_adaptor_deployment_mounts_only_read_credential_item() -> None:
    deployment = _adaptor_deployment()
    pod_spec = deployment["spec"]["template"]["spec"]

    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}
    assert set(volumes) == {"credentials", "tmp"}
    credentials_secret = volumes["credentials"]["secret"]
    assert credentials_secret["secretName"] == "appliance-logging-credentials"
    assert credentials_secret["items"] == [
        {"key": "read_password", "path": "read_password", "mode": 0o444}
    ]
    assert volumes["tmp"]["emptyDir"] == {"medium": "Memory"}


def test_build_adaptor_deployment_rejects_unsafe_cr_uid() -> None:
    with pytest.raises(ValueError, match="^invalid retained logging credentials$"):
        build_adaptor_deployment(
            appliance_name="appliance",
            namespace="coriolis",
            accepted_version="2603.4",
            owner=_owner(),
            settings=_settings(),
            cr_uid="bad:uid",
        )


def test_build_adaptor_service_selects_adaptor_and_exposes_only_8080() -> None:
    service = build_adaptor_service(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )

    assert service["kind"] == "Service"
    assert service["metadata"]["name"] == "appliance-adaptor"
    assert service["metadata"]["ownerReferences"][0]["uid"] == _owner()["uid"]
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"]["coriolis.cloudbase.it/appliance"] == "appliance"
    assert service["spec"]["selector"]["coriolis.cloudbase.it/component"] == "adaptor"
    ports = service["spec"]["ports"]
    assert [port["port"] for port in ports] == [8080]
    assert [port["targetPort"] for port in ports] == [8080]
    assert all(port["protocol"] == "TCP" for port in ports)
    assert 3100 not in [port["port"] for port in ports]


def test_build_adaptor_ingress_routes_logs_and_log_stream_without_rewrite() -> None:
    ingress = build_adaptor_ingress(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_ingress_settings(),
    )

    assert ingress["kind"] == "Ingress"
    assert ingress["apiVersion"] == "networking.k8s.io/v1"
    assert ingress["metadata"]["name"] == "appliance-adaptor"
    assert ingress["metadata"]["ownerReferences"][0]["uid"] == _owner()["uid"]
    assert ingress["spec"]["ingressClassName"] == "nginx"
    assert ingress["spec"]["tls"] == [
        {"hosts": ["coriolis.app.cloudbase.wiki"], "secretName": "coriolis-tls"}
    ]

    paths = ingress["spec"]["rules"][0]["http"]["paths"]
    assert [path["pathType"] for path in paths] == ["Prefix", "Prefix"]
    assert [path["path"] for path in paths] == ["/logs", "/log-stream"]
    for path in paths:
        assert path["backend"]["service"]["name"] == "appliance-adaptor"
        assert path["backend"]["service"]["port"] == {"number": 8080}


def test_build_adaptor_ingress_matches_service_backend_and_is_owned() -> None:
    service = build_adaptor_service(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    ingress = build_adaptor_ingress(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_ingress_settings(),
    )

    service_name = service["metadata"]["name"]
    paths = ingress["spec"]["rules"][0]["http"]["paths"]
    for path in paths:
        assert path["backend"]["service"]["name"] == service_name


def test_build_adaptor_ingress_logs_access_off_and_no_cors_or_rewrite() -> None:
    ingress = build_adaptor_ingress(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_ingress_settings(),
    )
    annotations = ingress["metadata"]["annotations"]

    assert annotations["nginx.ingress.kubernetes.io/enable-access-log"] == "false"
    assert annotations["nginx.ingress.kubernetes.io/proxy-buffering"] == "off"
    assert annotations["nginx.ingress.kubernetes.io/proxy-read-timeout"] == "3600"
    assert annotations["nginx.ingress.kubernetes.io/proxy-send-timeout"] == "3600"
    assert annotations["nginx.ingress.kubernetes.io/proxy-http-version"] == "1.1"

    assert "nginx.ingress.kubernetes.io/enable-cors" not in annotations
    assert "nginx.ingress.kubernetes.io/cors-allow-origin" not in annotations
    assert "nginx.ingress.kubernetes.io/rewrite-target" not in annotations
    assert "nginx.ingress.kubernetes.io/use-regex" not in annotations
    assert "cert-manager.io/cluster-issuer" not in annotations
    assert "coriolis.cloudbase.it/appliance-name" in annotations


def test_adaptor_resources_are_owned_and_distinct() -> None:
    deployment = _adaptor_deployment()
    service = build_adaptor_service(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    ingress = build_adaptor_ingress(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_ingress_settings(),
    )

    kinds = {
        deployment["kind"]: deployment["metadata"]["name"],
        service["kind"]: service["metadata"]["name"],
        ingress["kind"]: ingress["metadata"]["name"],
    }
    assert kinds == {
        "Deployment": "appliance-adaptor",
        "Service": "appliance-adaptor",
        "Ingress": "appliance-adaptor",
    }
    for resource in (deployment, service, ingress):
        assert resource["metadata"]["ownerReferences"][0]["uid"] == _owner()["uid"]


def _credential_values() -> dict[str, str]:
    return {
        "read_password": "read-pass",
        "write_password": "write-pass",
        "read_password_hash": "read-hash-value",
        "write_password_hash": "write-hash-value",
    }


def _existing_resources(**overrides: Any) -> LoggingExistingResources:
    values = _credential_values()
    credentials_secret = build_logging_credentials_secret(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        retention="logging-credentials",
        values=values,
    )
    loki_pvc = build_loki_data_pvc(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        settings=_settings(),
    )
    loki_config_map = build_loki_config_map(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        values=render_loki_config(settings=_settings()),
    )
    gateway_config_secret = build_gateway_config_secret(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        values=render_gateway_config(credentials=_credentials(), tenant=_tenant()),
    )
    gateway_service = build_gateway_service(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    loki_stateful_set = build_loki_stateful_set(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
    )
    alloy_config_map = build_alloy_config_map(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        values={"config.alloy": _alloy_render()},
    )
    alloy_service_account = build_alloy_service_account(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    alloy_role = build_alloy_role(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    alloy_role_binding = build_alloy_role_binding(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    alloy_deployment = build_alloy_deployment(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
    )
    adaptor_deployment = build_adaptor_deployment(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_settings(),
        cr_uid=_cr_uid(),
    )
    adaptor_service = build_adaptor_service(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
    )
    adaptor_ingress = build_adaptor_ingress(
        appliance_name="appliance",
        namespace="coriolis",
        accepted_version="2603.4",
        owner=_owner(),
        settings=_ingress_settings(),
    )
    base: dict[str, Any] = {
        "credentials_secret": credentials_secret,
        "loki_pvc": loki_pvc,
        "loki_config_map": loki_config_map,
        "gateway_config_secret": gateway_config_secret,
        "gateway_service": gateway_service,
        "loki_stateful_set": loki_stateful_set,
        "alloy_config_map": alloy_config_map,
        "alloy_service_account": alloy_service_account,
        "alloy_role": alloy_role,
        "alloy_role_binding": alloy_role_binding,
        "alloy_deployment": alloy_deployment,
        "adaptor_deployment": adaptor_deployment,
        "adaptor_service": adaptor_service,
        "adaptor_ingress": adaptor_ingress,
    }
    base.update(overrides)
    return LoggingExistingResources(**base)


def _preflight(
    *,
    cr_uid: str | None = None,
    existing: LoggingExistingResources | None = None,
    password_factory: Any = None,
    hash_factory: Any = None,
) -> LoggingPreflight:
    kwargs: dict[str, Any] = {
        "appliance_name": "appliance",
        "namespace": "coriolis",
        "accepted_version": "2603.4",
        "owner": _owner(),
        "cr_uid": cr_uid or _cr_uid(),
        "settings": _settings(),
        "ingress_settings": _ingress_settings(),
        "existing": existing or _existing_resources(),
    }
    if password_factory is not None:
        kwargs["password_factory"] = password_factory
    if hash_factory is not None:
        kwargs["hash_factory"] = hash_factory
    return preflight_logging_resources(**kwargs)


def test_preflight_absent_builds_all_manifests_with_deterministic_phase_order() -> None:
    preflight = _preflight(existing=LoggingExistingResources())

    assert set(preflight.manifests) == set(LOGGING_PHASE_KEYS)
    assert preflight.collision_resource_name is None
    assert preflight.credentials is not None
    assert set(preflight.credentials) == LOGGING_CREDENTIALS_KEYS

    assert preflight.classifications[LOGGING_CREDENTIALS_KEY] is (
        RetainedClassification.ABSENT
    )
    assert preflight.classifications[LOGGING_LOKI_PVC_KEY] is (
        RetainedClassification.ABSENT
    )
    for key in (
        LOGGING_LOKI_CONFIG_KEY,
        LOGGING_GATEWAY_CONFIG_SECRET_KEY,
        LOGGING_GATEWAY_SERVICE_KEY,
        LOGGING_LOKI_STATEFUL_SET_KEY,
        LOGGING_ALLOY_CONFIG_KEY,
        LOGGING_ALLOY_SERVICE_ACCOUNT_KEY,
        LOGGING_ALLOY_ROLE_KEY,
        LOGGING_ALLOY_ROLE_BINDING_KEY,
        LOGGING_ALLOY_DEPLOYMENT_KEY,
        LOGGING_ADAPTOR_DEPLOYMENT_KEY,
        LOGGING_ADAPTOR_SERVICE_KEY,
        LOGGING_ADAPTOR_INGRESS_KEY,
    ):
        assert preflight.classifications[key] is OwnedClassification.ABSENT

    assert [m["kind"] for m in preflight.loki_phase()] == [
        "Secret",
        "PersistentVolumeClaim",
        "ConfigMap",
        "Secret",
        "Service",
        "StatefulSet",
    ]
    assert [m["kind"] for m in preflight.alloy_phase()] == [
        "ConfigMap",
        "ServiceAccount",
        "Role",
        "RoleBinding",
        "Deployment",
    ]
    assert [m["kind"] for m in preflight.adaptor_phase()] == ["Service", "Deployment"]
    assert [m["kind"] for m in preflight.adaptor_ingress_phase()] == ["Ingress"]


def test_preflight_reuses_existing_retained_secret_without_generators() -> None:
    existing = _existing_resources()
    assert existing.credentials_secret is not None
    original_data = copy.deepcopy(existing.credentials_secret["data"])

    def fail_factory(*_: object) -> object:
        raise AssertionError("credential generator must not run on reuse")

    preflight = _preflight(existing=existing, password_factory=fail_factory)

    assert preflight.classifications[LOGGING_CREDENTIALS_KEY] is (
        RetainedClassification.REUSE
    )
    assert preflight.credentials == _credential_values()
    assert existing.credentials_secret["data"] == original_data


def test_preflight_new_uid_reuses_hashes_and_rerenders_gateway_tenant() -> None:
    new_uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000"
    preflight = _preflight(cr_uid=new_uid)

    assert preflight.credentials == _credential_values()
    gateway_secret = preflight.manifests[LOGGING_GATEWAY_CONFIG_SECRET_KEY]
    decoded = {
        key: base64.b64decode(value).decode("utf-8")
        for key, value in gateway_secret["data"].items()
    }
    new_tenant = logging_tenant(new_uid)
    assert f"proxy_set_header X-Scope-OrgID {new_tenant};" in decoded["nginx.conf"]
    assert f"{new_tenant}:read-hash-value\n" in decoded["read.htpasswd"]
    assert f"{new_tenant}:write-hash-value\n" in decoded["write.htpasswd"]
    assert "read-pass" not in decoded["read.htpasswd"]
    assert "write-pass" not in decoded["write.htpasswd"]


def test_preflight_adaptor_ingress_collision_is_reported_before_any_write() -> None:
    existing = _existing_resources()
    assert existing.adaptor_ingress is not None
    colliding_ingress = copy.deepcopy(existing.adaptor_ingress)
    colliding_ingress["metadata"]["ownerReferences"][0]["uid"] = "colliding-uid"
    existing = _existing_resources(adaptor_ingress=colliding_ingress)

    preflight = _preflight(existing=existing)

    assert preflight.collision_resource_name == "appliance-adaptor"
    assert preflight.classifications[LOGGING_ADAPTOR_INGRESS_KEY] is (
        OwnedClassification.COLLISION
    )
    assert preflight.manifests == {}
    assert preflight.loki_phase() == ()
    assert preflight.alloy_phase() == ()
    assert preflight.adaptor_phase() == ()
    assert preflight.adaptor_ingress_phase() == ()


def test_preflight_retained_credentials_collision_aborts_without_parsing() -> None:
    existing = _existing_resources()
    assert existing.credentials_secret is not None
    colliding = copy.deepcopy(existing.credentials_secret)
    colliding["metadata"]["labels"]["coriolis.cloudbase.it/component"] = "forged"
    existing = _existing_resources(credentials_secret=colliding)

    def fail_factory(*_: object) -> object:
        raise AssertionError("credential generator must not run on collision")

    preflight = _preflight(existing=existing, password_factory=fail_factory)

    assert preflight.collision_resource_name == "appliance-logging-credentials"
    assert preflight.credentials is None
    assert preflight.manifests == {}
    assert preflight.loki_phase() == ()


def test_preflight_repr_never_exposes_secret_data() -> None:
    preflight = _preflight()

    rendered = repr(preflight)
    for value in (
        "read-pass",
        "write-pass",
        "read-hash-value",
        "write-hash-value",
        "read_password",
        "write_password_hash",
    ):
        assert value not in rendered


def test_logging_existing_resources_readiness_gates() -> None:
    assert _existing_resources().loki_phase_ready() is False
    assert _existing_resources().alloy_phase_ready() is False
    assert _existing_resources().adaptor_phase_ready() is False
    assert _existing_resources().adaptor_ingress_ready() is False

    absent = LoggingExistingResources()
    assert absent.loki_phase_ready() is False
    assert absent.alloy_phase_ready() is False
    assert absent.adaptor_phase_ready() is False
    assert absent.adaptor_ingress_ready() is False

    loki_ready = _existing_resources(
        loki_pvc=_bound_pvc(),
        loki_stateful_set=_ready_stateful_set(),
    )
    assert loki_ready.loki_phase_ready() is True

    alloy_ready = _existing_resources(alloy_deployment=_ready_deployment())
    assert alloy_ready.alloy_phase_ready() is True

    adaptor_ready = _existing_resources(adaptor_deployment=_ready_deployment())
    assert adaptor_ready.adaptor_phase_ready() is True
    assert adaptor_ready.adaptor_ingress_ready() is True


def test_logging_existing_resources_operational_ready_aggregate() -> None:
    assert LoggingExistingResources().operational_ready() is False

    ready_base = {
        "loki_pvc": _bound_pvc(),
        "loki_stateful_set": _ready_stateful_set(),
        "alloy_deployment": _ready_deployment(),
        "adaptor_deployment": _ready_deployment(),
    }
    assert _existing_resources(**ready_base).operational_ready() is True

    for missing_key in (
        "loki_pvc",
        "loki_stateful_set",
        "gateway_service",
        "alloy_deployment",
        "adaptor_deployment",
        "adaptor_service",
        "adaptor_ingress",
    ):
        broken = dict(ready_base, **{missing_key: None})
        assert _existing_resources(**broken).operational_ready() is False


def _bound_pvc() -> dict[str, Any]:
    return {"status": {"phase": "Bound"}}


def _ready_stateful_set() -> dict[str, Any]:
    return {
        "spec": {"replicas": 1},
        "metadata": {"generation": 1},
        "status": {
            "observedGeneration": 1,
            "currentRevision": "rev",
            "updateRevision": "rev",
            "currentReplicas": 1,
            "updatedReplicas": 1,
            "readyReplicas": 1,
            "availableReplicas": 1,
            "unavailableReplicas": 0,
        },
    }


def _ready_deployment() -> dict[str, Any]:
    return {
        "spec": {"replicas": 1},
        "metadata": {"generation": 1},
        "status": {
            "observedGeneration": 1,
            "replicas": 1,
            "updatedReplicas": 1,
            "readyReplicas": 1,
            "availableReplicas": 1,
            "unavailableReplicas": 0,
        },
    }
