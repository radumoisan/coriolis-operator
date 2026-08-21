# Runtime Contract

Existing upstream Coriolis component repositories and images are immutable inputs. The operator does not rebuild or patch them. The deployed `0.5.3` controller remains marker-only, so `Ready=False/RuntimeNotImplemented` remains correct.

## :material-book-open-page-variant-outline: Completed Marker-Only Contract

Release `0.5.2` at `86552e46fd2fb13b05d66cc2b7e25f4968f00846` completed the initial controller lifecycle milestone. The namespace-scoped `coriolis.cloudbase.it/v1alpha1` `CoriolisAppliance` controller reconciled only an owned marker ConfigMap and status. `Ready=False/RuntimeNotImplemented` was expected; normal deletion garbage-collected the marker, and no sample CR or marker remains.

That limited, non-destructive contract is completed history. It is not the target runtime behavior.

## :material-book-open-page-variant-outline: Core Runtime Direction

The future `core` profile targets exact official release `2603.4` and deploys Kubernetes workloads, not an external VM. The current initial-runtime selection excludes the former web-proxy and Step CA bootstrap. Their immutable-source and image-mirror records are historical evidence, not current selection. `coriolis-common` remains a base image, not a workload; licensing server/UI, Metal Hub, console editor, and logger/InfluxDB remain deferred.

The configuration contract derives Kubernetes service names through `appliance_resource_name`; it uses plaintext RabbitMQ on `5672`, Keystone HTTP on `5000`, and removes only approved TLS/CA directives from Kubernetes-derived Coriolis and WSGI templates. Upstream root templates remain immutable. The web workload is blocked on offline evidence that it starts without `CA_FINGERPRINT` or a Step CA mount.

## :material-book-open-page-variant-outline: Network And Ingress Policy

Community ingress-nginx is the short-term controller. The future operator will own Ingress resources only; it will not install the controller or create, mutate, or delete certificate Secret material. `certManager` always derives `<host>-tls` and annotates a ready defaulted or explicit `ClusterIssuer`; `existingSecret` alone accepts a same-namespace external `tlsSecretName` and emits no issuer annotation. Defaults are host `coriolis.app.cloudbase.wiki`, class `nginx`, issuer `letsencrypt`, and derived `<host>-tls` Secret.

Ingress is the only external exposure. Backend and dependency Services are ClusterIP and plaintext, which assumes a trusted cluster network and is not encryption. Ingress terminates TLS and redirects HTTPS, then uses HTTP backends. The route, rewrite, exact-origin CORS/preflight, auth-header, and WebSocket contract is frozen in the [Foundational Resource Contract](foundational-resource-contract.md). No route is emitted before its Service exists.

## :material-book-open-page-variant-outline: Current Blocker And Ordering

The next runtime integration is limited to collision-safe reads, create/guarded SSA, and status retry for the marker plus four foundational resources: two retained credential Secrets, the configuration ConfigMap, and the configuration Secret. Add only the required Secret/ConfigMap `get`/`create`/`patch` RBAC and exhaustive tests.

Services, Ingress, dependency bootstrap, workloads, probes, storage, readiness, and route emission remain later milestones. No Kubernetes runtime I/O, Services, Ingresses, or workloads are implemented now.

## :material-book-open-page-variant-outline: API And Lifecycle Policy

The `v1alpha1` API retains optional/defaulted `spec.profile` (`core`), required non-empty `spec.version`, and optional `status.acceptedVersion`; version changes are controller-blocked through status rather than admission. The ingress CRD fields, sample, and pure validation/resolution changed in this slice. No `main.py`, runtime Kubernetes I/O, RBAC, Service, Ingress resource, workload, release/chart/image, or deployment behavior changed.

The condition types are `Accepted`, `Progressing`, `Reconciled`, `Ready`, `Degraded`, and `Upgradeable`. `Ready=False/RuntimeNotImplemented` remains truthful until required dependencies, workloads, and internal checks exist. Future deletion garbage-collects operator-owned workloads, Services, Jobs, and generated ConfigMaps; retained state credentials and PVCs survive, and external referenced Secrets are never deleted.

## :material-book-open-page-variant-outline: Development Constraints And Milestones

The privileged worker may mount `/dev` and `/lib/modules`; single-node `local-path` storage is acceptable and not production HA. Console-editor behavior must be declarative rather than host mutation. Logger Unix-socket compatibility may use a shared retained volume as a transitional design.

Milestone history remains: image/runtime inventory is complete; the API slice is local and undeployed; the deployed `0.5.3` remains marker-only. The next milestone is marker-plus-four foundational runtime integration, followed later by dependencies/bootstrap, workloads, Services/Ingresses, status/readiness, tests, and development acceptance. The first runtime acceptance is complete bootstrap with internally healthy UI/API, not a migration test.

Deferred work includes the licensing server and UI, Metal Hub, console editor and VM-host administration, external provider configuration, migration validation, automatic upgrades, and production HA.

## :material-book-open-page-variant-outline: Ordered Implementation Plan

1. Image and runtime inventory is complete.
2. CRD and runtime API are locally implemented and undeployed; this migration adds ingress schema/sample/pure validation only.
3. Foundational resources next implement collision-safe marker-plus-four reads, create/guarded SSA, minimal Secret/ConfigMap RBAC, status retry, and exhaustive tests.
4. Generated configuration retains immutable upstream provenance, provider order/maps, exact mount boundaries, and value-safe Secret handling.
5. Foundational dependencies and bootstrap Jobs remain later work.
6. Workloads, then their ClusterIP Services and logical-origin Ingress routing, remain later work; no route precedes its Service.
7. Controller watches, status/readiness, broader tests, and development acceptance follow runtime construction.

## :material-book-open-page-variant-outline: Image Gate History

RC4 remains OVA-only for Kubernetes because no approved `2608*` registry tags exist. The exact `2603.4` inventory remains authoritative: 26 images were mirrored and 21 image references passed pull validation. Those counts and digests are historical validation facts; they do not make Step CA or web-proxy current initial-runtime components. See [Image Inventory](image-inventory.md).
