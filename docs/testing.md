# Testing

Validate the work relevant to each change.

## :material-book-open-page-variant-outline: Released RabbitMQ Validation And POC

- Source `6a5a2b589c0dbfc2f5734f5863f9f8591c5f8c2d` passed successful Default pipeline `qhvqt1` (13:59:46-14:01:07 UTC) and released `0.5.10` at CI commit `c48fd79622a8e760591333bf1ab6a0aa25d2f9d3`. Its isolated clean bootstrap/authenticated-AMQP POC was not accepted: three sequential readiness diagnostics at period `5s`/timeout `5s` caused 37 timeout failures and Endpoint flapping despite zero restarts.
- Fix source `0d52c57aea1345213e519622106bb7b78236c0f1` changed readiness to period `10s`/timeout `15s`. Full validation passed: `uv run pytest tests/unit` (355 passed), Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. Successful Default pipeline `4nj6f5` (14:14:34-14:15:38 UTC) released `0.5.11` at CI commit `3985a677100a55844dc07ac74a30a24e1e2b03e0`.
- Coverage includes optional explicit RabbitMQ storage/resources settings validation; collision-first reads and preflight; exact ownerless retained RWO Filesystem PVC create/no-write reuse; owner ConfigMap and restricted one-replica StatefulSet construction; existing Service and retained infrastructure-Secret references; direct file-only bootstrap/probes; guarded SSA; no new RBAC verbs; MariaDB then RabbitMQ then Memcached writes; and marker-last ordering. `Ready=False/RuntimeNotImplemented` remains expected.
- Before publication, local image/runtime evidence covered approved `rabbitmq:2023.1-ubuntu-jammy@sha256:a595bf6f306ded2b6ad01f068ef69255df72eb73d471ba73ce9bbf0470d15d8a`, image ID `sha256:f9e28ef3ed172cfdda9e6c3d56c509ceaee672b516381343244ed40332a19e73`, direct server/diagnostics, restricted filesystem/security context, file-only definitions, retained-volume restart, and sanitized broker checks. That local evidence was not a Kubernetes POC.
- The accepted `0.5.11` POC in `coriolis-rabbitmq-validation-20260822` used single-node `local-path`, operator imageID `sha256:fbc0cd338f79bf8a7ea06446f05210859ce549b387371594eee02b0ddef8a724`, the approved Rabbit digest, and approved helper Coriolis API digest prefix `fce636...2705`. Probe-fix rollout produced eight consecutive 15-second Ready/endpoint samples and preserved a durable AMQP marker. Normal Pod deletion replaced in 4s, reached Ready in 29s with zero restarts/same digest, stayed stable for four samples, and preserved the marker. CR deletion removed owned resources in 32s while retained PVCs/credential Secrets preserved exact UIDs and unchanged resourceVersions; recreate reached Rabbit StatefulSet in 2s and Ready in 31s, preserved/authenticated the marker, and used new owner UID references.
- Final clean-storage smoke reached Rabbit StatefulSet in 59s and Ready in 94s with fresh ownerless Bound Rabbit PVC/PV, zero restarts, corrected probe, no readiness timeouts, four stable samples, and ready endpoint. Service-DNS authenticated AMQP durable publish/consume/ack and queue deletion passed. Normal cleanup removed owned resources in 20s, namespace in 78s, and final Rabbit/Maria Delete-policy PVs in 1s; all earlier POC PVs, release, helpers, and namespace are absent. CSI/cross-node, backup/restore, HA, RPO/RTO, credential rotation, and production storage remain open. Keystone is now complete and accepted as released `0.5.14` POC evidence (recorded below).

## :material-book-open-page-variant-outline: Keystone Runtime Evidence Checkpoint

