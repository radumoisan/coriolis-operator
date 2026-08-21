"""Render non-sensitive Coriolis configuration assets."""

from pathlib import PurePosixPath

from jinja2 import Environment, PackageLoader, StrictUndefined

CONFIG_TEMPLATE_MAP = {
    "coriolis-api.wsgi": "coriolis-api.wsgi.j2",
    "wsgi-coriolis.conf": "wsgi-coriolis.conf.j2",
    "vixdisklib.conf": "vixdisklib.conf.j2",
    "api-paste.ini": "api-paste.ini.j2",
    "policy.yml": "policy.yml.j2",
    "coriolis.release": "coriolis.release.j2",
}

_INVALID_INPUT_MESSAGE = "invalid Coriolis configuration input"
_RENDER_FAILURE_MESSAGE = "Coriolis configuration rendering failed"


def _validate_input(value: object, *, path: bool = False) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(_INVALID_INPUT_MESSAGE)
    if "\r" in value or "\n" in value or "\0" in value:
        raise ValueError(_INVALID_INPUT_MESSAGE)
    if path and not PurePosixPath(value).is_absolute():
        raise ValueError(_INVALID_INPUT_MESSAGE)
    return value


def render_coriolis_config(
    *,
    bind_address: str,
    coriolis_port: int,
    coriolis_config_dir: str,
    coriolis_vmware_vix_disklib_log_dir: str,
    accepted_version: str,
) -> dict[str, str]:
    """Return the approved non-sensitive Coriolis ConfigMap values."""
    validated_bind_address = _validate_input(bind_address)
    validated_config_dir = _validate_input(coriolis_config_dir, path=True)
    validated_disklib_log_dir = _validate_input(
        coriolis_vmware_vix_disklib_log_dir, path=True
    )
    validated_version = _validate_input(accepted_version)
    if type(coriolis_port) is not int or not 1 <= coriolis_port <= 65535:
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
            "coriolis_port": coriolis_port,
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
