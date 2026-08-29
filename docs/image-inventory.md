# Image Inventory Ledger

!!! abstract
    Authoritative milestone ledger for the `2603.4` image and runtime inventory gate. RC4 is **BLOCKED / OVA-only for Kubernetes**; the approved fallback is exact official release `2603.4`.

## :material-book-open-page-variant-outline: Scope And Provenance

| Item | Value |
| --- | --- |
| Target | `2603.4` (exact official release) |
| Provenance source | Jenkins job `1_coriolis-appliance-setup`, Build `868` (RC4 attempt); official `2603.4` Confluence release |
| Registry namespace | `registry.cloudbase.it/appliance` |
| Official release page | https://cloudbasedev.atlassian.net/wiki/spaces/COR/pages/3870556161/2603.4 |
| Official OVA | 9,934,848,000 bytes, SHA-256 `e8c7fad6a07bc96aad281876bfc47728df94c40b5f562ec45a16baabce9b93c9` |
| Validation date | 2026-08-20 |
| Gate state | Metadata and pull gate **complete**; all 26 images mirrored to `cr.virtomat.io/virtomat/coriolis`; all 21 initial-runtime image pulls passed in `virt-infra-dev-buc-hq` namespace `coriolis` |

The target runtime contract and acceptance boundary are in [Runtime Contract](runtime-contract.md). The approved environment and Jenkins reference are in [Development Environment](dev-environment.md).

## :material-book-open-page-variant-outline: RC4 Failure And Fallback Decision

- Jenkins job `1_coriolis-appliance-setup` Build `868` succeeded and exported an OVA.
- All 15 `registry.cloudbase.it/appliance/coriolis-*` repositories have **no `2608*` tag**; representative `coriolis-api:latest` predates RC4.
- Therefore RC4 is **BLOCKED / OVA-only for Kubernetes** and must not be used.
- The approved fallback is the exact official release **`2603.4`** — not `2603.41` or `2603.42`.

## :material-book-open-page-variant-outline: Platform, User, And Health Facts

- All images are **Linux/amd64**.
- No application or private support image declares an OCI healthcheck, so **probes are operator-owned**.
- Images default to **root/empty user**.
- Historical listener inventory: API `7667`, web `3000`, web proxy `443`. The web proxy is deferred from the current initial-runtime selection.
- The worker is **privileged** with `/dev` and `/lib/modules`.
- Kolla support images (keystone, barbican, kolla-toolbox, mariadb, rabbitmq, memcached) run as their platform service users by default.

## :material-book-open-page-variant-outline: Historical Pull-Validated Core Selection

Historical pull-validation selection included API, conductor, scheduler, transfer-cron, minion-manager, deployer-manager, privileged worker, compressor, web, web-proxy, and Step CA. The current initial-runtime selection defers web-proxy and Step CA; their mirrored images remain retained as historical inventory evidence.

- `coriolis-common` is a **base image**, not a workload.
- Deferred: licensing server, Metal Hub, console editor, logger/InfluxDB.

## :material-book-open-page-variant-outline: Application Images (`2603.4`)

Exact immutable manifest digests for the `2603.4` application images under `registry.cloudbase.it/appliance`:

| Image | Digest |
| --- | --- |
| coriolis-api | `fce6369f07ef777b5174d3a4f849d4eac914256a20a47ffa0cd1c98081be2705` |
| coriolis-common | `e0baa5094d651992253cc419f40411f2529a1a1236e87eda90809b235aaf235a` |
| coriolis-compressor | `af2cf9d2eb3ca153b56b3eb928045092f904be03a381371ff73efacaf7feb842` |
| coriolis-conductor | `27495f44fbb8b320098d0aa04cd9dcb2a4b432e57aa17417606efc5403ac09c7` |
| coriolis-console-editor | `c944df5b208a2b91d317ee2deb636e6bbc3cf278d181766943b7e1a08e589429` |
| coriolis-deployer-manager | `a2a7091daf8e172b96fa0b48d19ffad285d7bfaad42fc7e8cd44a688f06f36aa` |
| coriolis-licensing-server | `09d8332b1d271824300e9e210c2623251b432bfc46ca6e2500ced8ed2f8d2e6b` |
| coriolis-logger | `aafdad52913518d55a2c44d8e437b96f7cc079a79e4437c2ce0c396ed178cb4f` |
| coriolis-metal-hub | `e51ce9624312ef6a2e3b39dbd62f3d7d1b5059b40a11cfe8ba351330e45fa698` |
| coriolis-minion-manager | `1ea016dd967ce249a45cf9937701a45880f3b42f8146a93d1f5eb4f1d84e1fb9` |
| coriolis-scheduler | `45bea9e0bab4cac0fdddee6d3eac52006d12cf7de1e798e2949dd9ebc2a73c41` |
| coriolis-transfer-cron | `3a44d3b40ba92dff9217b8e7d6a7ca3e7a202efa2641c771ce9b2a3552b3ea9c` |
| coriolis-web | `32ebc391ac46fe627185694b3fd252afd7587b152f526dff38ae0a5b887c0db1` |
| coriolis-web-proxy | `649a4fa9ceb91effdd0f3d782e7ac593d2e099ac93ffe8d1c8b6629eba6be762` |
| coriolis-worker | `ff30999d6e43709411f197b1b6b80dbce1d7e5498a27f869df93a061626ab2c9` |

