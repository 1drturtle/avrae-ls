"""Workspace source discovery and literal module-member references."""

from __future__ import annotations

import ast
import os
from pathlib import Path

from lsprotocol import types

from avrae_ls.analysis.bindings import build_binding_index
from avrae_ls.analysis.module_symbols import module_exports_for_code
from avrae_ls.analysis.source_context import build_source_context
from avrae_ls.lsp.lsp_utils import range_from_positions, shift_range
from avrae_ls.runtime.context import GVarResolver


SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "bundled", "dist", "__pycache__"}
SUFFIXES = {".alias", ".drac", ".gvar", ".alias-module"}


def workspace_sources(root: Path, documents) -> dict[str, tuple[str, bool]]:
    sources: dict[str, tuple[str, bool]] = {}
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS and not name.startswith(".")]
        for name in files:
            path = Path(directory) / name
            if path.suffix not in SUFFIXES:
                continue
            try:
                sources[path.resolve().as_uri()] = (path.read_text(), path.suffix in {".gvar", ".alias-module"})
            except (OSError, UnicodeError):
                continue
    for uri, doc in documents.items():
        sources[uri] = (
            doc.source,
            getattr(doc, "language_id", "") == "avrae-module" or uri.endswith((".gvar", ".alias-module")),
        )
    return sources


def module_references(source: str, is_module: bool, resolver: GVarResolver):
    """Yield (member range, definition location) for unshadowed literal imports."""
    context = build_source_context(source, is_module)
    bindings = build_binding_index(source, treat_as_module=is_module)
    chunks = [(b.code, b.line_offset, b.char_offset) for b in context.blocks]
    if not chunks:
        chunks = [(context.prepared, 0, 0)]
    exports = {}
    from avrae_ls.analysis.module_symbols import location_for_module_symbol

    for code, line, col in chunks:
        exports.update(module_exports_for_code(code, resolver))
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            path = ast.unparse(node)
            symbol = exports.get(path)
            if symbol is None:
                continue
            base = node
            while isinstance(base, ast.Attribute):
                base = base.value
            if not isinstance(base, ast.Name):
                continue
            base_range = shift_range(
                range_from_positions(base.lineno, base.col_offset, base.end_lineno, base.end_col_offset), line, col
            )
            binding = bindings.at(base_range.start)
            # A parameter or reassignment can shadow an imported module.
            if binding is None or binding.scope is not bindings.root or len(binding.declarations) != 1:
                continue
            end_line = node.end_lineno or node.lineno
            end_col = node.end_col_offset or node.col_offset + len(node.attr)
            rng = shift_range(range_from_positions(end_line, end_col - len(node.attr), end_line, end_col), line, col)
            yield rng, location_for_module_symbol(symbol)


def same_location(a: types.Location, b: types.Location) -> bool:
    return a.uri == b.uri and a.range.start == b.range.start


def navigation_resolver(resolver: GVarResolver, config, documents) -> GVarResolver:
    """Overlay live local module text without mutating the execution cache."""
    from avrae_ls.runtime.context import gvar_source_path, make_gvar_record, gvar_script_writable

    snapshot = resolver.snapshot()
    for key, record in snapshot.items():
        path = gvar_source_path(record)
        if path is None:
            continue
        try:
            document = documents.get(path.as_uri())
            source = document.source if document is not None else path.read_text()
        except (OSError, UnicodeError):
            continue
        snapshot[key] = make_gvar_record(source, source_path=path, script_writable=gvar_script_writable(record))
    overlay = GVarResolver(config)
    overlay.load_snapshot(snapshot)
    return overlay
