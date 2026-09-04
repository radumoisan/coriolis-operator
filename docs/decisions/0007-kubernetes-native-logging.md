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

## :material-book-open-page-variant-outline: Released-Artifact Qualification Evidence

Milestone 8 is accepted and closed; it was previously functionally qualified with explicit residuals, all now closed. The operator-managed stack and its compatibility adaptor were qualified from released operator `0.5.49` (source `e59833c`, CIXpress Default `wcx6h3` all expected steps `SUCCEEDED`, CI commit `40c0f6d`, operator digest `sha256:68eacc65ce877065d6850c7888eb06a3a09f15254da206ed8168851fab31d44e`, local gate 812 unit tests plus Ruff/mypy/Helm/diff). In isolated context `virt-infra-dev-buc-hq`, namespace/Application `coriolis-logging-validation-20260829`, first public host `coriolis.app.cloudbase.wiki`, `1h` retention, and `local-path` 10Gi, qualification proved:

- ingestion and strict cross-CR isolation (tenant `coriolis-<CR UID>`): same-name recreation changed CR UID prefix `a923292a` to `ecd8756f`, garbage-collected 44 old-owned resources, preserved the retained logging Secret/PVC exact UID/RV/PV, held 43 new/0 old references, produced a fresh single-parent-mount Alloy, and kept one Loki ingester ACTIVE with 128 tokens while old and new tenants separately held data;
- bounded history and retention, ordered stream cursors, tail and reconnect behavior: `/logs` listed 8 components on the first public host; a bounded Keystone download was identical streaming/non-chunked at 96,091 bytes/566 lines; valid WebSocket frames, client reconnect, and negatives passed; one ~4.6s timestamp inversion was shown as-arrival, not global monotonic order;
- direct `/logs` and `/log-stream` routing through the dedicated adaptor Ingress on the first public host with valid TLS;
- query-token fixed-output safety: the query-token exact value matched zero times over 25 inspected surfaces, and the dedicated query-token test passed zero exact matches;
- retention-expiration handling: old fixed window baseline 2204 with terminal `2026-08-29T23:50:30Z` and a later exact query of zero; the continuous compactor completed 21 retention applies past the `1h`+`2h` checkpoint with pending/missing zero;
- normal cleanup: both CRs plus 80 total old-owner resources by normal GC, the Application, retained state/routes, and six Delete-policy PVs; after the user explicitly approved the exact guarded removal of the failed nip.io ACME Challenge finalizer, the namespace was absent.

Concurrent CR B (UID prefix `551e2fc6`) had both stacks Ready + `LoggingReady` with zero restarts; its nip.io certificate failed DNS-01 because the zone is unmanaged (insecure TLS is never used or claimed), and its adaptor was tested by Service port-forward. Markers isolated (own `1`/foreign `0`), own credentials `200`/cross `401`, spoof headers overwritten, and direct own=`100`/foreign=`0` both ways, stable.

The original 2026-08-29 qualification's physical-deletion wording is retained as historical evidence. Follow-up closure used value-silent `scripts/validate-coriolis-retention-runtime.py` (58 focused tests and Ruff). A released-digest diagnostic with test-only `10m` retention, `1m` compaction, and `5m` delete delay proved loaded configuration, 12 persisted candidates, a marker query that persisted then reached zero, absent candidate paths, zero deletion-marker files, and `SUMMARY retention passed 1480.246s` (`1338.911s` retention stage). The untouched-release formal run in `coriolis-logging-retention-formal-20260830` loaded `1h`/`15m`/`2h`, retained the logging Secret/PVC exact UID/RV and credential values across same-name recreation without emitting them, proved the new tenant marker zero while the direct old-tenant query remained positive before the exact window, then reached old-tenant zero with all 12 old-prefix candidate files physically absent. It recorded `SUMMARY retention-formal passed 11152.884s` (`10693.777s` retention stage); its global deletion-marker count was 2, non-gating and not attributed to old candidates.

The final residual was exactly one:

- the whole run is not credential-safe: one exploratory command printed the first CR's disposable Keystone admin password into an internal tool transcript, and no strict formal run has completed end-to-end to replace that qualification. The 2026-08-31 strict value-silent qualification (context `virt-infra-dev-buc-hq`, namespace/Application `coriolis-logging-closure-20260831`, apps A/B `logging-primary`/`logging-isolation`, public hosts `coriolis.app.cloudbase.wiki` and `coriolis-logging-isolation.app.cloudbase.wiki`, released `0.5.49` at the already-documented exact digest, a dual-SAN Let's Encrypt fixture certificate used through `existingSecret` on both apps) passed all TLS/redirect/identity/isolation/public HTTPS/WSS/audit-matrix stages and closed live backend-tail reconnect end-to-end: with one public WSS client kept open, exactly one validated non-master unprivileged NGINX gateway worker was TERM'd, A/B/C order/continuity passed, and the gateway Pod/container restart count was unchanged; the parent captured and audited child output value-silently, and normal cleanup after each attempt removed the target CRs/Application/namespace/claimRef PVs/routes/certs with shared Argo/operator/node healthy and no force or finalizer edits. The residual remains open because the first corrected formal run passed reconnect then failed retention when the child inherited a `25m` max wait for the `3h` window, and the next passed reconnect then failed `retention-cr-ready` when the recreated CR inherited `30s` against a measured `63-110s`; the code now explicitly passes max-wait `210m` and command/readiness timeout `300s`, safe fixed child failure prefixing, and PID1-aware exact-one-child worker selection. Another strict run is blocked until the CA exact-SAN duplicate allowance refills: five dual-SAN issuances were consumed during diagnostics and formal retries, and a cluster-wide metadata-only search found no existing wildcard/exact certificate covering both hosts; no reset timestamp is inferred.

