# 0005: Use CIXpress for CI and Release Automation

## Context

The project needs a defined CI and release model that owns synchronized chart, application, and image versions without conflating publication with deployment.

## Decision

Accept CIXpress as the CI and release automation model. Its ordered Kubernetes Job pipeline performs source validation, image publication, Helm metadata and chart publication, and release cleanup. Keep deployment and GitOps separate from CIXpress CI.

## Consequences

CIXpress owns release-version edits; developers do not manually change chart version, application version, or image tag for releases. CIXpress is experimental and non-transactional, so failed later steps do not roll back artifacts published earlier. The exact CIXpress repository configuration, Templates, and Job manifests remain future integration work. Argo CRD pre-upgrade automation also remains future work and must be integrated with promotion/deployment rather than assumed to be handled by the standard build pipeline.
