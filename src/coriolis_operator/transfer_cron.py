"""Fixed Coriolis transfer-cron runtime constants."""

TRANSFER_CRON_COMPONENT = "coriolis-transfer-cron"
TRANSFER_CRON_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-transfer-cron:2603.4"
    "@sha256:3a44d3b40ba92dff9217b8e7d6a7ca3e7a202efa2641c771ce9b2a3552b3ea9c"
)
TRANSFER_CRON_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
TRANSFER_CRON_REPLICAS = 1
TRANSFER_CRON_RUN_AS_ID = 42434
TRANSFER_CRON_TERMINATION_GRACE_PERIOD_SECONDS = 15
TRANSFER_CRON_COMMAND = "/usr/local/bin/coriolis-transfer-cron"
TRANSFER_CRON_ARGS = ("--config-file=/etc/coriolis/coriolis.conf",)
TRANSFER_CRON_CONFIG_DIR = "/etc/coriolis"
TRANSFER_CRON_LOG_DIR = "/var/log/coriolis"
TRANSFER_CRON_CONFIG_MAP_KEYS = (
    "coriolis-api.wsgi",
    "wsgi-coriolis.conf",
    "vixdisklib.conf",
    "api-paste.ini",
    "policy.yml",
    "coriolis.release",
)
