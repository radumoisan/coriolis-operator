# Status

Current milestone: bootstrap/controller skeleton and consistency sweep implemented and locally verified.

The Python 3.12 Kopf skeleton reconciles a namespace-scoped owned marker ConfigMap with collision-safe naming and server-side apply. Successful reconciliation sets status; `Ready=False` remains until an appliance runtime exists. Kubernetes API failures propagate without a custom failure condition. It has no finalizers and performs no destructive work.

Live-cluster validation is pending. The smoke test must run only in an isolated disposable Kubernetes cluster, never a shared or production context.

The chart configures logging, liveness, security context, and resource defaults; image publication is intentionally blocked by a non-deployable registry placeholder.

CIXpress CI/release behavior is documented but not configured or implemented in this repository. Open project details: OCI destination and promotion, exact CIXpress integration files/trigger and monitoring credentials, Argo CRD pre-upgrade automation in promotion/deployment, image registry ownership, and license.
