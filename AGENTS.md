# Agent Guidance

- Make the smallest correct change.
- Update `STATUS.md`, `docs/progress.md`, and `BACKLOG.md` when work makes a meaningful change to project state.
- Validate relevant work before reporting completion.
- Do not push, commit, or alter Git metadata unless explicitly instructed.
- Keep upstream Coriolis component repositories and images as immutable inputs.
- CIXpress owns release version changes. Do not manually edit `helm/Chart.yaml` `version` or `appVersion`, or `helm/values.yaml` image tag, unless explicitly requested.
- Never infer operational contexts, namespaces, or credentials.
- Do not treat HTTP 202 as pipeline success; report success only after all pipeline steps succeeded.
- For dev-cluster work, always specify Kubernetes context and namespace explicitly. `cixpress` is approved only for read-only CI observation; the operator namespace is TBD.
- Read-only investigation is safe. Restarts, deletes, scaling, patches, configuration changes, and new Jobs require explicit approval.
- Never display Kubernetes Secrets, kubeconfig credentials, tokens, or sensitive log content.
