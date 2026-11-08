import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = {"common.models", "common.names", "common.queues"}
LOGIC = [
    "agent/ports.py",
    "agent/services.py",
    "agent/guard.py",
    "agent/memory.py",
    "agent/summary.py",
    "agent/tools.py",
    "agent/triage.py",
    "api/ports.py",
    "api/services.py",
    "api/ops/ports.py",
    "api/ops/services.py",
    "ingest/ports.py",
    "ingest/dispatch.py",
    "ingest/services.py",
    "reconciler/ports.py",
    "reconciler/services.py",
]
LOGIC_MODULES = {path.removesuffix(".py").replace("/", ".") for path in LOGIC}


def imported_modules(path: Path) -> list[str]:
    modules = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            package = ROOT / node.module.replace(".", "/")
            if package.is_dir():
                modules += [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                modules.append(node.module)
    return modules


def allowed(module: str) -> bool:
    return module.split(".")[0] in sys.stdlib_module_names or module in CONTRACTS or module in LOGIC_MODULES


@pytest.mark.parametrize("path", LOGIC)
def test_business_logic_imports_only_contracts_ports_and_other_logic(path: str) -> None:
    assert [module for module in imported_modules(ROOT / path) if not allowed(module)] == []
