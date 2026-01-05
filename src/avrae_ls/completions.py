from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from lsprotocol import types

from .context import ContextData, GVarResolver
from .runtime import _default_builtins
from .api import AliasContextAPI, CharacterAPI, SimpleCombat
from .signature_help import FunctionSig
from .type_inference import annotation_label, infer_type_map, resolve_type_name
from .type_system import display_type_label, is_safe_call, type_meta
from .ast_utils import collect_target_names


IDENT_RE = re.compile(r"[A-Za-z_]\w*$")


@dataclass
class Suggestion:
    name: str
    kind: types.CompletionItemKind
    detail: str = ""
    documentation: str = ""


@dataclass(frozen=True)
class FunctionContext:
    params: list[str]
    param_types: dict[str, str]
    locals: list[str]


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

    for name in ctx_data.vars.svars:
        suggestions.append(Suggestion(name=name, kind=types.CompletionItemKind.Variable, detail="svar"))

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
        type_map = infer_type_map(sanitized, line)
        return _attribute_completions(receiver, attr_prefix, sanitized, type_map)

    line_text = _line_text_to_cursor(code, line, character)
    prefix = _current_prefix(line_text)
    items: list[types.CompletionItem] = []
    seen: set[str] = set()
    func_ctx = _function_context_at_line(code, line)
    type_map = infer_type_map(code, line) if func_ctx else {}
    for name in func_ctx.params if func_ctx else []:
        if prefix and not name.startswith(prefix):
            continue
        detail = "param"
        param_type = func_ctx.param_types.get(name) if func_ctx else None
        if param_type:
            detail = f"param: {param_type}"
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Variable,
                detail=detail,
            )
        )
        seen.add(name)
    for name in func_ctx.locals if func_ctx else []:
        if name in seen:
            continue
        if prefix and not name.startswith(prefix):
            continue
        detail = "local"
        local_type = type_map.get(name)
        if local_type:
            detail = f"local: {display_type_label(local_type)}"
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Variable,
                detail=detail,
            )
        )
        seen.add(name)
    for sugg in suggestions:
        if sugg.name in seen:
            continue
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
    type_key = resolve_type_name(receiver, code, type_map)
    if IDENT_RE.fullmatch(receiver) and (not type_map or receiver not in type_map) and type_key == receiver:
        # Avoid treating arbitrary variable names as known API types unless they were inferred.
        return items
    meta = type_meta(type_key)
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
    type_map = infer_type_map(code, line)
    bindings = _infer_constant_bindings(code, line, ctx_data)
    attr_ctx = _attribute_receiver_and_prefix(code, line, character, capture_full_token=True)
    if attr_ctx:
        receiver, attr_prefix = attr_ctx
        inferred = resolve_type_name(receiver, code, type_map)
        meta = type_meta(inferred)
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
        type_label = display_type_label(type_map[word])
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


def _function_context_at_line(code: str, line: int) -> FunctionContext | None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    target: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    best_start = -1
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = max(getattr(node, "lineno", 1) - 1, 0)
            end = max(getattr(node, "end_lineno", node.lineno) - 1, 0)
            if start <= line <= end and start >= best_start:
                target = node
                best_start = start

    if target is None:
        return None
    params: list[str] = []
    param_types: dict[str, str] = {}

    def _add_arg(arg: ast.arg) -> None:
        params.append(arg.arg)
        label = annotation_label(getattr(arg, "annotation", None))
        if label:
            param_types[arg.arg] = label

    args = target.args
    for arg in getattr(args, "posonlyargs", []):
        _add_arg(arg)
    for arg in args.args:
        _add_arg(arg)
    if args.vararg:
        _add_arg(args.vararg)
    for arg in args.kwonlyargs:
        _add_arg(arg)
    if args.kwarg:
        _add_arg(args.kwarg)

    locals_list = _function_locals_at_node(target, line + 1, params)
    return FunctionContext(params=params, param_types=param_types, locals=locals_list)


