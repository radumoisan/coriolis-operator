# Foundational Resource Contract

This page freezes the foundational Kubernetes resource contract for the `core` profile. It records policy, authoritative evidence, implemented pure/API-local slices, and the completed local marker-plus-four runtime gate. The deployed operator `0.5.3` remains marker-only.

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

Create absent resources collision-safely; apply managed owner-referenced resources with resourceVersion-guarded SSA; never write retained reuse. Write the four foundational resources, then the four Services in frozen order, and apply the marker last. `AlreadyExists` and resource-version conflicts retry from fresh reads. A failure writes a sanitized status before framework retry, with no rollback or compensation. `ResourceCollision` is non-transient and mutation-free. The historical marker-plus-five sequence, including Step CA credentials, is superseded and retained only as validation history.

The marker-plus-four runtime gate is committed locally at `862777d`, with status commit `f219977`; both are unpushed and undeployed. The four-Service slice is committed locally at `797235b` on `dev`, unpushed and undeployed. Workloads, endpoints, Ingress, Jobs, storage, probes, bootstrap, credential rotation, remaining Services, and runtime design are later work. `Ready=False/RuntimeNotImplemented` remains the truthful status until runtime readiness is implemented.

## :material-book-open-page-variant-outline: Ingress And Service Contract

### :material-application-edit-outline: Per-CR Ingress Settings

The schema and pure `IngressSettings` resolver are frozen. Defaults are host `coriolis.app.cloudbase.wiki`, ingress class `nginx`, `certManager` mode, issuer `letsencrypt`, and derived TLS Secret `<host>-tls`.

- `certManager` mode always derives and references `<host>-tls` and annotates the ready defaulted or explicit `ClusterIssuer`.
- `existingSecret` mode alone accepts `tlsSecretName`; it requires that same-namespace external TLS Secret and emits no issuer or cert-manager annotation.
- The future operator owns only its Ingress resources. It never installs ingress-nginx and never creates, mutates, or deletes certificate Secret material.

Ingress has not been reconciled. Community ingress-nginx is the short-term controller decision; see [ADR 0006](decisions/0006-kubernetes-network-ingress.md).

### :material-application-edit-outline: Service And Exposure Policy

Only Ingress is externally exposed. The locally implemented RabbitMQ, Memcached, MariaDB, and Keystone Services are ClusterIP and plaintext on the trusted cluster network; ClusterIP does not encrypt traffic. TLS and HTTPS redirect terminate at Ingress, with HTTP to backends. Other listed ports and routes remain contract-only until their Services are implemented.

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

No future route may be emitted until its backend Service exists. RabbitMQ, Memcached, MariaDB, and Keystone Services are locally implemented; no workloads, endpoints, Ingress, or other Services are implemented. Before adding the web workload, prove offline that the web image starts without `CA_FINGERPRINT` and without a Step CA mount.

## :material-book-open-page-variant-outline: Current Status And Accuracy

The immutable upstream appliance source still contains Step CA and web-proxy roles. They are historical source evidence, not initial Kubernetes runtime selection. The foundational runtime integration is committed locally at `862777d`, with status commit `f219977`; both are unpushed and undeployed. The four-Service slice is committed locally at `797235b` on `dev`, unpushed and undeployed, and adds only RabbitMQ, Memcached, MariaDB, and Keystone Services. Each uses deterministic `appliance_resource_name`, standard owner-referenced metadata, ClusterIP/plaintext with no explicit `clusterIP`/`clusterIPs`, selector exactly the label-safe appliance identity plus component, and one named TCP Service/target port at the same fixed number. Secret, ConfigMap, and Service RBAC are each exactly `get`/`create`/`patch`. Validation passed: 252 unit tests, Ruff lint, Ruff format check (35 files already formatted), mypy, Helm lint/template, and `git diff --check`. No workload, endpoint, Ingress, Job, release, chart, image, or deployment behavior changed. Deployed `0.5.3` remains marker-only.

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

This is a documentation-only, pre-implementation dependency workload evidence and eligibility contract. It is not a workload manifest contract. No dependency workload, Job, PVC, probe, readiness behavior, RBAC, or runtime behavior exists from this contract.

### :material-application-edit-outline: Approved Mirror Identities

The approved support-image identities are frozen from `scripts/mirror-images.py`:

- RabbitMQ: `cr.virtomat.io/virtomat/coriolis/rabbitmq:2023.1-ubuntu-jammy@sha256:a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a`
- Memcached: `cr.virtomat.io/virtomat/coriolis/memcached:2023.1-ubuntu-jammy@sha256:746b93082a4f6d07f464e93d4b14f5e30510abf17a9ae0a4af20e111408c8f1e`
- MariaDB: `cr.virtomat.io/virtomat/coriolis/mariadb-server:2023.1-ubuntu-jammy@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e`
- Keystone: `cr.virtomat.io/virtomat/coriolis/keystone:2023.1-ubuntu-jammy@sha256:7c57962762f5e6fdb1a109097e8f3e2e5f6218ad9c09f10a585adb67ed245cf0`

The mirrored tags were pull-validated. The tag-plus-digest strings themselves were not cluster-tested. Future workload manifests must pin the approved mirror digest and use the read-only external `coriolis-appliance-registry` pull Secret.

### :material-application-edit-outline: Dependency Evidence Matrix

