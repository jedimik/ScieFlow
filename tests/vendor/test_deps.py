"""ScieFlow stand-ins (_deps.py) and the package's import hygiene (M-C2 t1, plan 5.1 and check 3).

_deps.py stands in for the upstream modules the vendored code imports but ScieFlow does not
vendor: the connector stack, the snapshot store, the status axis and the provenance ledger. Each
stand-in must refuse, naming its ScieFlow replacement, and must never reach the network or a
file. Offline; the AST walk covers the whole package.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import pytest

from scieflow._vendor.researcher_core import _deps, errors, provenance_ts

PKG = Path(__file__).resolve().parents[2] / "src" / "scieflow" / "_vendor" / "researcher_core"
BANNED = {"httpx", "rapidfuzz", "platformdirs", "sqlite3"}


def test_versions_match_the_upstream_package() -> None:
    assert _deps.__version__ == "1.0.0"
    assert _deps.PARSER_VERSION == "1"
    assert _deps.PROTOCOL_VERSION == "1"


def test_error_types_are_the_excerpt_classes() -> None:
    assert _deps.ConnectorError is errors.ConnectorError
    assert _deps.SourceError is errors.SourceError
    assert _deps.SourceErrorKind is errors.SourceErrorKind
    assert _deps.UnsupportedOperation is errors.UnsupportedOperation


def test_provenance_helpers_are_the_excerpt_functions() -> None:
    assert _deps.ProvenanceError is provenance_ts.ProvenanceError
    assert _deps.normalize_ts is provenance_ts.normalize_ts


def test_create_connector_refuses_and_names_the_replacement() -> None:
    with pytest.raises(NotImplementedError, match="ScieFlow"):
        _deps.create_connector("openalex")


def test_snapshot_session_refuses_and_names_the_replacement() -> None:
    with pytest.raises(NotImplementedError, match="ScieFlow"):
        _deps.SnapshotSession()


def test_status_entry_refuses_and_names_the_replacement() -> None:
    with pytest.raises(NotImplementedError, match="ScieFlow"):
        _deps.StatusEntry()


def test_check_status_refuses_and_names_the_replacement() -> None:
    with pytest.raises(NotImplementedError, match="ScieFlow"):
        asyncio.run(_deps.check_status_async("10.1000/x", []))


def test_provenance_event_and_run_context_refuse_and_name_the_replacement() -> None:
    with pytest.raises(NotImplementedError, match="ScieFlow"):
        _deps.ProvenanceEvent()
    with pytest.raises(NotImplementedError, match="ScieFlow"):
        _deps.RunContext(run_id="r")


class _Probe(_deps.BaseConnector):
    name = "probe"
    capabilities = frozenset({"get_oa_pdf"})


def test_supports_reads_the_declared_capabilities() -> None:
    assert _Probe.supports("get_oa_pdf") is True
    assert _Probe.supports("search") is False


def test_unoverridden_operations_raise_unsupported() -> None:
    probe = _Probe()

    async def go() -> None:
        for call in (
            probe.search("q"),
            probe.get_by_id("id"),
            probe.resolve_doi("10.1000/x"),
            probe.get_oa_pdf("10.1000/x"),
        ):
            with pytest.raises(_deps.UnsupportedOperation):
                await call
        await probe.aclose()

    asyncio.run(go())


def _modules() -> list[Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_package_module_imports_a_banned_library() -> None:
    offenders: list[str] = []
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] in BANNED for name in names):
                offenders.append(path.relative_to(PKG).as_posix())
    assert offenders == []


def test_deps_imports_only_the_standard_library_and_its_own_package() -> None:
    tree = ast.parse((PKG / "_deps.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            assert node.module in {"errors", "provenance_ts"}, node.module
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert node.level == 0 and module.split(".")[0] in sys.stdlib_module_names, module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in sys.stdlib_module_names, alias.name
