# Roadmap

## Milestone 1: Bootstrap and Controller Skeleton

- Establish the Python 3.12, uv, and Kopf operator skeleton.
- Define and ship the namespaced `CoriolisAppliance` API.
- Watch the Helm release namespace.
- Reconcile only an idempotent marker ConfigMap and truthful status conditions.

## Later Milestones

- Model appliance runtime configuration while preserving its current shape.
- Add safe lifecycle capabilities after explicit contracts and operational review.
- Automate validation, chart releases, and image publication through CI.

## Pending Decisions

- Helm release and chart operational details.
- CI implementation and release policy.
- Image registry ownership and publication flow.
- License selection.
