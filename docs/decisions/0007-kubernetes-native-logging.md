# 0007: Use Loki And Alloy For Kubernetes-Native Logging

## :material-book-open-page-variant-outline: Context

Milestone 8 requires Kubernetes collection of container stdout/stderr and UI-compatible `/logs` plus WebSocket `/log-stream` records that preserve arbitrary values safely. `Ready=True` must remain unchanged and bounded.

The operator now manages the logging stack per `CoriolisAppliance` CR. An earlier platform-managed/shared design (platform-selected Alloy collection and platform-provisioned Grafana Loki, one tenant per CR) is superseded: read-only discovery in `virt-infra-dev-buc-hq` found no Loki or Alloy, and that platform framing was never provisioned. The tenant is derived exactly as `coriolis-<CR UID>`.

The immutable legacy logger input remains clean at `db67ca3c0d95d738679696970529897612325ee4` (`1.0.5`). It is a behavioral reference only; its legacy image mapping and InfluxDB persistence are not implementation dependencies.

## :material-book-open-page-variant-outline: Decision

Use the operator-managed per-CR logging stack. For each CR the operator owns and reconciles:

- a retained ownerless Loki RWO PVC and a logging-credentials Secret;
- a Loki ConfigMap, a gateway-config Secret/Service, and a Loki plus unprivileged-NGINX StatefulSet;
- an Alloy ConfigMap/ServiceAccount/Role/RoleBinding/Deployment;
- an adaptor Deployment/Service and a dedicated Ingress exposing it at `/logs` and `/log-stream`.

Tenant is exactly `coriolis-<CR UID>`; same-name CR recreation reuses the credentials/PVC but derives a new tenant and cannot query old tenant records. Loki binds loopback only; NGINX is the sole internal exposure, with exact path/method read/write Basic-auth allowlists, tenant overwrite, upstream Authorization stripping, disabled access logs, and WebSocket tail support. Alloy uses the namespaced Kubernetes API, is ephemeral and unprivileged, has no PVC, hostPath, DaemonSet, privilege, node metadata, Events, or cluster-scoped RBAC, uses an exact component allowlist, and holds only the write credential. The adaptor mounts only the read credential and is exposed directly at `/logs` and `/log-stream` after its Deployment and Service are ready; no legacy logger route is active.

Each adaptor validates Keystone itself and never forwards a user token to the backend. This decision does not authorize a privileged or hostPath DaemonSet, a shared socket, or a legacy logger/InfluxDB bridge. `spec.logging` is required; `retentionHours` accepts any positive integer; each logging component has explicit requests/limits.

## :material-book-open-page-variant-outline: Operator-Managed Stack Evidence

The operator implementation lives in `src/coriolis_operator/logging.py` and the `LoggingReady` observation in `src/coriolis_operator/main.py`. The `coriolis-logs-api-adaptor` provides the legacy-compatible log list, download, and WebSocket payloads against the operator-managed Loki/gateway.

Verified local implementation gates:

- `uv run pytest tests/unit`: 166 adaptor tests passed in 2.68s with one deprecation warning.
- Ruff lint passed; Ruff format checked 19 files.
- Strict mypy passed 10 source files.
- Targeted main sanity rerun passed 25 config tests in 1.06s.
- Local operator unit/static/Helm validation covers the required `spec.logging`/`retentionHours` schema, the unified logging preflight, staging order, and the owned resource manifests.
- Local implementation uses Loki `query_range` for bounded history and `tail` for streaming, with forward pagination, overlap replay, stable ordering, deduplication, bounded reconnect/backoff, and limits.

### :material-application-edit-outline: Container Gate

An ephemeral verification image `3e46bf5e48ce` (removed after test) started successfully as UID 10001 under Docker `--read-only`, dropped capabilities, no network, and no host mounts. Writes to `/` and `/app` fail read-only. App startup completed on 8080 using synthetic tmpfs-only config. No residual verification image or container remains.

## :material-book-open-page-variant-outline: Cluster Qualification Gate

Milestone 8 is not yet fully qualified. The operator-managed stack is implemented and locally validated, but isolated dev-cluster qualification and released-operator-artifact testing remain pending. The gate must prove:

- ingestion and strict cross-CR isolation (tenant `coriolis-<CR UID>`);
- bounded history and retention, ordered stream cursors, tail and reconnect behavior;
- direct `/logs` and `/log-stream` routing through the dedicated adaptor Ingress;
- that query-token requests do not expose tokens in fixed output;
- retention-expiration handling while the stack is absent and cleanup after same-name reuse/recreation;
- normal cleanup.

## :material-book-open-page-variant-outline: Compatibility And Safety Contract

Each adaptor is restricted to its owning CR and references that owner. It must return value-safe records for list, download, and WebSocket clients, and must not permit user-selected namespaces, appliances, components, or unconstrained time ranges.

Keystone validates admin role. `X-Auth-Token` takes precedence; the query token is accepted only with `auth_type=keystone`. The user token is sent only to Keystone and never to Loki. Loki uses per-tenant Basic credentials read from a mounted file. Access logging is disabled and errors are sanitized. NGINX is the only internal exposure and enforces the exact path/method Basic-auth allowlists with tenant overwrite and upstream Authorization stripping.

Reconciliation remains value-safe and must not surface credentials through controller output, conditions, or Events. The completion marker is applied last.

## :material-book-open-page-variant-outline: Rejected Alternatives

The legacy logger/InfluxDB bridge is rejected because it requires collector, socket, or Syslog adaptation; has an image-provenance gap; and its request logging can expose query credentials. It remains rejected under the operator-managed design.

Direct Pod Logs API proxying is rejected because it does not provide durable history, retention, or reliable replay.

An unapproved privileged or hostPath collector/store, a shared socket, and the earlier platform-managed/shared provisioning dependency are rejected: the operator now owns the per-CR stack with an explicit unprivileged, ephemeral, namespaced-API Alloy and an adaptor-only read credential.

## :material-book-open-page-variant-outline: Consequences

The operator-managed per-CR decision supersedes the platform-managed/shared framing. The architecture and local implementation blockers are resolved. Milestone 8 end-to-end acceptance remains pending only on isolated dev-cluster qualification and released-operator-artifact testing.

No production HA, storage, backup, or object-storage claim is made. The operator's core `Ready` semantics and the six core conditions do not change; periodic observation appends an independent `LoggingReady` condition. Without a running Loki compactor, physical retention expiration pauses while the stack is absent; records remain tenant-inaccessible and cleanup resumes after same-name stack reuse/recreation, or storage disappears with namespace/PV removal.