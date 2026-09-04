# 0008: Use An Operator-Managed Per-Appliance Barbican For UI Endpoint Credentials

## :material-book-open-page-variant-outline: Context

Milestone 9 requires secure UI endpoint credentials and browser login before any UI migration POC: actual browser authentication plus browser-based creation, validation, listing/selection, and normal removal of both source and destination OpenStack endpoints, with no migration in Milestone 9.

The immutable `coriolis-web` already performs the complete browser flow against Barbican: login through `/identity`, POST of endpoint connection payloads to `/barbican/v1/secrets`, storage of only the returned `secret_ref` in Coriolis, polling until the secret is `ACTIVE` with payload, endpoint validation, and endpoint-then-secret deletion. Coriolis OSS resolves `secret_ref` through the caller's Keystone catalog. No upstream Web or OSS change is planned, so the appliance itself must provide a Barbican that the caller's catalog resolves.

The current released appliance has no Barbican Service, workload, route, or credentials. The user selected a per-appliance operator-managed Barbican and actual-browser acceptance.

## :material-book-open-page-variant-outline: Decision

Adopt an operator-managed Barbican per `CoriolisAppliance`, always-on for the bounded `core` profile with no CRD field or toggle, and qualify Milestone 9 through the actual-browser two-endpoint contract below. The architecture is accepted and implemented locally with the local gates passed; release and live actual-browser qualification remain pending and are not claimed.

Use `simple_crypto`. Its master `kek` is a static 32-byte URL-safe base64 configuration value; encrypted per-project key metadata and payload state persist in MariaDB, so no Barbican PVC exists. The insecure built-in default key is forbidden.

Deploy only the API and worker of the pinned `2023.1-ubuntu-jammy` Barbican images: API `cr.virtomat.io/virtomat/coriolis/barbican-api:2023.1-ubuntu-jammy@sha256:a142a57761f708b241358383d6445ac5da4e05ae26a284369081cfb15cca8a60` and worker `cr.virtomat.io/virtomat/coriolis/barbican-worker:2023.1-ubuntu-jammy@sha256:ed907de778900b08f2645c9eeb82d48d8202ce6517cdb543d42db2e88ea642b5`. All three Barbican images are Kolla `16.6.1` / Barbican `16.0.2` with default user UID/GID `42403` and supplemental `kolla` group `42400`. The API serves uWSGI on port `9311`; the worker command is `barbican-worker`. The keystone-listener image (`sha256:cc6ee5067f336a578e761a031116b32b60a08ba323d1c33f0758d0e1c43ba0cb`) handles only Keystone project-deletion cleanup and is omitted for the bounded endpoint CRUD scope.

## :material-book-open-page-variant-outline: Runtime Contract

### :material-application-edit-outline: Resources And Ownership

Per CR the operator owns: the retained ownerless `<appliance>-barbican-credentials` Secret with exactly `barbican_database_password`, `barbican_keystone_password`, and `barbican_crypto_key` (existing infrastructure/coriolis Secret keysets are not altered); an owned ConfigMap with the non-sensitive API paste/vassal/policy/bootstrap assets; an owned config Secret containing the rendered `barbican.conf`; an API ClusterIP Service and API Deployment with an idempotent DB-migration init container; a worker Deployment; and a dedicated Barbican Ingress. There is no listener, PVC, ServiceAccount, Kubernetes RBAC expansion, CRD field or toggle, or new readiness condition, and the existing allowed resource kinds and verbs suffice. The implementation is complete locally and renders exactly this surface, with venv-Python health probes and the idempotent db-sync init container in the API Deployment.

### :material-application-edit-outline: Bootstrap And Catalog Registration

The MariaDB bootstrap gains idempotent barbican database/user creation and the existing pod-template schema annotation bump (implemented as the `barbican-v1` schema value). A new common bootstrap revision `v3` registers the barbican service user in the service project with the admin role, the `key-manager` service, internal/public/admin endpoints pointing to the internal ClusterIP `http://<barbican-service>:9311`, and the required compatibility roles `key-manager:service-admin`, `creator`, `observer`, and `audit`, with the Barbican password supplied through the mounted credential file only. The browser still reaches Barbican through the public `/barbican` route; the internal catalog URL avoids a hairpin through the public Ingress for Coriolis-side resolution.

### :material-application-edit-outline: Ordering And Readiness

