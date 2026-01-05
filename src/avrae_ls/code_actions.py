from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from lsprotocol import types

from .codes import MISSING_GVAR_CODE, UNDEFINED_NAME_CODE, UNSUPPORTED_IMPORT_CODE
from .parser import DraconicBlock
from .source_context import build_source_context

log = logging.getLogger(__name__)

# Workspace-relative file that can extend snippet-based code actions without touching code.
SNIPPET_FILENAME = ".avraels.snippets.json"


@dataclass
class Snippet:
    key: str
    title: str
    body: str
    description: str | None = None
    kind: str = types.CodeActionKind.RefactorRewrite


DEFAULT_SNIPPETS: tuple[Snippet, ...] = (
    Snippet(
        key="drac2Wrapper",
        title="Wrap in <drac2>…</drac2>",
        body="<drac2>\n{content}\n</drac2>\n",
        description="Wrap the current selection or file in a draconic block.",
        kind=types.CodeActionKind.RefactorRewrite,
    ),
)


def code_actions_for_document(
    source: str,
    params: types.CodeActionParams,
    workspace_root: Path,
    *,
    treat_as_module: bool = False,
) -> List[types.CodeAction]:
    """Collect code actions for a document without requiring a running server."""
    actions: list[types.CodeAction] = []
    source_ctx = build_source_context(source, treat_as_module, apply_args=False)
    blocks = source_ctx.blocks
    snippets = _load_snippets(workspace_root)
    only_kinds = list(params.context.only or [])

    actions.extend(_snippet_actions(source, params, snippets, only_kinds, blocks))
    actions.extend(_diagnostic_actions(source, params, blocks, only_kinds))
    return actions


def _diagnostic_actions(
    source: str,
    params: types.CodeActionParams,
    blocks: list[DraconicBlock],
    only_kinds: Sequence[str],
) -> Iterable[types.CodeAction]:
    for diag in params.context.diagnostics or []:
        if diag.code == UNDEFINED_NAME_CODE and _kind_allowed(types.CodeActionKind.QuickFix, only_kinds):
            name = (diag.data or {}).get("name") if isinstance(diag.data, dict) else None
            if name:
                yield _stub_variable_action(source, params.text_document.uri, blocks, diag, name)
        if diag.code == MISSING_GVAR_CODE and _kind_allowed(types.CodeActionKind.QuickFix, only_kinds):
            gvar_id = (diag.data or {}).get("gvar") if isinstance(diag.data, dict) else None
            if gvar_id:
                yield _using_stub_action(source, params.text_document.uri, blocks, diag, gvar_id)
        if diag.code == UNSUPPORTED_IMPORT_CODE and _kind_allowed(types.CodeActionKind.QuickFix, only_kinds):
            module = (diag.data or {}).get("module") if isinstance(diag.data, dict) else None
            yield _rewrite_import_action(source, params.text_document.uri, diag, module)


def _snippet_actions(
    source: str,
    params: types.CodeActionParams,
    snippets: Sequence[Snippet],
    only_kinds: Sequence[str],
    blocks: list[DraconicBlock],
) -> Iterable[types.CodeAction]:
    # Skip wrapper suggestions when a draconic block already exists.
    has_draconic = bool(blocks)
    selection_text = _text_in_range(source, params.range)
    for snippet in snippets:
        if not _kind_allowed(snippet.kind, only_kinds):
            continue
        if snippet.key == "drac2Wrapper" and has_draconic:
            continue
        rendered = _render_snippet(snippet, selection_text or source)
        edit_range = params.range
        if not selection_text:
            edit_range = _full_range(source)
        edit = types.TextEdit(range=edit_range, new_text=rendered)
        action = types.CodeAction(
            title=snippet.title,
            kind=snippet.kind,
            edit=types.WorkspaceEdit(changes={params.text_document.uri: [edit]}),
        )
        if snippet.description:
            action.diagnostics = None
            action.command = None
        yield action


def _stub_variable_action(
    source: str,
    uri: str,
    blocks: list[DraconicBlock],
    diag: types.Diagnostic,
    name: str,
) -> types.CodeAction:
    insertion_line, indent = _block_insertion(blocks, diag.range.start.line, source)
    edit = types.TextEdit(
        range=types.Range(
            start=types.Position(line=insertion_line, character=indent),
            end=types.Position(line=insertion_line, character=indent),
        ),
        new_text=f"{' ' * indent}{name} = None\n",
    )
    return types.CodeAction(
        title=f"Create stub variable '{name}'",
        kind=types.CodeActionKind.QuickFix,
        diagnostics=[diag],
        edit=types.WorkspaceEdit(changes={uri: [edit]}),
    )


