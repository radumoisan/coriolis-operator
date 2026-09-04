import base64
import hashlib
import inspect
import secrets

import pytest

from coriolis_operator import barbican
from coriolis_operator.barbican import (
    BARBICAN_API_COMMAND,
    BARBICAN_API_IMAGE,
    BARBICAN_API_STATE_DIR,
    BARBICAN_CONFIG_KEYS,
    BARBICAN_CONFIG_PATH,
    BARBICAN_DB_SYNC_COMMAND,
    BARBICAN_DB_SYNC_PATH,
    BARBICAN_HEALTH_PROBE,
    BARBICAN_HEALTHCHECK_DISABLE_PATH,
    BARBICAN_IMAGE_PULL_SECRET_NAME,
    BARBICAN_PASTE_PATH,
    BARBICAN_POLICY_PATH,
    BARBICAN_PORT,
    BARBICAN_REPLICAS,
    BARBICAN_RUN_AS_ID,
    BARBICAN_RUNTIME_DIR,
    BARBICAN_SECRET_CONFIG_KEYS,
    BARBICAN_SUPPLEMENTAL_GROUP,
    BARBICAN_TERMINATION_GRACE_PERIOD_SECONDS,
    BARBICAN_TMP_DIR,
    BARBICAN_VASSAL_PATH,
    BARBICAN_VASSALS_DIR,
    BARBICAN_VENV_PYTHON,
    BARBICAN_WORKER_COMMAND,
    BARBICAN_WORKER_IMAGE,
    SensitiveBarbicanConfig,
    SensitiveBarbicanCredentials,
    generate_barbican_crypto_key,
    render_barbican_config,
    render_sensitive_barbican_config,
    validate_retained_barbican_values,
)

_DB_PASSWORD = "p@ss/w?d#1"
_DB_PASSWORD_QUOTED = "p%40ss%2Fw%3Fd%231"
_KEYSTONE_PASSWORD = "k#y=1"
_RABBITMQ_PASSWORD = "r@b:it/"
_RABBITMQ_PASSWORD_QUOTED = "r%40b%3Ait%2F"
_SENTINELS = (_DB_PASSWORD, _KEYSTONE_PASSWORD, _RABBITMQ_PASSWORD)

_VALID_CRYPTO_KEY = base64.urlsafe_b64encode(b"\x00" * 32).decode("ascii")
# Public upstream default, constructed locally and never stored in module source.
_BUILTIN_DEFAULT_RAW = b"thirty_two_byte_keyblahblahblahh"
_BUILTIN_DEFAULT_KEK = base64.urlsafe_b64encode(_BUILTIN_DEFAULT_RAW).decode("ascii")

_INVALID_MESSAGE = "^invalid sensitive Barbican configuration input$"
_GENERATION_FAILURE = "^Barbican crypto key generation failed$"


def _credentials(
    *,
    database_password: str = _DB_PASSWORD,
    keystone_password: str = _KEYSTONE_PASSWORD,
    rabbitmq_password: str = _RABBITMQ_PASSWORD,
    crypto_key: str = _VALID_CRYPTO_KEY,
) -> SensitiveBarbicanCredentials:
    return SensitiveBarbicanCredentials(
        database_password=database_password,
        keystone_password=keystone_password,
        rabbitmq_password=rabbitmq_password,
        crypto_key=crypto_key,
    )


def _render(
    *,
    database_host: object = "mariadb",
    rabbitmq_host: object = "rabbitmq",
    keystone_host: object = "keystone",
    barbican_host: object = "barbican",
    credentials: object | None = None,
) -> SensitiveBarbicanConfig:
    return render_sensitive_barbican_config(
        database_host=database_host,
        rabbitmq_host=rabbitmq_host,
        keystone_host=keystone_host,
        barbican_host=barbican_host,
        credentials=(credentials if credentials is not None else _credentials()),
    )


