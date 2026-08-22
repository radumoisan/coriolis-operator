# Backlog

## Completed Locally: Bootstrap and Controller Skeleton

- Create the Python 3.12, uv, and Kopf project skeleton.
- Add the namespaced `CoriolisAppliance` CRD at `coriolis.cloudbase.it/v1alpha1` under `helm/crds/`.
- Implement namespace-scoped watching of the Helm release namespace.
- Reconcile an idempotent marker ConfigMap only.
- Set successful reconciliation status and keep `Ready=False` until an appliance runtime exists.
- Add focused validation for the first controller behavior.
- Build and run the container image locally as its non-root user.

## Completed: Controller Lifecycle Validation

- In `coriolis`, validated sample create, generation and observed generation, `Accepted=True`, `Reconciled=True`, and expected `Ready=False/RuntimeNotImplemented` status.
- Validated one owned `example-operator-state` marker with `development/1`, controller replacement/resume, ownership and marker uniqueness, and preserved condition transition times.
- Validated `spec.version` propagation to `development-updated/2` without changing transition times, then normal deletion and marker garbage collection. No sample CR or marker remains.

## Completed: Dev Deployment Validation

- Deploy release `0.5.2` through Argo CD in the approved `coriolis` namespace.
- Validate registry authentication through `regcred`, CRD installation, namespaced RBAC, operator startup, and liveness.
- Confirm the Argo application is `Synced` and `Healthy` and the operator Deployment is `1/1` available.

## Completed Locally: Consistency Sweep

- Align handler result delivery, collision-safe naming, standard conditions, CRD/RBAC scope, and the documented validation boundary.

## Documented: CIXpress CI/Release Model

- Record the CIXpress ordered Job pipeline, version policy, observability contract, and non-transactional release semantics.

## Completed: CIXpress Publication Validation

- Reran CI and confirmed registry publication plus `SUCCEEDED` states for all expected steps.

## Completed: Image Mirror And Pull Gate

- Mirrored all 26 approved images serially on 2026-08-20 to `cr.virtomat.io/virtomat/coriolis` with preserved and verified manifest digests via `scripts/mirror-images.py`: 15 application images at tag `2603.4`, 10 support images at `2023.1-ubuntu-jammy`, and Step CA at `2603.4` from its pinned source digest.
- The reusable utility holds source auth only in anonymous memfd storage, skips matching destinations, refuses conflicting tags, copies by digest with `--preserve-digests`, and verifies each destination digest. Harbor required a bounded retry for transient immediate post-push unauthorized responses.
- Independent destination verification of Step CA returned the exact expected digest.
- Kubernetes pull validation in `virt-infra-dev-buc-hq` namespace `coriolis` passed for all exact 21 historically selected image references (10 application images at `2603.4`, 10 support images at `2023.1-ubuntu-jammy`, and Step CA at `2603.4`), validated serially by `scripts/validate-image-pulls.py` using one short-lived Pod at a time with `imagePullPolicy: Always`, explicit context/namespace, and the destination Secret `coriolis-appliance-registry` (type `kubernetes.io/dockerconfigjson`); each successful Pod was removed and no pull-validation Pods remain. Independent main-agent validation of Step CA repeated successfully with the exact expected digest. Step CA and web-proxy are deferred from the current initial runtime; the historical image inventory and pull gate remain complete.

## Documented: Foundational Resource Contract Slice (Milestone 4, First Deliverable)

