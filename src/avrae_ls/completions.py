from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from lsprotocol import types

from .context import ContextData, GVarResolver
from .runtime import _default_builtins
from .api import (
    AliasAction,
    AliasBaseStats,
    AliasCoinpurse,
    AliasContextAPI,
    AliasCustomCounter,
    AliasDeathSaves,
    AliasResistances,
    AliasSaves,
    AliasSkill,
    AliasSkills,
    AliasSpellbook,
    AliasSpellbookSpell,
    AliasAttack,
    AliasAttackList,
    AliasLevels,
    CategoryAPI,
    ChannelAPI,
    CharacterAPI,
    CombatAPI,
    CombatantAPI,
    GuildAPI,
    RoleAPI,
    AuthorAPI,
    SimpleEffectAPI,
    SimpleGroupAPI,
    SimpleRollResult,
)
from .signature_help import FunctionSig

TYPE_MAP: Dict[str, object] = {
    "character": CharacterAPI,
    "combat": CombatAPI,
    "ctx": AliasContextAPI,
    "SimpleRollResult": SimpleRollResult,
    "stats": AliasBaseStats,
    "levels": AliasLevels,
    "attacks": AliasAttackList,
    "attack": AliasAttack,
    "skills": AliasSkills,
    "skill": AliasSkill,
    "saves": AliasSaves,
    "resistances": AliasResistances,
    "coinpurse": AliasCoinpurse,
    "custom_counter": AliasCustomCounter,
    "consumable": AliasCustomCounter,
    "death_saves": AliasDeathSaves,
    "action": AliasAction,
    "spellbook": AliasSpellbook,
    "spell": AliasSpellbookSpell,
    "guild": GuildAPI,
    "channel": ChannelAPI,
    "category": CategoryAPI,
    "author": AuthorAPI,
    "role": RoleAPI,
    "combatant": CombatantAPI,
    "group": SimpleGroupAPI,
    "effect": SimpleEffectAPI,
}


IDENT_RE = re.compile(r"[A-Za-z_]\w*$")
ATTR_RE = re.compile(r"([A-Za-z_][\w\.]*)\.([A-Za-z_]\w*)?\s*$")
ATTR_AT_CURSOR_RE = re.compile(r"([A-Za-z_][\w\.]*?(?:\(\))?)\.([A-Za-z_]\w*)")


@dataclass
class Suggestion:
    name: str
    kind: types.CompletionItemKind
    detail: str = ""
    documentation: str = ""


def gather_suggestions(
    ctx_data: ContextData,
    resolver: GVarResolver,
    sigs: Dict[str, FunctionSig],
) -> List[Suggestion]:
    suggestions: list[Suggestion] = []

    for name, sig in sigs.items():
        suggestions.append(
            Suggestion(
                name=name,
                kind=types.CompletionItemKind.Function,
                detail=sig.label,
                documentation=sig.doc,
            )
        )

    vars_map = ctx_data.vars.to_initial_names()
    for name in vars_map:
        suggestions.append(Suggestion(name=name, kind=types.CompletionItemKind.Variable, detail="var"))

    gvars = resolver.snapshot()
    for name in gvars:
        suggestions.append(Suggestion(name=name, kind=types.CompletionItemKind.Variable, detail="gvar"))

    for name in _default_builtins().keys():
        if name not in sigs:
            suggestions.append(Suggestion(name=name, kind=types.CompletionItemKind.Function))

    # context helpers
    suggestions.append(Suggestion(name="character", kind=types.CompletionItemKind.Function, detail="Alias character()"))
    suggestions.append(Suggestion(name="combat", kind=types.CompletionItemKind.Function, detail="Alias combat()"))
    suggestions.append(Suggestion(name="ctx", kind=types.CompletionItemKind.Variable, detail="Alias context"))

    return suggestions


def completion_items_for_position(
    code: str,
    line: int,
    character: int,
    suggestions: Iterable[Suggestion],
) -> List[types.CompletionItem]:
    line_text = _line_text_to_cursor(code, line, character)
    attr_match = ATTR_RE.search(line_text)
    if attr_match:
        receiver = attr_match.group(1)
        attr_prefix = attr_match.group(2) or ""
        sanitized = _sanitize_incomplete_line(code, line, character)
        type_map = _infer_type_map(sanitized)
        return _attribute_completions(receiver, attr_prefix, sanitized, type_map)

    prefix = _current_prefix(line_text)
    items: list[types.CompletionItem] = []
    for sugg in suggestions:
        if prefix and not sugg.name.startswith(prefix):
            continue
        items.append(
            types.CompletionItem(
                label=sugg.name,
                kind=sugg.kind,
                detail=sugg.detail or None,
                documentation=sugg.documentation or None,
            )
        )
    return items


def _attribute_completions(receiver: str, prefix: str, code: str, type_map: Dict[str, str] | None = None) -> List[types.CompletionItem]:
    items: list[types.CompletionItem] = []
    type_key = _resolve_type_name(receiver, code, type_map)
    attrs, methods = _type_meta(type_key)
    detail = f"{type_key}()"

    for name in attrs:
        if prefix and not name.startswith(prefix):
            continue
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Field,
                detail=detail,
            )
        )
    for name in methods:
        if prefix and not name.startswith(prefix):
            continue
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Method,
                detail=f"{detail} method",
            )
        )
    return items


