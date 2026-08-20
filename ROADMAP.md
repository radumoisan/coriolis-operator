# Roadmap

## Milestone 1: Bootstrap and Controller Skeleton (Implemented Locally)

- Establish the Python 3.12, uv, and Kopf operator skeleton.
- Define and ship the namespaced `CoriolisAppliance` API.
- Watch the Helm release namespace.
- Reconcile only an idempotent marker ConfigMap and standard successful status conditions.

Define and approve a dedicated dev operator namespace before live validation or deployment. Live-cluster validation remains pending.

## Later Milestones

- Model appliance runtime configuration while preserving its current shape.
- Add safe lifecycle capabilities after explicit contracts and operational review.
- Configure and validate the documented CIXpress CI/release pipeline.

## Pending Decisions

- OCI publication and promotion.
- Exact CIXpress repository configuration, templates, Job manifests, trigger, and monitoring credentials.
- Argo CRD pre-upgrade automation as part of promotion/deployment, not the standard build pipeline.
- Validate publication and authentication for the provisional `cr.virtomat.io/virtomat/coriolis/operator` repository.
- License selection.
