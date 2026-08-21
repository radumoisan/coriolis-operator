import base64
import hashlib
from dataclasses import FrozenInstanceError, fields, replace
from importlib.resources import files

import pytest

from coriolis_operator import configuration
from coriolis_operator.configuration import (
    SensitiveCoriolisConfig,
    SensitiveCoriolisCredentials,
    SensitiveCoriolisEndpoints,
    render_coriolis_config,
    render_sensitive_coriolis_config,
)
from coriolis_operator.reconcile import (
    CORIOLIS_CONFIG_KEYS,
    build_coriolis_config_map,
    build_coriolis_config_secret,
)

RENDER_KWARGS = {
    "bind_address": "127.0.0.1",
    "coriolis_port": 8443,
    "coriolis_config_dir": "/etc/coriolis",
    "coriolis_vmware_vix_disklib_log_dir": "/var/log/coriolis/vixdisklib",
    "accepted_version": "2603.4",
}
CONFIG_OUTPUTS = {
    "coriolis-api.wsgi",
    "wsgi-coriolis.conf",
    "vixdisklib.conf",
    "api-paste.ini",
    "policy.yml",
    "coriolis.release",
}
SENSITIVE_ENDPOINTS = SensitiveCoriolisEndpoints(
    rabbitmq_host="rabbitmq.synthetic.test",
    rabbitmq_port=5671,
    memcached_host="memcached.synthetic.test",
    database_host="database.synthetic.test",
    keystone_protocol="https",
    keystone_host="keystone.synthetic.test",
    keystone_public_port=5000,
    keystone_internal_port=35357,
)
SENSITIVE_CREDENTIALS = SensitiveCoriolisCredentials(
    rabbitmq_password="RABBIT_SENTINEL_41e9",
    coriolis_database_password="DATABASE_SENTINEL_7c2a",
    coriolis_keystone_password="KEYSTONE_SENTINEL_9b64",
    temp_keypair_password="KEYPAIR_SENTINEL_5d03",
)
PROVIDER_SECTIONS = (
    "[openstack_migration_provider]",
    "[oracle_vm_migration_provider]",
    "[opc_migration_provider]",
    "[azure_migration_provider]",
    "[scvmm_migration_provider]",
    "[vmware_vsphere_migration_provider]",
    "[aws_migration_provider]",
    "[metal_migration_provider]",
    "[ovirt_migration_provider]",
    "[nutanix_migration_provider]",
    "[oci_migration_provider]",
    "[kubevirt_migration_provider]",
    "[lxd_migration_provider]",
    "[proxmox_migration_provider]",
    "[libvirt_migration_provider]",
    "[cloudstack_migration_provider]",
)
PROVIDER_MODULES = (
    "coriolis_provider_openstack.ExportProvider",
    "coriolis_provider_oracle_vm.ExportProvider",
    "coriolis_provider_opc.ExportProvider",
    "coriolis_provider_azure.ExportProvider",
    "coriolis_provider_scvmm.HyperVExportProvider",
    "coriolis_provider_vmware_vsphere.ExportProvider",
    "coriolis_provider_aws.ExportProvider",
    "coriolis_provider_metal.ExportProvider",
    "coriolis_provider_ovirt_olvm.ExportProvider,coriolis_provider_ovirt_rhev.ExportProvider",
    "coriolis_provider_nutanix.ExportProvider",
    "coriolis_provider_openstack.ImportProvider,coriolis_provider_vhi.ImportProvider",
    "coriolis_provider_oracle_vm.ImportProvider",
    "coriolis_provider_opc.ImportProvider",
    "coriolis_provider_azure.ImportProvider",
    "coriolis_provider_scvmm.ImportProvider",
    "coriolis_provider_oci.ImportProvider,coriolis_provider_opca.ImportProvider,coriolis_provider_o3c.ImportProvider",
    "coriolis_provider_aws.ImportProvider",
    "coriolis_provider_vmware_vsphere.ImportProvider",
    "coriolis_provider_ovirt_olvm.ImportProvider,coriolis_provider_ovirt_rhev.ImportProvider",
    "coriolis_provider_kubevirt.ImportProvider,coriolis_provider_harvester.ImportProvider",
    "coriolis_provider_lxd.ImportProvider",
    "coriolis_provider_proxmox.ImportProvider",
    "coriolis_provider_libvirt.ImportProvider",
    "coriolis_provider_cloudstack.imp.ImportProvider",
)
OWNER = {
    "apiVersion": "coriolis.cloudbase.it/v1alpha1",
    "kind": "CoriolisAppliance",
    "name": "example",
    "uid": "abc-123",
}