That residual was closed on 2026-09-03: after a clean read-only preflight, the exact formal command `uv run python scripts/validate-coriolis-logging-qualification.py --context virt-infra-dev-buc-hq --namespace coriolis-logging-closure-20260831 --application coriolis-logging-closure-20260831 --app-a logging-primary --host-a coriolis.app.cloudbase.wiki --app-b logging-isolation --host-b coriolis-logging-isolation.app.cloudbase.wiki --mode formal --run` ran exactly once with no separate issuance probe and no retry, and the process exited 0. The fixed transcript passed namespace, secrets, tls-fixture, operator, app-a, app-b, all 13 matrix stages, reconnect, retention, final-audit, and cleanup, ending with `PASS final-audit`, `PASS cleanup`, and `SUMMARY logging-formal passed 11602.859`, with no stderr and no credentials, encoded forms, tokens, raw child output, ACME URLs, or key material. Independent post-run verification found the target Namespace, Argo Application, both `CoriolisAppliance` CRs, target claimRef PVs, and both host Ingress and Certificate claims absent; shared `argocd/coriolis` `Synced/Healthy` with no in-flight operation; operator `1/1`; and node Ready/schedulable. The released operator/chart remained `0.5.49` with `skipCrds` at the already-documented exact digest. The earlier exact-SAN rate-limit diagnosis and Retry-After remain historical context. This corrected whole run supplied the missing whole-run value-silent evidence; the 2026-08-29 credential incident remains a historical incident and is not described as retroactively remediated, and no claim is made that all future runs are safe.

The second public TLS endpoint residual is closed: a controlled DNS focused rerun (context `virt-infra-dev-buc-hq`/`default`, namespace/Application `coriolis-logging-tls-validation-20260830`, released `0.5.49` with `skipCrds`, host `coriolis-logging-isolation.app.cloudbase.wiki`) passed default-trust checks with exact HTTP `308`, hostname/SAN and Let's Encrypt chain, unauthenticated `/logs` `401`, authorized `/logs` `200`, and WSS `/log-stream` `101` with a valid frame, with no insecure TLS and no credential/token value printed; cleanup was normal. At that point Milestone 8 remained open only for the one residual closed by the 2026-09-03 qualification above.

## :material-book-open-page-variant-outline: Compatibility And Safety Contract

Each adaptor is restricted to its owning CR and references that owner. It must return value-safe records for list, download, and WebSocket clients, and must not permit user-selected namespaces, appliances, components, or unconstrained time ranges.

Keystone validates admin role. `X-Auth-Token` takes precedence; the query token is accepted only with `auth_type=keystone`. The user token is sent only to Keystone and never to Loki. Loki uses per-tenant Basic credentials read from a mounted file. Access logging is disabled and errors are sanitized. NGINX is the only internal exposure and enforces the exact path/method Basic-auth allowlists with tenant overwrite and upstream Authorization stripping.

Reconciliation remains value-safe and must not surface credentials through controller output, conditions, or Events. The completion marker is applied last.

## :material-book-open-page-variant-outline: Rejected Alternatives

The legacy logger/InfluxDB bridge is rejected because it requires collector, socket, or Syslog adaptation; has an image-provenance gap; and its request logging can expose query credentials. It remains rejected under the operator-managed design.

Direct Pod Logs API proxying is rejected because it does not provide durable history, retention, or reliable replay.

An unapproved privileged or hostPath collector/store, a shared socket, and the earlier platform-managed/shared provisioning dependency are rejected: the operator now owns the per-CR stack with an explicit unprivileged, ephemeral, namespaced-API Alloy and an adaptor-only read credential.

## :material-book-open-page-variant-outline: Consequences

The operator-managed per-CR decision supersedes the platform-managed/shared framing. The architecture and local implementation blockers are resolved. Milestone 8 is accepted and closed with all residuals closed; see the Released-Artifact Qualification Evidence section.

No production HA, storage, backup, or object-storage claim is made. The operator's core `Ready` semantics and the six core conditions do not change; periodic observation appends an independent `LoggingReady` condition. Without a running Loki compactor, physical retention expiration pauses while the stack is absent; records remain tenant-inaccessible and cleanup resumes after same-name stack reuse/recreation, or storage disappears with namespace/PV removal.
