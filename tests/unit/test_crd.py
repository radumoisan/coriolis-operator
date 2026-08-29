from pathlib import Path

import yaml

from coriolis_operator.reconcile import SUPPORTED_INITIAL_VERSION, SUPPORTED_PROFILE

CRD_PATH = (
    Path(__file__).resolve().parents[2] / "helm" / "crds" / "coriolisappliances.yaml"
)
SAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "samples"
    / "coriolisappliance.yaml"
)


def _load_schema() -> dict:
    with CRD_PATH.open() as crd_file:
        crd = yaml.safe_load(crd_file)
    return crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        if key in value:
            return True
        return any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


def test_crd_defines_core_profile_with_enum_and_default() -> None:
    schema = _load_schema()
    profile = schema["properties"]["spec"]["properties"]["profile"]
    assert profile == {
        "type": "string",
        "enum": [SUPPORTED_PROFILE],
        "default": SUPPORTED_PROFILE,
    }


def test_crd_keeps_version_required_non_empty_without_enum() -> None:
    schema = _load_schema()
    spec = schema["properties"]["spec"]
    assert spec["required"] == ["version", "logging"]
    version = spec["properties"]["version"]
    assert version["type"] == "string"
    assert version["minLength"] == 1
    assert "enum" not in version


def test_crd_allows_accepted_version_status_field() -> None:
    schema = _load_schema()
    accepted_version = schema["properties"]["status"]["properties"]["acceptedVersion"]
    assert accepted_version == {"type": "string", "minLength": 1}


def test_crd_initial_version_matches_controller_constant() -> None:
    assert SUPPORTED_INITIAL_VERSION == "2603.4"


def test_crd_has_no_cel_immutability_rules() -> None:
    schema = _load_schema()
    assert not _contains_key(schema, "x-kubernetes-validations")


def test_crd_defines_structural_ingress_defaults_and_tls_mode_shape() -> None:
    schema = _load_schema()
    ingress = schema["properties"]["spec"]["properties"]["ingress"]

    assert ingress["type"] == "object"
    assert ingress["default"] == {
        "host": "coriolis.app.cloudbase.wiki",
        "ingressClassName": "nginx",
        "tls": {"mode": "certManager"},
    }
    assert ingress["properties"]["host"] == {
        "type": "string",
        "default": "coriolis.app.cloudbase.wiki",
    }
    assert ingress["properties"]["ingressClassName"] == {
        "type": "string",
        "default": "nginx",
    }
    assert ingress["properties"]["tls"] == {
        "type": "object",
        "default": {"mode": "certManager"},
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["certManager", "existingSecret"],
                "default": "certManager",
            },
            "clusterIssuer": {"type": "string"},
            "tlsSecretName": {"type": "string"},
        },
    }


def test_crd_adds_optional_storage_and_resources_without_defaults() -> None:
    schema = _load_schema()
    spec = schema["properties"]["spec"]

    assert spec["required"] == ["version", "logging"]
    assert spec["properties"]["storage"] == {
        "type": "object",
        "properties": {
            "mariadb": {
                "type": "object",
                "required": ["storageClassName", "size"],
                "properties": {
                    "storageClassName": {"type": "string", "minLength": 1},
                    "size": {"type": "string", "minLength": 1},
                },
            },
            "rabbitmq": {
                "type": "object",
                "required": ["storageClassName", "size"],
                "properties": {
                    "storageClassName": {"type": "string", "minLength": 1},
                    "size": {"type": "string", "minLength": 1},
                },
            },
        },
    }
    assert spec["properties"]["resources"] == {
        "type": "object",
        "properties": {
            "mariadb": {
                "type": "object",
                "required": ["requests", "limits"],
                "properties": {
                    resource_type: {
                        "type": "object",
                        "required": ["cpu", "memory"],
                        "properties": {
                            "cpu": {"type": "string", "minLength": 1},
                            "memory": {"type": "string", "minLength": 1},
                        },
                    }
                    for resource_type in ("requests", "limits")
                },
            },
            "rabbitmq": {
                "type": "object",
                "required": ["requests", "limits"],
                "properties": {
                    resource_type: {
                        "type": "object",
                        "required": ["cpu", "memory"],
                        "properties": {
                            "cpu": {"type": "string", "minLength": 1},
                            "memory": {"type": "string", "minLength": 1},
                        },
                    }
                    for resource_type in ("requests", "limits")
                },
            },
        },
    }


def test_sample_uses_explicit_dev_cert_manager_ingress_settings() -> None:
    with SAMPLE_PATH.open() as sample_file:
        sample = yaml.safe_load(sample_file)

    assert sample["spec"]["ingress"] == {
        "host": "coriolis.app.cloudbase.wiki",
        "ingressClassName": "nginx",
        "tls": {"mode": "certManager", "clusterIssuer": "letsencrypt"},
    }


def _logging_component_resources_schema() -> dict:
    return {
        resource_type: {
            "type": "object",
            "required": ["cpu", "memory"],
            "properties": {
                "cpu": {"type": "string", "minLength": 1},
                "memory": {"type": "string", "minLength": 1},
            },
        }
        for resource_type in ("requests", "limits")
    }


def test_crd_requires_logging_without_defaults() -> None:
    schema = _load_schema()
    spec = schema["properties"]["spec"]

    assert spec["required"] == ["version", "logging"]
    logging = spec["properties"]["logging"]
    assert logging["type"] == "object"
    assert logging["required"] == ["retentionHours", "storage", "resources"]
    assert logging.get("default") is None
    assert logging["properties"]["retentionHours"] == {
        "type": "integer",
        "minimum": 1,
    }
    assert logging["properties"]["storage"] == {
        "type": "object",
        "required": ["loki"],
        "properties": {
            "loki": {
                "type": "object",
                "required": ["storageClassName", "size"],
                "properties": {
                    "storageClassName": {"type": "string", "minLength": 1},
                    "size": {"type": "string", "minLength": 1},
                },
            },
        },
    }
    assert logging["properties"]["resources"] == {
        "type": "object",
        "required": ["loki", "gateway", "alloy", "adaptor"],
        "properties": {
            component: {
                "type": "object",
                "required": ["requests", "limits"],
                "properties": _logging_component_resources_schema(),
            }
            for component in ("loki", "gateway", "alloy", "adaptor")
        },
    }


def test_sample_uses_explicit_logging_settings() -> None:
    with SAMPLE_PATH.open() as sample_file:
        sample = yaml.safe_load(sample_file)

    assert sample["spec"]["logging"] == {
        "retentionHours": 24,
        "storage": {"loki": {"storageClassName": "local-path", "size": "10Gi"}},
        "resources": {
            "loki": {
                "requests": {"cpu": "250m", "memory": "512Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            },
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
    }
