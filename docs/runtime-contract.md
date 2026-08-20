# Runtime Contract

The managed appliance retains the current Coriolis Appliance runtime shape. Existing Coriolis component repositories and container images are immutable upstream inputs.

`CoriolisAppliance` is namespaced and uses API version `coriolis.cloudbase.it/v1alpha1`.

For the first delivery, the operator watches its Helm release namespace and may create or update only its marker ConfigMap. Reconciliation must be idempotent and status conditions must accurately describe observed state.

The initial controller must not use finalizers or initiate destructive operations.
