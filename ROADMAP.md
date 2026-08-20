# Roadmap

## Milestone 1: Bootstrap and Controller Skeleton (Implemented Locally)

- Establish the Python 3.12, uv, and Kopf operator skeleton.
- Define and ship the namespaced `CoriolisAppliance` API.
- Watch the Helm release namespace.
- Reconcile only an idempotent marker ConfigMap and standard successful status conditions.

Disposable-cluster smoke validation remains pending.

## Later Milestones

- Model appliance runtime configuration while preserving its current shape.
- Add safe lifecycle capabilities after explicit contracts and operational review.
- Configure and validate the documented CIXpress CI/release pipeline.

## Pending Decisions

- OCI publication and promotion.
- Exact CIXpress repository configuration, templates, Job manifests, trigger, and monitoring credentials.
- Argo CRD pre-upgrade automation as part of promotion/deployment, not the standard build pipeline.
- Image registry ownership and publication flow.
- License selection.
