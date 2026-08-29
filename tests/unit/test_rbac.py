import re
from pathlib import Path


def test_role_grants_only_required_secret_verbs() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    assert re.search(r"^kind: Role$", role, flags=re.MULTILINE)
    secret_rule = next(
        block for block in role.split("  - apiGroups:") if "      - secrets" in block
    )
    resource_block = secret_rule.split("    resources:\n", maxsplit=1)[1].split(
        "    verbs:", maxsplit=1
    )[0]
    verb_block = secret_rule.split("    verbs:\n", maxsplit=1)[1]
    resources = re.findall(r"^      - (\w+)$", resource_block, flags=re.MULTILINE)
    verbs = re.findall(r"^      - (\w+)$", verb_block, flags=re.MULTILINE)

    assert resources == ["secrets"]
    assert verbs == ["get", "create", "patch"]
    assert "resourceNames:" not in secret_rule


def test_role_grants_only_required_service_verbs() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    service_rule = next(
        block for block in role.split("  - apiGroups:") if "      - services" in block
    )
    resource_block = service_rule.split("    resources:\n", maxsplit=1)[1].split(
        "    verbs:", maxsplit=1
    )[0]
    verb_block = service_rule.split("    verbs:\n", maxsplit=1)[1]
    resources = re.findall(r"^      - (\w+)$", resource_block, flags=re.MULTILINE)
    verbs = re.findall(r"^      - (\w+)$", verb_block, flags=re.MULTILINE)

    assert resources == ["services"]
    assert verbs == ["get", "create", "patch"]
    assert "resourceNames:" not in service_rule


def test_role_grants_only_required_ingress_verbs() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    ingress_rule = next(
        block for block in role.split("  - apiGroups:") if "      - ingresses" in block
    )
    assert re.findall(r"^      - ([\w.]+)$", ingress_rule, flags=re.MULTILINE) == [
        "networking.k8s.io",
        "ingresses",
        "get",
        "create",
        "patch",
    ]
    assert "resourceNames:" not in ingress_rule
    assert "delete" not in ingress_rule
    assert "list" not in ingress_rule
    assert "watch" not in ingress_rule


def test_role_grants_only_required_mariadb_and_memcached_workload_verbs() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    pvc_rule = next(
        block
        for block in role.split("  - apiGroups:")
        if "      - persistentvolumeclaims" in block
    )
    stateful_set_rule = next(
        block
        for block in role.split("  - apiGroups:")
        if "      - statefulsets" in block
    )
    deployment_rule = next(
        block
        for block in role.split("  - apiGroups:")
        if "      - deployments" in block
    )

    assert re.findall(r"^      - (\w+)$", pvc_rule, flags=re.MULTILINE) == [
        "persistentvolumeclaims",
        "get",
        "create",
    ]
    assert re.findall(r"^      - (\w+)$", stateful_set_rule, flags=re.MULTILINE) == [
        "apps",
        "statefulsets",
        "get",
        "create",
        "patch",
    ]
    assert re.findall(r"^      - (\w+)$", deployment_rule, flags=re.MULTILINE) == [
        "apps",
        "deployments",
        "get",
        "create",
        "patch",
    ]
    assert "delete" not in pvc_rule
    assert "patch" not in pvc_rule
    assert "resourceNames:" not in pvc_rule
    assert "resourceNames:" not in stateful_set_rule
    assert "resourceNames:" not in deployment_rule


def test_role_grants_only_required_bootstrap_job_verbs() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    job_rule = next(
        block for block in role.split("  - apiGroups:") if "      - jobs" in block
    )
    assert re.findall(r"^      - (\w+)$", job_rule, flags=re.MULTILINE) == [
        "batch",
        "jobs",
        "get",
        "create",
    ]
    assert "patch" not in job_rule
    assert "delete" not in job_rule
    assert "list" not in job_rule
    assert "watch" not in job_rule


def test_role_grants_only_required_serviceaccount_verbs() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    serviceaccount_rule = next(
        block
        for block in role.split("  - apiGroups:")
        if "      - serviceaccounts" in block
    )
    assert re.findall(r"^      - (\w+)$", serviceaccount_rule, flags=re.MULTILINE) == [
        "serviceaccounts",
        "get",
        "create",
        "patch",
    ]
    assert "resourceNames:" not in serviceaccount_rule
    assert "delete" not in serviceaccount_rule
    assert "update" not in serviceaccount_rule
    assert "escalate" not in serviceaccount_rule
    assert "bind" not in serviceaccount_rule


def test_role_grants_only_required_pod_verbs() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    pod_rule = next(
        block for block in role.split("  - apiGroups:") if "      - pods" in block
    )
    assert "      - pods/log" not in pod_rule
    assert re.findall(r"^      - (\w+)$", pod_rule, flags=re.MULTILINE) == [
        "pods",
        "get",
        "list",
        "watch",
    ]
    assert "resourceNames:" not in pod_rule
    assert "create" not in pod_rule
    assert "delete" not in pod_rule
    assert "patch" not in pod_rule
    assert "update" not in pod_rule


def test_role_grants_pod_logs_get_only() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    pod_log_rule = next(
        block for block in role.split("  - apiGroups:") if "      - pods/log" in block
    )
    assert re.findall(r"^      - ([\w/]+)$", pod_log_rule, flags=re.MULTILINE) == [
        "pods/log",
        "get",
    ]
    assert "resourceNames:" not in pod_log_rule


def test_role_grants_only_required_rbac_object_verbs() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    roles_rule = next(
        block for block in role.split("  - apiGroups:") if "      - roles" in block
    )
    rolebindings_rule = next(
        block
        for block in role.split("  - apiGroups:")
        if "      - rolebindings" in block
    )

    assert re.findall(r"^      - ([\w.]+)$", roles_rule, flags=re.MULTILINE) == [
        "rbac.authorization.k8s.io",
        "roles",
        "get",
        "create",
        "patch",
    ]
    assert re.findall(r"^      - ([\w.]+)$", rolebindings_rule, flags=re.MULTILINE) == [
        "rbac.authorization.k8s.io",
        "rolebindings",
        "get",
        "create",
        "patch",
    ]
    for rule in (roles_rule, rolebindings_rule):
        assert "resourceNames:" not in rule
        assert "      - delete" not in rule
        assert "      - update" not in rule
        assert "      - escalate" not in rule
        assert "      - bind" not in rule
        assert "*" not in rule
    assert "clusterroles" not in role
    assert "clusterrolebindings" not in role


def test_role_excludes_deferred_mariadb_permissions() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    assert "      - poddisruptionbudgets" not in role
    assert "      - delete" not in role
