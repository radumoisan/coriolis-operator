# Foundational Resource Contract

This page freezes the foundational Kubernetes resource contract for the `core` profile. It records policy, authoritative evidence, implemented pure/API-local slices, and runtime slices. At accepted Keystone POC cleanup, Argo ran operator `0.5.14`, `1/1` with zero restarts and zero appliance CRs; that deployed controller did not establish overall appliance readiness.

!!! note
    Earlier commits documented three retained Secrets, Step CA bootstrap, and a marker-plus-five-resource sequence. Those are historical design and validation facts only. They are superseded by the marker-plus-four-resource contract below; no past commit, mirror result, or source input is rewritten.

## :material-book-open-page-variant-outline: Evidence And Provenance

Statements below are labeled by evidence class. Authoritative source inputs cite repository-relative paths; frozen Kubernetes policy is normative future behavior, not a claim that it is deployed; unresolved requirements are not source facts.

### :material-application-edit-outline: Authoritative Source Inputs

- `coriolis-docker/coriolis_ansible/appliance.yml` — ordered appliance role application, including historical `web-proxy`.
- `coriolis-docker/coriolis_ansible/bootstrap.yml` — historical bootstrap ordering: `coriolis/common` then `bootstrap/step-ca`.
- `coriolis-docker/coriolis_ansible/group_vars/all.yml` — ports, endpoints, mounts, directories, provider lists, and module maps.
- `coriolis-docker/coriolis_ansible/inventory/appliance` — the single local `coriolis` appliance host.
- `coriolis-docker/coriolis_ansible/library/kolla_deployment_facts.py` — MariaDB, RabbitMQ, Keystone, and Barbican connection facts from the host Kolla deployment.
- `coriolis-docker/passwords.yml.sample` — authoritative appliance credential-key schema; `roles/coriolis/common/tasks/setup.yml` creates the database/user, Keystone user/service/endpoints, and renders configuration.
- `coriolis-docker/coriolis_ansible/roles/common/mariadb/` — MariaDB configuration role.
- `coriolis-docker/coriolis_ansible/roles/coriolis/common/templates/coriolis.conf.j2` — immutable upstream base configuration template and embedded credential locations.
- `coriolis-docker/coriolis_ansible/roles/coriolis/api/tasks/setup_api_container.yml` — representative container shape.
- `coriolis-docker/coriolis_ansible/roles/bootstrap/step-ca/` — historical CA bootstrap and retained CA state evidence.
- `coriolis-oss/systemd/coriolis-api.service`, `coriolis-oss/systemd/coriolis-conductor.service`, `coriolis-oss/systemd/coriolis-worker.service`, and `coriolis-oss/etc/coriolis/coriolis.conf` — upstream service/config conventions.
- `coriolis-operator/src/coriolis_operator/reconcile.py` — local naming/metadata helpers and pure foundational helpers; `main.py` remains the only deployed Kubernetes interaction.
- `coriolis-operator/config/samples/coriolisappliance.yaml` — current sample CR; ingress schema/pure validation changed, not runtime resource behavior.

### :material-application-edit-outline: Evidence Classes

1. **Authoritative source input** — directly quoted or derived from the paths above.
2. **Observed snapshot evidence** — recorded cluster observations, including the registry pull Secret and marker-only deployment.
3. **Frozen Kubernetes policy** — normative contracts to implement, not current runtime behavior.
4. **Unresolved requirement** — a decision or value not established by authoritative evidence.

## :material-book-open-page-variant-outline: Naming And Metadata

### :material-application-edit-outline: Deterministic Names

`state_config_map_name` produces the shipped marker ConfigMap name as a DNS subdomain up to 253 characters, `<appliance-name>-operator-state`, using a 12-character SHA-256 overflow. It is valid for ConfigMaps but deliberately not generalized to Services or workload kinds that require a 63-character DNS label.

`appliance_resource_name` and `appliance_identity` implement the future label-safe shape. Every generated runtime object name is a lowercase single DNS label, `<appliance>-<component>`, at most 63 characters, with no dots. On overflow, hash the full desired name, reserve `-<hash>-<component>`, and truncate the appliance prefix. The helpers are implemented locally but construct no runtime objects.

All operator-created objects use Kubernetes recommended `app.kubernetes.io/*` labels (`name`, `instance`, `version`, `component`, `part-of`, `managed-by`), `coriolis.cloudbase.it/appliance`, `coriolis.cloudbase.it/component`, and the full appliance-name annotation. Label values are capped at 63 characters; the full CR name is not used directly as a label value. A pre-existing object is never adopted or overwritten unless its deterministic name and complete management identity match. A mismatch is `ResourceCollision` and remains unmodified.

Ephemeral, rebuildable resources use a controller owner reference so Kubernetes garbage-collects them on CR deletion. This includes Deployments/StatefulSets, Services, Jobs, generated non-secret ConfigMaps, and the rebuildable configuration Secret. Retained state credentials are ownerless and marked `coriolis.cloudbase.it/retention: state-credentials`; retained PVCs and later retained logger storage are also not owner-referenced. `classify_retained_resource` permits reuse only for an ownerless exact match of deterministic name/namespace and all operator-controlled identity fields; missing, partial, conflicting, or owner-plus-retention metadata is a mutation-free collision. Historical Step CA state is superseded for the initial runtime, but remains source evidence. Referenced external Secrets, including `coriolis-appliance-registry` (`kubernetes.io/dockerconfigjson`), are read-only. There is no destructive finalizer.

## :material-book-open-page-variant-outline: Secrets And Configuration

### :material-application-edit-outline: Retained Credentials

The retained, ownerless credential Secrets are exactly:

- `<appliance>-coriolis-credentials` (component `coriolis-credentials`): `coriolis_database_password`, `coriolis_keystone_password`, `temp_keypair_password`.
- `<appliance>-infrastructure-credentials` (component `infrastructure-credentials`): `database_password`, `rabbitmq_password`, `keystone_admin_password`.

`coriolis_database_password` and `coriolis_keystone_password` map to the Coriolis DB and Keystone users; `temp_keypair_password` maps to the source `passwords.yml.sample` schema. `database_password`, `rabbitmq_password`, and `keystone_admin_password` preserve the Kolla MariaDB, RabbitMQ, and Keystone fact names. The authoritative database and Keystone identities are all `coriolis`: `coriolis_database_name`, `coriolis_database_user`, and `coriolis_keystone_user`.

Values are independently generated by the pure local helpers, are never CR inputs, and must never appear in logs, status, events, metadata, or documentation. The retained Secret validator accepts mapping-shaped resources and `V1Secret` models; it requires `Opaque`, forbids persisted `stringData`, requires exact base64-encoded key sets and non-empty UTF-8 values, and returns a new decoded mapping without exposing values.

### :material-application-edit-outline: Rebuildable Configuration

The owner-referenced ConfigMap `<appliance>-coriolis-config` contains exactly `coriolis-api.wsgi`, `wsgi-coriolis.conf`, `vixdisklib.conf`, `api-paste.ini`, `policy.yml`, and `coriolis.release`. The owner-referenced Secret `<appliance>-coriolis-config-secret` contains exactly `coriolis.conf`. The latter is sensitive and is never a ConfigMap or observable output.

`KubernetesCoriolisRenderInputs` and `kubernetes_coriolis_render_inputs()` derive deterministic `rabbitmq`, `memcached`, `mariadb`, and `keystone` Service names with `appliance_resource_name`; the factory does not render or create Services. They freeze the Coriolis bind address to `0.0.0.0:7667`, configuration directory to `/etc/coriolis`, and VixDiskLib log directory to `/var/log/coriolis/vmware-root`.

The four host-only endpoint fields `rabbitmq_host`, `memcached_host`, `database_host`, and `keystone_host` are non-sensitive internal orchestration inputs, not CRD fields. Protocols and ports are not caller-supplied renderer inputs: the Kubernetes contract fixes RabbitMQ to plaintext `5672` with `ssl=False`, Memcached to `11211`, MariaDB to its driver-default `3306`, Keystone to HTTP public/internal `5000`, and the API bind address to `0.0.0.0:7667`. It has no internal CA references. Root upstream templates remain immutable. Kubernetes-derived `coriolis.conf` and WSGI variants remove only the approved TLS/CA directives while preserving provider fragments and unrelated HTTPS configuration.

| Renderer input group | Contract |
| --- | --- |
| Retained credentials | `rabbitmq_password`; `coriolis_database_password`, `coriolis_keystone_password`, and `temp_keypair_password`. `database_password` and `keystone_admin_password` are not renderer inputs. |
| Dependency addresses | Only the four internal host fields above; all protocols and ports are fixed by the Kubernetes contract. |
| Derived values | Frozen provider lists, first-seen union, and import/export module maps below. Template loop variables are internal. |

The renderer uses Jinja2 `PackageLoader`, `StrictUndefined`, disabled autoescape, and preserves the upstream trailing newline and Apache-2.0 attribution. It packages the immutable base template and all 16 provider fragments. Initial compression is fixed to `compress_transfers=False` and `enable_coriolis_compressor=False`; `compressor_address` is omitted and compressor runtime remains deferred.

