# CIXpress CI and Release Automation

## :material-book-open-page-variant-outline: Model

CIXpress is a Kubernetes-native pipeline orchestrator. Instead of running a pipeline inside one CI runner, it creates an ordered sequence of Kubernetes Jobs.

- A Template defines the ordered pipeline steps.
- Pipeline configuration supplies the repository and step parameters.
- A Job manifest defines the Kubernetes Job and image used by a step.
- The Conductor creates the pipeline, shared PVC, and Jobs.
- The Monitor watches Jobs and reports their status.
- The Frontend displays pipeline progress and step logs.
- Redis/Valkey stores runtime state and Monitor events.

Each pipeline has a six-character pipeline ID. Job names are `<step>-job-<pipeline-id>` and the shared workspace PVC is `pipeline-pvc-<pipeline-id>`. Steps run sequentially. A failed step stops later steps. CIXpress removes the PVC after either success or failure.

## :material-book-open-page-variant-outline: Standard Pipeline

`git-clone -> kaniko-build -> helm-update -> cleanup`

- `git-clone` clones the requested branch into the shared PVC and validates Helm version alignment.
- `kaniko-build` calculates the next version and pushes both the versioned image and `latest`.
- `helm-update` updates `helm/values.yaml`, `helm/Chart.yaml` `version` and `appVersion`, packages the chart, and pushes it as an OCI artifact.
- `cleanup` commits and pushes version changes, creates a Git tag for `main` or `dev`, optionally triggers documentation builds, and cleans the workspace.

## :material-book-open-page-variant-outline: Version Policy

The build derives its next version from `helm/values.yaml`. For `main`, `x.y.z` becomes `x.(y+1).0`. For `dev` and other branches, `x.y.z` becomes `x.y.(z+1)`. A `dev` build fails early when its Helm version trails the highest repository tag.

Chart version, application version, and image tag are synchronized at release `0.5.2`. The selected image repository is `cr.virtomat.io/virtomat/coriolis/operator`. CIXpress, not developers, owns future release-version edits.

## :material-book-open-page-variant-outline: Status and Observability

For approved dev-environment observation, follow the polling-only [CIXpress Pipeline Monitoring](cixpress-monitoring.md) procedure. It uses authorized Kubernetes exec and explicitly scoped, read-only API GETs; never use SSE or `/stream`. HTTP 202 only confirms acceptance, not success. A pipeline is successful only when every expected step has succeeded.

## :material-book-open-page-variant-outline: Troubleshooting

Start with the pipeline ID and identify the last step that changed state using [CIXpress Pipeline Monitoring](cixpress-monitoring.md). The procedure limits troubleshooting to safe polling, metadata-first log inspection, and a read-only Job fallback. Do not infer an operator namespace or use `cixpress` for operator deployment.

## :material-book-open-page-variant-outline: Outputs and Boundaries

A successful pipeline produces a versioned image and `latest`, an OCI chart at the same version, updated `helm/values.yaml` and `helm/Chart.yaml`, a source commit containing the version bump, a Git tag for `main` or `dev`, all four steps in `SUCCEEDED`, and removal of the temporary PVC. Publication is not deployment; deployment remains a separate GitOps or promotion action.

CIXpress is experimental and non-transactional: artifacts published by earlier steps are not rolled back if a later step fails. CI image push and OCI chart publication are validated through release `0.5.2`; pipeline `5ly5kg` completed all expected steps as `SUCCEEDED`. Argo image pull and deployment are also validated for `0.5.2`. CIXpress configuration and manifests are not yet in this repository. Promotion policy, exact integration artifacts/templates/triggers/credentials, and Argo CRD pre-upgrade automation remain separate work.
