from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from avrae_ls.config import VarSources
from avrae_ls.gvar_utils import sanitize_gvar_binding
from avrae_ls.runtime.context import ContextBuilder, ContextData
from avrae_ls.runtime.runtime import MockExecutor, ModuleExecutionError
from avrae_ls.testing._common import deep_merge_dicts, parse_expected_value, parse_metadata_mapping, value_matches

log = logging.getLogger(__name__)


class GVarTestError(Exception):
    """Raised when a gvar test cannot be parsed or executed."""


@dataclass
class GVarTestCase:
    path: Path
    gvar_path: Path
    gvar_name: str
    binding_name: str
    name: str | None
    body: str
    expected_raw: str
    expected: Any
    var_overrides: dict[str, Any] | None = None
    character_overrides: dict[str, Any] | None = None

    @property
    def target_kind(self) -> str:
        return "gvar"

    @property
    def target_name(self) -> str:
        return self.gvar_name


@dataclass
class GVarTestResult:
    case: GVarTestCase
    passed: bool
    actual: Any
    stdout: str
    error: str | None = None
    details: str | None = None
    error_line: int | None = None
    error_col: int | None = None


def parse_gvar_tests(path: Path) -> list[GVarTestCase]:
    try:
        text = path.read_text()
    except OSError as exc:  # pragma: no cover - filesystem edge
        raise GVarTestError(f"Failed to read {path}: {exc}") from exc

    lines = text.splitlines()
    idx = 0
    cases: list[GVarTestCase] = []
    while idx < len(lines):
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if idx >= len(lines):
            break

        body_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            body_lines.append(lines[idx])
            idx += 1
        if not body_lines:
            raise GVarTestError(f"{path} has no test body before '---'")
        if idx >= len(lines):
            raise GVarTestError(f"{path} is missing a '---' separator")
        idx += 1

        expected_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            expected_lines.append(lines[idx])
            idx += 1

        meta_lines: list[str] = []
        if idx < len(lines) and lines[idx].strip() == "---":
            idx += 1
            while idx < len(lines) and lines[idx].strip():
                meta_lines.append(lines[idx])
                idx += 1

        body = "\n".join(body_lines).strip()
        expected_raw = "\n".join(expected_lines)
        meta_raw = "\n".join(meta_lines)
        expected = parse_expected_value(expected_raw)
        try:
            meta = parse_metadata_mapping(meta_raw, str(path))
        except ValueError as exc:
            raise GVarTestError(str(exc)) from exc

        gvar_path = _resolve_gvar_path(path)
        gvar_name = gvar_path.stem
        binding_name = sanitize_gvar_binding(gvar_name)
        name = meta.get("name") if isinstance(meta, dict) else None
        var_overrides = meta.get("vars") if isinstance(meta, dict) else None
        character_overrides = meta.get("character") if isinstance(meta, dict) else None

        cases.append(
            GVarTestCase(
                path=path,
                gvar_path=gvar_path,
                gvar_name=gvar_name,
                binding_name=binding_name,
                name=name,
                body=body,
                expected_raw=expected_raw,
                expected=expected,
                var_overrides=var_overrides if isinstance(var_overrides, dict) else None,
                character_overrides=character_overrides if isinstance(character_overrides, dict) else None,
            )
        )
    return cases


async def run_gvar_tests(
    cases: Iterable[GVarTestCase], builder: ContextBuilder, executor: MockExecutor
) -> list[GVarTestResult]:
    case_list = list(cases)
    gvar_sources: dict[Path, str] = {}
    gvar_errors: dict[Path, str] = {}

    source_started = time.perf_counter()
    for gvar_path in {case.gvar_path for case in case_list}:
        try:
            gvar_sources[gvar_path] = gvar_path.read_text()
        except OSError as exc:
            gvar_errors[gvar_path] = f"Failed to read gvar file {gvar_path}: {exc}"
    log_elapsed = (time.perf_counter() - source_started) * 1000
    if gvar_sources:
        log.debug("Loaded %d gvar source file(s) for tests in %.2fms", len(gvar_sources), log_elapsed)

    baseline = builder.build_baseline()
    results: list[GVarTestResult] = []
    for case in case_list:
        error = gvar_errors.get(case.gvar_path)
        if error is not None:
            results.append(GVarTestResult(case=case, passed=False, actual=None, stdout="", error=error))
            continue
        results.append(
            await run_gvar_test(
                case,
                builder,
                executor,
                gvar_source=gvar_sources.get(case.gvar_path),
                base_context=baseline,
            )
        )
    return results


