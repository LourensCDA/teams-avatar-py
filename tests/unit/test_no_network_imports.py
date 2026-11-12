"""Security pass (T072, FR-012, SC-006): the shipped package never imports network modules."""

from __future__ import annotations

import ast
from pathlib import Path

import teams_avatar

FORBIDDEN = {"socket", "http", "urllib", "requests", "ssl", "ftplib", "smtplib", "asyncio"}
PACKAGE = Path(teams_avatar.__file__).parent


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_module_imports_network_libraries() -> None:
    offenders = {
        str(p.relative_to(PACKAGE)): sorted(imported_roots(p) & FORBIDDEN)
        for p in PACKAGE.rglob("*.py")
        if imported_roots(p) & FORBIDDEN
    }
    assert offenders == {}


def test_scan_covers_the_package() -> None:
    files = {p.name for p in PACKAGE.rglob("*.py")}
    assert {"app.py", "cli.py", "control.py"} <= files
