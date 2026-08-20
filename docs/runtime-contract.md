# Runtime Contract

Existing upstream Coriolis component repositories and images are immutable inputs. This operator must not rebuild or patch upstream Coriolis code or images.

## :material-book-open-page-variant-outline: Completed Marker-Only Contract

Release `0.5.2` at `86552e46fd2fb13b05d66cc2b7e25f4968f00846` completed the initial controller lifecycle milestone. The namespace-scoped `coriolis.cloudbase.it/v1alpha1` `CoriolisAppliance` controller reconciled only an owned marker ConfigMap and status. `Ready=False/RuntimeNotImplemented` was expected; normal deletion garbage-collected the marker, and no sample CR or marker remains.

That limited, non-destructive contract is completed history. It is not the target runtime behavior.

## :material-book-open-page-variant-outline: Kubernetes-Native Core Runtime

Creating a `CoriolisAppliance` must deploy the complete selected Coriolis stack as Kubernetes workloads directly in namespace `coriolis`. It must not provision an external VM. OpenStack and VMware remain migration endpoints.

The first runtime profile is `core`, targeting Coriolis release `2608.0-rc4`. Its initial components are MariaDB, RabbitMQ, Memcached, Keystone, Barbican, Step CA, InfluxDB/logger compatibility, API, conductor, scheduler, transfer cron, minion manager, deployer manager, privileged worker, compressor, web, and web proxy.

The first acceptance is complete bootstrap with an internally healthy and reachable UI and API. It does not include a migration test.

!!! warning
    The exact component images, immutable digests, and registry availability are not confirmed. Do not implement the runtime until the image gate is passed.

## :material-book-open-page-variant-outline: API And Lifecycle Policy

`spec.version` is immutable for the first runtime profile. An attempted version change must leave the current workloads unchanged and set `Upgradeable=False` with reason `UpgradeBlocked` rather than attempt an unsafe upgrade.

The planned status condition types are `Accepted`, `Progressing`, `Reconciled`, `Ready`, `Degraded`, and `Upgradeable`. `Ready=True` is allowed only after mandatory Jobs, dependencies, workloads, and internal UI/API checks pass.

Deletion removes operator-owned workloads, Services, Jobs, and generated ConfigMaps. It retains PVCs, CA state, and state credentials for recovery. Pre-existing referenced Secrets are never deleted. The initial policy avoids a destructive finalizer.

## :material-book-open-page-variant-outline: Development Constraints

The privileged worker may mount host `/dev` and `/lib/modules`. Single-node `local-path` storage is acceptable. Retained state is not production HA.

Console-editor behavior must become declarative Kubernetes or CR configuration, not host mutation. Logger Unix-socket compatibility may initially use a shared single-node retained volume; this is a transitional development design.

Deferred work includes the licensing server and UI, Metal Hub, console editor and VM-host administration, external provider configuration, migration validation, automatic upgrades, and production HA.

## :material-book-open-page-variant-outline: Blocking Image Gate

Before implementation, inventory the complete `2608.0-rc4` application and support image set. Start with sanitized metadata from appliance Jenkins job `1_coriolis-appliance-setup` Build `868`, then verify registry metadata before pulling layers. Record immutable digests, platforms, entrypoints, users, listeners, health capabilities, and compatibility. Verify access to `registry.cloudbase.it/appliance` with a dedicated pull Secret created securely from Jenkins credential ID `docker-appliance-creds`; never store or display credential values.

!!! danger
    Stop if the release exists only as an OVA or if no complete compatible image set exists.

## :material-book-open-page-variant-outline: Ordered Implementation Plan

1. Image and runtime inventory.
2. CRD and runtime API.
3. Naming, ownership, and retention.
4. Generated configuration and secrets.
5. Foundational dependencies and bootstrap Jobs.
6. Coriolis workloads.
7. Server-side apply and controller watches.
8. Status and readiness.
9. Tests.
10. Development deployment and acceptance.
