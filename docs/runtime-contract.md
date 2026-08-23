# Runtime Contract

Existing upstream Coriolis component repositories and images are immutable inputs. The operator does not rebuild or patch them. At accepted Keystone POC cleanup, Argo ran healthy operator `0.5.14`, `1/1` with zero restarts and no appliance CR; `Ready=False/RuntimeNotImplemented` remains correct because the complete runtime is not implemented.

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

The development stack through MariaDB reconciliation is included in released `0.5.6`. Memcached reconciliation source `063e438ef416599e9816a2400afcc5a5a7af9aa0` is released as `0.5.8`. RabbitMQ reconciliation is released as `0.5.11` with its retained PVC, owner ConfigMap, and restricted one-replica StatefulSet. Its `0.5.10` bootstrap/authenticated-AMQP POC was not accepted after 37 readiness timeouts and Endpoint flapping at `5s`/`5s`; the `10s`/`15s` probe fix passed the accepted `0.5.11` single-node POC. No runtime workload remains deployed after validation cleanup; the Coriolis API Service/Deployment slice is released and POC-accepted as `0.5.22`, and the conductor slice is released and POC-accepted as `0.5.24` (both recorded below); the remaining application workloads follow.

Coriolis-common bootstrap is accepted as released `0.5.17`: fix source `764b9952d1e1c9bc1cbce08afddea8781f391f42` was published by pipeline `ectoq4` (`2026-08-23T12:16:43Z`-`12:18:06Z`), with all expected steps `SUCCEEDED`, as CI commit `6bfc494d50949b7a5aa770c4febb7c5100b3b363`; operator imageID is `sha256:443e6e5dec8cd6e7f2040ca4fe1f5dcfcfa40ad36184cb30dc54a4be7547d8a6`. Historical `0.5.16` v1 failed on provider-continuation parsing before dbsync and correctly produced `BootstrapFailed`, no marker, and no mutation. v2 completed in place in 31s with success `1`, failure `0`, exit `0`, and zero restarts on the exact conductor digest; v1 remained failed and untouched. Dependencies were `1/1` with one endpoint each. Final state is v2 only, v1 absent, `acceptedVersion=2603.4`, `Accepted=True`, `Reconciled=True`, `Degraded=False`, and `Ready=False/RuntimeNotImplemented`. Exact-image output-suppressed verification passed twice in 10s, including post-recreation, covering schema, user/project/auth/admin assignment, migration service, and one RegionOne admin/internal/public endpoint each. No Ingress or application workload is deployed.

The first application slice is released and POC-accepted as `0.5.22`: implementation source `5a06759d6e0332345262f990b2fb56d9d82fbef7` (`Implement Coriolis API reconciliation`) passed the full local gate (503 unit tests, Ruff lint/format, strict mypy, Helm lint/template, container build, and `git diff --check`) and was published by Default pipeline `44muez` (18:45:00Z-18:46:33Z; all expected steps `SUCCEEDED`) as CI release commit `ef86249cecee812482a96da8f41b1985d7cb764b`, chart/app/image `0.5.22`. The accepted isolated POC in `coriolis-api-validation-20260823` at exact chart/image `0.5.22` showed the owner-referenced `<appliance>-coriolis-api` ClusterIP Service `7667` (ready EndpointSlice TCP `7667`) and one-replica `Recreate` direct-binary Deployment pinned to the exact `2603.4` API digest, with collision auto-recovery, retention across CR recreation, and normal cleanup. Exact-image qualification proved non-root UID/GID `42434`, read-only root, dropped capabilities, no-new-privileges, read-only projected `/etc/coriolis`, writable `/tmp`, `/var/log/coriolis`, and `/opt/coriolis/locks`, stable unauthenticated `/v1` HTTP `401`, and normal stop. Reconciliation applies API only after successful common bootstrap and before the marker; it adds no CRD or RBAC and retains `Ready=False/RuntimeNotImplemented`. Authenticated workflow completeness beyond the verified read-only RPC smoke remains unclaimed; the conductor slice that provides the RPC backend is released and POC-accepted as `0.5.24`.

