from pathlib import Path

import yaml

from coriolis_operator.reconcile import SUPPORTED_INITIAL_VERSION, SUPPORTED_PROFILE

CRD_PATH = (
    Path(__file__).resolve().parents[2] / "helm" / "crds" / "coriolisappliances.yaml"
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
    assert spec["required"] == ["version"]
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
