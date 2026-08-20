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

The helper slice is committed locally at `fbab6e5` on `dev`, but not pushed or deployed; the deployed marker `0.5.3` is unchanged and carries no standard labels. Collision pre-read/enforcement, legacy marker migration, retained-resource adoption, and all runtime resource construction remain deferred to the separate collision/migration API-layer slice.

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
