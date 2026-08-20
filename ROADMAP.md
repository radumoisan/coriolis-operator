# Roadmap

## Milestone 1: Bootstrap and Controller Skeleton (Completed)

- Establish the Python 3.12, uv, and Kopf operator skeleton.
- Define and ship the namespaced `CoriolisAppliance` API.
- Watch the Helm release namespace.
- Reconcile only an idempotent marker ConfigMap and standard successful status conditions.
- Publish and deploy release `0.5.2` in the approved `coriolis` namespace.
- Validate create, controller replacement, update, deletion, and marker garbage collection.

## Milestone 2: RC4 Image And Runtime Inventory (Active)

- Use appliance Jenkins job `1_coriolis-appliance-setup` Build `868` as the initial `2608.0-rc4` provenance source.
- Inventory the complete application and support image set before implementation.
- Validate registry metadata before pulls; record immutable digests, platforms, runtime configuration, health capabilities, and compatibility.
- Create a dedicated pull Secret securely from Jenkins credential ID `docker-appliance-creds` without storing or displaying credential values.
- Stop if the release is OVA-only or lacks a complete compatible Kubernetes image set.

## Later Milestones

- Define the `core` CRD/runtime API from the approved inventory.
- Implement dependencies, bootstrap Jobs, Coriolis workloads, controller watches, status, readiness, tests, and development acceptance.
- Add safe upgrade and production lifecycle capabilities after explicit contracts and operational review.

## Pending Decisions

- OCI publication and promotion.
- Exact CIXpress repository configuration, templates, Job manifests, trigger, and monitoring credentials.
- Argo CRD pre-upgrade automation as part of promotion/deployment, not the standard build pipeline.
- License selection.
