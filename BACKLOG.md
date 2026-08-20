# Backlog

## Completed Locally: Bootstrap and Controller Skeleton

- Create the Python 3.12, uv, and Kopf project skeleton.
- Add the namespaced `CoriolisAppliance` CRD at `coriolis.cloudbase.it/v1alpha1` under `helm/crds/`.
- Implement namespace-scoped watching of the Helm release namespace.
- Reconcile an idempotent marker ConfigMap only.
- Set successful reconciliation status and keep `Ready=False` until an appliance runtime exists.
- Add focused validation for the first controller behavior.
- Build and run the container image locally as its non-root user.

## Completed: Controller Lifecycle Validation

- In `coriolis`, validated sample create, generation and observed generation, `Accepted=True`, `Reconciled=True`, and expected `Ready=False/RuntimeNotImplemented` status.
- Validated one owned `example-operator-state` marker with `development/1`, controller replacement/resume, ownership and marker uniqueness, and preserved condition transition times.
- Validated `spec.version` propagation to `development-updated/2` without changing transition times, then normal deletion and marker garbage collection. No sample CR or marker remains.

## Completed: Dev Deployment Validation

- Deploy release `0.5.2` through Argo CD in the approved `coriolis` namespace.
- Validate registry authentication through `regcred`, CRD installation, namespaced RBAC, operator startup, and liveness.
- Confirm the Argo application is `Synced` and `Healthy` and the operator Deployment is `1/1` available.

## Completed Locally: Consistency Sweep

- Align handler result delivery, collision-safe naming, standard conditions, CRD/RBAC scope, and the documented validation boundary.

## Documented: CIXpress CI/Release Model

- Record the CIXpress ordered Job pipeline, version policy, observability contract, and non-transactional release semantics.

## Completed: CIXpress Publication Validation

- Reran CI and confirmed registry publication plus `SUCCEEDED` states for all expected steps.

## Pending CIXpress Integration

- Receive and add the exact CIXpress pipeline configuration, Template, and Job manifests.
- Validate version-alignment behavior, including the dev branch tag check.
- Define trigger and monitoring credentials without storing secrets in the repository.
- Integrate Argo CRD pre-upgrade automation into promotion/deployment; the standard build pipeline does not perform it.

## Planned: Kubernetes-Native Core Runtime

1. Gate implementation on a complete `2608.0-rc4` application/support image inventory. Start from Jenkins job `1_coriolis-appliance-setup` Build `868`, validate metadata before pulls, and record immutable digests, entrypoints, users, listeners, health capabilities, compatibility, and `registry.cloudbase.it/appliance` access. Use a dedicated pull Secret created securely from Jenkins credential ID `docker-appliance-creds`; never record its value. Stop if only an OVA or no complete compatible image set exists.
2. Define the `core` CRD/runtime API for Kubernetes-native workloads in `coriolis`; `spec.version` is immutable and changes report `Upgradeable=False` with reason `UpgradeBlocked`.
3. Implement naming, ownership, retention, generated configuration and secrets, then foundational dependencies and bootstrap Jobs.
4. Implement MariaDB, RabbitMQ, Memcached, Keystone, Barbican, Step CA, InfluxDB/logger compatibility, API, conductor, scheduler, transfer cron, minion manager, deployer manager, privileged worker, compressor, web, and web proxy.
5. Add server-side apply, controller watches, status/readiness, tests, and development acceptance. `Ready=True` requires mandatory Jobs, dependencies, workloads, and internal UI/API checks.
6. Retain PVCs, CA state, and state credentials on deletion; delete only operator-owned workloads, Services, Jobs, and generated ConfigMaps. Never delete pre-existing referenced Secrets. Avoid a destructive finalizer.

## Pending Decisions

- Define promotion.
- Define the exact CIXpress repository integration and trigger.
- Select a license.
