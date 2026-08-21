# Runtime Contract

Existing upstream Coriolis component repositories and images are immutable inputs. This operator must not rebuild or patch upstream Coriolis code or images.

## :material-book-open-page-variant-outline: Completed Marker-Only Contract

Release `0.5.2` at `86552e46fd2fb13b05d66cc2b7e25f4968f00846` completed the initial controller lifecycle milestone. The namespace-scoped `coriolis.cloudbase.it/v1alpha1` `CoriolisAppliance` controller reconciled only an owned marker ConfigMap and status. `Ready=False/RuntimeNotImplemented` was expected; normal deletion garbage-collected the marker, and no sample CR or marker remains.

That limited, non-destructive contract is completed history. It is not the target runtime behavior.

## :material-book-open-page-variant-outline: Kubernetes-Native Core Runtime

Creating a `CoriolisAppliance` must deploy the complete selected Coriolis stack as Kubernetes workloads directly in namespace `coriolis`. It must not provision an external VM. OpenStack and VMware remain migration endpoints.

The first runtime profile is `core`, targeting exact official Coriolis release `2603.4` (not `2603.41`/`2603.42`). Its initial core workload is API, conductor, scheduler, transfer cron, minion manager, deployer manager, privileged worker, compressor, web, and web proxy. `coriolis-common` is a base image, not a workload. Deferred: licensing server, Metal Hub, console editor, and logger/InfluxDB.

The first acceptance is complete bootstrap with an internally healthy and reachable UI and API. It does not include a migration test.

!!! note
    The image inventory and pull gate are complete. The exact `2603.4` image set, immutable digests, platform, users, listeners, and health capability are recorded in the [Image Inventory](image-inventory.md) ledger; all 26 approved images are mirrored to `cr.virtomat.io/virtomat/coriolis`, and all 21 initial-runtime image pulls passed in `virt-infra-dev-buc-hq` namespace `coriolis`. Runtime implementation is unblocked and is the next phase.

## :material-book-open-page-variant-outline: API And Lifecycle Policy

!!! note
    The API slice below is committed locally at `ab9df83` (branch `dev`, not pushed) and is absent from the deployed operator. Full controller lifecycle validation remains on release `0.5.2`; the currently deployed `0.5.3` retains the marker-only controller behavior.

The `v1alpha1` API defines optional/defaulted `spec.profile` (`core`, the only enum value), required non-empty `spec.version`, and optional non-empty `status.acceptedVersion`. The sample uses `profile: core`, `version: "2603.4"`.

`spec.version` is immutable for the first runtime profile, enforced by the controller rather than admission: no CEL/validation rule rejects a change, so a rejected request remains observable in status. The controller compares the requested version against the persisted `status.acceptedVersion`; a change applies no resources, preserves the accepted state, advances `observedGeneration`, and sets `Upgradeable=False` with reason `UpgradeBlocked` rather than attempt an unsafe upgrade. Unsupported initial profiles/versions apply no resources and report `Accepted=False` rejection conditions.

The implemented status condition types are `Accepted`, `Progressing`, `Reconciled`, `Ready`, `Degraded`, and `Upgradeable`. The API-only reconcile truthfully reports `Ready=False/RuntimeNotImplemented`; `Ready=True` is allowed only after mandatory Jobs, dependencies, workloads, and internal UI/API checks pass.

Deletion removes operator-owned workloads, Services, Jobs, and generated ConfigMaps. It retains PVCs, CA state, and state credentials for recovery. Pre-existing referenced Secrets are never deleted. The initial policy avoids a destructive finalizer.

## :material-book-open-page-variant-outline: Development Constraints

The privileged worker may mount host `/dev` and `/lib/modules`. Single-node `local-path` storage is acceptable. Retained state is not production HA.

Console-editor behavior must become declarative Kubernetes or CR configuration, not host mutation. Logger Unix-socket compatibility may initially use a shared single-node retained volume; this is a transitional development design.

Deferred work includes the licensing server and UI, Metal Hub, console editor and VM-host administration, external provider configuration, migration validation, automatic upgrades, and production HA.

## :material-book-open-page-variant-outline: Image Inventory Gate

