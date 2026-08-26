# 0006: Use Ingress-NGINX for Kubernetes Runtime Exposure

## :material-book-open-page-variant-outline: Context

The appliance needs one public HTTPS origin while its Coriolis API, web UI, and future dependency backends communicate on the cluster network. The former appliance design included an initial web proxy and Step CA bootstrap, but neither is part of the initial Kubernetes runtime.

## :material-book-open-page-variant-outline: Decision

Use the community ingress-nginx controller for short-term public exposure. The operator will later own the appliance Ingress resources, but will never install the controller or create, mutate, or delete TLS certificate Secret material.

The per-CR ingress contract supports `certManager` and `existingSecret` modes. `certManager` always derives `<host>-tls` and annotates a ready defaulted or explicit `ClusterIssuer`; `existingSecret` alone accepts an externally managed same-namespace `tlsSecretName` and emits no issuer annotation. TLS terminates and redirects at Ingress; every backend and dependency Service is ClusterIP and plaintext. The logical public origin is exactly `https://<host>`.

## :material-book-open-page-variant-outline: Consequences

ClusterIP is not encryption. Plaintext backend traffic is accepted only within a trusted cluster network; clusters needing encryption or stronger workload isolation require a separate approved design. No route may be emitted until its backend Service exists. The web offline evidence gate is complete locally: the exact image passed its 17-stage validator without `CA_FINGERPRINT` or Step CA in `31.889s`, including expected missing-fingerprint `500`; the local controller slice owns web ClusterIP `3000` and a one-replica Deployment. This is uncommitted/unpublished/unreleased work on CI-owned `0.5.32` base commit `26fc9555ccd3ebe3593d2f353700938ec542f4fe`, not a CIXpress result or Kubernetes POC. Ingress/TLS/external routing remains deferred until review, commit/publish approval, exact-SHA CIXpress success, CI release synchronization, and isolated released-artifact web POC acceptance.