def hover_for_position(
    code: str,
    line: int,
    character: int,
    sigs: Dict[str, FunctionSig],
    ctx_data: ContextData,
    resolver: GVarResolver,
) -> Optional[types.Hover]:
    line_text = _line_text(code, line)
    type_map = _infer_type_map(code)
    bindings = _infer_constant_bindings(code, line, ctx_data)
    receiver, attr_name = _attribute_at_position(line_text, character)
    if receiver and attr_name:
        inferred = _resolve_type_name(receiver, code, type_map)
        attrs, methods = _type_meta(inferred)
        if attr_name in attrs:
            contents = f"`{inferred}().{attr_name}`"
            return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))
        if attr_name in methods:
            contents = f"`{inferred}().{attr_name}()`"
            return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))

    word, _, _ = _word_at_position(line_text, character)
    if not word:
        return None
    if word in type_map:
        contents = f"`{word}` type: `{type_map[word]}()`"
        return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))
    if word in sigs:
        sig = sigs[word]
        contents = f"```avrae\n{sig.label}\n```\n\n{sig.doc}"
        return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))
    if word in bindings:
        return _format_binding_hover(word, bindings[word], "local")

    vars_map = ctx_data.vars.to_initial_names()
    if word in vars_map:
        return _format_binding_hover(word, vars_map[word], "var")

    gvars = resolver.snapshot()
    if word in gvars:
        return _format_binding_hover(word, gvars[word], "gvar")
    return None


def _current_prefix(line_text: str) -> str:
    match = IDENT_RE.search(line_text)
    return match.group(0) if match else ""


def _word_from_line(text: str, cursor: int) -> str:
    return _word_at_position(text, cursor)[0]


def _word_at_position(text: str, cursor: int) -> tuple[str, int, int]:
    cursor = max(0, min(cursor, len(text)))
    start = cursor
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        start -= 1
    end = cursor
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    return text[start:end], start, end


def _line_text_to_cursor(code: str, line: int, character: int) -> str:
    lines = code.splitlines()
    if line >= len(lines):
        return ""
    return lines[line][:character]


def _sanitize_incomplete_line(code: str, line: int, character: int) -> str:
    lines = code.splitlines()
    if 0 <= line < len(lines):
        prefix = lines[line][:character].rstrip()
        if prefix.endswith("."):
            prefix = prefix[:-1]
        lines[line] = prefix
    return "\n".join(lines)


def _line_text(code: str, line: int) -> str:
    lines = code.splitlines()
    if line < 0 or line >= len(lines):
        return ""
    return lines[line]


def _infer_receiver_type(code: str, name: str) -> Optional[str]:
    return _infer_type_map(code).get(name)


def _infer_type_map(code: str) -> Dict[str, str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    type_map: dict[str, str] = {}

    class Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign):
            val_type = self._value_type(node.value)
            if val_type:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        type_map[target.id] = val_type
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign):
            val_type = self._value_type(node.value) if node.value else None
            if val_type and isinstance(node.target, ast.Name):
                type_map[node.target.id] = val_type
            self.generic_visit(node)

        @staticmethod
        def _value_type(value: ast.AST | None) -> Optional[str]:
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                if value.func.id in {"character", "combat"}:
                    return value.func.id
                if value.func.id == "vroll":
                    return "SimpleRollResult"
            if isinstance(value, ast.Name) and value.id in {"character", "combat", "ctx"}:
                return value.id
            return None

    Visitor().visit(tree)
    return type_map


def _resolve_type_name(receiver: str, code: str, type_map: Dict[str, str] | None = None) -> str:
    receiver = receiver.rstrip("()")
    mapping = type_map or _infer_type_map(code)
    if receiver in mapping:
        return mapping[receiver]
    if receiver in TYPE_MAP:
        return receiver
    tail = receiver.split(".")[-1]
    if tail in TYPE_MAP:
        return tail
    return receiver


def _type_meta(type_name: str) -> tuple[List[str], List[str]]:
    cls = TYPE_MAP.get(type_name)
    if cls is None:
        return [], []
    attrs = list(getattr(cls, "ATTRS", []))
    methods = list(getattr(cls, "METHODS", []))
    return sorted(set(attrs)), sorted(set(methods))


def _attribute_at_position(line_text: str, cursor: int) -> tuple[Optional[str], Optional[str]]:
    cursor = max(0, min(cursor, len(line_text)))
    for match in ATTR_AT_CURSOR_RE.finditer(line_text):
        start, end = match.span(2)
        if start <= cursor <= end:
            return match.group(1), match.group(2)
    return None, None


