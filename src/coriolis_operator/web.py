"""Fixed Coriolis web runtime constants."""

WEB_BIND_ADDRESS = "0.0.0.0"
WEB_COMPONENT = "coriolis-web"
WEB_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-web:2603.4"
    "@sha256:32ebc391ac46fe627185694b3fd252afd7587b152f526dff38ae0a5b887c0db1"
)
WEB_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
WEB_PORT = 3000
WEB_PROBE_PATH = "/api/config"
WEB_REPLICAS = 1
WEB_RUN_AS_ID = 0
WEB_TERMINATION_GRACE_PERIOD_SECONDS = 15