### :material-application-edit-outline: Legacy Logger Reference And Loki Adaptor Evidence

The immutable `coriolis-logger` source clone is clean at `db67ca3c0d95d738679696970529897612325ee4`, tag `1.0.5`. It confirms Syslog-only ingestion, InfluxDB persistence, legacy API/WebSocket semantics, Keystone query-token fallback, and unsafe full-request access logging. The `2603.4` logger digest above remains historical inventory only: no source-commit-to-image mapping is proven, and [ADR 0007](decisions/0007-kubernetes-native-logging.md) replaces that implementation dependency with an operator-managed per-CR Loki/Alloy stack and a separate compatibility adaptor.

### :material-application-edit-outline: Operator-Managed Logging Stack Images

Milestone 8 now uses an operator-managed per-`CoriolisAppliance` logging stack. The four immutable logging images and their runtime identities are:

| Image | Reference | Runtime identity |
| --- | --- | --- |
| Loki | `cr.virtomat.io/virtomat/coriolis/loki@sha256:550d599ec4efacd8ebc0a5871766855057cba2bd0c669c0711d898c00d6d901f` | 3.7.7, UID 10001 |
| Alloy | `cr.virtomat.io/virtomat/coriolis/alloy@sha256:1eeba15ef3193438c72f66efd3d76f769c523a4c661db0fae6eddde906004bc8` | v1.19.2, manifest UID 10001 |
| NGINX (unprivileged) | `cr.virtomat.io/virtomat/coriolis/nginx-unprivileged@sha256:9849698e95fe2b466e473ad8c452b1a812e08713af1514c61ece0aa77cc8e013` | 1.30.4, UID 101 |
| Logs adaptor | `cr.virtomat.io/virtomat/coriolis/logs-adaptor@sha256:100701724e228c616803d5764b20e0b31e665e305390455c5c2a62b0bb514237` | UID 10001 |

The adaptor image's source-to-image provenance is incomplete because its published OCI metadata lacks a source revision label; that is not the current implementation blocker. These are local-qualification identities: they are frozen for the operator-managed stack and locally validated, but isolated dev-cluster qualification and released-operator-artifact testing remain pending, so no cluster/released-artifact claim is made for them.

The local `coriolis-logs-api-adaptor` gate passed 166 tests, Ruff lint/format, strict mypy over 10 source files, and a non-root read-only-root container startup. Ephemeral image `3e46bf5e48ce` was removed after verification. This is local implementation evidence only: the operator-managed stack and adaptor are not yet qualified in an isolated cluster or as a released-operator artifact, and no `/logs` or `/log-stream` route has been exercised end-to-end. No frozen `2603.4` digest changes.

### :material-application-edit-outline: Coriolis Worker 0.5.31 Released And POC Accepted

