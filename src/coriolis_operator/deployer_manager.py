"""Fixed Coriolis deployer-manager runtime constants."""

DEPLOYER_MANAGER_COMPONENT = "coriolis-deployer-manager"
DEPLOYER_MANAGER_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-deployer-manager:2603.4"
    "@sha256:a2a7091daf8e172b96fa0b48d19ffad285d7bfaad42fc7e8cd44a688f06f36aa"
)
DEPLOYER_MANAGER_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
DEPLOYER_MANAGER_REPLICAS = 1
DEPLOYER_MANAGER_RUN_AS_ID = 42434
DEPLOYER_MANAGER_TERMINATION_GRACE_PERIOD_SECONDS = 15
DEPLOYER_MANAGER_COMMAND = "/usr/local/bin/coriolis-deployer-manager"
DEPLOYER_MANAGER_ARGS = ("--config-file=/etc/coriolis/coriolis.conf",)
DEPLOYER_MANAGER_CONFIG_DIR = "/etc/coriolis"
DEPLOYER_MANAGER_LOG_DIR = "/var/log/coriolis"
