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

The development stack through MariaDB reconciliation is included in released `0.5.6`. Memcached reconciliation source `063e438ef416599e9816a2400afcc5a5a7af9aa0` is released as `0.5.8`. RabbitMQ reconciliation is released as `0.5.11` with its retained PVC, owner ConfigMap, and restricted one-replica StatefulSet. Its `0.5.10` bootstrap/authenticated-AMQP POC was not accepted after 37 readiness timeouts and Endpoint flapping at `5s`/`5s`; the `10s`/`15s` probe fix passed the accepted `0.5.11` single-node POC. No runtime workload remains deployed after validation cleanup.

Clean first boot of `0.5.5` exposed anonymous initialization accounts shadowing `coriolis@%`; commit `3ee5d2d` adds `--skip-test-db` and CIXpress released it as `0.5.6`. Released `0.5.6` passed clean single-node `local-path` validation without repair: RWO Filesystem provisioning, fsGroup writes, exact retained reuse, persisted state, authenticated probes, same-node remount, and normal 12-second termination. RabbitMQ `0.5.11` has accepted released-artifact single-node POC evidence. Keystone implementation `f90cae4` was released as `0.5.13` (CI commit `7eb2215`) and, after the emptyDir-mount fix `087bbc2`, accepted as `0.5.14` (CI commit `edd349a`) with an isolated released-artifact single-node POC. Source `391f94c6095f55178ba125c5eec22270284b0b92` was published by successful CIXpress pipeline `v2nv5t` as `0.5.16` at CI commit `54381936f98941473b166543274c0ea29a263e40`. Its POC reached ready dependencies and Services, but immutable `<appliance>-common-bootstrap-v1` failed all three attempts before dbsync; `BootstrapFailed`, no marker, `Ready=False/RuntimeNotImplemented`, and no Job mutation were correct. Sanitized exact-image diagnosis identified oslo-config provider-continuation parsing at lines 15 and 27. The uncommitted/unpushed/unreleased/undeployed v2 correction uses `<appliance>-common-bootstrap-v2`, exact conductor digest `27495f44fbb8b320098d0aa04cd9dcb2a4b432e57aa17417606efc5403ac09c7`, contiguous indented provider-list rendering, and full generated config with supported dbsync `--nouse-syslog --log-dir=` overrides; its released-artifact POC is pending. No Ingresses or released/implemented application Services or Jobs are deployed.

## :material-book-open-page-variant-outline: API And Lifecycle Policy

The `v1alpha1` API retains optional/defaulted `spec.profile` (`core`), required non-empty `spec.version`, and optional `status.acceptedVersion`; version changes are controller-blocked through status rather than admission. Ingress plus optional MariaDB and RabbitMQ storage/resources CRD fields remain schema-compatible; complete runtime settings are required before mutation. Released `0.5.16` includes MariaDB, Memcached, RabbitMQ, and Keystone dependency workloads plus the failed immutable v1 Coriolis-common bootstrap Job; it includes no application workloads or Ingress. The local v2 correction is the next release and released-artifact POC gate.

The condition types are `Accepted`, `Progressing`, `Reconciled`, `Ready`, `Degraded`, and `Upgradeable`. `Ready=False/RuntimeNotImplemented` remains truthful until required dependencies, workloads, and internal checks exist. Deletion garbage-collects operator-owned workloads, Services, Jobs, and generated ConfigMaps; retained state credentials and PVCs survive, and external referenced Secrets are never deleted.

## :material-book-open-page-variant-outline: Development Constraints And Milestones

The privileged worker may mount `/dev` and `/lib/modules`; single-node `local-path` storage is acceptable and not production HA. Console-editor behavior must be declarative rather than host mutation. Logger Unix-socket compatibility may use a shared retained volume as a transitional design.

Milestone history remains: image/runtime inventory, the MariaDB single-node POC, the Memcached `0.5.8` POC, the RabbitMQ `0.5.11` accepted POC, and Keystone standalone evidence are complete. Keystone's local evidence covered dedicated database/user, schema, retained-key model, idempotent bootstrap, direct WSGI, authenticated HTTP, restart, and cleanup in `69.063s`; reconciliation then passed 397 tests plus repository quality gates, and fix `087bbc2` passed 398 tests before release. No appliance or runtime workload remains after cleanup. MariaDB/RabbitMQ/Keystone CSI cross-node evidence, backup/restore, HA, RPO/RTO, credential/key rotation, and production storage remain later gates. Keystone remains accepted as released `0.5.14` POC evidence; the next milestone is v2 publication and its released-artifact POC, then later Services and Ingress.

Deferred work includes the licensing server and UI, Metal Hub, console editor and VM-host administration, external provider configuration, migration validation, automatic upgrades, and production HA.

## :material-book-open-page-variant-outline: Ordered Implementation Plan

1. Image and runtime inventory is complete.
2. The CRD and runtime API are released through `0.5.16`; its disposable POC reached dependencies and Services but failed the immutable v1 bootstrap before dbsync, without claiming accepted bootstrap. Optional explicit MariaDB and RabbitMQ storage/resource inputs are present without changing the API version.
3. Foundational resources, the four-Service slice, and dependency reconciliation are released; no appliance runtime remains deployed after POC cleanup. The four-Service slice adds only four dependency Services with matching guarded reconciliation and Service `get`/`create`/`patch` RBAC.
4. Generated configuration retains immutable upstream provenance, provider order/maps, exact mount boundaries, and value-safe Secret handling.
5. RabbitMQ is published and its isolated released-artifact POC is accepted. Keystone is released as `0.5.14` and its isolated released-artifact POC is accepted. Coriolis-common `0.5.16` POC failed before dbsync as documented above; local uncommitted v2 under `<appliance>-common-bootstrap-v2` retains create-only/collision, time-bound, RBAC, marker-last, and `Ready=False/RuntimeNotImplemented` rules. Its released-artifact POC is the next milestone.
6. Barbican and other backend Services, then logical-origin Ingress routing, remain later work; no route precedes its Service.
7. Controller watches, status/readiness, broader tests, and development acceptance follow runtime construction.

## :material-book-open-page-variant-outline: Image Gate History

RC4 remains OVA-only for Kubernetes because no approved `2608*` registry tags exist. The exact `2603.4` inventory remains authoritative: 26 images were mirrored and 21 image references passed pull validation. Those counts and digests are historical validation facts; they do not make Step CA or web-proxy current initial-runtime components. See [Image Inventory](image-inventory.md).
