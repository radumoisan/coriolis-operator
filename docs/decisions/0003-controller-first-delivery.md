# 0003: Deliver a Minimal Non-Destructive Controller First

## Context

The operator needs a safe initial delivery that proves the reconciliation contract.

## Decision

Define namespaced `CoriolisAppliance` at `coriolis.cloudbase.it/v1alpha1`. The first operator watches its Helm release namespace, owns only an idempotent marker ConfigMap, and reports truthful status conditions. It uses no finalizers and performs no destructive work.

CRDs live in `helm/crds/` and are updated separately before chart upgrades. Helm content lives in `helm/`.

## Consequences

Lifecycle expansion is deferred until explicit contracts and operational review. Future CI owns chart version, application version, and image-tag synchronization.