async def run_gvar_test(
    case: GVarTestCase,
    builder: ContextBuilder,
    executor: MockExecutor,
    *,
    gvar_source: str | None = None,
    base_context: ContextData | None = None,
) -> GVarTestResult:
    if gvar_source is None:
        source_started = time.perf_counter()
        try:
            gvar_source = case.gvar_path.read_text()
        except OSError as exc:
            return GVarTestResult(
                case=case,
                passed=False,
                actual=None,
                stdout="",
                error=f"Failed to read gvar file {case.gvar_path}: {exc}",
            )
        log.debug("Loaded gvar source %s in %.2fms", case.gvar_path, (time.perf_counter() - source_started) * 1000)

    ctx_data = builder.build_from_baseline(base_context)
    if case.var_overrides:
        ctx_data.vars = ctx_data.vars.merge(VarSources.from_data(case.var_overrides))
    if case.character_overrides:
        ctx_data.character = deep_merge_dicts(ctx_data.character, case.character_overrides)
    builder.gvar_resolver.reset(ctx_data.vars.gvars)

    collision = _reserved_name_collision(case.binding_name, executor, ctx_data)
    if collision is not None:
        return GVarTestResult(case=case, passed=False, actual=None, stdout="", error=collision)

    builder.gvar_resolver.seed({case.gvar_name: gvar_source})
    wrapped_code = _wrap_test_body(case.binding_name, case.gvar_name, case.body)
    result = await executor.run(wrapped_code, ctx_data, builder.gvar_resolver)
    if result.error:
        error_line, error_col = _map_error_position(result.error, wrapper_lines=1)
        return GVarTestResult(
            case=case,
            passed=False,
            actual=result.value,
            stdout=result.stdout,
            error=str(result.error),
            error_line=error_line,
            error_col=error_col,
        )

    passed = value_matches(case.expected, result.value)
    details = None if passed else "Result did not match expected output"
    return GVarTestResult(
        case=case,
        passed=passed,
        actual=result.value,
        stdout=result.stdout,
        details=details,
    )


def _resolve_gvar_path(path: Path) -> Path:
    base_dir = path.parent
    stem = path.name
    for suffix in (".gvar-test", ".gvartest"):
        if stem.endswith(suffix):
            gvar_name = stem[: -len(suffix)]
            break
    else:  # pragma: no cover - defensive
        gvar_name = path.stem

    if not gvar_name:
        raise GVarTestError(f"{path} must have a stem before '.gvar-test'")

    target = base_dir / f"{gvar_name}.gvar"
    if target.exists():
        return target
    raise GVarTestError(f"Could not find sibling gvar file for '{gvar_name}'. Checked: {target}")


def _wrap_test_body(binding_name: str, gvar_name: str, body: str) -> str:
    return f'using({binding_name}="{gvar_name}")\n{body}'


def _reserved_name_collision(binding_name: str, executor: MockExecutor, ctx_data: ContextData) -> str | None:
    if binding_name in executor.available_names(ctx_data):
        return f"Implicit gvar binding '{binding_name}' collides with an existing runtime name."
    return None


def _map_error_position(error: BaseException, *, wrapper_lines: int) -> tuple[int | None, int | None]:
    if isinstance(error, ModuleExecutionError):
        return None, None
    if isinstance(getattr(error, "__cause__", None), ModuleExecutionError):
        return None, None
    if isinstance(getattr(error, "original", None), ModuleExecutionError):
        return None, None

    line_in_code: int | None = None
    col_in_code: int | None = None
    node = getattr(error, "node", None)
    if node is not None:
        node_line = getattr(node, "lineno", None)
        if isinstance(node_line, int) and node_line > 0:
            line_in_code = node_line
        node_col = getattr(node, "col_offset", None)
        if isinstance(node_col, int) and node_col >= 0:
            col_in_code = node_col
    if line_in_code is None:
        lineno = getattr(error, "lineno", None)
        if isinstance(lineno, int) and lineno > 0:
            line_in_code = lineno
        offset = getattr(error, "offset", None)
        if isinstance(offset, int) and offset > 0:
            col_in_code = offset - 1

    if line_in_code is None:
        return None, None
    if line_in_code <= wrapper_lines:
        return 1, (col_in_code or 0) + 1
    return line_in_code - wrapper_lines, (col_in_code or 0) + 1