| Dependency | Fixed Service and source-backed consumption | Exact eligibility blockers |
| --- | --- | --- |
| RabbitMQ | Plaintext Kubernetes Service `5672`; source Kolla TLS is historical and must not be copied. Known config: `/etc/kolla/rabbitmq/rabbitmq.conf`. Coriolis uses user `openstack` and `rabbitmq_password`; Coriolis common creates no RabbitMQ user, vhost, or policy. | OCI User/Entrypoint/Cmd; plaintext Kolla config; writable path and persistence policy; user/default-vhost provisioning; probe. |
| Memcached | Service `11211`; no credentials, provisioning, or configuration found. | OCI metadata; launch/config mechanism; writable or ephemeral policy; probe. |
| MariaDB | Service `3306`; `/etc/kolla/mariadb/galera.cnf` adjusts `max_allowed_packet=64M` and `innodb_log_file_size=256M`. Admin uses `database_password`. Coriolis common idempotently creates database/user `coriolis` with `coriolis_database_password` and grants `coriolis.*:ALL` from `%`. | OCI metadata; single-node, non-Galera configuration; data path/PVC contract; startup/bootstrap; probe. |
| Keystone | HTTP Service `5000`; `/etc/kolla/keystone/wsgi-keystone.conf`; admin uses `keystone_admin_password`. Coriolis common idempotently creates user `coriolis`, admin role on `service`, `migration` service, and RegionOne admin/internal/public endpoints. | OCI metadata; WSGI config and command; MariaDB schema/sync; fernet/bootstrap state; writable paths; probe. |

`coriolis-dbsync` is an Alembic-to-head command proven in `coriolis-oss`. No local source proves its invocation, selected image, or ordering. No Job is selected.

### :material-application-edit-outline: Durable Workload Invariants

- Only the four existing dependency Services are in scope. Barbican, Step CA, and web-proxy are excluded.
- Generated workloads, Jobs, and configuration resources use controller owner references. Retained PVCs are ownerless, exact-match reuse only, and are never automatically deleted; retained credentials are never automatically rotated.
- Credential values never appear in environment variables, metadata, status, events, logs, or documentation. Configuration files and projected Secrets remain the value boundary.
- Desired-state preparation completes before writes. Only `404` is absent. Collisions are mutation-free. Managed rebuildable resources use guarded SSA.
- The marker records foundational completion only. `Ready=False/RuntimeNotImplemented` remains until mandatory dependencies, Jobs, Coriolis workloads, and internal checks succeed.

### :material-application-edit-outline: Eligibility Sequence

Complete all four OCI, configuration, and probe interfaces first. MariaDB is the first candidate contract and vertical slice because source evidence places database provisioning first, but it is not implementation-eligible until its storage, startup, and probe gates close. RabbitMQ and Memcached follow; Keystone follows MariaDB; Coriolis common bootstrap follows healthy dependencies. Historical Ansible order is evidence, not Kubernetes readiness proof.

Before code for any dependency, require evidence for:

- OCI User, Entrypoint, and Cmd.
- Configuration copy or start mechanism, non-secret environment, security context, and writable/data paths.
- Storage class, size, access mode, mount, and retention where storage applies.
- Idempotent bootstrap command, image, authentication, inputs, outputs, retry, and completion behavior.
- Startup, readiness, and liveness probes; replica and disruption behavior; dependency readiness.

If evidence is absent, stop rather than infer.

## :material-book-open-page-variant-outline: Unresolved Gates

This evidence contract is local, unpushed, and undeployed. The marker API/migration, metadata, retained-resource classifier, builders, credential generation, retained Secret validation, renderers, preflight, Kubernetes render-input factory, derived template variants, ingress schema/resolver, marker-plus-four runtime gate (`862777d` with status `f219977`), and four-Service slice (`797235b`) retain their recorded status. Current validation passed: 252 unit tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`.

Implementation remains blocked on the required per-dependency OCI/configuration/security/writable-path/storage/bootstrap/probe/replica/disruption/readiness evidence, MariaDB single-node and persistence decisions, Keystone schema/fernet/bootstrap evidence, and a selected, proven `coriolis-dbsync` Job contract. Credential rotation, provider private material, optional component credentials, Barbican and other backend Services, and Ingress routes after backend workloads remain deferred. No runtime readiness is claimed.

### :material-application-edit-outline: Milestone History

- `ab9df83` is the local API slice; `fbab6e5` adds label-safe naming/metadata; `d8df00f` adds marker pre-read/migration handling; and `1b73045` adds pure retained-resource classification. None is deployed.
- `050f16e` adds pure manifest builders; `a604579` adds credential generation; `5165629` adds retained Secret semantic validation; `97153a7` adds non-sensitive rendering; `35eac9b` adds the historical five-resource preflight; and `9bb20f3` adds sensitive rendering. The historical three-Secret preflight is superseded by the current marker-plus-four contract.
- The marker-plus-four runtime gate follows the present ingress/pure-input slice and is committed locally at `862777d`, with status commit `f219977`; both are unpushed and undeployed. The four-Service slice is committed locally at `797235b` on `dev`, unpushed and undeployed, raises the local unit count to 252, and implements only RabbitMQ, Memcached, MariaDB, and Keystone Services; deployed `0.5.3` remains marker-only.
