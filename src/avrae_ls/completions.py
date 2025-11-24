from __future__ import annotations

import ast
import inspect
import re
import typing
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, ClassVar, Dict, Iterable, List, Optional

from lsprotocol import types

from .context import ContextData, GVarResolver
from .argparser import ParsedArguments
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
    SimpleCombat,
    SimpleCombatant,
    GuildAPI,
    RoleAPI,
    AuthorAPI,
    SimpleEffect,
    SimpleGroup,
    SimpleRollResult,
)
from .signature_help import FunctionSig


class _BuiltinList:
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = ["append", "extend", "insert", "remove", "pop", "clear", "index", "count", "sort", "reverse", "copy"]

    def __iter__(self) -> Iterable[Any]:
        return iter([])

    def append(self, value: Any) -> None: ...
    def extend(self, iterable: Iterable[Any]) -> None: ...
    def insert(self, index: int, value: Any) -> None: ...
    def remove(self, value: Any) -> None: ...
    def pop(self, index: int = -1) -> Any: ...
    def clear(self) -> None: ...
    def index(self, value: Any, start: int = 0, stop: int | None = None) -> int: ...
    def count(self, value: Any) -> int: ...
    def sort(self, *, key=None, reverse: bool = False) -> None: ...
    def reverse(self) -> None: ...
    def copy(self) -> list[Any]: ...


class _BuiltinDict:
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = ["get", "keys", "values", "items", "pop", "popitem", "update", "setdefault", "clear", "copy"]

    def __iter__(self) -> Iterable[Any]:
        return iter({})

    def get(self, key: Any, default: Any = None) -> Any: ...
    def keys(self) -> Any: ...
    def values(self) -> Any: ...
    def items(self) -> Any: ...
    def pop(self, key: Any, default: Any = None) -> Any: ...
    def popitem(self) -> tuple[Any, Any]: ...
    def update(self, *args, **kwargs) -> None: ...
    def setdefault(self, key: Any, default: Any = None) -> Any: ...
    def clear(self) -> None: ...
    def copy(self) -> dict[Any, Any]: ...


class _BuiltinStr:
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = [
        "lower",
        "upper",
        "title",
        "split",
        "join",
        "replace",
        "strip",
        "startswith",
        "endswith",
        "format",
    ]

    def __iter__(self) -> Iterable[str]:
        return iter("")

    def lower(self) -> str: ...
    def upper(self) -> str: ...
    def title(self) -> str: ...
    def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]: ...
    def join(self, iterable: Iterable[str]) -> str: ...
    def replace(self, old: str, new: str, count: int = -1) -> str: ...
    def strip(self, chars: str | None = None) -> str: ...
    def startswith(self, prefix, start: int = 0, end: int | None = None) -> bool: ...
    def endswith(self, suffix, start: int = 0, end: int | None = None) -> bool: ...
    def format(self, *args, **kwargs) -> str: ...


TYPE_MAP: Dict[str, object] = {
    "character": CharacterAPI,
    "combat": SimpleCombat,
    "SimpleCombat": SimpleCombat,
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
    "combatant": SimpleCombatant,
    "SimpleCombatant": SimpleCombatant,
    "group": SimpleGroup,
    "SimpleGroup": SimpleGroup,
    "effect": SimpleEffect,
    "SimpleEffect": SimpleEffect,
    "list": _BuiltinList,
    "dict": _BuiltinDict,
    "str": _BuiltinStr,
    "ParsedArguments": ParsedArguments,
}


IDENT_RE = re.compile(r"[A-Za-z_]\w*$")
ATTR_RE = re.compile(r"([A-Za-z_][\w\.\(\)]*)\.(?:([A-Za-z_]\w*)\s*)?$")
DICT_GET_RE = re.compile(r"^([A-Za-z_]\w*)\.get\(\s*(['\"])(.+?)\2")
ATTR_AT_CURSOR_RE = re.compile(r"([A-Za-z_][\w\.\(\)]*)\.([A-Za-z_]\w*)")


