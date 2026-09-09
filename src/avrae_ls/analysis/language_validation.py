"""Validate Draconic syntax without executing user code."""

from __future__ import annotations

import ast
from collections.abc import Sequence

import draconic


def unsupported_syntax(body: Sequence[ast.AST]) -> list[tuple[ast.AST, str]]:
    interpreter = draconic.DraconicInterpreter()
    problems: list[tuple[ast.AST, str]] = []

    class Validator(ast.NodeVisitor):
        loops = 0

        def visit(self, node):
            if isinstance(node, (ast.stmt, ast.expr)) and type(node) not in interpreter.nodes:
                # Imports have their own diagnostic and quick fix.
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    problems.append((node, f"{type(node).__name__} is not supported by Draconic"))
                return
            if isinstance(node, ast.pattern) and type(node) not in interpreter.patma_nodes:
                problems.append((node, f"{type(node).__name__} is not supported by Draconic"))
                return
            super().visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef):
            for decorator in node.decorator_list:
                problems.append((decorator, "Function decorators are not supported by Draconic"))
            previous = self.loops
            self.loops = 0
            # Type annotations are not evaluated by Draconic functions.
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if default is not None:
                    self.visit(default)
            for statement in node.body:
                self.visit(statement)
            self.loops = previous

        def visit_For(self, node: ast.For):
            self.visit(node.iter)
            self.target(node.target)
            self.loop_body(node.body, node.orelse)

        def visit_While(self, node: ast.While):
            self.visit(node.test)
            self.loop_body(node.body, node.orelse)

        def loop_body(self, body, orelse):
            self.loops += 1
            for statement in body:
                self.visit(statement)
            self.loops -= 1
            for statement in orelse:
                self.visit(statement)

        def visit_Break(self, node: ast.Break):
            if not self.loops:
                problems.append((node, "Loop control outside loop"))

        def visit_Continue(self, node: ast.Continue):
            if not self.loops:
                problems.append((node, "Loop control outside loop"))

        def target(self, node):
            if type(node) not in interpreter.assign_nodes:
                problems.append((node, "This assignment target is not supported by Draconic"))
            elif isinstance(node, (ast.Tuple, ast.List)):
                for item in node.elts:
                    self.target(item)
            elif isinstance(node, ast.Starred):
                self.target(node.value)
            elif isinstance(node, ast.Subscript):
                self.visit(node.value)
                self.visit(node.slice)

        def visit_Assign(self, node: ast.Assign):
            self.visit(node.value)
            for target in node.targets:
                self.target(target)

        def visit_AugAssign(self, node: ast.AugAssign):
            self.target(node.target)
            self.visit(node.value)

        def visit_ExceptHandler(self, node: ast.ExceptHandler):
            exception = node.type
            literals = exception.elts if isinstance(exception, ast.Tuple) else [exception]
            if exception is not None and not all(
                isinstance(item, ast.Constant) and isinstance(item.value, str) for item in literals
            ):
                problems.append((node, "Draconic except clauses require string literals or a tuple of string literals"))
            if node.name is not None:
                problems.append((node, "'except ... as name' is not supported by Draconic"))
            for statement in node.body:
                self.visit(statement)

        def visit_comprehension(self, node: ast.comprehension):
            if node.is_async:
                problems.append((node.iter, "Async comprehensions are not supported by Draconic"))
            self.target(node.target)
            self.visit(node.iter)
            for condition in node.ifs:
                self.visit(condition)

    validator = Validator()
    for node in body:
        validator.visit(node)
    return problems


def literal_truth(node: ast.AST) -> bool | None:
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return bool(node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.keys)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = literal_truth(node.operand)
        return not value if value is not None else None
    return None


def nonempty_iterable(node: ast.AST) -> bool | None:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return literal_truth(node)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range":
        if (
            1 <= len(node.args) <= 3
            and not node.keywords
            and all(isinstance(arg, ast.Constant) and type(arg.value) is int for arg in node.args)
        ):
            values = [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
            try:
                return bool(range(*values))
            except (ValueError, OverflowError):
                pass
    return None


def terminates(statements: Sequence[ast.AST]) -> bool:
    for node in statements:
        if isinstance(node, (ast.Return, ast.Break, ast.Continue, ast.Raise)):
            return True
        if isinstance(node, ast.If) and terminates(node.body) and terminates(node.orelse):
            return True
    return False