| Frozen item | Exact initial value |
| --- | --- |
| Export providers, in order | `openstack`, `oracle-vm`, `opc`, `azure`, `scvmm`, `vmware`, `aws`, `metal`, `ovirt`, `nutanix` |
| Import providers, in order | `openstack`, `oracle-vm`, `opc`, `azure`, `scvmm`, `oci`, `aws`, `vmware`, `ovirt`, `kubevirt`, `lxd`, `proxmox`, `libvirt`, `cloudstack` |
| Provider union, first-seen order | `openstack`, `oracle-vm`, `opc`, `azure`, `scvmm`, `vmware`, `aws`, `metal`, `ovirt`, `nutanix`, `oci`, `kubevirt`, `lxd`, `proxmox`, `libvirt`, `cloudstack` |
| Export module map | `openstack=coriolis_provider_openstack.ExportProvider`; `oracle-vm=coriolis_provider_oracle_vm.ExportProvider`; `opc=coriolis_provider_opc.ExportProvider`; `azure=coriolis_provider_azure.ExportProvider`; `scvmm=coriolis_provider_scvmm.HyperVExportProvider`; `vmware=coriolis_provider_vmware_vsphere.ExportProvider`; `aws=coriolis_provider_aws.ExportProvider`; `metal=coriolis_provider_metal.ExportProvider`; `ovirt=coriolis_provider_ovirt_olvm.ExportProvider,coriolis_provider_ovirt_rhev.ExportProvider`; `nutanix=coriolis_provider_nutanix.ExportProvider` |
| Import module map | `openstack=coriolis_provider_openstack.ImportProvider,coriolis_provider_vhi.ImportProvider`; `oracle-vm=coriolis_provider_oracle_vm.ImportProvider`; `opc=coriolis_provider_opc.ImportProvider`; `azure=coriolis_provider_azure.ImportProvider`; `scvmm=coriolis_provider_scvmm.ImportProvider`; `oci=coriolis_provider_oci.ImportProvider,coriolis_provider_opca.ImportProvider,coriolis_provider_o3c.ImportProvider`; `aws=coriolis_provider_aws.ImportProvider`; `vmware=coriolis_provider_vmware_vsphere.ImportProvider`; `ovirt=coriolis_provider_ovirt_olvm.ImportProvider,coriolis_provider_ovirt_rhev.ImportProvider`; `kubevirt=coriolis_provider_kubevirt.ImportProvider,coriolis_provider_harvester.ImportProvider`; `lxd=coriolis_provider_lxd.ImportProvider`; `proxmox=coriolis_provider_proxmox.ImportProvider`; `libvirt=coriolis_provider_libvirt.ImportProvider`; `cloudstack=coriolis_provider_cloudstack.imp.ImportProvider` |

The required fragments, in provider-union order, are `openstack.conf.j2`, `oracle-vm.conf.j2`, `opc.conf.j2`, `azure.conf.j2`, `scvmm.conf.j2`, `vmware.conf.j2`, `aws.conf.j2`, `metal.conf.j2`, `ovirt.conf.j2`, `nutanix.conf.j2`, `oci.conf.j2`, `kubevirt.conf.j2`, `lxd.conf.j2`, `proxmox.conf.j2`, `libvirt.conf.j2`, and `cloudstack.conf.j2`. Custom module overrides are prohibited. Provider endpoint credentials/private material and standalone provider files remain deferred.

The fixed source-audited values include `rabbitmq_user="openstack"`, `coriolis_debug=true`, database/user/Keystone user `coriolis`, literal Memcached port `11211`, `/etc/coriolis`, `/var/log/coriolis`, `/opt/coriolis/export`, `/opt/coriolis/locks`, `/etc/coriolis/policy.yml`, `/opt/coriolis/vmware-vix-disklib`, and `/etc/coriolis/vixdisklib.conf`. Typed validation requires the supported non-empty string inputs, no CR/LF/NUL injection, redacted representations, fixed category-only errors, and fail-closed undefined or extra inputs. It does not validate removed caller-supplied protocol or port fields: Kubernetes derives the fixed plaintext/HTTP endpoint contract above.

### :material-application-edit-outline: Superseded Initial CA Design

The prior `<appliance>-step-ca-credentials` Secret (whose sole key was `init_password`), its generator/builder/preflight entry, Step CA initial credential, and CA bootstrap are removed from the current foundational set. The former three-retained-Secret/five-resource descriptions are superseded, not evidence that historical source playbooks or mirrored images changed. Historical CA state and TLS/private-key layouts remain unresolved, not initial-runtime work.

### :material-application-edit-outline: Mount And Secret Boundaries

Workloads will mount the generated ConfigMap and configuration Secret together as one read-only projected volume at `/etc/coriolis`, with explicit `items`, no credential environment variables, and no `subPath`. The configuration Secret is rebuilt from retained credentials and is not retained state. Retained credentials are generated only when absent, reused only after exact-match ownerless classification, and never rotated automatically. `Opaque` retained Secrets forbid persisted `stringData`, require exact base64-encoded key sets, and decode to non-empty UTF-8 strings. Values and rendered bytes must never appear in Pod annotations, labels, status, events, logs, or errors.

## :material-book-open-page-variant-outline: Foundational Reconciliation Failure And Marker Contract

### :material-application-edit-outline: Marker-Plus-Four Ordering

The runtime integration validates profile/version, collision-safely reads the marker, then reads these four resources in order: Coriolis credentials Secret, infrastructure credentials Secret, configuration ConfigMap, configuration Secret. It then reads the locally implemented Services in frozen order: RabbitMQ `5672`, Memcached `11211`, MariaDB `3306`, and Keystone `5000`. A `404` is absent; any other read error stops before mutation. Classification, foundational preflight, rendering, and all desired-manifest construction finish before the first write.

Create absent resources collision-safely; apply managed owner-referenced resources with resourceVersion-guarded SSA; never write retained reuse. Write the four foundational resources, then the four Services in frozen order, absent Keystone retained Secrets, MariaDB resources, RabbitMQ resources, the Memcached Deployment, Keystone generated resources, and the marker last. `AlreadyExists` and resource-version conflicts retry from fresh reads. A failure writes a sanitized status before framework retry, with no rollback or compensation. `ResourceCollision` is non-transient and mutation-free. The historical marker-plus-five sequence, including Step CA credentials, is superseded and retained only as validation history.

The development stack through MariaDB reconciliation and anonymous-account prevention is released as `0.5.6`. Source `063e438ef416599e9816a2400afcc5a5a7af9aa0` adds the Memcached Deployment and is released as `0.5.8`. Its isolated single-node POC passed the exact restricted Deployment, Service/EndpointSlice, protocol, replacement, ephemerality, and cleanup contracts. That POC observed healthy operator `0.5.8`; the later Keystone POC cleanup observed healthy `0.5.14` with no appliance CR. `Ready=False/RuntimeNotImplemented` remains truthful until runtime readiness is implemented.

## :material-book-open-page-variant-outline: Ingress And Service Contract

### :material-application-edit-outline: Per-CR Ingress Settings

The schema and pure `IngressSettings` resolver are frozen. Defaults are host `coriolis.app.cloudbase.wiki`, ingress class `nginx`, `certManager` mode, issuer `letsencrypt`, and derived TLS Secret `<host>-tls`.

- `certManager` mode always derives and references `<host>-tls` and annotates the ready defaulted or explicit `ClusterIssuer`.
- `existingSecret` mode alone accepts `tlsSecretName`; it requires that same-namespace external TLS Secret and emits no issuer or cert-manager annotation.
- The future operator owns only its Ingress resources. It never installs ingress-nginx and never creates, mutates, or deletes certificate Secret material.

Ingress has not been reconciled. Community ingress-nginx is the short-term controller decision; see [ADR 0006](decisions/0006-kubernetes-network-ingress.md).

### :material-application-edit-outline: Service And Exposure Policy

Only Ingress is externally exposed. RabbitMQ, Memcached, MariaDB, and Keystone Services are ClusterIP and plaintext on the trusted cluster network; ClusterIP does not encrypt traffic. Memcached's Service selector and EndpointSlice were validated against its Ready Pod on TCP `11211` in the released `0.5.8` POC. TLS and HTTPS redirect terminate at Ingress, with HTTP to backends. Other listed ports and routes remain contract-only until their Services are implemented.

| Component | Service port |
| --- | ---: |
| rabbitmq | 5672 |
| memcached | 11211 |
| mariadb | 3306 |
| keystone | 5000 |
| barbican | 9311 |
| api | 7667 |
| web | 3000 |
| logger | 9998 |
| licensing-server | 37667 |
| metal-hub | 9900 |

### :material-application-edit-outline: Logical Route Map

The public contract is one origin, `https://<host>`. It may use multiple ingress-nginx Ingress resources where per-path rewrites require them. CORS and preflight must allow exactly that origin, preserve authentication headers, and never use a wildcard origin. WebSocket support is required for `/log-stream`.

