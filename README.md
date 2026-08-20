# Coriolis Operator

Kubernetes-native management for a Coriolis Appliance while retaining the current appliance runtime shape.

The operator is built with Python 3.12, uv, and Kopf. Existing Coriolis component repositories and images are immutable upstream inputs; this project orchestrates them rather than modifying or rebuilding them.

`CoriolisAppliance` is a namespaced API at `coriolis.cloudbase.it/v1alpha1`.

The bootstrap/controller skeleton is implemented locally. Live-cluster smoke validation remains pending.

## Layout

- `helm/`: Helm chart, including CRDs in `helm/crds/`.
- `docs/`: architecture, contracts, development guidance, and decisions.
- `docs/ci.md`: CIXpress CI and release model.
- `docs/cixpress-monitoring.md`: approved polling-only CIXpress pipeline monitoring and troubleshooting procedure.
- `docs/dev-environment.md`: approved dev-environment access, CI observation, and safety boundary.

## Open Details

CIXpress is the known CI/release automation model and owns future chart version, application version, and image-tag synchronization. The provisional `0.0.0` image repository is `cr.virtomat.io/virtomat/coriolis/operator`; publication/authentication validation and promotion, the exact CIXpress integration files and trigger, Argo CRD pre-upgrade automation, and licensing remain open.