def _infer_constant_bindings(code: str, upto_line: int | None, ctx_data: ContextData) -> Dict[str, Any]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    bindings: dict[str, Any] = {}
    limit = None if upto_line is None else upto_line + 1

    def _value_for(node: ast.AST) -> Any | None:
        value = _literal_value(node)
        if value is None:
            value = _evaluated_value(node, ctx_data, bindings)
        return value

    def _loop_binding(node: ast.AST) -> Any | None:
        value = _value_for(node)
        if value is None:
            return _LoopVarBinding()
        try:
            iterator = iter(value)
        except TypeError:
            return _LoopVarBinding()
        try:
            return next(iterator)
        except StopIteration:
            return _LoopVarBinding()

    class Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign):
            if limit is not None and node.lineno > limit:
                return
            value = _value_for(node.value)
            if value is None:
                self.generic_visit(node)
                return
            for name in _names_from_target(node.targets):
                bindings[name] = value

        def visit_AnnAssign(self, node: ast.AnnAssign):
            if limit is not None and node.lineno > limit:
                return
            if node.value is None:
                return
            value = _value_for(node.value)
            if value is None:
                self.generic_visit(node)
                return
            for name in _names_from_target([node.target]):
                bindings[name] = value

        def visit_For(self, node: ast.For):
            if limit is not None and node.lineno > limit:
                return
            loop_val = _loop_binding(node.iter)
            for name in _names_from_target([node.target]):
                bindings[name] = loop_val
            self.generic_visit(node)

        def visit_AsyncFor(self, node: ast.AsyncFor):
            if limit is not None and node.lineno > limit:
                return
            loop_val = _loop_binding(node.iter)
            for name in _names_from_target([node.target]):
                bindings[name] = loop_val
            self.generic_visit(node)

    Visitor().visit(tree)
    return bindings


def _names_from_target(targets: Iterable[ast.expr]) -> List[str]:
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(_names_from_target(target.elts))
    return names


def _literal_value(node: ast.AST) -> Any | None:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        val = _literal_value(node.operand)
        if isinstance(val, (int, float, complex)):
            return val if isinstance(node.op, ast.UAdd) else -val
        return None
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = []
        for elt in node.elts:
            val = _literal_value(elt)
            if val is None:
                return None
            items.append(val)
        if isinstance(node, ast.List):
            return items
        if isinstance(node, ast.Tuple):
            return tuple(items)
        return set(items)
    if isinstance(node, ast.Dict):
        keys = []
        values = []
        for k, v in zip(node.keys, node.values):
            key_val = _literal_value(k) if k is not None else None
            val_val = _literal_value(v)
            if key_val is None or val_val is None:
                return None
            keys.append(key_val)
            values.append(val_val)
        return dict(zip(keys, values))
    return None


def _evaluated_value(node: ast.AST, ctx_data: ContextData, bindings: Dict[str, Any] | None = None) -> Any | None:
    bindings = bindings or {}
    try:
        return _eval_node(node, ctx_data, bindings)
    except Exception:
        return None


def _eval_node(node: ast.AST, ctx_data: ContextData, bindings: Dict[str, Any]) -> Any | None:
    if isinstance(node, ast.Attribute):
        base = _eval_node(node.value, ctx_data, bindings)
        if base is None:
            return None
        return getattr(base, node.attr, None)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id == "character":
                return CharacterAPI(ctx_data.character)
            if node.func.id == "combat":
                return CombatAPI(ctx_data.combat)
            if node.func.id == "range":
                args = []
                for arg in node.args:
                    val = _literal_value(arg)
                    if val is None:
                        return None
                    args.append(val)
                try:
                    return range(*args)
                except Exception:
                    return None
        if isinstance(node.func, ast.Attribute):
            base = _eval_node(node.func.value, ctx_data, bindings)
            if base is None:
                return None
            method_name = node.func.attr
            if not _is_safe_call(base, method_name):
                return None
            args = []
            for arg in node.args:
                val = _literal_value(arg)
                if val is None:
                    val = _eval_node(arg, ctx_data, bindings)
                if val is None:
                    return None
                args.append(val)
            kwargs = {}
            for kw in node.keywords:
                if kw.arg is None:
                    return None
                val = _literal_value(kw.value)
                if val is None:
                    val = _eval_node(kw.value, ctx_data, bindings)
                if val is None:
                    return None
                kwargs[kw.arg] = val
            callee = getattr(base, method_name, None)
            if not callable(callee):
                return None
            try:
                return callee(*args, **kwargs)
            except Exception:
                return None
    if isinstance(node, ast.Name):
        if node.id in bindings:
            return bindings[node.id]
        if node.id == "ctx":
            return AliasContextAPI(ctx_data.ctx)
    return None


def _is_safe_call(base: Any, method: str) -> bool:
    for cls, allowed in SAFE_METHODS.items():
        if isinstance(base, cls) and method in allowed:
            return True
    return False


def _format_binding_hover(name: str, value: Any, label: str) -> types.Hover:
    type_name = type(value).__name__
    preview = _preview_value(value)
    contents = f"**{label}** `{name}`\n\nType: `{type_name}`\nValue: `{preview}`"
    return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))


def _preview_value(value: Any) -> str:
    text = repr(value)
    if len(text) > 120:
        text = text[:117] + "..."
    return text


class _LoopVarBinding:
    def __repr__(self) -> str:
        return "<loop item>"
SAFE_METHODS: dict[type, set[str]] = {
    AliasLevels: {"get"},
    dict: {"get"},
}
