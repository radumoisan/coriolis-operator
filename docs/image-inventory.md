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

The authoritative MariaDB image was anonymously inspected and pulled by digest: `cr.virtomat.io/virtomat/coriolis/mariadb-server@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e`. It is Linux/amd64; image user `mysql` is UID/GID `42434:42434` with supplemental `kolla` group `42400`; Entrypoint is `dumb-init --`; Cmd is `kolla_start`; Kolla is `16.6.1`; MariaDB is `10.6.22`; it has no OCI healthcheck; data is `/var/lib/mysql`; runtime socket/PID files are under `/run/mysqld`; and it listens on `3306`.

Local disposable Docker validation proved direct `mariadbd` single-node non-Galera operation (`wsrep_on=OFF`) as `42434:42434` with read-only root, no-new-privileges, all capabilities dropped, writable `/var/lib/mysql`, `/run/mysqld`, and `/tmp`, bind `0.0.0.0:3306`, 64M packet limit, 256M InnoDB log size, and utf8mb4/InnoDB. It initialized absent system tables only with `mariadb-install-db`, used mode-0600 ephemeral SQL/client files for raw credentials without credential environment variables, command arguments, logs, or output, performed idempotent database/user/grant bootstrap and authenticated TCP `SELECT 1`, and preserved a durable marker across clean stop and recreation with the same Docker volume. All disposable resources were removed.

`kolla_start` is not an operator path: it fails without `/var/lib/kolla/config_files/config.json`, requires `DB_ROOT_PASSWORD`, and passes client passwords in process arguments. `healthcheck_mariadb` and `clustercheck` are Galera-specific and fail against a healthy non-Galera server. This evidence does not select a Kubernetes workload contract. MariaDB remains blocked on workload kind; PVC ownership/fsGroup behavior; storage class/size, access mode, and retention; exact generated ConfigMap/Secret/init-container/start-script manifests; probe timing/thresholds/failure behavior; resources; lifecycle and recovery policy; and reliable container log capture. The CRD has no storage configuration. `log-error=/dev/stderr` failed by attempting `/dev/stderr.err`; bare `log-error` did not prove Docker-observable readiness logs.

## :material-book-open-page-variant-outline: Third-Party Images

Step CA is **pinned and mirrored** as historical inventory: sourced by exact digest `sha256:e9e8fa3262bf37b130962ffddbf6a64ac188f0bbb80959cf3ddc04c6bf294c3d` from `smallstep/step-ca` and mirrored to `cr.virtomat.io/virtomat/coriolis/step-ca:2603.4`. Independent destination verification returned the exact expected digest. It is deferred and is not selected for the current initial runtime.

Logger/InfluxDB remain **deferred**. The `influxdb:1.7` candidate digest `2eb372aaa8f3446e6876b8095d97f9a4e90711593995806f1158f4c988b9765e` is not yet locked.

## :material-book-open-page-variant-outline: Gate Status

- The metadata gate is **complete**: the exact `2603.4` image set, immutable digests, platform, users, listeners, and health capability are recorded above.
- All 26 approved images were **mirrored serially on 2026-08-20** to `cr.virtomat.io/virtomat/coriolis` with preserved and verified manifest digests via `scripts/mirror-images.py`; independent destination verification of Step CA returned the exact expected digest. Logger/InfluxDB remain deferred.
- **Pull validation passed on 2026-08-20** in `virt-infra-dev-buc-hq` namespace `coriolis`: all exact 21 historically selected image references (10 application images at `2603.4`, 10 support images at `2023.1-ubuntu-jammy`, and Step CA at `2603.4`) pulled successfully, validated serially by `scripts/validate-image-pulls.py` using one short-lived Pod at a time with `imagePullPolicy: Always`, explicit context/namespace, and the destination Secret `coriolis-appliance-registry` (type `kubernetes.io/dockerconfigjson`). Each successful Pod was removed; **zero residual validation Pods remain**. Independent main-agent validation of Step CA repeated successfully with the expected digest. This historical pull result does not select Step CA or web-proxy for the current initial runtime.
- The inventory and pull gates are **complete**. The foundational runtime gate is committed locally at `862777d`, with status commit `f219977`; both are unpushed and undeployed. The four-Service slice is committed locally at `797235b` on `dev`, unpushed and undeployed, and implements only RabbitMQ, Memcached, MariaDB, and Keystone Services; workloads, endpoints, Ingress, readiness, and remaining runtime design are deferred, and no Coriolis core runtime workloads have been implemented or deployed.

Related project tracking: [Roadmap](../ROADMAP.md), [Status](../STATUS.md), and [Appliance Runtime](../../docs/appliance-runtime.md).
