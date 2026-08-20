# Status

Current milestone: bootstrap/controller skeleton implemented and locally verified.

The Python 3.12 Kopf skeleton locally validates namespace-scoped reconciliation of an owned marker ConfigMap, bounded ConfigMap naming, server-side apply, truthful status conditions, and a non-root container image build. It has no finalizers and performs no destructive work.

Live-cluster validation is pending. The smoke test must run only in an isolated disposable Kubernetes cluster, never a shared or production context.

The chart configures logging, liveness, security context, and resource defaults; image publication is intentionally blocked by a non-deployable registry placeholder.

Open project details: Helm release behavior, CI, image registry, and license.
