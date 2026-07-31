"""The sim-purity rule, enforced rather than trusted.

`SYSTEM.md` says `sim/` stays pure and dependency-light. That is an architectural
invariant with no natural failure mode: importing `logging` for one debug line, or
reaching into `services` for one helper, works perfectly and costs nothing until the day
someone needs to property-test the sampler or run it outside a request. By then the import
has been there for months.

So it is a test. The cost of keeping it honest is one assertion; the cost of discovering
it later is unwinding a boundary that has had real code across it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

#: Anything that would give the package a side effect, a dependency on a running service,
#: or a path back into the layers that are supposed to call *it*.
FORBIDDEN = (
    "sqlalchemy",
    "asyncpg",
    "redis",
    "fastapi",
    "httpx",
    "requests",
    "celery",
    "logging",
    "app.db",
    "app.models",
    "app.api",
    "app.services",
    "app.schedule",
)

SIM = pathlib.Path(__file__).resolve().parents[2] / "app" / "sim"


def _imports(path: pathlib.Path) -> list[str]:
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


@pytest.mark.parametrize("module", sorted(SIM.glob("*.py")), ids=lambda p: p.name)
def test_module_imports_nothing_impure(module: pathlib.Path) -> None:
    offending = [
        m
        for m in _imports(module)
        if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)
    ]
    assert not offending, (
        f"{module.name} imports {', '.join(offending)}. The sim package must stay free of "
        "the database, the network, the framework and the layers that call it."
    )


def test_the_only_app_dependency_is_the_error_hierarchy() -> None:
    # core.errors is pure Python and is what lets the API translate one family of
    # exceptions. Anything else from app/ pointing into sim is the arrow going backwards.
    allowed = {"app.core.errors"}
    seen = {
        m
        for module in SIM.glob("*.py")
        for m in _imports(module)
        if m.startswith("app.") and not m.startswith("app.sim")
    }
    assert seen <= allowed, f"unexpected app-level imports: {sorted(seen - allowed)}"


def test_no_module_reads_the_clock_at_import_or_run() -> None:
    # A timestamp anywhere in the engine breaks reproducibility: RunManifest deliberately
    # carries none, and recording when a run happened is the persistence layer's job.
    for module in SIM.glob("*.py"):
        assert "datetime" not in _imports(module), f"{module.name} imports datetime"
        assert "time" not in _imports(module), f"{module.name} imports time"
