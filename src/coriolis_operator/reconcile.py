"""Pure values and Kubernetes resource bodies used by the controller."""

from collections.abc import Mapping
from typing import Any

STATE_CONFIG_MAP_SUFFIX = "-operator-state"
CONFIG_MAP_NAME_MAX_LENGTH = 253


def state_config_map_name(resource_name: str) -> str:
    """Return the deterministic state ConfigMap name for an appliance."""
    prefix_length = CONFIG_MAP_NAME_MAX_LENGTH - len(STATE_CONFIG_MAP_SUFFIX)
    return f"{resource_name[:prefix_length]}{STATE_CONFIG_MAP_SUFFIX}"


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


def build_status(generation: int) -> dict[str, Any]:
    """Build status reflecting accepted desired state but no runtime yet."""
    return {
        "observedGeneration": generation,
        "conditions": [
            {
                "type": "Accepted",
                "status": "True",
                "reason": "Accepted",
                "observedGeneration": generation,
            },
            {
                "type": "Reconciled",
                "status": "True",
                "reason": "Reconciled",
                "observedGeneration": generation,
            },
            {
                "type": "Ready",
                "status": "False",
                "reason": "RuntimeNotImplemented",
                "observedGeneration": generation,
            },
        ],
    }
