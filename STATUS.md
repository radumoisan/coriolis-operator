# Status

Current milestone: release `0.4.0` is published by CIXpress and deployed successfully through Argo CD in the `coriolis` namespace.

The Python 3.12 Kopf skeleton reconciles a namespace-scoped owned marker ConfigMap with collision-safe naming and server-side apply. Successful reconciliation sets status; `Ready=False` remains until an appliance runtime exists. Kubernetes API failures propagate without a custom failure condition. It has no finalizers and performs no destructive work.

Live-cluster installation validation is complete in `infra-dev-buc-hq` using context `virt-infra-dev-buc-hq`. CIXpress observation in `cixpress` remains read-only. The approved operator namespace is `coriolis`; full appliance reconciliation and lifecycle smoke validation remain pending.

The chart configures logging, liveness, security context, resource defaults, and the `regcred` image pull secret. Release `0.4.0` uses `cr.virtomat.io/virtomat/coriolis/operator`. Image publication and pull, OCI chart publication, CRD installation, operator startup, liveness, and Argo deployment are validated.

CIXpress CI/release behavior is documented and the integration runs externally, but its exact configuration and manifests are not stored in this repository. Open project details: full appliance lifecycle validation, promotion policy, exact CIXpress integration files/trigger and monitoring credentials, Argo CRD pre-upgrade automation, and license.

The CIXpress build for commit `b824f5c` correlated to pipeline `hlzfy3` (template `Default`) and failed: top-level state `FAILED`, started `2026-08-20T09:23:41+00:00`, completed `2026-08-20T09:23:56Z`. Detail steps were empty, so per-step confirmation is unavailable. Log metadata remained available without exposing content: `git-clone` returned HTTP 200 with one stream/25 lines and `kaniko-build` returned HTTP 200 with one stream/12 lines; `helm-update` and `cleanup` returned HTTP 404. The confirmed root cause is Kaniko DNS failure resolving `example.invalid` during push-permission checking.

The dummy trigger for commit `c9d9dd5ec3ecfe06f03e0cbfc1eda3ff4b0fd58d` ran as pipeline `jcr0vn` using template `Default`, started `2026-08-20T11:18:10+00:00`, and completed `2026-08-20T11:19:27+00:00`. The top-level state and all expected steps (`git-clone`, `kaniko-build`, `helm-update`, and `cleanup`) were `SUCCEEDED`. It generated release commit and tag `0.2.0` at `49cb5dc7dbe247e432e604db19078ecf1c2b5437`.

Commit `2579c6a44bb8fd3b04c4c3f37f4096fba8f2777e` configured the `regcred` chart default. Pipeline `0kvajx` completed with all expected steps `SUCCEEDED` and generated release `0.3.0` at `61b6bb334ccb50aff51228c3d1428a9f284eaff8`. Its Argo deployment confirmed registry authentication, then exposed an invalid CRD schema and a chart command that bypassed the image virtual environment.

Commit `5a8cc443ec00a81baec7ac6d14de1ca40cd1d736` corrected both deployment defects. Pipeline `gepx3l` ended with all expected steps `SUCCEEDED`, although an intermediate `INPROGRESS` response already contained a completion timestamp and therefore remains inconsistent monitoring evidence. The generated release commit and tag `0.4.0` resolve to `4eee8a9f24eb05640c61ece8fa057ecd49136e85`. Argo CD synchronized `0.4.0` successfully; the application is `Healthy`, the CRD is established, and the operator Deployment is `1/1` available with a ready pod and zero restarts.
