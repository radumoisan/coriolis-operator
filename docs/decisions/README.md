# Architecture Decisions

Decision records are append-only. New decisions use the next numeric identifier and describe context, decision, and consequences.

Existing records establish the implementation direction for the initial controller.

Decision 0005 accepts CIXpress as CI/release automation while retaining deployment/GitOps as a separate concern. Decision 0006 freezes ingress-nginx as the short-term public-exposure model and keeps TLS Secret material external to the operator. `cr.virtomat.io/virtomat/coriolis/operator` is a provisional repository starting point, not a permanent ADR. Open decisions cover publication/authentication validation and promotion, exact CIXpress repository integration, Argo CRD pre-upgrade automation, and licensing.

Decision 0008 freezes Milestone 9's operator-managed per-`CoriolisAppliance` Barbican (pinned API and worker images only, no listener or PVC, `simple_crypto` with MariaDB-persisted state and a retained ownerless `<appliance>-barbican-credentials` Secret) plus the actual-browser two-endpoint `secret_ref` creation/validation/listing/selection/removal contract with no migration. The architecture is accepted and implemented locally with the local gates passed; release and the live actual-browser qualification remain pending and are not claimed.
