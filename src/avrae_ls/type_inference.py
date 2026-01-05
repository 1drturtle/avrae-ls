from __future__ import annotations

import ast
import re
from typing import Dict, Iterable, Optional

from .type_system import resolve_type_key, type_meta


def annotation_label(node: ast.AST | None) -> str:
    base, elem = _annotation_types(node)
    if not base:
        return ""
    if elem:
        return f"{base}[{elem}]"
    return base


def infer_type_map(code: str, line: int | None = None) -> Dict[str, str]:
    try:
        tree = ast.parse(code, type_comments=True)
    except SyntaxError:
        return {}
    visitor = _TypeInferencer(code)
    visitor.visit(tree)
    return visitor.export_types(line)


def resolve_type_name(receiver: str, code: str, type_map: Dict[str, str] | None = None) -> str:
    mapping = type_map or infer_type_map(code)
    get_match = _DICT_GET_RE.match(receiver)
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
        base_type = resolve_type_name(base_expr, code, mapping)
        if base_type:
            base_meta = type_meta(base_type)
            if base_meta.element_type:
                return base_meta.element_type
            return base_type
    receiver = receiver.rstrip("()")
    if "." in receiver:
        base_expr, attr_name = receiver.rsplit(".", 1)
        base_type = resolve_type_name(base_expr, code, mapping)
        if base_type:
            meta = type_meta(base_type)
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
    resolved_receiver = resolve_type_key(receiver)
    if resolved_receiver:
        return resolved_receiver
    tail = receiver.split(".")[-1].split("[", 1)[0]
    resolved_tail = resolve_type_key(tail)
    if resolved_tail:
        return resolved_tail
    return receiver


_DICT_GET_RE = re.compile(r"^([A-Za-z_]\w*)\.get\(\s*(['\"])(.+?)\2")


def _split_annotation_string(text: str) -> tuple[Optional[str], Optional[str]]:
    stripped = text.strip().strip("'\"")
    if not stripped:
        return None, None
    match = re.match(
        r"^([A-Za-z_][\w]*)\s*(?:\[\s*([A-Za-z_][\w]*)(?:\s*,\s*([A-Za-z_][\w]*))?\s*\])?$",
        stripped,
    )
    if not match:
        return stripped, None
    base = match.group(1)
    elem = match.group(3) or match.group(2)
    base_norm = base.lower() if base.lower() in {"list", "dict", "set", "tuple"} else base
    return base_norm, elem


def _annotation_types(node: ast.AST | None) -> tuple[Optional[str], Optional[str]]:
    if node is None:
        return None, None
    if isinstance(node, str):
        return _split_annotation_string(node)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _split_annotation_string(node.value)
    if isinstance(node, ast.Str):
        return _split_annotation_string(node.s)
    if isinstance(node, ast.Name):
        return node.id, None
    if isinstance(node, ast.Attribute):
        return node.attr, None
    try:
        text = ast.unparse(node)
    except Exception:
        text = ""
    if text:
        return _split_annotation_string(text)
    return None, None


