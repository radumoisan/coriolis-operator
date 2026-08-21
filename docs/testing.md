# Testing

Validate the work relevant to each change.

For the controller skeleton, these local validations have passed:

- Python 3.12 Ruff format.
- Ruff lint.
- mypy.
- Unit tests.
- Helm lint.
- Helm template with CRDs.
- Container image build and non-root runtime identity.
- `git diff --check`.

For the local API-only `core` runtime slice, these local validations passed:

- 25 unit tests covering the CRD schema (profile enum/default, required non-empty `spec.version`, `status.acceptedVersion`, and the absence of CEL immutability rules), controller enforcement of the immutable accepted version, rejection and version-change-blocked conditions, the accepted API-only reconcile, profile defaulting and profile-change routing, and no-resource paths.
- Ruff lint.
- mypy.
- Helm lint and Helm template with CRDs.
- `git diff --check`.

This API slice is committed locally at `ab9df83` (branch `dev`, not pushed/deployed) and is absent from the deployed operator. No cluster or external service was changed by this API slice; the image mirror/pull gate remains passed, and no dependencies, bootstrap Jobs, services, storage, secrets, or Coriolis runtime workloads are implemented or deployed.

For the local metadata-only helper slice, these local validations passed:

- 44 unit tests covering `appliance_resource_name` (single lowercase DNS label <=63; dotted/overflow dot-to-hyphen prefix plus 12-character SHA-256; invalid appliance/component rejection), `appliance_identity`, `build_resource_metadata` (standard `app.kubernetes.io/*` and `coriolis.cloudbase.it/*` labels, full appliance-name annotation, exactly one of owner reference or retention), and `build_state_config_map` (standard metadata with component `operator-state` while retaining the shipped `state_config_map_name`).
- `uv run ruff check .` and `uv run ruff format --check .`.
- `uv run mypy src`.
- `git diff --check`.

The helper slice is committed locally at `fbab6e5` on `dev`, but not pushed or deployed; the deployed marker `0.5.3` is unchanged and carries no standard labels. The collision/migration marker API-layer slice is described below; retained-resource adoption and all runtime resource construction remain deferred.

For the local collision/migration marker API-layer slice, these local validations passed:

- 70 unit tests covering the pre-read classification (404 create; fully matching managed marker proceeds with unchanged body; compatible legacy `0.5.2`/`0.5.3` marker normalization in place with stale generation updated, including dotted/long names; `ResourceCollision` for partial/conflicting standard metadata, owner mismatch, incompatible legacy data, and owner-plus-retention metadata, never patching/adopting/deleting/renaming; preservation of a prior `acceptedVersion` and condition transition time), non-404 read error propagation without patching, `V1ConfigMap` object handling, and deterministic `ResourceCollision` conditions.
- `uv run ruff check .` and `uv run ruff format --check .`.
- `uv run mypy src`.
- `helm lint helm/` and `helm template coriolis-operator helm/ --include-crds`.
- `git diff --check`.

This slice is **committed locally at `d8df00f` on `dev`, but not pushed or deployed**; the deployed marker `0.5.3` is unchanged and lacks these pre-read/collision semantics. ConfigMap RBAC gains only `get`.

For the local retained-resource authorization/classification slice, these local validations passed:

- 94 unit tests (24 new for this slice) covering the pure `classify_retained_resource` classifier returning `RetainedClassification.ABSENT/REUSE/COLLISION`: absent resource eligible for creation; exact matching PVC/state Secret/CA-state reuse; changed creating-appliance UID with otherwise exact retained identity is `REUSE` (UID is deliberately ignored; a stale `coriolis.cloudbase.it/appliance-uid` annotation is treated as unrelated); name/namespace/appliance/component/retention mismatches collide; missing/partial labels and annotations collide; any owner reference collides (even a matching owner UID); unrelated extra labels/annotations are allowed; the external `coriolis-appliance-registry` Secret fails closed as `COLLISION` both when absent and when forged with exact matching metadata; mapping-shaped dict and real `V1Secret`/`V1PersistentVolumeClaim` model representations; and no input mutation.
- `uv run ruff check .` and `uv run ruff format --check .`.
- `uv run mypy src`.
- `helm lint helm/` and `helm template coriolis-operator helm/ --include-crds`.
- `git diff --check`.

This slice is **committed locally at `1b73045` on `dev`, but not pushed or deployed**; the deployed marker `0.5.3` is unchanged. It constructs/reconciles/patch/reads/adopts no runtime resource and adds no adoption mutations; external/pre-existing resources such as `coriolis-appliance-registry` fail closed as `COLLISION` and remain read-only and outside this classifier/reconciliation policy. A MariaDB vertical slice remains blocked by the remaining generators/builders/RBAC, storage, probes/readiness, and rotation gates.

For the local documentation-only Secret/configuration contract slice, these validations passed:

- No code, builders, values, RBAC, CRD, or runtime behavior changed; the change is confined to [docs/foundational-resource-contract.md](foundational-resource-contract.md) and the tracking/docs pages.
- The modified contract page was reviewed for contradictory claims: concrete Secret/ConfigMap names and key layouts and the primary `coriolis.conf` split are stated as frozen, not unresolved, and the remaining genuinely-unresolved items are listed under the contract's Remaining Unresolved Secret Items and Unresolved Gates.
- `uv run pytest`; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; `git diff --check`.