def test_identity_constants_are_exact() -> None:
    assert BARBICAN_API_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/barbican-api:2023.1-ubuntu-jammy"
        "@sha256:a142a57761f708b241358383d6445ac5da4e05ae26a284369081cfb15cca8a60"
    )
    assert BARBICAN_WORKER_IMAGE == (
        "cr.virtomat.io/virtomat/coriolis/barbican-worker:2023.1-ubuntu-jammy"
        "@sha256:ed907de778900b08f2645c9eeb82d48d8202ce6517cdb543d42db2e88ea642b5"
    )
    assert BARBICAN_IMAGE_PULL_SECRET_NAME == "coriolis-appliance-registry"
    assert BARBICAN_PORT == 9311
    assert BARBICAN_REPLICAS == 1
    assert BARBICAN_RUN_AS_ID == 42403
    assert BARBICAN_SUPPLEMENTAL_GROUP == 42400
    assert BARBICAN_TERMINATION_GRACE_PERIOD_SECONDS == 30
    assert BARBICAN_RUNTIME_DIR == "/etc/barbican-runtime"
    assert BARBICAN_TMP_DIR == "/tmp"
    assert BARBICAN_API_STATE_DIR == "/var/lib/barbican"
    assert BARBICAN_VASSALS_DIR == "/etc/barbican-runtime/vassals"
    assert BARBICAN_PASTE_PATH == "/etc/barbican-runtime/barbican-api-paste.ini"
    assert BARBICAN_VASSAL_PATH == "/etc/barbican-runtime/vassals/barbican-api.ini"
    assert BARBICAN_POLICY_PATH == "/etc/barbican-runtime/policy.yaml"
    assert BARBICAN_DB_SYNC_PATH == "/etc/barbican-runtime/db-sync.py"
    assert BARBICAN_CONFIG_PATH == "/etc/barbican-runtime/barbican.conf"
    assert BARBICAN_HEALTHCHECK_DISABLE_PATH == (
        "/var/lib/barbican/healthcheck_disable"
    )


def test_commands_are_exact_with_dumb_init_wrappers() -> None:
    assert BARBICAN_API_COMMAND == (
        "/usr/bin/dumb-init",
        "--single-child",
        "--",
        "/var/lib/kolla/venv/bin/uwsgi",
        "--master",
        "--emperor",
        BARBICAN_VASSALS_DIR,
    )
    assert BARBICAN_WORKER_COMMAND == (
        "/usr/bin/dumb-init",
        "--single-child",
        "--",
        "/var/lib/kolla/venv/bin/barbican-worker",
        "--config-file",
        BARBICAN_CONFIG_PATH,
        "--nouse-syslog",
        "--log-dir=",
    )
    assert BARBICAN_WORKER_COMMAND[:3] == BARBICAN_API_COMMAND[:3]
    assert BARBICAN_VENV_PYTHON == "/var/lib/kolla/venv/bin/python3"
    assert BARBICAN_DB_SYNC_COMMAND == (
        BARBICAN_VENV_PYTHON,
        BARBICAN_DB_SYNC_PATH,
    )


def test_health_probe_targets_local_healthcheck() -> None:
    assert "http.client.HTTPConnection('127.0.0.1',9311,timeout=5)" in (
        BARBICAN_HEALTH_PROBE
    )
    assert "connection.request('GET','/healthcheck')" in BARBICAN_HEALTH_PROBE
    assert "sys.exit(0 if response.status == 200 else 1)" in BARBICAN_HEALTH_PROBE


def test_config_key_partition_is_exact_and_disjoint() -> None:
    assert BARBICAN_CONFIG_KEYS == frozenset(
        {
            "barbican-api-paste.ini",
            "barbican-api.ini",
            "policy.yaml",
            "db-sync.py",
        }
    )
    assert BARBICAN_SECRET_CONFIG_KEYS == frozenset({"barbican.conf"})
    assert not BARBICAN_CONFIG_KEYS & BARBICAN_SECRET_CONFIG_KEYS
    assert set(render_barbican_config()) == BARBICAN_CONFIG_KEYS


def test_render_barbican_config_is_deterministic() -> None:
    assert render_barbican_config() == render_barbican_config()


def test_paste_uses_exact_urlmap_colon_syntax() -> None:
    paste = render_barbican_config()["barbican-api-paste.ini"]
    assert "use = egg:Paste#urlmap\n" in paste
    assert "/: barbican_version\n" in paste
    assert "/healthcheck: healthcheck\n" in paste
    assert "/v1: barbican-api-keystone\n" in paste
    assert "/ = barbican_version" not in paste
    assert "/healthcheck = healthcheck" not in paste
    assert "/v1 = barbican-api-keystone" not in paste
    assert (
        "paste.filter_factory = "
        "barbican.api.middleware.microversion:MicroversionMiddleware.factory"
    ) in paste
    assert f"disable_by_file_path = {BARBICAN_HEALTHCHECK_DISABLE_PATH}" in paste


