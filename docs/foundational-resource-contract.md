# Foundational Resource Contract

This page freezes the foundational Kubernetes resource contracts for the `core` runtime profile: deterministic naming, metadata labels, ownership/deletion/retention, Secret/configuration classes, and a dependency evidence inventory with a proposed implementation ordering. It records **policy and evidence**, not implementation. A metadata-only helper slice (naming, identity, labels, and owner-or-retention metadata) and a pure retained-resource authorization/classification slice (`classify_retained_resource`) are now implemented locally; no retained resource, configuration, Secret, dependency, or workload **construction or deployment** is implemented, and nothing here changes deployed runtime behavior.

!!! note
    This is the first Milestone 4 deliverable, a documentation-only contract slice plus the now-implemented local metadata-only helper slice. The deployed operator `0.5.3` remains marker-only; the local API slice is committed at `ab9df83` but not pushed or deployed, the local metadata-only helper slice is committed at `fbab6e5` but not pushed or deployed, and the collision/migration marker API-layer slice is committed at `d8df00f` on `dev` but not pushed or deployed. The pure retained-resource authorization/classification slice is committed locally at `1b73045` on `dev`, but **not pushed or deployed** (see [Current Status And Accuracy](#current-status-and-accuracy)). A documentation-only Secret/configuration contract slice now freezes the foundational Secret/ConfigMap names and key layouts and the primary `coriolis.conf` split (see [Secrets And Configuration](#secrets-and-configuration)); it changes no runtime behavior or resources and is **committed locally at `8ce26ba` on `dev`, but not pushed or deployed** (see [Current Status And Accuracy](#current-status-and-accuracy)). A MariaDB vertical slice still follows only after the remaining gates below (actual generators/builders, RBAC, storage, probes/readiness, rotation) are approved.

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
    This page records **key names only**, never values. In legacy/source Ansible deployments, values are supplied from external per-deploy inputs (the per-deploy `config.yml` / Kolla passwords file) and are never part of this repository. In contrast, this approved Kubernetes operator contract **generates** the three retained Secret classes' values once and reuses them under the exact-match retained policy ([Generated Credential Reuse](#generated-credential-reuse)); operator-generated values are **not** supplied externally. No generation algorithm or length is documented because none is evidenced in source.

### :material-application-edit-outline: Sensitive Rendered Configuration Split

`coriolis.conf.j2` embeds credential values directly: RabbitMQ in the transport URL (`coriolis.conf.j2:3`), MariaDB connection (`coriolis.conf.j2:49`), Keystone authtoken password (`coriolis.conf.j2:56`), trustee password (`coriolis.conf.j2:66`), and the temp-keypair password (`coriolis.conf.j2:121`).

The primary `coriolis.conf` split is now **frozen**: a complete rendered `coriolis.conf` must never be placed in a ConfigMap. It is held in the rebuildable configuration Secret `<appliance>-coriolis-config-secret` (key `coriolis.conf`) and mounted with the generated ConfigMap as a projected volume (see [Configuration Mount](#configuration-mount)). This page still does not claim that every other template rendered by `roles/coriolis/common/tasks/setup.yml` is safe as a ConfigMap.

### :material-application-edit-outline: Class 2 — Generated/Rebuildable Configuration

Generated configuration is derived from the CR, can be regenerated, and is owner-referenced so it is garbage-collected with the CR. It is **not** a retained credential store: retained credential values live only in the Class 1 Secrets, and this class is rebuilt from them.

- **ConfigMap `<appliance>-coriolis-config`** (component `coriolis-config`), owner-referenced and rebuildable, contains **exactly** these source-audited non-secret files/keys:
  - `coriolis-api.wsgi`
  - `wsgi-coriolis.conf`
  - `vixdisklib.conf`
  - `api-paste.ini`
  - `policy.yml`
  - `coriolis.release`

  Forbidden in this ConfigMap: `coriolis.conf`, provider fragments, credentials, tokens, private keys, and registry auth. Provider fragments remain Secret-backed and deferred until individually classified; this contract does not assert they are safe.

- **Secret `<appliance>-coriolis-config-secret`** (component `coriolis-config-secret`), owner-referenced and rebuildable, contains **exactly** the key `coriolis.conf` with the complete rendered configuration (it embeds credentials). It is garbage-collected with the CR and regenerated from retained credentials; it is not a retained credential store.

### :material-application-edit-outline: Configuration Mount

Workloads mount the generated ConfigMap `<appliance>-coriolis-config` and the configuration Secret `<appliance>-coriolis-config-secret` together as a single read-only projected volume at `/etc/coriolis`, with explicit `items` paths for each file/key, **no credential environment variables**, and **no `subPath`**. The exact workload rollout/reload mechanism for credential rotation remains deferred, but sensitive values must never appear in Pod template annotations, labels, status, events, or logs.

### :material-application-edit-outline: Generated Credential Reuse

Retained credential values are generated **once** and then reused only under the exact-match retained classifier policy committed at `1b73045` (see [Retained Resources](#retained-resources-not-owner-referenced) and [Current Status And Accuracy](#current-status-and-accuracy)); generation algorithms/lengths not evidenced in source are not documented here. The external `coriolis-appliance-registry` Secret remains read-only and is never copied into operator-owned Secrets.

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

The following are intentionally out of scope for this contract slice and are not decided here: full dependency/workload construction, RBAC, status/readiness changes, cluster validation, Kubernetes Job design, and the actual Secret/ConfigMap generators/builders. The foundational Secret/ConfigMap names, key layouts, and the primary `coriolis.conf` split are frozen above; recorded ports, commands, probes, volumes, storage sizes, and readiness checks are **not guessed** here; they are listed as unresolved and require authoritative evidence.

## :material-book-open-page-variant-outline: Current Status And Accuracy

- The API slice is now **committed locally at `ab9df83`** (branch `dev`, ahead of `origin/dev` by one commit); it is **not pushed and not deployed**. The deployed operator `0.5.3` remains marker-only.
- The local **metadata-only helper slice** is implemented and validated (44 unit tests, `mypy src`, Ruff, `git diff --check`) and is **committed locally at `fbab6e5`** on `dev`, but is **not pushed and not deployed**. It adds `appliance_resource_name`, `appliance_identity`, and `build_resource_metadata` in `coriolis-operator/src/coriolis_operator/reconcile.py`, and routes the marker ConfigMap through `build_resource_metadata` (standard labels/annotation) while retaining its shipped `state_config_map_name`. The deployed marker `0.5.3` is unchanged and carries no standard labels.
- The local **collision/migration marker API-layer slice** is implemented and validated (70 unit tests; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; `git diff --check`) and is **committed locally at `d8df00f` on `dev`, but not pushed or deployed**. Reconciliation pre-reads the marker ConfigMap before SSA: a 404 creates normally; a fully matching managed marker reconciles; a compatible legacy `0.5.2`/`0.5.3`-shaped marker (no management signature, matching controller owner reference, compatible `acceptedVersion`/`profile`) is normalized in place under the unchanged shipped marker name with its stale generation updated; and partial/conflicting standard metadata, owner mismatch, incompatible legacy data, or owner-plus-retention metadata is a `ResourceCollision` that never patches, adopts, deletes, or renames the object and reports `Accepted=True`, `Progressing`/`Reconciled`/`Ready=False`, `Degraded=True`, `Upgradeable=False`, preserving a prior `acceptedVersion` but not newly establishing one. Non-404 read failures still propagate; SSA content type, field manager, `force=True`, marker data, naming, and patch-error behavior are unchanged; ConfigMap RBAC gains only `get`.
- The pure **retained-resource authorization/classification slice** is implemented and validated locally (94 unit tests; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; `git diff --check`) and is **committed locally at `1b73045` on `dev`, but not pushed or deployed**. `classify_retained_resource` in `coriolis-operator/src/coriolis_operator/reconcile.py` returns `RetainedClassification.ABSENT/REUSE/COLLISION`: an absent resource is eligible for creation; a retained resource (PVC, state Secret, CA state) is reused only on an exact match of name/namespace and every operator-controlled identity field (full appliance-name annotation, standard managed/identity labels, component label, exact retention annotation/class) with **no owner references** (owner plus retention is a collision even if an owner UID matches), permitting unrelated extra labels/annotations, while missing/partial/conflicting identity metadata is a collision and is never normalized; a matching ownerless retained object is `REUSE` with no mutation or adoption patching. The creating appliance CR UID is deliberately **not** part of the identity: retained resources survive CR deletion/recreation, so exact-match reattachment works even when the CR UID changes, and any stale `coriolis.cloudbase.it/appliance-uid` annotation is ignored as unrelated. This is a namespace trust boundary (in-namespace users can forge identity metadata). External/pre-existing resources, especially the registry pull Secret `coriolis-appliance-registry` (`EXTERNAL_READ_ONLY_RESOURCES`), fail closed as `COLLISION` (even absent or with forged matching metadata) and remain read-only and outside this classifier/reconciliation policy. The classifier is pure and works with mapping-shaped fakes and real `V1Secret`/`V1PersistentVolumeClaim` model objects. **No runtime resource is constructed, reconciled, patched, read, or adopted, and no adoption mutations exist.**
- The documentation-only **Secret/configuration contract slice** is **committed locally at `8ce26ba` on `dev`, but not pushed or deployed** and is absent from the deployed marker-only `0.5.3`. It freezes the foundational Secret/ConfigMap names, key layouts, and the primary `coriolis.conf` split (see [Secrets And Configuration](#secrets-and-configuration)): retained, ownerless Secrets `<appliance>-coriolis-credentials`, `<appliance>-infrastructure-credentials`, and `<appliance>-step-ca-credentials`; and the owner-referenced, rebuildable ConfigMap `<appliance>-coriolis-config` and configuration Secret `<appliance>-coriolis-config-secret`, mounted together at `/etc/coriolis`. It is documentation-only: no code, builders, values, RBAC, CRD, runtime resource, or reconcile behavior changed, and no credential values were included. The prior unresolved name/key and `coriolis.conf` split gates are closed; the remaining gates (generators/builders/reconcile reads/SSA/RBAC, TLS/CA layout, optional component credentials, storage, probes/readiness/bootstrap, rotation rollout) are unchanged.
- The only Kubernetes behavior actually deployed today is the owned state ConfigMap via server-side apply (`coriolis-operator/src/coriolis_operator/main.py`, `reconcile.py`).
- Collision pre-read/enforcement and legacy marker migration for the marker ConfigMap are implemented locally (committed at `d8df00f`, not pushed/deployed); the pure retained-resource authorization/classification slice is implemented locally and committed at `1b73045` (not pushed/deployed; no runtime resources or adoption mutations exist); retained-resource runtime construction/adoption and all runtime resource construction remain deferred; everything else in this document is frozen policy, a proposed implementation ordering, or an unresolved requirement.
- **No runtime validation or readiness is claimed.**

## :material-book-open-page-variant-outline: Unresolved Gates

The prior unresolved **name/key** mapping and the primary **`coriolis.conf` split** gates are closed by the frozen contract in [Secrets And Configuration](#secrets-and-configuration). Remaining gates:

1. **Actual generators/builders, reconcile reads, SSA application, and RBAC** for the frozen Secrets and ConfigMap above. This contract freezes naming, keys, and mount policy only; construction is separate, future work.
2. **TLS/CA private material layout** beyond the Step CA `init_password` key.
3. **Optional component credentials** (licensing server/UI, Metal Hub, InfluxDB) and provider-fragment Secret-backed classification.
4. **Exact storage sizes and volume layouts** for retained PVCs and CA state.
5. **Probes, readiness, and bootstrap sequencing** for the bootstrap and workload resources.
6. **Credential rotation rollout/reload mechanics** (the mount is frozen; rotation is not).
7. **Runtime construction/deployment and any adoption mutations for retained-resource reuse.** The pure authorization/classification policy (exact-match, ownerless, `classify_retained_resource`) is implemented locally and committed at `1b73045` (not pushed/deployed); translating it into runtime resource construction, reconciliation, and adoption remains future, separately approved work.
