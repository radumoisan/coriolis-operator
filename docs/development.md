# Development

Use Python 3.12 and uv for local development. Implement the operator with Kopf.

Keep changes minimal and preserve the current appliance runtime shape. Treat existing Coriolis component repositories and images as immutable upstream inputs.

Place Helm chart content in `helm/` and CRDs in `helm/crds/`. Apply CRD updates separately before Helm chart upgrades.

Future CI, not local manual updates, owns synchronization of chart version, application version, and image tag.
