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


def test_role_excludes_deferred_mariadb_permissions() -> None:
    role = (Path(__file__).parents[2] / "helm/templates/role.yaml").read_text()

    assert "      - pods" not in role
    assert "      - pods/log" not in role
    assert "      - poddisruptionbudgets" not in role
    assert "      - delete" not in role