def _using_stub_action(
    source: str,
    uri: str,
    blocks: list[DraconicBlock],
    diag: types.Diagnostic,
    gvar_id: str,
) -> types.CodeAction:
    alias = _sanitize_symbol(gvar_id)
    insertion_line, indent = _block_insertion(blocks, diag.range.start.line, source)
    text = f"{' ' * indent}using({alias}=\"{gvar_id}\")\n"
    edit = types.TextEdit(
        range=types.Range(
            start=types.Position(line=insertion_line, character=indent),
            end=types.Position(line=insertion_line, character=indent),
        ),
        new_text=text,
    )
    return types.CodeAction(
        title=f"Add using() stub for gvar '{gvar_id}'",
        kind=types.CodeActionKind.QuickFix,
        diagnostics=[diag],
        edit=types.WorkspaceEdit(changes={uri: [edit]}),
    )


def _rewrite_import_action(
    source: str,
    uri: str,
    diag: types.Diagnostic,
    module: str | None,
) -> types.CodeAction:
    target = module or "module"
    replacement = f"using({target}=\"<gvar-id>\")"
    edit = types.TextEdit(range=diag.range, new_text=replacement)
    return types.CodeAction(
        title="Replace import with using()",
        kind=types.CodeActionKind.QuickFix,
        diagnostics=[diag],
        edit=types.WorkspaceEdit(changes={uri: [edit]}),
    )


def _block_insertion(blocks: list[DraconicBlock], line: int, source: str) -> tuple[int, int]:
    for block in blocks:
        start = block.line_offset
        end = block.line_offset + block.line_count
        if start <= line <= end:
            indent = _line_indent(source, start, default=block.char_offset)
            return start, indent
    return 0, _line_indent(source, 0, default=0)


def _line_indent(source: str, line: int, default: int = 0) -> int:
    lines = source.splitlines()
    if 0 <= line < len(lines):
        match = re.match(r"(\s*)", lines[line])
        if match:
            return len(match.group(1))
    return default


def _sanitize_symbol(label: str) -> str:
    cleaned = re.sub(r"\W+", "_", str(label))
    if cleaned and cleaned[0].isdigit():
        cleaned = f"gvar_{cleaned}"
    return cleaned or "gvar_import"


def _kind_allowed(kind: str, only: Sequence[str]) -> bool:
    if not only:
        return True
    for requested in only:
        if kind.startswith(requested) or requested.startswith(kind):
            return True
    return False


def _text_in_range(source: str, rng: types.Range) -> str:
    if rng.start == rng.end:
        return ""
    start = _offset_at_position(source, rng.start)
    end = _offset_at_position(source, rng.end)
    return source[start:end]


def _offset_at_position(source: str, pos: types.Position) -> int:
    if pos.line < 0:
        return 0
    lines = source.splitlines(keepends=True)
    if not lines:
        return 0
    if pos.line >= len(lines):
        return len(source)
    offset = sum(len(line) for line in lines[: pos.line])
    return min(offset + pos.character, offset + len(lines[pos.line]))


def _render_snippet(snippet: Snippet, selection: str) -> str:
    if "{content}" in snippet.body:
        return snippet.body.replace("{content}", selection)
    return snippet.body


def _full_range(source: str) -> types.Range:
    lines = source.splitlines()
    if not lines:
        return types.Range(start=types.Position(line=0, character=0), end=types.Position(line=0, character=0))
    end_line = len(lines) - 1
    end_char = len(lines[-1])
    return types.Range(
        start=types.Position(line=0, character=0),
        end=types.Position(line=end_line, character=end_char),
    )


def _load_snippets(root: Path) -> List[Snippet]:
    snippets: list[Snippet] = list(DEFAULT_SNIPPETS)
    user_file = root / SNIPPET_FILENAME
    if not user_file.exists():
        return snippets
    try:
        raw = json.loads(user_file.read_text())
    except Exception as exc:  # pragma: no cover - best-effort load
        log.warning("Failed to read snippet file %s: %s", user_file, exc)
        return snippets

    def _coerce(entry) -> Snippet | None:
        if not isinstance(entry, dict):
            return None
        key = str(entry.get("key") or entry.get("title") or "")
        body = entry.get("body")
        title = entry.get("title") or key
        if not key or not isinstance(body, str):
            return None
        return Snippet(
            key=key,
            title=str(title),
            body=body,
            description=str(entry.get("description") or ""),
            kind=str(entry.get("kind") or types.CodeActionKind.RefactorRewrite),
        )

    entries: Iterable[dict] = []
    if isinstance(raw, dict):
        entries = raw.values()
    elif isinstance(raw, list):
        entries = raw
    for entry in entries:
        coerced = _coerce(entry)
        if coerced:
            snippets.append(coerced)
    return snippets