def test_render_coriolis_config_is_exact_deterministic_and_non_sensitive() -> None:
    first = render_coriolis_config(**RENDER_KWARGS)
    second = render_coriolis_config(**RENDER_KWARGS)
    daemon_process = (
        "WSGIDaemonProcess coriolis-api processes=5 threads=1 "
        "user=${APACHE_RUN_USER} group=${APACHE_RUN_GROUP} "
        "display-name=coriolis-api"
    )
    log_format = (
        'LogFormat "%{X-Forwarded-For}i %l %u %t \\"%r\\" %>s %b %D '
        '\\"%{Referer}i\\" \\"%{User-Agent}i\\"" logformat'
    )

    assert first == second
    assert set(first) == CONFIG_OUTPUTS == CORIOLIS_CONFIG_KEYS
    assert first["coriolis-api.wsgi"] == (
        "from coriolis import service\n\napplication = service.get_application()\n"
    )
    assert (
        first["wsgi-coriolis.conf"]
        == """LoadModule ssl_module /usr/lib/apache2/modules/mod_ssl.so
Listen 127.0.0.1:8443

ServerSignature Off
ServerTokens Prod
TraceEnable off
TimeOut 60
KeepAliveTimeout 60

ErrorLog \"/var/log/coriolis/coriolis-error.log\"
<IfModule log_config_module>
    CustomLog \"/var/log/coriolis/coriolis-api.log\" common
</IfModule>

<VirtualHost 127.0.0.1:8443>
    ServerName https://127.0.0.1:8443
    __DAEMON_PROCESS__
    WSGIProcessGroup coriolis-api
    WSGIScriptAlias / /usr/local/bin/coriolis-api.wsgi
    WSGIApplicationGroup %{GLOBAL}
    WSGIPassAuthorization On
    <IfVersion >= 2.4>
      ErrorLogFormat \"%{cu}t %M\"
    </IfVersion>
    <Directory /usr/local/bin>
        <IfVersion >= 2.4>
            Require all granted
        </IfVersion>
        <IfVersion < 2.4>
            Order allow,deny
            Allow from all
        </IfVersion>
    </Directory>

    ErrorLog \"/var/log/coriolis/coriolis-error.log\"
    __LOG_FORMAT__
    CustomLog \"/var/log/coriolis/coriolis-api.log\" logformat

    SSLEngine on
    SSLCertificateFile \"/etc/coriolis/ssl/coriolis.crt\"
    SSLCertificateKeyFile \"/etc/coriolis/ssl/coriolis.key\"
</VirtualHost>
""".replace("__DAEMON_PROCESS__", daemon_process).replace("__LOG_FORMAT__", log_format)
    )
    assert first["vixdisklib.conf"] == "tmpDirectory = /var/log/coriolis/vixdisklib\n"
    assert first["coriolis.release"] == "2603.4\n"
    template_root = files("coriolis_operator").joinpath("templates")
    for output_name, template_name in {
        "api-paste.ini": "api-paste.ini.j2",
        "policy.yml": "policy.yml.j2",
    }.items():
        assert first[output_name] == template_root.joinpath(template_name).read_text()
    assert all(value.endswith("\n") for value in first.values())
    assert all(
        "{{" not in value and "{%" not in value and "{#" not in value
        for value in first.values()
    )
    assert "coriolis.conf" not in first
    assert not {"coriolis_database_password", "provider", "credentials"} & set(first)


def test_rendered_values_compose_directly_with_config_map_builder() -> None:
    values = render_coriolis_config(**RENDER_KWARGS)

    body = build_coriolis_config_map(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner={
            "apiVersion": "coriolis.cloudbase.it/v1alpha1",
            "kind": "CoriolisAppliance",
            "name": "example",
            "uid": "abc-123",
        },
        values=values,
    )

    assert body["data"] == values


