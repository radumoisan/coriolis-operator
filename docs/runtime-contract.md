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

## :material-book-open-page-variant-outline: Current Implementation Boundary

The development stack through MariaDB reconciliation is published on `origin/dev` at `55212b0` and included in released operator `0.5.6`. It retains the read prefix and implements exactly four Services in frozen order: RabbitMQ `5672`, Memcached `11211`, MariaDB `3306`, and Keystone `5000`. It uses ordered reads, completes preparation before writes, returns mutation-free `InvalidRuntimeConfiguration` for invalid storage/resources configuration, creates an absent PVC or exactly reuses it without writes, uses guarded SSA for managed ConfigMap, Secret, and StatefulSet resources, and writes MariaDB resources before the marker. PVC RBAC is `get`/`create`; StatefulSet RBAC is `get`/`create`/`patch`.

Clean first boot of `0.5.5` exposed anonymous initialization accounts shadowing `coriolis@%`; commit `3ee5d2d` adds `--skip-test-db` and CIXpress released it as `0.5.6`. Released `0.5.6` passed clean single-node `local-path` validation without repair: RWO Filesystem provisioning, fsGroup writes, exact retained reuse, persisted state, authenticated probes, same-node remount, and normal 12-second termination. CSI and cross-node attach/detach remain unvalidated. Remaining dependency bootstrap, probe, and readiness work, Barbican and other Services, and route emission remain later milestones. No endpoints, Ingresses, or Jobs are implemented.

## :material-book-open-page-variant-outline: API And Lifecycle Policy

The `v1alpha1` API retains optional/defaulted `spec.profile` (`core`), required non-empty `spec.version`, and optional `status.acceptedVersion`; version changes are controller-blocked through status rather than admission. The ingress and optional MariaDB storage/resources CRD fields, sample, and pure validation/resolution remain local-only. Current local reconciliation adds four dependency Services plus MariaDB PVC, configuration resources, and StatefulSet, but no Ingress resource, endpoint, Job, release/chart/image version, or deployment behavior.

The condition types are `Accepted`, `Progressing`, `Reconciled`, `Ready`, `Degraded`, and `Upgradeable`. `Ready=False/RuntimeNotImplemented` remains truthful until required dependencies, workloads, and internal checks exist. Deletion garbage-collects operator-owned workloads, Services, Jobs, and generated ConfigMaps; retained state credentials and PVCs survive, and external referenced Secrets are never deleted.

## :material-book-open-page-variant-outline: Development Constraints And Milestones

The privileged worker may mount `/dev` and `/lib/modules`; single-node `local-path` storage is acceptable and not production HA. Console-editor behavior must be declarative rather than host mutation. Logger Unix-socket compatibility may use a shared retained volume as a transitional design.

Milestone history remains: image/runtime inventory and the accepted MariaDB single-node POC are complete; the development stack and anonymous-account fix are published on `origin/dev`, with the fix released as `0.5.6`. No appliance or runtime workload remains after POC cleanup. RabbitMQ and Memcached workload evidence is next; CSI/cross-node evidence and production storage acceptance remain later gates. Remaining Services and Ingress routes follow only after their backends are defined. The first runtime acceptance is complete bootstrap with internally healthy UI/API, not a migration test.

Deferred work includes the licensing server and UI, Metal Hub, console editor and VM-host administration, external provider configuration, migration validation, automatic upgrades, and production HA.

## :material-book-open-page-variant-outline: Ordered Implementation Plan

1. Image and runtime inventory is complete.
2. CRD and runtime API are locally implemented and undeployed; this migration adds ingress schema/sample/pure validation only.
3. Foundational resources, the four-Service slice, and MariaDB reconciliation are published on `origin/dev` through `55212b0` and undeployed; the four-Service slice adds only four dependency Services with matching guarded reconciliation and Service `get`/`create`/`patch` RBAC.
4. Generated configuration retains immutable upstream provenance, provider order/maps, exact mount boundaries, and value-safe Secret handling.
5. Remaining dependency workloads, bootstrap Jobs, storage, and readiness remain later work.
6. Barbican and other backend Services, then logical-origin Ingress routing, remain later work; no route precedes its Service.
7. Controller watches, status/readiness, broader tests, and development acceptance follow runtime construction.

## :material-book-open-page-variant-outline: Image Gate History

RC4 remains OVA-only for Kubernetes because no approved `2608*` registry tags exist. The exact `2603.4` inventory remains authoritative: 26 images were mirrored and 21 image references passed pull validation. Those counts and digests are historical validation facts; they do not make Step CA or web-proxy current initial-runtime components. See [Image Inventory](image-inventory.md).