def _function_locals_at_node(
    target: ast.FunctionDef | ast.AsyncFunctionDef,
    line_limit: int,
    params: list[str],
) -> list[str]:
    seen = set(params)
    names: list[str] = []

    def _add_name(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        names.append(name)

    class LocalCollector(ast.NodeVisitor):
        def __init__(self) -> None:
            super().__init__()

        def visit(self, node: ast.AST):  # type: ignore[override]
            lineno = getattr(node, "lineno", None)
            if lineno is not None and lineno > line_limit:
                return
            return super().visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef):
            if node is target:
                for stmt in node.body:
                    self.visit(stmt)
                return
            _add_name(node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            if node is target:
                for stmt in node.body:
                    self.visit(stmt)
                return
            _add_name(node.name)

        def visit_ClassDef(self, node: ast.ClassDef):
            _add_name(node.name)

        def visit_Assign(self, node: ast.Assign):
            for name in collect_target_names(node.targets):
                _add_name(name)

        def visit_AnnAssign(self, node: ast.AnnAssign):
            for name in collect_target_names([node.target]):
                _add_name(name)

        def visit_AugAssign(self, node: ast.AugAssign):
            for name in collect_target_names([node.target]):
                _add_name(name)

        def visit_For(self, node: ast.For):
            for name in collect_target_names([node.target]):
                _add_name(name)
            self.generic_visit(node)

        def visit_AsyncFor(self, node: ast.AsyncFor):
            for name in collect_target_names([node.target]):
                _add_name(name)
            self.generic_visit(node)

        def visit_With(self, node: ast.With):
            for item in node.items:
                if item.optional_vars:
                    for name in collect_target_names([item.optional_vars]):
                        _add_name(name)
            self.generic_visit(node)

        def visit_AsyncWith(self, node: ast.AsyncWith):
            for item in node.items:
                if item.optional_vars:
                    for name in collect_target_names([item.optional_vars]):
                        _add_name(name)
            self.generic_visit(node)

        def visit_ExceptHandler(self, node: ast.ExceptHandler):
            if isinstance(getattr(node, "name", None), str):
                _add_name(node.name)
            self.generic_visit(node)

    LocalCollector().visit(target)
    return names


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

    receiver_fragment = line_text[:dot].rstrip()
    start = len(receiver_fragment)
    paren = bracket = brace = 0

    def _allowed(ch: str) -> bool:
        return ch.isalnum() or ch in {"_", ".", "]", "[", ")", "(", "'", '"'}

    for idx in range(len(receiver_fragment) - 1, -1, -1):
        ch = receiver_fragment[idx]
        if ch in ")]}":
            if ch == ")":
                paren += 1
            elif ch == "]":
                bracket += 1
            else:
                brace += 1
            start = idx
            continue
        if ch in "([{":
            if ch == "(" and paren > 0:
                paren -= 1
                start = idx
                continue
            if ch == "[" and bracket > 0:
                bracket -= 1
                start = idx
                continue
            if ch == "{" and brace > 0:
                brace -= 1
                start = idx
                continue
            break
        if paren or bracket or brace:
            start = idx
            continue
        if ch.isspace():
            break
        if not _allowed(ch):
            break
        start = idx

    receiver_src = receiver_fragment[start:].strip()
    if not receiver_src:
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
            # Neutralize later lines with trailing dots to avoid parse failures unrelated to the target line.
            changed = False
            for idx in range(len(lines)):
                if idx == line:
                    continue
                if lines[idx].rstrip().endswith("."):
                    lines[idx] = "pass"
                    changed = True
            if changed:
                candidate = "\n".join(lines)
                try:
                    ast.parse(candidate)
                except SyntaxError:
                    pass
                else:
                    return candidate
            indent = re.match(r"[ \t]*", lines[line]).group(0)
            lines[line] = indent + "pass"
    return "\n".join(lines)


def _line_text(code: str, line: int) -> str:
    lines = code.splitlines()
    if line < 0 or line >= len(lines):
        return ""
    return lines[line]


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
            for name in collect_target_names(node.targets):
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
            for name in collect_target_names([node.target]):
                bindings[name] = value

        def visit_For(self, node: ast.For):
            if limit is not None and node.lineno > limit:
                return
            loop_val = _loop_binding(node.iter)
            for name in collect_target_names([node.target]):
                bindings[name] = loop_val
            self.generic_visit(node)

        def visit_AsyncFor(self, node: ast.AsyncFor):
            if limit is not None and node.lineno > limit:
                return
            loop_val = _loop_binding(node.iter)
            for name in collect_target_names([node.target]):
                bindings[name] = loop_val
            self.generic_visit(node)

    Visitor().visit(tree)
    return bindings


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
            if not is_safe_call(base, method_name):
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
    def _short(val: Any, max_len: int = 30) -> str:
        try:
            text = repr(val)
        except Exception:
            text = type(val).__name__
        return text if len(text) <= max_len else text[: max_len - 3] + "..."

    try:
        if isinstance(value, dict):
            items = list(value.items())
            parts = [f"{_short(k)}: {_short(v)}" for k, v in items[:3]]
            suffix = ", …" if len(items) > 3 else ""
            return "{" + ", ".join(parts) + suffix + f"}} ({len(items)} items)"
        if isinstance(value, (list, tuple, set)):
            seq = list(value)
            parts = [_short(v) for v in seq[:3]]
            suffix = ", …" if len(seq) > 3 else ""
            bracket = ("[", "]") if isinstance(value, list) else ("(", ")") if isinstance(value, tuple) else ("{", "}")
            return f"{bracket[0]}" + ", ".join(parts) + suffix + f"{bracket[1]} ({len(seq)} items)"
    except Exception:
        pass
    return _short(value, max_len=120)


class _LoopVarBinding:
    def __repr__(self) -> str:
        return "<loop item>"