| Public path | Backend and rewritten path |
| --- | --- |
| `/`, `/api`, `/proxy` | web |
| `/identity` | Keystone `/v3` |
| `/barbican` | Barbican |
| `/coriolis` | API `/v1` |
| `/logs` | logger `/api/v1/logs` |
| `/log-stream` | logger `/api/v1/ws` (WebSocket) |
| `/licensing` | licensing server `/v2` |
| `/metal-hub` | Metal Hub `/api/v1` |

No future route may be emitted until its backend Service exists. RabbitMQ, Memcached, MariaDB, and Keystone Services are implemented. MariaDB, Memcached, and RabbitMQ workloads are released and POC-tested, and Keystone is released as `0.5.14` with accepted released-artifact POC evidence; no workload is currently deployed after cleanup. The API Service/Deployment is released and POC-accepted as `0.5.22`. The web Service/Deployment is released and POC-accepted as `0.5.33`: source `942557a0914b7455af6dbeac6ae5966417bd1223` passed CIXpress Default `opfrnr` at every expected step (`08:39:32Z`-`08:40:58Z`) and CI commit `9f7151af10e2275e15718a325a12e850601ec5f3` published chart/app/operator `0.5.33`. It owns the same-name owner-referenced ClusterIP Service TCP `3000`, ready EndpointSlice, and one `Recreate` replica after the API Service/Deployment and before the per-appliance operator-state marker; it uses `BIND=0.0.0.0`, `/api/config` probes, relative same-origin URLs, and omits `CA_FINGERPRINT`, Step CA, and web-proxy. The exact digest `sha256:32ebc391ac46fe627185694b3fd252afd7587b152f526dff38ae0a5b887c0db1` passed the 17-stage validator and accepted isolated POC, including collision recovery, Service-DNS validation, replacement, drift repair, no-write retained-state CR recreation, and normal cleanup. The released backend web gate is complete; Ingress and all remaining routes remain unimplemented, with logical-origin Ingress next for design and implementation.

## :material-book-open-page-variant-outline: Current Status And Accuracy

The immutable upstream appliance source still contains Step CA and web-proxy roles. They are historical source evidence, not initial Kubernetes runtime selection. Memcached reconciliation is released in `0.5.8`: pipeline `4dcpfk` succeeded at every expected step and CI-owned commit `cb6b055eaf5e74c99e26c1c3d662b2d749331627` set chart/app/image `0.5.8`. Its POC used `cr.virtomat.io/virtomat/coriolis/operator:0.5.8` with imageID `sha256:9af4b018c2a7c0a23635d115d5335477b17bb81a731979bd0c93083c88461af4`; 326 tests plus Ruff lint/format, mypy, Helm lint/template, and diff check remain the local validation. MariaDB CSI/cross-node and production backup/restore, HA, and RPO/RTO remain open. `Ready=False/RuntimeNotImplemented` remains truthful.

### :material-application-edit-outline: Preserved Source And Slice Record

The following detail is retained to distinguish immutable source evidence and completed local pure slices from the current, narrower runtime contract. It does not reinstate the historical Step CA resource or marker-plus-five runtime policy.

| Source or local slice | Preserved detail and current interpretation |
| --- | --- |
| Source bootstrap | `bootstrap.yml` imports `coriolis/common` before `bootstrap/step-ca`. This is historical source ordering only. |
| Source appliance | `appliance.yml` begins with MariaDB, then compressor/common, then logger, API, conductor, transfer-cron, scheduler, minion-manager, deployer-manager, worker, web, web-proxy, licensing server, console editor, Metal Hub, validation, and licensing UI. This is not Kubernetes readiness ordering. |
| Kolla facts | Kolla facts provide MariaDB, RabbitMQ, Keystone, and Barbican connection information. They prove dependency endpoints, not Service construction or readiness. |
| Marker migration | The marker remains under the shipped `state_config_map_name`; compatible legacy marker data may be normalized in place, never renamed. |
| Metadata slice | `appliance_resource_name`, `appliance_identity`, and `build_resource_metadata` provide deterministic naming and identity only; broader runtime construction remains future work. |
| Retained classifier | Exact matching ownerless retained resources are reusable without mutation. The classifier is pure and works with mapping fakes and `V1Secret`/`V1PersistentVolumeClaim` models. |
| Builders and generation | Builders enforce frozen key sets and opaque Secret encoding; generators use independent `secrets.token_urlsafe(32)` values. Neither performs Kubernetes I/O. |
| Renderers | Non-sensitive and sensitive renderers package source templates with strict, value-safe input boundaries. They do not create resources or establish dependencies. |
| Historical preflight | `35eac9b` validated the former five resources. Its Step CA entry is superseded; metadata-first, fail-closed validation remains the model for the four-resource preflight. |

The prior source-audited configuration contract remains useful where it does not conflict with the Kubernetes migration:

- `coriolis.conf.j2` embeds RabbitMQ transport, MariaDB, Keystone authtoken, trustee, and temporary-keypair credentials. The complete file is therefore Secret-only.
- The configuration Secret contains exactly `coriolis.conf`; the ConfigMap excludes `coriolis.conf`, provider fragments, credentials, tokens, private keys, and registry authentication.
- The six ConfigMap keys are source-audited template outputs: `coriolis-api.wsgi`, `wsgi-coriolis.conf`, `vixdisklib.conf`, `api-paste.ini`, `policy.yml`, and `coriolis.release`.
- Provider endpoint credentials and private material remain endpoint data and are not invented as renderer inputs.
- CA path rendering and source Step CA files are historical evidence only. The current derived variants deliberately remove the approved TLS/CA directives and do not establish CA material.
- Compression remains disabled: `compress_transfers=False` and `enable_coriolis_compressor=False`; `compressor_address` is omitted.

### :material-application-edit-outline: Preserved Validation Semantics

The historical resource contract established these durable safeguards, retained for the marker-plus-four migration:

1. Before a write, validate profile/version, pre-read every foundational resource, classify identity/ownership, validate retained Secret semantics, then construct all desired manifests in memory.
2. A `404` is the only absent result. Any other read error stops before resource mutation and produces only a sanitized category status.
3. Retained `REUSE` is a no-write path. A retained Secret is generated only when absent after the complete collision-free preflight.
4. A stable identity, ownership, retention, or semantic mismatch is `ResourceCollision`; it is never force-adopted, normalized, deleted, or converted to a transient API failure.
5. Absent resources use collision-safe create. `AlreadyExists` restarts from fresh reads and preflight.
6. Managed rebuildable resources use guarded server-side apply. An optimistic-concurrency conflict restarts from fresh reads and preflight.
7. Force SSA may resolve field ownership only after identity, ownership/retention, classification, and concurrency checks. It does not bypass those checks.
8. Operations are ordered and marker-last. A failure retains earlier successful writes, skips later writes, and has no rollback, compensation, or deletion.
9. Successful marker application is a foundational-completion record only. It is not workload readiness, cross-object transactionality, or protection from later drift.
10. Status and errors are value-safe: no credential, rendered configuration, base64 data, decoded value, API exception body, header, or token is emitted.

| Status circumstance | Preserved behavior |
| --- | --- |
| Non-`404` marker or foundational pre-read failure | `ResourceReadFailed`; no foundational writes; publish sanitized failure status, then framework retry. |
| Foundational create or guarded-SSA failure | `ResourceApplyFailed`; retain earlier writes, skip later writes, marker unchanged, then retry. |
| Marker create or guarded-SSA failure | `MarkerApplyFailed`; earlier writes remain; marker is unchanged or absent, then retry. |
| Stable metadata or Secret semantic collision | `ResourceCollision`; no mutation and no transient retry conversion. |
| Success | Apply marker last, then publish accepted status. |

For retryable failures, retain the existing condition shape: `Accepted=True/Accepted`, `Progressing=True/Retrying`, `Reconciled=False`, `Ready=False`, `Degraded=True`, and `Upgradeable=False/UpgradeNotSupported`, each with the category reason. Advance `observedGeneration`, preserve a valid prior `acceptedVersion`, and do not establish it after an initially failed reconcile. Use framework-managed retry/backoff only.

### :material-application-edit-outline: Superseded Step CA Record

The following former facts remain historical evidence and must not be treated as current implementation requirements:

- `<appliance>-step-ca-credentials` was a retained ownerless Secret with component `step-ca-credentials` and exactly `init_password`.
- The source Step CA setup generated that password with `openssl rand -base64 32` and stored it at `/etc/step/init_password`.
- The broader `/etc/step` state was classified as retained CA state, while its TLS/private-key Secret layout was unresolved.
- The prior retained credential set therefore had three Secrets and seven generated keys; the current set has two Secrets and six generated keys.
- The former pure `generate_step_ca_credentials` helper, builder, semantic preflight entry, and marker-plus-five policy are superseded by the current contract.
- The source bootstrap role, historical CA volume evidence, and source image observations are not deleted or contradicted by this migration.

### :material-application-edit-outline: Local Slice History