- Documented the first Milestone 4 deliverable in [docs/foundational-resource-contract.md](docs/foundational-resource-contract.md), based on authoritative local sources: `coriolis-docker/coriolis_ansible/appliance.yml`, `group_vars/all.yml`, `library/kolla_deployment_facts.py`, the `common/mariadb`, `coriolis/common`, `coriolis/api`, and `bootstrap/step-ca` roles/templates, plus `coriolis-oss` systemd/config sources.
- Froze deterministic naming (a conservative single 63-character DNS-label shape `<appliance>-<component>` with no dots, using the existing 12-character SHA-256 overflow principle; the current ConfigMap helper's subdomain-with-dots shape is not generalized), standard `app.kubernetes.io/*` labels plus a deterministic label-safe `coriolis.cloudbase.it/appliance` identity token with the full CR name in an annotation, collision handling (never adopt/overwrite a mismatched object), ownership/retention (owner-reference ephemeral resources; retain PVCs, CA state, and state credentials; never mutate external Secrets; no destructive finalizer), and three Secret/configuration classes plus an explicit sensitive-rendered-configuration rule (`coriolis.conf` embeds credentials and must never be a ConfigMap).
- Recorded a dependency **evidence inventory** (bootstrap.yml imports common bootstrap then Step CA; appliance.yml starts with MariaDB; Kolla facts prove dependency endpoints only) and a proposed implementation ordering that explicitly requires approval and a readiness design; it is not a proven readiness sequence or Kubernetes Job design. Automatic reattachment/adoption of retained resources by a recreated CR is recorded as an unresolved safety gate.
- Recorded unresolved gates instead of guessing: Class 1 retained Secret names/keys (mapping the `passwords.yml.sample` key schema), the Secret/ConfigMap split and mount design, storage sizes, probes, commands, and readiness checks. No credential values were included. No code, build, cluster, or Git metadata was changed.

## Completed Locally: Metadata-Only Helper Slice

- Implemented and validated the local metadata-only helper slice in `src/coriolis_operator/reconcile.py`: `appliance_resource_name` (single lowercase DNS label <=63; dotted/overflow names use a visible dot-to-hyphen prefix plus the first 12 SHA-256 characters of the full unmodified input), `appliance_identity` (label-safe appliance identity token), and `build_resource_metadata` (standard `app.kubernetes.io/*` and `coriolis.cloudbase.it/*` labels, full appliance-name annotation, and exactly one lifecycle mode — owner reference or retention annotation). Invalid appliance/component values are rejected.
- `build_state_config_map` now uses standard metadata with component `operator-state` but deliberately continues to use the shipped `state_config_map_name`, preserving the `0.5.2`/`0.5.3` names including dotted/long DNS-subdomain behavior. Marker data, the SSA call, `force=True`, API behavior, and status are unchanged.
- Validation passed: 44 unit tests; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `git diff --check` was clean before doc alignment. The metadata-only helper implementation is committed locally at `fbab6e5` on `dev`, but not pushed or deployed; the deployed marker `0.5.3` is unchanged and carries no standard labels.
- Collision pre-read/enforcement, legacy marker migration, and collision status were the next step and are now implemented locally in the separate collision/migration API-layer slice (see the section below); retained-resource adoption and all runtime resource construction remain deferred.

## Completed Locally: Core Runtime API Slice

- The `v1alpha1` CRD now has optional/defaulted `spec.profile: core` (enum only `core`), required non-empty `spec.version`, and optional non-empty `status.acceptedVersion`. No CEL/admission immutability was added: the controller enforces the immutable accepted version using the persisted `status.acceptedVersion`.
- Initial acceptance supports exact `2603.4`; an omitted profile defaults to `core`. Unsupported initial profiles/versions apply no Kubernetes resources and report rejection conditions (`Accepted=False`, `Reconciled=False`, `Ready=False/RuntimeNotImplemented`).
- A requested version different from `status.acceptedVersion` applies no resources, preserves the accepted state, advances `observedGeneration`, and reports `Accepted=False/VersionChangeRejected`, `Reconciled=False`, and `Upgradeable=False/UpgradeBlocked`.
- A valid API-only reconcile records only the owned controller-state ConfigMap (`acceptedVersion`, `profile`, `generation`) and reports all six conditions (Accepted, Progressing, Reconciled, Ready, Degraded, Upgradeable); `Ready=False/RuntimeNotImplemented` remains truthful, and profile changes route through the same reconcile path.
- The sample uses `profile: core`, `version: "2603.4"`. 25 tests pass; Ruff and mypy pass; Helm lint/template pass. No cluster or external service was changed by this API slice, and the image mirror/pull gate remains passed. No dependencies, bootstrap Jobs, services, storage, secrets, or Coriolis runtime workloads have been implemented or deployed.

## Completed Locally: Collision/Migration Marker API-Layer Slice

- Implemented the collision/migration marker API-layer slice in `src/coriolis_operator/reconcile.py` and `src/coriolis_operator/main.py`: reconciliation pre-reads the deterministic marker ConfigMap before server-side apply. A 404 creates normally; a fully matching managed marker (standard `app.kubernetes.io/*`/`coriolis.cloudbase.it/*` labels, appliance-name annotation, matching controller owner reference) reconciles; and a compatible legacy `0.5.2`/`0.5.3`-shaped marker — no management signature, matching controller owner reference, compatible `acceptedVersion`/`profile` — is normalized in place under the unchanged shipped marker name with its stale generation updated.
- Partial/conflicting standard metadata, owner mismatch, incompatible legacy data, or owner-plus-retention metadata is a `ResourceCollision`: the object is not patched, adopted, deleted, or renamed. Status keeps all six conditions with `Accepted=True`, `Progressing`/`Reconciled`/`Ready=False`, `Degraded=True`, and `Upgradeable=False/UpgradeNotSupported`; a prior `acceptedVersion` is preserved but not newly established on an initial collision. Non-404 Kubernetes read failures still propagate; SSA content type, field manager, `force=True`, marker data, naming, and existing patch-error behavior are unchanged.
- ConfigMap RBAC gains only `get` (no list/delete or broader permission).
- Validation passed: 70 unit tests; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; `git diff --check`. The slice is **committed locally at `d8df00f` on `dev`, but not pushed or deployed**; the deployed release `0.5.3` remains marker-only and unchanged and lacks these local pre-read/collision semantics. Existing local commit references remain accurate and now include the collision slice `d8df00f`: API slice `ab9df83`, metadata helper `fbab6e5`, status docs `6bf5cb5`.
- Retained-resource adoption remains an unresolved authorization safety gate and is NOT implemented; all runtime resources remain unimplemented/undeployed. A MariaDB vertical slice remains blocked by unresolved Secret/configuration/storage/readiness gates.

## Completed Locally: Retained-Resource Authorization/Classification Slice

- Implemented the pure retained-resource authorization/classification slice in `src/coriolis_operator/reconcile.py` only: `classify_retained_resource`, returning `RetainedClassification.ABSENT/REUSE/COLLISION`, plus the `EXTERNAL_READ_ONLY_RESOURCES` constant. It is a pure classifier (mapping-shaped fakes and real `V1Secret`/`V1PersistentVolumeClaim` model objects) and constructs/reconciles nothing.
- Policy: an absent resource is eligible for creation; a retained resource (PVC, state Secret, CA state) is reused automatically only when its deterministic name/namespace and every operator-controlled identity field match exactly — full appliance-name annotation, standard managed/identity labels, component label, exact retention annotation/class — with **no owner references** (owner plus retention is a collision even if an owner UID matches); unrelated extra labels/annotations are permitted; missing/partial/conflicting identity metadata is a collision and is never normalized; a matching ownerless retained object is `REUSE` with no mutation/adoption patching. The creating appliance CR UID is deliberately **not** part of the identity: retained resources survive CR deletion/recreation, so automatic exact-match reattachment works even when the CR UID changes, and any stale `coriolis.cloudbase.it/appliance-uid` annotation is ignored as unrelated.
- This is a namespace trust boundary: anyone who can create resources in the namespace can forge the operator's identity metadata. External/pre-existing resources, especially the registry pull Secret `coriolis-appliance-registry`, fail closed as `COLLISION` (even absent or with forged matching metadata) and remain read-only and outside this classifier/reconciliation policy (`EXTERNAL_READ_ONLY_RESOURCES`).
- Validation passed: 94 unit tests; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; `git diff --check`. The slice is **committed locally at `1b73045` on `dev`, but not pushed or deployed**; the deployed release `0.5.3` remains marker-only and unchanged. No runtime resource is constructed, reconciled, patched, read, or adopted, and no adoption mutations exist. A MariaDB vertical slice remains blocked by unresolved Secret/configuration/storage/readiness gates. Prior local commit references remain accurate: API slice `ab9df83`, metadata helper `fbab6e5`, status docs `6bf5cb5`, collision/migration slice `d8df00f`.

## Documented Locally: Secret/Configuration Contract Slice (Documentation-Only)

- Documented the authoritative Secret/configuration naming and key mapping in [docs/foundational-resource-contract.md](docs/foundational-resource-contract.md), freezing the immediate Kubernetes Secret/ConfigMap object names, key layouts, and the primary `coriolis.conf` split using only known source key names. All generated names use the existing `appliance_resource_name(<CR name>, <component>)` truncation/hash rules.
- Frozen retained, ownerless, operator-generated Secrets: `<appliance>-coriolis-credentials` (`coriolis_database_password`, `coriolis_keystone_password`, `temp_keypair_password`; licensing server/UI, Metal Hub, and InfluxDB keys absent/deferred); `<appliance>-infrastructure-credentials` (`database_password`, `rabbitmq_password`, `keystone_admin_password`, preserving local Kolla source names, with no invented Barbican/Memcached password); `<appliance>-step-ca-credentials` (`init_password`, with the broader `/etc/step` CA state on retained storage and TLS/private-key layout deferred).
- Frozen owner-referenced, rebuildable configuration: ConfigMap `<appliance>-coriolis-config` with exactly `coriolis-api.wsgi`, `wsgi-coriolis.conf`, `vixdisklib.conf`, `api-paste.ini`, `policy.yml`, `coriolis.release` (forbidden: `coriolis.conf`, provider fragments, credentials, tokens, private keys, registry auth); Secret `<appliance>-coriolis-config-secret` with exactly `coriolis.conf` (complete because it embeds credentials; GC'd with the CR and regenerated from retained credentials, not a retained credential store). Workloads mount both together as a read-only projected volume at `/etc/coriolis` with explicit `items` paths, no credential environment variables, and no `subPath`; rotation rollout mechanics remain deferred, and sensitive values must never appear in Pod template annotations/labels/status/events/logs.
- Recorded that retained credential values are generated once and reused only under the exact-match retained classifier policy committed at `1b73045`; generation algorithms/lengths not evidenced are not documented. The external `coriolis-appliance-registry` Secret remains read-only and is never copied into operator-owned Secrets. The prior unresolved name/key and `coriolis.conf` split gates are closed; remaining gates (generators/builders/reconcile reads/SSA/RBAC, TLS/CA layout, optional component credentials, storage sizes/layout, probes/readiness/bootstrap, rotation rollout mechanics) are listed in the contract's Unresolved Gates.
- This slice is documentation-only: no Secret/ConfigMap builders, values, RBAC, CRD, runtime resources, or reconcile behavior changed, and no credential values were included. It is **committed locally at `8ce26ba` on `dev`, but not pushed or deployed**.

## Completed Locally: Pure Secret/ConfigMap Builder Slice

- Implemented five pure builders in `src/coriolis_operator/reconcile.py`: `build_coriolis_credentials_secret`, `build_infrastructure_credentials_secret`, `build_step_ca_credentials_secret`, `build_coriolis_config_map`, and `build_coriolis_config_secret`. They use deterministic names and standard metadata: retained credential Secrets are ownerless with retention metadata; rebuildable configuration resources are owner-referenced without retention.
- The builders enforce exact frozen key sets, accept caller-provided opaque strings without mutation, and reject missing, extra, or non-string keys without exposing values. Secret manifests are `Opaque` with UTF-8/base64 `data` and no `stringData`; ConfigMap data is plain and restricted to the six approved files, never `coriolis.conf` or credentials.
- Validation passed: 116 total tests (22 new cases from the prior 94; 21 matched the final focused selector), Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. This is pure manifest construction only: no credential generation, `main.py` reconciliation, Kubernetes reads/SSA, RBAC, CRD, runtime resource creation, status/readiness, or deployment change. It is **committed locally at `050f16e` on `dev`, but not pushed or deployed**; deployed `0.5.3` remains marker-only.

## Completed Locally: Pure Retained Credential Generation Slice

- Implemented `generate_coriolis_credentials`, `generate_infrastructure_credentials`, and `generate_step_ca_credentials`. Each independently generates the seven frozen credential keys with production `secrets.token_urlsafe(32)`: 32 random bytes/256 bits as URL-safe opaque strings. Token-factory injection exists only for deterministic tests; empty or non-string factory results fail value-safely without exposing values.
- The generated mappings compose unchanged with existing builders and values must never appear in documentation, logs, status, events, or errors. Policy is operator-generated only: no inline CR credential values and no external credential Secret source. Existing external/pre-existing Secrets such as `coriolis-appliance-registry` remain read-only and outside this policy.
- Generate-once/reuse is policy only: future runtime calls each generator only when its retained Secret is `ABSENT`; exact matching ownerless retained Secrets are reused unchanged; collisions fail closed; automatic rotation remains deferred. `main.py` does not call these helpers.
- Validation passed: 132 total tests (16 new from 116), focused tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. No reconciliation, Kubernetes reads/writes/SSA, RBAC, CRD, runtime resources, status/readiness, chart/release, deployment, or rotation changed. The slice is **committed locally at `a604579` on `dev`, but not pushed or deployed**; deployed `0.5.3` remains marker-only.

## Completed Locally: Retained Secret Semantic Validation/Extraction Slice

- Implemented `validated_retained_secret_values`, committed locally at `5165629` on `dev`, but not pushed or deployed. It accepts mapping-shaped objects and Kubernetes `V1Secret` models; tolerates absent `apiVersion`/`kind` but rejects conflicting present values; requires `type: Opaque`; rejects persisted `stringData`; requires the exact frozen `data` keys with string encoded values; strictly base64-decodes then UTF-8-decodes them; and rejects empty decoded values.
- The helper returns a new decoded mapping without mutating input. Fixed/category-only errors expose neither encoded nor decoded values. It validates semantics only: no metadata classification, Kubernetes reads/writes, generation, SSA, collision/status handling, or reconciliation.
- Validation passed: 152 total tests (20 new from 132), focused 20 tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. No `main.py`, RBAC, CRD, runtime resource, chart/release, deployment, or rotation behavior changed; deployed `0.5.3` remains marker-only.

## Completed Locally: Non-Sensitive Configuration Rendering Slice

- Implemented pure `render_coriolis_config` in `src/coriolis_operator/configuration.py`, committed locally at `97153a7` on `dev`, but not pushed or deployed. It validates explicit bind-address, port, configuration-directory, VixDiskLib log-directory, and accepted-version inputs; renders only the six frozen ConfigMap keys through Jinja2 `PackageLoader` and `StrictUndefined`, with autoescape disabled and trailing newlines preserved.
- The six verbatim Apache-2.0 upstream templates are packaged with source/license attribution. Rendering remains non-sensitive: it does not render `coriolis.conf`, provider fragments, credentials, or any other Secret content. `accepted_version` maps only to the legacy `default_coriolis_docker_images_tag` variable.
- Validation passed: 19 focused renderer tests and 171 total tests, including wheel resource inspection and template byte comparison. No `main.py`, reconciliation, Kubernetes reads/writes, SSA, RBAC, CRD, runtime resources, workload, release/chart/image version, or deployment behavior changed; deployed `0.5.3` remains marker-only.

## Completed Locally: Pure Foundational Five-Resource Preflight

- Implemented `OwnedClassification.ABSENT/MANAGED/COLLISION`, `classify_owned_resource`, the credential-safe frozen `FoundationalResourcePreflight`, and `preflight_foundational_resources` in `src/coriolis_operator/reconcile.py`. The pure API covers exactly the three retained credential Secrets plus the owner-referenced configuration ConfigMap and Secret.
- All five resources have metadata classified before retained Secret semantics. Exact owner metadata/controller matches are `MANAGED` despite content/type drift (repairable later by SSA); mismatched ownership or retention metadata is `COLLISION`; unrelated extra metadata is allowed. A metadata collision stops semantic validation and all generation. `REUSE` retained Secrets are semantically validated, a validation `ValueError` maps to that resource's `COLLISION`, all reusable semantics finish before generation, and generators run only for their corresponding `ABSENT` retained Secrets after a collision-free preflight. Successful results carry generated/decoded credentials while excluding them from `repr`; errors, documentation, logs, status, and events remain value-safe.
- Validation passed: 23 focused tests, 194 total tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. This is pure only: no `main.py`, Kubernetes reads/writes, SSA, RBAC, CRD, runtime construction/adoption, reconciliation/status behavior, chart/release/image version, deployment, TLS/storage/readiness/bootstrap/rotation changes. It is **committed locally at `35eac9b` on `dev`, but not pushed or deployed**; deployed `0.5.3` remains marker-only.

## Completed Locally: Sensitive Renderer Contract (Documentation-Only)

- Documented the future pure sensitive `coriolis.conf` renderer contract. The complete rendered file remains exactly one key in the owner-referenced configuration Secret, never ConfigMap/log/status/event/metadata/documentation content; no renderer implementation, tests, or runtime behavior changed.
- The immutable upstream base template and all 16 provider fragments are frozen, preserving authoritative provider lists/order/module maps. Custom module overrides and compression/compressor are disabled initially. Explicit internal dependency inputs, fixed source-audited identities/paths, credential mappings, strict value-safe validation, and a redacted interface boundary are frozen without CRD fields.
- All three generated credential Secrets use exactly `coriolis.cloudbase.it/retention: state-credentials`. TLS/CA material, provider connection/private data, optional credentials, dependency Services/workloads, bootstrap, storage, readiness, and rotation remain deferred. Source review covered every base-template variable and all fragments; `git diff --check` passed. This documentation-only contract is **committed locally at `574efcf` on `dev`, but not pushed or deployed**; deployed `0.5.3` remains marker-only.

## Completed Locally: Pure Sensitive Configuration Rendering

- Implemented `SensitiveCoriolisEndpoints`, `SensitiveCoriolisCredentials`, redacted one-key `SensitiveCoriolisConfig`, and `render_sensitive_coriolis_config`, committed locally at `9bb20f3` on `dev`, but not pushed or deployed; deployed `0.5.3` remains marker-only.
- The exact one-key `coriolis.conf` output composes with the existing configuration-Secret builder and is rejected at the ConfigMap boundary. Inputs are frozen/exact and unmutated; credential/output reprs are redacted; errors are fixed/category-only and value-safe.
- Jinja uses `PackageLoader`, `StrictUndefined`, disabled autoescape, and a trailing newline with byte-identical immutable upstream base and all 16 provider fragments. Frozen provider lists/order/module maps, source-audited values/paths, source/license attribution, prohibited custom overrides, and disabled compression/compressor are enforced.
- Validation passed: 40 focused configuration tests, 215 total tests, Ruff lint/format, strict mypy, Helm lint/template, `git diff --check`, 17/17 source byte parity, and offline wheel inspection with exactly 25 expected template resources. No `main.py`, reconciliation, Kubernetes reads/writes/SSA, RBAC, CRD, runtime resources/workloads, chart/release/image version, deployment, TLS/CA/bootstrap, provider/private data, optional credentials, storage, readiness, or rotation behavior changed.

## Documented Locally: Foundational Multi-Resource Failure Contract

- The historical, superseded documentation-only policy in [docs/foundational-resource-contract.md](docs/foundational-resource-contract.md) records validation; marker and five-foundational-resource pre-reads/classification; pure preparation before writes; canonical operations; marker last; and non-transactional stop-on-failure behavior.
- `ABSENT` uses create with `AlreadyExists` retry/preflight; managed or legacy objects use resource-version/concurrency-guarded SSA with `Conflict` retry/preflight; `force` never bypasses classification or concurrency; retained `REUSE` is no-write. Retryable read/apply/marker failures use value-safe `ResourceReadFailed`, `ResourceApplyFailed`, or `MarkerApplyFailed` status before framework retry, while stable `ResourceCollision` remains non-transient and mutation-free. The marker records foundational completion only, never readiness, transactionality, or no-drift proof; `Ready=False/RuntimeNotImplemented` remains truthful.
- That historical contract changed no runtime behavior. The marker-plus-four foundational runtime gate is committed locally at `862777d`, with status commit `f219977`; the four-Service slice is committed locally at `797235b` on `dev`, unpushed and undeployed, and implements RabbitMQ, Memcached, MariaDB, and Keystone Services. Remaining Services, Ingress, workloads, and runtime design are deferred. Deployed `0.5.3` remains marker-only.

## Completed Locally: Marker-Plus-Four Foundational Runtime Gate

- Reconciliation performs the marker plus four exact ordered pre-reads, treats only `404` as absent, and completes metadata-first preflight/rendering before any write. Retained `state-credentials` create when absent or reuse without writing; managed resources use resourceVersion-guarded SSA; the four foundational resources write in order with the marker last; and failures do not roll back earlier writes.
- Retryable read, foundational-apply, and marker-apply failures publish sanitized `ResourceReadFailed`, `ResourceApplyFailed`, or `MarkerApplyFailed` status before `kopf.TemporaryError`. Stable `ResourceCollision` remains mutation-free; successful reconciliation remains `Ready=False/RuntimeNotImplemented`. Secret and ConfigMap RBAC is exactly `get`/`create`/`patch`.
- Validation passed: `uv run pytest tests/unit` (243 passed); `uv run ruff check .`; `uv run ruff format --check .` (35 files already formatted); `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; and `git diff --check`. The gate is committed locally at `862777d` on `dev`, with status commit `f219977`; both are unpushed and undeployed.

## Completed Locally: Four Dependency Service Slice

- The four-Service slice is committed locally at `797235b` on `dev`, unpushed and undeployed, and adds exactly four owner-referenced ClusterIP plaintext Services in frozen order: RabbitMQ `5672`, Memcached `11211`, MariaDB `3306`, and Keystone `5000`. Each has deterministic `appliance_resource_name`, standard metadata, no explicit `clusterIP`/`clusterIPs`, selector exactly the label-safe appliance identity plus component, and one named TCP Service/target port at its fixed number.
- Reconciliation retains the marker-plus-four read prefix, then pre-reads the Services in frozen order. Only `404` is absent; reads/classification/foundational preflight/rendering/manifest construction complete before writes. Managed Services use resourceVersion-guarded SSA; writes are four foundational resources, then Services in frozen order, marker last. Stable collisions are mutation-free `ResourceCollision`; sanitized read/apply failures stop without rollback and retry. Service RBAC is exactly `get`/`create`/`patch`; existing Secret/ConfigMap RBAC remains exactly `get`/`create`/`patch`; `Ready=False/RuntimeNotImplemented` remains expected.
- Validation passed: `uv run pytest tests/unit` (252 passed); Ruff lint; Ruff format check (35 files already formatted); mypy; Helm lint/template; and `git diff --check`. No workloads, endpoints, Ingresses, Jobs, storage, probes/readiness, bootstrap, credential rotation, additional Services, release/chart/image/deployment, or CRD version change was added. Deployed `0.5.3` remains marker-only.

## Completed Locally: Dependency Workload Evidence Gate

- Documentation-only, local, unpushed, and undeployed: froze the four approved mirrored support-image identities, source-backed dependency evidence, durable resource and credential boundaries, fail-closed eligibility checklist, and MariaDB-first sequence in [docs/foundational-resource-contract.md](docs/foundational-resource-contract.md).
- OCI/configuration/security/storage/bootstrap/probe/readiness evidence remains blocked across the dependency set; the later MariaDB evidence advancement below closes only its local OCI and standalone path while preserving its Kubernetes blockers. No workload, Job, PVC, RBAC, readiness, or runtime behavior changed.
- Validation passed: 252 unit tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`.

## Completed Locally: MariaDB Runtime Evidence Advancement

- Documentation-only, local, unpushed, and undeployed: anonymously inspected and pulled `cr.virtomat.io/virtomat/coriolis/mariadb-server@sha256:22cb109d23d1aa6a6acb17e54657b5b9cd753837b01345b52fc3c35cbbd9981e`, then proved direct non-Galera `mariadbd` operation under the required restricted Docker security context with value-safe, idempotent initialization and persistent-volume recreation evidence.
- Rejected `kolla_start` because its required config, `DB_ROOT_PASSWORD` environment input, and password-bearing client arguments violate the value boundary; Galera-specific built-in checks also fail for the proved non-Galera path. This closes only local OCI/standalone evidence, not Kubernetes implementation eligibility.
- At this evidence stage, MariaDB remained blocked on the Kubernetes workload, storage, manifest, probe, resource, lifecycle/recovery, and reliable log-capture contracts; the later contract section below closes that development design boundary. No source/runtime code, CRD, workload, PVC, Job, RBAC, probe/readiness, release/version, deployment, or cluster state changed. Deployed `0.5.3` remains marker-only.
- Validation passed: 252 unit tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`; no disposable MariaDB containers, networks, or volumes remain.

## Completed Locally: MariaDB Kubernetes Contract

- Documentation-only, local, unpushed, and undeployed: froze the future development MariaDB API, retained-PVC, StatefulSet, generated configuration, bootstrap, probe, lifecycle, and reconciliation contract in [docs/foundational-resource-contract.md](docs/foundational-resource-contract.md). The exact approved digest and direct `mariadbd --console` path are retained; credentials remain file-only and value-safe.
- This closes design eligibility only for the next pure CRD validation/resolver and manifest-builder/preflight slice. No runtime code, CRD, RBAC, workload, PVC, Helm artifact, sample, deployment, or cluster behavior changed; deployed `0.5.3` remains marker-only.
- Validation passed: 252 unit tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`; no disposable MariaDB resources remain.

## Completed Locally: MariaDB Pure Desired-State Preparation

- Local, unpushed, and undeployed: added the optional CRD storage/resources shape and pure complete-settings resolver with positive Kubernetes quantity validation and request/limit checks. Added value-safe direct-MariaDB configuration/bootstrap rendering with redacted credential interfaces and no credential environment or argument exposure.
- Added pure retained-PVC, owner-referenced ConfigMap/Secret/StatefulSet builders and metadata-first preflight. The PVC classifier permits only exact retained identity and semantically equal immutable storage settings while ignoring provisioner binding/status fields; collisions return no prepared manifests. The StatefulSet manifest fixes the approved digest, restricted security context, direct startup, file-authenticated probes, configured resources, retained RWO Filesystem claim, ordinary runtime/tmp `emptyDir` volumes, and 30-second termination.
- This slice does not wire `main.py`, perform Kubernetes I/O, add RBAC, change status/readiness, create runtime resources, deploy, or validate target storage. Validation passed: 286 unit tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`.

## Completed and Published: MariaDB Reconciliation

- Published on `origin/dev` at `55212b0` and undeployed: wired complete MariaDB settings into reconciliation with a stable, mutation-free `InvalidRuntimeConfiguration` status before Kubernetes client construction. Added storage/resources field handlers so corrected configuration and supported resource changes reconcile without broad child-resource watches.
- Preserved the existing marker/foundational/four-Service read prefix, then added ordered PVC, ConfigMap, configuration Secret, and StatefulSet reads. All collision classification, secret-safe rendering, resource-version validation, and desired-state preparation complete before writes. The write path preserves foundational resources and Services first, creates an absent PVC or performs exact no-write reuse, applies guarded SSA to managed MariaDB configuration and the StatefulSet, and keeps the marker last.
- Added exact PVC `get`/`create` and StatefulSet `get`/`create`/`patch` RBAC with no PVC patch/delete, pod/log, PDB, or cluster-scope expansion. Exhaustive tests cover stable configuration failure, cross-API ordering, each MariaDB collision/read/apply position, retained-PVC reuse, guarded SSA, marker-last behavior, sanitization, and field handlers. Validation passed before publication: 316 unit tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`.

## Completed: Released Memcached Reconciliation And POC

- Source commit `063e438ef416599e9816a2400afcc5a5a7af9aa0` was published by pipeline `4dcpfk` (`Default`; all expected steps `SUCCEEDED`) as CI-owned release `cb6b055eaf5e74c99e26c1c3d662b2d749331627`, chart/app/image `0.5.8`.
- The released-artifact POC passed in single-node disposable namespace `coriolis-memcached-validation-20260822`. The CR created the Deployment in 44s and the original Pod became Ready 3s later; MariaDB and Memcached both reached `1/1`.
- The exact Deployment contract, Service selector/ready TCP `11211` endpoint, Service-DNS `version` and fixed set/get, replacement after normal Pod deletion in 3.603s, changed Pod UID, zero restarts, same image digest, and cache ephemerality all passed. Normal cleanup removed the CR, Helm release, namespace, copied registry Secret, retained PVC, and Delete-policy PV; no appliance remains. `Ready=False/RuntimeNotImplemented` remains expected. That source baseline remains 326 tests plus Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. RabbitMQ `0.5.11` has accepted released-artifact POC evidence; Keystone evidence and local implementation are now complete.

## Completed: MariaDB Single-Node POC

- Operator `0.5.5` clean first boot exposed anonymous accounts from MariaDB test-database initialization shadowing `coriolis@%`. Commit `3ee5d2d` adds `--skip-test-db`; pipeline `kpv306` succeeded and released `0.5.6` at CI-owned commit `647c63b`. The full 316-test suite and all static/Helm checks pass.
- Released `0.5.6` clean first boot on single-node `local-path` passed without repair: RWO Filesystem PVC Bound in 4 seconds, Pod Ready in 17 seconds, no anonymous accounts or `test` database, `fsGroup: 42434` writes, authenticated probes, exact retained-PVC/no-write credential reuse across CR recreation, persisted database state, and normal 12-second termination plus same-node remount. All disposable resources and the `Delete`-policy PV were removed.
- CSI and cross-node attach/detach evidence remain open beyond the accepted single-node POC. Production backup/restore, HA, and RPO/RTO acceptance remain separate gates. Argo removed the legacy Deployment selector overlap; the historical POC ran `0.5.8`, and Argo now runs healthy `0.5.11`.

## Historical: RabbitMQ Reconciliation And Runtime Evidence

- Local, uncommitted, unpushed, undeployed, and runtime-incomplete: implemented an operator-managed single-node retained RabbitMQ StatefulSet. It adds optional explicit `spec.storage.rabbitmq` and `spec.resources.rabbitmq`, a separate ownerless retained RWO Filesystem PVC, owner ConfigMap, restricted one-replica StatefulSet, direct startup/probe scripts, existing Service and retained infrastructure-Secret references, collision-first preflight, ordered reads/writes, guarded SSA, PVC no-write reuse, marker-last ordering, and no new RBAC verbs. Writes are MariaDB, RabbitMQ, then Memcached.
- Local image evidence uses approved `rabbitmq:2023.1-ubuntu-jammy@sha256:a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a`, local image ID `sha256:f9e28ef3ed172cfdda9e6c3d56c509ceaee672b516381343244ed40332a19e73`, Linux/amd64, Kolla `16.6.1`, and UID/GID `42439` without a supplemental group. Direct `/usr/sbin/rabbitmq-server` and `/usr/sbin/rabbitmq-diagnostics` replace rejected default `dumb-init --single-child --` plus `kolla_start`; the proven context is plaintext `0.0.0.0:5672`, read-only root, dropped `ALL`, no-new-privileges, console-only logging, and writable `/var/lib/rabbitmq`, `/run/rabbitmq`, and `/var/log/rabbitmq`.
- File-only bootstrap retains the infrastructure Secret key as a mounted file; a random 4-byte salt and streamed Rabbit SHA256 definitions avoid credential/hash argv, environment, logs, and output. It provisions `openstack`, vhost `/`, and exact permissions from mode-`0600` ephemeral definitions. Two retained-volume launches reached Ready in 15.024s and 13.533s; sanitized broker checks, persisted marker/state, and SIGTERM exits in 6.580s and 6.757s passed; disposable Docker artifacts were removed. Full local validation passed: 355 unit tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`.
- This was pre-release evidence. The released-artifact POC and its acceptance are recorded below; `Ready=False/RuntimeNotImplemented` remains expected overall.

## Completed: Released RabbitMQ 0.5.11 POC

- Source `6a5a2b589c0dbfc2f5734f5863f9f8591c5f8c2d` was published by successful Default pipeline `qhvqt1` (13:59:46-14:01:07 UTC) as CI release `c48fd79622a8e760591333bf1ab6a0aa25d2f9d3`, `0.5.10`. Its isolated bootstrap/authenticated-AMQP POC was not accepted: readiness used three sequential diagnostics at period `5s`/timeout `5s`, causing 37 timeout failures and Endpoint flapping despite zero restarts.
- Fix source `0d52c57aea1345213e519622106bb7b78236c0f1` sets readiness period `10s`/timeout `15s`; 355 tests plus Ruff lint/format, mypy, Helm lint/template, and diff check passed. Successful Default pipeline `4nj6f5` (14:14:34-14:15:38 UTC) released CI commit `3985a677100a55844dc07ac74a30a24e1e2b03e0`, chart/app/image `0.5.11`.
- Accepted released-artifact validation in `coriolis-rabbitmq-validation-20260822` on single-node `local-path` passed manifest/security, clean bootstrap/fsGroup/RWO, Service/EndpointSlice, Service-DNS authenticated durable AMQP, stable readiness, normal Pod replacement, same-node persistence, exact retained no-write CR recreation, and cleanup. The final clean-storage smoke reached Rabbit StatefulSet in 59s and Ready in 94s with zero restarts, no readiness timeouts, four stable 15-second samples, and a ready endpoint. `Accepted=True`/`Reconciled=True`; `Ready=False` remains truthful.
- Cleanup removed owned resources in 20s, the namespace in 78s, and final Rabbit/Maria Delete-policy PVs in 1s. CSI/cross-node attach-detach, backup/restore, HA, RPO/RTO, credential rotation, and production storage remain open. Keystone evidence and implementation are now complete; its CI-owned release and released-artifact POC are next.

## Completed: Keystone Standalone Runtime Evidence

- `scripts/validate-keystone-runtime.py` passed in `69.063s` against the exact MariaDB and Keystone digests. It proved dedicated least-scope database setup, schema sync/check, non-root key setup and exact metadata, file-only idempotent admin bootstrap, direct restricted WSGI, `/v3`, authenticated token issuance, normal stop/restart, retained state, and cleanup without exposing values.
- Installed source proves initial token/receipt and credential files use independent `base64.urlsafe_b64encode(os.urandom(32))` values, exact files `0` and `1`, with shared token/receipt and separate credential repositories. Read-only Secret-backed steady state is supported; rotation is not.
- The frozen development contract selects three new retained Secrets, generated rebuildable configuration, a one-replica `Recreate` Deployment, idempotent init containers, direct WSGI, authenticated startup/readiness, unauthenticated liveness, no PVC, no Job, no new RBAC verbs, and truthful `Ready=False/RuntimeNotImplemented`. The implementation is complete below; a CI-owned release and released-artifact Kubernetes POC remain next.

## Completed Locally: Keystone Reconciliation

- Implemented dedicated database-password, Fernet-key, and credential-key retained Secrets with metadata-first collision checks, absent-only independent generation, semantic validation, and exact no-write reuse. Generated configuration is split between an owner ConfigMap and Secret without putting values in argv, environment, metadata, status, events, or logs.
- Extended MariaDB bootstrap with the dedicated `keystone` database/user and least-scope grant while preserving the existing MariaDB Secret key set. A fixed non-sensitive bootstrap-schema annotation rolls managed MariaDB Pods once.
- Added the restricted one-replica `Recreate` Deployment with prepare, schema-sync/check, and file-only bootstrap init containers; direct WSGI; authenticated startup/readiness; unauthenticated liveness; no PVC, Job, service-account token, or new RBAC verbs.
- Reconciliation pre-reads all six Keystone resources before mutation, completes preflight/rendering and MariaDB rerendering before writes, preserves retained no-write reuse and guarded SSA, and writes Keystone retained state before MariaDB while keeping generated Keystone resources after Memcached and the marker last. Collision/read/apply failures remain mutation-safe or value-safe as applicable.
- Full local validation passed: 397 unit tests, Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. This slice is undeployed and not Kubernetes POC evidence. No CRD, RBAC, Helm version, release, or cluster state changed; `Ready=False/RuntimeNotImplemented` remains truthful. A CI-owned release and isolated released-artifact POC are next.

## Pending CIXpress Integration

- Receive and add the exact CIXpress pipeline configuration, Template, and Job manifests.
- Validate version-alignment behavior, including the dev branch tag check.
- Define trigger and monitoring credentials without storing secrets in the repository.
- Integrate Argo CRD pre-upgrade automation into promotion/deployment; the standard build pipeline does not perform it.

## Planned: Kubernetes-Native Core Runtime

1. Image identity and pull gates are complete. RC4 failed (Build `868` exported an OVA but no `2608*` tag exists, so RC4 is OVA-only and must not be used); the approved fallback is exact official release `2603.4`. Recorded immutable digests, platform, runtime-user class, listeners, OCI healthcheck availability, compatibility, and `registry.cloudbase.it/appliance` access in [the image inventory ledger](docs/image-inventory.md), and mirrored all 26 approved images to `cr.virtomat.io/virtomat/coriolis` (see the mirror section above). Pull validation in `virt-infra-dev-buc-hq` namespace `coriolis` passed using the destination Secret `coriolis-appliance-registry` (type `kubernetes.io/dockerconfigjson`): all 21 initial-runtime image references pulled successfully and no pull-validation Pods remain. Exact support-image launch and configuration interfaces remain part of the workload eligibility gate.
2. The development stack through MariaDB reconciliation is published on `origin/dev` at `55212b0` and included in released operator `0.5.6`. No runtime workloads remain deployed after POC cleanup.
3. MariaDB reconciliation includes stable configuration failure, ordered pre-reads, exact retained-PVC reuse, guarded managed-resource writes, marker-last ordering, and narrow PVC/StatefulSet RBAC. Released `0.5.6` closes its accepted single-node `local-path` POC; CSI attach/detach/rescheduling and production backup/restore, HA, and RPO/RTO remain open. Released `0.5.8` closes the Memcached single-node POC for its ephemeral Deployment, Service endpoint, protocol, replacement, and cleanup contract. RabbitMQ `0.5.11` has accepted released-artifact POC evidence. Keystone reconciliation is implemented and validated; its CI-owned release and released-artifact POC evidence remain next. Barbican and all other backend Services remain deferred.
4. Emit an Ingress route only after its backend Service exists. The controller will not own controller or certificate Secrets; ingress-nginx owns public TLS/redirects. Include explicit-origin CORS/preflight/auth headers and WebSocket behavior when those backends are defined.
5. Add controller watches, status/readiness, tests, and development acceptance. `Ready=True` requires mandatory Jobs, dependencies, workloads, and internal UI/API checks.
6. Retain state credentials on deletion; delete only operator-owned workloads, Services, Jobs, and generated ConfigMaps. Never delete pre-existing referenced Secrets. Avoid a destructive finalizer.

## Pending Decisions

- Define promotion.
- Define the exact CIXpress repository integration and trigger.
- Select a license.
