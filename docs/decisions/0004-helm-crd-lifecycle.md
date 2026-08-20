# 0004: Manage CRD Lifecycle Explicitly

## Context

The chart includes the `CoriolisAppliance` CRD, whose lifecycle must remain predictable during upgrades and removal.

## Decision

Store CRDs in `helm/crds/`. Helm installs them on a fresh installation but does not manage their upgrades or deletions from that directory. Explicitly apply CRD changes before chart upgrades.

## Consequences

Chart upgrade procedures must include an explicit CRD-application step when CRDs change. CRD removal requires a separate, deliberate operation.