- `ab9df83`: API slice, local only and not deployed.
- `fbab6e5`: label-safe naming/metadata helper slice, local only and not deployed.
- `d8df00f`: marker collision and legacy migration API slice, local only and not deployed.
- `1b73045`: retained-resource authorization/classification slice, local only and not deployed.
- `050f16e`: pure Secret/ConfigMap builder slice, local only and not deployed.
- `a604579`: retained credential generation slice, local only and not deployed.
- `5165629`: retained Secret semantic validation/extraction slice, local only and not deployed.
- `97153a7`: non-sensitive configuration renderer slice, local only and not deployed.
- `35eac9b`: historical five-resource preflight slice, local only and superseded only for its Step CA entry.
- `9bb20f3`: sensitive configuration renderer slice, local only and not deployed.

These slice records establish pure/API behavior only. The subsequent marker-plus-four runtime gate adds `main.py` reconciliation, ordered Kubernetes reads/writes/guarded SSA, exact Secret/ConfigMap `get`/`create`/`patch` RBAC, and sanitized status-then-Kopf-retry handling. The four-Service slice is committed locally at `797235b` on `dev`, unpushed and undeployed, and adds only the four documented Services; Ingress resources, workloads, readiness, chart/release/image versioning, TLS bootstrap, storage, rotation, and deployment remain deferred.

## :material-book-open-page-variant-outline: Dependency And Resource Plan

This section began as the documentation-only dependency evidence gate. MariaDB and Memcached have released implementations and accepted single-node POCs; RabbitMQ is released in `0.5.11` with accepted single-node POC evidence; Keystone is released in `0.5.14` with accepted single-node POC evidence. Later dependencies remain fail-closed on missing evidence. The contracts below distinguish implementation, release, POC, and production readiness.

### :material-application-edit-outline: Approved Mirror Identities

The approved support-image identities are frozen from `scripts/mirror-images.py`:

- RabbitMQ: `cr.virtomat.io/virtomat/coriolis/rabbitmq:2023.1-ubuntu-jammy@sha256:a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a`
- Memcached: `cr.virtomat.io/virtomat/coriolis/memcached:2023.1-ubuntu-jammy@sha256:746b93082a4f6d07f464e93d4b14f5e30510abf17a9ae0a4af20e111408c8f1e`
- MariaDB: `cr.virtomat.io/virtomat/coriolis/mariadb-server:2023.1-ubuntu-jammy@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e`
- Keystone: `cr.virtomat.io/virtomat/coriolis/keystone:2023.1-ubuntu-jammy@sha256:7c57962762f5e6fdb1a109097e8f3e2e5f6218ad9c09f10a585adb67ed245cf0`

The mirrored tags were pull-validated. MariaDB, Memcached, and RabbitMQ workloads additionally validated their exact tag-plus-digest identities in accepted single-node POCs. Local Keystone validation covers the exact digest, direct standalone runtime, database schema, key setup, bootstrap, authenticated HTTP, restart behavior, and operator implementation; Keystone is additionally released as `0.5.14` with an accepted released-artifact single-node POC. Future workload manifests must pin the approved mirror digest and use the read-only external `coriolis-appliance-registry` pull Secret.

### :material-application-edit-outline: Dependency Evidence Matrix

| Dependency | Fixed Service and source-backed consumption | Exact eligibility blockers |
| --- | --- | --- |
| RabbitMQ | Plaintext Kubernetes Service `5672`; source Kolla TLS is historical and must not be copied. Coriolis uses user `openstack` and `rabbitmq_password`. Released `0.5.11` reconciliation and accepted single-node POC evidence cover image/runtime identity, direct startup, retained storage, file-only bootstrap, user/vhost/permission provisioning, probes, and lifecycle. | Released `0.5.11` single-node POC is complete: target storage fsGroup/RWO, authenticated AMQP through Service DNS, EndpointSlice, normal replacement/same-node persistence, CR recreation retained reuse/no-write, and cleanup. CSI cross-node, backup/restore, HA, RPO/RTO, credential rotation, and production storage remain later gates. |
| Memcached | Service `11211`; no credentials, provisioning, or configuration found. OCI and standalone evidence support the released Deployment implementation described below. | Released `0.5.8` single-node POC is complete; persistence, HA, credentials, configuration, resource API, and production readiness are not claimed. |
| MariaDB | Service `3306`; `/etc/kolla/mariadb/galera.cnf` adjusts `max_allowed_packet=64M` and `innodb_log_file_size=256M`. Admin uses `database_password`. Coriolis common idempotently creates database/user `coriolis` with `coriolis_database_password` and grants `coriolis.*:ALL` from `%`. OCI, standalone runtime, and the development Kubernetes contract are recorded below. | Released `0.5.6` passed the accepted single-node `local-path` POC. CSI attach-detach/rescheduling and production backup/restore, HA, and RPO/RTO remain deferred. |
| Keystone | HTTP Service `5000`; admin uses `keystone_admin_password`. Coriolis common idempotently creates user `coriolis`, admin role on `service`, `migration` service, and RegionOne admin/internal/public endpoints. Exact-image standalone evidence supports direct `keystone-wsgi-public`, not the absent baked Apache site. | Dedicated MariaDB database/user and least-scope grant; schema sync/check; Fernet and credential key setup; file-only idempotent admin bootstrap; direct non-root WSGI; `/v3`; token authentication; normal restart; retained key/database state; and operator implementation are complete. Released `0.5.14` accepted an isolated released-artifact Kubernetes POC; rotation, HA, and production readiness remain open. |

### :material-application-edit-outline: Memcached Runtime Evidence

- Use exactly `cr.virtomat.io/virtomat/coriolis/memcached:2023.1-ubuntu-jammy@sha256:746b93082a4f6d07f464e93d4b14f5e30510abf17a9ae0a4af20e111408c8f1e`: Linux/amd64, Memcached `1.6.14`, OCI User `memcached`, Entrypoint `dumb-init --single-child --`, Cmd `kolla_start`, no exposed ports, and no OCI healthcheck. Reject the default Kolla command because it fails without `/var/lib/kolla/config_files/config.json`.
- Start `/usr/bin/memcached` directly with `-p`, `11211`, `-U`, `0`; its default bind is `INADDR_ANY`. Set explicit UID/GID `42457:42457`; supplemental `kolla` group `42400` is not required. The standalone proof passed with a read-only root filesystem, all capabilities dropped, no-new-privileges, explicit non-root IDs, and no mounts, tmpfs, or published ports. No writable path, config, Secret, PVC, or init container is needed.
- Bash and `timeout` exist; netcat does not. Bash `/dev/tcp` proved `version` and set/get behavior. Stopping within 10 seconds exited `0`; recreation lost the cached key as required for an ephemeral cache; disposable resources were cleaned.

### :material-application-edit-outline: MariaDB Runtime Evidence

The approved workload image is `cr.virtomat.io/virtomat/coriolis/mariadb-server:2023.1-ubuntu-jammy@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e`; local inspection and runs used its digest-only form `cr.virtomat.io/virtomat/coriolis/mariadb-server@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e`. It is Linux/amd64, runs as image user `mysql` (UID/GID `42434:42434`, supplemental `kolla` group `42400`), has Entrypoint `dumb-init --` and Cmd `kolla_start`, contains Kolla `16.6.1` and MariaDB `10.6.22`, declares no OCI healthcheck, uses data path `/var/lib/mysql`, runtime socket/PID path `/run/mysqld`, and port `3306`.

`kolla_start` is rejected for operator use: without `/var/lib/kolla/config_files/config.json` it fails; its bootstrap requires `DB_ROOT_PASSWORD` in the environment and passes passwords in client process arguments, violating the value boundary. Its `healthcheck_mariadb` and `clustercheck` are Galera-specific and fail against a healthy non-Galera server.

A disposable local Docker validation proved direct `mariadbd` single-node operation with `wsrep_on=OFF` as `42434:42434`, read-only root filesystem, no-new-privileges, dropped capabilities, writable `/var/lib/mysql`, `/run/mysqld`, and `/tmp`, binding `0.0.0.0:3306`, `max_allowed_packet=64M`, `innodb_log_file_size=256M`, and utf8mb4/InnoDB. Initialization ran only when system tables were absent via `mariadb-install-db`; mode-0600 ephemeral SQL/client files held raw credentials, with no credential environment variables, command arguments, logs, or output. Idempotent database/user/grant bootstrap, authenticated TCP `SELECT 1`, and a durable marker survived clean stop and container recreation using the same Docker volume. All disposable containers, networks, and volumes were removed.

This standalone evidence supports the development Kubernetes contract below. `log-error=/dev/stderr` must not be used because MariaDB tried `/dev/stderr.err`; direct `--console` emitted startup, ready-for-connections, normal shutdown, InnoDB shutdown, and shutdown-complete messages to container logs.

`coriolis-dbsync` is an Alembic-to-head command proven in `coriolis-oss`. No local source proves its invocation, selected image, or ordering. No Job is selected. **Superseded:** the Coriolis-common Bootstrap Kubernetes Contract below now selects the exact conductor Job, whose rendered script performs the Coriolis schema dbsync against the conductor image. This historical MariaDB-runtime sentence is retained as evidence of the pre-selection state, not current selection.

