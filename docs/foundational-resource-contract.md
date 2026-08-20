# Foundational Resource Contract

This page freezes the foundational Kubernetes resource contracts for the `core` runtime profile: deterministic naming, metadata labels, ownership/deletion/retention, Secret/configuration classes, and a dependency evidence inventory with a proposed implementation ordering. It records **policy and evidence**, not implementation. A metadata-only helper slice is now implemented locally (naming, identity, labels, and owner-or-retention metadata); no retained resource, configuration, Secret, dependency, or workload **construction or deployment** is implemented, and nothing here changes deployed runtime behavior.

!!! note
    This is the first Milestone 4 deliverable, a documentation-only contract slice plus the now-implemented local metadata-only helper slice. The deployed operator `0.5.3` remains marker-only; the local API slice is committed at `ab9df83` but not pushed or deployed, and the local metadata-only helper slice is committed at `fbab6e5` but not pushed or deployed. The collision/migration marker API-layer slice is implemented locally (committed at `d8df00f` on `dev`, not pushed or deployed; see [Current Status And Accuracy](#current-status-and-accuracy)); a MariaDB vertical slice follows only after the unresolved contracts below are approved.

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

Because these are not owner-referenced, they require **stable labels and annotations** for discovery and future explicit cleanup: the appliance identity labels above, plus a `coriolis.cloudbase.it/retention` annotation classifying the retained resource, and a stable deterministic name. **Stable names/labels permit discovery only**; they do not authorize a recreated same-name CR to automatically adopt/reattach a retained resource.

!!! warning
    Automatic reattachment/adoption of a retained PVC/Secret/CA volume by a recreated `CoriolisAppliance` is an **unresolved safety gate**. It is not promised by this contract and requires explicit authorization before implementation.

### :material-application-edit-outline: External And Pre-Existing Secrets

External/pre-existing Secrets (notably the registry pull Secret `coriolis-appliance-registry`, type `kubernetes.io/dockerconfigjson`, used by pull validation per [image-inventory.md](image-inventory.md)) must **never be mutated or deleted** by the operator. They are referenced read-only.

### :material-application-edit-outline: Finalizer Policy

The initial design keeps **no destructive finalizer**. Deletion relies on Kubernetes owner-reference garbage collection plus the explicit retained-resource contract above. A future finalizer for explicit cleanup of retained resources is a later, separately approved design.

## :material-book-open-page-variant-outline: Secrets And Configuration

Secrets are divided into three classes. **This page never includes credential values**; it records source variable names and evidence only. Concrete Kubernetes Secret names and key layouts remain unresolved.

### :material-application-edit-outline: Class 1 — Generated Retained State Credentials

Credentials that must persist across appliance deletion for recovery, held in retained Secrets (see retention above). Authoritative evidence of the key schema:

- **`coriolis-docker/passwords.yml.sample`** defines the authoritative credential key schema (names only, empty values): `coriolis_database_password`, `coriolis_keystone_password`, `coriolis_licensing_server_database_password`, `coriolis_licensing_ui_database_password`, `coriolis_metal_hub_database_password`, `influxdb_admin_password`, `influxdb_user_password`, `temp_keypair_password`.
- **Step CA init password** — generated with `openssl rand -base64 32` and stored at `/etc/step/init_password` (`coriolis-docker/coriolis_ansible/roles/bootstrap/step-ca/tasks/setup_step_ca_container.yml`). This is retained CA state.
- **Coriolis database user password** (`coriolis_database_password`) — embedded in `coriolis.conf.j2` and used to create the `coriolis` DB user in `roles/coriolis/common/tasks/setup.yml`.
- **Coriolis Keystone user password** (`coriolis_keystone_password`) — embedded in the same template and used to create the Keystone user.
- **`temp_keypair_password`** — in the key schema and embedded in `coriolis.conf.j2`.

The database and Keystone user/service names are authoritative: `coriolis_database_name: "coriolis"`, `coriolis_database_user: "coriolis"`, `coriolis_keystone_user: "coriolis"` (`coriolis-docker/coriolis_ansible/roles/coriolis/common/vars/main.yml`).

!!! warning
    `passwords.yml.sample` defines the **empty key schema only**; deployed values are supplied from outside version control (the per-deploy `config.yml` / Kolla passwords file) and are never part of this repository. The concrete Kubernetes Secret object names and exact key layout remain **unresolved**.

### :material-application-edit-outline: Sensitive Rendered Configuration Rule

`coriolis.conf.j2` embeds credential values directly: RabbitMQ in the transport URL (`coriolis.conf.j2:3`), MariaDB connection (`coriolis.conf.j2:49`), Keystone authtoken password (`coriolis.conf.j2:56`), trustee password (`coriolis.conf.j2:66`), and the temp-keypair password (`coriolis.conf.j2:121`).

Therefore a **complete rendered `coriolis.conf` must never be placed in a ConfigMap**. Any rendered file containing credentials must be mounted from a Secret or safely composed from Secret-backed values at mount time. The exact split between Secret-held and ConfigMap-held configuration, and the mount design, are **unresolved** and require a separately approved design. This page does not claim that all templates rendered by `roles/coriolis/common/tasks/setup.yml` are safe ConfigMaps.

### :material-application-edit-outline: Class 2 — Generated/Rebuildable Non-Secret Configuration

Configuration that is derived from the CR, can be regenerated, and is **verified non-sensitive**, and is therefore safe to owner-reference and garbage-collect (generated ConfigMaps). Authoritative evidence of what is rendered:

- Rendered from `roles/coriolis/common/tasks/setup.yml` via templates in `roles/coriolis/common/templates/`: `coriolis-api.wsgi`, `wsgi-coriolis.conf`, `coriolis.conf`, `vixdisklib.conf`, `api-paste.ini`, `policy.yml`, and the `coriolis.release` file.
- Each component also renders its own `Dockerfile.j2` and component-specific templates.

Per the sensitive-rendered-configuration rule above, each candidate file must be verified non-sensitive before it may be stored in a ConfigMap; `coriolis.conf` is explicitly excluded. The exact rendered ConfigMap names, keys, per-file granularity, and the non-sensitive split are **unresolved** (not yet decided).

### :material-application-edit-outline: Class 3 — External References

Secrets the operator references read-only and must never own:

- `coriolis-appliance-registry` (registry pull Secret, `kubernetes.io/dockerconfigjson`) — existing external Secret.
- Any other pre-existing Secret provided by the environment.

### :material-application-edit-outline: Unresolved Secret Items

The following have **no authoritative evidence** in the repository and must not be guessed:

- The concrete Kubernetes Secret object names and exact key layouts for Class 1 retained credentials (the Ansible host stores them as files and in `/etc/kolla/passwords.yml`, not as Kubernetes Secrets).
- The deployed values of the `passwords.yml.sample` keys (supplied from outside version control).
- The Secret for the generated CA/private key material beyond the Step CA init password evidence.
- The exact split/mount design between Secret-held and ConfigMap-held rendered configuration per the sensitive-rendered-configuration rule.

These remain open until a source of truth (per-deploy config, Kolla passwords file mapping, or an approved operator-owned generation design) is provided.

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

The following are intentionally out of scope for this contract slice and are not decided here: full dependency/workload construction, RBAC, status/readiness changes, cluster validation, Kubernetes Job design, and the complete Secret key layout. Recorded ports, commands, probes, volumes, storage sizes, configuration rendering details, and readiness checks are **not guessed** here; they are listed as unresolved and require authoritative evidence.

## :material-book-open-page-variant-outline: Current Status And Accuracy

- The API slice is now **committed locally at `ab9df83`** (branch `dev`, ahead of `origin/dev` by one commit); it is **not pushed and not deployed**. The deployed operator `0.5.3` remains marker-only.
- The local **metadata-only helper slice** is implemented and validated (44 unit tests, `mypy src`, Ruff, `git diff --check`) and is **committed locally at `fbab6e5`** on `dev`, but is **not pushed and not deployed**. It adds `appliance_resource_name`, `appliance_identity`, and `build_resource_metadata` in `coriolis-operator/src/coriolis_operator/reconcile.py`, and routes the marker ConfigMap through `build_resource_metadata` (standard labels/annotation) while retaining its shipped `state_config_map_name`. The deployed marker `0.5.3` is unchanged and carries no standard labels.
- The local **collision/migration marker API-layer slice** is implemented and validated (70 unit tests; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; `git diff --check`) and is **committed locally at `d8df00f` on `dev`, but not pushed or deployed**. Reconciliation pre-reads the marker ConfigMap before SSA: a 404 creates normally; a fully matching managed marker reconciles; a compatible legacy `0.5.2`/`0.5.3`-shaped marker (no management signature, matching controller owner reference, compatible `acceptedVersion`/`profile`) is normalized in place under the unchanged shipped marker name with its stale generation updated; and partial/conflicting standard metadata, owner mismatch, incompatible legacy data, or owner-plus-retention metadata is a `ResourceCollision` that never patches, adopts, deletes, or renames the object and reports `Accepted=True`, `Progressing`/`Reconciled`/`Ready=False`, `Degraded=True`, `Upgradeable=False`, preserving a prior `acceptedVersion` but not newly establishing one. Non-404 read failures still propagate; SSA content type, field manager, `force=True`, marker data, naming, and patch-error behavior are unchanged; ConfigMap RBAC gains only `get`.
- The only Kubernetes behavior actually deployed today is the owned state ConfigMap via server-side apply (`coriolis-operator/src/coriolis_operator/main.py`, `reconcile.py`).
- Collision pre-read/enforcement and legacy marker migration for the marker ConfigMap are implemented locally (committed at `d8df00f`, not pushed/deployed); retained-resource adoption and all runtime resource construction remain deferred; everything else in this document is frozen policy, a proposed implementation ordering, or an unresolved requirement.
- **No runtime validation or readiness is claimed.**

## :material-book-open-page-variant-outline: Unresolved Gates

1. Concrete Class 1 retained Secret names and key layouts (mapping the `passwords.yml.sample` key schema and generated CA material to Kubernetes Secrets).
2. Concrete generated ConfigMap names and keys for Class 2 configuration rendering, and the Secret/ConfigMap split and mount design for sensitive rendered configuration.
3. Exact storage sizes, volume layouts, probes, commands, and readiness checks for the bootstrap and workload resources.
4. **Authorization for automatic reattachment/adoption of retained resources by a recreated same-name CR** (not promised by this contract; requires explicit safety authorization).