def test_package_resources_include_templates_and_attribution() -> None:
    template_root = files("coriolis_operator").joinpath("templates")

    assert {child.name for child in template_root.iterdir()} >= {
        "coriolis-api.wsgi.j2",
        "wsgi-coriolis.conf.j2",
        "vixdisklib.conf.j2",
        "api-paste.ini.j2",
        "policy.yml.j2",
        "coriolis.release.j2",
        "LICENSE.apache-2.0",
        "SOURCE.md",
        "coriolis.conf.j2",
        "providers",
    }
    assert {child.name for child in template_root.joinpath("providers").iterdir()} == {
        "openstack.conf.j2",
        "oracle-vm.conf.j2",
        "opc.conf.j2",
        "azure.conf.j2",
        "scvmm.conf.j2",
        "vmware.conf.j2",
        "aws.conf.j2",
        "metal.conf.j2",
        "ovirt.conf.j2",
        "nutanix.conf.j2",
        "oci.conf.j2",
        "kubevirt.conf.j2",
        "lxd.conf.j2",
        "proxmox.conf.j2",
        "libvirt.conf.j2",
        "cloudstack.conf.j2",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bind_address", 1),
        ("coriolis_config_dir", 1),
        ("coriolis_vmware_vix_disklib_log_dir", 1),
        ("accepted_version", 1),
        ("coriolis_port", True),
        ("coriolis_port", "8443"),
        ("coriolis_port", 0),
        ("coriolis_port", 65536),
        ("bind_address", ""),
        ("accepted_version", "   "),
        ("coriolis_config_dir", "relative/path"),
        ("coriolis_vmware_vix_disklib_log_dir", "relative/path"),
        ("bind_address", "127.0.0.1\rmalicious"),
        ("coriolis_config_dir", "/etc/coriolis\nmalicious"),
        ("accepted_version", "2603.4\0malicious"),
    ],
)
def test_render_rejects_invalid_values_with_fixed_value_safe_error(
    field: str, value: object
) -> None:
    kwargs = dict(RENDER_KWARGS)
    kwargs[field] = value

    with pytest.raises(ValueError) as excinfo:
        render_coriolis_config(**kwargs)  # type: ignore[arg-type]

    assert str(excinfo.value) == "invalid Coriolis configuration input"
    assert "malicious" not in str(excinfo.value)


def test_render_failure_is_fixed_and_has_no_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenEnvironment:
        def __init__(self, **_: object) -> None:
            pass

        def get_template(self, _: str) -> object:
            raise RuntimeError("input-secret")

    monkeypatch.setattr(configuration, "Environment", BrokenEnvironment)

    with pytest.raises(ValueError) as excinfo:
        render_coriolis_config(**RENDER_KWARGS)

    assert str(excinfo.value) == "Coriolis configuration rendering failed"
    assert excinfo.value.__cause__ is None
    assert "input-secret" not in str(excinfo.value)


def test_sensitive_records_are_exact_frozen_and_redact_credentials() -> None:
    assert tuple(field.name for field in fields(SensitiveCoriolisEndpoints)) == (
        "rabbitmq_host",
        "rabbitmq_port",
        "memcached_host",
        "database_host",
        "keystone_protocol",
        "keystone_host",
        "keystone_public_port",
        "keystone_internal_port",
    )
    assert tuple(field.name for field in fields(SensitiveCoriolisCredentials)) == (
        "rabbitmq_password",
        "coriolis_database_password",
        "coriolis_keystone_password",
        "temp_keypair_password",
    )
    with pytest.raises(FrozenInstanceError):
        SENSITIVE_ENDPOINTS.rabbitmq_host = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        SENSITIVE_CREDENTIALS.rabbitmq_password = "other"  # type: ignore[misc]

    credential_representation = repr(SENSITIVE_CREDENTIALS)
    assert credential_representation == str(SENSITIVE_CREDENTIALS)
    for sentinel in SENSITIVE_CREDENTIALS.__dict__.values():
        assert sentinel not in credential_representation


