"""Fixed Coriolis API runtime constants."""

API_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/coriolis-api:2603.4"
    "@sha256:fce6369f07ef777b5174d3a4f849d4eac914256a20a47ffa0cd1c98081be2705"
)
API_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
API_REPLICAS = 1
API_RUN_AS_ID = 42434
API_TERMINATION_GRACE_PERIOD_SECONDS = 15
API_PORT = 7667
API_COMMAND = "/usr/local/bin/coriolis-api"
API_ARGS = (
    "--worker-process-count",
    "1",
    "--config-file=/etc/coriolis/coriolis.conf",
)
API_CONFIG_DIR = "/etc/coriolis"
API_LOG_DIR = "/var/log/coriolis"
API_LOCKS_DIR = "/opt/coriolis/locks"
API_CONFIG_MAP_KEYS = (
    "coriolis-api.wsgi",
    "wsgi-coriolis.conf",
    "vixdisklib.conf",
    "api-paste.ini",
    "policy.yml",
    "coriolis.release",
)
API_PROTOCOL_PROBE = (
    "import http.client,sys; "
    "connection=http.client.HTTPConnection('127.0.0.1',7667,timeout=5); "
    "connection.request('GET','/v1'); "
    "response=connection.getresponse(); "
    "sys.exit(0 if response.status == 401 else 1)"
)