- Local disposable inspection validated exact Keystone digest `sha256:7c57962762f5e6fdb1a109097e8f3e2e5f6218ad9c09f10a585adb67ed245cf0`, image ID `sha256:94cd15e8f645f97f65bd21a38713a13b5da44c67049de3a06436e0741f66d1ec`, Linux/amd64, Keystone `23.0.2`, unset OCI user/root default, account `42425:42425` with supplemental group `42400`, default `dumb-init --single-child --` plus `kolla_start`, and no declared port or healthcheck.
- The default command fails without Kolla `config.json`; no Keystone configuration, Apache site, Fernet keys, or credential keys are baked in. Tracked validator `scripts/validate-keystone-runtime.py` completed in `69.063s` with file-only inputs and sanitized fixed-category output. Dedicated database creation/user/password/grant, repeated SQL, TCP authentication, table access, unrelated-database rejection, normal MariaDB stop, retained-data restart, schema sync/repeat/check, key setup/repeat, exact key metadata, bootstrap/repeat, direct WSGI, `/v3`, token authentication, normal restart, repeat authentication, and cleanup passed.
- WSGI first/restart health took `5.628s`/`5.720s`; token authentication took `1.480s`/`1.618s`; normal stops took `0.297s`/`0.277s`. Runtime used UID/GID `42425:42425`, supplemental group `42400`, read-only root, dropped capabilities, no-new-privileges, private networking, read-only configuration, retained key state, and limited tmpfs writable paths. No credentials, SQL values, DSNs, tokens, key contents/hashes, headers, bodies, raw logs, or environments were emitted. All containers, networks, volumes, scratch files, and generated credentials were removed. No Kubernetes, reconciliation, CRD, RBAC, release, or deployment change occurred.

## :material-book-open-page-variant-outline: Keystone Reconciliation Validation

- Local implementation covers independent retained database/Fernet/credential Secret generation, exact no-write reuse, semantic and collision checks, generated non-sensitive/sensitive configuration partitioning, dedicated MariaDB bootstrap and rollout annotation, the restricted direct-WSGI Deployment, and exact command/probe/security/mount contracts.
- Controller coverage proves all six Keystone reads occur before mutation; every read and collision position is mutation-free or sanitized; retained creates precede MariaDB; owner resources follow Memcached; managed resources use guarded SSA; create and patch failures stop at every Keystone write position; and the marker remains last. `Ready=False/RuntimeNotImplemented` remains expected.
- Full local validation passed: `uv run --offline pytest tests/unit -q` (397 passed), `uv run --offline ruff check .`, `uv run --offline ruff format --check .`, `uv run --offline mypy src`, `helm lint helm/`, `helm template coriolis-operator helm/ --include-crds`, and `git diff --check`. The implementation is now released as `0.5.14` with accepted released-artifact POC evidence; no CRD or RBAC changed.

## :material-book-open-page-variant-outline: Released Keystone 0.5.14 Validation And POC

- Keystone implementation source `f90cae4` was published by CIXpress pipeline `c2pajn`; all expected `git-clone`/`kaniko-build`/`helm-update`/`cleanup` steps `SUCCEEDED`, releasing `0.5.13` at CI commit `7eb2215`. Its isolated single-node POC was not accepted: the Keystone prepare init failed before db-sync/bootstrap because non-root UID/fsGroup `42425` could not chmod the root-owned emptyDir mount roots during `install -d -m 0700`; MariaDB, RabbitMQ, and Memcached stayed healthy.
- Fix source `087bbc2` (`Fix Keystone emptyDir preparation`) passed 398 unit tests plus Ruff lint/format, mypy, and `git diff --check`; pipeline `fhzlg6` succeeded at all four expected steps and released accepted `0.5.14` at CI commit `edd349a`. A direct local Helm OCI pull returned HTTP `401` before any disposable Helm release or CR was created, so the exact `0.5.14` chart was safely promoted by a disposable Argo Application in project `default` using the existing registry integration, an existing compatible CRD, and `skipCrds`; no credentials were exposed.
- Accepted isolated single-node POC: Keystone reached `Available` about 90 seconds after CR creation; prepare, db-sync, and bootstrap init containers exited `0`; `/v3` returned 200 and an authenticated token request returned 201 with a token header present on the restricted direct-WSGI one-replica `Recreate` Deployment, with ready Service/EndpointSlice and zero restarts across all workloads. Normal Pod replacement produced a distinct UID, the exact same image digest, zero restarts, init exit `0`, unchanged retained Keystone Secret UID/resourceVersion/timestamp, and passing probes/auth. CR deletion/recreation removed owned resources, reused retained Keystone Secrets and MariaDB/RabbitMQ PVCs exactly without writes under a new CR UID/ownerRefs, reached Keystone `Available` about 37 seconds later, and passed idempotent init/probes/auth.
- A deliberate unmanaged Keystone ConfigMap collision remained unchanged with `Reconciled=False/ResourceCollision` and zero managed writes; removing it restored healthy reconciliation. This is historical `0.5.14` evidence; the accepted `0.5.17` Coriolis-common record is below.