The tracked validator qualifies the exact `2603.4` worker image `sha256:ff30999d...26ab2c9` under its exact direct command; the worker slice is released and POC-accepted as `0.5.31`. The image is Linux/amd64 with root/unset user, `/entrypoint.sh`, an image Cmd of the direct worker/config, and no declared port, volume, or healthcheck. A hardened no-network/no-write image-contract stage uses `find_spec` without importing providers to prove all active provider module roots are present and `coriolis_provider_oracle_vm`, `coriolis_provider_opc`, `coriolis_provider_nutanix`, and `coriolis_provider_cloudstack` are absent, so the active Kubernetes provider class list excludes only oracle-vm/opc/nutanix/cloudstack while the module maps/templates remain provenance. The Kubernetes configuration adds `[luks] tpm2_pcrs = 7`. A registration-only validator on an internal disposable network, with intentionally no host mounts, devices, or provider actions, first proves the expected `NoWorkerServiceError`, then one exact worker enabled/UP, direct worker RPC and scheduler selection, the same hashed database identity across a clean stop/restart and RabbitMQ recovery, unchanged containers, 20s stability, and complete cleanup; final `SUMMARY runtime passed 478.013`. The controller owns one owner-referenced one-replica `Recreate` worker Deployment (explicitly root/privileged/`Unconfined` with read-only root, memory `/tmp`, `emptyDir` logs/export, and `hostPath` `/dev` RW and `/lib/modules` RO) applied after deployer-manager and before the API. During qualification, full-stack long runs reproducibly hit exit 137 at historical 15s bounds for deployer/scheduler/worker, which now use 30s. Conductor's approximately 30s internal shutdown also raced a 30s SIGKILL boundary, so its manifest/validator bound is 45s. Mandatory exit 0 remains; final shutdown timings deployer `24.431s`, worker `22.135s`, minion `8.762s`, transfer `7.369s`, scheduler `20.518s`, conductor `30.689s`. The accepted released-artifact POC (context `virt-infra-dev-buc-hq`, namespace/Application `coriolis-worker-validation-20260826`, OCI chart `coriolis/helm/coriolis-operator:0.5.31`) proved the exact live privileged Deployment and host mounts working for the Pod/registration lifecycle, registration-only RPC/scheduler/DB identity, worker and RabbitMQ replacement, retained no-write CR recreation, and normal cleanup; provider imports/classes beyond `find_spec` and any provider/migration/task/write/Keystone-trust/VDDK/Hyper-V/libvirt/iSCSI/logger workflow remain excluded. These lifecycle results do not change the frozen digest table above.

### :material-application-edit-outline: Coriolis Web 0.5.33 Released And POC Accepted

On 2026-08-26, source `942557a0914b7455af6dbeac6ae5966417bd1223` was released as `0.5.33` by CIXpress Default `opfrnr` (`08:39:32Z`-`08:40:58Z`, top-level and `git-clone`/`kaniko-build`/`helm-update`/`cleanup` all `SUCCEEDED`) at CI commit `9f7151af10e2275e15718a325a12e850601ec5f3`. The released chart and operator digests are `sha256:0e0452229c22c2a4067c55df25c6e09c1ffeefcc8e92eb3fba77ab477713e27e` and `sha256:8f3e80f3c6ea2a79c83feff5078ca45ef8d8615b060ae91b1fae235428a31273`; exact web image `cr.virtomat.io/virtomat/coriolis/coriolis-web:2603.4@sha256:32ebc391ac46fe627185694b3fd252afd7587b152f526dff38ae0a5b887c0db1` is linux/amd64, root/unset user, workdir `/root/coriolis-web`, entrypoint `npm run start`, no Cmd, exposes `3000`, and declares no volume or healthcheck. The 17-stage validator passed in `31.889s` with root/config `200`, exact relative logical-origin URLs, expected missing-fingerprint `500`, first-launch POST `200` then `false`, 20-second stability, recreation reset, and upstream stop exit `1` in `0.747s` (bound `5s`); the accepted exception retains root `0:0` and writable ephemeral root because non-root exited `243`, read-only root made POST return `500`, and direct Node exceeded 10 seconds and was killed `137`.

The accepted isolated POC used `virt-infra-dev-buc-hq`, Argo `default`, namespace/Application `coriolis-web-validation-20260826`, exact chart `0.5.33`, and `skipCrds`. It proved healthy-node preflight; mutation-free Service and Deployment/ReplicaSet collision holds with fixture-only recovery; the exact owner-referenced ClusterIP Service TCP `3000`, ready EndpointSlice, and one-replica `Recreate` Deployment/Pod at this digest; fixed-output `coriolis-web-poc-ok` after 21 seconds; zero-restart Pod replacement and `coriolis-web-reset-ok` after 21 seconds; drift repair; no-write Secret/PVC/PV CR recreation; and normal cleanup. It does not establish Ingress/TLS/external routing, browser/API/provider/write flow, persisted first-launch state, HA/production storage/backup/restore, or overall readiness.

### :material-application-edit-outline: Scheduler, Transfer-Cron, Minion-Manager, And Deployer-Manager Runtime Qualification