This slice is **committed locally at `8ce26ba` on `dev`, but not pushed or deployed**; the deployed `0.5.3` remains marker-only and unchanged, and no runtime resources or adoption mutations exist.

## :material-book-open-page-variant-outline: Pure Secret/ConfigMap Builder Slice

- 116 total tests, including 22 new builder cases from the previous 94; 21 cases matched the final focused selector. Coverage includes deterministic names/standard metadata; ownerless retained credential Secrets with retention metadata; owner-referenced rebuildable configuration resources without retention; exact key sets; opaque caller-provided string inputs without mutation; missing, extra, and non-string input failures without value exposure; `Opaque` UTF-8/base64 Secret `data` with no `stringData`; and plain six-file ConfigMap data that excludes `coriolis.conf` and credentials.
- `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; and `git diff --check` passed.

The five pure builders (`build_coriolis_credentials_secret`, `build_infrastructure_credentials_secret`, `build_step_ca_credentials_secret`, `build_coriolis_config_map`, and `build_coriolis_config_secret`) are **committed locally at `050f16e` on `dev`, but not pushed or deployed**. This covers manifest construction only: no credential generation, `main.py` reconciliation, Kubernetes reads/SSA, RBAC, CRD, runtime resources, status/readiness, or deployment changed; deployed `0.5.3` remains marker-only.

## :material-book-open-page-variant-outline: Pure Retained Credential Generation Slice

- 132 total tests, including 16 new cases from the previous 116, plus focused tests. Coverage includes independent generation of all seven frozen keys through `secrets.token_urlsafe(32)` (32 random bytes/256 bits, URL-safe opaque strings), deterministic token-factory injection for tests only, invalid empty/non-string factory outputs failing without value exposure, unchanged composition with existing builders, and no credential values in failures.
- `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; and `git diff --check` passed.

The pure helpers `generate_coriolis_credentials`, `generate_infrastructure_credentials`, and `generate_step_ca_credentials` are **committed locally at `a604579` on `dev`, but not pushed or deployed**. They are not called by `main.py`; no controller reconciliation, Kubernetes reads/writes/SSA, RBAC, CRD, runtime resources, status/readiness, chart/release, deployment, or rotation changed, and deployed `0.5.3` remains marker-only. The frozen policy is operator-generated only, with no inline CR credential values or external credential Secret source. Runtime generate-once/reuse remains pending: generate only for `ABSENT`, reuse exact matching ownerless retained Secrets unchanged, fail closed on collisions, and defer rotation.

## :material-book-open-page-variant-outline: Retained Secret Semantic Validation/Extraction Slice

- 152 total tests, including 20 new cases from the previous 132, plus focused 20 tests. Coverage includes mapping-shaped objects and Kubernetes `V1Secret` models; optional non-conflicting `apiVersion`/`kind`; required `Opaque` type; rejection of persisted `stringData`; exact frozen `data` keys with string encoded values; strict base64 then UTF-8 decoding; empty decoded-value rejection; a new decoded mapping without input mutation; and fixed/category-only failures without value exposure.
- `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; and `git diff --check` passed.

`validated_retained_secret_values` is **committed locally at `5165629` on `dev`, but not pushed or deployed**. It validates semantics only: no metadata classification, Kubernetes reads/writes, generation, SSA, collision/status handling, or reconciliation. No `main.py`, RBAC, CRD, runtime resource, chart/release, deployment, or rotation behavior changed; deployed `0.5.3` remains marker-only. Future preflight must classify metadata first and map semantic failure fail-closed to `COLLISION`; decoded values remain internal and are never logged, statused, or evented.

Live-cluster controller lifecycle validation passed for release `0.5.2` in the approved `coriolis` namespace. The currently deployed `0.5.3` retains the marker-only controller behavior and predates this local API slice; full lifecycle validation was not repeated for `0.5.3`.

The approved dev cluster is `infra-dev-buc-hq` (`virt-infra-dev-buc-hq`); CIXpress remains approved for read-only pipeline troubleshooting and monitoring only. The dedicated operator namespace is `coriolis`. See [Development Environment](dev-environment.md).

The completed `0.5.2` lifecycle validation covered:

- Apply the sample and check status, ownership, and marker configuration data.
- Replace the controller pod and verify reconciliation resumes without changing ownership, marker uniqueness, or condition transition times.
- Update `spec.version` and verify generation, observed generation, status, and marker data.
- Delete the resource and verify normal garbage collection.

The local controller coverage includes:

- CRD structure and namespaced scope.
- Namespace-scoped watch configuration.
- Idempotent marker ConfigMap reconciliation.
- Successful reconciliation status.
- `Ready=False` until an appliance runtime exists.
- Kubernetes API failures propagate without a custom failure condition.
- Absence of finalizers and destructive behavior.

CIXpress CI behavior is documented but not configured in this repository. Pending integration validation includes receiving the exact pipeline configuration, Template, and Job manifests; validating version alignment and the dev tag check; and defining trigger and monitoring credentials without storing secrets. Argo CRD pre-upgrade automation must be validated as promotion/deployment work, not assumed to be handled by the standard build pipeline.

Do not treat registry publication/authentication validation, promotion, or licensing decisions as settled test assumptions.
