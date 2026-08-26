# 0006: Use Ingress-NGINX for Kubernetes Runtime Exposure

## :material-book-open-page-variant-outline: Context

The appliance needs one public HTTPS origin while its Coriolis API, web UI, and future dependency backends communicate on the cluster network. The former appliance design included an initial web proxy and Step CA bootstrap, but neither is part of the initial Kubernetes runtime.

## :material-book-open-page-variant-outline: Decision

Use the community ingress-nginx controller for short-term public exposure. The operator will later own the appliance Ingress resources, but will never install the controller or create, mutate, or delete TLS certificate Secret material.

The per-CR ingress contract supports `certManager` and `existingSecret` modes. `certManager` always derives `<host>-tls` and annotates a ready defaulted or explicit `ClusterIssuer`; `existingSecret` alone accepts an externally managed same-namespace `tlsSecretName` and emits no issuer annotation. TLS terminates and redirects at Ingress; every backend and dependency Service is ClusterIP and plaintext. The logical public origin is exactly `https://<host>`.

## :material-book-open-page-variant-outline: Consequences

ClusterIP is not encryption. Plaintext backend traffic is accepted only within a trusted cluster network; clusters needing encryption or stronger workload isolation require a separate approved design. No route may be emitted until its backend Service exists. The backend web gate is complete: source `942557a0914b7455af6dbeac6ae5966417bd1223` passed CIXpress Default `opfrnr` at all expected steps (`08:39:32Z`-`08:40:58Z`) and CI commit `9f7151af10e2275e15718a325a12e850601ec5f3` released chart/app/operator `0.5.33`, chart digest `sha256:0e0452229c22c2a4067c55df25c6e09c1ffeefcc8e92eb3fba77ab477713e27e`, operator digest `sha256:8f3e80f3c6ea2a79c83feff5078ca45ef8d8615b060ae91b1fae235428a31273`, and exact web digest `sha256:32ebc391ac46fe627185694b3fd252afd7587b152f526dff38ae0a5b887c0db1`. The accepted isolated POC proved the owner-referenced web ClusterIP `3000`, ready EndpointSlice, one-replica Deployment/Pod, collision recovery, lifecycle and drift repair, retained-state no-write CR recreation, and normal cleanup. Ingress/TLS/external routing remains unimplemented and unclaimed; logical-origin Ingress is now the next design and implementation milestone. A broad cleanup inventory command accidentally emitted Secret payload data into internal tool output; no Secret was changed and disposable copies were deleted, and no claim that the `coriolis/regcred` and `coriolis/coriolis-appliance-registry` credentials were not exposed is valid. The user explicitly waived credential rotation for this work and accepted the residual risk, allowing logical-origin Ingress to proceed.