## :material-book-open-page-variant-outline: Accepted Coriolis-Common 0.5.17 POC

- Fix source `764b9952d1e1c9bc1cbce08afddea8781f391f42` was published by CIXpress pipeline `ectoq4` (`2026-08-23T12:16:43Z`-`12:18:06Z`), with all expected steps `SUCCEEDED`, as `0.5.17` at CI commit `6bfc494d50949b7a5aa770c4febb7c5100b3b363`; operator imageID is `sha256:443e6e5dec8cd6e7f2040ca4fe1f5dcfcfa40ad36184cb30dc54a4be7547d8a6`. Historical `0.5.16` v1 failed on provider-continuation parsing before dbsync and correctly remained `BootstrapFailed`, markerless, and unmodified.
- v2 completed in place in 31s (`12:23:11Z`-`12:23:42Z`), `succeeded=1`, `failed=0`, exit `0`, restarts `0`, on the exact conductor digest; v1 remained failed and untouched. Dependencies were `1/1` with one endpoint each. Final recreated state was v2 only, v1 absent, `acceptedVersion=2603.4`, `Accepted=True`, `Reconciled=True`, `Degraded=False`, `Ready=False/RuntimeNotImplemented`.
- Output-suppressed exact-image verification passed twice in 10s, including post-recreation, covering required schema tables, unique enabled user/default project/password auth/admin assignment, migration service, and exactly one RegionOne admin/internal/public endpoint each. No secrets, logs, or raw data were emitted. Resume reused the completed immutable Job/ConfigMap exactly without writes; replacement operator was ready in 4s with zero restarts. Local validation passed 477 tests, Ruff, mypy, Helm, parser, Docker build, diff, and exact renderer runtime `135.212s`.
- Collision removal is not automatic: the unmanaged immutable v2 ConfigMap remained exact and produced `ResourceCollision` with zero newly owned writes; five retained ownerless Secrets and MariaDB/RabbitMQ PVCs remained exact and Bound. Removing the conflict alone did not reconcile because metadata/children are not watched. Normal operator replacement invoked Kopf resume in 6s and converged in 124s; this remains an operational follow-up. Cleanup left only expected retained Secrets during the 300s CR observation; Application/namespace/both Delete-policy PVs deleted in 11s/53s/9s. POC application, namespace, and PVs are absent; shared `argocd/coriolis` `0.*.*` is `0.5.17` Synced/Healthy, operator `1/1` zero restarts, node Ready/schedulable. No application workload, Ingress, or `Ready=True`; single-node local-path only and production gates remain open.

## :material-book-open-page-variant-outline: Current MariaDB Reconciliation Validation

- The development stack through MariaDB reconciliation is published on `origin/dev` at `55212b0`, including the foundational gate, four-Service slice, MariaDB evidence/contract, and pure preparation commit `5a2dfce`; it is undeployed.
- Validation passed before publication: `uv run pytest tests/unit` (316 passed); Ruff lint/format; mypy; Helm lint/template; and `git diff --check`.
- Coverage includes the marker-plus-four foundational gate and Service slice, followed by MariaDB desired-state preparation and local reconciliation integration: ordered reads; only `404` absent; all validation, classification, preflight, rendering, and manifest construction before writes; stable mutation-free `InvalidRuntimeConfiguration`; exact PVC create/no-write reuse; guarded ConfigMap, Secret, Service, and StatefulSet SSA; foundational writes, Services in frozen order, MariaDB resources, then marker last; PVC `get`/`create`; StatefulSet `get`/`create`/`patch`; no rollback; sanitized retry failures; stable `ResourceCollision`; and `Ready=False/RuntimeNotImplemented`.

The four-Service slice, MariaDB pure desired-state preparation, and reconciliation are part of the published `55212b0` development stack on `origin/dev` and released operator `0.5.6`. Production backup/restore, HA, and RPO/RTO remain open; `Ready=False/RuntimeNotImplemented` remains truthful.

## :material-book-open-page-variant-outline: Released Memcached Validation And POC