@dataclass
class Suggestion:
    name: str
    kind: types.CompletionItemKind
    detail: str = ""
    documentation: str = ""


@dataclass
class AttrMeta:
    doc: str = ""
    type_name: str = ""
    element_type: str = ""


@dataclass
class MethodMeta:
    signature: str = ""
    doc: str = ""


@dataclass
class TypeMeta:
    attrs: Dict[str, AttrMeta]
    methods: Dict[str, MethodMeta]
    element_type: str = ""


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
    attr_ctx = _attribute_receiver_and_prefix(code, line, character)
    if attr_ctx:
        receiver, attr_prefix = attr_ctx
        sanitized = _sanitize_incomplete_line(code, line, character)
        type_map = _infer_type_map(sanitized)
        return _attribute_completions(receiver, attr_prefix, sanitized, type_map)

    line_text = _line_text_to_cursor(code, line, character)
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
    meta = _type_meta(type_key)
    detail = f"{type_key}()"

    for name, attr_meta in meta.attrs.items():
        if prefix and not name.startswith(prefix):
            continue
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Field,
                detail=detail,
                documentation=attr_meta.doc or None,
            )
        )
    for name, method_meta in meta.methods.items():
        if prefix and not name.startswith(prefix):
            continue
        method_detail = method_meta.signature or f"{name}()"
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Method,
                detail=method_detail,
                documentation=method_meta.doc or None,
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
    attr_ctx = _attribute_receiver_and_prefix(code, line, character, capture_full_token=True)
    if attr_ctx:
        receiver, attr_prefix = attr_ctx
        inferred = _resolve_type_name(receiver, code, type_map)
        meta = _type_meta(inferred)
        if attr_prefix in meta.attrs:
            doc = meta.attrs[attr_prefix].doc
            contents = f"```avrae\n{inferred}().{attr_prefix}\n```"
            if doc:
                contents += f"\n\n{doc}"
            return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))
        if attr_prefix in meta.methods:
            method_meta = meta.methods[attr_prefix]
            signature = method_meta.signature or f"{attr_prefix}()"
            doc = method_meta.doc
            contents = f"```avrae\n{signature}\n```"
            if doc:
                contents += f"\n\n{doc}"
            return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))

    word, _, _ = _word_at_position(line_text, character)
    if not word:
        return None
    if word in bindings:
        return _format_binding_hover(word, bindings[word], "local")
    if word in type_map:
        type_label = _display_type_label(type_map[word])
        contents = f"`{word}` type: `{type_label}`"
        return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))
    if word in sigs:
        sig = sigs[word]
        contents = f"```avrae\n{sig.label}\n```\n\n{sig.doc}"
        return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))

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


def _attribute_receiver_and_prefix(code: str, line: int, character: int, capture_full_token: bool = False) -> Optional[tuple[str, str]]:
    lines = code.splitlines()
    if line >= len(lines):
        return None
    line_text = lines[line]
    end = character
    if capture_full_token:
        while end < len(line_text) and (line_text[end].isalnum() or line_text[end] == "_"):
            end += 1
    line_text = line_text[: end]
    dot = line_text.rfind(".")
    if dot == -1:
        return None
    tail = line_text[dot + 1 :]
    prefix_match = re.match(r"\s*([A-Za-z_]\w*)?", tail)
    prefix = prefix_match.group(1) or "" if prefix_match else ""
    suffix = tail[prefix_match.end() if prefix_match else 0 :]
    placeholder = "__COMPLETE__"
    new_line = f"{line_text[:dot]}.{placeholder}{suffix}"
    # Close unmatched parentheses so the temporary code parses.
    paren_balance = new_line.count("(") - new_line.count(")")
    if paren_balance > 0:
        new_line = new_line + (")" * paren_balance)
    mod_lines = list(lines)
    mod_lines[line] = new_line
    mod_code = "\n".join(mod_lines)
    try:
        tree = ast.parse(mod_code)
    except SyntaxError:
        return None

    receiver_src: Optional[str] = None

    class Finder(ast.NodeVisitor):
        def visit_Attribute(self, node: ast.Attribute):
            nonlocal receiver_src
            if isinstance(node.attr, str) and node.attr == placeholder:
                try:
                    receiver_src = ast.unparse(node.value)
                except Exception:
                    receiver_src = None
            self.generic_visit(node)

    Finder().visit(tree)
    if receiver_src is None:
        return None
    return receiver_src, prefix


