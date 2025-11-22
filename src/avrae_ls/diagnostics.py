from __future__ import annotations

import ast
import logging
import inspect
from typing import Iterable, List, Sequence, Set

import draconic
from lsprotocol import types

from .argument_parsing import apply_argument_parsing
from .config import DiagnosticSettings
from .context import ContextData, GVarResolver
from .parser import find_draconic_blocks
from .runtime import MockExecutor, _default_builtins

log = logging.getLogger(__name__)

SEVERITY = {
    "error": types.DiagnosticSeverity.Error,
    "warning": types.DiagnosticSeverity.Warning,
    "info": types.DiagnosticSeverity.Information,
}


class DiagnosticProvider:
    def __init__(self, executor: MockExecutor, settings: DiagnosticSettings):
        self._executor = executor
        self._settings = settings
        self._builtin_signatures = _build_builtin_signatures()

    async def analyze(
        self,
        source: str,
        ctx_data: ContextData,
        gvar_resolver: GVarResolver,
    ) -> List[types.Diagnostic]:
        diagnostics: list[types.Diagnostic] = []

        source = apply_argument_parsing(source)
        blocks = find_draconic_blocks(source)
        if not blocks:
            diagnostics.extend(await self._analyze_code(source, ctx_data, gvar_resolver))
            return diagnostics

        for block in blocks:
            block_diags = await self._analyze_code(block.code, ctx_data, gvar_resolver)
            diagnostics.extend(_shift_diagnostics(block_diags, block.line_offset, block.char_offset))
        return diagnostics

    async def _analyze_code(
        self,
        code: str,
        ctx_data: ContextData,
        gvar_resolver: GVarResolver,
    ) -> List[types.Diagnostic]:
        diagnostics: list[types.Diagnostic] = []
        parser = draconic.DraconicInterpreter()
        line_shift = 0
        try:
            body = parser.parse(code)
        except draconic.DraconicSyntaxError as exc:
            wrapped, added = _wrap_draconic(code)
            try:
                body = parser.parse(wrapped)
                line_shift = -added
            except draconic.DraconicSyntaxError:
                diagnostics.append(_syntax_diagnostic(exc))
                return diagnostics
        except SyntaxError as exc:
            diagnostics.append(_syntax_from_std(exc))
            return diagnostics

        diagnostics.extend(
            self._check_unknown_names(body, ctx_data, self._settings.semantic_level)
        )
        diagnostics.extend(await _check_gvars(body, gvar_resolver, self._settings))
        diagnostics.extend(_check_imports(body, self._settings.semantic_level))
        diagnostics.extend(_check_call_args(body, self._builtin_signatures, self._settings.semantic_level))
        diagnostics.extend(_check_private_method_calls(body))
        if line_shift:
            diagnostics = _shift_diagnostics(diagnostics, line_shift, 0)
        return diagnostics

    def _check_unknown_names(
        self,
        body: Sequence[ast.AST],
        ctx_data: ContextData,
        severity_level: str,
    ) -> List[types.Diagnostic]:
        known: Set[str] = set(self._executor.available_names(ctx_data))
        diagnostics: list[types.Diagnostic] = []

        class Walker(ast.NodeVisitor):
            def __init__(self, tracker: Set[str]):
                self.tracker = tracker

            def visit_Assign(self, node: ast.Assign):
                self.visit(node.value)
                for target in node.targets:
                    self.tracker.update(_names_in_target(target))

            def visit_AnnAssign(self, node: ast.AnnAssign):
                if node.value:
                    self.visit(node.value)
                self.tracker.update(_names_in_target(node.target))

            def visit_AugAssign(self, node: ast.AugAssign):
                self.visit(node.value)
                self.tracker.update(_names_in_target(node.target))

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self.tracker.add(node.name)
                for arg in node.args.args:
                    self.tracker.add(arg.arg)
                for stmt in node.body:
                    self.visit(stmt)

            def visit_ClassDef(self, node: ast.ClassDef):
                self.tracker.add(node.name)
                for stmt in node.body:
                    self.visit(stmt)

            def visit_For(self, node: ast.For):
                # Loop targets become defined for the loop body and after the loop
                self.tracker.update(_names_in_target(node.target))
                self.visit(node.iter)
                for stmt in node.body:
                    self.visit(stmt)
                for stmt in node.orelse:
                    self.visit(stmt)

            def visit_AsyncFor(self, node: ast.AsyncFor):
                # Async loop targets follow the same scoping rules as regular loops
                self.tracker.update(_names_in_target(node.target))
                self.visit(node.iter)
                for stmt in node.body:
                    self.visit(stmt)
                for stmt in node.orelse:
                    self.visit(stmt)

            def visit_Name(self, node: ast.Name):
                if isinstance(node.ctx, ast.Load) and node.id not in self.tracker:
                    diagnostics.append(
                        _make_diagnostic(
                            node,
                            f"'{node.id}' may be undefined in this scope",
                            severity_level,
                        )
                    )

        walker = Walker(known)
        for stmt in body:
            walker.visit(stmt)
        return diagnostics


