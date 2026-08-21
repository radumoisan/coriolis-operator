from importlib.resources import files

import pytest

from coriolis_operator import configuration
from coriolis_operator.configuration import render_coriolis_config
from coriolis_operator.reconcile import CORIOLIS_CONFIG_KEYS, build_coriolis_config_map

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