def _sanitize_incomplete_line(code: str, line: int, character: int) -> str:
    lines = code.splitlines()
    if 0 <= line < len(lines):
        prefix = lines[line][:character]
        trimmed = prefix.rstrip()
        if trimmed.endswith("."):
            prefix = trimmed[:-1]
        else:
            dot = prefix.rfind(".")
            if dot != -1:
                after = prefix[dot + 1 :]
                if not re.match(r"\s*[A-Za-z_]", after):
                    prefix = prefix[:dot] + after
        lines[line] = prefix
        candidate = "\n".join(lines)
        try:
            ast.parse(candidate)
        except SyntaxError:
            indent = re.match(r"[ \t]*", lines[line]).group(0)
            lines[line] = indent + "pass"
    return "\n".join(lines)


def _line_text(code: str, line: int) -> str:
    lines = code.splitlines()
    if line < 0 or line >= len(lines):
        return ""
    return lines[line]


def _display_type_label(type_key: str) -> str:
    if type_key in TYPE_MAP:
        return TYPE_MAP[type_key].__name__
    return type_key


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
            val_type, elem_type = self._value_type(node.value)
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if val_type:
                    type_map[target.id] = val_type
                if elem_type:
                    type_map[f"{target.id}.__element__"] = elem_type
                self._record_dict_key_types(target.id, node.value)
            self.generic_visit(node)

        def visit_For(self, node: ast.For):
            iter_type, elem_type = self._value_type(node.iter)
            if not elem_type and isinstance(node.iter, ast.Name):
                elem_type = type_map.get(f"{node.iter.id}.__element__")
            if elem_type and isinstance(node.target, ast.Name):
                type_map[node.target.id] = elem_type
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign):
            val_type, elem_type = self._value_type(node.value) if node.value else (None, None)
            if isinstance(node.target, ast.Name):
                if val_type:
                    type_map[node.target.id] = val_type
                if elem_type:
                    type_map[f"{node.target.id}.__element__"] = elem_type
                self._record_dict_key_types(node.target.id, node.value)
            self.generic_visit(node)

        def _value_type(self, value: ast.AST | None) -> tuple[Optional[str], Optional[str]]:
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                if value.func.id in {"character", "combat"}:
                    return value.func.id, None
                if value.func.id == "vroll":
                    return "SimpleRollResult", None
                if value.func.id == "argparse":
                    return "ParsedArguments", None
                if value.func.id in {"list", "dict", "str"}:
                    return value.func.id, None
            if isinstance(value, ast.List):
                return "list", None
            if isinstance(value, ast.Dict):
                return "dict", None
            if isinstance(value, ast.Constant):
                if isinstance(value.value, str):
                    return "str", None
            if isinstance(value, ast.Name):
                if value.id in type_map:
                    return type_map[value.id], type_map.get(f"{value.id}.__element__")
                if value.id in {"character", "combat", "ctx"}:
                    return value.id, None
            if isinstance(value, ast.Attribute):
                attr_name = value.attr
                base_type = None
                base_elem = None
                if isinstance(value.value, ast.Name):
                    base_type = type_map.get(value.value.id)
                    base_elem = type_map.get(f"{value.value.id}.__element__")
                if base_type is None:
                    base_type, base_elem = self._value_type(value.value)
                if base_type:
                    meta = _type_meta(base_type)
                    attr_meta = meta.attrs.get(attr_name)
                    if attr_meta:
                        if attr_meta.type_name:
                            return attr_meta.type_name, attr_meta.element_type or None
                        if attr_meta.element_type:
                            return base_type, attr_meta.element_type
                    if base_elem:
                        return base_elem, None
                    if attr_name in TYPE_MAP:
                        return attr_name, None
                return None, None
            return None, None

        def _record_dict_key_types(self, var_name: str, value: ast.AST | None) -> None:
            if not isinstance(value, ast.Dict):
                return
            for key_node, val_node in zip(value.keys or [], value.values or []):
                if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                    val_type, elem_type = self._value_type(val_node)
                    if val_type:
                        type_map[f"{var_name}.{key_node.value}"] = val_type
                    if elem_type:
                        type_map[f"{var_name}.{key_node.value}.__element__"] = elem_type

    Visitor().visit(tree)
    return type_map