def test_vassal_uses_image_supported_uwsgi_form() -> None:
    vassal = render_barbican_config()["barbican-api.ini"]
    assert vassal.startswith("[uwsgi]\n")
    assert "socket = :9311\n" in vassal
    assert "protocol = http\n" in vassal
    assert "plugins = python\n" in vassal
    assert "http-socket" not in vassal
    assert "python3" not in vassal
    assert f"processes = {BARBICAN_REPLICAS}\n" in vassal
    assert f"paste = config:{BARBICAN_PASTE_PATH}\n" in vassal
    assert f"pyargv = --config-file={BARBICAN_CONFIG_PATH}\n" in vassal
    assert "add-header = Connection: close\n" in vassal


def test_db_sync_compiles_and_uses_local_config_with_explicit_sql_url() -> None:
    source = render_barbican_config()["db-sync.py"]
    compile(source, "db-sync.py", "exec")
    assert "conf = config.new_config()" in source
    assert "config.parse_args(\n    conf,\n    args=[],\n" in source
    assert f"default_config_files=['{BARBICAN_CONFIG_PATH}']" in source
    assert "commands.upgrade(to_version='head', sql_url=conf.sql_connection)" in source
    assert "config.CONF" not in source
    assert "mysql" not in source


def test_policy_asset_is_exact() -> None:
    policy = render_barbican_config()["policy.yaml"]
    assert '"creator": "role:admin or role:member"\n' in policy
    assert '"observer": "role:admin or role:reader"\n' in policy


def test_configmap_assets_never_carry_sentinels() -> None:
    for content in render_barbican_config().values():
        for sentinel in (*_SENTINELS, _VALID_CRYPTO_KEY, _BUILTIN_DEFAULT_KEK):
            assert sentinel not in content


def test_sensitive_render_partitions_urls_sections_and_quotes() -> None:
    config = _render()
    assert set(config) == BARBICAN_SECRET_CONFIG_KEYS
    assert config == _render()
    text = config["barbican.conf"]
    assert (
        f"sql_connection = mysql+pymysql://barbican:{_DB_PASSWORD_QUOTED}"
        "@mariadb:3306/barbican\n" in text
    )
    assert (
        f"transport_url = rabbit://openstack:{_RABBITMQ_PASSWORD_QUOTED}"
        "@rabbitmq:5672/\n" in text
    )
    assert "host_href = http://barbican:9311\n" in text
    assert "www_authenticate_uri = http://keystone:5000/v3\n" in text
    assert "auth_url = http://keystone:5000/v3\n" in text
    assert f"password = {_KEYSTONE_PASSWORD}\n" in text
    assert f"kek = {_VALID_CRYPTO_KEY}\n" in text
    assert f"policy_file = {BARBICAN_POLICY_PATH}\n" in text
    assert "use_stderr = true\n" in text
    assert "use_syslog = false\n" in text
    assert "db_auto_create = False\n" in text
    assert "enabled_secretstore_plugins = store_crypto\n" in text
    assert "enabled_crypto_plugins = simple_crypto\n" in text
    assert "driver = noop\n" in text
    assert "memcache" not in text.lower()
    assert [line for line in text.splitlines() if line.startswith("[")] == [
        "[DEFAULT]",
        "[secretstore]",
        "[crypto]",
        "[simple_crypto_plugin]",
        "[keystone_notifications]",
        "[keystone_authtoken]",
        "[oslo_messaging_notifications]",
        "[oslo_messaging_rabbit]",
        "[oslo_middleware]",
        "[oslo_policy]",
    ]


def test_sensitive_render_accepts_boundary_dns_label_hosts() -> None:
    text = _render(
        database_host="m",
        rabbitmq_host="a1-b2",
        keystone_host="k" * 63,
        barbican_host="barbican-api-0",
    )["barbican.conf"]
    assert "sql_connection = mysql+pymysql://barbican:" in text
    assert "@m:3306/barbican" in text
    assert "@a1-b2:5672/" in text
    assert f"http://{'k' * 63}:5000/v3" in text
    assert "host_href = http://barbican-api-0:9311" in text


