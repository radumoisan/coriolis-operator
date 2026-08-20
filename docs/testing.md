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

Live-cluster controller lifecycle validation passed for release `0.5.2` in the approved `coriolis` namespace.

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