The tracked validator qualifies the exact `2603.4` scheduler image `sha256:45bea9e...a73c41`, transfer-cron image `sha256:3a44d3b...2b3ea9c`, minion-manager image `sha256:1ea016dd...e1fb9`, and deployer-manager image `sha256:a2a7091d...f36aa` under their exact direct commands. All run as numeric UID/GID `42434` with read-only root, dropped `ALL`, no-new-privileges, read-only generated `/etc/coriolis`, writable only `/tmp` and `/var/log/coriolis`, on a private internal network with no host ports and no Service/listener. Deployer-manager's generated `log_dir` requires the log path; after the initial sanitized `OSError` exposed its omission, the corrected full validator passed in `256.242s`. Scheduler, transfer-cron, minion-manager, and deployer-manager are released as `0.5.26`, `0.5.27`, `0.5.28`, and `0.5.29`; deployer-manager source `4382c000f18cc323bacebc0e1df78c8b2f052d20` was published by fully successful CIXpress `wk1zo8` as CI release `c35afc7032de19ec43f3870b6300e2f4f02370aa`, with operator digest `sha256:37634ac8afd4ce7e7445aeb5f5786867cdd009d0f41b13b9baf95b1e92be4b08`. Its accepted released-artifact POC proved exact deployer digest/command/security and omitted surfaces, mutation-free collision holding, automatic recovery, stability, RabbitMQ recovery, retained no-write CR recreation, and normal cleanup; replacement and duplicate-create latency are deliberately unclaimed. These lifecycle results do not change the frozen digest table above.

## :material-book-open-page-variant-outline: Kolla And Support Images (`2023.1-ubuntu-jammy`)

Exact immutable manifest digests for support images at tag `2023.1-ubuntu-jammy`:

| Image | Digest |
| --- | --- |
| barbican-api | `a142a57761f708b241358383d6445ac5da4e05ae26a284369081cfb15cca8a60` |
| barbican-keystone-listener | `cc6ee5067f336a578e761a031116b32b60a08ba323d1c33f0758d0e1c43ba0cb` |
| barbican-worker | `ed907de778900b08f2645c9eeb82d48d8202ce6517cdb543d42db2e88ea642b5` |
| keystone | `7c57962762f5e6fdb1a109097e8f3e2e5f6218ad9c09f10a585adb67ed245cf0` |
| keystone-fernet | `2f10e712c99f8c9bb78cdc9a33452d9994e228f46c00aaeb2d45b1806e3ed03f` |
| keystone-ssh | `a3ab792cb4375c6aa4eab3930486ec536629fee45ff4c9285a5e23c2b4fed60c` |
| kolla-toolbox | `b0952a70fad1df6ed8351ff522b1e86b77148d52efc77d85b048a517574e0bff` |
| mariadb-server | `22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e` |
| memcached | `746b93082a4f6d07f464e93d4b14f5e30510abf17a9ae0a4af20e111408c8f1e` |
| rabbitmq | `a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a` |

### :material-application-edit-outline: MariaDB Runtime Evidence

The approved MariaDB workload image is `cr.virtomat.io/virtomat/coriolis/mariadb-server:2023.1-ubuntu-jammy@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e`; local inspection and pulls used its digest-only form `cr.virtomat.io/virtomat/coriolis/mariadb-server@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e`. It is Linux/amd64; image user `mysql` is UID/GID `42434:42434` with supplemental `kolla` group `42400`; Entrypoint is `dumb-init --`; Cmd is `kolla_start`; Kolla is `16.6.1`; MariaDB is `10.6.22`; it has no OCI healthcheck; data is `/var/lib/mysql`; runtime socket/PID files are under `/run/mysqld`; and it listens on `3306`.

Local disposable Docker validation proved direct `mariadbd --console` single-node non-Galera operation (`wsrep_on=OFF`) as `42434:42434` with read-only root, no-new-privileges, all capabilities dropped, writable `/var/lib/mysql`, `/run/mysqld`, and `/tmp`, bind `0.0.0.0:3306`, 64M packet limit, 256M InnoDB log size, and utf8mb4/InnoDB. Console logs included startup, ready-for-connections, normal/InnoDB shutdown, and shutdown-complete messages. `mariadb-install-db --datadir=/var/lib/mysql --auth-root-authentication-method=normal` initialized only absent system tables; first bootstrap used passwordless local socket root only to establish the selected root password through stdin/file SQL. Mode-0600 client files then authenticated `mariadb ... --execute='SELECT 1'` and `mariadb-admin ... ping` without secret environment variables or password arguments. Docker stop through the image's `dumb-init` entrypoint produced clean shutdown within 30 seconds, and same-volume recreation preserved data. All disposable resources were removed.

`kolla_start` is not an operator path: it fails without `/var/lib/kolla/config_files/config.json`, requires `DB_ROOT_PASSWORD`, and passes client passwords in process arguments. `healthcheck_mariadb` and `clustercheck` are Galera-specific and fail against a healthy non-Galera server. `log-error=/dev/stderr` must not be used because MariaDB attempts `/dev/stderr.err`; `--console` is the logging path. MariaDB and Memcached workload implementations are released and POC-tested, but no runtime workload is currently deployed after cleanup. MariaDB CSI/cross-node validation and production backup/restore, HA, and RPO/RTO remain open.

