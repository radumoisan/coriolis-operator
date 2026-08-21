# 0006: Use Ingress-NGINX for Kubernetes Runtime Exposure

## :material-book-open-page-variant-outline: Context

The appliance needs one public HTTPS origin while its Coriolis API, web UI, and future dependency backends communicate on the cluster network. The former appliance design included an initial web proxy and Step CA bootstrap, but neither is part of the initial Kubernetes runtime.

## :material-book-open-page-variant-outline: Decision

Use the community ingress-nginx controller for short-term public exposure. The operator will later own the appliance Ingress resources, but will never install the controller or create, mutate, or delete TLS certificate Secret material.

The per-CR ingress contract supports `certManager` and `existingSecret` modes. `certManager` always derives `<host>-tls` and annotates a ready defaulted or explicit `ClusterIssuer`; `existingSecret` alone accepts an externally managed same-namespace `tlsSecretName` and emits no issuer annotation. TLS terminates and redirects at Ingress; every backend and dependency Service is ClusterIP and plaintext. The logical public origin is exactly `https://<host>`.

## :material-book-open-page-variant-outline: Consequences

ClusterIP is not encryption. Plaintext backend traffic is accepted only within a trusted cluster network; clusters needing encryption or stronger workload isolation require a separate approved design. Ingress, Services, workloads, and route implementation remain future work. No route may be emitted until its backend Service exists. The web image must first pass an offline evidence gate showing startup without `CA_FINGERPRINT` or a Step CA mount.
