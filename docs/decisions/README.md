# Architecture Decisions

Decision records are append-only. New decisions use the next numeric identifier and describe context, decision, and consequences.

Existing records establish the implementation direction for the initial controller.

Decision 0005 accepts CIXpress as CI/release automation while retaining deployment/GitOps as a separate concern. `cr.virtomat.io/virtomat/coriolis/operator` is a provisional repository starting point, not a permanent ADR. Open decisions cover publication/authentication validation and promotion, exact CIXpress repository integration, Argo CRD pre-upgrade automation, and licensing.
