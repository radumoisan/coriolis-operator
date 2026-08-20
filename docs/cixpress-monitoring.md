# CIXpress Pipeline Monitoring

!!! abstract
    Use this polling-only procedure to identify, monitor, and safely troubleshoot CIXpress pipelines in the approved development environment.

## :material-book-open-page-variant-outline: Safety Boundary

- Use only `kubectl --context virt-infra-dev-buc-hq -n cixpress` and authorized `exec` access to `deployment/conductor`.
- Query the internal API at `http://localhost:5000` through that exec path. Ingress may require deployment-specific authentication; this path uses Kubernetes authorization and does not bypass it.
- Use GET requests only: `/healthz/ready`, `/pipelines`, `/pipelines/{pipeline_id}`, and `/pipelines/{pipeline_id}/logs`.
- Never trigger pipelines, clear state, write configuration, use POST/PUT/PATCH/DELETE, mutate Kubernetes resources, inspect Secrets or credentials, port-forward, deploy into `cixpress`, or expose raw sensitive logs.
- Never use SSE or `/stream`; polling is the only supported monitoring mechanism.

## :material-book-open-page-variant-outline: Preflight And Discovery

Confirm that local `jq` and the in-container `curl` are available. Stop rather than installing a missing tool.

```sh
/usr/bin/jq --version
kubectl --context virt-infra-dev-buc-hq -n cixpress exec deployment/conductor -c conductor -- /usr/bin/curl --version
```

Start each new discovery session with a unique `User-Agent`, because CIXpress filters persist by derived session. Check readiness, then list sanitized pipeline fields:

```sh
kubectl --context virt-infra-dev-buc-hq -n cixpress exec deployment/conductor -c conductor -- \
  /usr/bin/curl -fsS -H "User-Agent: cixpress-pipeline-monitor-$(date +%s%N)" http://localhost:5000/healthz/ready
kubectl --context virt-infra-dev-buc-hq -n cixpress exec deployment/conductor -c conductor -- \
  /usr/bin/curl -fsS -H "User-Agent: cixpress-pipeline-monitor-$(date +%s%N)" \
  "http://localhost:5000/pipelines?itemsPerPage=200&startIdx=0" |
  /usr/bin/jq '[if type == "object" then to_entries[] | {id: (.value.id // .key), templateName: .value.templateName, head_id: (.value.head.id // null), state: .value.state, start_time: .value.start_time, completion_time: .value.completion_time} else .[] | {id, templateName, head_id: (.head.id // null), state, start_time, completion_time} end] |
    sort_by(.start_time // "") | reverse'
```

Identify a pipeline in this order: an explicit valid six-character ID; an exact commit SHA; then a unique commit prefix together with template and time. Do not guess when multiple candidates match. Repository and branch filters are not guaranteed. The observed list ordering is descending by start time, but it is not guaranteed: sort explicitly before selecting a candidate.

Replace `<pipeline-id>` with a candidate only, then validate it before using it. Stop on failure:

```sh
pipeline_id='<pipeline-id>'
if ! [[ "$pipeline_id" =~ ^[A-Za-z0-9]{6}$ ]]; then
  printf '%s\n' 'invalid pipeline ID; stopping' >&2
  exit 1
fi
```

## :material-book-open-page-variant-outline: Bounded Polling

Fetch detail with a fresh request. The detail response is an ID-keyed object or `{}`. Normalize object or array steps while emitting only the listed safe fields. Replace `<pipeline-id>` only after validation.

```sh
kubectl --context virt-infra-dev-buc-hq -n cixpress exec deployment/conductor -c conductor -- \
  /usr/bin/curl -fsS -H "User-Agent: cixpress-pipeline-monitor-$(date +%s%N)" "http://localhost:5000/pipelines/<pipeline-id>" |
  /usr/bin/jq --arg id '<pipeline-id>' 'if has($id) then .[$id] |
    {id: (.id // $id), templateName, head_id: (.head.id // null), state, start_time, completion_time,
     steps: [(.steps // []) | if type == "object" then to_entries[] | {name: (.value.name // .key), identifier: .value.identifier, state: .value.state, start_time: .value.start_time, completion_time: .value.completion_time} else .[] | {name: (.name // .identifier), identifier, state, start_time, completion_time} end]}
    else {id: $id, error: "pipeline not found"} end'
```

Poll this individual bounded GET every 15 seconds, at most 20 times (five minutes), unless a different bound is requested. Compare each result with the previous one, report transitions, and stop at a terminal state. Do not use an infinite loop and do not treat HTTP 202 as success.