- Source `063e438ef416599e9816a2400afcc5a5a7af9aa0` ("Implement Memcached reconciliation") was published by CIXpress pipeline `4dcpfk` using `Default`, started `2026-08-22T12:29:20+00:00`, completed `12:30:25+00:00`, and reported top-level plus `git-clone`, `kaniko-build`, `helm-update`, and `cleanup` as `SUCCEEDED`. CI-owned commit `cb6b055eaf5e74c99e26c1c3d662b2d749331627` released chart/app/image `0.5.8`.
- In disposable single-node namespace `coriolis-memcached-validation-20260822`, CR `memcached-validation` created the Memcached Deployment in 44s; the original Deployment reached Ready 3s later, and MariaDB and Memcached both reached `1/1`. The released operator image was `cr.virtomat.io/virtomat/coriolis/operator:0.5.8` with imageID `sha256:9af4b018c2a7c0a23635d115d5335477b17bb81a731979bd0c93083c88461af4`; the Memcached Pod imageID matched approved digest `sha256:746b93082a4f6d07f464e93d4b14f5e30510abf17a9ae0a4af20e111408c8f1e`.
- The live Deployment passed its CR owner, one-replica `Recreate`, exact approved Memcached digest, direct command/args, port/pull Secret, UID/GID `42457`, disabled automount/service links, 30-second grace, restricted context, exact protocol probes, and absence of init containers, volumes/mounts, environment, configuration, credentials, PVC, and resource API. The Service selector and EndpointSlice targeted the Ready Pod on TCP `11211`.
- Service-DNS `version` and fixed set/get passed. Normal Pod deletion produced a distinct-UID replacement Ready in 3.603s with zero restarts, the same digest, and a ready endpoint; the original key returned only `END`, then fresh version/set/get passed. This proves ephemerality only, not persistence, HA, credentials, configuration, resource API, production readiness, or overall appliance readiness.
- The CR correctly remained `Ready=False/RuntimeNotImplemented` while reconciled dependencies were healthy. Normal cleanup removed the CR, Helm release, namespace, retained PVC, Delete-policy PV `pvc-b2856ccb-88ed-4d60-9878-89ef06331be8`, and copied registry Secret; at that POC cleanup point the namespace/PV/release were absent, the node was Ready and schedulable, and zero appliances remained cluster-wide. That historical POC used Argo `coriolis-operator:0.5.8`; the later Keystone POC cleanup observed healthy `0.5.14`, `1/1`, with zero appliance CRs and no POC workload.
- That Memcached source baseline remains `uv run --offline pytest tests/unit` (326 passed), Ruff lint/format, mypy, Helm lint/template, and `git diff --check`. RabbitMQ `0.5.11` and Keystone `0.5.14` accepted released-artifact POC evidence are documented above. MariaDB CSI/cross-node and production gates remain open.

## :material-book-open-page-variant-outline: Live MariaDB POC

- CIXpress pipeline `8nownk` succeeded for source `6ba9c7e`; the isolated operator pulled `0.5.5` at `sha256:14e991746aaf42334f2e48b2982493c8a6544909e13bf9d55f80eccb50fa062e`.
- Clean first boot on `0.5.5` exposed anonymous MariaDB accounts shadowing `coriolis@%`; commit `3ee5d2d` adds `--skip-test-db`, passed `uv run --offline pytest tests/unit` (316 passed), Ruff lint/format, mypy, Helm lint/template, and `git diff --check`, and was released by pipeline `kpv306` as `0.5.6`.
- Released `0.5.6` clean first boot on single-node `local-path` passed without repair: PVC Bound in 4 seconds, Pod Ready in 17 seconds, zero anonymous accounts/test database, `fsGroup: 42434` write access, authenticated probes, retained-PVC and credential identity across CR recreation, no-write PVC reuse, database persistence, normal 12-second pod termination, and healthy same-node remount.
- CSI and cross-node attach/detach were not available in this one-node environment. Cleanup removed the CR, release, namespace, PVC, and reclaimed local-path PV without force or finalizer changes.

Barbican and all other Services remain deferred; no Ingress route is emitted before its backend Service exists.

For the controller skeleton, these local validations have passed:

- Python 3.12 Ruff format.
- Ruff lint.
- mypy.
- Unit tests.
- Helm lint.
- Helm template with CRDs.
- Container image build and non-root runtime identity.
- `git diff --check`.

For the local API-only `core` runtime slice, these local validations passed:

- 25 unit tests covering the CRD schema (profile enum/default, required non-empty `spec.version`, `status.acceptedVersion`, and the absence of CEL immutability rules), controller enforcement of the immutable accepted version, rejection and version-change-blocked conditions, the accepted API-only reconcile, profile defaulting and profile-change routing, and no-resource paths.
- Ruff lint.
- mypy.
- Helm lint and Helm template with CRDs.
- `git diff --check`.

