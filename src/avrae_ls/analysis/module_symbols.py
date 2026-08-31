"""Static exports and imports for Draconic ``using()`` modules."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from lsprotocol import types

from avrae_ls.analysis.symbols import SymbolEntry, build_symbol_table
from avrae_ls.runtime.context import GVarResolver, gvar_source_path


GVAR_URI_SCHEME = "avrae-gvar"
MAX_MODULE_PREFETCH_DEPTH = 50


@dataclass(frozen=True)
class ModuleSymbol:
    gvar_id: str
    entry: SymbolEntry
    source_path: Path | None


def gvar_uri(gvar_id: str) -> str:
    return f"{GVAR_URI_SCHEME}://gvar/{quote(gvar_id, safe='')}.gvar"


def literal_using_imports(code: str) -> dict[str, str]:
    """Return ``using(alias='gvar')`` bindings that can be statically resolved."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "using":
            continue
        for keyword in node.keywords:
            if keyword.arg and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                imports[keyword.arg] = keyword.value.value
    return imports


async def prefetch_module_dependency_ids(
    gvar_ids: Iterable[str],
    resolver: GVarResolver,
    *,
    max_depth: int = MAX_MODULE_PREFETCH_DEPTH,
) -> None:
    """Fetch a transitive graph of literal ``using()`` module IDs.

    ``visited`` protects circular imports, while the depth limit mirrors
    Draconic's module runtime. This intentionally excludes ``get_gvar()``,
    because it is only needed for module navigation and completion.
    """
    frontier = set(gvar_ids)
    visited: set[str] = set()
    for _ in range(max_depth):
        current = frontier - visited
        if not current:
            return
        visited.update(current)
        try:
            await resolver.ensure_many(current)
        except Exception:
            return

        frontier = set()
        for gvar_id in current:
            content = resolver.get_local(gvar_id)
            if isinstance(content, str):
                frontier.update(literal_using_imports(content).values())


def module_exports_for_code(code: str, resolver: GVarResolver) -> dict[str, ModuleSymbol]:
    """Index direct imports from code using the resolver's currently cached gvars."""
    results: dict[str, ModuleSymbol] = {}
    for alias, gvar_id in literal_using_imports(code).items():
        results.update(_exports_for_gvar(gvar_id, resolver, set(), alias))
    return results


def _exports_for_gvar(gvar_id: str, resolver: GVarResolver, visiting: set[str], prefix: str) -> dict[str, ModuleSymbol]:
    if gvar_id in visiting:
        return {}
    record = resolver.get_record(gvar_id)
    content = resolver.get_local(gvar_id)
    if not isinstance(content, str) or record is None:
        return {}
    visiting = {*visiting, gvar_id}
    source_path = gvar_source_path(record)
    exports = {
        f"{prefix}.{entry.name}": ModuleSymbol(gvar_id, entry, source_path)
        for entry in build_symbol_table(content, treat_as_module=True).entries
    }
    for child_alias, child_id in literal_using_imports(content).items():
        exports.update(_exports_for_gvar(child_id, resolver, visiting, f"{prefix}.{child_alias}"))
    return exports


def member_at_position(code: str, line: int, character: int) -> str | None:
    lines = code.splitlines()
    if line < 0 or line >= len(lines):
        return None
    text = lines[line]
    start = min(max(character, 0), len(text))
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "._"):
        start -= 1
    end = min(max(character, 0), len(text))
    while end < len(text) and (text[end].isalnum() or text[end] in "._"):
        end += 1
    candidate = text[start:end]
    return candidate if "." in candidate else None


def location_for_module_symbol(symbol: ModuleSymbol) -> types.Location:
    if symbol.source_path is not None:
        return types.Location(uri=symbol.source_path.as_uri(), range=symbol.entry.selection_range)
    return types.Location(uri=gvar_uri(symbol.gvar_id), range=symbol.entry.selection_range)
