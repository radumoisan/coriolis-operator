# Progress Log

This log is append-only. Add a dated entry for meaningful project progress.

## 2026-08-20: Documentation and Tracking Baseline

Created the project documentation and tracking baseline before implementation of the bootstrap/controller skeleton. Recorded the initial namespace-scoped, non-destructive controller contract and pending operational decisions.

## 2026-08-20: Controller Skeleton Implemented Locally

Implemented and locally validated the Python 3.12 Kopf controller skeleton: namespace-scoped marker ConfigMap reconciliation, bounded generated names, server-side apply, successful reconciliation status, configurable logging and liveness, and Helm security and resource defaults. Chart release conventions, CI version and image-tag synchronization, registry publication, and licensing remain open.

## 2026-08-20: Container Image Validated Locally

Built the Python 3.12 container image and verified that it runs as the non-root `operator` user. A later consistency sweep replaced reliance on a base-image group with a dedicated `coriolis` group.

## 2026-08-20: Safe Validation Boundary

Local validation is complete. Live-cluster smoke validation remains pending and must run only in an isolated disposable Kubernetes cluster, never a shared or production context.

## 2026-08-20: Documentation Consistency Sweep

Aligned the documented handler result, collision-safe naming, standard conditions, and CRD/RBAC scope with the local implementation. Documented the restricted runtime contract and pending disposable-cluster smoke test without treating garbage collection as proven behavior.

## 2026-08-20: Consistency Sweep Validated

Validated the dependency lock, Python formatting and linting, typing, nine unit tests, Helm lint and rendering, synchronized project/chart/image versions, and a fresh non-root container build. Live behavior and RBAC remain pending verification in a disposable Kubernetes cluster.

## 2026-08-20: CIXpress CI Model Documented

Documented the supplied CIXpress Kubernetes Job pipeline, release version policy, observability contract, and non-transactional publication behavior. This repository does not yet contain CIXpress configuration, Templates, Job manifests, triggers, or credentials; CRD pre-upgrade automation remains promotion/deployment work.

## 2026-08-20: Dev Environment Boundary Received

Recorded the approved read-only CIXpress monitoring boundary for `infra-dev-buc-hq` and its explicit Kubernetes context and namespace. Received the report that CI accepted and started a build for commit `b824f5c`; no pipeline ID or final step outcome is known, so no pipeline success is claimed. The dedicated operator namespace remains TBD and requires approval before deployment or live validation.

## 2026-08-20: CIXpress Pipeline Result And Polling Procedure

Recorded the observed result for commit `b824f5c`: pipeline `hlzfy3` using template `Default` failed after starting at `2026-08-20T09:23:41+00:00` and completing at `2026-08-20T09:23:56Z`. Detail steps were empty. Safe log metadata showed one 25-line `git-clone` stream and one 12-line `kaniko-build` stream, while `helm-update` and `cleanup` returned HTTP 404; no log content was exposed and root cause remains unknown. Added the bounded polling-only monitoring procedure and project-local OpenCode skill, including explicit context/namespace, GET-only API access, metadata-first log handling, and no SSE or `/stream`.

## 2026-08-20: Provisional Registry Baseline And Confirmed CI Failure

Confirmed that pipeline `hlzfy3` failed because Kaniko could not resolve `example.invalid` during push-permission checking. Set the provisional synchronized `0.0.0` image baseline to `cr.virtomat.io/virtomat/coriolis/operator`. Registry publication/authentication and promotion remain pending validation.

## :material-book-open-page-variant-outline: 2026-08-20: CIXpress Release 0.2.0 Published

The dummy trigger for commit `c9d9dd5ec3ecfe06f03e0cbfc1eda3ff4b0fd58d` completed as pipeline `jcr0vn` using template `Default`: it started at `2026-08-20T11:18:10+00:00`, completed at `2026-08-20T11:19:27+00:00`, and reported top-level plus `git-clone`, `kaniko-build`, `helm-update`, and `cleanup` states as `SUCCEEDED`. CI image push and OCI chart publication were validated. The generated release commit and tag `0.2.0` resolve to `49cb5dc7dbe247e432e604db19078ecf1c2b5437`; image pull/deployment and promotion remain pending.

## :material-book-open-page-variant-outline: 2026-08-20: Argo Deployment Validated

