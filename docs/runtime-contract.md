# Runtime Contract

The managed appliance retains the current Coriolis Appliance runtime shape. Existing Coriolis component repositories and container images are immutable upstream inputs.

`CoriolisAppliance` is namespaced and uses API version `coriolis.cloudbase.it/v1alpha1`.

For the first delivery, the operator watches its Helm release namespace and may create or update only its marker ConfigMap, plus patch the managed `CoriolisAppliance` status subresource. Reconciliation must be idempotent. Successful reconciliation records status, and `Ready=False` remains until an appliance runtime exists. Kubernetes API failures propagate without a custom failure condition.

The initial controller must not use finalizers or initiate destructive operations.
