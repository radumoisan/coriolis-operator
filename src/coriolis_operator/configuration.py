"""Render Coriolis configuration assets."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from types import MappingProxyType

from jinja2 import Environment, PackageLoader, StrictUndefined

CONFIG_TEMPLATE_MAP = {
    "coriolis-api.wsgi": "coriolis-api.wsgi.j2",
    "wsgi-coriolis.conf": "kubernetes/wsgi-coriolis.conf.j2",
    "vixdisklib.conf": "vixdisklib.conf.j2",
    "api-paste.ini": "api-paste.ini.j2",
    "policy.yml": "policy.yml.j2",
    "coriolis.release": "coriolis.release.j2",
}

_INVALID_INPUT_MESSAGE = "invalid Coriolis configuration input"
_RENDER_FAILURE_MESSAGE = "Coriolis configuration rendering failed"
_INVALID_SENSITIVE_INPUT_MESSAGE = "invalid sensitive Coriolis configuration input"
_SENSITIVE_RENDER_FAILURE_MESSAGE = "sensitive Coriolis configuration rendering failed"

# Exact 2603.4 worker-qualified Kubernetes provider set; retain module maps/templates
# for unavailable providers as upstream provenance.
_EXPORT_PROVIDERS = (
    "openstack",
    "azure",
    "scvmm",
    "vmware",
    "aws",
    "metal",
    "ovirt",
)
_IMPORT_PROVIDERS = (
    "openstack",
    "azure",
    "scvmm",
    "oci",
    "aws",
    "vmware",
    "ovirt",
    "kubevirt",
    "lxd",
    "proxmox",
    "libvirt",
)
_PROVIDERS = tuple(dict.fromkeys((*_EXPORT_PROVIDERS, *_IMPORT_PROVIDERS)))
_EXPORT_MODULES = MappingProxyType(
    {
        "openstack": "coriolis_provider_openstack.ExportProvider",
        "oracle-vm": "coriolis_provider_oracle_vm.ExportProvider",
        "opc": "coriolis_provider_opc.ExportProvider",
        "azure": "coriolis_provider_azure.ExportProvider",
        "scvmm": "coriolis_provider_scvmm.HyperVExportProvider",
        "vmware": "coriolis_provider_vmware_vsphere.ExportProvider",
        "aws": "coriolis_provider_aws.ExportProvider",
        "metal": "coriolis_provider_metal.ExportProvider",
        "ovirt": (
            "coriolis_provider_ovirt_olvm.ExportProvider,"
            "coriolis_provider_ovirt_rhev.ExportProvider"
        ),
        "nutanix": "coriolis_provider_nutanix.ExportProvider",
    }
)
_IMPORT_MODULES = MappingProxyType(
    {
        "openstack": (
            "coriolis_provider_openstack.ImportProvider,"
            "coriolis_provider_vhi.ImportProvider"
        ),
        "oracle-vm": "coriolis_provider_oracle_vm.ImportProvider",
        "opc": "coriolis_provider_opc.ImportProvider",
        "azure": "coriolis_provider_azure.ImportProvider",
        "scvmm": "coriolis_provider_scvmm.ImportProvider",
        "oci": (
            "coriolis_provider_oci.ImportProvider,"
            "coriolis_provider_opca.ImportProvider,"
            "coriolis_provider_o3c.ImportProvider"
        ),
        "aws": "coriolis_provider_aws.ImportProvider",
        "vmware": "coriolis_provider_vmware_vsphere.ImportProvider",
        "ovirt": (
            "coriolis_provider_ovirt_olvm.ImportProvider,"
            "coriolis_provider_ovirt_rhev.ImportProvider"
        ),
        "kubevirt": (
            "coriolis_provider_kubevirt.ImportProvider,"
            "coriolis_provider_harvester.ImportProvider"
        ),
        "lxd": "coriolis_provider_lxd.ImportProvider",
        "proxmox": "coriolis_provider_proxmox.ImportProvider",
        "libvirt": "coriolis_provider_libvirt.ImportProvider",
        "cloudstack": "coriolis_provider_cloudstack.imp.ImportProvider",
    }
)


@dataclass(frozen=True)
class SensitiveCoriolisEndpoints:
    """Non-sensitive dependencies required by the sensitive config template."""

    rabbitmq_host: str
    memcached_host: str
    database_host: str
    keystone_host: str


@dataclass(frozen=True)
class KubernetesCoriolisRenderInputs:
    """Fixed Kubernetes runtime values and dependency endpoints."""

    bind_address: str
    coriolis_port: int
    coriolis_config_dir: str
    coriolis_vmware_vix_disklib_log_dir: str
    endpoints: SensitiveCoriolisEndpoints


@dataclass(frozen=True)
class SensitiveCoriolisCredentials:
    """Credential inputs whose representation deliberately omits all values."""

    rabbitmq_password: str = field(repr=False)
    coriolis_database_password: str = field(repr=False)
    coriolis_keystone_password: str = field(repr=False)
    temp_keypair_password: str = field(repr=False)


class SensitiveCoriolisConfig(Mapping[str, str]):
    """One-key sensitive output mapping that never renders its content in repr."""

    __slots__ = ("_content",)

    def __init__(self, content: str) -> None:
        self._content = content

    def __getitem__(self, key: str) -> str:
        if key != "coriolis.conf":
            raise KeyError(key)
        return self._content

    def __iter__(self) -> Iterator[str]:
        yield "coriolis.conf"

    def __len__(self) -> int:
        return 1

    def __repr__(self) -> str:
        return "SensitiveCoriolisConfig({'coriolis.conf': '<redacted>'})"

    __str__ = __repr__


def _validate_input(value: object, *, path: bool = False) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(_INVALID_INPUT_MESSAGE)
    if "\r" in value or "\n" in value or "\0" in value:
        raise ValueError(_INVALID_INPUT_MESSAGE)
    if path and not PurePosixPath(value).is_absolute():
        raise ValueError(_INVALID_INPUT_MESSAGE)
    return value


def _validate_sensitive_string(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(_INVALID_SENSITIVE_INPUT_MESSAGE)
    if "\r" in value or "\n" in value or "\0" in value:
        raise ValueError(_INVALID_SENSITIVE_INPUT_MESSAGE)
    return value


def render_coriolis_config(
    *,
    inputs: KubernetesCoriolisRenderInputs,
    accepted_version: str,
) -> dict[str, str]:
    """Return the approved non-sensitive Coriolis ConfigMap values."""
    if type(inputs) is not KubernetesCoriolisRenderInputs:
        raise ValueError(_INVALID_INPUT_MESSAGE)
    validated_bind_address = _validate_input(inputs.bind_address)
    validated_config_dir = _validate_input(inputs.coriolis_config_dir, path=True)
    validated_disklib_log_dir = _validate_input(
        inputs.coriolis_vmware_vix_disklib_log_dir, path=True
    )
    validated_version = _validate_input(accepted_version)
    if type(inputs.coriolis_port) is not int or not 1 <= inputs.coriolis_port <= 65535:
        raise ValueError(_INVALID_INPUT_MESSAGE)

    try:
        environment = Environment(
            loader=PackageLoader("coriolis_operator", "templates"),
            autoescape=False,
            keep_trailing_newline=True,
            undefined=StrictUndefined,
        )
        context = {
            "bind_address": validated_bind_address,
            "coriolis_port": inputs.coriolis_port,
            "coriolis_config_dir": validated_config_dir,
            "coriolis_vmware_vix_disklib_log_dir": validated_disklib_log_dir,
            "default_coriolis_docker_images_tag": validated_version,
        }
        return {
            output_name: environment.get_template(template_name).render(context)
            for output_name, template_name in CONFIG_TEMPLATE_MAP.items()
        }
    except Exception:
        raise ValueError(_RENDER_FAILURE_MESSAGE) from None


def render_sensitive_coriolis_config(
    *,
    endpoints: SensitiveCoriolisEndpoints,
    credentials: SensitiveCoriolisCredentials,
) -> SensitiveCoriolisConfig:
    """Return credential-redacted Secret values for the packaged Coriolis config."""
    if type(endpoints) is not SensitiveCoriolisEndpoints:
        raise ValueError(_INVALID_SENSITIVE_INPUT_MESSAGE)
    if type(credentials) is not SensitiveCoriolisCredentials:
        raise ValueError(_INVALID_SENSITIVE_INPUT_MESSAGE)

    rabbitmq_host = _validate_sensitive_string(endpoints.rabbitmq_host)
    memcached_host = _validate_sensitive_string(endpoints.memcached_host)
    database_host = _validate_sensitive_string(endpoints.database_host)
    keystone_host = _validate_sensitive_string(endpoints.keystone_host)
    rabbitmq_password = _validate_sensitive_string(credentials.rabbitmq_password)
    coriolis_database_password = _validate_sensitive_string(
        credentials.coriolis_database_password
    )
    coriolis_keystone_password = _validate_sensitive_string(
        credentials.coriolis_keystone_password
    )
    temp_keypair_password = _validate_sensitive_string(
        credentials.temp_keypair_password
    )

    try:
        environment = Environment(
            loader=PackageLoader("coriolis_operator", "templates"),
            autoescape=False,
            keep_trailing_newline=True,
            undefined=StrictUndefined,
        )
        content = environment.get_template("kubernetes/coriolis.conf.j2").render(
            {
                "rabbitmq_user": "openstack",
                "rabbitmq_password": rabbitmq_password,
                "rabbitmq_host": rabbitmq_host,
                "rabbitmq_port": 5672,
                "coriolis_debug": True,
                "coriolis_log_dir": "/var/log/coriolis",
                "coriolis_export_providers": _EXPORT_PROVIDERS,
                "coriolis_import_providers": _IMPORT_PROVIDERS,
                "coriolis_providers": _PROVIDERS,
                "merged_export_modules": _EXPORT_MODULES,
                "merged_import_modules": _IMPORT_MODULES,
                "compress_transfers": False,
                "enable_coriolis_compressor": False,
                "coriolis_config_dir": "/etc/coriolis",
                "memcached_host": memcached_host,
                "coriolis_database_user": "coriolis",
                "coriolis_database_password": coriolis_database_password,
                "database_host": database_host,
                "coriolis_database_name": "coriolis",
                "keystone_protocol": "http",
                "keystone_host": keystone_host,
                "keystone_public_port": 5000,
                "keystone_internal_port": 5000,
                "coriolis_keystone_user": "coriolis",
                "coriolis_keystone_password": coriolis_keystone_password,
                "coriolis_policy_file": "/etc/coriolis/policy.yml",
                "coriolis_locks_dir_containers": "/opt/coriolis/locks",
                "coriolis_export_dir": "/opt/coriolis/export",
                "temp_keypair_password": temp_keypair_password,
                "coriolis_vmware_vix_disklib_dir": "/opt/coriolis/vmware-vix-disklib",
                "coriolis_vmware_vix_disklib_config_path": (
                    "/etc/coriolis/vixdisklib.conf"
                ),
            }
        )
        return SensitiveCoriolisConfig(content)
    except Exception:
        raise ValueError(_SENSITIVE_RENDER_FAILURE_MESSAGE) from None