Configured the `regcred` image pull secret through the source-controlled Helm default and published release `0.3.0`. Its deployment confirmed registry authentication but exposed an invalid CRD schema and a chart command that could not find the virtual-environment console script. Removed the forbidden CRD schema keywords, corrected the command, validated the CRD with Kubernetes server-side dry-run, and published release `0.4.0`. Pipeline `gepx3l` reported all expected steps as `SUCCEEDED`, but one intermediate `INPROGRESS` response already contained a completion timestamp; publication was independently confirmed by release commit and tag `4eee8a9f24eb05640c61ece8fa057ecd49136e85`. Argo synchronized `0.4.0` successfully in `coriolis`; the application is `Healthy`, the CRD is established, and the operator Deployment is `1/1` available with a ready pod and zero restarts.

## :material-book-open-page-variant-outline: 2026-08-20: Releases 0.5.1 and 0.5.2 Published

The initial `0.5.0` live sample exposed Kubernetes Python client `32.0.1` rejecting `_content_type`. Source commit `d3aecb2ce71fc83730969db174b50727c37fe96c` was released by pipeline `jqd3ri` as `0.5.1` at `5ffc69534a558417405e99e1b75c3145319ac19a`. Release `0.5.1` exposed missing ConfigMap-create RBAC; source commit `cbdd28fc645e37c5a8146ff900f7fd84f6330210` added ConfigMap `create` and `patch` only. Pipeline `5ly5kg` verified `git-clone`, `kaniko-build`, `helm-update`, and `cleanup` as `SUCCEEDED` and generated `0.5.2` at `86552e46fd2fb13b05d66cc2b7e25f4968f00846`.

## :material-book-open-page-variant-outline: 2026-08-20: Release 0.5.2 Lifecycle Validated

Argo Application `argocd/coriolis` targeting `0.*.*` resolves to release `0.5.2` and is `Synced` and `Healthy`; its Deployment uses image `0.5.2`, is `1/1` available, and has one Running/Ready pod with zero restarts. In approved namespace `coriolis`, the `example` sample completed create, controller replacement/resume, `spec.version` update, and normal deletion checks. Generation and observed generation started at `1`; conditions were `Accepted=True`, `Reconciled=True`, and expected `Ready=False/RuntimeNotImplemented`. The single owned `example-operator-state` marker held `development/1`; replacement/resume preserved marker state, ownership, uniqueness, and condition transition times. The old pod exceeded its grace period but disappeared naturally before authorized force cleanup, so no force command ran. Updating to `development-updated` propagated generation, observed generation, status, and marker to `2` while preserving transition times. Normal CR deletion garbage-collected the marker; no sample CR or marker remains. Appliance runtime work remains open pending an explicit runtime/API contract.

## :material-book-open-page-variant-outline: 2026-08-20: Kubernetes-Native Core Runtime Agreed

Recorded the next runtime architecture: creating `CoriolisAppliance` deploys the selected complete Coriolis stack as Kubernetes workloads directly in `coriolis`, not an external VM; OpenStack and VMware remain migration endpoints. The first `core` profile targets `2608.0-rc2`, with initial acceptance limited to complete bootstrap and internally healthy/reachable UI and API. The first blocking gate is a complete application/support image inventory, immutable digests, runtime compatibility, `registry.cloudbase.it/appliance` access, and an approved pull Secret; work stops if the release is OVA-only or lacks a complete compatible image set. Recorded the ordered implementation packages, immutable `spec.version` with `Upgradeable=False` and reason `UpgradeBlocked`, readiness conditions, recovery-retention deletion policy, development-only host mounts and local-path storage, and deferred production/feature scope. Upstream Coriolis images and code remain immutable inputs.

## :material-book-open-page-variant-outline: 2026-08-20: Runtime Target Advanced To RC4

Advanced the active `core` inventory target from `2608.0-rc2` to `2608.0-rc4`. Recorded the canonical Confluence environment source, appliance Jenkins job `1_coriolis-appliance-setup`, and reference Build `868`. Registry work will validate metadata before pulls and use a dedicated pull Secret created securely from Jenkins credential ID `docker-appliance-creds`; secret values remain outside Git and reports. The safe unauthenticated Build `868` metadata request timed out from the workspace, and work is paused while the user restores access.