RC4 is blocked/OVA-only for Kubernetes: Build `868` exported an OVA, but no `registry.cloudbase.it/appliance/coriolis-*` repository carries a `2608*` tag. The approved fallback is exact official release `2603.4`; its authoritative inventory is the [Image Inventory](image-inventory.md) ledger. The metadata gate is complete and all 26 approved images were mirrored to `cr.virtomat.io/virtomat/coriolis` on 2026-08-20 with preserved/verified digests. Pull validation in `virt-infra-dev-buc-hq` namespace `coriolis` has passed: all 21 initial-runtime image references pulled successfully via `scripts/validate-image-pulls.py` using the destination Secret `coriolis-appliance-registry` (type `kubernetes.io/dockerconfigjson`), and no pull-validation Pods remain. The gate is complete, runtime implementation is unblocked and is the next phase, and no Coriolis core runtime workloads have been implemented or deployed yet.

!!! note
    The pull gate has passed; do not claim runtime readiness or bootstrap. Runtime implementation is the next phase and no core runtime workloads exist yet.

## :material-book-open-page-variant-outline: Ordered Implementation Plan

1. Image and runtime inventory. *(complete)*
2. CRD and runtime API. *(committed locally at `ab9df83`, not pushed/deployed)*
3. Foundational resource contracts. *(documented in the [Foundational Resource Contract](foundational-resource-contract.md); the metadata-only helper slice `appliance_resource_name`/`appliance_identity`/`build_resource_metadata` is implemented locally and validated — 44 unit tests, `mypy src`, Ruff — and committed locally at `fbab6e5` but not pushed or deployed. The collision/migration marker API-layer slice is implemented locally and committed at `d8df00f` on `dev`, but not pushed or deployed (70 unit tests; Ruff lint/format; mypy; Helm lint/template; `git diff --check`) — covering marker pre-read classification, legacy `0.5.2`/`0.5.3` marker normalization, and `ResourceCollision` status. The pure retained-resource authorization/classification slice (`classify_retained_resource`, `RetainedClassification.ABSENT/REUSE/COLLISION`) is implemented and validated locally — 94 unit tests; Ruff lint/format; mypy; Helm lint/template; `git diff --check` — and is **committed locally at `1b73045` on `dev`, but not pushed or deployed**; it authorizes exact-match, ownerless reuse of retained PVCs/state Secrets/CA state, constructs/reconciles nothing, adds no adoption mutations, and keeps external/pre-existing resources such as `coriolis-appliance-registry` read-only and outside this policy. A changed CR UID is intentionally ignored (retained resources survive CR deletion/recreation, so exact stable-identity reattachment works across UID changes). A documentation-only Secret/configuration contract slice now freezes the foundational Secret/ConfigMap names, key layouts, and the primary `coriolis.conf` split (retained `<appliance>-coriolis-credentials`, `<appliance>-infrastructure-credentials`, `<appliance>-step-ca-credentials`; owner-referenced rebuildable ConfigMap `<appliance>-coriolis-config` and Secret `<appliance>-coriolis-config-secret`, mounted together at `/etc/coriolis`); it changes no runtime behavior and is **committed locally at `8ce26ba` on `dev`, but not pushed or deployed**. The later pure builder slice at `050f16e` closes the concrete manifest-builder gate, and the pure generator slice at `a604579` freezes the operator-generated credential policy/algorithm without runtime wiring. Before any MariaDB vertical slice, remaining gates are configuration rendering inputs, retained Secret semantic validation, collision-safe pre-reads, `ABSENT`-only generation, SSA, minimal Secret RBAC, and reconciliation/status failure semantics; TLS/optional credentials/storage/probes/readiness/bootstrap/rotation remain deferred)*
4. Generated configuration and secrets. *(pure builders are committed at `050f16e` and pure retained credential generators at `a604579`, not pushed/deployed. Generators independently produce the seven frozen keys with `secrets.token_urlsafe(32)` and are not called by `main.py`. Generate-once/reuse is unwired policy: generate only for `ABSENT` retained Secrets, reuse exact matching ownerless Secrets unchanged, and fail closed on collisions. Next: configuration rendering inputs, retained Secret semantic validation, collision-safe pre-reads, SSA, minimal Secret RBAC, and reconciliation/status failure semantics; rotation remains deferred.)*

5. Foundational dependencies and bootstrap Jobs.
6. Coriolis workloads.
7. Server-side apply and controller watches.
8. Status and readiness.
9. Tests.
10. Development deployment and acceptance.
