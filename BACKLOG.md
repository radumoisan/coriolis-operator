# Backlog

## Completed Locally: Bootstrap and Controller Skeleton

- Create the Python 3.12, uv, and Kopf project skeleton.
- Add the namespaced `CoriolisAppliance` CRD at `coriolis.cloudbase.it/v1alpha1` under `helm/crds/`.
- Implement namespace-scoped watching of the Helm release namespace.
- Reconcile an idempotent marker ConfigMap only.
- Set successful reconciliation status and keep `Ready=False` until an appliance runtime exists.
- Add focused validation for the first controller behavior.
- Build and run the container image locally as its non-root user.

## Pending Validation

- Install the chart, apply the sample, check status, validate marker ownership and configuration data, restart the controller, update `spec.version`, then delete the resource and verify garbage collection. Garbage collection is a pending verification, not a proven behavior.

## Completed: Dev Deployment Validation

- Deploy release `0.4.0` through Argo CD in the approved `coriolis` namespace.
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

## Pending Decisions

- Define promotion.
- Define the exact CIXpress repository integration and trigger.
- Select a license.