### :material-application-edit-outline: RabbitMQ Runtime Evidence

The approved image is `cr.virtomat.io/virtomat/coriolis/rabbitmq:2023.1-ubuntu-jammy@sha256:a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a`; local image ID is `sha256:f9e28ef3ed172cfdda9e6c3d56c509ceaee672b516381343244ed40332a19e73`. Local inspection established Linux/amd64, Kolla `16.6.1`, UID/GID `42439`, and no supplemental group requirement. Reject default `dumb-init --single-child --` plus `kolla_start` without configuration. Direct `/usr/sbin/rabbitmq-server` and `/usr/sbin/rabbitmq-diagnostics` proved plaintext `0.0.0.0:5672`, console-only logging, read-only root, dropped `ALL`, no-new-privileges, and writable `/var/lib/rabbitmq`, `/run/rabbitmq`, and `/var/log/rabbitmq`.

File-only bootstrap mounts the retained infrastructure Secret key as a file, uses a random 4-byte salt, and streams Rabbit SHA256 definitions without credential/hash argv, environment, log, or output exposure. Mode-`0600` ephemeral definitions provision `openstack`, vhost `/`, and exact permissions. Two retained-volume launches reached Ready in 15.024s and 13.533s; sanitized broker checks and state/marker persistence/reconvergence passed; SIGTERM exited `0` in 6.580s and 6.757s; all disposable artifacts were removed. This is local evidence only, not a Kubernetes POC.

## :material-book-open-page-variant-outline: Third-Party Images

Step CA is **pinned and mirrored** as historical inventory: sourced by exact digest `sha256:e9e8fa3262bf37b130962ffddbf6a64ac188f0bbb80959cf3ddc04c6bf294c3d` from `smallstep/step-ca` and mirrored to `cr.virtomat.io/virtomat/coriolis/step-ca:2603.4`. Independent destination verification returned the exact expected digest. It is deferred and is not selected for the current initial runtime.

Legacy logger/InfluxDB remain **deferred** and are not implementation dependencies. The `influxdb:1.7` candidate digest `2eb372aaa8f3446e6876b8095d97f9a4e90711593995806f1158f4c988b9765e` is not locked. The operator-managed Loki, Alloy, unprivileged-NGINX, and logs-adaptor images are recorded above and are distinct from this immutable `2603.4` appliance and `2023.1-ubuntu-jammy` support ledger.

## :material-book-open-page-variant-outline: Gate Status

- The metadata gate is **complete**: the exact `2603.4` image set, immutable digests, platform, users, listeners, and health capability are recorded above.
- All 26 approved images were **mirrored serially on 2026-08-20** to `cr.virtomat.io/virtomat/coriolis` with preserved and verified manifest digests via `scripts/mirror-images.py`; independent destination verification of Step CA returned the exact expected digest. Legacy logger/InfluxDB remain deferred and replaced by the ADR 0007 Loki/Alloy architecture.
- **Pull validation passed on 2026-08-20** in `virt-infra-dev-buc-hq` namespace `coriolis`: all exact 21 historically selected image references (10 application images at `2603.4`, 10 support images at `2023.1-ubuntu-jammy`, and Step CA at `2603.4`) pulled successfully, validated serially by `scripts/validate-image-pulls.py` using one short-lived Pod at a time with `imagePullPolicy: Always`, explicit context/namespace, and the destination Secret `coriolis-appliance-registry` (type `kubernetes.io/dockerconfigjson`). Each successful Pod was removed; **zero residual validation Pods remain**. Independent main-agent validation of Step CA repeated successfully with the expected digest. This historical pull result does not select Step CA or web-proxy for the current initial runtime.
- The inventory and pull gates are **complete**. Memcached reconciliation is released as `0.5.8`; RabbitMQ is released as `0.5.11` and passed its accepted isolated single-node POC. The prior `0.5.10` POC was not accepted because readiness period `5s`/timeout `5s` produced 37 timeouts and Endpoint flapping; `0.5.11` uses `10s`/`15s`. Keystone is released as `0.5.14` and passed its accepted isolated single-node POC after `0.5.13` was not accepted because of emptyDir mount-root permissions and fix `087bbc2` corrected the prepare init. At POC cleanup, Argo ran `0.5.14`, `1/1`, with zero restarts and no appliance CRs or POC workload. CSI/cross-node, backup/restore, HA, RPO/RTO, credential rotation, production storage, Ingress, overall appliance readiness, and remaining runtime design are deferred.

Related project tracking: [Roadmap](../ROADMAP.md) and [Status](../STATUS.md).