def test_render_sensitive_config_is_deterministic_exact_and_redacted() -> None:
    first = render_sensitive_coriolis_config(
        endpoints=SENSITIVE_ENDPOINTS, credentials=SENSITIVE_CREDENTIALS
    )
    second = render_sensitive_coriolis_config(
        endpoints=SENSITIVE_ENDPOINTS, credentials=SENSITIVE_CREDENTIALS
    )
    content = first["coriolis.conf"]

    assert type(first) is SensitiveCoriolisConfig
    assert tuple(first) == ("coriolis.conf",)
    assert len(first) == 1
    assert first.keys() == {"coriolis.conf"}
    with pytest.raises(KeyError):
        first["other"]
    assert repr(first) == "SensitiveCoriolisConfig({'coriolis.conf': '<redacted>'})"
    assert str(first) == repr(first)
    assert (
        hashlib.sha256(content.encode()).digest()
        == hashlib.sha256(second["coriolis.conf"].encode()).digest()
    )
    assert content.endswith("\n")
    assert not any(token in content for token in ("{{", "{%", "{#"))
    for sentinel in SENSITIVE_CREDENTIALS.__dict__.values():
        assert sentinel not in repr(first)


def test_sensitive_render_has_frozen_providers_and_fixed_values() -> None:
    content = render_sensitive_coriolis_config(
        endpoints=SENSITIVE_ENDPOINTS, credentials=SENSITIVE_CREDENTIALS
    )["coriolis.conf"]

    assert all(content.count(section) == 1 for section in PROVIDER_SECTIONS)
    assert [content.index(section) for section in PROVIDER_SECTIONS] == sorted(
        content.index(section) for section in PROVIDER_SECTIONS
    )
    assert [content.index(module) for module in PROVIDER_MODULES] == sorted(
        content.index(module) for module in PROVIDER_MODULES
    )
    assert all(content.count(module) == 1 for module in PROVIDER_MODULES)
    for fixed_line in (
        "messaging_transport_url = rabbit://openstack:RABBIT_SENTINEL_41e9@rabbitmq.synthetic.test:5671/",
        "debug = True",
        "log_dir = /var/log/coriolis",
        "compress_transfers = False",
        "ssl_ca_file = /etc/coriolis/ssl/ca/coriolis-ca.crt",
        "backend_argument = url:memcached.synthetic.test:11211",
        "connection = mysql://coriolis:DATABASE_SENTINEL_7c2a@database.synthetic.test/coriolis",
        "auth_uri = https://keystone.synthetic.test:5000/v3",
        "auth_url = https://keystone.synthetic.test:35357/v3",
        "username = coriolis",
        "policy_file = /etc/coriolis/policy.yml",
        "lock_path = /opt/coriolis/locks",
        "export_base_path = /opt/coriolis/export",
        "temp_keypair_password = KEYPAIR_SENTINEL_5d03",
        "vixdisklib_library_directory = /opt/coriolis/vmware-vix-disklib",
        "vixdisklib_config_location = /etc/coriolis/vixdisklib.conf",
    ):
        assert fixed_line in content
    assert "compressor_address" not in content


def test_sensitive_credentials_only_render_at_contracted_locations() -> None:
    content = render_sensitive_coriolis_config(
        endpoints=SENSITIVE_ENDPOINTS, credentials=SENSITIVE_CREDENTIALS
    )["coriolis.conf"]

    for sentinel, expected_count in zip(
        SENSITIVE_CREDENTIALS.__dict__.values(), (1, 1, 2, 1), strict=True
    ):
        assert content.count(sentinel) == expected_count
    for forbidden in (
        "DATABASE_ADMIN_SENTINEL",
        "KEYSTONE_ADMIN_SENTINEL",
        "RABBITMQ_EXTERNAL_SENTINEL",
        "compressor.synthetic.test",
    ):
        assert forbidden not in content


def test_sensitive_config_composes_only_with_secret_builder() -> None:
    values = render_sensitive_coriolis_config(
        endpoints=SENSITIVE_ENDPOINTS, credentials=SENSITIVE_CREDENTIALS
    )
    secret = build_coriolis_config_secret(
        appliance_name="example",
        namespace="operators",
        accepted_version="2603.4",
        owner=OWNER,
        values=values,
    )

    assert set(secret["data"]) == {"coriolis.conf"}
    assert (
        base64.b64decode(secret["data"]["coriolis.conf"]).decode()
        == values["coriolis.conf"]
    )
    with pytest.raises(ValueError):
        build_coriolis_config_map(
            appliance_name="example",
            namespace="operators",
            accepted_version="2603.4",
            owner=OWNER,
            values=values,
        )


