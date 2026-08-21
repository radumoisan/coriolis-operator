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
