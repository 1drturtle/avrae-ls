from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import draconic
from lsprotocol import types

from .argument_parsing import apply_argument_parsing
from .parser import find_draconic_blocks

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


def build_symbol_table(source: str) -> SymbolTable:
    entries: list[SymbolEntry] = []
    parsed_source = apply_argument_parsing(source)
    blocks = find_draconic_blocks(parsed_source)
    if not blocks:
        entries.extend(_symbols_from_code(parsed_source, 0))
    else:
        for block in blocks:
            entries.extend(_symbols_from_code(block.code, block.line_offset))
    return SymbolTable(entries)


def document_symbols(source: str) -> List[types.DocumentSymbol]:
    table = build_symbol_table(source)
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


def _symbols_from_code(code: str, line_offset: int) -> List[SymbolEntry]:
    parser = draconic.DraconicInterpreter()
    local_offset = line_offset
    try:
        body = parser.parse(code)
    except draconic.DraconicSyntaxError:
        wrapped, added = _wrap_draconic(code)
        try:
            body = parser.parse(wrapped)
            local_offset += -added
        except draconic.DraconicSyntaxError:
            return []
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Symbol extraction failed: %s", exc)
        return []

    entries: list[SymbolEntry] = []
    for node in body:
        entry = _entry_from_node(node, local_offset)
        if entry:
            entries.append(entry)
    return entries


def _entry_from_node(node: ast.AST, line_offset: int = 0) -> SymbolEntry | None:
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
        else:
            return None
    else:
        return None

    rng = _range_from_positions(
        getattr(node, "lineno", 1),
        getattr(node, "col_offset", 0) + 1,
        getattr(node, "end_lineno", None),
        getattr(node, "end_col_offset", None),
    )
    rng = _shift_range(rng, line_offset)
    return SymbolEntry(name=name, kind=kind, range=rng, selection_range=rng)


def _range_from_positions(
    lineno: int | None,
    col_offset: int | None,
    end_lineno: int | None,
    end_col_offset: int | None,
) -> types.Range:
    start = types.Position(
        line=max((lineno or 1) - 1, 0),
        character=max((col_offset or 1) - 1, 0),
    )
    end = types.Position(
        line=max(((end_lineno or lineno or 1) - 1), 0),
        character=max(((end_col_offset or col_offset or 1) - 1), 0),
    )
    return types.Range(start=start, end=end)


def _shift_range(rng: types.Range, line_offset: int) -> types.Range:
    if line_offset == 0:
        return rng
    return types.Range(
        start=types.Position(line=max(rng.start.line + line_offset, 0), character=rng.start.character),
        end=types.Position(line=max(rng.end.line + line_offset, 0), character=rng.end.character),
    )


def _wrap_draconic(code: str) -> tuple[str, int]:
    indented = "\n".join(f"    {line}" for line in code.splitlines())
    wrapped = f"def __alias_main__():\n{indented}\n__alias_main__()"
    return wrapped, 1
