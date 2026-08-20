# Development Environment

## Environment

| Item | Value |
| --- | --- |
| Environment | `infra-dev-buc-hq` |
| Kubernetes context | `virt-infra-dev-buc-hq` |
| SSH alias | `virt-infra-dev-buc-hq` |
| Ingress address | `89.34.101.238` |
| Main namespaces | `voyager`, `cloudbill`, `keycloak`, `cixpress`, `onboarding`, `argocd` |
| CIXpress namespace | `cixpress` |
| CIXpress URL | <https://cixpress.virtomat.dev> |
| Operator namespace | TBD; requires approval before deployment or live validation |

## Workstation Access

Ask a platform administrator for a dev-cluster kubeconfig, SSH access to the dev host, and any additional credentials required by the assigned role. Install the kubeconfig under `~/.kube/config`, then verify access without changing cluster state:

```sh
kubectl config get-contexts
kubectl --context virt-infra-dev-buc-hq get nodes
kubectl --context virt-infra-dev-buc-hq get namespaces
ssh virt-infra-dev-buc-hq
```

## CIXpress Observation

CIXpress runs in `cixpress` and is available at <https://cixpress.virtomat.dev>. Its components are frontend, conductor, monitor, Redis/Valkey, and temporary pipeline Job pods.

The environment is approved for read-only pipeline troubleshooting and monitoring. Always specify both the Kubernetes context and namespace explicitly.

Use the polling-only [CIXpress Pipeline Monitoring](cixpress-monitoring.md) procedure for pipeline status and logs. It queries the conductor through authorized Kubernetes exec because ingress may require deployment-specific authentication; it does not bypass Kubernetes authorization. Never use SSE or `/stream`.

```sh
kubectl --context virt-infra-dev-buc-hq -n cixpress get pods
kubectl --context virt-infra-dev-buc-hq -n cixpress get deployments
kubectl --context virt-infra-dev-buc-hq -n cixpress get services
kubectl --context virt-infra-dev-buc-hq -n cixpress get ingress
kubectl --context virt-infra-dev-buc-hq -n cixpress get jobs
kubectl --context virt-infra-dev-buc-hq -n cixpress logs deployment/conductor --all-containers --tail=200
kubectl --context virt-infra-dev-buc-hq -n cixpress get events --sort-by=.lastTimestamp
```

Troubleshoot in this order:

1. Pods and readiness.
2. Recent logs.
3. Pod descriptions and events.
4. Deployments.
5. Services and ingress.
6. Redis/Valkey and pipeline Jobs.

## Safety Boundary

Read-only investigation is safe. Restarts, deletes, scaling, patches, configuration changes, and new Jobs require explicit approval. Never display Kubernetes Secrets, kubeconfig credentials, tokens, or sensitive log content.

Future operator deployment may use this cluster only in a dedicated namespace that remains TBD. Approval of that namespace is required before any operator deployment or live validation.
