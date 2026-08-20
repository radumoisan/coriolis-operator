# Status

Current milestone: bootstrap/controller skeleton and consistency sweep implemented and locally verified.

The Python 3.12 Kopf skeleton reconciles a namespace-scoped owned marker ConfigMap with collision-safe naming and server-side apply. Successful reconciliation sets status; `Ready=False` remains until an appliance runtime exists. Kubernetes API failures propagate without a custom failure condition. It has no finalizers and performs no destructive work.

Live-cluster validation is pending. The known dev monitoring environment is `infra-dev-buc-hq` using context `virt-infra-dev-buc-hq`; CIXpress observation in `cixpress` is read-only. The operator namespace is TBD and must be approved before deployment or live validation.

The chart configures logging, liveness, security context, and resource defaults. Its provisional `0.0.0` baseline uses `cr.virtomat.io/virtomat/coriolis/operator`; registry credentials and publication still require validation.

CIXpress CI/release behavior is documented and the integration runs externally, but its exact configuration and manifests are not stored in this repository. Open project details: OCI publication/authentication validation and promotion, exact CIXpress integration files/trigger and monitoring credentials, Argo CRD pre-upgrade automation in promotion/deployment, and license.

The CIXpress build for commit `b824f5c` correlated to pipeline `hlzfy3` (template `Default`) and failed: top-level state `FAILED`, started `2026-08-20T09:23:41+00:00`, completed `2026-08-20T09:23:56Z`. Detail steps were empty, so per-step confirmation is unavailable. Log metadata remained available without exposing content: `git-clone` returned HTTP 200 with one stream/25 lines and `kaniko-build` returned HTTP 200 with one stream/12 lines; `helm-update` and `cleanup` returned HTTP 404. The confirmed root cause is Kaniko DNS failure resolving `example.invalid` during push-permission checking.
