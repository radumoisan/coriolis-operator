# Coriolis Operator

Kubernetes-native management for a Coriolis Appliance while retaining the current appliance runtime shape.

The operator is built with Python 3.12, uv, and Kopf. Existing Coriolis component repositories and images are immutable upstream inputs; this project orchestrates them rather than modifying or rebuilding them.

`CoriolisAppliance` is a namespaced API at `coriolis.cloudbase.it/v1alpha1`.

The bootstrap/controller skeleton is implemented locally. Live-cluster smoke validation remains pending.

## Layout

- `helm/`: Helm chart, including CRDs in `helm/crds/`.
- `docs/`: architecture, contracts, development guidance, and decisions.
- `docs/ci.md`: CIXpress CI and release model.

## Open Details

CIXpress is the known CI/release automation model and owns chart version, application version, and image-tag synchronization. OCI destination and promotion, the exact CIXpress integration files and trigger, Argo CRD pre-upgrade automation, registry ownership, and licensing remain open.