def _syntax_diagnostic(exc: draconic.DraconicSyntaxError) -> types.Diagnostic:
    rng = _range_from_positions(
        exc.lineno,
        exc.offset,
        exc.end_lineno,
        exc.end_offset,
    )
    return types.Diagnostic(
        message=exc.msg,
        range=rng,
        severity=types.DiagnosticSeverity.Error,
        source="avrae-ls",
    )


def _syntax_from_std(exc: SyntaxError) -> types.Diagnostic:
    lineno, offset = exc.lineno, exc.offset
    rng = _range_from_positions(lineno, offset, getattr(exc, "end_lineno", None), getattr(exc, "end_offset", None))
    return types.Diagnostic(
        message=exc.msg,
        range=rng,
        severity=types.DiagnosticSeverity.Error,
        source="avrae-ls",
    )


def _names_in_target(target: ast.AST) -> Set[str]:
    names: set[str] = set()
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, ast.Tuple):
        for elt in target.elts:
            names.update(_names_in_target(elt))
    elif isinstance(target, ast.List):
        for elt in target.elts:
            names.update(_names_in_target(elt))
    return names


async def _check_gvars(
    body: Sequence[ast.AST],
    resolver: GVarResolver,
    settings: DiagnosticSettings,
) -> List[types.Diagnostic]:
    diagnostics: list[types.Diagnostic] = []
    for node in _iter_calls(body):
        if not isinstance(node.func, ast.Name) or node.func.id != "get_gvar":
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, (ast.Str, ast.Constant)) and isinstance(getattr(arg, "s", None) or arg.value, str):
            gvar_id = arg.s if isinstance(arg, ast.Str) else arg.value
            found_local = resolver.get_local(gvar_id)
            ensured = found_local is not None or await resolver.ensure(gvar_id)
            if not ensured:
                diagnostics.append(
                    _make_diagnostic(
                        arg,
                        f"Unknown gvar '{gvar_id}'",
                        settings.semantic_level,
                    )
                )
    return diagnostics


def _iter_calls(body: Sequence[ast.AST]) -> Iterable[ast.Call]:
    class Finder(ast.NodeVisitor):
        def __init__(self):
            self.calls: list[ast.Call] = []

        def visit_Call(self, node: ast.Call):
            self.calls.append(node)
            self.generic_visit(node)

    finder = Finder()
    for stmt in body:
        finder.visit(stmt)
    return finder.calls


