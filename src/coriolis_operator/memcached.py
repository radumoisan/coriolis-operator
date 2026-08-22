"""Fixed Memcached runtime constants."""

MEMCACHED_IMAGE = (
    "cr.virtomat.io/virtomat/coriolis/memcached:2023.1-ubuntu-jammy"
    "@sha256:746b93082a4f6d07f464e93d4b14f5e30510abf17a9ae0a4af20e111408c8f1e"
)
MEMCACHED_IMAGE_PULL_SECRET_NAME = "coriolis-appliance-registry"
MEMCACHED_REPLICAS = 1
MEMCACHED_RUN_AS_ID = 42457
MEMCACHED_TERMINATION_GRACE_PERIOD_SECONDS = 30
MEMCACHED_PORT = 11211
MEMCACHED_COMMAND = "/usr/bin/memcached"
MEMCACHED_ARGS = ("-p", "11211", "-U", "0")
MEMCACHED_PROTOCOL_PROBE_COMMAND = (
    "exec 3<>/dev/tcp/127.0.0.1/11211; "
    "printf 'version\\r\\n' >&3; "
    "IFS= read -r response <&3; "
    '[[ "$response" == VERSION\\ * ]]'
)