The standard operator steps are `git-clone`, `kaniko-build`, `helm-update`, and `cleanup`. Success requires every expected step to be confirmed `SUCCEEDED`. If top-level state is present but steps are empty, report the top-level state and that per-step confirmation is unavailable. `INPROGRESS` with a `completion_time`, or any contradictory data, is stale/inconsistent rather than evidence of completion. Missing Jobs or step data can be cleanup behavior, not success.

## :material-book-open-page-variant-outline: Safe Log Inspection

Inspect only an active, failed, or specifically requested step. Query metadata and line counts before any excerpt; request offset and step with `--data-urlencode`. Replace placeholders only after ID validation and selection.

```sh
kubectl --context virt-infra-dev-buc-hq -n cixpress exec deployment/conductor -c conductor -- \
  /usr/bin/curl -fsS -G -H "User-Agent: cixpress-pipeline-monitor-$(date +%s%N)" \
  --data-urlencode 'step=<step>' --data-urlencode 'offset=0' \
  "http://localhost:5000/pipelines/<pipeline-id>/logs" |
  /usr/bin/jq --arg pipeline_id '<pipeline-id>' --arg step '<step>' \
    '{pipeline_id: $pipeline_id, step: $step, streams: [(.logs // {}) | to_entries[] | {stream: .key, lines: (.value | length)}]}'
```

If an excerpt is necessary, use this optional best-effort transformation only after metadata inspection. It emits at most the last 40 lines per stream and redacts likely bearer, authorization, token, password, secret, API-key, JWT, and URL-userinfo values:

```sh
kubectl --context virt-infra-dev-buc-hq -n cixpress exec deployment/conductor -c conductor -- \
  /usr/bin/curl -fsS -G -H "User-Agent: cixpress-pipeline-monitor-$(date +%s%N)" \
  --data-urlencode 'step=<step>' --data-urlencode 'offset=0' \
  "http://localhost:5000/pipelines/<pipeline-id>/logs" |
  /usr/bin/jq --arg pipeline_id '<pipeline-id>' --arg step '<step>' '
    def redact:
      gsub("(?i)(?<prefix>bearer[[:space:]]+)[^[:space:],;]+"; "\(.prefix)[REDACTED]") |
      gsub("(?i)(?<prefix>(?:authorization|auth|token|password|secret|api[-_ ]?key)[[:space:]]*[:=][[:space:]]*)[^[:space:],;]+"; "\(.prefix)[REDACTED]") |
      gsub("(?i)(?<prefix>https?://)[^/@[:space:]]+@"; "\(.prefix)[REDACTED]@") |
      gsub("(?i)\\b[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\b"; "[REDACTED_JWT]");
    {pipeline_id: $pipeline_id, step: $step, streams: [(.logs // {}) | to_entries[] |
      {stream: .key, lines: [(.value | if type == "array" then .[-40:] else [] end)[] |
        if type == "string" then redact else "[NON_STRING_LINE]" end]}]}'
```

This redaction is best-effort. If safe redaction is uncertain, return metadata only; do not execute the excerpt command against live logs without that determination. Summarize rather than reproduce output. Offset semantics are only partially confirmed: a single 12-line stream returned zero lines at offset 12. Do not claim cross-stream completeness; track and report offsets conservatively.

## :material-book-open-page-variant-outline: Kubernetes Fallback And API Caveats

Kubernetes Jobs are a secondary read-only fallback only. Match names ending in `-job-<pipeline-id>`; no matching Job may mean it was cleaned up.

```sh
kubectl --context virt-infra-dev-buc-hq -n cixpress get jobs -o name |
  /usr/bin/jq -R -r --arg id '<pipeline-id>' 'select(endswith("-job-" + $id))'
```

The public contract is OpenAPI 3.0.3, API `0.13.2`, at <https://docs.voyager.virtomat.dev/ci-conductor/swagger.yaml>. It describes `/pipelines` as a map keyed by pipeline ID, while the deployed runtime also returns a list containing `id`; normalize both. Runtime list fields observed are `id`, `templateName`, `head`, `state`, `start_time`, `completion_time`, and `steps`. Observed pipeline states are `SUCCEEDED`, `FAILED`, and `INPROGRESS`; documented step states are `NOT_STARTED`, `STARTED`, `FAILED`, and `SUCCEEDED`.

## :material-book-open-page-variant-outline: Report

Report the pipeline ID, selection evidence, template, commit, start/completion times, top-level state, each expected step state, observed transitions, poll count, and whether step confirmation was available. For logs, report only requested step, HTTP outcome, stream count, line counts, offsets, and a safely redacted bounded summary. State uncertainty explicitly.
