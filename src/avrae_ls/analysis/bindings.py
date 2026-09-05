"""Binding identities for navigation and conservative, scope-aware refactoring."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from lsprotocol import types

from avrae_ls.analysis.source_context import build_source_context
from avrae_ls.lsp.lsp_utils import range_from_positions, shift_range


@dataclass(eq=False)
class Binding:
    name: str
    scope: Scope
    declarations: list[types.Range] = field(default_factory=list)
    reads: list[types.Range] = field(default_factory=list)


@dataclass(eq=False)
class Scope:
    parent: Scope | None = None
    bindings: dict[str, Binding] = field(default_factory=dict)

    def define(self, name: str) -> Binding:
        return self.bindings.setdefault(name, Binding(name, self))

    def resolve(self, name: str) -> Binding | None:
        return self.bindings.get(name) or (self.parent.resolve(name) if self.parent else None)


@dataclass
class BindingIndex:
    root: Scope
    occurrences: list[tuple[types.Range, Binding, Scope]]

    def at(self, position: types.Position) -> Binding | None:
        point = (position.line, position.character)
        for rng, binding, _ in self.occurrences:
            if (rng.start.line, rng.start.character) <= point < (rng.end.line, rng.end.character):
                return binding
        for rng, binding, _ in self.occurrences:
            if rng.end == position:
                return binding
        return None

    def can_rename(self, binding: Binding, new_name: str) -> bool:
        for rng, current, _ in self.occurrences:
            if current is binding and any(
                other_rng == rng and other is not binding for other_rng, other, _ in self.occurrences
            ):
                return False
        # Reject capture in either direction, including reads of an outer name.
        for _, current, scope in self.occurrences:
            if current is binding:
                other = scope.resolve(new_name)
                if other is not None and other is not binding:
                    return False
            elif current.name == new_name:
                cursor: Scope | None = scope
                while cursor is not None:
                    if cursor is binding.scope:
                        return False
                    if new_name in cursor.bindings:
                        break
                    cursor = cursor.parent
        return True


def build_binding_index(source: str, *, treat_as_module: bool = False) -> BindingIndex:
    context = build_source_context(source, treat_as_module)
    index = BindingIndex(Scope(), [])
    visitor = _Bindings(index)
    chunks = [(b.code, b.line_offset, b.char_offset) for b in context.blocks]
    if not chunks:
        chunks = [(context.prepared, 0, 0)]
    parsed: list[tuple[ast.Module, int, int]] = []
    for code, line, col in chunks:
        try:
            parsed.append((ast.parse(code), line, col))
        except SyntaxError:
            continue
    # Deferred function bodies may refer to later top-level declarations.
    for tree, _, _ in parsed:
        visitor.predeclare(tree.body)
    for tree, line, col in parsed:
        visitor.line, visitor.col = line, col
        visitor.visit(tree)
    return index


class _Bindings(ast.NodeVisitor):
    def __init__(self, index: BindingIndex):
        self.index = index
        self.scope = index.root
        self.line = self.col = 0

    def rng(self, node: ast.AST, name: str | None = None) -> types.Range:
        col = getattr(node, "col_offset", 0)
        if isinstance(node, ast.FunctionDef):
            col += 4
        return shift_range(
            range_from_positions(
                getattr(node, "lineno", 1),
                col,
                getattr(node, "lineno", 1) if name else getattr(node, "end_lineno", None),
                col + len(name) if name else getattr(node, "end_col_offset", None),
            ),
            self.line,
            self.col,
        )

    def record(self, node: ast.AST, name: str, declaration: bool = False):
        binding = self.scope.define(name) if declaration else self.scope.resolve(name)
        if not declaration and binding is not None and binding.scope is self.scope and self.scope.parent:
            if not binding.declarations:
                binding = self.scope.parent.resolve(name)
        if binding is None:
            return
        rng = self.rng(node, name)
        (binding.declarations if declaration else binding.reads).append(rng)
        self.index.occurrences.append((rng, binding, self.scope))

    def predeclare(self, nodes):
        outer = self

        class Declarations(ast.NodeVisitor):
            def visit_Name(self, node: ast.Name):
                if isinstance(node.ctx, ast.Store):
                    outer.scope.define(node.id)

            def visit_FunctionDef(self, node: ast.FunctionDef):
                outer.scope.define(node.name)

            def visit_Lambda(self, node: ast.Lambda):
                pass

            def visit_ListComp(self, node: ast.AST):
                pass

            visit_SetComp = visit_ListComp
            visit_DictComp = visit_ListComp
            visit_GeneratorExp = visit_ListComp

            def visit_Call(self, node: ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "using":
                    for kw in node.keywords:
                        if kw.arg:
                            outer.scope.define(kw.arg)
                self.generic_visit(node)

        collector = Declarations()
        for node in nodes:
            collector.visit(node)

    def visit_Name(self, node: ast.Name):
        self.record(node, node.id, isinstance(node.ctx, ast.Store))

    def visit_AugAssign(self, node: ast.AugAssign):
        if isinstance(node.target, ast.Name):
            self.record(node.target, node.target.id)
        self.visit(node.value)
        self.visit(node.target)

    def visit_arg(self, node: ast.arg):
        self.record(node, node.arg, True)

    def function(self, args: ast.arguments, body: list[ast.AST]):
        for default in [*args.defaults, *args.kw_defaults]:
            if default is not None:
                self.visit(default)
        parent = self.scope
        self.scope = Scope(parent)
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is not None:
                self.visit(arg)
        self.predeclare(body)
        for statement in body:
            self.visit(statement)
        self.scope = parent

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.record(node, node.name, True)
        self.function(node.args, list(node.body))

    def visit_Lambda(self, node: ast.Lambda):
        self.function(node.args, [node.body])

    def comprehension(self, node):
        parent = self.scope
        self.scope = Scope(parent)
        for gen in node.generators:
            self.visit(gen.iter)
            self.visit(gen.target)
            for condition in gen.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.scope = parent

    visit_ListComp = comprehension
    visit_SetComp = comprehension
    visit_DictComp = comprehension
    visit_GeneratorExp = comprehension

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "using":
            for kw in node.keywords:
                if kw.arg:
                    self.record(kw, kw.arg, True)
        self.generic_visit(node)