This API slice is committed locally at `ab9df83` (branch `dev`, not pushed/deployed) and is absent from the deployed operator. No cluster or external service was changed by this API slice; the image mirror/pull gate remains passed, and no dependencies, bootstrap Jobs, services, storage, secrets, or Coriolis runtime workloads are implemented or deployed.

For the local metadata-only helper slice, these local validations passed:

- 44 unit tests covering `appliance_resource_name` (single lowercase DNS label <=63; dotted/overflow dot-to-hyphen prefix plus 12-character SHA-256; invalid appliance/component rejection), `appliance_identity`, `build_resource_metadata` (standard `app.kubernetes.io/*` and `coriolis.cloudbase.it/*` labels, full appliance-name annotation, exactly one of owner reference or retention), and `build_state_config_map` (standard metadata with component `operator-state` while retaining the shipped `state_config_map_name`).
- `uv run ruff check .` and `uv run ruff format --check .`.
- `uv run mypy src`.
- `git diff --check`.

The helper slice is committed locally at `fbab6e5` on `dev`, but not pushed or deployed; the deployed marker `0.5.3` is unchanged and carries no standard labels. The collision/migration marker API-layer slice is described below; retained-resource adoption and all runtime resource construction remain deferred.

For the local collision/migration marker API-layer slice, these local validations passed:

- 70 unit tests covering the pre-read classification (404 create; fully matching managed marker proceeds with unchanged body; compatible legacy `0.5.2`/`0.5.3` marker normalization in place with stale generation updated, including dotted/long names; `ResourceCollision` for partial/conflicting standard metadata, owner mismatch, incompatible legacy data, and owner-plus-retention metadata, never patching/adopting/deleting/renaming; preservation of a prior `acceptedVersion` and condition transition time), non-404 read error propagation without patching, `V1ConfigMap` object handling, and deterministic `ResourceCollision` conditions.
- `uv run ruff check .` and `uv run ruff format --check .`.
- `uv run mypy src`.
- `helm lint helm/` and `helm template coriolis-operator helm/ --include-crds`.
- `git diff --check`.

This slice is **committed locally at `d8df00f` on `dev`, but not pushed or deployed**; the deployed marker `0.5.3` is unchanged and lacks these pre-read/collision semantics. ConfigMap RBAC gains only `get`.

For the local retained-resource authorization/classification slice, these local validations passed:

- 94 unit tests (24 new for this slice) covering the pure `classify_retained_resource` classifier returning `RetainedClassification.ABSENT/REUSE/COLLISION`: absent resource eligible for creation; exact matching PVC/state Secret/CA-state reuse; changed creating-appliance UID with otherwise exact retained identity is `REUSE` (UID is deliberately ignored; a stale `coriolis.cloudbase.it/appliance-uid` annotation is treated as unrelated); name/namespace/appliance/component/retention mismatches collide; missing/partial labels and annotations collide; any owner reference collides (even a matching owner UID); unrelated extra labels/annotations are allowed; the external `coriolis-appliance-registry` Secret fails closed as `COLLISION` both when absent and when forged with exact matching metadata; mapping-shaped dict and real `V1Secret`/`V1PersistentVolumeClaim` model representations; and no input mutation.
- `uv run ruff check .` and `uv run ruff format --check .`.
- `uv run mypy src`.
- `helm lint helm/` and `helm template coriolis-operator helm/ --include-crds`.
- `git diff --check`.

This slice is **committed locally at `1b73045` on `dev`, but not pushed or deployed**; the deployed marker `0.5.3` is unchanged. It constructs/reconciles/patch/reads/adopts no runtime resource and adds no adoption mutations; external/pre-existing resources such as `coriolis-appliance-registry` fail closed as `COLLISION` and remain read-only and outside this classifier/reconciliation policy. A MariaDB vertical slice remains blocked by the remaining generators/builders/RBAC, storage, probes/readiness, and rotation gates.

For the local documentation-only Secret/configuration contract slice, these validations passed:

