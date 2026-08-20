# Architecture

The Coriolis Operator manages a Kubernetes-native Coriolis Appliance while retaining the current appliance runtime shape. It coordinates immutable upstream Coriolis component repositories and images rather than changing their contents.

The API is the namespaced `CoriolisAppliance` resource in `coriolis.cloudbase.it/v1alpha1`. The first controller is namespace-scoped to the Helm release namespace.

Initial reconciliation deliberately owns only an idempotent marker ConfigMap and resource status conditions. It does not install finalizers or perform destructive work.

Helm packaging is located in `helm/`; CRDs are located in `helm/crds/`.
