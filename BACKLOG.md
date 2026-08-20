# Backlog

## Completed Locally: Bootstrap and Controller Skeleton

- Create the Python 3.12, uv, and Kopf project skeleton.
- Add the namespaced `CoriolisAppliance` CRD at `coriolis.cloudbase.it/v1alpha1` under `helm/crds/`.
- Implement namespace-scoped watching of the Helm release namespace.
- Reconcile an idempotent marker ConfigMap only.
- Publish truthful status conditions.
- Add focused validation for the first controller behavior.
- Build and run the container image locally as its non-root user.

## Pending Validation

- Run the controller smoke test in an isolated disposable Kubernetes cluster only; never use a shared or production context.
- Install the chart, apply the sample, check status, validate marker ownership and configuration data, restart the controller, update `spec.version`, then delete the resource and verify garbage collection.

## Pending Decisions

- Define Helm release and upgrade conventions.
- Define CI ownership of chart/app version and image-tag synchronization.
- Select an image registry and publication workflow.
- Select a license.
