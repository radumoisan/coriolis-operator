"""Fixed Coriolis worker runtime constants."""

WORKER_COMPONENT = "coriolis-worker"
WORKER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-worker:2603.4"
    "@sha256:ff30999d6e43709411f197b1b6b80dbce1d7e5498a27f869df93a061626ab2c9"
)
WORKER_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
WORKER_REPLICAS = 1
WORKER_TERMINATION_GRACE_PERIOD_SECONDS = 30
WORKER_COMMAND = "/usr/local/bin/coriolis-worker"
WORKER_ARGS = (
    "--worker-process-count",
    "1",
    "--config-file=/etc/coriolis/coriolis.conf",
)
WORKER_CONFIG_DIR = "/etc/coriolis"
WORKER_LOG_DIR = "/var/log/coriolis"
WORKER_EXPORT_DIR = "/opt/coriolis/export"