def _resolve_type_name(receiver: str, code: str, type_map: Dict[str, str] | None = None) -> str:
    mapping = type_map or _infer_type_map(code)
    get_match = DICT_GET_RE.match(receiver)
    if get_match:
        base, _, key = get_match.groups()
        dict_key = f"{base}.{key}"
        if dict_key in mapping:
            return mapping[dict_key]
    bracket = receiver.rfind("[")
    if bracket != -1 and receiver.endswith("]"):
        base_expr = receiver[:bracket]
        elem_hint = mapping.get(f"{base_expr}.__element__")
        if elem_hint:
            return elem_hint
        base_type = _resolve_type_name(base_expr, code, mapping)
        if base_type:
            base_meta = _type_meta(base_type)
            if base_meta.element_type:
                return base_meta.element_type
            return base_type
    receiver = receiver.rstrip("()")
    if "." in receiver:
        base_expr, attr_name = receiver.rsplit(".", 1)
        base_type = _resolve_type_name(base_expr, code, mapping)
        if base_type:
            meta = _type_meta(base_type)
            attr_key = attr_name.split("[", 1)[0]
            attr_meta = meta.attrs.get(attr_key)
            if attr_meta:
                if attr_meta.element_type:
                    return attr_meta.element_type
                if attr_meta.type_name:
                    return attr_meta.type_name

    if receiver in mapping:
        return mapping[receiver]
    elem_key = f"{receiver}.__element__"
    if elem_key in mapping:
        return mapping[elem_key]
    if receiver in TYPE_MAP:
        return receiver
    tail = receiver.split(".")[-1].split("[", 1)[0]
    if tail in TYPE_MAP:
        return tail
    return receiver


def _type_meta(type_name: str) -> TypeMeta:
    return _type_meta_map().get(type_name, TypeMeta(attrs={}, methods={}, element_type=""))


@lru_cache()
def _type_meta_map() -> Dict[str, TypeMeta]:
    meta: dict[str, TypeMeta] = {}
    reverse_type_map: dict[type, str] = {}
    for key, cls in TYPE_MAP.items():
        reverse_type_map[cls] = key

    def _iter_element_for_type_name(type_name: str) -> str:
        cls = TYPE_MAP.get(type_name)
        if not cls:
            return ""
        return _element_type_from_iterable(cls, reverse_type_map)

    for type_name, cls in TYPE_MAP.items():
        attrs: dict[str, AttrMeta] = {}
        methods: dict[str, MethodMeta] = {}
        iterable_element = _iter_element_for_type_name(type_name)

        for attr in getattr(cls, "ATTRS", []):
            doc = ""
            type_name_hint = ""
            element_type_hint = ""
            try:
                attr_obj = getattr(cls, attr)
            except Exception:
                attr_obj = None
            if isinstance(attr_obj, property) and attr_obj.fget:
                doc = (attr_obj.fget.__doc__ or "").strip()
                ann = _return_annotation(attr_obj.fget, cls)
                type_name_hint, element_type_hint = _type_names_from_annotation(ann, reverse_type_map)
            elif attr_obj is not None:
                doc = (getattr(attr_obj, "__doc__", "") or "").strip()
            if not type_name_hint and not element_type_hint:
                ann = _class_annotation(cls, attr)
                type_name_hint, element_type_hint = _type_names_from_annotation(ann, reverse_type_map)
            if type_name_hint and not element_type_hint:
                element_type_hint = _iter_element_for_type_name(type_name_hint)
            attrs[attr] = AttrMeta(doc=doc, type_name=type_name_hint, element_type=element_type_hint)

        for meth in getattr(cls, "METHODS", []):
            doc = ""
            sig_label = ""
            try:
                meth_obj = getattr(cls, meth)
            except Exception:
                meth_obj = None
            if callable(meth_obj):
                sig_label = _format_method_signature(meth, meth_obj)
                doc = (meth_obj.__doc__ or "").strip()
            methods[meth] = MethodMeta(signature=sig_label, doc=doc)

        meta[type_name] = TypeMeta(attrs=attrs, methods=methods, element_type=iterable_element)
    return meta


