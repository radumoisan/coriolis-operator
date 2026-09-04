# Development Environment

## :material-book-open-page-variant-outline: Environment

| Item | Value |
| --- | --- |
| Environment | `infra-dev-buc-hq` |
| Kubernetes context | `virt-infra-dev-buc-hq` |
| SSH alias | `virt-infra-dev-buc-hq` |
| Ingress address | `89.34.101.238` |
| Main namespaces | `voyager`, `cloudbill`, `keycloak`, `cixpress`, `onboarding`, `argocd`, `coriolis` |
| CIXpress namespace | `cixpress` |
| CIXpress URL | <https://cixpress.virtomat.dev> |
| Operator namespace | `coriolis` |
| Coriolis environment source | [New Coriolis environments](https://cloudbasedev.atlassian.net/wiki/spaces/CDS/pages/2706014209/New+Coriolis+environments) |
| Appliance Jenkins URL | <http://10.8.1.121:8080> |
| Appliance setup job | `1_coriolis-appliance-setup` |
| `2608.0-rc4` reference build | [Build 868](http://10.8.1.121:8080/job/1_coriolis-appliance-setup/868/) |

!!! warning
    Treat the Confluence environment page as the source of truth for appliance build infrastructure, but never copy credentials from it into this repository or other durable project artifacts. Transient use of dev credentials and sensitive diagnostics in the private tool/browser session is allowed under the safety boundary below. Record only non-secret identifiers such as URLs, Jenkins job names, Jenkins credential IDs, and Kubernetes Secret names in durable documentation.

## :material-book-open-page-variant-outline: Workstation Access

Ask a platform administrator for a dev-cluster kubeconfig, SSH access to the dev host, and any additional credentials required by the assigned role. Install the kubeconfig under `~/.kube/config`, then verify access without changing cluster state:

```sh
kubectl config get-contexts
kubectl --context virt-infra-dev-buc-hq get nodes
kubectl --context virt-infra-dev-buc-hq get namespaces
ssh virt-infra-dev-buc-hq
```

## :material-book-open-page-variant-outline: CIXpress Observation

CIXpress runs in `cixpress` and is available at <https://cixpress.virtomat.dev>. Its components are frontend, conductor, monitor, Redis/Valkey, and temporary pipeline Job pods.

The environment is approved for read-only pipeline troubleshooting and monitoring. Always specify both the Kubernetes context and namespace explicitly.

Use the polling-only [CIXpress Pipeline Monitoring](cixpress-monitoring.md) procedure for pipeline status and logs. It queries the conductor through authorized Kubernetes exec because ingress may require deployment-specific authentication; it does not bypass Kubernetes authorization. Never use SSE or `/stream`.

```sh
kubectl --context virt-infra-dev-buc-hq -n cixpress get pods
kubectl --context virt-infra-dev-buc-hq -n cixpress get deployments
kubectl --context virt-infra-dev-buc-hq -n cixpress get services
kubectl --context virt-infra-dev-buc-hq -n cixpress get ingress
kubectl --context virt-infra-dev-buc-hq -n cixpress get jobs
kubectl --context virt-infra-dev-buc-hq -n cixpress get events --sort-by=.lastTimestamp
```

Troubleshoot in this order:

1. Pods and readiness.
2. Sanitized pipeline log metadata or excerpts from the monitoring procedure.
3. Pod descriptions and events.
4. Deployments.
5. Services and ingress.
6. Redis/Valkey and pipeline Jobs.

## :material-book-open-page-variant-outline: Safety Boundary

Read-only investigation is safe. In this approved dev environment (`virt-infra-dev-buc-hq` context), a user request to bring up, deploy, qualify, or troubleshoot Coriolis authorizes the ordinary scoped mutations, retries, and normal cleanup needed to complete it — restarts, deletes, scaling, patches, configuration changes, test resources, and new Jobs inside the target namespace — without repeated approval. Keep every command explicitly scoped by context and namespace; the operator namespace is `coriolis`.

Separate explicit approval is still required for production or non-dev targets, changes to shared infrastructure outside the target, force deletion, grace-zero deletion, finalizer or owner-reference manipulation, and other destructive or out-of-scope actions.

Dev credentials, Kubernetes Secret values, tokens, and sensitive diagnostic content may be handled and displayed transiently in the private tool/browser session when useful for dev bring-up or troubleshooting; avoid unnecessary repetition of values. Never place credential material in commits, repository files, this documentation, or final reports unless the user explicitly requests otherwise. Production credentials remain off-limits.
