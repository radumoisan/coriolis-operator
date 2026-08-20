# CIXpress Pipeline Monitoring

!!! abstract
    Use this polling-only procedure to identify, monitor, and safely troubleshoot CIXpress pipelines in the approved development environment.

## :material-book-open-page-variant-outline: Safety Boundary

- Always use `kubectl --context virt-infra-dev-buc-hq -n cixpress` explicitly.
- Use authorized Kubernetes exec to query only `http://localhost:5000`; ingress may require deployment-specific authentication. This relies on Kubernetes authorization; never bypass it or inspect credentials.
- Allow only GET requests to `/healthz/ready`, `/pipelines`, `/pipelines/{pipeline_id}`, and `/pipelines/{pipeline_id}/logs`.
- Never use SSE or `/stream`. Polling is the only monitoring mechanism.
- Never trigger a pipeline, use `/clear`, write configuration, issue POST/PUT/PATCH/DELETE, mutate Kubernetes, inspect Secrets or credentials, port-forward, deploy into `cixpress`, or output raw sensitive logs.
- Never report HTTP 202 as successful data; it means acceptance only.

## :material-book-open-page-variant-outline: Preflight And GET Procedure

Require local `/usr/bin/jq` 1.6 and in-container `/usr/bin/curl`. Stop if either is unavailable; do not install tools.

```bash
set -o pipefail
/usr/bin/jq --version
kubectl --context virt-infra-dev-buc-hq -n cixpress exec deployment/conductor -c conductor -- /usr/bin/curl --version
```

Use this GET-only helper. It captures the body in memory and emits a structured HTTP outcome to stderr. Transport failures, curl pipeline failures, HTTP 202, and non-2xx responses stop processing.

```bash
cixpress_get() {
  local user_agent=$1 path=$2 response
  shift 2

  if ! response="$(kubectl --context virt-infra-dev-buc-hq -n cixpress exec deployment/conductor -c conductor -- \
    /usr/bin/curl -sS --request GET -G -H "User-Agent: $user_agent" \
    --write-out $'\n%{http_code}' "$@" "http://localhost:5000${path}")"; then
    printf '%s\n' 'GET transport or pipeline failure; stopping' >&2
    return 1
  fi
  http_status=${response##*$'\n'}
  http_body=${response%$'\n'*}
  if ! [[ $http_status =~ ^[0-9]{3}$ ]]; then
    printf '%s\n' '{"http_status":"unknown","error":"missing HTTP status"}' >&2
    return 1
  fi
  printf '{"http_status":%s}\n' "$http_status" >&2
  if [[ $http_status == 202 ]]; then
    printf '%s\n' 'HTTP 202 is acceptance-only, not valid pipeline data' >&2
    return 1
  fi
  if ! [[ $http_status =~ ^2[0-9]{2}$ ]]; then
    printf '%s\n' 'non-2xx HTTP response is not valid pipeline data' >&2
    return 1
  fi
}
```

The helper sets `http_status` and `http_body` for the immediately following guarded `jq` command. Never display `http_body` directly. Generate a fresh unique User-Agent immediately before every `/pipelines` discovery request; server-side filters persist by derived session.

```bash
discovery_user_agent="cixpress-pipeline-monitor-$(date +%s%N)-$RANDOM"
cixpress_get "$discovery_user_agent" '/healthz/ready'
if [[ $http_body != 'OK' ]]; then
  printf '%s\n' 'readiness response was not OK; stopping' >&2
  exit 1
fi
```

## :material-book-open-page-variant-outline: Discovery And Identification

`/pipelines?itemsPerPage=200&startIdx=0` is one page only. It is documented as a pipeline-ID map, while the deployed runtime may return a list containing `id`. Candidates outside those 200 items require explicit safe pagination; never imply completeness.

Normalize only sanitized selection fields. `head.id` is the source commit SHA in both shapes; `head_id` is a defensive fallback only for a list item.

