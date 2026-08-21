import copy
from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from coriolis_operator.ingress import IngressSettings, resolve_ingress_settings


def test_resolve_ingress_settings_defaults_when_omitted_or_empty() -> None:
    expected = IngressSettings(
        host="coriolis.app.cloudbase.wiki",
        ingress_class_name="nginx",
        tls_mode="certManager",
        tls_secret_name="coriolis.app.cloudbase.wiki-tls",
        annotations=MappingProxyType({"cert-manager.io/cluster-issuer": "letsencrypt"}),
    )

    assert resolve_ingress_settings(None) == expected
    assert resolve_ingress_settings({}) == expected
    assert resolve_ingress_settings({"tls": {}}) == expected


def test_resolve_ingress_settings_cert_manager_custom_issuer() -> None:
    settings = resolve_ingress_settings(
        {
            "host": "api.coriolis.example",
            "ingressClassName": "community-nginx",
            "tls": {"mode": "certManager", "clusterIssuer": "production"},
        }
    )

    assert settings.host == "api.coriolis.example"
    assert settings.ingress_class_name == "community-nginx"
    assert settings.tls_mode == "certManager"
    assert settings.tls_secret_name == "api.coriolis.example-tls"
    assert settings.annotations == {"cert-manager.io/cluster-issuer": "production"}


def test_resolve_ingress_settings_existing_secret_has_no_cert_manager_annotation() -> (
    None
):
    settings = resolve_ingress_settings(
        {
            "host": "coriolis.internal.example",
            "tls": {"mode": "existingSecret", "tlsSecretName": "coriolis-tls"},
        }
    )

    assert settings.tls_mode == "existingSecret"
    assert settings.tls_secret_name == "coriolis-tls"
    assert settings.annotations == {}


@pytest.mark.parametrize(
    "ingress",
    [
        [],
        {"tls": []},
        {"tls": {"mode": "unsupported"}},
        {"tls": {"mode": "certManager", "tlsSecretName": "provided"}},
        {"tls": {"mode": "existingSecret"}},
        {
            "tls": {
                "mode": "existingSecret",
                "tlsSecretName": "provided",
                "clusterIssuer": "letsencrypt",
            }
        },
    ],
)
def test_resolve_ingress_settings_rejects_invalid_and_mixed_tls_modes(
    ingress: object,
) -> None:
    with pytest.raises(ValueError, match="^invalid ingress settings$"):
        resolve_ingress_settings(ingress)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "ingress",
    [
        {"host": "UPPER.example"},
        {"host": "bad_host.example"},
        {"host": "bad\n.example"},
        {"ingressClassName": "bad\0class"},
        {"tls": {"clusterIssuer": "issuer\rname"}},
        {"tls": {"clusterIssuer": "issuer\x7fname"}},
        {"tls": {"mode": "existingSecret", "tlsSecretName": "secret/name"}},
        {"host": "a" * 60},
        {"host": "a" * 254},
        {"host": "a" * 64 + ".example"},
        {"host": True},
        {"ingressClassName": type("StringSubclass", (str,), {})("nginx")},
        {"tls": {"mode": True}},
        {
            "tls": {
                "mode": "existingSecret",
                "tlsSecretName": type("StringSubclass", (str,), {})("secret"),
            }
        },
    ],
)
def test_resolve_ingress_settings_rejects_dns_length_control_and_type_failures(
    ingress: object,
) -> None:
    with pytest.raises(ValueError) as excinfo:
        resolve_ingress_settings(ingress)  # type: ignore[arg-type]

    assert str(excinfo.value) == "invalid ingress settings"


def test_ingress_settings_and_annotations_are_immutable_without_input_mutation() -> (
    None
):
    ingress = {
        "host": "api.coriolis.example",
        "tls": {"mode": "certManager", "clusterIssuer": "production"},
    }
    before = copy.deepcopy(ingress)

    settings = resolve_ingress_settings(ingress)

    assert ingress == before
    with pytest.raises(FrozenInstanceError):
        settings.host = "other.example"  # type: ignore[misc]
    with pytest.raises(TypeError):
        settings.annotations["other"] = "value"  # type: ignore[index]


def test_ingress_settings_copies_annotations_to_an_immutable_mapping() -> None:
    annotations = {"cert-manager.io/cluster-issuer": "letsencrypt"}
    settings = IngressSettings(
        host="coriolis.app.cloudbase.wiki",
        ingress_class_name="nginx",
        tls_mode="certManager",
        tls_secret_name="coriolis.app.cloudbase.wiki-tls",
        annotations=annotations,
    )
    annotations["cert-manager.io/cluster-issuer"] = "changed"

    assert settings.annotations == {"cert-manager.io/cluster-issuer": "letsencrypt"}
    with pytest.raises(TypeError):
        settings.annotations["other"] = "value"  # type: ignore[index]