All reads and preflight precede writes; any collision is fail-closed and all-or-nothing; the completion marker remains the last write. The dedicated Ingress emits `/barbican(/|$)(.*)` rewritten to `/$2` only after the Service, API, and worker are ready, and core readiness now includes the Barbican API, worker, and Service. The locally implemented reconciliation enforces this: all six Barbican reads/preflight are fail-closed with explicit Service and Deployment duplicate-name guards, writes are dependency-safe, and route emission and core readiness gate on Service/API/worker readiness as above.

### :material-application-edit-outline: Security And Trust Boundary

Every Barbican container is non-root UID/GID `42403` (supplemental group `42400`), read-only root filesystem, no privilege escalation, dropped `ALL` capabilities, `RuntimeDefault` seccomp, and disabled service-account-token automount and service links. Writable paths are memory-backed `/tmp` for both workloads and an ephemeral `/var/lib/barbican` for the API; logs go to stdout/stderr. Sensitive values are file-mounted only and never enter environment variables, argv, status, events, or controller logs. The internal plaintext trusted-network boundary is unchanged; public TLS remains terminated by the existing ingress-nginx/cert-manager path.

## :material-book-open-page-variant-outline: Browser Acceptance

Acceptance uses the actually deployed UI/backend with a real browser and no mocked backend: login; create the source endpoint stored as a Barbican `secret_ref`; create the destination endpoint through a distinct `secret_ref`; validate both endpoints; list/select both only; delete both endpoints and then both Barbican secrets; and restore the zero endpoint/secret baseline. No transfer or migration is executed. Fixed durable evidence records statuses, shapes, and counts only, never values. Current approved-dev rules allow transient private-session handling of dev credentials but prohibit credentials in repository files, durable documentation, or final evidence. The fixed-output value-silent `scripts/validate-barbican-runtime.py` checks live resources, catalog registration, secret CRUD, cleanup, and stability, and the actual browser run is performed via the configured Playwright MCP against the real UI/backend; neither has been executed.

## :material-book-open-page-variant-outline: Rejected Alternatives

- Reusing or extending the existing infrastructure/coriolis credential Secret keysets for Barbican values: rejected in favor of a dedicated retained ownerless `<appliance>-barbican-credentials` Secret that leaves every frozen keyset unchanged.
- Including `barbican-keystone-listener`: rejected because it performs only Keystone project-deletion cleanup, which is outside the bounded endpoint CRUD flow.
- A dedicated Barbican PVC or filesystem payload store, or a hardware-backed crypto plugin: rejected because `simple_crypto` with MariaDB-persisted encrypted state closes the requirement without persistent Barbican filesystem storage.
- A shared or platform-provisioned Barbican across appliances: rejected; the user selected per-appliance operator-managed Barbican, consistent with the ADR 0007 operator-owned direction.
- Any upstream `coriolis-web`/Coriolis OSS change or continued inline `connection_info` endpoint creation as the UI path: rejected because upstream inputs are immutable and inline creation cannot qualify the secure UI credential flow.
- Resolving Barbican through the public URL from inside the cluster: rejected as a hairpin; internal catalog endpoints target the ClusterIP directly.

## :material-book-open-page-variant-outline: Consequences

The Milestone 9 architecture is accepted and the source implementation is complete locally: the full local gate passed `1181 passed in 70.35s`, Ruff check, Ruff format check over 85 files, strict mypy over 20 source files, Helm lint (0 failed), Helm template, and `git diff --check`, and the fixed-output value-silent `scripts/validate-barbican-runtime.py` passed 73 focused tests. The live validator has not been run and no build, deploy, release, live CRUD, or browser success is claimed; CI owns versioning. Milestone 9 remains open until release and the actual-browser two-endpoint acceptance above complete on the deployed UI/backend, performed via the configured Playwright MCP against the real UI/backend.

The appliance surface grows by one Service, two Deployments (API with its migration init container, and worker), one dedicated Ingress, one owned ConfigMap and config Secret, one retained credential Secret, one MariaDB database/user, one new common bootstrap revision, and the Barbican catalog objects and compatibility roles. Core `Ready` now depends on Barbican API, worker, and Service, widening the failure surface within the existing bounded readiness claim while adding no condition.

`simple_crypto`'s static retained `kek` means retained-Secret loss is unrecoverable for previously stored secrets; automatic rotation remains excluded, consistent with existing retained-credential policy. The omitted listener leaves Keystone project-deletion secret cleanup unperformed; the qualified browser flow deletes endpoint and secret normally instead.
