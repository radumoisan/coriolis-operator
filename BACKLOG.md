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
- Kubernetes pull validation in `virt-infra-dev-buc-hq` namespace `coriolis` passed: all exact 21 initial-runtime image references (10 application images at `2603.4`, 10 support images at `2023.1-ubuntu-jammy`, and Step CA at `2603.4`) pulled successfully, validated serially by `scripts/validate-image-pulls.py` using one short-lived Pod at a time with `imagePullPolicy: Always`, explicit context/namespace, and the destination Secret `coriolis-appliance-registry` (type `kubernetes.io/dockerconfigjson`); each successful Pod was removed and no pull-validation Pods remain. Independent main-agent validation of Step CA repeated successfully with the exact expected digest. The image inventory and pull gate are therefore complete.

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

## Pending CIXpress Integration

- Receive and add the exact CIXpress pipeline configuration, Template, and Job manifests.
- Validate version-alignment behavior, including the dev branch tag check.
- Define trigger and monitoring credentials without storing secrets in the repository.
- Integrate Argo CRD pre-upgrade automation into promotion/deployment; the standard build pipeline does not perform it.

## Planned: Kubernetes-Native Core Runtime

1. Image inventory and pull gate are complete. RC4 failed (Build `868` exported an OVA but no `2608*` tag exists, so RC4 is OVA-only and must not be used); the approved fallback is exact official release `2603.4`. Recorded immutable digests, entrypoints, users, listeners, health capabilities, compatibility, and `registry.cloudbase.it/appliance` access in [the image inventory ledger](docs/image-inventory.md), and mirrored all 26 approved images to `cr.virtomat.io/virtomat/coriolis` (see the mirror section above). Pull validation in `virt-infra-dev-buc-hq` namespace `coriolis` passed using the destination Secret `coriolis-appliance-registry` (type `kubernetes.io/dockerconfigjson`): all 21 initial-runtime image references pulled successfully and no pull-validation Pods remain. This gate is complete.
2. Implement naming, ownership, retention, generated configuration and secrets, then foundational dependencies and bootstrap Jobs. The Milestone 4 contract slice is documented in [docs/foundational-resource-contract.md](docs/foundational-resource-contract.md), and the metadata-only helper slice (`appliance_resource_name`, `appliance_identity`, `build_resource_metadata`) is implemented locally. The collision/migration marker API-layer slice is implemented and committed locally at `d8df00f` on `dev` (not pushed or deployed); the retained-resource authorization/classification slice is implemented and committed locally at `1b73045` on `dev` (not pushed or deployed; no runtime resources or adoption mutations exist). The pure Secret/ConfigMap builders are committed locally at `050f16e` (not pushed/deployed). A MariaDB vertical slice remains blocked by credential generation/value-source policy, collision-safe Secret/ConfigMap pre-reads, SSA, minimal RBAC, reconciliation, storage, probes/readiness/bootstrap, and rotation rollout. No runtime workloads are implemented or deployed.
3. Implement MariaDB, RabbitMQ, Memcached, Keystone, Barbican, Step CA, InfluxDB/logger compatibility, API, conductor, scheduler, transfer cron, minion manager, deployer manager, privileged worker, compressor, web, and web proxy.
4. Add server-side apply, controller watches, status/readiness, tests, and development acceptance. `Ready=True` requires mandatory Jobs, dependencies, workloads, and internal UI/API checks.
5. Retain PVCs, CA state, and state credentials on deletion; delete only operator-owned workloads, Services, Jobs, and generated ConfigMaps. Never delete pre-existing referenced Secrets. Avoid a destructive finalizer.

## Pending Decisions

- Define promotion.
- Define the exact CIXpress repository integration and trigger.
- Select a license.
