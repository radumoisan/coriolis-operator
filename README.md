# Coriolis Operator

Kubernetes-native management for a Coriolis Appliance while retaining the current appliance runtime shape.

The operator is built with Python 3.12, uv, and Kopf. Existing Coriolis component repositories and images are immutable upstream inputs; this project orchestrates them rather than modifying or rebuilding them.

`CoriolisAppliance` is a namespaced API at `coriolis.cloudbase.it/v1alpha1`.

The bootstrap/controller skeleton is implemented; controller lifecycle validation passed on release `0.5.2` in the approved `coriolis` namespace, and the currently deployed release is `0.5.3`. The deployed `0.5.3` retains the marker-only controller behavior (it predates the local API slice). `Ready=False/RuntimeNotImplemented` is expected until the recorded Kubernetes-native core runtime is implemented.

On 2026-08-20 the local API-only `core` runtime slice was implemented and validated: the `v1alpha1` CRD defines optional/defaulted `spec.profile: core` (enum only `core`), required non-empty `spec.version`, and optional non-empty `status.acceptedVersion`. The controller enforces the immutable accepted version from the persisted `status.acceptedVersion` (no CEL/admission rules), supports initial exact `2603.4` with omitted profile defaulting to `core`, and otherwise applies no resources and reports rejection or `Upgradeable=False/UpgradeBlocked` conditions. A valid API-only reconcile records only the owned controller-state ConfigMap and reports all six conditions, with `Ready=False/RuntimeNotImplemented` remaining truthful. 25 tests, Ruff, mypy, and Helm lint/template pass; no cluster or external service was changed and no runtime workloads are implemented or deployed. This API slice is committed locally at `ab9df83` but is **not** pushed or deployed and is **not** included in the deployed `0.5.3`.

## Layout

- `helm/`: Helm chart, including CRDs in `helm/crds/`.
- `docs/`: architecture, contracts, development guidance, and decisions.
- `docs/foundational-resource-contract.md`: foundational Kubernetes resource contracts (naming, labels, ownership/retention, Secrets, dependency order) for the `core` profile.
- `docs/ci.md`: CIXpress CI and release model.
- `docs/cixpress-monitoring.md`: approved polling-only CIXpress pipeline monitoring and troubleshooting procedure.
- `docs/dev-environment.md`: approved dev-environment access, CI observation, and safety boundary.

## Open Details

CIXpress is the known CI/release automation model and owns future chart version, application version, and image-tag synchronization. The image repository is `cr.virtomat.io/virtomat/coriolis/operator`; publication, authentication, and deployment are validated, and the currently deployed release is `0.5.3`. The Kubernetes-native `core` runtime contract is recorded; RC4 is blocked/OVA-only and the approved fallback is exact official release `2603.4`. All 26 approved images were mirrored to `cr.virtomat.io/virtomat/coriolis` on 2026-08-20 with preserved and verified manifest digests, and Kubernetes pull validation in `virt-infra-dev-buc-hq` namespace `coriolis` passed for all 21 initial-runtime image references with no residual validation Pods. The image inventory and pull gate are complete and the local `core` runtime API slice is implemented (committed locally at `ab9df83`, not pushed/deployed; the deployed `0.5.3` remains marker-only). The Milestone 4 foundational resource contract is documented in [docs/foundational-resource-contract.md](docs/foundational-resource-contract.md), and a local metadata-only helper slice (`appliance_resource_name`, `appliance_identity`, `build_resource_metadata`) is implemented and validated (44 unit tests, `mypy src`, Ruff) but uncommitted and unpushed; the next step is the separate collision/migration API-layer slice. No core runtime workloads have been implemented or deployed yet. Promotion, exact CIXpress configuration/templates/triggers/credentials, Argo CRD pre-upgrade automation, and licensing as applicable remain open.
