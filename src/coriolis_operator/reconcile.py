"""Pure values and Kubernetes resource bodies used by the controller."""

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

STATE_CONFIG_MAP_SUFFIX = "-operator-state"
CONFIG_MAP_NAME_MAX_LENGTH = 253
DNS_LABEL_MAX_LENGTH = 63
NAME_HASH_LENGTH = 12


def state_config_map_name(resource_name: str) -> str:
    """Return the deterministic state ConfigMap name for an appliance."""
    final_label = resource_name.rsplit(".", 1)[-1]
    if (
        len(resource_name) + len(STATE_CONFIG_MAP_SUFFIX) <= CONFIG_MAP_NAME_MAX_LENGTH
        and len(final_label) + len(STATE_CONFIG_MAP_SUFFIX) <= DNS_LABEL_MAX_LENGTH
    ):
        return f"{resource_name}{STATE_CONFIG_MAP_SUFFIX}"

    name_hash = hashlib.sha256(resource_name.encode()).hexdigest()[:NAME_HASH_LENGTH]
    suffix_label = f"{name_hash}{STATE_CONFIG_MAP_SUFFIX}"
    prefix_length = CONFIG_MAP_NAME_MAX_LENGTH - len(suffix_label) - 1
    prefix = resource_name[:prefix_length].rstrip(".-")
    return f"{prefix}.{suffix_label}"


def build_state_config_map(
    *,
    name: str,
    namespace: str,
    version: str,
    generation: int,
    owner: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the complete server-side apply body for the owned ConfigMap."""
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": state_config_map_name(name),
            "namespace": namespace,
            "ownerReferences": [
                {
                    "apiVersion": str(owner["apiVersion"]),
                    "kind": str(owner["kind"]),
                    "name": str(owner["name"]),
                    "uid": str(owner["uid"]),
                    "controller": True,
                }
            ],
        },
        "data": {
            "requestedVersion": version,
            "generation": str(generation),
        },
    }


def _transition_time(
    condition_type: str,
    condition_status: str,
    prior_conditions: object,
    timestamp: str,
) -> str:
    if not isinstance(prior_conditions, list):
        return timestamp

    for condition in prior_conditions:
        if not isinstance(condition, Mapping):
            continue
        previous_time = condition.get("lastTransitionTime")
        if (
            condition.get("type") == condition_type
            and condition.get("status") == condition_status
            and isinstance(previous_time, str)
            and _is_rfc3339(previous_time)
        ):
            return previous_time
    return timestamp


def _is_rfc3339(value: str) -> bool:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def build_status(
    generation: int,
    prior_conditions: object = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build status reflecting accepted desired state but no runtime yet."""
    now = timestamp or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    timestamp_value = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    conditions = (
        (
            "Accepted",
            "True",
            "Accepted",
            "The requested appliance configuration is valid.",
        ),
        (
            "Reconciled",
            "True",
            "Reconciled",
            "The requested appliance state was applied to Kubernetes.",
        ),
        (
            "Ready",
            "False",
            "RuntimeNotImplemented",
            "The appliance runtime is not implemented yet.",
        ),
    )
    return {
        "observedGeneration": generation,
        "conditions": [
            {
                "type": condition_type,
                "status": condition_status,
                "reason": reason,
                "message": message,
                "observedGeneration": generation,
                "lastTransitionTime": _transition_time(
                    condition_type,
                    condition_status,
                    prior_conditions,
                    timestamp_value,
                ),
            }
            for condition_type, condition_status, reason, message in conditions
        ],
    }