### :material-application-edit-outline: RabbitMQ Runtime Evidence

Use exactly `cr.virtomat.io/virtomat/coriolis/rabbitmq:2023.1-ubuntu-jammy@sha256:a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a`; local image ID is `sha256:f9e28ef3ed172cfdda9e6c3d56c509ceaee672b516381343244ed40332a19e73`. Local inspection found Linux/amd64, Kolla `16.6.1`, account UID/GID `42439`, default `dumb-init --single-child --` plus `kolla_start`, and no supplemental group requirement. Reject the default path without configuration. Direct `/usr/sbin/rabbitmq-server` and `/usr/sbin/rabbitmq-diagnostics` operate with plaintext `0.0.0.0:5672`, console-only logging, a read-only root, dropped `ALL` capabilities, no-new-privileges, and writable `/var/lib/rabbitmq`, `/run/rabbitmq`, and `/var/log/rabbitmq`.

The retained infrastructure Secret key is mounted only as a file. Bootstrap uses a random 4-byte salt and streams Rabbit SHA256 definitions without credential/hash argv, environment, logs, or output; mode-`0600` ephemeral definitions create `openstack`, vhost `/`, and exact permissions. Two retained-volume local launches reached Ready in 15.024s and 13.533s; sanitized running/local-alarm/listener and user/vhost/permission checks passed, broker state and a marker persisted/reconverged, SIGTERM exited `0` in 6.580s and 6.757s, and disposable Docker artifacts were removed.

### :material-application-edit-outline: Keystone Runtime Evidence Checkpoint

- Use exactly `cr.virtomat.io/virtomat/coriolis/keystone:2023.1-ubuntu-jammy@sha256:7c57962762f5e6fdb1a109097e8f3e2e5f6218ad9c09f10a585adb67ed245cf0`; local image ID is `sha256:94cd15e8f645f97f65bd21a38713a13b5da44c67049de3a06436e0741f66d1ec`. It is Linux/amd64, contains Keystone `23.0.2` under `/var/lib/kolla/venv`, declares no OCI user, port, or healthcheck, and defaults to root through Entrypoint `dumb-init --single-child --` plus Cmd `kolla_start`. The packaged `keystone` account is UID/GID `42425:42425` with supplemental `kolla` group `42400`.
- Reject the unconfigured default path: `kolla_start` exits because `/var/lib/kolla/config_files/config.json` is absent. The image has Apache `2.4.52` and enabled `mod_wsgi`, but only its default port-80 site is present; no Keystone Apache site, `/etc/keystone/keystone.conf`, Fernet directory, or credential-key directory is baked in. Standalone evidence and the local implementation select `/var/lib/kolla/venv/bin/keystone-wsgi-public` directly with explicit port `5000`. `/bin/sh`, `curl`, and `ss` are available; `wget` and netcat are absent.
- The tracked local validator `scripts/validate-keystone-runtime.py` completed in `69.063s`. Its file-only MariaDB stages created dedicated database/user `keystone`, set and repeated the password, granted only `keystone.*`, rejected unrelated database creation, authenticated over TCP, wrote/dropped a table, stopped normally, recreated runtime state against retained data, and repeated all checks. No credential, SQL value, DSN, token, key, response body, header, raw log, or process environment was emitted.
- `keystone-manage --config-file <path>` completed `db_sync`, repeated sync, and `db_sync --check`. Non-root `fernet_setup` and `credential_setup` completed and repeated as UID/GID `42425:42425`; each repository contained exact files `0` and `1`, mode `0600`. Installed source confirms each file is an independent `base64.urlsafe_b64encode(os.urandom(32))` value: 44 ASCII bytes without newline. Token and receipt repositories intentionally share the Fernet path; credential keys remain separate. File `0` is staged and file `1` is the initial primary key.
- A non-secret in-process wrapper used `keystone.server.configure(config_files=[...])` and `keystone.cmd.bootstrap.Bootstrapper`; it read the admin password only from a mode-`0400` file and completed twice idempotently. Direct `/var/lib/kolla/venv/bin/keystone-wsgi-public --host 0.0.0.0 --port 5000 -- --config-file <path>` ran as `42425:42425`, supplemental group `42400`, with read-only root, dropped capabilities, no-new-privileges, disabled cache, and writable tmpfs only at `/tmp`, `/run`, `/var/lib/keystone`, and `/var/log/kolla`. `/v3` became healthy in `5.628s`; file-backed password authentication returned HTTP `201` with a non-empty token header in `1.480s`. Normal stop took `0.297s`; restart with the same database and key repositories reached `/v3` in `5.720s`, authenticated in `1.618s`, and stopped in `0.277s`.
- Every disposable container, network, volume, scratch path, and generated credential was removed. This is standalone development evidence, not an operator implementation, release, Kubernetes POC, HA, rotation, or production-readiness claim. Read-only retained Secrets support initial steady state only; automatic key rotation, staged promotion, revocation, credential migration, compromise recovery, and coordinated multi-replica rollout remain deferred.

## :material-book-open-page-variant-outline: Memcached Kubernetes Contract

This frozen development contract has a matching released `0.5.8` implementation and isolated single-node POC. It is not a persistence, HA, credentials, configuration, resource API, or production-readiness claim.

- Freeze one owner-referenced Deployment replica with `Recreate`, the existing ClusterIP Service on `11211`, and the approved external `coriolis-appliance-registry` image pull Secret. Create no config, credential, or storage resources, and make no resource CRD/API change until source-backed values exist. Set pod `runAsUser`/`runAsGroup` to `42457`, `automountServiceAccountToken: false`, `enableServiceLinks: false`, and a 30-second grace period. Set container `runAsNonRoot`, `readOnlyRootFilesystem`, no privilege escalation, drop `ALL` capabilities, and `RuntimeDefault` seccomp.
- Use credential-free protocol-level startup, readiness, and liveness exec probes through `/usr/bin/bash -ec`: open `/dev/tcp/127.0.0.1/11211`, send `version\r\n`, read one response, and accept only a `VERSION ` prefix. Do not replace these with TCP-only probes.
- Reconciliation completes validation, desired-state build, and classification before writes. After the current MariaDB reads, it reads the Deployment in order; creates when absent or uses owned resourceVersion-guarded SSA. Collisions are mutation-free. It writes after MariaDB and before the marker. RBAC is exactly Deployment `get`/`create`/`patch`, with no delete, pod, or log permission. `Ready=False/RuntimeNotImplemented` remains truthful.

## :material-book-open-page-variant-outline: MariaDB Kubernetes Contract

This is a frozen development contract. MariaDB pure desired-state preparation and reconciliation are published on `origin/dev` through `55212b0` and remain undeployed; neither is a deployment, cluster-validation, or production-readiness claim.

### :material-application-edit-outline: API And Failure Boundary

- Retain top-level `spec.profile`, `spec.version`, and `spec.ingress` unchanged. Add optional top-level `spec.storage` and `spec.resources` objects so existing CRs remain schema-valid.
- `spec.storage.mariadb`, when present, requires non-empty `storageClassName` and positive Kubernetes quantity `size`. `spec.resources.mariadb`, when present, requires positive `requests.cpu`, `requests.memory`, `limits.cpu`, and `limits.memory`; each request must not exceed its corresponding limit.
- MariaDB desired-state preparation requires complete storage and resource blocks. Omitted or incomplete configuration is a stable, mutation-free runtime-configuration failure that preserves accepted version state; it creates no MariaDB resource.
- Storage class, initial size, RWO, Filesystem volume mode, and retention identity are immutable after PVC creation; expansion is never automatic. Resource changes may update the managed StatefulSet template and restart its sole pod one at a time. Image/version/storage changes remain blocked.

### :material-application-edit-outline: Storage And Workload

- Create exactly one ownerless retained PVC `<appliance>-mariadb-data`, annotated exactly `coriolis.cloudbase.it/retention: mariadb-data`, with explicit requested class/size, exactly RWO and Filesystem. Exact stable identity and immutable-spec reuse is no-write; never patch or delete a reused PVC. Provisioner/status fields are not operator-managed identity drift.
- Create exactly one owner-referenced StatefulSet `<appliance>-mariadb` with `replicas: 1`. Do not create a Deployment, `volumeClaimTemplates`, PDB, HA, or Galera resources.
- The pod uses `runAsUser`, `runAsGroup`, and `fsGroup` `42434`, `fsGroupChangePolicy: OnRootMismatch`, and `supplementalGroups: [42400]`. The container is non-root, read-only-root, no privilege escalation, drops `ALL` capabilities, and uses `RuntimeDefault` seccomp.
- The retained PVC mounts only at `/var/lib/mysql`. Ordinary `emptyDir` mounts are `/run/mysqld` and `/tmp`; memory-backed or size-limited `emptyDir` is unsupported by evidence. Target storage must honor fsGroup; no privileged/root chown init fallback exists.
- Use exactly `cr.virtomat.io/virtomat/coriolis/mariadb-server:2023.1-ubuntu-jammy@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e` and the read-only external imagePullSecret `coriolis-appliance-registry`.

