from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import draconic
from lsprotocol import types

from .source_context import build_source_context
from .parser import wrap_draconic
from .lsp_utils import range_from_positions, shift_range

log = logging.getLogger(__name__)


@dataclass
class SymbolEntry:
    name: str
    kind: types.SymbolKind
    range: types.Range
    selection_range: types.Range


class SymbolTable:
    def __init__(self, entries: List[SymbolEntry]):
        self._entries = entries
        self._index: Dict[str, SymbolEntry] = {entry.name: entry for entry in entries}

    @property
    def entries(self) -> List[SymbolEntry]:
        return self._entries

    def lookup(self, name: str) -> Optional[SymbolEntry]:
        return self._index.get(name)


def build_symbol_table(source: str, *, treat_as_module: bool = False) -> SymbolTable:
    entries: list[SymbolEntry] = []
    source_ctx = build_source_context(source, treat_as_module)
    if not source_ctx.blocks:
        entries.extend(_symbols_from_code(source_ctx.prepared, 0, 0))
    else:
        for block in source_ctx.blocks:
            entries.extend(_symbols_from_code(block.code, block.line_offset, block.char_offset))
    return SymbolTable(entries)


def document_symbols(source: str, *, treat_as_module: bool = False) -> List[types.DocumentSymbol]:
    table = build_symbol_table(source, treat_as_module=treat_as_module)
    return [
        types.DocumentSymbol(
            name=entry.name,
            kind=entry.kind,
            range=entry.range,
            selection_range=entry.selection_range,
        )
        for entry in table.entries
    ]


def find_definition_range(table: SymbolTable, name: str) -> types.Range | None:
    entry = table.lookup(name)
    if entry:
        return entry.selection_range
    return None


def find_references(
    table: SymbolTable,
    source: str,
    name: str,
    include_declaration: bool = True,
    *,
    treat_as_module: bool = False,
) -> List[types.Range]:
    ranges: list[types.Range] = []
    entry = table.lookup(name)
    include_stores = include_declaration and entry is None
    if include_declaration:
        if entry:
            ranges.append(entry.selection_range)

    source_ctx = build_source_context(source, treat_as_module)
    if not source_ctx.blocks:
        ranges.extend(_references_from_code(source_ctx.prepared, name, 0, 0, include_stores))
    else:
        for block in source_ctx.blocks:
            ranges.extend(
                _references_from_code(block.code, name, block.line_offset, block.char_offset, include_stores)
            )
    return _dedupe_ranges(ranges)


def range_for_word(source: str, position: types.Position) -> types.Range | None:
    lines = source.splitlines()
    if position.line >= len(lines):
        return None
    line = lines[position.line]
    if position.character > len(line):
        return None

    def _is_ident(ch: str) -> bool:
        return ch.isalnum() or ch == "_"

    start_idx = position.character
    while start_idx > 0 and _is_ident(line[start_idx - 1]):
        start_idx -= 1

    end_idx = position.character
    while end_idx < len(line) and _is_ident(line[end_idx]):
        end_idx += 1

    if start_idx == end_idx:
        return None

    return types.Range(
        start=types.Position(line=position.line, character=start_idx),
        end=types.Position(line=position.line, character=end_idx),
    )


def _symbols_from_code(code: str, line_offset: int, char_offset: int) -> List[SymbolEntry]:
    body, offset_adjust = _parse_draconic(code)
    if not body:
        return []

    local_offset = line_offset + offset_adjust

    entries: list[SymbolEntry] = []
    for node in body:
        entry = _entry_from_node(node, local_offset, char_offset)
        if entry:
            entries.append(entry)
    return entries


def _entry_from_node(node: ast.AST, line_offset: int = 0, char_offset: int = 0) -> SymbolEntry | None:
    if isinstance(node, ast.FunctionDef):
        kind = types.SymbolKind.Function
        name = node.name
    elif isinstance(node, ast.ClassDef):
        kind = types.SymbolKind.Class
        name = node.name
    elif isinstance(node, ast.Assign) and node.targets:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            kind = types.SymbolKind.Variable
            name = target.id
            node = target
        else:
            return None
    elif isinstance(node, ast.AnnAssign):
        target = node.target
        if isinstance(target, ast.Name):
            kind = types.SymbolKind.Variable
            name = target.id
            node = target
        else:
            return None
    else:
        return None

    rng = range_from_positions(
        getattr(node, "lineno", 1),
        getattr(node, "col_offset", 0),
        getattr(node, "end_lineno", None),
        getattr(node, "end_col_offset", None),
        ensure_nonempty=True,
    )
    rng = shift_range(rng, line_offset, char_offset)
    return SymbolEntry(name=name, kind=kind, range=rng, selection_range=rng)


class _ReferenceCollector(ast.NodeVisitor):
    def __init__(self, target: str, include_stores: bool):
        super().__init__()
        self._target = target
        self._include_stores = include_stores
        self.ranges: list[types.Range] = []

    def visit_Name(self, node: ast.Name):  # type: ignore[override]
        if node.id == self._target:
            if isinstance(node.ctx, ast.Store) and not self._include_stores:
                return
            rng = range_from_positions(
                getattr(node, "lineno", 1),
                getattr(node, "col_offset", 0),
                getattr(node, "end_lineno", None),
                getattr(node, "end_col_offset", None),
                ensure_nonempty=True,
            )
            self.ranges.append(rng)
        self.generic_visit(node)


def _references_from_code(
    code: str,
    name: str,
    line_offset: int,
    char_offset: int,
    include_stores: bool,
) -> List[types.Range]:
    body, offset_adjust = _parse_draconic(code)
    if not body:
        return []

    collector = _ReferenceCollector(name, include_stores)
    for node in body:
        collector.visit(node)

    local_offset = line_offset + offset_adjust
    return [shift_range(rng, local_offset, char_offset) for rng in collector.ranges]


def _parse_draconic(code: str) -> tuple[list[ast.AST], int]:
    parser = draconic.DraconicInterpreter()
    try:
        return parser.parse(code), 0
    except draconic.DraconicSyntaxError:
        wrapped, added = wrap_draconic(code)
        try:
            return parser.parse(wrapped), -added
        except draconic.DraconicSyntaxError:
            return [], 0
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Symbol extraction failed: %s", exc)
        return [], 0


def _dedupe_ranges(ranges: Iterable[types.Range]) -> List[types.Range]:
    seen = set()
    unique: list[types.Range] = []
    for rng in ranges:
        key = (rng.start.line, rng.start.character, rng.end.line, rng.end.character)
        if key in seen:
            continue
        seen.add(key)
        unique.append(rng)
    return unique