class _TypeInferencer(ast.NodeVisitor):
    def __init__(self, code: str) -> None:
        self.code = code
        self._scopes: list[dict[str, str]] = [dict()]
        self._scoped_maps: list[tuple[tuple[int, int], dict[str, str]]] = []

    def _current_scope(self) -> dict[str, str]:
        return self._scopes[-1]

    def _push_scope(self) -> None:
        self._scopes.append(dict())

    def _pop_scope(self) -> None:
        self._scopes.pop()

    def export_types(self, line: int | None = None) -> dict[str, str]:
        if line is not None:
            for (start, end), scope in reversed(self._scoped_maps):
                if start <= line <= end:
                    combined = dict(self._scopes[0])
                    combined.update(scope)
                    return combined
        return dict(self._scopes[0])

    def _set_type(self, key: str, value: Optional[str]) -> None:
        if value:
            self._current_scope()[key] = value

    def _get_type(self, key: str) -> Optional[str]:
        for scope in reversed(self._scopes):
            if key in scope:
                return scope[key]
        return None

    def visit_Assign(self, node: ast.Assign):
        val_type, elem_type = self._value_type(node.value)
        if getattr(node, "type_comment", None):
            ann_type, ann_elem = _annotation_types(node.type_comment)
            val_type = ann_type or val_type
            elem_type = ann_elem or elem_type
        for target in node.targets:
            self._bind_target(target, val_type, elem_type, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        val_type, elem_type = self._value_type(node.value) if node.value else (None, None)
        ann_type, ann_elem = _annotation_types(getattr(node, "annotation", None))
        val_type = val_type or ann_type
        elem_type = elem_type or ann_elem
        if getattr(node, "type_comment", None):
            c_type, c_elem = _annotation_types(node.type_comment)
            val_type = val_type or c_type
            elem_type = elem_type or c_elem
        self._bind_target(node.target, val_type, elem_type, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        val_type, elem_type = self._value_type(node.value)
        self._bind_target(
            node.target,
            val_type or self._existing_type(node.target),
            elem_type or self._existing_element(node.target),
            None,
        )
        self.generic_visit(node)

    def visit_For(self, node: ast.For):
        _, elem_type = self._value_type(node.iter)
        if not elem_type and isinstance(node.iter, ast.Name):
            elem_type = self._get_type(f"{node.iter.id}.__element__")
        self._bind_target(node.target, elem_type, None, None)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor):
        _, elem_type = self._value_type(node.iter)
        if not elem_type and isinstance(node.iter, ast.Name):
            elem_type = self._get_type(f"{node.iter.id}.__element__")
        self._bind_target(node.target, elem_type, None, None)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._push_scope()
        try:
            self._bind_function_args(node.args)
            for stmt in node.body:
                self.visit(stmt)
            self._record_scope(node)
        finally:
            self._pop_scope()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._push_scope()
        try:
            self._bind_function_args(node.args)
            for stmt in node.body:
                self.visit(stmt)
            self._record_scope(node)
        finally:
            self._pop_scope()

    def visit_ClassDef(self, node: ast.ClassDef):
        self._push_scope()
        try:
            for stmt in node.body:
                self.visit(stmt)
            self._record_scope(node)
        finally:
            self._pop_scope()

    def visit_If(self, node: ast.If):
        self.visit(node.test)
        base_map = self._current_scope().copy()
        body_map = self._visit_block(node.body, base_map.copy())
        orelse_seed = base_map.copy()
        orelse_map = self._visit_block(node.orelse, orelse_seed) if node.orelse else orelse_seed
        self._current_scope().update(self._merge_branch_types(base_map, body_map, orelse_map))

    def _visit_block(self, nodes: Iterable[ast.stmt], seed: dict[str, str]) -> dict[str, str]:
        walker = _TypeInferencer(self.code)
        walker._scopes = [seed.copy()]
        for stmt in nodes:
            walker.visit(stmt)
        self._scoped_maps.extend(walker._scoped_maps)
        return walker._current_scope()

    def _merge_branch_types(self, base: dict[str, str], left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
        merged = base.copy()
        for key in set(left) | set(right):
            l_val = left.get(key)
            r_val = right.get(key)
            if l_val and r_val and l_val == r_val:
                merged[key] = l_val
            elif key in base:
                merged[key] = base[key]
            elif l_val and not r_val:
                merged[key] = l_val
            elif r_val and not l_val:
                merged[key] = r_val
            elif key in merged:
                merged.pop(key, None)
        return merged

    def _bind_target(self, target: ast.AST, val_type: Optional[str], elem_type: Optional[str], source: ast.AST | None):
        if isinstance(target, ast.Name):
            self._set_type(target.id, val_type)
            self._set_type(f"{target.id}.__element__", elem_type)
            if source is not None:
                self._record_dict_key_types(target.id, source)
        elif isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(source, (ast.Tuple, ast.List)) and len(source.elts or []) == len(target.elts):
                for elt, val_node in zip(target.elts, source.elts):
                    elt_type, elt_elem = self._value_type(val_node)
                    self._bind_target(elt, elt_type, elt_elem, val_node)
            else:
                for elt in target.elts:
                    self._bind_target(elt, val_type, elem_type, source)

    def _bind_function_args(self, args: ast.arguments) -> None:
        for arg in getattr(args, "posonlyargs", []):
            self._bind_arg_annotation(arg)
        for arg in args.args:
            self._bind_arg_annotation(arg)
        if args.vararg:
            self._bind_arg_annotation(args.vararg)
        for arg in args.kwonlyargs:
            self._bind_arg_annotation(arg)
        if args.kwarg:
            self._bind_arg_annotation(args.kwarg)

    def _bind_arg_annotation(self, arg: ast.arg) -> None:
        ann_type, elem_type = _annotation_types(getattr(arg, "annotation", None))
        if ann_type:
            self._set_type(arg.arg, ann_type)
        if elem_type:
            self._set_type(f"{arg.arg}.__element__", elem_type)

    def _existing_type(self, target: ast.AST) -> Optional[str]:
        if isinstance(target, ast.Name):
            return self._get_type(target.id)
        return None

    def _existing_element(self, target: ast.AST) -> Optional[str]:
        if isinstance(target, ast.Name):
            return self._get_type(f"{target.id}.__element__")
        return None

    def _value_type(self, value: ast.AST | None) -> tuple[Optional[str], Optional[str]]:
        if isinstance(value, ast.Call):
            if isinstance(value.func, ast.Name):
                if value.func.id in {"character", "combat"}:
                    return value.func.id, None
                if value.func.id == "vroll":
                    return "SimpleRollResult", None
                if value.func.id == "argparse":
                    return "ParsedArguments", None
                if value.func.id == "range":
                    return "range", "int"
                if value.func.id in {"list", "dict", "str", "int", "float"}:
                    return value.func.id, None
            if isinstance(value.func, ast.Attribute):
                base_type, base_elem = self._value_type(value.func.value)
                if value.func.attr == "get" and value.args:
                    key_literal = self._literal_key(value.args[0])
                    val_type, elem_type = self._subscript_type(value.func.value, key_literal, base_type, base_elem)
                    if val_type:
                        return val_type, elem_type
                    if base_elem:
                        return base_elem, None
        if isinstance(value, ast.Compare):
            return "bool", None
        if isinstance(value, ast.List):
            elem_type, _ = self._iterable_element_from_values(value.elts)
            return "list", elem_type
        if isinstance(value, ast.Tuple):
            elem_type, _ = self._iterable_element_from_values(getattr(value, "elts", []))
            return "tuple", elem_type
        if isinstance(value, ast.Set):
            elem_type, _ = self._iterable_element_from_values(getattr(value, "elts", []))
            return "set", elem_type
        if isinstance(value, ast.ListComp):
            comp_type, comp_elem = self._value_type(value.elt)
            return "list", comp_type or comp_elem
        if isinstance(value, ast.Dict):
            elem_type, _ = self._iterable_element_from_values(value.values or [])
            return "dict", elem_type
        if isinstance(value, ast.Subscript):
            return self._subscript_value_type(value)
        if isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                return "str", None
        if isinstance(value, ast.Name):
            existing = self._get_type(value.id)
            if existing:
                return existing, self._get_type(f"{value.id}.__element__")
            if value.id in {"character", "combat", "ctx"}:
                return value.id, None
        if isinstance(value, ast.Attribute):
            attr_name = value.attr
            base_type = None
            base_elem = None
            if isinstance(value.value, ast.Name):
                base_type = self._get_type(value.value.id)
                base_elem = self._get_type(f"{value.value.id}.__element__")
            if base_type is None:
                base_type, base_elem = self._value_type(value.value)
            if base_type:
                meta = type_meta(base_type)
                attr_meta = meta.attrs.get(attr_name)
                if attr_meta:
                    if attr_meta.type_name:
                        return attr_meta.type_name, attr_meta.element_type or None
                    if attr_meta.element_type:
                        return base_type, attr_meta.element_type
                if base_elem:
                    return base_elem, None
                resolved_attr_type = resolve_type_key(attr_name, base_type)
                if resolved_attr_type:
                    return resolved_attr_type, None
            return None, None
        if isinstance(value, ast.IfExp):
            t_type, t_elem = self._value_type(value.body)
            e_type, e_elem = self._value_type(value.orelse)
            if t_type and e_type and t_type == e_type:
                merged_elem = t_elem or e_elem
                if t_elem and e_elem and t_elem != e_elem:
                    merged_elem = None
                return t_type, merged_elem
            return t_type or e_type, t_elem or e_elem
        return None, None

    def _iterable_element_from_values(self, values: Iterable[ast.AST]) -> tuple[Optional[str], Optional[str]]:
        elem_type: Optional[str] = None
        nested_elem: Optional[str] = None
        for node in values:
            val_type, inner_elem = self._value_type(node)
            if not val_type:
                return None, None
            if elem_type is None:
                elem_type = val_type
                nested_elem = inner_elem
            elif elem_type != val_type:
                return None, None
            if inner_elem:
                if nested_elem is None:
                    nested_elem = inner_elem
                elif nested_elem != inner_elem:
                    nested_elem = None
        return elem_type, nested_elem

    def _literal_key(self, node: ast.AST | None) -> str | int | None:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (str, int)):
                return node.value
        if hasattr(ast, "Index") and isinstance(node, getattr(ast, "Index")):
            return self._literal_key(getattr(node, "value", None))
        return None

    def _subscript_type(
        self,
        base_expr: ast.AST,
        key_literal: str | int | None,
        base_type: Optional[str],
        base_elem: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        base_name = base_expr.id if isinstance(base_expr, ast.Name) else None
        if base_name and key_literal is not None:
            dict_key = f"{base_name}.{key_literal}"
            dict_type = self._get_type(dict_key)
            if dict_type:
                return dict_type, self._get_type(f"{dict_key}.__element__")
        elem_hint = base_elem
        if base_name and not elem_hint:
            elem_hint = self._get_type(f"{base_name}.__element__")
        if base_type:
            meta = type_meta(base_type)
            if key_literal is not None and key_literal in meta.attrs:
                attr_meta = meta.attrs[key_literal]
                if attr_meta.type_name:
                    return attr_meta.type_name, attr_meta.element_type or None
                if attr_meta.element_type:
                    return base_type, attr_meta.element_type
            elem_hint = elem_hint or meta.element_type
        if elem_hint:
            return elem_hint, None
        return base_type, None

    def _subscript_value_type(self, node: ast.Subscript) -> tuple[Optional[str], Optional[str]]:
        base_type, base_elem = self._value_type(node.value)
        key_literal = self._literal_key(getattr(node, "slice", None))
        return self._subscript_type(node.value, key_literal, base_type, base_elem)

    def _record_dict_key_types(self, var_name: str, value: ast.AST | None) -> None:
        if not isinstance(value, ast.Dict):
            return
        for key_node, val_node in zip(value.keys or [], value.values or []):
            key_literal = self._literal_key(key_node)
            if key_literal is None:
                continue
            val_type, elem_type = self._value_type(val_node)
            if val_type:
                self._set_type(f"{var_name}.{key_literal}", val_type)
            if elem_type:
                self._set_type(f"{var_name}.{key_literal}.__element__", elem_type)

    def _record_scope(self, node: ast.AST) -> None:
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", start)
        if start is None or end is None:
            return
        start = max(start - 1, 0)
        end = max(end - 1, start)
        self._scoped_maps.append(((start, end), self._current_scope().copy()))