### :material-application-edit-outline: Generated Configuration And Startup

- Owner-referenced ConfigMap `<appliance>-mariadb-config` contains exactly `my.cnf`, `prepare-mariadb.sh`, and `start-mariadb.sh`, with no credentials. Owner-referenced rebuildable Secret `<appliance>-mariadb-config-secret` contains exactly `admin.cnf`, `coriolis.cnf`, and `bootstrap.sql`, rendered value-safely from the two retained credential Secrets using `data`, never `stringData`.
- An init container using the exact image copies the Secret projection read-only into `/run/mysqld` as UID/GID `42434:42434`, mode `0600`. It runs `mariadb-install-db --datadir=/var/lib/mysql --skip-test-db --auth-root-authentication-method=normal` only if `/var/lib/mysql/mysql` is absent, preventing default test-database anonymous accounts from shadowing `coriolis@%`; it writes an ephemeral first-initialization marker and performs no network SQL.
- The main container preserves image Entrypoint `dumb-init --` and runs the non-secret start script. That script starts direct `mariadbd --defaults-file=<my.cnf-path> --console`, waits on the local socket, applies idempotent file-based `bootstrap.sql`, then writes an ephemeral bootstrap-complete marker only after success; Service endpoints are not ready first. First start alone uses passwordless local-socket root; subsequent starts use mode-`0600` `admin.cnf`. It forwards SIGTERM, waits for MariaDB, and exits with it.
- `my.cnf` fixes `wsrep_on=OFF`, datadir/socket/PID/bind/port, `max_allowed_packet=64M`, `innodb_log_file_size=256M`, and utf8mb4/InnoDB. It has no `log-error`; `--console` is the logging path. Renderers must escape option-file and SQL values. Credentials never appear in ConfigMaps, environment, arguments, metadata, logs, status, events, errors, or documentation.

### :material-application-edit-outline: Probes And Lifecycle

- Startup exec requires the bootstrap-complete marker and mode-`0600` admin-file `mariadb-admin ... ping`: period `10s`, timeout `5s`, failure threshold `30`. Readiness uses the Coriolis TCP client file and `mariadb ... --execute='SELECT 1'`: period `10s`, timeout `5s`, failure threshold `3`, success threshold `1`. Liveness uses the admin socket client file and `mariadb-admin ... ping`: period `10s`, timeout `5s`, failure threshold `6`.
- These conservative timings are development policy, not source facts. Startup gates readiness and liveness. `terminationGracePeriodSeconds` is `30`.
- One StatefulSet replica with RWO and no operator force deletion is the no-concurrent-writer policy. Planned and unplanned disruptions cause downtime; no PDB exists. Rely on Kubernetes/CSI attach-detach and never create a second writer, erase, repair, or reinitialize an existing retained datadir.
- Retain the PVC and credential Secrets across CR deletion/recreation; garbage collection recreates owner-referenced StatefulSet/config resources and `emptyDir`. There is no automatic backup, restore, repair, resize, credential rotation, or MariaDB/image upgrade. Restore is a separately approved operator-run procedure. Production HA/RPO/RTO/backup acceptance remains deferred.

### :material-application-edit-outline: Reconciliation And RBAC

- After the existing marker/foundational/four-Service read prefix, read retained PVC, MariaDB ConfigMap, MariaDB config Secret, then StatefulSet. Complete API validation, classification, secret-safe rendering, and all desired manifests before writes.
- Write the existing four foundational resources, four Services, then create an absent PVC or exact no-write reuse, guarded SSA ConfigMap, Secret, StatefulSet, and marker last. Only `404` is absent; collisions remain mutation-free and retry/status rules remain sanitized.
- PVC RBAC is `get`/`create` only and StatefulSet RBAC is `get`/`create`/`patch`; existing ConfigMap/Secret permissions suffice. Do not grant pod/log, delete, PVC patch, PDB, or cluster-scope permissions.
- Released `0.5.6` passed clean single-node `local-path` validation without repair: RWO, fsGroup, zero anonymous accounts/test database, authenticated probes, exact retained reuse, persisted same-node remount, and clean 30-second termination. CSI rescheduling/attach-detach remains unvalidated. Production is still blocked on backup/restore, HA, and RPO/RTO.

### :material-application-edit-outline: Durable Workload Invariants

- Only the four existing dependency Services are in scope. Barbican, Step CA, and web-proxy are excluded.
- Generated workloads, Jobs, and configuration resources use controller owner references. Retained PVCs are ownerless, exact-match reuse only, and are never automatically deleted; retained credentials are never automatically rotated.
- Credential values never appear in environment variables, metadata, status, events, logs, or documentation. Configuration files and projected Secrets remain the value boundary.
- Desired-state preparation completes before writes. Only `404` is absent. Collisions are mutation-free. Managed rebuildable resources use guarded SSA.
- The marker records foundational completion only. `Ready=False/RuntimeNotImplemented` remains until mandatory dependencies, Jobs, Coriolis workloads, and internal checks succeed.

## :material-book-open-page-variant-outline: RabbitMQ Kubernetes Contract

This contract was local evidence before publication. It is now implemented and released in `0.5.11`; the accepted single-node POC evidence below is the current acceptance record, not overall appliance readiness.

- Optional explicit `spec.storage.rabbitmq` and `spec.resources.rabbitmq` require complete valid settings before RabbitMQ manifest preparation. Invalid settings are stable and mutation-free.
- Create one separate ownerless retained RWO Filesystem PVC with exact-match no-write reuse, one owner ConfigMap, and one restricted owner-referenced StatefulSet with `replicas: 1`. Use the existing RabbitMQ Service and infrastructure Secret reference, direct file-only scripts, and the approved digest. No new RBAC verbs are added.
- Read and classify before mutation; prepare all RabbitMQ manifests before writes; use guarded SSA for managed rebuildable resources; preserve retained PVC reuse without writes. After MariaDB writes, write RabbitMQ before Memcached, with the marker last. Collisions are mutation-free and failures remain value-safe.
- Released `0.5.11` accepted the single-node `local-path` gates: fsGroup/RWO, Service/EndpointSlice, authenticated AMQP through Service DNS, stable readiness, normal replacement/same-node persistence, exact retained no-write CR recreation, and cleanup. CSI cross-node, backup/restore, HA, RPO/RTO, credential rotation, and production storage remain later gates. Keystone is released as `0.5.14` with accepted released-artifact POC evidence. `Ready=False/RuntimeNotImplemented` remains truthful.

## :material-book-open-page-variant-outline: Keystone Kubernetes Contract

This frozen development contract follows the completed standalone evidence above. It is implemented, validated, and released; the accepted `0.5.14` released-artifact POC is the current acceptance record, but it is not HA, rotation-capable, or production-ready.

### :material-application-edit-outline: Retained Credentials And Keys

- Add ownerless retained Secret `<appliance>-keystone-database-credentials`, component `keystone-database-credentials`, retention `state-credentials`, with exactly `keystone_database_password`. Generate it independently through the existing 32-byte `secrets.token_urlsafe(32)` policy only when absent; exact reuse is no-write and automatic rotation is excluded.
- Add ownerless retained Secret `<appliance>-keystone-fernet-keys`, component `keystone-fernet-keys`, retention `state-credentials`, with exactly keys `0` and `1`. Add a separate ownerless retained Secret `<appliance>-keystone-credential-keys` with the same metadata shape and exact keys `0` and `1`. Generate each file independently as `base64.urlsafe_b64encode(secrets.token_bytes(32))`, producing exact 44-byte ASCII values without newline, then encode those values as Kubernetes Secret `data`. File `0` is staged and file `1` is primary. Never share credential keys with token/receipt keys.
- Mount retained Secret projections read-only. A restricted prepare init container copies them into separate `emptyDir` repositories owned by `42425:42425`, directories mode `0700`, files mode `0600`; the runtime never writes the Secret projections. Retain all three Secrets across CR deletion/recreation. Do not add PVCs, service-account-token access, a key-setup Job, automatic setup/rotation commands, or rotation RBAC.

### :material-application-edit-outline: Database And Generated Configuration

- Extend the existing MariaDB `bootstrap.sql` renderer from the dedicated retained Keystone database password: idempotently create database/user `keystone`, reset its password, and grant only `keystone.*` from `%`. Keep the existing MariaDB configuration Secret key set unchanged and keep all values file-only. Add a fixed non-secret MariaDB pod-template bootstrap-schema annotation so existing managed MariaDB Pods roll once when this schema support is introduced; do not expose a credential-derived hash.
- Create owner-referenced ConfigMap `<appliance>-keystone-config` with exactly non-secret `bootstrap.py`. Create owner-referenced rebuildable Secret `<appliance>-keystone-config-secret` with exactly `keystone.conf` and `auth-request.json`, rendered from the dedicated database and existing admin credentials. The config fixes PyMySQL to Service `3306`, Fernet token/receipt repositories, separate credential repository, disabled cache, stderr logging, and HTTP endpoints `http://<appliance>-keystone:5000/v3`. Values never enter argv, environment, ConfigMaps, metadata, status, events, logs, or documentation.