def _check_private_method_calls(body: Sequence[ast.AST]) -> List[types.Diagnostic]:
    diagnostics: list[types.Diagnostic] = []

    class Finder(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr.startswith("_"):
                diagnostics.append(
                    _make_diagnostic(
                        func,
                        "Calling private methods (starting with '_') is not allowed",
                        "error",
                    )
                )
            self.generic_visit(node)

    finder = Finder()
    for stmt in body:
        finder.visit(stmt)
    return diagnostics


def _make_diagnostic(node: ast.AST, message: str, level: str) -> types.Diagnostic:
    severity = SEVERITY.get(level, types.DiagnosticSeverity.Warning)
    if hasattr(node, "lineno"):
        rng = _range_from_positions(
            getattr(node, "lineno", 1),
            getattr(node, "col_offset", 0) + 1,
            getattr(node, "end_lineno", None),
            getattr(node, "end_col_offset", None),
        )
    else:
        rng = types.Range(
            start=types.Position(line=0, character=0),
            end=types.Position(line=0, character=1),
    )
    return types.Diagnostic(
        message=message,
        range=rng,
        severity=severity,
        source="avrae-ls",
    )


def _shift_diagnostics(diags: List[types.Diagnostic], line_offset: int, char_offset: int) -> List[types.Diagnostic]:
    shifted: list[types.Diagnostic] = []
    for diag in diags:
        shifted.append(
            types.Diagnostic(
                message=diag.message,
                range=_shift_range(diag.range, line_offset, char_offset),
                severity=diag.severity,
                source=diag.source,
                code=diag.code,
                code_description=diag.code_description,
                tags=diag.tags,
                related_information=diag.related_information,
                data=diag.data,
            )
        )
    return shifted


def _shift_range(rng: types.Range, line_offset: int, char_offset: int) -> types.Range:
    def _shift_pos(pos: types.Position) -> types.Position:
        return types.Position(
            line=max(pos.line + line_offset, 0),
            character=max(pos.character + (char_offset if pos.line == 0 else 0), 0),
        )

    return types.Range(start=_shift_pos(rng.start), end=_shift_pos(rng.end))


def _wrap_draconic(code: str) -> tuple[str, int]:
    indented = "\n".join(f"    {line}" for line in code.splitlines())
    wrapped = f"def __alias_main__():\n{indented}\n__alias_main__()"
    return wrapped, 1


def _build_builtin_signatures() -> dict[str, inspect.Signature]:
    sigs: dict[str, inspect.Signature] = {}
    builtins = _default_builtins()

    def try_add(name: str, obj):
        try:
            sigs[name] = inspect.signature(obj)
        except (TypeError, ValueError):
            pass

    for name, obj in builtins.items():
        try_add(name, obj)

    # runtime helpers we expose
    def get_gvar(key): ...
    def get_svar(name, default=None): ...
    def get_cvar(name, default=None): ...
    def get_uvar(name, default=None): ...
    def get_uvars(): ...
    def set_uvar(name, value): ...
    def set_uvar_nx(name, value): ...
    def delete_uvar(name): ...
    def uvar_exists(name): ...
    def exists(name): ...
    def get(name, default=None): ...
    def using(**imports): ...
    def signature(data=0): ...
    def verify_signature(sig=None): ...
    def print_fn(*args, sep=" ", end="\n"): ...

    helpers = {
        "get_gvar": get_gvar,
        "get_svar": get_svar,
        "get_cvar": get_cvar,
        "get_uvar": get_uvar,
        "get_uvars": get_uvars,
        "set_uvar": set_uvar,
        "set_uvar_nx": set_uvar_nx,
        "delete_uvar": delete_uvar,
        "uvar_exists": uvar_exists,
        "exists": exists,
        "get": get,
        "using": using,
        "signature": signature,
        "verify_signature": verify_signature,
        "print": print_fn,
    }
    for name, obj in helpers.items():
        try_add(name, obj)
    return sigs


def _check_call_args(
    body: Sequence[ast.AST],
    signatures: dict[str, inspect.Signature],
    severity_level: str,
) -> List[types.Diagnostic]:
    diagnostics: list[types.Diagnostic] = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call):
            if isinstance(node.func, ast.Name):
                fn = node.func.id
                if fn in signatures:
                    sig = signatures[fn]
                    if not _call_args_match(sig, node):
                        diagnostics.append(
                            _make_diagnostic(
                                node.func,
                                f"Call to '{fn}' may have invalid arguments",
                                severity_level,
                            )
                        )
            self.generic_visit(node)

    visitor = Visitor()
    for stmt in body:
        visitor.visit(stmt)
    return diagnostics


def _call_args_match(sig: inspect.Signature, call: ast.Call) -> bool:
    params = list(sig.parameters.values())
    required = [
        p
        for p in params
        if p.default is inspect._empty
        and p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    max_args = None
    if any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params):
        max_args = None
    else:
        max_args = len(
            [
                p
                for p in params
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
        )

    arg_count = len(call.args)
    if arg_count < len(required):
        return False
    if max_args is not None and arg_count > max_args:
        return False
    return True


def _check_imports(body: Sequence[ast.AST], severity_level: str) -> List[types.Diagnostic]:
    diagnostics: list[types.Diagnostic] = []

    class Visitor(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import):
            diagnostics.append(_make_diagnostic(node, "Imports are not supported in draconic aliases", severity_level))

        def visit_ImportFrom(self, node: ast.ImportFrom):
            diagnostics.append(_make_diagnostic(node, "Imports are not supported in draconic aliases", severity_level))

    visitor = Visitor()
    for stmt in body:
        visitor.visit(stmt)
    return diagnostics


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
