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

Live-cluster smoke validation has not been run.

The future smoke test must use an isolated disposable Kubernetes cluster only. Never use a shared or production context. It must:

- Install the chart.
- Apply the sample.
- Wait for and check resource status.
- Validate marker ConfigMap ownership and configuration data.
- Restart the controller.
- Update `spec.version`.
- Delete the resource and verify garbage collection.

The local controller coverage includes:

- CRD structure and namespaced scope.
- Namespace-scoped watch configuration.
- Idempotent marker ConfigMap reconciliation.
- Truthful status-condition transitions and error reporting.
- Absence of finalizers and destructive behavior.

Do not treat future Helm, CI, registry, or licensing decisions as settled test assumptions.
