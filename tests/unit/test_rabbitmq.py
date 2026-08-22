import copy
import subprocess
from pathlib import Path

import pytest

from coriolis_operator.rabbitmq import (
    RABBITMQ_CONFIG_KEYS,
    RABBITMQ_IMAGE,
    RabbitMQSettings,
    render_rabbitmq_config,
    resolve_rabbitmq_settings,
)


def _settings_values() -> tuple[dict[str, object], dict[str, object]]:
    return (
        {"rabbitmq": {"storageClassName": "standard", "size": "1Gi"}},
        {
            "rabbitmq": {
                "requests": {"cpu": "500m", "memory": "512Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            }
        },
    )


def test_resolve_rabbitmq_settings_is_immutable_and_complete() -> None:
    storage, resources = _settings_values()
    before = copy.deepcopy((storage, resources))
    settings = resolve_rabbitmq_settings(storage=storage, resources=resources)

    assert isinstance(settings, RabbitMQSettings)
    assert settings.storage.storage_class_name == "standard"
    assert settings.storage.size == "1Gi"
    assert settings.resources.requests_cpu == "500m"
    assert settings.resources.limits_memory == "1Gi"
    assert (storage, resources) == before


@pytest.mark.parametrize(
    ("storage", "resources"),
    [
        ({}, _settings_values()[1]),
        ({"rabbitmq": {"storageClassName": "standard"}}, _settings_values()[1]),
        (_settings_values()[0], {"rabbitmq": {"requests": {}}}),
        (
            {"rabbitmq": {"storageClassName": " bad", "size": "1Gi"}},
            _settings_values()[1],
        ),
        (
            {"rabbitmq": {"storageClassName": "standard", "size": "0"}},
            _settings_values()[1],
        ),
        (
            _settings_values()[0],
            {
                "rabbitmq": {
                    "requests": {"cpu": "2", "memory": "512Mi"},
                    "limits": {"cpu": "1", "memory": "1Gi"},
                }
            },
        ),
    ],
)
def test_resolve_rabbitmq_settings_rejects_invalid_values_without_leaks(
    storage: object, resources: object
) -> None:
    with pytest.raises(ValueError, match="^invalid RabbitMQ settings$"):
        resolve_rabbitmq_settings(storage=storage, resources=resources)


def test_rabbitmq_config_and_password_stream_contract_are_fixed_and_redacted(
    tmp_path: Path,
) -> None:
    values = render_rabbitmq_config()
    config = values["rabbitmq.conf"]
    script = values["start-rabbitmq.sh"]

    assert set(values) == RABBITMQ_CONFIG_KEYS
    assert RABBITMQ_IMAGE.endswith(
        "@sha256:a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a"
    )
    assert config == (
        "listeners.tcp.1 = 0.0.0.0:5672\n"
        "loopback_users.guest = true\n"
        "load_definitions = /run/rabbitmq/definitions.json\n"
        "log.console = true\n"
        "log.file = false\n"
    )
    assert script.startswith("#!/bin/sh\nset -eu\n")
    assert script.index("umask 077") < script.index("dd if=/dev/urandom")
    assert 'dd if=/dev/urandom of="$salt_file" bs=4 count=1 2>/dev/null' in script
    assert (
        '{ cat "$salt_file"; { cat "$salt_file"; '
        "cat /etc/rabbitmq-secret/rabbitmq_password; } | "
        "openssl dgst -sha256 -binary; } | base64 -w0"
    ) in script
    assert '"tags":[]' in script
    assert (
        script.index('"users"')
        < script.index('"vhosts"')
        < script.index('"permissions"')
    )
    assert "chmod 0600 /run/rabbitmq/definitions.json" in script
    assert 'rm "$salt_file"' in script
    assert script.endswith("exec /usr/sbin/rabbitmq-server\n")
    assert "count=48" not in script
    assert "password=" not in script and "hash=" not in script
    assert "SENTINEL_PASSWORD" not in repr(values)
    script_path = tmp_path / "start-rabbitmq.sh"
    script_path.write_text(script)
    syntax = subprocess.run(
        ["/bin/sh", "-n", str(script_path)], check=False, capture_output=True, text=True
    )
    assert syntax.returncode == 0, syntax.stderr