## :material-book-open-page-variant-outline: API And Lifecycle Policy

The `v1alpha1` API retains optional/defaulted `spec.profile` (`core`), required non-empty `spec.version`, and optional `status.acceptedVersion`; version changes are controller-blocked through status rather than admission. Ingress plus optional MariaDB and RabbitMQ storage/resources CRD fields remain schema-compatible; complete runtime settings are required before mutation. Released `0.5.17` includes MariaDB, Memcached, RabbitMQ, Keystone, and the accepted immutable v2 Coriolis-common bootstrap Job; it includes no application workloads or Ingress.

The condition types are `Accepted`, `Progressing`, `Reconciled`, `Ready`, `Degraded`, and `Upgradeable`. `Ready=False/RuntimeNotImplemented` remains truthful until required dependencies, workloads, and internal checks exist. Deletion garbage-collects operator-owned workloads, Services, Jobs, and generated ConfigMaps; retained state credentials and PVCs survive, and external referenced Secrets are never deleted.

## :material-book-open-page-variant-outline: Development Constraints And Milestones

The privileged worker may mount `/dev` and `/lib/modules`; single-node `local-path` storage is acceptable and not production HA. Console-editor behavior must be declarative rather than host mutation. Logger Unix-socket compatibility may use a shared retained volume as a transitional design.

Milestone history remains: image/runtime inventory, the MariaDB single-node POC, the Memcached `0.5.8` POC, the RabbitMQ `0.5.11` accepted POC, Keystone `0.5.14`, accepted Coriolis-common `0.5.17` v2, accepted Coriolis API `0.5.22`, and accepted Coriolis conductor `0.5.24`. No appliance or runtime workload remains deployed after cleanup. MariaDB/RabbitMQ/Keystone CSI cross-node evidence, backup/restore, HA, RPO/RTO, credential/key rotation, and production storage remain later gates. The immediate milestone is the remaining application workloads, then Ingress.

Deferred work includes the licensing server and UI, Metal Hub, console editor and VM-host administration, external provider configuration, migration validation, automatic upgrades, and production HA.

## :material-book-open-page-variant-outline: Ordered Implementation Plan

1. Image and runtime inventory is complete.
2. The CRD and runtime API are released through accepted `0.5.17`; its immutable v2 Coriolis-common bootstrap completed successfully while historical v1 was left failed and untouched during promotion. Optional explicit MariaDB and RabbitMQ storage/resource inputs are present without changing the API version.
3. Foundational resources, the four-Service slice, and dependency reconciliation are released; no appliance runtime remains deployed after POC cleanup. The four-Service slice adds only four dependency Services with matching guarded reconciliation and Service `get`/`create`/`patch` RBAC.
4. Generated configuration retains immutable upstream provenance, provider order/maps, exact mount boundaries, and value-safe Secret handling.
5. RabbitMQ, Keystone, and Coriolis-common `0.5.17` v2 are accepted released-artifact POCs. The v2 Job retains its create-only/collision, time-bound, RBAC, marker-last, and `Ready=False/RuntimeNotImplemented` rules.
6. The API Service/Deployment is released and POC-accepted as `0.5.22`. The conductor slice is released and POC-accepted as `0.5.24`; the remaining application workloads follow, then logical-origin Ingress routing; no route precedes its Service.
7. Controller watches, status/readiness, broader tests, and development acceptance follow runtime construction.

## :material-book-open-page-variant-outline: Image Gate History

RC4 remains OVA-only for Kubernetes because no approved `2608*` registry tags exist. The exact `2603.4` inventory remains authoritative: 26 images were mirrored and 21 image references passed pull validation. Those counts and digests are historical validation facts; they do not make Step CA or web-proxy current initial-runtime components. See [Image Inventory](image-inventory.md).
