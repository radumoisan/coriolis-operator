# Foundational Resource Contract

This page freezes the foundational Kubernetes resource contracts for the `core` runtime profile: deterministic naming, metadata labels, ownership/deletion/retention, Secret/configuration classes, credential-generation policy, retained Secret semantics, non-sensitive and sensitive configuration rendering, pure five-resource preflight, multi-resource read/apply failure and marker-last semantics, and a dependency evidence inventory with a proposed implementation ordering. It records **policy and evidence** plus implemented pure local slices. Metadata-only helpers, the pure retained-resource authorization/classification slice (`classify_retained_resource`), five pure Secret/ConfigMap manifest builders, three pure retained credential generators, the pure `validated_retained_secret_values` helper, pure `render_coriolis_config`, pure `render_sensitive_coriolis_config`, and pure `preflight_foundational_resources` are implemented locally. No Kubernetes reconciliation, read/SSA/RBAC path, dependency, or workload construction/deployment is implemented, and nothing here changes deployed runtime behavior.

!!! note
    This is the first Milestone 4 deliverable, a documentation-only contract slice plus implemented local helper/classifier/builder/generator/semantic-validation/rendering/preflight slices. The deployed operator `0.5.3` remains marker-only; the local API slice is committed at `ab9df83`, the metadata-only helper slice at `fbab6e5`, the collision/migration marker API-layer slice at `d8df00f`, the retained-resource authorization/classification slice at `1b73045`, the documentation-only Secret/configuration contract slice at `8ce26ba`, the pure builder slice at `050f16e`, the pure generator slice at `a604579`, the semantic validation/extraction slice at `5165629`, the non-sensitive renderer at `97153a7`, the pure preflight at `35eac9b`, the documentation-only sensitive renderer contract at `574efcf`, and the pure sensitive renderer at `9bb20f3`, all on `dev` but not pushed or deployed. A MariaDB vertical slice still requires collision-safe runtime pre-reads and create/guarded SSA for all five resources plus the marker, minimal Secret/ConfigMap RBAC, status-then-retry wiring, storage, probes/readiness/bootstrap, and rotation decisions.

## :material-book-open-page-variant-outline: Evidence And Provenance

Statements below are labeled by evidence class. **Authoritative source inputs** cite an exact repository-relative path; **frozen Kubernetes policy** is a normative contract to implement, not a claim about current behavior or source evidence; **unresolved** items record a gap with no authoritative evidence. Do not treat policy or an unresolved item as authoritative source fact.

### :material-application-edit-outline: Authoritative Source Inputs

- `coriolis-docker/coriolis_ansible/appliance.yml` — ordered role application for the appliance.
- `coriolis-docker/coriolis_ansible/bootstrap.yml` — a separate bootstrap play that imports `coriolis/common` (tasks_from `bootstrap`) then `bootstrap/step-ca` (tasks_from `deploy`).
- `coriolis-docker/coriolis_ansible/group_vars/all.yml` — ports, endpoints, mounts, directories, and image registry/namespace/tags.
- `coriolis-docker/coriolis_ansible/inventory/appliance` — the single `coriolis` appliance host with `ansible_connection=local`.
- `coriolis-docker/coriolis_ansible/library/kolla_deployment_facts.py` — reads `/etc/kolla/globals.yml` and `/etc/kolla/passwords.yml` on the host and exposes MariaDB, RabbitMQ, Keystone, and Barbican connection facts.
- `coriolis-docker/passwords.yml.sample` — the authoritative key schema for appliance credential variables (names only, empty values).
- `coriolis-docker/coriolis_ansible/roles/common/mariadb/` — MariaDB configuration role.
- `coriolis-docker/coriolis_ansible/roles/coriolis/common/tasks/setup.yml` — database/user creation, Keystone user/service/endpoints, and configuration template rendering.
- `coriolis-docker/coriolis_ansible/roles/coriolis/common/templates/coriolis.conf.j2` — the rendered Coriolis configuration and the credential values it embeds.
- `coriolis-docker/coriolis_ansible/roles/coriolis/api/tasks/setup_api_container.yml` — representative container shape (name, hostname, image, volumes, network mode).
- `coriolis-docker/coriolis_ansible/roles/bootstrap/step-ca/` — CA bootstrap and retained CA state.
- `coriolis-oss/systemd/coriolis-api.service`, `coriolis-oss/systemd/coriolis-conductor.service`, `coriolis-oss/systemd/coriolis-worker.service` — upstream service/config conventions.
- `coriolis-oss/etc/coriolis/coriolis.conf` — upstream config reference (contains only sample placeholder values, never real credentials).
- `coriolis-operator/src/coriolis_operator/reconcile.py` — the local naming/metadata helpers (`state_config_map_name`, `appliance_resource_name`, `appliance_identity`, `build_resource_metadata`, `build_state_config_map`).
- `coriolis-operator/src/coriolis_operator/main.py` — the only implemented Kubernetes interaction today (server-side apply of that ConfigMap).
- `coriolis-operator/config/samples/coriolisappliance.yaml` — the sample CR (`profile: core`, `version: "2603.4"`).

### :material-application-edit-outline: Evidence Classes