def test_sensitive_config_and_credentials_representations_are_redacted() -> None:
    config = _render()
    assert repr(config) == "SensitiveBarbicanConfig(<redacted>)"
    assert str(config) == "SensitiveBarbicanConfig(<redacted>)"
    assert dict(config)
    assert len(config) == 1
    assert list(config) == ["barbican.conf"]
    credentials = _credentials()
    assert repr(credentials) == "SensitiveBarbicanCredentials()"
    for sentinel in _SENTINELS:
        assert sentinel not in repr(config)
        assert sentinel not in repr(credentials)


def test_generate_crypto_key_is_exact_and_canonical() -> None:
    key = generate_barbican_crypto_key(lambda size: b"A" * size)
    assert key == base64.urlsafe_b64encode(b"A" * 32).decode("ascii")
    assert len(key) == 44
    raw = base64.urlsafe_b64decode(key)
    assert len(raw) == 32
    assert base64.urlsafe_b64encode(raw).decode("ascii") == key


def test_generated_crypto_key_round_trips_into_sensitive_render() -> None:
    key = generate_barbican_crypto_key()
    text = _render(credentials=_credentials(crypto_key=key))["barbican.conf"]
    assert f"kek = {key}\n" in text


def _failing_factory(size: int) -> bytes:
    raise RuntimeError("leak-me")


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda size: b"short", id="short-bytes"),
        pytest.param(lambda size: b"x" * 33, id="long-bytes"),
        pytest.param(lambda size: "A" * 32, id="not-bytes"),
        pytest.param(lambda size: None, id="none"),
        pytest.param(_failing_factory, id="raises"),
    ],
)
def test_generate_crypto_key_rejects_invalid_factories_without_leaks(
    factory: object,
) -> None:
    with pytest.raises(ValueError, match=_GENERATION_FAILURE) as excinfo:
        generate_barbican_crypto_key(factory)
    assert "leak-me" not in str(excinfo.value)


def test_generate_crypto_key_rejects_builtin_default_output() -> None:
    with pytest.raises(ValueError, match=_GENERATION_FAILURE):
        generate_barbican_crypto_key(lambda size: _BUILTIN_DEFAULT_RAW)


def test_builtin_default_kek_is_not_stored_in_module_source() -> None:
    source = inspect.getsource(barbican)
    assert _BUILTIN_DEFAULT_KEK not in source
    digest = hashlib.sha256(_BUILTIN_DEFAULT_KEK.encode("utf-8")).hexdigest()
    assert digest == barbican._BUILTIN_DEFAULT_KEK_SHA256


@pytest.mark.parametrize(
    "crypto_key",
    [
        pytest.param("", id="empty"),
        pytest.param("A" * 43, id="short-length"),
        pytest.param("A" * 45, id="long-length"),
        pytest.param("!" * 44, id="not-base64"),
        pytest.param(
            base64.urlsafe_b64encode(b"x" * 31).decode("ascii"),
            id="wrong-raw-length-short",
        ),
        pytest.param(
            base64.urlsafe_b64encode(b"x" * 33).decode("ascii"),
            id="wrong-raw-length-long",
        ),
        pytest.param(
            base64.b64encode(b"\xff" * 32).decode("ascii"),
            id="noncanonical-alphabet",
        ),
        pytest.param(_BUILTIN_DEFAULT_KEK, id="builtin-default"),
    ],
)
def test_sensitive_render_rejects_invalid_crypto_keys_without_leaks(
    crypto_key: str,
) -> None:
    with pytest.raises(ValueError, match=_INVALID_MESSAGE) as excinfo:
        _render(credentials=_credentials(crypto_key=crypto_key))
    if crypto_key:
        assert crypto_key not in str(excinfo.value)


@pytest.mark.parametrize(
    "host",
    [
        pytest.param("", id="empty"),
        pytest.param("MariaDB", id="uppercase"),
        pytest.param("bad host", id="space"),
        pytest.param("-abc", id="leading-hyphen"),
        pytest.param("abc-", id="trailing-hyphen"),
        pytest.param("a" * 64, id="too-long"),
        pytest.param("bad/host", id="slash"),
        pytest.param("bad\\host", id="backslash"),
        pytest.param("bad:host", id="colon"),
        pytest.param("bad%host", id="percent"),
        pytest.param("h\xf6st", id="unicode-letter"),
        pytest.param("bad\nhost", id="control-char"),
        pytest.param("--config-file=x", id="option-injection"),
        pytest.param(5, id="non-string"),
        pytest.param(None, id="none"),
    ],
)
@pytest.mark.parametrize("position", ["database", "rabbitmq", "keystone", "barbican"])
def test_sensitive_render_rejects_invalid_hosts(host: object, position: str) -> None:
    kwargs: dict[str, object] = {
        "database_host": "mariadb",
        "rabbitmq_host": "rabbitmq",
        "keystone_host": "keystone",
        "barbican_host": "barbican",
    }
    kwargs[f"{position}_host"] = host
    with pytest.raises(ValueError, match=_INVALID_MESSAGE):
        _render(**kwargs)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"database_password": ""}, id="empty-database-password"),
        pytest.param({"keystone_password": "a\nb"}, id="control-char-password"),
        pytest.param({"rabbitmq_password": "\x00"}, id="nul-password"),
    ],
)
def test_sensitive_render_rejects_invalid_credential_fields(
    overrides: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match=_INVALID_MESSAGE):
        _render(credentials=_credentials(**overrides))