```bash
discovery_user_agent="cixpress-pipeline-monitor-$(date +%s%N)-$RANDOM"
cixpress_get "$discovery_user_agent" '/pipelines?itemsPerPage=200&startIdx=0'
printf '%s' "$http_body" | /usr/bin/jq '
  def map_records:
    if all(.[]; type == "object") then
      to_entries[] | {id: (.value.id // .key), templateName: .value.templateName,
        head_id: (.value.head.id? // null), state: .value.state,
        start_time: .value.start_time, completion_time: .value.completion_time}
    else error("malformed /pipelines map value") end;
  def list_records:
    if all(.[]; type == "object") then
      .[] | {id, templateName, head_id: (.head.id? // .head_id? // null), state,
        start_time, completion_time}
    else error("malformed /pipelines list item") end;
  [if type == "object" then map_records
   elif type == "array" then list_records
   else error("unexpected /pipelines JSON shape") end]
  | sort_by(.start_time // "") | reverse'
```

Select in this order: explicit ID, exact commit SHA, then a unique commit prefix plus template and time. Sort by `start_time` because observed descending order is not guaranteed. Repository and branch filters are not guaranteed. Stop and report uncertainty if no candidate or more than one candidate matches.

Validate the selected ID once, then use `pipeline_id` in every detail, log, and Job command:

```bash
pipeline_id='<selected-pipeline-id>'
if ! [[ $pipeline_id =~ ^[A-Za-z0-9]{6}$ ]]; then
  printf '%s\n' 'invalid pipeline ID; stopping' >&2
  exit 1
fi
```

## :material-book-open-page-variant-outline: Detail And Polling

`/pipelines/{pipeline_id}` must be an ID-keyed top-level object. Normalize object, array, or null `steps`; missing or null steps are unavailable, and malformed values stop with a controlled error.

```bash
detail_user_agent="cixpress-pipeline-monitor-$(date +%s%N)-$RANDOM"
cixpress_get "$detail_user_agent" "/pipelines/$pipeline_id"
printf '%s' "$http_body" | /usr/bin/jq --arg id "$pipeline_id" '
  def step_record($fallback):
    {name: (.name // $fallback), identifier, state, start_time, completion_time};
  def normalized_steps:
    if .steps == null then {steps: [], step_confirmation: "unavailable"}
    elif (.steps | type) == "object" then
      if (.steps | all(.[]; type == "object")) then
        {steps: [.steps | to_entries[] | (.key as $key | .value | step_record($key))],
         step_confirmation: (if (.steps | length) > 0 then "available" else "unavailable" end)}
      else error("malformed object step") end
    elif (.steps | type) == "array" then
      if (.steps | all(.[]; type == "object")) then
        {steps: [.steps[] | step_record(.identifier)],
         step_confirmation: (if (.steps | length) > 0 then "available" else "unavailable" end)}
      else error("malformed array step") end
    else error("unexpected steps shape") end;
  if type != "object" then error("unexpected pipeline detail shape")
  elif has($id) | not then error("pipeline not found")
  elif .[$id] | type != "object" then error("malformed pipeline detail")
  else .[$id] | {id: (.id // $id), templateName,
    head_id: (.head.id? // null), state, start_time, completion_time} + normalized_steps
  end'
```

Discovery and detail polling have separate bounds. Discovery normally makes one list request. When waiting for a candidate, make the first request immediately, then wait 15 seconds only between requests, with at most 20 list requests and a five-minute wall-clock deadline. After selection, make the first detail request immediately with its own 20-request and five-minute limits. Normalize every detail response, compare it to the preceding normalized state to report transitions, and stop on a terminal state. Sleep 15 seconds only between non-terminal detail polls. Report uncertainty when either bound is reached; never use an infinite loop or combine budgets.

Expected steps are `git-clone`, `kaniko-build`, `helm-update`, and `cleanup`. Report success only if all four are observed as `SUCCEEDED`. If top-level state exists but steps are unavailable or empty, report that state and unavailable per-step confirmation. `INPROGRESS` with `completion_time`, or any contradiction, is stale or inconsistent data. Missing Jobs or steps can result from cleanup, not success. Observed pipeline states are `SUCCEEDED`, `FAILED`, and `INPROGRESS`; OpenAPI step states are `NOT_STARTED`, `STARTED`, `FAILED`, and `SUCCEEDED`.

