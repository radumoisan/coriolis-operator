# Development

Use Python 3.12 and uv for local development. Implement the operator with Kopf.

Keep changes minimal and preserve the current appliance runtime shape. Treat existing Coriolis component repositories and images as immutable upstream inputs.

Place Helm chart content in `helm/` and CRDs in `helm/crds/`. Apply CRD updates separately before Helm chart upgrades.

CIXpress, not local manual updates, owns synchronization of chart version, application version, and image tag. See [CI and release automation](ci.md). Do not manually change those release values unless explicitly requested.

Exact CIXpress configuration, Template and Job manifests, trigger, and credentials are not present in this repository. Do not infer them or store credentials here. Argo CRD pre-upgrade work belongs to promotion/deployment, not the standard CIXpress build pipeline.

For approved dev-cluster work, including read-only CIXpress observation and operator deployment or live validation in the dedicated `coriolis` namespace, see [Development Environment](dev-environment.md).

## Local Validation

Run these supported local commands from the repository root:

```sh
uv sync --frozen
make format-check
make lint
make typecheck
make test
helm lint helm/
helm template coriolis-operator helm/ --include-crds
docker build -t coriolis-operator:local .
```
