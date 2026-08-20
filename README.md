# Coriolis Operator

Kubernetes-native management for a Coriolis Appliance while retaining the current appliance runtime shape.

The operator is built with Python 3.12, uv, and Kopf. Existing Coriolis component repositories and images are immutable upstream inputs; this project orchestrates them rather than modifying or rebuilding them.

`CoriolisAppliance` is a namespaced API at `coriolis.cloudbase.it/v1alpha1`.

The bootstrap/controller skeleton is implemented and release `0.5.2` controller lifecycle validation passed in the approved `coriolis` namespace. `Ready=False/RuntimeNotImplemented` is expected until the recorded Kubernetes-native core runtime is implemented.

## Layout

- `helm/`: Helm chart, including CRDs in `helm/crds/`.
- `docs/`: architecture, contracts, development guidance, and decisions.
- `docs/ci.md`: CIXpress CI and release model.
- `docs/cixpress-monitoring.md`: approved polling-only CIXpress pipeline monitoring and troubleshooting procedure.
- `docs/dev-environment.md`: approved dev-environment access, CI observation, and safety boundary.

## Open Details

CIXpress is the known CI/release automation model and owns future chart version, application version, and image-tag synchronization. Release `0.5.2` uses image repository `cr.virtomat.io/virtomat/coriolis/operator`; publication, authentication, and deployment are validated. The Kubernetes-native `core` runtime contract is recorded; its active first gate is the `2608.0-rc4` image and registry inventory, using appliance Jenkins Build `868` as the initial provenance source. Promotion, exact CIXpress configuration/templates/triggers/credentials, Argo CRD pre-upgrade automation, and licensing as applicable remain open.
