# Agent Guidance

- Make the smallest correct change.
- Update `STATUS.md`, `docs/progress.md`, and `BACKLOG.md` when work makes a meaningful change to project state.
- Validate relevant work before reporting completion.
- Do not push, commit, or alter Git metadata unless explicitly instructed.
- Keep upstream Coriolis component repositories and images as immutable inputs.
- CIXpress owns release version changes. Do not manually edit `helm/Chart.yaml` `version` or `appVersion`, or `helm/values.yaml` image tag, unless explicitly requested.
- Never infer operational contexts, namespaces, or credentials. When the user explicitly says the dev environment, you may use the documented `virt-infra-dev-buc-hq` context; resolve the namespace from the task target or the documented `coriolis` operator namespace.
- Do not treat HTTP 202 as pipeline success; report success only after all pipeline steps succeeded.
- For dev-cluster work, always specify Kubernetes context and namespace explicitly. `cixpress` is approved only for read-only CI observation; the operator namespace is `coriolis`.
- Read-only investigation is safe. In the approved dev environment, a request to bring up, deploy, qualify, or troubleshoot Coriolis authorizes the ordinary scoped mutations, retries, and normal cleanup needed to complete it (restarts, deletes, scaling, patches, configuration changes, test resources, and new Jobs) without repeated approval.
- Still require separate explicit approval for production or non-dev targets, changes to shared infrastructure outside the target, force deletion, grace-zero deletion, finalizer or owner-reference manipulation, and other destructive or out-of-scope actions.
- Dev credentials, Kubernetes Secret values, tokens, and sensitive diagnostic content may be handled and displayed transiently in the private tool/browser session when useful for dev bring-up or troubleshooting; avoid unnecessary repetition of values. Never place credential material in commits, repository files, durable documentation, or final reports unless the user explicitly requests otherwise. Production credentials remain off-limits.
