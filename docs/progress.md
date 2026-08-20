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