@pytest.mark.parametrize(
    ("record", "field_name", "value"),
    [
        ("endpoints", "rabbitmq_host", ""),
        ("endpoints", "memcached_host", "   "),
        ("endpoints", "database_host", "host\rmalicious"),
        ("endpoints", "keystone_host", "host\nmalicious"),
        ("credentials", "rabbitmq_password", "password\0malicious"),
        ("credentials", "coriolis_database_password", 1),
        ("endpoints", "rabbitmq_host", type("StringSubclass", (str,), {})("host")),
        ("endpoints", "rabbitmq_port", True),
        ("endpoints", "rabbitmq_port", "5671"),
        ("endpoints", "rabbitmq_port", 0),
        ("endpoints", "rabbitmq_port", 65536),
        ("endpoints", "keystone_public_port", type("IntSubclass", (int,), {})(5000)),
        ("endpoints", "keystone_protocol", "ftp"),
    ],
)
def test_sensitive_render_rejects_invalid_values_without_leakage(
    record: str, field_name: str, value: object
) -> None:
    endpoints = (
        replace(SENSITIVE_ENDPOINTS, **{field_name: value})
        if record == "endpoints"
        else SENSITIVE_ENDPOINTS
    )
    credentials = (
        replace(SENSITIVE_CREDENTIALS, **{field_name: value})
        if record == "credentials"
        else SENSITIVE_CREDENTIALS
    )

    with pytest.raises(ValueError) as excinfo:
        render_sensitive_coriolis_config(endpoints=endpoints, credentials=credentials)

    assert str(excinfo.value) == "invalid sensitive Coriolis configuration input"
    assert "malicious" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None


def test_sensitive_render_rejects_wrong_record_types_and_malformed_records() -> None:
    with pytest.raises(
        ValueError, match="^invalid sensitive Coriolis configuration input$"
    ):
        render_sensitive_coriolis_config(
            endpoints=object(),
            credentials=SENSITIVE_CREDENTIALS,  # type: ignore[arg-type]
        )
    with pytest.raises(
        ValueError, match="^invalid sensitive Coriolis configuration input$"
    ):
        render_sensitive_coriolis_config(
            endpoints=SENSITIVE_ENDPOINTS,
            credentials=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        SensitiveCoriolisEndpoints()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        SensitiveCoriolisEndpoints(
            "rabbitmq",
            5671,
            "memcached",
            "database",
            "https",
            "keystone",
            5000,
            35357,
            "extra",
        )
    with pytest.raises(TypeError):
        SensitiveCoriolisCredentials("one", "two", "three")
    with pytest.raises(TypeError):
        SensitiveCoriolisCredentials("one", "two", "three", "four", "five")


def test_sensitive_render_failure_is_fixed_and_does_not_leak_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenEnvironment:
        def __init__(self, **_: object) -> None:
            pass

        def get_template(self, _: str) -> object:
            raise RuntimeError("RABBIT_SENTINEL_41e9")

    monkeypatch.setattr(configuration, "Environment", BrokenEnvironment)

    with pytest.raises(ValueError) as excinfo:
        render_sensitive_coriolis_config(
            endpoints=SENSITIVE_ENDPOINTS, credentials=SENSITIVE_CREDENTIALS
        )

    assert str(excinfo.value) == "sensitive Coriolis configuration rendering failed"
    assert excinfo.value.__cause__ is None
    assert "RABBIT_SENTINEL_41e9" not in str(excinfo.value)


def test_sensitive_render_does_not_mutate_input_records() -> None:
    endpoint_hash = hash(SENSITIVE_ENDPOINTS)
    credential_hash = hash(SENSITIVE_CREDENTIALS)

    render_sensitive_coriolis_config(
        endpoints=SENSITIVE_ENDPOINTS, credentials=SENSITIVE_CREDENTIALS
    )

    assert hash(SENSITIVE_ENDPOINTS) == endpoint_hash
    assert hash(SENSITIVE_CREDENTIALS) == credential_hash