## :material-book-open-page-variant-outline: Safe Log Inspection

Inspect only the active, failed, or user-requested step. Validate its conservative identifier before requesting logs. `--data-urlencode` encodes the step and zero-based offset. Metadata is the default and must precede any excerpt.

```bash
step='<selected-step>'
if ! [[ $step =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  printf '%s\n' 'invalid step identifier; stopping' >&2
  exit 1
fi
logs_user_agent="cixpress-pipeline-monitor-$(date +%s%N)-$RANDOM"
cixpress_get "$logs_user_agent" "/pipelines/$pipeline_id/logs" \
  --data-urlencode "step=$step" --data-urlencode 'offset=0'
printf '%s' "$http_body" | /usr/bin/jq --argjson http_status "$http_status" --arg pipeline_id "$pipeline_id" --arg step "$step" '
  if .logs == null then
    {pipeline_id: $pipeline_id, step: $step, http_status: $http_status, logs: "unavailable"}
  elif (.logs | type) == "object" and (.logs | all(.[]; type == "array")) then
    {pipeline_id: $pipeline_id, step: $step, http_status: $http_status,
     streams: [.logs | to_entries[] | {stream: .key, lines: (.value | length)}]}
  else error("malformed .logs; metadata-only report required") end'
```

Include the structured `http_status` in every log report. If `.logs` is malformed or safe redaction is uncertain, report metadata only and do not request or display an excerpt. If an excerpt is necessary after metadata inspection, use this best-effort transformation. It emits at most the last 40 lines per stream and redacts likely bearer, authorization, token, password, secret, API-key, JWT, and URL-userinfo values:

```bash
printf '%s' "$http_body" | /usr/bin/jq --argjson http_status "$http_status" --arg pipeline_id "$pipeline_id" --arg step "$step" '
  def redact:
    gsub("(?i)(?<prefix>bearer[[:space:]]+)[^[:space:],;]+"; "\(.prefix)[REDACTED]") |
    gsub("(?i)(?<prefix>(?:authorization|auth|token|password|secret|api[-_ ]?key)[[:space:]]*[:=][[:space:]]*)[^[:space:],;]+"; "\(.prefix)[REDACTED]") |
    gsub("(?i)(?<prefix>https?://)[^/@[:space:]]+@"; "\(.prefix)[REDACTED]@") |
    gsub("(?i)\\b[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\b"; "[REDACTED_JWT]");
  if (.logs | type) == "object" and (.logs | all(.[]; type == "array")) then
    {pipeline_id: $pipeline_id, step: $step, http_status: $http_status,
     streams: [.logs | to_entries[] | {stream: .key,
       lines: [(.value[-40:])[] | if type == "string" then redact else "[NON_STRING_LINE]" end]}]}
  else error("malformed .logs; do not produce excerpt") end'
```

Redaction is best-effort. Never run the excerpt transformation against live logs unless safety is determined. Summarize rather than reproduce output. Offsets are partially guaranteed only: a 12-line single stream returned zero lines at offset 12. Do not claim cross-stream completeness; track and report offsets conservatively.

## :material-book-open-page-variant-outline: Kubernetes Job Fallback And Provenance

Jobs are secondary read-only evidence. Match names ending in `-job-$pipeline_id`; no Job can mean cleanup:

```bash
kubectl --context virt-infra-dev-buc-hq -n cixpress get jobs -o name |
  /usr/bin/jq -R -r --arg id "$pipeline_id" 'select(endswith("-job-" + $id))'
```

The contract provenance is OpenAPI 3.0.3, API `0.13.2`, at <https://docs.voyager.virtomat.dev/ci-conductor/swagger.yaml>.

## :material-book-open-page-variant-outline: Report

Report selection evidence, pipeline ID, template, commit, start/completion times, top-level state, expected-step states, transitions, poll count, and step-confirmation availability. For logs, report the requested step, HTTP outcome, stream count, line counts, offsets, and only a safely redacted bounded summary. State uncertainty rather than infer success or root cause.