@pytest.mark.parametrize(
    "credentials",
    [
        pytest.param(None, id="none"),
        pytest.param(
            {
                "database_password": _DB_PASSWORD,
                "keystone_password": _KEYSTONE_PASSWORD,
                "rabbitmq_password": _RABBITMQ_PASSWORD,
                "crypto_key": _VALID_CRYPTO_KEY,
            },
            id="plain-dict",
        ),
    ],
)
def test_sensitive_render_rejects_non_credentials_objects(
    credentials: object,
) -> None:
    with pytest.raises(ValueError, match=_INVALID_MESSAGE) as excinfo:
        render_sensitive_barbican_config(
            database_host="mariadb",
            rabbitmq_host="rabbitmq",
            keystone_host="keystone",
            barbican_host="barbican",
            credentials=credentials,
        )
    for sentinel in _SENTINELS:
        assert sentinel not in str(excinfo.value)


def test_validate_retained_barbican_values_accepts_valid_and_generated() -> None:
    assert (
        validate_retained_barbican_values(
            database_password=_DB_PASSWORD,
            keystone_password=_KEYSTONE_PASSWORD,
            crypto_key=_VALID_CRYPTO_KEY,
        )
        is None
    )
    assert (
        validate_retained_barbican_values(
            database_password=secrets.token_urlsafe(32),
            keystone_password=secrets.token_urlsafe(32),
            crypto_key=generate_barbican_crypto_key(),
        )
        is None
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("database_password", " leading", id="db-leading-space"),
        pytest.param("database_password", "trailing ", id="db-trailing-space"),
        pytest.param("database_password", "#hash", id="db-leading-hash"),
        pytest.param("database_password", ";semicolon", id="db-leading-semicolon"),
        pytest.param("database_password", "", id="db-empty"),
        pytest.param("database_password", "a\nb", id="db-control-char"),
        pytest.param("keystone_password", " leading", id="keystone-leading-space"),
        pytest.param("keystone_password", "trailing ", id="keystone-trailing-space"),
        pytest.param("keystone_password", "#hash", id="keystone-leading-hash"),
        pytest.param(
            "keystone_password", ";semicolon", id="keystone-leading-semicolon"
        ),
        pytest.param("keystone_password", "\x00", id="keystone-nul"),
        pytest.param(
            "crypto_key",
            base64.urlsafe_b64encode(b"k" * 16).decode("ascii"),
            id="kek-16-byte-valid-base64",
        ),
        pytest.param(
            "crypto_key",
            base64.b64encode(b"\xff" * 32).decode("ascii"),
            id="kek-noncanonical-alphabet",
        ),
        pytest.param("crypto_key", _BUILTIN_DEFAULT_KEK, id="kek-builtin-default"),
        pytest.param(
            "crypto_key",
            f" {_VALID_CRYPTO_KEY}",
            id="kek-padded-valid-base64",
        ),
    ],
)
def test_validate_retained_barbican_values_rejects_without_leaks(
    field: str, value: str
) -> None:
    arguments: dict[str, object] = {
        "database_password": _DB_PASSWORD,
        "keystone_password": _KEYSTONE_PASSWORD,
        "crypto_key": _VALID_CRYPTO_KEY,
    }
    arguments[field] = value
    with pytest.raises(ValueError, match=_INVALID_MESSAGE) as excinfo:
        validate_retained_barbican_values(**arguments)
    if value:
        assert value not in str(excinfo.value)
    for sentinel in _SENTINELS:
        assert sentinel not in str(excinfo.value)
