# Architecture Decisions

Decision records are append-only. New decisions use the next numeric identifier and describe context, decision, and consequences.

Existing records establish the implementation direction for the initial controller.

Decision 0005 accepts CIXpress as CI/release automation while retaining deployment/GitOps as a separate concern. Open decisions cover OCI destination and promotion, exact CIXpress repository integration, Argo CRD pre-upgrade automation, image registry ownership, and licensing.
