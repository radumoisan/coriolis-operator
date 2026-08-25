"""Fixed Coriolis minion-manager runtime constants."""

MINION_MANAGER_COMPONENT = "coriolis-minion-manager"
MINION_MANAGER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-minion-manager:2603.4"
    "@sha256:1ea016dd967ce249a45cf9937701a45880f3b42f8146a93d1f5eb4f1d84e1fb9"
)
MINION_MANAGER_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
MINION_MANAGER_REPLICAS = 1
MINION_MANAGER_RUN_AS_ID = 42434
MINION_MANAGER_TERMINATION_GRACE_PERIOD_SECONDS = 15
MINION_MANAGER_COMMAND = "/usr/local/bin/coriolis-minion-manager"
MINION_MANAGER_ARGS = ("--config-file=/etc/coriolis/coriolis.conf",)
MINION_MANAGER_CONFIG_DIR = "/etc/coriolis"
MINION_MANAGER_LOG_DIR = "/var/log/coriolis"
MINION_MANAGER_CONFIG_MAP_KEYS = (
    "coriolis-api.wsgi",
    "wsgi-coriolis.conf",
    "vixdisklib.conf",
    "api-paste.ini",
    "policy.yml",
    "coriolis.release",
)