- No code, builders, values, RBAC, CRD, or runtime behavior changed; the change is confined to [docs/foundational-resource-contract.md](foundational-resource-contract.md) and the tracking/docs pages.
- The modified contract page was reviewed for contradictory claims: concrete Secret/ConfigMap names and key layouts and the primary `coriolis.conf` split are stated as frozen, not unresolved, and the remaining genuinely-unresolved items are listed under the contract's Remaining Unresolved Secret Items and Unresolved Gates.
- `uv run pytest`; `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; `git diff --check`.

This slice is **committed locally at `8ce26ba` on `dev`, but not pushed or deployed**; the deployed `0.5.3` remains marker-only and unchanged, and no runtime resources or adoption mutations exist.

## :material-book-open-page-variant-outline: Pure Secret/ConfigMap Builder Slice

- 116 total tests, including 22 new builder cases from the previous 94; 21 cases matched the final focused selector. Coverage includes deterministic names/standard metadata; ownerless retained credential Secrets with retention metadata; owner-referenced rebuildable configuration resources without retention; exact key sets; opaque caller-provided string inputs without mutation; missing, extra, and non-string input failures without value exposure; `Opaque` UTF-8/base64 Secret `data` with no `stringData`; and plain six-file ConfigMap data that excludes `coriolis.conf` and credentials.
- `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; and `git diff --check` passed.

The five pure builders (`build_coriolis_credentials_secret`, `build_infrastructure_credentials_secret`, `build_step_ca_credentials_secret`, `build_coriolis_config_map`, and `build_coriolis_config_secret`) are **committed locally at `050f16e` on `dev`, but not pushed or deployed**. This covers manifest construction only: no credential generation, `main.py` reconciliation, Kubernetes reads/SSA, RBAC, CRD, runtime resources, status/readiness, or deployment changed; deployed `0.5.3` remains marker-only.

## :material-book-open-page-variant-outline: Pure Retained Credential Generation Slice

- 132 total tests, including 16 new cases from the previous 116, plus focused tests. Coverage includes independent generation of all seven frozen keys through `secrets.token_urlsafe(32)` (32 random bytes/256 bits, URL-safe opaque strings), deterministic token-factory injection for tests only, invalid empty/non-string factory outputs failing without value exposure, unchanged composition with existing builders, and no credential values in failures.
- `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; and `git diff --check` passed.

The pure helpers `generate_coriolis_credentials`, `generate_infrastructure_credentials`, and `generate_step_ca_credentials` are **committed locally at `a604579` on `dev`, but not pushed or deployed**. They are not called by `main.py`; no controller reconciliation, Kubernetes reads/writes/SSA, RBAC, CRD, runtime resources, status/readiness, chart/release, deployment, or rotation changed, and deployed `0.5.3` remains marker-only. The frozen policy is operator-generated only, with no inline CR credential values or external credential Secret source. Runtime generate-once/reuse remains pending: generate only for `ABSENT`, reuse exact matching ownerless retained Secrets unchanged, fail closed on collisions, and defer rotation.

## :material-book-open-page-variant-outline: Retained Secret Semantic Validation/Extraction Slice

- 152 total tests, including 20 new cases from the previous 132, plus focused 20 tests. Coverage includes mapping-shaped objects and Kubernetes `V1Secret` models; optional non-conflicting `apiVersion`/`kind`; required `Opaque` type; rejection of persisted `stringData`; exact frozen `data` keys with string encoded values; strict base64 then UTF-8 decoding; empty decoded-value rejection; a new decoded mapping without input mutation; and fixed/category-only failures without value exposure.
- `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; and `git diff --check` passed.

`validated_retained_secret_values` is **committed locally at `5165629` on `dev`, but not pushed or deployed**. It validates semantics only: no metadata classification, Kubernetes reads/writes, generation, SSA, collision/status handling, or reconciliation. No `main.py`, RBAC, CRD, runtime resource, chart/release, deployment, or rotation behavior changed; deployed `0.5.3` remains marker-only. The subsequent pure preflight classifies metadata first and maps semantic failure fail-closed to `COLLISION`; decoded values remain internal and are never logged, statused, or evented.

## :material-book-open-page-variant-outline: Non-Sensitive Configuration Rendering Slice

- 19 focused renderer tests and 171 total tests passed. Coverage includes explicit input validation, Jinja2 `PackageLoader`/`StrictUndefined` behavior, disabled autoescape, preserved trailing newlines, all six frozen ConfigMap keys, `accepted_version` mapping to legacy `default_coriolis_docker_images_tag`, value-safe failures, wheel resource inspection, and byte comparison against the six verbatim Apache-2.0 upstream templates.
- No `main.py`, reconciliation, Kubernetes reads/writes, SSA, RBAC, CRD, runtime resources, workload, release/chart/image version, deployment, TLS, storage, readiness, bootstrap, or rotation behavior changed.