def _format_method_signature(name: str, obj: Any) -> str:
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return f"{name}()"
    params = list(sig.parameters.values())
    if params and params[0].name in {"self", "cls"}:
        params = params[1:]
    sig = sig.replace(parameters=params)
    return f"{name}{sig}"


def _return_annotation(func: Any, cls: type) -> Any:
    try:
        module = inspect.getmodule(func) or inspect.getmodule(cls)
        globalns = module.__dict__ if module else None
        hints = typing.get_type_hints(func, globalns=globalns, include_extras=False)
        return hints.get("return")
    except Exception:
        return getattr(func, "__annotations__", {}).get("return")


def _class_annotation(cls: type, attr: str) -> Any:
    try:
        module = inspect.getmodule(cls)
        globalns = module.__dict__ if module else None
        hints = typing.get_type_hints(cls, globalns=globalns, include_extras=False)
        if attr in hints:
            return hints[attr]
    except Exception:
        pass
    return getattr(getattr(cls, "__annotations__", {}), "get", lambda _k: None)(attr)


def _type_names_from_annotation(ann: Any, reverse_type_map: Dict[type, str]) -> tuple[str, str]:
    if ann is None:
        return "", ""
    if isinstance(ann, str):
        return "", ""
    try:
        origin = getattr(ann, "__origin__", None)
    except Exception:
        origin = None
    args = getattr(ann, "__args__", ()) if origin else ()

    if ann in reverse_type_map:
        return reverse_type_map[ann], ""

    # handle list/sequence typing to detect element type
    iterable_origins = {list, List, Iterable, typing.Sequence, typing.Iterable}
    try:
        from collections.abc import Iterable as ABCIterable, Sequence as ABCSequence
        iterable_origins.update({ABCIterable, ABCSequence})
    except Exception:
        pass
    if origin in iterable_origins:
        if args:
            elem = args[0]
            elem_name, _ = _type_names_from_annotation(elem, reverse_type_map)
            container_name = reverse_type_map.get(origin) or "list"
            return container_name, elem_name
        return reverse_type_map.get(origin) or "list", ""

    if isinstance(ann, type) and ann in reverse_type_map:
        return reverse_type_map[ann], ""
    return "", ""


def _element_type_from_iterable(cls: type, reverse_type_map: Dict[type, str]) -> str:
    try:
        hints = typing.get_type_hints(cls.__iter__, globalns=inspect.getmodule(cls).__dict__, include_extras=False)
        ret_ann = hints.get("return")
        _, elem = _type_names_from_annotation(ret_ann, reverse_type_map)
        return elem
    except Exception:
        return ""


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
                return SimpleCombat(ctx_data.combat)
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
    type_name = _describe_type(value)
    preview = _preview_value(value)
    contents = f"**{label}** `{name}`\n\nType: `{type_name}`\nValue: `{preview}`"
    return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))


def _describe_type(value: Any) -> str:
    # Provide light element-type hints for common iterables so hover shows list[Foo].
    def _iterable_type(iterable: Iterable[Any], container: str) -> str:
        try:
            seen = {type(item).__name__ for item in iterable if item is not None}
        except Exception:
            return container
        return f"{container}[{seen.pop()}]" if len(seen) == 1 else container

    try:
        if isinstance(value, list):
            return _iterable_type(value, "list")
        if isinstance(value, tuple):
            return _iterable_type(value, "tuple")
        if isinstance(value, set):
            return _iterable_type(value, "set")
    except Exception:
        pass
    return type(value).__name__


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
