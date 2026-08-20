# Architecture

The Coriolis Operator manages a Kubernetes-native Coriolis Appliance while retaining the current appliance runtime shape. It coordinates immutable upstream Coriolis component repositories and images rather than changing their contents.

The API is the namespaced `CoriolisAppliance` resource in `coriolis.cloudbase.it/v1alpha1`. The first controller is namespace-scoped to the Helm release namespace.

Initial reconciliation may create or update only an idempotent marker ConfigMap and patch the managed resource's status subresource. It does not install finalizers or perform destructive work. Successful reconciliation records status, while `Ready=False` remains until an appliance runtime exists; Kubernetes API failures propagate without a custom failure condition.

Helm packaging is located in `helm/`; CRDs are located in `helm/crds/`.