Pure `render_coriolis_config` is **committed locally at `97153a7` on `dev`, but not pushed or deployed**. It renders no `coriolis.conf`, provider fragments, credentials, or other Secret content; deployed `0.5.3` remains marker-only.

## :material-book-open-page-variant-outline: Pure Foundational Five-Resource Preflight

- 23 focused tests and 194 total tests passed. Coverage includes `OwnedClassification.ABSENT/MANAGED/COLLISION`, `classify_owned_resource`, credential-safe frozen `FoundationalResourcePreflight`, and `preflight_foundational_resources` across exactly three retained credential Secrets plus the owner-referenced configuration ConfigMap and Secret.
- Metadata is classified for all five before retained Secret semantics: exact owner metadata/controller matches are `MANAGED` despite content/type drift; mismatched ownership or retention metadata is `COLLISION`; unrelated extra metadata is allowed; a metadata collision stops semantics and generation. `REUSE` retained Secrets are semantically validated, validation `ValueError` maps to that resource's `COLLISION`, all reusable semantics complete before generation, and generators run only for corresponding `ABSENT` retained Secrets after a collision-free preflight. Generated/decoded credentials are excluded from `repr`, errors, logs, status, and events.
- `uv run ruff check .`; `uv run ruff format --check .`; `uv run mypy src`; `helm lint helm/`; `helm template coriolis-operator helm/ --include-crds`; and `git diff --check` passed.

This pure slice is **committed locally at `35eac9b` on `dev`, but not pushed or deployed**. It changes no `main.py`, Kubernetes reads/writes, SSA, RBAC, CRD, runtime construction/adoption, reconciliation/status behavior, chart/release/image version, deployment, TLS/storage/readiness/bootstrap/rotation behavior; deployed `0.5.3` remains marker-only.

## :material-book-open-page-variant-outline: Sensitive Renderer Contract (Documentation-Only)

- The documentation-only contract is **committed locally at `574efcf` on `dev`, but not pushed or deployed**. No renderer implementation/tests or runtime behavior changed; deployed `0.5.3` remains marker-only.
- Documentation validation reviewed every immutable upstream base-template variable and all 16 provider fragments. It preserved authoritative provider lists/order/module maps, froze no initial custom module overrides and disabled compression/compressor, and confirmed the complete `coriolis.conf` remains exactly one key in the owner-referenced configuration Secret, never ConfigMap/log/status/event/metadata/documentation content.
- At that stage, documentation validation also confirmed explicit internal dependency inputs, source-audited identities/paths, credential mappings, strict value-safe validation, redacted interface boundary, no CRD fields, and exactly `coriolis.cloudbase.it/retention: state-credentials` on all three generated credential Secrets. TLS/CA material, provider connection/private data, optional credentials, dependency Services/workloads, bootstrap, storage, readiness, and rotation remained deferred.
- `git diff --check` passed. This is documentation validation evidence, not code-test evidence; no code test count is claimed.

At `574efcf`, the next sequence was pure renderer implementation/tests, then multi-resource semantics; this is retained documentation-contract history.

## :material-book-open-page-variant-outline: Pure Sensitive Configuration Rendering Slice

- 40 focused configuration tests and 215 total tests passed. Coverage includes `SensitiveCoriolisEndpoints`, `SensitiveCoriolisCredentials`, redacted one-key `SensitiveCoriolisConfig`, `render_sensitive_coriolis_config`, frozen/exact unmutated inputs, redacted credential/output reprs, fixed/category-only value-safe errors, exact one-key composition with the existing configuration-Secret builder, and ConfigMap-boundary rejection.
- Jinja `PackageLoader`, `StrictUndefined`, disabled autoescape, and trailing newline validation passed for the immutable upstream base plus all 16 provider fragments; 17/17 source byte parity and offline wheel inspection of exactly 25 expected template resources passed. Frozen provider lists/order/module maps, source-audited values/paths, source/license attribution, prohibited custom overrides, and disabled compression/compressor are covered.
- Ruff lint/format, strict mypy, Helm lint/template, and `git diff --check` passed.

