import copy
from dataclasses import FrozenInstanceError

import pytest

from coriolis_operator.ingress import resolve_ingress_settings
from coriolis_operator.reconcile import (
    IngressResourcePreflight,
    OwnedClassification,
    build_api_ingress,
    build_keystone_ingress,
    build_web_ingress,
    preflight_ingress_resources,
)

OWNER = {
    "apiVersion": "coriolis.cloudbase.it/v1alpha1",
    "kind": "CoriolisAppliance",
    "name": "example",
    "uid": "abc-123",
}


def kwargs() -> dict[str, object]:
    return {
        "appliance_name": "example",
        "namespace": "operators",
        "accepted_version": "2603.4",
        "owner": OWNER,
        "settings": resolve_ingress_settings(
            {
                "host": "coriolis.example",
                "ingressClassName": "nginx-public",
                "tls": {"mode": "certManager", "clusterIssuer": "production"},
            }
        ),
    }


def test_ingress_manifests_have_the_frozen_logical_origin_contract() -> None:
    web = build_web_ingress(**kwargs())
    keystone = build_keystone_ingress(**kwargs())
    api = build_api_ingress(**kwargs())

    shared_spec = {
        "ingressClassName": "nginx-public",
        "tls": [{"hosts": ["coriolis.example"], "secretName": "coriolis.example-tls"}],
    }
    cors_annotations = {
        "nginx.ingress.kubernetes.io/ssl-redirect": "true",
        "nginx.ingress.kubernetes.io/enable-cors": "true",
        "nginx.ingress.kubernetes.io/cors-allow-origin": "https://coriolis.example",
        "nginx.ingress.kubernetes.io/cors-allow-methods": (
            "POST, GET, OPTIONS, DELETE, PUT, PATCH"
        ),
        "nginx.ingress.kubernetes.io/cors-allow-headers": (
            "x-requested-with, X-Auth-Token, X-Subject-Token, Content-Type, "
            "origin, authorization, accept, client-security-token"
        ),
        "nginx.ingress.kubernetes.io/cors-allow-credentials": "true",
        "nginx.ingress.kubernetes.io/cors-expose-headers": "X-Subject-Token",
        "nginx.ingress.kubernetes.io/cors-max-age": "1000",
    }

    assert [manifest["metadata"]["name"] for manifest in (web, keystone, api)] == [
        "example-coriolis-web",
        "example-keystone",
        "example-coriolis-api",
    ]
    for manifest, component in zip(
        (web, keystone, api), ("coriolis-web", "keystone", "coriolis-api"), strict=True
    ):
        assert manifest["apiVersion"] == "networking.k8s.io/v1"
        assert manifest["kind"] == "Ingress"
        assert manifest["metadata"]["namespace"] == "operators"
        assert set(manifest["metadata"]) == {
            "name",
            "namespace",
            "labels",
            "annotations",
            "ownerReferences",
        }
        assert manifest["metadata"]["labels"] == {
            "app.kubernetes.io/name": "coriolis",
            "app.kubernetes.io/instance": "example",
            "app.kubernetes.io/version": "2603.4",
            "app.kubernetes.io/component": component,
            "app.kubernetes.io/part-of": "coriolis-appliance",
            "app.kubernetes.io/managed-by": "coriolis-operator",
            "coriolis.cloudbase.it/appliance": "example",
            "coriolis.cloudbase.it/component": component,
        }
        assert manifest["metadata"]["ownerReferences"] == [dict(OWNER, controller=True)]
        assert {
            key: value for key, value in manifest["spec"].items() if key != "rules"
        } == shared_spec
        assert (
            manifest["metadata"]["annotations"] | cors_annotations
            == (manifest["metadata"]["annotations"])
        )
        assert "*" not in manifest["metadata"]["annotations"].values()

    assert web["metadata"]["annotations"] == {
        "coriolis.cloudbase.it/appliance-name": "example",
        "cert-manager.io/cluster-issuer": "production",
        **cors_annotations,
    }
    assert web["spec"]["rules"] == [
        {
            "host": "coriolis.example",
            "http": {
                "paths": [
                    {
                        "path": "/",
                        "pathType": "Prefix",
                        "backend": {
                            "service": {
                                "name": "example-coriolis-web",
                                "port": {"number": 3000},
                            }
                        },
                    }
                ]
            },
        }
    ]

    for manifest, path, service_name, port, rewrite_target in (
        (keystone, "/identity(/|$)(.*)", "example-keystone", 5000, "/v3/$2"),
        (api, "/coriolis(/|$)(.*)", "example-coriolis-api", 7667, "/v1/$2"),
    ):
        assert manifest["metadata"]["annotations"] == {
            "coriolis.cloudbase.it/appliance-name": "example",
            **cors_annotations,
            "nginx.ingress.kubernetes.io/use-regex": "true",
            "nginx.ingress.kubernetes.io/rewrite-target": rewrite_target,
        }
        assert manifest["spec"]["rules"] == [
            {
                "host": "coriolis.example",
                "http": {
                    "paths": [
                        {
                            "path": path,
                            "pathType": "ImplementationSpecific",
                            "backend": {
                                "service": {
                                    "name": service_name,
                                    "port": {"number": port},
                                }
                            },
                        }
                    ]
                },
            }
        ]


