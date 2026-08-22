import base64

import pytest

from coriolis_operator.keystone import (
    KEYSTONE_CONFIG_KEYS,
    KEYSTONE_KEY_KEYS,
    KEYSTONE_SECRET_CONFIG_KEYS,
    SensitiveKeystoneCredentials,
    generate_keystone_keys,
    render_keystone_config,
    render_sensitive_keystone_config,
)


def test_keystone_keys_are_independent_exact_urlsafe_values() -> None:
    calls: list[int] = []

    def factory(size: int) -> bytes:
        calls.append(size)
        return bytes([len(calls)]) * size

    values = generate_keystone_keys(factory)

    assert set(values) == KEYSTONE_KEY_KEYS
    assert calls == [32, 32]
    assert values["0"] != values["1"]
    assert all(len(value) == 44 and "\n" not in value for value in values.values())
    assert all(base64.urlsafe_b64decode(value) for value in values.values())


@pytest.mark.parametrize("value", [b"", b"x" * 31, "not-bytes"])
def test_keystone_keys_reject_invalid_factory_values(value: object) -> None:
    with pytest.raises(ValueError, match="^Keystone key generation failed$"):
        generate_keystone_keys(lambda _: value)  # type: ignore[return-value]


def test_keystone_renderers_partition_and_redact_sensitive_values() -> None:
    credentials = SensitiveKeystoneCredentials(
        database_password="db:/?#[]@", admin_password="ADMIN_SENTINEL"
    )
    config = render_keystone_config(keystone_host="example-keystone")
    secret = render_sensitive_keystone_config(
        database_host="mariadb", keystone_host="keystone", credentials=credentials
    )

    assert set(config) == KEYSTONE_CONFIG_KEYS
    assert set(secret) == KEYSTONE_SECRET_CONFIG_KEYS
    assert "keystone.server.configure" not in config["bootstrap.py"]
    assert "server.configure(config_files=[config])" in config["bootstrap.py"]
    assert "db%3A%2F%3F%23%5B%5D%40" in secret["keystone.conf"]
    assert "ADMIN_SENTINEL" not in repr(credentials)
    assert "ADMIN_SENTINEL" not in repr(secret)
    assert "ADMIN_SENTINEL" not in "".join(config.values())
    assert "http://example-keystone:5000/v3" in config["bootstrap.py"]


def test_keystone_sensitive_renderer_rejects_control_characters_without_leaks() -> None:
    sentinel = "secret\nvalue"
    with pytest.raises(ValueError) as error:
        render_sensitive_keystone_config(
            database_host="mariadb",
            keystone_host="keystone",
            credentials=SensitiveKeystoneCredentials(
                database_password=sentinel, admin_password="admin"
            ),
        )
    assert str(error.value) == "invalid sensitive Keystone configuration input"
    assert sentinel not in str(error.value)