The pure renderer is **committed locally at `9bb20f3` on `dev`, but not pushed or deployed**; deployed `0.5.3` remains marker-only. No `main.py`, reconciliation, Kubernetes reads/writes/SSA, RBAC, CRD, runtime resources/workloads, chart/release/image version, deployment, TLS/CA/bootstrap, provider/private data, optional credentials, storage, readiness, or rotation behavior changed. Historical five-resource policy remains documentation evidence; the current runtime gate is collision-safe marker-plus-four reads/create/guarded SSA, Secret/ConfigMap `get`/`create`/`patch` RBAC, sanitized status then Kopf retry, and exhaustive tests.

## :material-book-open-page-variant-outline: Foundational Multi-Resource Failure Contract (Documentation-Only)

- The historical, superseded policy in [Foundational Resource Contract](foundational-resource-contract.md) records validation; marker and five-resource pre-reads/classification; pure preparation before mutation; canonical operations; marker last; non-transactional failure handling; and value-safe status outcomes.
- This documentation-only slice changes no runtime code, Kubernetes interaction, RBAC, CRD, resource, chart/release, or deployment behavior. `git diff --check` is its only validation; no code test count is claimed. The current code coverage fact remains that API failures presently propagate, which is distinct from the frozen future runtime semantics.
- At that stage, the next implementation was collision-safe marker-plus-four reads/create/guarded SSA, Secret/ConfigMap `get`/`create`/`patch` RBAC, sanitized status then Kopf retry, and exhaustive tests. Services, Ingress, and workloads followed later.

## :material-book-open-page-variant-outline: Current Pure Contract/Input Migration

- 218 unit tests passed. Focused code evidence covered 186 tests and focused ingress/CRD evidence covered 32 tests.
- The pure factory derives deterministic RabbitMQ, Memcached, MariaDB, and Keystone Service names; fixes API `0.0.0.0:7667`, `/etc/coriolis`, and `/var/log/coriolis/vmware-root`; and updates Kubernetes-derived templates to RabbitMQ plaintext `5672`, Keystone HTTP `5000`, and HTTP WSGI without internal CA/TLS directives. The slice changes the CRD ingress schema/sample and adds pure validation for host/class/TLS inputs, including the derived `<host>-tls` Secret name.
- Ruff lint/format, strict mypy, Helm lint/template, and `git diff --check` passed. Byte comparison preserved all 23 immutable copied upstream templates; the offline wheel contained all 27 expected template resources, including the two Kubernetes-derived variants.

This migration is **committed locally at `e2ddb30` on `dev`, but not pushed or deployed**. It adds no `main.py`, runtime Kubernetes I/O, SSA/RBAC, actual Service/Ingress/workload resources, or release/chart/image/deployment behavior; deployed `0.5.3` remains marker-only.

Live-cluster controller lifecycle validation passed for release `0.5.2` in the approved `coriolis` namespace and was not repeated in full for later releases. The separate Memcached POC used healthy `0.5.8`; the later Keystone POC cleanup observed healthy `0.5.14` with no appliance CR.

The approved dev cluster is `infra-dev-buc-hq` (`virt-infra-dev-buc-hq`); CIXpress remains approved for read-only pipeline troubleshooting and monitoring only. The dedicated operator namespace is `coriolis`. See [Development Environment](dev-environment.md).

The completed `0.5.2` lifecycle validation covered:

- Apply the sample and check status, ownership, and marker configuration data.
- Replace the controller pod and verify reconciliation resumes without changing ownership, marker uniqueness, or condition transition times.
- Update `spec.version` and verify generation, observed generation, status, and marker data.
- Delete the resource and verify normal garbage collection.

The local controller coverage includes:

- CRD structure and namespaced scope.
- Namespace-scoped watch configuration.
- Idempotent marker ConfigMap reconciliation.
- Successful reconciliation status.
- `Ready=False` until an appliance runtime exists.
- Kubernetes API failures propagate without a custom failure condition.
- Absence of finalizers and destructive behavior.

CIXpress CI behavior is documented but not configured in this repository. Pending integration validation includes receiving the exact pipeline configuration, Template, and Job manifests; validating version alignment and the dev tag check; and defining trigger and monitoring credentials without storing secrets. Argo CRD pre-upgrade automation must be validated as promotion/deployment work, not assumed to be handled by the standard build pipeline.

Do not treat registry publication/authentication validation, promotion, or licensing decisions as settled test assumptions.
