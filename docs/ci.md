# CIXpress CI and Release Automation

## Model

CIXpress is a Kubernetes-native pipeline orchestrator. Instead of running a pipeline inside one CI runner, it creates an ordered sequence of Kubernetes Jobs.

- A Template defines the ordered pipeline steps.
- Pipeline configuration supplies the repository and step parameters.
- A Job manifest defines the Kubernetes Job and image used by a step.
- The Conductor creates the pipeline, shared PVC, and Jobs.
- The Monitor watches Jobs and reports their status.
- The Frontend displays pipeline progress and step logs.
- Redis/Valkey stores runtime state and Monitor events.

Each pipeline has a six-character pipeline ID. Job names are `<step>-job-<pipeline-id>` and the shared workspace PVC is `pipeline-pvc-<pipeline-id>`. Steps run sequentially. A failed step stops later steps. CIXpress removes the PVC after either success or failure.

## Standard Pipeline

`git-clone -> kaniko-build -> helm-update -> cleanup`

- `git-clone` clones the requested branch into the shared PVC and validates Helm version alignment.
- `kaniko-build` calculates the next version and pushes both the versioned image and `latest`.
- `helm-update` updates `helm/values.yaml`, `helm/Chart.yaml` `version` and `appVersion`, packages the chart, and pushes it as an OCI artifact.
- `cleanup` commits and pushes version changes, creates a Git tag for `main` or `dev`, optionally triggers documentation builds, and cleans the workspace.

## Version Policy

The build derives its next version from `helm/values.yaml`. For `main`, `x.y.z` becomes `x.(y+1).0`. For `dev` and other branches, `x.y.z` becomes `x.y.(z+1)`. A `dev` build fails early when its Helm version trails the highest repository tag.

Chart version, application version, and image tag are synchronized. CIXpress, not developers, owns these release-version edits. Do not manually bump the existing `0.1.0` values.

## Status and Observability

Pipeline status transitions are `NOT_STARTED -> STARTED -> SUCCEEDED/FAILED`. HTTP 202 only confirms acceptance; it is not success. A pipeline is successful only when all steps have succeeded.

- `GET /pipelines`
- `GET /pipelines/<pipeline-id>`
- `GET /pipelines/<pipeline-id>/logs?step=<step>&offset=0`
- `GET /stream`

`GET /stream` uses best-effort SSE. Refresh pipeline state rather than relying on the stream as authoritative.

## Troubleshooting

Start with the pipeline ID and identify the last step that changed state. Use only the actual operational context and namespace supplied for the environment.

```sh
kubectl --context <context> -n <namespace> get jobs,pods,pvc
kubectl --context <context> -n <namespace> describe job <step>-job-<pipeline-id>
kubectl --context <context> -n <namespace> logs job/<step>-job-<pipeline-id>
```

Use this diagnostic order:

1. Check pipeline and step status in the Frontend or API.
2. If no Job exists, inspect Conductor logs for Template, configuration, Job manifest, PVC, or RBAC errors.
3. If a Pod is Pending, inspect events for image-pull, Secret, scheduling, or PVC problems.
4. If a Job failed, inspect its container logs.
5. If Kubernetes shows progress but the Frontend does not, inspect the Monitor pod, backend connectivity, and Conductor logs.
6. If cleanup fails, inspect Git authentication and branch divergence; earlier image or chart publication might already have succeeded.

Common failures include missing manifests, unavailable Job images, registry authentication, invalid Helm metadata, Git conflicts, missing Secrets, and insufficient Kubernetes permissions.

## Outputs and Boundaries

A successful pipeline produces a versioned image and `latest`, an OCI chart at the same version, updated `helm/values.yaml` and `helm/Chart.yaml`, a source commit containing the version bump, a Git tag for `main` or `dev`, all four steps in `SUCCEEDED`, and removal of the temporary PVC. Publication is not deployment; deployment remains a separate GitOps or promotion action.

CIXpress is experimental and non-transactional: artifacts published by earlier steps are not rolled back if a later step fails. CIXpress configuration and manifests are not yet in this repository. OCI destination and promotion, trigger and monitoring credentials, registry ownership, and deployment/GitOps remain separate integration work. Argo CRD pre-upgrade automation belongs to promotion/deployment and is not part of the standard build pipeline.
