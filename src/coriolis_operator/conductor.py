"""Fixed Coriolis conductor runtime constants."""

CONDUCTOR_COMPONENT = "coriolis-conductor"
CONDUCTOR_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
CONDUCTOR_REPLICAS = 1
CONDUCTOR_RUN_AS_ID = 42434
CONDUCTOR_TERMINATION_GRACE_PERIOD_SECONDS = 45
CONDUCTOR_COMMAND = "/usr/local/bin/coriolis-conductor"
CONDUCTOR_ARGS = ("--config-file=/etc/coriolis/coriolis.conf",)
CONDUCTOR_CONFIG_DIR = "/etc/coriolis"
CONDUCTOR_LOG_DIR = "/var/log/coriolis"
CONDUCTOR_LOCKS_DIR = "/opt/coriolis/locks"
CONDUCTOR_CONFIG_MAP_KEYS = (
    "coriolis-api.wsgi",
    "wsgi-coriolis.conf",
    "vixdisklib.conf",
    "api-paste.ini",
    "policy.yml",
    "coriolis.release",
)