def test_ingress_manifests_use_existing_tls_secret_without_cert_manager() -> None:
    ingress = {
        "host": "coriolis.internal.example",
        "tls": {"mode": "existingSecret", "tlsSecretName": "coriolis-tls"},
    }
    before = copy.deepcopy(ingress)
    settings = resolve_ingress_settings(ingress)
    manifests = (
        build_web_ingress(**dict(kwargs(), settings=settings)),
        build_keystone_ingress(**dict(kwargs(), settings=settings)),
        build_api_ingress(**dict(kwargs(), settings=settings)),
    )

    assert ingress == before
    for manifest in manifests:
        assert manifest["spec"]["tls"] == [
            {
                "hosts": ["coriolis.internal.example"],
                "secretName": "coriolis-tls",
            }
        ]
        assert not any(
            key.startswith("cert-manager.io/")
            for key in manifest["metadata"]["annotations"]
        )


def test_ingress_preflight_is_ordered_guarded_and_does_not_mutate_inputs() -> None:
    arguments = kwargs()
    absent = preflight_ingress_resources(
        **arguments,
        web_ingress=None,
        keystone_ingress=None,
        api_ingress=None,
    )

    assert arguments == kwargs()
    assert absent == IngressResourcePreflight(
        OwnedClassification.ABSENT,
        OwnedClassification.ABSENT,
        OwnedClassification.ABSENT,
        (
            build_web_ingress(**kwargs()),
            build_keystone_ingress(**kwargs()),
            build_api_ingress(**kwargs()),
        ),
    )
    with pytest.raises(FrozenInstanceError):
        absent.web_classification = OwnedClassification.MANAGED  # type: ignore[misc]

    existing = copy.deepcopy(absent.manifests)
    for resource, resource_version in zip(existing, ("11", "12", "13"), strict=True):
        resource["metadata"]["resourceVersion"] = resource_version
    before_existing = copy.deepcopy(existing)
    managed = preflight_ingress_resources(
        **kwargs(),
        web_ingress=existing[0],
        keystone_ingress=existing[1],
        api_ingress=existing[2],
    )

    assert existing == before_existing
    assert managed.web_classification is OwnedClassification.MANAGED
    assert managed.keystone_classification is OwnedClassification.MANAGED
    assert managed.api_classification is OwnedClassification.MANAGED
    assert [
        manifest["metadata"]["resourceVersion"] for manifest in managed.manifests
    ] == ["11", "12", "13"]


@pytest.mark.parametrize("collision_index", [0, 1, 2])
def test_ingress_preflight_is_all_or_nothing_on_any_collision(
    collision_index: int,
) -> None:
    manifests = [
        build_web_ingress(**kwargs()),
        build_keystone_ingress(**kwargs()),
        build_api_ingress(**kwargs()),
    ]
    manifests[collision_index]["metadata"]["labels"][
        "coriolis.cloudbase.it/component"
    ] = "other"
    before = copy.deepcopy(manifests)

    result = preflight_ingress_resources(
        **kwargs(),
        web_ingress=manifests[0],
        keystone_ingress=manifests[1],
        api_ingress=manifests[2],
    )

    assert manifests == before
    assert OwnedClassification.COLLISION in (
        result.web_classification,
        result.keystone_classification,
        result.api_classification,
    )
    assert result.manifests == ()


def test_ingress_preflight_rejects_a_managed_ingress_without_resource_version() -> None:
    web = build_web_ingress(**kwargs())

    with pytest.raises(
        ValueError, match="^managed resource is missing resourceVersion$"
    ):
        preflight_ingress_resources(
            **kwargs(),
            web_ingress=web,
            keystone_ingress=None,
            api_ingress=None,
        )
