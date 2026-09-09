# Coriolis Operator Guidance

Read and follow `/home/radu/Dev/cb-coriolis/AGENTS.md`. These rules extend it for `coriolis-operator/` and its runtime. Paths below are relative to the operator repository.

- Validate relevant operator work before reporting completion; update `STATUS.md`, `docs/progress.md`, and `BACKLOG.md` when project state meaningfully changes.
- Keep upstream Coriolis component repositories and images as immutable inputs.
- CIXpress owns operator release versioning.
- Never infer operational contexts, namespaces, or credentials. When the user explicitly says the dev environment, you may use the documented `virt-infra-dev-buc-hq` context; resolve the namespace from the task target or the documented `coriolis` operator namespace.
- Report pipeline success only after all steps succeed; HTTP 202 alone is insufficient.
- For dev-cluster work, always specify Kubernetes context and namespace explicitly. `cixpress` is approved only for read-only CI observation; the operator namespace is `coriolis`.
- Read-only investigation is safe. In the approved dev environment, a request to bring up, deploy, qualify, or troubleshoot Coriolis authorizes the ordinary scoped mutations, retries, and normal cleanup needed to complete it (restarts, deletes, scaling, patches, configuration changes, test resources, and new Jobs) without repeated approval.
- Still require separate explicit approval for production or non-dev targets, changes to shared infrastructure outside the target, force deletion, grace-zero deletion, finalizer or owner-reference manipulation, and other destructive or out-of-scope actions.
