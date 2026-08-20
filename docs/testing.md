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

The approved dev cluster is `infra-dev-buc-hq` (`virt-infra-dev-buc-hq`) for read-only pipeline troubleshooting and monitoring only. Operator deployment and live validation remain forbidden until a dedicated operator namespace is defined and approved. See [Development Environment](dev-environment.md).

After that approval, the future smoke test must:

- Install the chart.
- Apply the sample.
- Wait for and check resource status.
- Validate marker ConfigMap ownership and configuration data.
- Restart the controller.
- Update `spec.version`.
- Delete the resource and verify garbage collection; this remains a pending verification, not a proven behavior.

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