### :material-application-edit-outline: Workload And Lifecycle

- Create one owner-referenced Deployment `<appliance>-keystone`, `replicas: 1`, strategy `Recreate`, using the approved digest and existing ClusterIP Service. Do not add a StatefulSet, PVC, PDB, service-account token, sidecar, `keystone-fernet`, `keystone-ssh`, Apache site, TLS, or Ingress in this slice.
- Set pod UID/GID/fsGroup `42425`, supplemental groups `[42400]`, `fsGroupChangePolicy: OnRootMismatch`, disabled service links and service-account-token automount, and 30-second termination. Every container is non-root, read-only-root, no privilege escalation, drops `ALL`, and uses `RuntimeDefault` seccomp. Use ordinary `emptyDir` only for prepared configuration/key repositories and `/tmp`, `/run`, `/var/lib/keystone`, and `/var/log/kolla`.
- Init containers run in order: prepare mode/ownership-safe files; `keystone-manage --config-file <path> db_sync` plus `db_sync --check`; then the file-only in-process `Bootstrapper`. These operations are idempotent and may repeat after Pod replacement. Coriolis user, `migration` service, role assignment, and endpoints remain the later Coriolis-common bootstrap milestone.
- The main process is direct `keystone-wsgi-public --host 0.0.0.0 --port 5000 -- --config-file <path>`. Startup and readiness exec probes use file-backed HTTP password authentication, require HTTP `201` and a non-empty token header, and emit neither. Liveness uses unauthenticated HTTP `/v3`; it must not turn a transient database outage into an immediate restart loop. Use development timings startup `10s`/timeout `10s`/threshold `30`, readiness `10s`/timeout `10s`/threshold `3`, and liveness `10s`/timeout `5s`/threshold `6`.

### :material-application-edit-outline: Reconciliation And RBAC

- After the existing reads, pre-read and classify all three retained Keystone Secrets, the owner ConfigMap/configuration Secret, and Deployment before mutation. Generate absent retained state only after every collision check; exact retained reuse is no-write. Complete MariaDB rerendering and all Keystone manifests before the first write.
- Write existing foundational resources and Services, then absent Keystone retained Secrets, MariaDB resources, RabbitMQ resources, Memcached, Keystone generated configuration and Deployment, and the marker last. Managed resources use resourceVersion-guarded SSA; collisions remain mutation-free and retry/status failures remain value-safe. The Deployment init containers retry naturally until MariaDB becomes ready.
- Existing Secret, ConfigMap, and Deployment permissions already cover exact `get`/`create`/`patch`; retained reuse never patches. Add no Job, Pod/log, delete, PVC, or cluster-scope RBAC. `Ready=False/RuntimeNotImplemented` remains truthful after this dependency workload because Coriolis-common bootstrap and application workloads are still absent.

### :material-application-edit-outline: Eligibility Sequence

Complete all four dependency interfaces first. MariaDB's accepted clean single-node POC is complete; CSI/cross-node evidence remains later. Memcached is released and its isolated POC is complete. RabbitMQ `0.5.11` has accepted released-artifact POC evidence. Keystone is released as `0.5.14` with accepted released-artifact POC evidence. Coriolis common bootstrap follows healthy dependencies. Historical Ansible order is evidence, not Kubernetes readiness proof.

Before code for any dependency, require evidence for:

- OCI User, Entrypoint, and Cmd.
- Configuration copy or start mechanism, non-secret environment, security context, and writable/data paths.
- Storage class, size, access mode, mount, and retention where storage applies.
- Idempotent bootstrap command, image, authentication, inputs, outputs, retry, and completion behavior.
- Startup, readiness, and liveness probes; replica and disruption behavior; dependency readiness.

If evidence is absent, stop rather than infer.

## :material-book-open-page-variant-outline: Coriolis-Common Bootstrap Kubernetes Contract

Release `0.5.20` is accepted for collision recovery. Corrective source `5f52c7004b3b393d85c10b07e9301d3fa3587164` (`Continue collision recovery retries`) passed Default pipeline `0km4kz` (15:10:28Z-15:11:36Z; top-level and `git-clone`/`kaniko-build`/`helm-update`/`cleanup` all `SUCCEEDED`) and released CI commit `61c7b3a0d0cea2e81b290023fa8ee5605e2ff261`; POC operator imageID was `sha256:8d537d978783496338ba4bda60d412690b9c3bd24f6187e31ee8d1de4735e190`. Full local validation passed: 490 unit tests, Ruff lint/format, mypy, Helm lint/template, container build, and `git diff --check`. `0.5.19` remains unaccepted historical root-cause evidence: its 28s automatic recovery dropped the timer retry after publishing `BootstrapRunning`, leaving the succeeded Job's CR `Reconciled=False/BootstrapRunning` for over 10 minutes. `coriolis-common` remains a base image, not a workload.

### :material-application-edit-outline: Image, Script, And Object Identity

- The Job selects the exact conductor `2603.4` digest `27495f44fbb8b320098d0aa04cd9dcb2a4b432e57aa17417606efc5403ac09c7`. Accepted v2 completed in place from `12:23:11Z` to `12:23:42Z` (31s), `succeeded=1`, `failed=0`, exit `0`, restarts `0`; v1 remained failed and untouched. Dependencies were `1/1` with one endpoint each. `coriolis-common` is not a workload; the bootstrap runs on the conductor image.
- The owner ConfigMap and owner Job share deterministic `<appliance>-common-bootstrap-v2` identity (component `common-bootstrap-v2`). The ConfigMap is immutable and holds exactly one non-secret rendered `bootstrap.py`; it is create-only, with no patch, delete, or TTL.
- The Job pins the conductor image and is create-only immutable with no patch, delete, or TTL. It binds the exact rendered script via a non-sensitive script-digest annotation and a template-id digest, both recorded in the object and immutable pod-template annotations; the template ID covers the script identity, so any script change under a given revision is a collision that requires an explicit next revision. No service-account token automount and no service links.

### :material-application-edit-outline: Mounts, Values, And Security

- The bootstrap script, the existing generated configuration, and the retained credential Secrets are projected file-only through independent, non-overlapping read-only mounts. No credential, config, or script content enters environment variables, argv, status, events, logs, or documentation.
- Kubernetes-derived `coriolis.conf` emits the provider list as one contiguous indented oslo-config multiline value. Dbsync retains the complete generated configuration and adds only supported `--nouse-syslog --log-dir=` overrides for the restricted Job.
- The container runs as non-root UID/GID `42434`, read-only root, dropped capabilities, no-new-privileges, and only `/tmp` writable, under a restricted security context.

### :material-application-edit-outline: Bounds And Completion

- A bounded shared dependency wait of `300s` precedes the run; the Coriolis schema dbsync has a `120s` timeout; the Job has a `600s` active deadline and `backoffLimit: 2`.

### :material-application-edit-outline: Validation Evidence

- Tracked validator `scripts/validate-coriolis-bootstrap-runtime.py` uses the actual full sensitive renderer, not a minimal dbsync config, and emits no secrets. Dependencies, dbsync and repeat (`5.076s`, `3.761s`), actual bootstrap and repeat (`23.886s`, `20.044s`), and independent verification (`8.022s`) passed in `135.212s` with zero leftovers.
- The Coriolis DB/user/grants remain existing MariaDB effects; the service-project creation is a necessary dedicated-Keystone precondition.

### :material-application-edit-outline: Reconciliation And RBAC

