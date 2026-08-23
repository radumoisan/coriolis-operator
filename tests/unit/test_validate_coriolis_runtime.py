import importlib.util
import re
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "validate-coriolis-bootstrap-runtime.py"
)
sys.path.insert(0, str(SCRIPT.parent.parent / "src"))
SPEC = importlib.util.spec_from_file_location("validate_coriolis_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)

_EXPECTED_RUNTIME_CONFIG_FILES = (
    "coriolis.conf",
    "api-paste.ini",
    "policy.yml",
    "vixdisklib.conf",
    "coriolis.release",
)


def test_evidence_files_are_private_and_stage_runtime_config(tmp_path: Path) -> None:
    paths = runtime.create_evidence_files(tmp_path)

    assert paths.scratch.stat().st_mode & 0o777 == 0o700
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in paths.scratch.rglob("*")
        if path.is_file()
    )
    assert all(
        (paths.coriolis / name).exists() for name in _EXPECTED_RUNTIME_CONFIG_FILES
    )
    assert (paths.coriolis / "coriolis_rpc_probe.py").exists()


def test_rpc_probe_emits_only_fixed_markers_and_no_sensitive_origin() -> None:
    source = runtime.CORIOLIS_RPC_PROBE

    prints = re.findall(r"print\(([^)]*)\)", source)
    markers = [token.strip().strip("'\"") for token in prints]
    assert set(markers) == {"coriolis-rpc-ok", "CORIOLIS_RPC_FAIL"}

    for sensitive_fragment in (
        "X-Auth-Token",
        "password",
        "project_id",
        "token",
        "json.dumps",
        "headers",
        "environ",
        "read_text",
    ):
        assert not re.search(rf"print\([^)]*{re.escape(sensitive_fragment)}", source), (
            sensitive_fragment
        )