1. **Authoritative source input** — directly quoted or derived from the paths above (deterministic behavior of the existing controller, container/db/service names, ports, volumes).
2. **Observed snapshot evidence** — live-cluster observations already recorded in [progress.md](progress.md), [runtime-contract.md](runtime-contract.md), and [image-inventory.md](image-inventory.md) (for example, the retained `coriolis-appliance-registry` pull Secret, and the marker-only deployed behavior).
3. **Frozen Kubernetes policy** — this document's normative decisions (labels, retention annotations, ownership strategy). These are contracts to implement, not current behavior.
4. **Unresolved requirement** — a decision or value with no authoritative evidence yet; listed under the relevant section and in [Unresolved Gates](#unresolved-gates).

## :material-book-open-page-variant-outline: Naming And Metadata

### :material-application-edit-outline: Deterministic Naming Shape

The controller must derive every resource name deterministically from the `CoriolisAppliance` resource so that reconciliation is idempotent and names are reproducible across restarts. Two distinct helper families exist in `coriolis-operator/src/coriolis_operator/reconcile.py`:

- `state_config_map_name` produces the shipped state ConfigMap name (a DNS **subdomain** up to 253 characters, `<appliance-name>-operator-state`, with a 12-character SHA-256 overflow; `NAME_HASH_LENGTH = 12`, `DNS_LABEL_MAX_LENGTH = 63`, `CONFIG_MAP_NAME_MAX_LENGTH = 253`). That subdomain shape is valid for ConfigMaps but is **not** valid for kinds that require a 63-character DNS **label**, notably Services and many workload/resource names, so it is deliberately not generalized.
- `appliance_resource_name` and `appliance_identity` implement the label-safe single-DNS-label shape below. These helpers are **implemented locally**; they produce names and identity tokens only, and no runtime object is constructed or deployed from them yet.

Frozen policy uses a conservative single shape for all generated runtime object names:

- Every generated runtime object name is a single lowercase DNS label: `<appliance>-<component>`.
- Maximum **63 characters**; the combined name must contain **no dots**.
- `<component>` is a fixed, short, validated component token (e.g. `api`, `worker`, `mariadb`).
- On overflow, hash the full untruncated desired name with the existing 12-character SHA-256 principle, reserve `-<hash>-<component>`, truncate/trim the appliance prefix, and produce no dots.

The label-safe helpers above (`appliance_resource_name`, `appliance_identity`) now implement this shape locally and are covered by the 44 unit tests, but they produce names/identity only; no runtime object is constructed or deployed from them yet.

### :material-application-edit-outline: Standard Labels And Appliance Identity

The locally updated marker ConfigMap (`build_state_config_map` in `coriolis-operator/src/coriolis_operator/reconcile.py`) now routes through `build_resource_metadata` and therefore carries the label groups below plus the full-appliance-name annotation, while deliberately retaining its shipped `state_config_map_name` (preserving the `0.5.2`/`0.5.3` names, including dotted/long DNS-subdomain behavior). The **deployed** marker `0.5.3` remains unchanged and carries no labels. Frozen policy adds two label groups to every operator-created object:

- **Kubernetes recommended labels** (`app.kubernetes.io/*`): `name`, `instance`, `version`, `component`, `part-of`, `managed-by` — for selector-less grouping, discovery, and tooling.
- **Operator appliance identity**: `coriolis.cloudbase.it/appliance` and `coriolis.cloudbase.it/component` for the workload or resource role.

Label **values** are also capped at 63 characters. Do not place an arbitrary CR name directly into `app.kubernetes.io/instance` or `coriolis.cloudbase.it/appliance`. Instead, define a deterministic label-safe appliance identity token using the same truncation/hash principle, and record the full CR name in an annotation such as `coriolis.cloudbase.it/appliance-name`. The **owner UID** remains the ephemeral-resource identity check; for retained resources (below) the label-safe token and annotation support discovery only.

These labels are the management/ownership signature the controller uses for collision handling (below). `build_resource_metadata` now implements them locally (standard `app.kubernetes.io/*` labels, the `coriolis.cloudbase.it/*` identity labels, the full-appliance-name annotation, and exactly one lifecycle mode — owner reference or retention annotation). Marker ConfigMap collision handling/enforcement is now implemented locally via pre-read classification (see [Current Status And Accuracy](#current-status-and-accuracy)); collision handling for the broader future runtime resource set remains frozen policy and is not implemented.

### :material-application-edit-outline: Collision Handling

The controller must **never adopt or overwrite an existing object** unless its management labels and ownership/retention identity match the appliance contract. Concretely:

- Before patching any object it intends to manage, read it and verify the appliance identity label (`coriolis.cloudbase.it/appliance`) and component label match the appliance, and that any owner reference matches the same `CoriolisAppliance` UID.
- If an object with the target name exists but does not match, treat it as a collision: do not modify it, surface it as a reconcile error/blocking condition, and do not delete it.
- Server-side apply (SSA) is preserved as the application mechanism of the existing marker (`field_manager="coriolis-operator"`, `force=True`, content type `application/apply-patch+yaml` in `coriolis-operator/src/coriolis_operator/main.py:122`). Extending SSA to the broader resource set is a **future implementation step**, not current behavior. The collision rule above must hold under SSA so a mismatched pre-existing object is never force-adopted.

## :material-book-open-page-variant-outline: Ownership, Deletion, And Retention

The deletion contract is already recorded at a policy level in [runtime-contract.md](runtime-contract.md): deleting the `CoriolisAppliance` removes operator-owned workloads, Services, Jobs, and generated ConfigMaps, while retaining PVCs, CA state, and state credentials for recovery, and never deleting pre-existing referenced Secrets.

### :material-application-edit-outline: Owner-Referenced (Garbage-Collected) Resources

Owner-reference (with `controller: true`, as the marker already does) the **ephemeral, rebuildable** resources so Kubernetes garbage-collects them on CR deletion:

- Deployments/StatefulSets where appropriate (runtime workloads).
- Services.
- Jobs (bootstrap and one-shot).
- Generated (non-secret, non-state) ConfigMaps.

These are safe to GC because their content is derived from the CR and can be reconstructed.

### :material-application-edit-outline: Retained Resources (Not Owner-Referenced)

Do **not** owner-reference resources whose deletion would destroy recoverable state. These must survive CR deletion:

- **Retained PVCs** (data volumes, e.g. MariaDB data, retained logger volume, CA volume).
- **CA state** (Step CA home under `/etc/step`, see `coriolis-docker/coriolis_ansible/roles/bootstrap/step-ca/tasks/setup_step_ca_container.yml`).
- **State credential Secrets** (generated retained state credentials; see [Secrets And Configuration](#secrets-and-configuration)).

Because these are not owner-referenced, they require **stable labels and annotations** for discovery and future explicit cleanup: the appliance identity labels above, plus a `coriolis.cloudbase.it/retention` annotation classifying the retained resource, and a stable deterministic name. A pure classification slice is now implemented locally (see [Current Status And Accuracy](#current-status-and-accuracy)) that authorizes automatic reuse of a retained resource only on an exact match of the deterministic name/namespace and every operator-controlled identity field, with no owner reference; it does not construct, reconcile, or adopt any runtime resource.

!!! note
    Automatic exact-match reattachment of a retained PVC/Secret/CA volume is authorized by the local `classify_retained_resource` slice only when every operator-controlled identity field matches exactly and the object has no owner references. It is a namespace trust boundary: anyone who can create resources in the namespace can forge the operator's identity metadata. This slice is committed locally at `1b73045` on `dev`, but **not pushed or deployed**; it constructs/reconciles nothing and performs no adoption mutations. Deployment/construction and any runtime adoption remain future, separately approved work.

### :material-application-edit-outline: External And Pre-Existing Secrets

External/pre-existing Secrets (notably the registry pull Secret `coriolis-appliance-registry`, type `kubernetes.io/dockerconfigjson`, used by pull validation per [image-inventory.md](image-inventory.md)) must **never be mutated or deleted** by the operator. They are referenced read-only.

### :material-application-edit-outline: Finalizer Policy

The initial design keeps **no destructive finalizer**. Deletion relies on Kubernetes owner-reference garbage collection plus the explicit retained-resource contract above. A future finalizer for explicit cleanup of retained resources is a later, separately approved design.

## :material-book-open-page-variant-outline: Secrets And Configuration

Secrets and generated configuration are divided into classes below. **This page never includes credential values**; it records source variable names, evidence, and the now-frozen object/key mapping only. All generated object names below are produced by `appliance_resource_name(<CR name>, <component>)` (single lowercase DNS label, 63-char cap, 12-char SHA-256 overflow), so each `<appliance>-<component>` name is the deterministic result of that helper for the given CR name.

### :material-application-edit-outline: Class 1 — Generated Retained State Credentials

These are **retained, ownerless, operator-generated** Secrets that persist across appliance deletion for recovery (see [Retained Resources](#retained-resources-not-owner-referenced)); they are authorized for exact-match reuse only under the retained classifier policy (see [Generated Credential Reuse](#generated-credential-reuse)). The following concrete Secret names and key layouts are **frozen**:

- **`<appliance>-coriolis-credentials`** (component `coriolis-credentials`) contains **exactly**:
  - `coriolis_database_password` — Coriolis DB user password (embedded in `coriolis.conf.j2` and used to create the `coriolis` DB user in `roles/coriolis/common/tasks/setup.yml`).
  - `coriolis_keystone_password` — Coriolis Keystone user password (embedded in the same template and used to create the Keystone user).
  - `temp_keypair_password` — in the `passwords.yml.sample` key schema and embedded in `coriolis.conf.j2`.

  Keys for the licensing server/UI, Metal Hub, and InfluxDB (`coriolis_licensing_server_database_password`, `coriolis_licensing_ui_database_password`, `coriolis_metal_hub_database_password`, `influxdb_admin_password`, `influxdb_user_password`) remain **absent and deferred** until those components are implemented; this contract does not invent their current object/key mapping.

- **`<appliance>-infrastructure-credentials`** (component `infrastructure-credentials`) contains **exactly**:
  - `database_password` — MariaDB administrative credential.
  - `rabbitmq_password` — RabbitMQ `openstack` user credential.
  - `keystone_admin_password` — Keystone `admin` credential.

  These preserve the local Kolla source names read from the host's `/etc/kolla/passwords.yml` by `coriolis-docker/coriolis_ansible/library/kolla_deployment_facts.py` (which exposes MariaDB, RabbitMQ, and Keystone connection facts); they are **not** defined in `coriolis-docker/passwords.yml.sample`. No Barbican or Memcached password is invented because no authoritative evidence exists.

- **`<appliance>-step-ca-credentials`** (component `step-ca-credentials`) contains **exactly** `init_password` (generated with `openssl rand -base64 32` and stored at `/etc/step/init_password` per `coriolis-docker/coriolis_ansible/roles/bootstrap/step-ca/tasks/setup_step_ca_container.yml`). The broader `/etc/step` CA state belongs on retained storage; the TLS/private-key Secret key layout remains **deferred**.

The database and Keystone user/service names are authoritative: `coriolis_database_name: "coriolis"`, `coriolis_database_user: "coriolis"`, `coriolis_keystone_user: "coriolis"` (`coriolis-docker/coriolis_ansible/roles/coriolis/common/vars/main.yml`).

!!! warning
    This page records **key names only**, never values. In legacy/source Ansible deployments, values are supplied from external per-deploy inputs (the per-deploy `config.yml` / Kolla passwords file) and are never part of this repository. In contrast, this approved Kubernetes operator contract is **operator-generated only**: no inline CR credential values and no external credential Secret source. Each frozen key is independently generated by the pure local helpers with `secrets.token_urlsafe(32)` (32 random bytes/256 bits, URL-safe opaque string); injected token factories are deterministic-test-only, and invalid results fail without value exposure. Values compose unchanged with builders and must never appear in documentation, logs, status, events, or errors.

### :material-application-edit-outline: Sensitive Rendered Configuration Split

`coriolis.conf.j2` embeds credential values directly: RabbitMQ in the transport URL (`coriolis.conf.j2:3`), MariaDB connection (`coriolis.conf.j2:49`), Keystone authtoken password (`coriolis.conf.j2:56`), trustee password (`coriolis.conf.j2:66`), and the temp-keypair password (`coriolis.conf.j2:121`).

The primary `coriolis.conf` split is now **frozen**: a complete rendered `coriolis.conf` must never be placed in a ConfigMap. It is held in the rebuildable configuration Secret `<appliance>-coriolis-config-secret` (key `coriolis.conf`) and mounted with the generated ConfigMap as a projected volume (see [Configuration Mount](#configuration-mount)). This page still does not claim that every other template rendered by `roles/coriolis/common/tasks/setup.yml` is safe as a ConfigMap.

#### Sensitive `coriolis.conf` Rendering Contract

The sensitive renderer is implemented locally at `9bb20f3`. `SensitiveCoriolisEndpoints`, `SensitiveCoriolisCredentials`, redacted one-key `SensitiveCoriolisConfig`, and `render_sensitive_coriolis_config` produce exactly one `coriolis.conf` key for the owner-referenced, rebuildable `<appliance>-coriolis-config-secret`; the output composes directly with the existing configuration-Secret builder and is rejected by the ConfigMap boundary. Rendered content must never be placed in metadata, status, events, logs, or documentation.

!!! warning
    The complete rendered file embeds credentials. Its bytes and any value-bearing render context are secret material. Do not log, status, event, inspect, or serialize them outside the Secret payload.

The renderer packages verbatim the Apache-2.0 upstream base template `coriolis_ansible/roles/coriolis/common/templates/coriolis.conf.j2` and all provider fragments below, using Jinja2 `PackageLoader`, `StrictUndefined`, and `autoescape=False`. It renders deterministically with the upstream trailing newline; packaged sources retain source and Apache-2.0 license attribution.

Initial provider policy freezes the `group_vars/all.yml:74-103` lists and module maps without custom overrides:

| Frozen item | Exact initial value |
| --- | --- |
| Export providers, in order | `openstack`, `oracle-vm`, `opc`, `azure`, `scvmm`, `vmware`, `aws`, `metal`, `ovirt`, `nutanix` |
| Import providers, in order | `openstack`, `oracle-vm`, `opc`, `azure`, `scvmm`, `oci`, `aws`, `vmware`, `ovirt`, `kubevirt`, `lxd`, `proxmox`, `libvirt`, `cloudstack` |
| Provider union, first-seen order | `openstack`, `oracle-vm`, `opc`, `azure`, `scvmm`, `vmware`, `aws`, `metal`, `ovirt`, `nutanix`, `oci`, `kubevirt`, `lxd`, `proxmox`, `libvirt`, `cloudstack` |
| Export module map | `openstack=coriolis_provider_openstack.ExportProvider`; `oracle-vm=coriolis_provider_oracle_vm.ExportProvider`; `opc=coriolis_provider_opc.ExportProvider`; `azure=coriolis_provider_azure.ExportProvider`; `scvmm=coriolis_provider_scvmm.HyperVExportProvider`; `vmware=coriolis_provider_vmware_vsphere.ExportProvider`; `aws=coriolis_provider_aws.ExportProvider`; `metal=coriolis_provider_metal.ExportProvider`; `ovirt=coriolis_provider_ovirt_olvm.ExportProvider,coriolis_provider_ovirt_rhev.ExportProvider`; `nutanix=coriolis_provider_nutanix.ExportProvider` |
| Import module map | `openstack=coriolis_provider_openstack.ImportProvider,coriolis_provider_vhi.ImportProvider`; `oracle-vm=coriolis_provider_oracle_vm.ImportProvider`; `opc=coriolis_provider_opc.ImportProvider`; `azure=coriolis_provider_azure.ImportProvider`; `scvmm=coriolis_provider_scvmm.ImportProvider`; `oci=coriolis_provider_oci.ImportProvider,coriolis_provider_opca.ImportProvider,coriolis_provider_o3c.ImportProvider`; `aws=coriolis_provider_aws.ImportProvider`; `vmware=coriolis_provider_vmware_vsphere.ImportProvider`; `ovirt=coriolis_provider_ovirt_olvm.ImportProvider,coriolis_provider_ovirt_rhev.ImportProvider`; `kubevirt=coriolis_provider_kubevirt.ImportProvider,coriolis_provider_harvester.ImportProvider`; `lxd=coriolis_provider_lxd.ImportProvider`; `proxmox=coriolis_provider_proxmox.ImportProvider`; `libvirt=coriolis_provider_libvirt.ImportProvider`; `cloudstack=coriolis_provider_cloudstack.imp.ImportProvider` |

All 16 upstream fragments are required, in provider-union order: `openstack.conf.j2`, `oracle-vm.conf.j2`, `opc.conf.j2`, `azure.conf.j2`, `scvmm.conf.j2`, `vmware.conf.j2`, `aws.conf.j2`, `metal.conf.j2`, `ovirt.conf.j2`, `nutanix.conf.j2`, `oci.conf.j2`, `kubevirt.conf.j2`, `lxd.conf.j2`, `proxmox.conf.j2`, `libvirt.conf.j2`, and `cloudstack.conf.j2`. Custom import/export module overrides are not allowed initially. The only provider-fragment Jinja inputs are `coriolis_vmware_vix_disklib_dir` and `coriolis_vmware_vix_disklib_config_path`; provider endpoint credentials and private material remain endpoint data and deferred, not renderer inputs invented here. Template loop variables are internal and are not caller inputs.

Initial compression is fixed to `compress_transfers=False` and `enable_coriolis_compressor=False`. The renderer therefore omits `compressor_address`; compressor runtime remains deferred.

The base-template input model is frozen as follows:

| Input group | Frozen values or sources |
| --- | --- |
| Retained credentials | `rabbitmq_password` from infrastructure credentials; `coriolis_database_password`, `coriolis_keystone_password`, and `temp_keypair_password` from Coriolis credentials. `database_password` and `keystone_admin_password` must not be fed into this file. |
| Non-sensitive dependency inputs | Internal future-orchestration inputs only, not CRD fields: `rabbitmq_host`, `rabbitmq_port`, `memcached_host`, `database_host`, `keystone_protocol`, `keystone_host`, `keystone_public_port`, and `keystone_internal_port`. |
| Fixed/source-audited values | `rabbitmq_user="openstack"`; `coriolis_debug=true`; `coriolis_database_name="coriolis"`; `coriolis_database_user="coriolis"`; `coriolis_keystone_user="coriolis"`; Memcached port `11211` is literal in the template; `coriolis_config_dir="/etc/coriolis"`; `coriolis_log_dir="/var/log/coriolis"`; `coriolis_export_dir="/opt/coriolis/export"`; `coriolis_locks_dir_containers="/opt/coriolis/locks"`; `coriolis_policy_file="/etc/coriolis/policy.yml"`; `coriolis_vmware_vix_disklib_dir="/opt/coriolis/vmware-vix-disklib"`; `coriolis_vmware_vix_disklib_config_path="/etc/coriolis/vixdisklib.conf"`; CA references resolve under `/etc/coriolis/ssl/ca/coriolis-ca.crt`. |
| Derived values | `coriolis_export_providers`, `coriolis_import_providers`, `coriolis_providers`, `merged_export_modules`, and `merged_import_modules` are exactly the frozen lists, first-seen union, and module maps above. `item`, `provider`, and Jinja loop metadata are renderer-internal, not caller inputs. |

!!! note
    CA path rendering is frozen, but CA material and bootstrap are deferred. The renderer does not establish the referenced CA file.

The implementation uses exact typed internal schemas: required non-empty strings; ports in `1..65535`; Keystone protocol restricted to `http` or `https`; and required non-empty credentials. It rejects CR, LF, and NUL injection; credential and output reprs are redacted; errors are fixed/category-only; inputs are not mutated; and missing, extra, or undefined values fail closed. This contract defines no CRD fields.

The implemented interface separates `SensitiveCoriolisEndpoints` from `SensitiveCoriolisCredentials`; future runtime orchestration must construct the credential record only from successful `FoundationalResourcePreflight`. `SensitiveCoriolisConfig` is a credential-redacted one-key Mapping before composition into the existing configuration-Secret builder.

The three generated credential Secrets `<appliance>-coriolis-credentials`, `<appliance>-infrastructure-credentials`, and `<appliance>-step-ca-credentials` all carry exactly `coriolis.cloudbase.it/retention: state-credentials`. Their component labels remain distinct. This closes the retention-class gap as policy only; it changes no code or runtime behavior.

Non-goals: no runtime Kubernetes read/write, SSA, RBAC, status, CRD, dependency Service/workload, database/Keystone bootstrap, TLS/CA material, optional component credential, provider connection credential/private material, storage, probe, readiness, rotation, or deployment change.

### :material-application-edit-outline: Class 2 — Generated/Rebuildable Configuration

Generated configuration is derived from the CR, can be regenerated, and is owner-referenced so it is garbage-collected with the CR. It is **not** a retained credential store: retained credential values live only in the Class 1 Secrets, and this class is rebuilt from them.

- **ConfigMap `<appliance>-coriolis-config`** (component `coriolis-config`), owner-referenced and rebuildable, contains **exactly** these source-audited non-secret files/keys:
  - `coriolis-api.wsgi`
  - `wsgi-coriolis.conf`
  - `vixdisklib.conf`
  - `api-paste.ini`
  - `policy.yml`
  - `coriolis.release`

  Forbidden in this ConfigMap: `coriolis.conf`, provider fragments, credentials, tokens, private keys, and registry auth. The 16 source-audited provider sections frozen above remain part of the sensitive `coriolis.conf` Secret and are not ConfigMap-safe; provider connection data, private material, and any standalone provider-generated files remain deferred.

- **Secret `<appliance>-coriolis-config-secret`** (component `coriolis-config-secret`), owner-referenced and rebuildable, contains **exactly** the key `coriolis.conf` with the complete rendered configuration (it embeds credentials). It is garbage-collected with the CR and regenerated from retained credentials; it is not a retained credential store.

### :material-application-edit-outline: Configuration Mount

Workloads mount the generated ConfigMap `<appliance>-coriolis-config` and the configuration Secret `<appliance>-coriolis-config-secret` together as a single read-only projected volume at `/etc/coriolis`, with explicit `items` paths for each file/key, **no credential environment variables**, and **no `subPath`**. The exact workload rollout/reload mechanism for credential rotation remains deferred, but sensitive values must never appear in Pod template annotations, labels, status, events, or logs.

### :material-application-edit-outline: Generated Credential Reuse

Retained credential values are generated **once** and then reused only under the exact-match retained classifier policy: future runtime reconciliation calls each generator only when its retained Secret is `ABSENT`, reuses an exact matching ownerless retained Secret unchanged, and fails closed on collisions. This is frozen policy, not current runtime behavior: `main.py` does not call generators. Automatic rotation remains deferred. The pure helpers `generate_coriolis_credentials`, `generate_infrastructure_credentials`, and `generate_step_ca_credentials` are committed locally at `a604579` but not pushed or deployed. The external `coriolis-appliance-registry` Secret remains read-only and is never copied into operator-owned Secrets.

### :material-application-edit-outline: Retained Secret Semantic Shape

For each Class 1 retained credential Secret, persisted content is frozen as follows:

- It may be a mapping-shaped object or a Kubernetes `V1Secret` model.
- `apiVersion` and `kind` may be absent; if present, they must be `v1` and `Secret` respectively.
- `type` must be `Opaque`; persisted `stringData` is forbidden.
- `data` must contain exactly the frozen key set for that Secret, with string encoded values only.
- Each encoded value must strictly base64-decode, UTF-8-decode, and produce a non-empty decoded string.

`validated_retained_secret_values` implements this pure validation/extraction contract locally and is **committed at `5165629` on `dev`, but not pushed or deployed**. It returns a new decoded mapping without mutating input. Its fixed/category-only errors reveal neither encoded nor decoded values.

This helper does **not** classify metadata, read or write Kubernetes resources, generate values, apply SSA, handle collisions/status, or reconcile. The pure preflight now classifies metadata first, then validates these semantics; any semantic failure maps fail-closed to `COLLISION`. Returned decoded values remain internal and must never be logged, statused, or evented.

### :material-application-edit-outline: Class 3 — External References

Secrets the operator references read-only and must never own:

- `coriolis-appliance-registry` (registry pull Secret, `kubernetes.io/dockerconfigjson`) — existing external Secret.
- Any other pre-existing Secret provided by the environment.

### :material-application-edit-outline: Remaining Unresolved Secret Items

The following remain **deferred/unresolved** and must not be guessed:

- The deployed **values** of the `passwords.yml.sample` keys (supplied from outside version control).
- The TLS/private-key Secret layout for CA material beyond the Step CA `init_password` key.
- Optional component credentials (licensing server/UI, Metal Hub, InfluxDB).
- Provider-fragment Secret-backed layout/classification.

These remain open until the relevant component or source of truth is provided.

## :material-book-open-page-variant-outline: Foundational Reconciliation Failure And Marker Contract

This is a documentation-only contract. It implements no runtime reads, writes, RBAC, SSA, status retry behavior, or runtime resource construction; deployed `0.5.3` remains marker-only.

!!! warning
    Credential values, rendered Secret/configuration content, and encoded or decoded values must never appear in status, errors, events, or logs. Failure messages are fixed/category-only and may identify only the operation, kind, namespace, and name; never an API exception text/body/headers.

### :material-application-edit-outline: Ordered Read And Apply Sequence

Each reconciliation validates profile/version before CoreV1 API work, then pre-reads and classifies the existing marker. It next pre-reads all five foundational resources in this canonical order: retained `<appliance>-coriolis-credentials` Secret, retained `<appliance>-infrastructure-credentials` Secret, retained `<appliance>-step-ca-credentials` Secret, `<appliance>-coriolis-config` ConfigMap, and `<appliance>-coriolis-config-secret` Secret. A `404` for any pre-read is `ABSENT`; every other read failure stops before resource mutation.

The existing pure five-resource preflight runs over the complete observed set; any stable classification or semantic collision stops with no writes. Before the first resource write, reconciliation completes retained credential reuse/generation and all rendering, then constructs all five desired manifests in memory.

Resource operations use the same canonical order: retained `REUSE` is no-write; `ABSENT` uses collision-safe create; owned `ABSENT` uses collision-safe create; and owned `MANAGED` uses guarded SSA. The marker operation is last. Successful status is written only after marker success.

### :material-application-edit-outline: Atomicity And Race Safety

The pre-read, preflight, and preparation barrier prevents writes until the complete set is known valid and desired. This is not a Kubernetes multi-object transaction: there is no rollback, compensation, or deletion. On the first failed operation, retain earlier successful operations, skip every later operation, and leave an initially absent marker absent or a prior marker unchanged/stale. A framework retry starts from fresh reads and converges idempotently. Status patching is observability, not part of foundational-resource atomicity.

`ABSENT` uses create semantics; `AlreadyExists` requires retry and fresh preflight. Existing managed or legacy resources require optimistic concurrency against their observed resource version; `Conflict` requires retry and fresh preflight. Force SSA can resolve field ownership only after identity, ownership/retention, classification, and concurrency checks; it never bypasses them. Retained `REUSE` is never written. This does not provide cross-object snapshot isolation; later drift requires another reconciliation.

### :material-application-edit-outline: Failure Status And Marker Meaning

| Condition | Result |
| --- | --- |
| Non-`404` marker or foundational pre-read failure | `ResourceReadFailed`; no resource writes; marker unchanged; publish sanitized failure status, then retry. |
| Foundational create or SSA failure | `ResourceApplyFailed`; stop later operations; marker unchanged; publish sanitized failure status, then retry. |
| Marker create or SSA failure | `MarkerApplyFailed`; foundational writes remain; marker is unchanged or absent; publish sanitized failure status, then retry. |
| Stable classification or semantic collision | Preserve `ResourceCollision` with no mutation; do not convert it to a transient API failure. |
| Success | Apply marker last, then publish the existing accepted API-only status. |

For every retryable failure, set `Accepted=True/Accepted`, `Progressing=True/Retrying`, `Reconciled=False` with the category reason, `Ready=False` with the category reason, `Degraded=True` with the category reason, and `Upgradeable=False/UpgradeNotSupported`. Advance `observedGeneration`, preserve a valid prior `acceptedVersion`, and do not establish `acceptedVersion` on an initially failed reconcile. Use framework-managed retry/backoff only: no manual loop or sleep.

The marker is a successful-completion record for foundational reconciliation at its stored generation. It is not workload readiness, a multi-object transaction, or proof against later drift. Set `Reconciled=True` only after marker success; `Ready` remains `False/RuntimeNotImplemented`. Never delete or downgrade a prior marker after a later failure.

!!! note
    Future tests must cover the frozen marker-plus-five-resource read/apply sequence, including every stop point, retry from fresh reads, retained no-write reuse, collision, `AlreadyExists`, conflict, marker-last behavior, and value-safe statuses.

### :material-application-edit-outline: Next Implementation Gate

Implement collision-safe runtime reads and create/guarded-SSA operations, minimal Secret/ConfigMap `get`/`create`/`patch` RBAC, status-then-Kopf-retry wiring, and exhaustive tests. Retained adoption/runtime construction and TLS/provider private data/bootstrap/storage/readiness/rotation remain deferred.

## :material-book-open-page-variant-outline: Dependency And Resource Plan

### :material-application-edit-outline: Evidence Inventory

The bootstrap play `coriolis-docker/coriolis_ansible/bootstrap.yml` imports `coriolis/common` (tasks_from `bootstrap`) then `bootstrap/step-ca` (tasks_from `deploy`). Independently, the appliance play `coriolis-docker/coriolis_ansible/appliance.yml` begins its appliance roles with `common/mariadb`, then `coriolis/compressor`, `coriolis/common`, then the Coriolis components (`logger`, `api`, `conductor`, `transfer-cron`, `scheduler`, `minion-manager`, `deployer-manager`, `worker`, `web`, `web-proxy`, `licensing-server`, `console-editor`, `metal-hub`), a separate validation play, and finally `licensing-ui`.

The Kolla connection facts (`coriolis-docker/coriolis_ansible/library/kolla_deployment_facts.py`) and `coriolis.conf.j2` demonstrate that the appliance **depends on** MariaDB (port `3306`), RabbitMQ (`5671`), Keystone (`5000`), Barbican (`9311`), and Memcached (`11211`) by consuming their endpoints and credentials. This proves dependency **endpoints**, not the Kubernetes creation/readiness order of RabbitMQ/Keystone/Memcached/Barbican. Step CA is invoked by `bootstrap.yml` to provide TLS certificates.

### :material-application-edit-outline: Proposed Implementation Ordering (Requires Approval)

The following is **proposed policy** requiring explicit approval and a readiness design. It is an evidence-informed ordering, **not** a proven readiness sequence and **not** a Kubernetes Job design:

1. **Step CA** — CA bootstrap and certificate state (from `bootstrap.yml` ordering).
2. **MariaDB** — the first appliance role in `appliance.yml`; provides the database the Coriolis role creates (`coriolis` DB/user).
3. **Kolla infrastructure services** — RabbitMQ, Keystone, Memcached, Barbican. Evidence shows Coriolis consumes their endpoints, but their creation/readiness order is **not proven** and requires design.
4. **`coriolis/common`** — creates the `coriolis` database/user, Keystone user/service/endpoints, and renders the Coriolis configuration.
5. **Coriolis runtime components** — api, conductor, transfer-cron, scheduler, minion-manager, deployer-manager, worker, web, web-proxy (plus deferred compressor/licensing/logger/console-editor/metal-hub per [runtime-contract.md](runtime-contract.md)).

!!! note
    Mapping this ordering to Jobs, probes, and readiness checks is a later milestone and remains out of scope here.

## :material-book-open-page-variant-outline: Out Of Scope

The following are intentionally out of scope for runtime implementation and are not decided here: full dependency/workload construction, runtime retained-resource pre-reads/reconciliation/RBAC, cluster validation, and Kubernetes Job design. The pure Secret/ConfigMap builders are committed locally at `050f16e`, the pure credential generators at `a604579`, semantic validation/extraction at `5165629`, non-sensitive rendering at `97153a7`, foundational preflight at `35eac9b`, and sensitive rendering at `9bb20f3`; the foundational names, key layouts, operator-generated-only policy, generation algorithm, persisted retained Secret semantics, rendering boundaries, safe preflight ordering, and failure/status/atomicity/marker-last contract are frozen above. Recorded ports, commands, probes, volumes, storage sizes, and readiness checks are **not guessed** here; they are listed as unresolved and require authoritative evidence.

## :material-book-open-page-variant-outline: Current Status And Accuracy

- The API slice is now **committed locally at `ab9df83`** (branch `dev`, ahead of `origin/dev` by one commit); it is **not pushed and not deployed**. The deployed operator `0.5.3` remains marker-only.
- The local **metadata-only helper slice** is implemented and validated (44 unit tests, `mypy src`, Ruff, `git diff --check`) and is **committed locally at `fbab6e5`** on `dev`, but is **not pushed and not deployed**. It adds `appliance_resource_name`, `appliance_identity`, and `build_resource_metadata` in `coriolis-operator/src/coriolis_operator/reconcile.py`, and routes the marker ConfigMap through `build_resource_metadata` (standard labels/annotation) while retaining its shipped `state_config_map_name`. The deployed marker `0.5.3` is unchanged and carries no standard labels.
- The local **collision/migration marker API-layer slice** is implemented and validated (70 unit tests; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; `git diff --check`) and is **committed locally at `d8df00f` on `dev`, but not pushed or deployed**. Reconciliation pre-reads the marker ConfigMap before SSA: a 404 creates normally; a fully matching managed marker reconciles; a compatible legacy `0.5.2`/`0.5.3`-shaped marker (no management signature, matching controller owner reference, compatible `acceptedVersion`/`profile`) is normalized in place under the unchanged shipped marker name with its stale generation updated; and partial/conflicting standard metadata, owner mismatch, incompatible legacy data, or owner-plus-retention metadata is a `ResourceCollision` that never patches, adopts, deletes, or renames the object and reports `Accepted=True`, `Progressing`/`Reconciled`/`Ready=False`, `Degraded=True`, `Upgradeable=False`, preserving a prior `acceptedVersion` but not newly establishing one. Non-404 read failures still propagate; SSA content type, field manager, `force=True`, marker data, naming, and patch-error behavior are unchanged; ConfigMap RBAC gains only `get`.
- The pure **retained-resource authorization/classification slice** is implemented and validated locally (94 unit tests; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; `git diff --check`) and is **committed locally at `1b73045` on `dev`, but not pushed or deployed**. `classify_retained_resource` in `coriolis-operator/src/coriolis_operator/reconcile.py` returns `RetainedClassification.ABSENT/REUSE/COLLISION`: an absent resource is eligible for creation; a retained resource (PVC, state Secret, CA state) is reused only on an exact match of name/namespace and every operator-controlled identity field (full appliance-name annotation, standard managed/identity labels, component label, exact retention annotation/class) with **no owner references** (owner plus retention is a collision even if an owner UID matches), permitting unrelated extra labels/annotations, while missing/partial/conflicting identity metadata is a collision and is never normalized; a matching ownerless retained object is `REUSE` with no mutation or adoption patching. The creating appliance CR UID is deliberately **not** part of the identity: retained resources survive CR deletion/recreation, so exact-match reattachment works even when the CR UID changes, and any stale `coriolis.cloudbase.it/appliance-uid` annotation is ignored as unrelated. This is a namespace trust boundary (in-namespace users can forge identity metadata). External/pre-existing resources, especially the registry pull Secret `coriolis-appliance-registry` (`EXTERNAL_READ_ONLY_RESOURCES`), fail closed as `COLLISION` (even absent or with forged matching metadata) and remain read-only and outside this classifier/reconciliation policy. The classifier is pure and works with mapping-shaped fakes and real `V1Secret`/`V1PersistentVolumeClaim` model objects. **No runtime resource is constructed, reconciled, patched, read, or adopted, and no adoption mutations exist.**
- The documentation-only **Secret/configuration contract slice** is **committed locally at `8ce26ba` on `dev`, but not pushed or deployed** and is absent from the deployed marker-only `0.5.3`. It freezes the foundational Secret/ConfigMap names, key layouts, and the primary `coriolis.conf` split (see [Secrets And Configuration](#secrets-and-configuration)): retained, ownerless Secrets `<appliance>-coriolis-credentials`, `<appliance>-infrastructure-credentials`, and `<appliance>-step-ca-credentials`; and the owner-referenced, rebuildable ConfigMap `<appliance>-coriolis-config` and configuration Secret `<appliance>-coriolis-config-secret`, mounted together at `/etc/coriolis`. It is documentation-only: no code, builders, values, RBAC, CRD, runtime resource, or reconcile behavior changed, and no credential values were included. Later pure slices close its concrete manifest, generated-value, persisted Secret semantic, ConfigMap rendering, preflight-ordering, and sensitive rendering gates. The multi-resource runtime failure/apply/reconciliation-status/atomicity/marker-last policy is now frozen; runtime work remains collision-safe pre-reads and create/guarded SSA for all five resources plus the marker, minimal Secret/ConfigMap RBAC, status-then-Kopf-retry wiring, and exhaustive tests.
- The pure **Secret/ConfigMap builder slice** is implemented and validated locally and is **committed locally at `050f16e` on `dev`, but not pushed or deployed**. `build_coriolis_credentials_secret`, `build_infrastructure_credentials_secret`, `build_step_ca_credentials_secret`, `build_coriolis_config_map`, and `build_coriolis_config_secret` use deterministic names and standard metadata: retained credential Secrets are ownerless with retention metadata, while rebuildable configuration resources are owner-referenced without retention. They enforce exact frozen key sets; preserve caller-provided opaque string inputs; fail missing, extra, or non-string inputs without exposing values; generate `Opaque` Secret manifests with UTF-8/base64 `data` and no `stringData`; and keep ConfigMap data plain and restricted to the six approved files, excluding `coriolis.conf` and credentials. Validation passed: 116 total tests (22 new cases from the previous 94; 21 matched the final focused selector), Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. This is pure manifest construction only: no credential generation, `main.py` reconciliation, Kubernetes reads/SSA, RBAC, CRD, runtime resource creation, status/readiness, or deployment change. The deployed `0.5.3` remains marker-only.
- The pure **retained credential generation slice** is implemented and validated locally and is **committed locally at `a604579` on `dev`, but not pushed or deployed**. `generate_coriolis_credentials`, `generate_infrastructure_credentials`, and `generate_step_ca_credentials` independently generate all seven frozen keys with `secrets.token_urlsafe(32)` (32 random bytes/256 bits, URL-safe opaque strings). Token-factory injection is deterministic-test-only; empty/non-string results fail value-safely. Values compose unchanged with builders and must never appear in documentation, logs, status, events, or errors. This changes no runtime behavior: `main.py` does not call generators; there are no Kubernetes reads/writes/SSA, RBAC, CRD, runtime resources, status/readiness, chart/release, deployment, or rotation changes. Validation passed: 132 total tests (16 new from 116), focused tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`; deployed `0.5.3` remains marker-only.
- The pure **retained Secret semantic validation/extraction slice** is implemented and validated locally and is **committed locally at `5165629` on `dev`, but not pushed or deployed**. `validated_retained_secret_values` accepts mapping-shaped objects and Kubernetes `V1Secret` models; tolerates absent `apiVersion`/`kind` but rejects conflicting present values; requires `type: Opaque`; rejects persisted `stringData`; requires exact frozen string-valued encoded `data`; strictly base64-decodes then UTF-8-decodes; and rejects empty decoded values. It returns a new decoded mapping without input mutation, with fixed/category-only failures that expose no values. Validation passed: 152 total tests (20 new from 132), focused 20 tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. It performs no metadata classification, Kubernetes reads/writes, generation, SSA, collision/status handling, or reconciliation; no `main.py`, RBAC, CRD, runtime resource, chart/release, deployment, or rotation behavior changed, and deployed `0.5.3` remains marker-only.
- The pure **non-sensitive configuration rendering slice** is implemented and validated locally and is **committed locally at `97153a7` on `dev`, but not pushed or deployed**. `render_coriolis_config` validates explicit bind address, port, configuration directory, VixDiskLib log directory, and accepted version; Jinja2 `PackageLoader` with `StrictUndefined`, disabled autoescape, and preserved trailing newlines renders exactly the six frozen ConfigMap files. The six verbatim Apache-2.0 upstream templates are packaged with source/license attribution, and `accepted_version` maps to legacy `default_coriolis_docker_images_tag`. It renders no `coriolis.conf`, provider fragments, credentials, or Secret content. Validation passed: 19 focused renderer tests, 171 total tests, wheel resource inspection, and template byte comparison. No `main.py`, reconciliation, Kubernetes read/write, SSA, RBAC, CRD, runtime, release, or deployment behavior changed; deployed `0.5.3` remains marker-only.
- At **`574efcf`**, the documentation-only sensitive renderer contract froze the complete `coriolis.conf` as exactly one key in the owner-referenced configuration Secret; it is never ConfigMap, log, status, event, metadata, or documentation content. It also froze the immutable upstream base template plus all 16 provider fragments, authoritative provider lists/order/module maps, prohibited custom module overrides, disabled compression/compressor, explicit internal dependency inputs, source-audited identities/paths, credential mappings, strict value-safe validation, a redacted interface boundary, and no CRD fields. All three generated credential Secrets have exactly `coriolis.cloudbase.it/retention: state-credentials`.
- The pure **sensitive renderer slice** is implemented and validated locally and is **committed locally at `9bb20f3` on `dev`, but not pushed or deployed**. `SensitiveCoriolisEndpoints`, `SensitiveCoriolisCredentials`, redacted one-key `SensitiveCoriolisConfig`, and `render_sensitive_coriolis_config` compose the exact one-key output with the existing configuration-Secret builder and reject ConfigMap output. Inputs are frozen/exact and unmutated; credential/output reprs are redacted; errors are fixed/category-only and value-safe. Jinja `PackageLoader`, `StrictUndefined`, disabled autoescape, and trailing newline preserve the immutable base plus all 16 fragments. Validation passed: 40 focused configuration tests, 215 total tests, Ruff lint/format, strict mypy, Helm lint/template, `git diff --check`, 17/17 source byte parity, and offline wheel inspection of exactly 25 expected template resources. No `main.py`, reconciliation, Kubernetes reads/writes/SSA, RBAC, CRD, runtime resources/workloads, chart/release/image version, deployment, TLS/CA/bootstrap, provider/private data, optional credentials, storage, readiness, or rotation behavior changed; deployed `0.5.3` remains marker-only.
- The only Kubernetes behavior actually deployed today is the owned state ConfigMap via server-side apply (`coriolis-operator/src/coriolis_operator/main.py`, `reconcile.py`).
- Collision pre-read/enforcement and legacy marker migration for the marker ConfigMap are implemented locally (committed at `d8df00f`, not pushed/deployed); the pure retained-resource authorization/classification slice is implemented locally and committed at `1b73045` (not pushed/deployed; no runtime resources or adoption mutations exist); pure retained Secret semantic validation/extraction is committed at `5165629`, pure non-sensitive configuration rendering at `97153a7`, pure foundational preflight at `35eac9b`, and pure sensitive configuration rendering at `9bb20f3` (not pushed/deployed). The preflight remains valid for all five frozen resources: metadata classification first, retained Secret semantic validation second, semantic failure fail-closed to `COLLISION`, and generators only for corresponding `ABSENT` retained Secrets after collision-free validation. The multi-resource failure/status/atomicity/marker-last contract is frozen above, but no corresponding runtime behavior is implemented. The next runtime sequence is collision-safe pre-reads and create/guarded SSA for all five resources plus the marker, minimal Secret/ConfigMap RBAC, status-then-Kopf-retry wiring, and exhaustive tests. Retained-resource runtime construction/adoption remains separately deferred.
- **No runtime validation or readiness is claimed.**

## :material-book-open-page-variant-outline: Unresolved Gates

The prior unresolved **name/key** mapping and the primary **`coriolis.conf` split** gates are closed by the frozen contract in [Secrets And Configuration](#secrets-and-configuration). Remaining gates:

1. **Collision-safe Kubernetes runtime reads and create/guarded-SSA operations** for all five frozen resources, with minimal Secret/ConfigMap `get`/`create`/`patch` RBAC, status-then-Kopf-retry wiring, and exhaustive tests. The failure, reconciliation-status, atomicity, race-safety, and marker-last semantics are frozen above. The pure builders, generators, semantic helper, non-sensitive renderer, preflight, and sensitive renderer are committed locally at `050f16e`, `a604579`, `5165629`, `97153a7`, `35eac9b`, and `9bb20f3`; decoded values remain internal and must never be logged, statused, or evented.
2. **TLS/CA private material layout** beyond the Step CA `init_password` key.
3. **Optional component credentials** (licensing server/UI, Metal Hub, InfluxDB), provider connection credentials/private material, and any standalone provider-generated files beyond the 16 sections included in sensitive `coriolis.conf`.
4. **Exact storage sizes and volume layouts** for retained PVCs and CA state.
5. **Probes, readiness, and bootstrap sequencing** for the bootstrap and workload resources.
6. **Credential rotation rollout/reload mechanics** (the mount is frozen; rotation is not).
7. **Runtime construction/deployment and any adoption mutations for retained-resource reuse.** The pure authorization/classification policy (exact-match, ownerless, `classify_retained_resource`) is implemented locally and committed at `1b73045` (not pushed/deployed); translating it into runtime resource construction, reconciliation, and adoption remains future, separately approved work.
