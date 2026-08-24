"""Fixed Coriolis scheduler runtime constants."""

SCHEDULER_COMPONENT = "coriolis-scheduler"
SCHEDULER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-scheduler:2603.4"
    "@sha256:45bea9e0bab4cac0fdddee6d3eac52006d12cf7de1e798e2949dd9ebc2a73c41"
)
SCHEDULER_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
SCHEDULER_REPLICAS = 1
SCHEDULER_RUN_AS_ID = 42434
SCHEDULER_TERMINATION_GRACE_PERIOD_SECONDS = 15
SCHEDULER_COMMAND = "/usr/local/bin/coriolis-scheduler"
SCHEDULER_ARGS = ("--config-file=/etc/coriolis/coriolis.conf",)
SCHEDULER_CONFIG_DIR = "/etc/coriolis"
SCHEDULER_LOG_DIR = "/var/log/coriolis"
SCHEDULER_CONFIG_MAP_KEYS = (
    "coriolis-api.wsgi",
    "wsgi-coriolis.conf",
    "vixdisklib.conf",
    "api-paste.ini",
    "policy.yml",
    "coriolis.release",
)
