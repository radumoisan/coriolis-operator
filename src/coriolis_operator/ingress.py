"""Pure ingress settings validation for Coriolis appliances."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

DEFAULT_INGRESS_HOST = "coriolis.app.cloudbase.wiki"
DEFAULT_INGRESS_CLASS_NAME = "nginx"
DEFAULT_CLUSTER_ISSUER = "letsencrypt"

_INVALID_INGRESS_SETTINGS = "invalid ingress settings"
_DNS_SUBDOMAIN_RE = re.compile(
    r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*"
)


@dataclass(frozen=True)
class IngressSettings:
    """Resolved ingress values for a future same-namespace Ingress."""

    host: str
    ingress_class_name: str
    tls_mode: str
    tls_secret_name: str
    annotations: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "annotations", MappingProxyType(dict(self.annotations))
        )


def _invalid() -> ValueError:
    return ValueError(_INVALID_INGRESS_SETTINGS)


def _validated_dns_name(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or any(unicodedata.category(char) == "Cc" for char in value)
    ):
        raise _invalid()
    if len(value) > 253 or not _DNS_SUBDOMAIN_RE.fullmatch(value):
        raise _invalid()
    if any(len(label) > 63 for label in value.split(".")):
        raise _invalid()
    return value


def _mapping_or_default(value: object | None) -> Mapping[str, object]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _invalid()
    return value


def resolve_ingress_settings(
    spec_ingress: Mapping[str, object] | None,
) -> IngressSettings:
    """Resolve and validate per-appliance ingress input without Kubernetes I/O."""
    ingress = _mapping_or_default(spec_ingress)
    host = _validated_dns_name(ingress.get("host", DEFAULT_INGRESS_HOST))
    ingress_class_name = _validated_dns_name(
        ingress.get("ingressClassName", DEFAULT_INGRESS_CLASS_NAME)
    )
    tls = _mapping_or_default(ingress.get("tls"))
    mode = tls.get("mode", "certManager")
    if type(mode) is not str or mode not in {"certManager", "existingSecret"}:
        raise _invalid()

    if mode == "certManager":
        if "tlsSecretName" in tls:
            raise _invalid()
        cluster_issuer = _validated_dns_name(
            tls.get("clusterIssuer", DEFAULT_CLUSTER_ISSUER)
        )
        tls_secret_name = _validated_dns_name(f"{host}-tls")
        return IngressSettings(
            host=host,
            ingress_class_name=ingress_class_name,
            tls_mode=mode,
            tls_secret_name=tls_secret_name,
            annotations=MappingProxyType(
                {"cert-manager.io/cluster-issuer": cluster_issuer}
            ),
        )

    if "clusterIssuer" in tls or "tlsSecretName" not in tls:
        raise _invalid()
    tls_secret_name = _validated_dns_name(tls["tlsSecretName"])
    return IngressSettings(
        host=host,
        ingress_class_name=ingress_class_name,
        tls_mode=mode,
        tls_secret_name=tls_secret_name,
        annotations=MappingProxyType({}),
    )