- Reconciliation pre-reads the bootstrap ConfigMap and Job after Keystone and before writes; classification is collision-mutation-free. The immutable ConfigMap is created only when absent and is never patched (exact managed reuse is no-write). The Job is create-only: a managed Job is reusable only when its projected managed spec exactly equals the desired spec (image, command, env, security contexts, mounts, volume sources/items/modes, pull Secret, restart/deadline/backoff/completions/parallelism, and template labels/annotations) and both object metadata and pod-template annotations carry the exact script and template IDs; any drift is a mutation-free `ResourceCollision`. An absent/active Job returns `Progressing`/`BootstrapRunning` with the existing ten-second `TemporaryError` requeue and no marker; a terminal failed Job returns `Degraded`/`BootstrapFailed` with no marker; a succeeded Job is a no-writes path plus the marker last. The current accepted status remains `Ready=False/RuntimeNotImplemented`.
- Released collision recovery retains the 60-second collision-only initial-delay/interval timer. It invokes normal reconciliation only for persisted exact `Reconciled=False/ResourceCollision` or a Kopf `retry > 0`; malformed and noncollision `retry=0` calls are no-ops. It narrowly continues the retry that published `BootstrapRunning`, suppresses unchanged operator-owned status updates, preserves unrelated status, and adds no child watches or list/watch/delete RBAC, CRD, finalizer, destructive behavior, or readiness change.
- Accepted `0.5.20` collision evidence in fresh isolated `coriolis-collision-recovery-20260823`: the unmanaged immutable bootstrap ConfigMap held for 135s (over two intervals) with unchanged UID/resourceVersion/creation/immutable/hash; CR `90548df2-6321-409e-98fb-7130975200e5` remained generation `1`, resourceVersion `13263783`, `Reconciled=False/ResourceCollision`; operator Pod `076dc2e0-583e-45c8-ab44-eb1f56ec7df7` remained zero-restart; and no appliance children or status churn occurred. Deletion at `15:25:20.744Z` began recovery at `15:26:12.507Z` (51.763s); retry crossed `BootstrapRunning` and final convergence was `15:27:40.726Z` (139.982s total), without a user CR/spec change, generation change, or operator restart. Final state retained CR UID/generation/observed generation `1`, `acceptedVersion=2603.4`, `Accepted=True`, `Reconciled=True`, `Degraded=False`, `Ready=False/RuntimeNotImplemented`; Job succeeded at `15:27:34Z` (`1` succeeded, `0` failed, exit `0`, restarts `0`); dependencies were `1/1`; four Services had one ready EndpointSlice each; the replacement immutable ConfigMap had a distinct UID and correct ownership; and the operator-state marker was present. Exact Application `0.5.20` was `Synced/Healthy`; normal cleanup removed CR-owned children in 1s, Application in 18s, namespace in 50s, and both Delete-policy PVs without force or retained-resource/PV deletion.
- RBAC adds only batch/jobs `get`/`create`, with no patch/delete/list/watch/Pod/log or cluster scope. No CRD, application Service/Deployment, Ingress, new Secret, finalizer, or `Ready=True` is added.
- Full local validation passes at 490 unit tests, Ruff lint/format, mypy, Helm lint/template, container build, and `git diff --check`. No `Ready=True`, application workload, or RBAC change is added.

## :material-book-open-page-variant-outline: Coriolis API Kubernetes Contract

This contract is released and POC-accepted as `0.5.22`. It is the first application workload after accepted Coriolis-common bootstrap and does not establish full runtime readiness.

### :material-application-edit-outline: Exact Image And Process

- Pin `cr.virtomat.io/virtomat/coriolis/coriolis-api:2603.4@sha256:fce6369f07ef777b5174d3a4f849d4eac914256a20a47ffa0cd1c98081be2705`. Run the direct `/usr/local/bin/coriolis-api --worker-process-count 1 --config-file=/etc/coriolis/coriolis.conf` interface, not the rejected legacy Apache startup path.
- Exact-image qualification with synthetic generated configuration proved one master and one worker, 30-second stability, exact unauthenticated `/v1` HTTP `401`, the fixed output-suppressed Python exec probe, and normal exit `0` in `0.848s`. No live dependency was required for that negative auth probe; meaningful authenticated API behavior remains coupled to conductor RPC.

### :material-application-edit-outline: Service, Workload, And Security

- Create owner-referenced `<appliance>-coriolis-api` ClusterIP Service TCP `7667` and one-replica `Recreate` Deployment. Select only the label-safe appliance identity and component `coriolis-api`; do not add external exposure or Ingress in this slice.
- Project all six existing non-sensitive ConfigMap keys plus sensitive `coriolis.conf` together read-only at `/etc/coriolis`, with explicit key/path/mode entries, no `subPath`, no environment credentials, and no Pod-template value/hash leakage. Use ordinary `emptyDir` for `/var/log/coriolis` and `/opt/coriolis/locks`, memory-backed `emptyDir` for `/tmp`, and no PVC, init container, sidecar, or resource API invention.
- Run UID/GID/fsGroup `42434`, `fsGroupChangePolicy: OnRootMismatch`, no service-account token, no service links, 15-second termination, read-only root, non-root, no privilege escalation, dropped `ALL`, and `RuntimeDefault`. Startup/readiness/liveness all execute the fixed Python localhost `/v1` probe and accept only HTTP `401`; liveness uses a six-failure threshold.

### :material-application-edit-outline: Reconciliation And Validation

- Pre-read the API Service after dependency Services and the API Deployment after existing dependency Deployments, before any mutation. Classify both before building manifests; either collision returns mutation-free `ResourceCollision`. Managed resources require resourceVersion-guarded SSA.
- Build all API desired state before writes but apply neither resource while common bootstrap is absent, active, or failed. After a succeeded immutable bootstrap Job, apply Service then Deployment, then marker last. Existing Service/Deployment `get`/`create`/`patch` RBAC is sufficient; add no watch/list/delete, child handler, finalizer, or CRD change.
- Full local validation passes at 503 unit tests, Ruff lint/format, strict mypy, Helm lint/template, container build, and `git diff --check`. `Accepted=True/Reconciled=True` describes desired-state application, but `Ready=False/RuntimeNotImplemented` remains mandatory until required workloads and internal checks exist.
- Released as `0.5.22`: implementation source `5a06759d6e0332345262f990b2fb56d9d82fbef7` (`Implement Coriolis API reconciliation`) passed the full local gate and was published by Default pipeline `44muez` (18:45:00Z-18:46:33Z; top-level and `git-clone`/`kaniko-build`/`helm-update`/`cleanup` all `SUCCEEDED`) as CI release commit `ef86249cecee812482a96da8f41b1985d7cb764b`, chart/app/image `0.5.22`.
- Accepted isolated POC in `coriolis-api-validation-20260823` at exact chart/image `0.5.22`: unmanaged API Service (UID `c70790a3-c791-4477-9e43-aaac5f1351be`) held over 65s unchanged, then auto-recovered in about 127s after conflict deletion at `18:57:46Z`; the happy API showed the owner-referenced Service ready EndpointSlice TCP `7667` and one-replica `Recreate` Deployment at the exact digest with API Pod Ready zero restarts and Service-DNS `/v1` exact unauthenticated `401`; a deployment collision/recreation hit an unmanaged zero-replica Deployment (UID prefix `8407babd`) about 70s then auto-recovered in about 73s with exact retained metadata/PVC volumes unchanged under the new owner UID; normal cleanup removed CR-owned children in 5s and the exact Application, namespace, and both Delete-policy PVs normally. Authenticated workflow completeness beyond the verified read-only RPC smoke remains unclaimed; the conductor RPC backend is now released and POC-accepted as `0.5.24`, and overall `Ready=False/RuntimeNotImplemented` remains required.

## :material-book-open-page-variant-outline: Unresolved Gates

The development stack through MariaDB reconciliation is included in released `0.5.6`. Memcached source `063e438ef416599e9816a2400afcc5a5a7af9aa0` is included in released `0.5.8`; pipeline `4dcpfk` and each expected step succeeded. The completed POC removed its CR, Helm release, namespace, copied registry Secret, retained PVC, and Delete-policy PV; the node remained Ready/schedulable and zero appliances remained cluster-wide. No workload remains deployed after cleanup.

For MariaDB, the development contract fixes workload, retained storage, generated manifests, bootstrap, probes, resource rules, lifecycle, and console logging; preparation, reconciliation, and anonymous-account prevention are published on `origin/dev`, with the fix released as `0.5.6`. Released clean-first-boot single-node `local-path` validation passed for RWO, fsGroup, zero anonymous/test accounts, retained reuse, same-node remount, authenticated probes, persistence, and clean termination without repair. RabbitMQ `0.5.11` accepted POC evidence covers its single-node local-path contract, including clean-storage smoke and normal cleanup. Keystone is released as `0.5.14` with accepted released-artifact POC evidence. The API slice is released and POC-accepted as `0.5.22`. The conductor slice is released and POC-accepted as `0.5.24`; its authenticated read-only RPC is accepted, while write workflows/diagnostics and scheduler/workers/transfer-cron/minion/deployer remain absent. CSI/cross-node, backup/restore, HA, RPO/RTO, credential and key rotation, and production storage remain open. Provider private material, optional component credentials, Barbican and other backend Services, and Ingress routes remain deferred.

### :material-application-edit-outline: Milestone History

- `ab9df83` is the local API slice; `fbab6e5` adds label-safe naming/metadata; `d8df00f` adds marker pre-read/migration handling; and `1b73045` adds pure retained-resource classification. None is deployed.
- `050f16e` adds pure manifest builders; `a604579` adds credential generation; `5165629` adds retained Secret semantic validation; `97153a7` adds non-sensitive rendering; `35eac9b` adds the historical five-resource preflight; and `9bb20f3` adds sensitive rendering. The historical three-Secret preflight is superseded by the current marker-plus-four contract.
- The marker-plus-four runtime gate follows the present ingress/pure-input slice and is committed locally at `862777d`, with status commit `f219977`; both are unpushed and undeployed. The four-Service slice is committed locally at `797235b` on `dev`, unpushed and undeployed, raises the local unit count to 252, and implements only RabbitMQ, Memcached, MariaDB, and Keystone Services; deployed `0.5.3` remains marker-only.
- MariaDB desired-state preparation, reconciliation, and anonymous-account prevention are published on `origin/dev`, with the fix released as `0.5.6`; current validation is 316 tests plus Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. The accepted clean single-node POC is complete. CSI/cross-node and production backup/restore, HA, and RPO/RTO remain open.
