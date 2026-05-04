from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from avrae_ls.runtime import argparser as avrae_argparser
from avrae_ls.runtime.alias_preview import render_alias_command, simulate_command
from avrae_ls.runtime.context import ContextBuilder, ContextData
from avrae_ls.runtime.runtime import MockExecutor
from avrae_ls.config import VarSources
from avrae_ls.testing._common import (
    deep_merge_dicts,
    dict_matches,
    merge_new_gvars_into_suite_cache,
    parse_expected_value,
    parse_metadata_mapping,
    scalar_matches,
)

log = logging.getLogger(__name__)


class AliasTestError(Exception):
    """Raised when an alias test cannot be parsed or executed."""


@dataclass
class AliasTestCase:
    path: Path
    alias_path: Path
    alias_name: str
    name: str | None
    args: list[str]
    expected_raw: str
    expected: Any
    var_overrides: dict[str, Any] | None = None
    character_overrides: dict[str, Any] | None = None

    @property
    def target_kind(self) -> str:
        return "alias"

    @property
    def target_name(self) -> str:
        return self.alias_name


@dataclass
class AliasTestResult:
    case: AliasTestCase
    passed: bool
    actual: Any
    stdout: str
    embed: dict[str, Any] | None = None
    error: str | None = None
    details: str | None = None
    error_line: int | None = None
    error_col: int | None = None


def discover_test_files(
    target: Path, *, recursive: bool = True, patterns: Sequence[str] = ("*.alias-test", "*.aliastest")
) -> list[Path]:
    if target.is_file():
        return [target]
    files: set[Path] = set()
    for pattern in patterns:
        globber = target.rglob if recursive else target.glob
        files.update(globber(pattern))
    return sorted(files)


def parse_alias_tests(path: Path) -> list[AliasTestCase]:
    try:
        text = path.read_text()
    except OSError as exc:  # pragma: no cover - filesystem edge
        raise AliasTestError(f"Failed to read {path}: {exc}") from exc

    lines = text.splitlines()
    idx = 0
    cases: list[AliasTestCase] = []
    while idx < len(lines):
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if idx >= len(lines):
            break
        command_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---":
            command_lines.append(lines[idx])
            idx += 1
        if not command_lines:
            raise AliasTestError(f"{path} has no command to execute before '---'")
        if idx >= len(lines):
            raise AliasTestError(f"{path} is missing a '---' separator")
        idx += 1  # consume first ---

        expected_lines: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "---" and not lines[idx].lstrip().startswith("!"):
            expected_lines.append(lines[idx])
            idx += 1

        meta_lines: list[str] = []
        if idx < len(lines) and lines[idx].strip() == "---":
            idx += 1  # consume second ---
            while idx < len(lines) and not lines[idx].lstrip().startswith("!"):
                meta_lines.append(lines[idx])
                idx += 1

        command_part = "\n".join(command_lines).strip()
        expected_raw = "\n".join(expected_lines)
        meta_raw = "\n".join(meta_lines)

        tokens = _split_command(command_part, path)
        alias_name = tokens[0].lstrip("!")
        args = tokens[1:]
        alias_path = _resolve_alias_path(path, alias_name)
        expected = parse_expected_value(expected_raw)
        try:
            meta = parse_metadata_mapping(meta_raw, str(path))
        except ValueError as exc:
            raise AliasTestError(str(exc)) from exc
        name = meta.get("name") if isinstance(meta, dict) else None
        var_overrides = meta.get("vars") if isinstance(meta, dict) else None
        character_overrides = meta.get("character") if isinstance(meta, dict) else None

        cases.append(
            AliasTestCase(
                path=path,
                alias_path=alias_path,
                alias_name=alias_name,
                name=name,
                args=args,
                expected_raw=expected_raw,
                expected=expected,
                var_overrides=var_overrides if isinstance(var_overrides, dict) else None,
                character_overrides=character_overrides if isinstance(character_overrides, dict) else None,
            )
        )
    return cases


async def run_alias_tests(
    cases: Iterable[AliasTestCase],
    builder: ContextBuilder,
    executor: MockExecutor,
    *,
    suite_gvar_cache: dict[str, Any] | None = None,
) -> list[AliasTestResult]:
    case_list = list(cases)
    alias_sources: dict[Path, str] = {}
    alias_errors: dict[Path, str] = {}

    source_started = time.perf_counter()
    for alias_path in {case.alias_path for case in case_list}:
        try:
            alias_sources[alias_path] = alias_path.read_text()
        except OSError as exc:
            alias_errors[alias_path] = f"Failed to read alias file {alias_path}: {exc}"
    log_elapsed = (time.perf_counter() - source_started) * 1000
    if alias_sources:
        log.debug(
            "Loaded %d alias source file(s) for tests in %.2fms",
            len(alias_sources),
            log_elapsed,
        )

    baseline = builder.build_baseline()
    shared_gvar_cache = suite_gvar_cache if suite_gvar_cache is not None else dict(baseline.vars.gvars)
    results: list[AliasTestResult] = []
    for case in case_list:
        error = alias_errors.get(case.alias_path)
        if error is not None:
            results.append(AliasTestResult(case=case, passed=False, actual=None, stdout="", error=error))
            continue
        results.append(
            await run_alias_test(
                case,
                builder,
                executor,
                alias_source=alias_sources.get(case.alias_path),
                base_context=baseline,
                suite_gvar_cache=shared_gvar_cache,
            )
        )
    return results


async def run_alias_test(
    case: AliasTestCase,
    builder: ContextBuilder,
    executor: MockExecutor,
    *,
    alias_source: str | None = None,
    base_context: ContextData | None = None,
    suite_gvar_cache: dict[str, Any] | None = None,
) -> AliasTestResult:
    if alias_source is None:
        source_started = time.perf_counter()
        try:
            alias_source = case.alias_path.read_text()
        except OSError as exc:
            return AliasTestResult(
                case=case,
                passed=False,
                actual=None,
                stdout="",
                error=f"Failed to read alias file {case.alias_path}: {exc}",
            )
        log.debug(
            "Loaded alias source %s in %.2fms",
            case.alias_path,
            (time.perf_counter() - source_started) * 1000,
        )

    ctx_data = builder.build_from_baseline(base_context)
    if case.var_overrides:
        ctx_data.vars = ctx_data.vars.merge(VarSources.from_data(case.var_overrides))
    if case.character_overrides:
        ctx_data.character = deep_merge_dicts(ctx_data.character, case.character_overrides)
    shared_gvar_cache = suite_gvar_cache if suite_gvar_cache is not None else dict(ctx_data.vars.gvars)
    builder.gvar_resolver.load_snapshot(shared_gvar_cache)
    builder.gvar_resolver.seed(ctx_data.vars.gvars)
    local_only_gvars = set(ctx_data.vars.gvars.keys())

    rendered = await render_alias_command(alias_source, executor, ctx_data, builder.gvar_resolver, args=case.args)
    merge_new_gvars_into_suite_cache(
        shared_gvar_cache,
        builder.gvar_resolver.snapshot(),
        exclude_keys=local_only_gvars,
    )
    if rendered.error:
        return AliasTestResult(
            case=case,
            passed=False,
            actual=None,
            stdout=rendered.stdout,
            error=str(rendered.error),
            error_line=rendered.error_line,
            error_col=rendered.error_col,
        )

    preview = simulate_command(rendered.command)
    if preview.validation_error:
        return AliasTestResult(
            case=case,
            passed=False,
            actual=None,
            stdout=rendered.stdout,
            error=preview.validation_error,
        )

    if preview.preview is not None:
        actual = preview.preview
    else:
        if rendered.command.strip() == "" and rendered.last_value is None:
            actual = None
        elif rendered.last_value is not None and rendered.command.strip() == str(rendered.last_value):
            actual = rendered.last_value
        else:
            actual = rendered.command
    embed_dict = preview.embed.to_dict() if preview.embed else None

    if embed_dict is not None and isinstance(case.expected, dict):
        passed = dict_matches(embed_dict, case.expected)
        details = None if passed else "Embed preview did not match expected dictionary"
        actual_display = embed_dict
    else:
        passed = scalar_matches(case.expected, actual)
        details = None if passed else "Result did not match expected output"
        actual_display = actual

    return AliasTestResult(
        case=case,
        passed=passed,
        actual=actual_display,
        stdout=rendered.stdout,
        embed=embed_dict,
        details=details,
    )
def _split_command(command: str, path: Path) -> list[str]:
    try:
        tokens = avrae_argparser.argsplit(command)
    except (avrae_argparser.BadArgument, avrae_argparser.ExpectedClosingQuoteError) as exc:
        raise AliasTestError(f"{path} has an invalid command line: {exc}") from exc
    if not tokens:
        raise AliasTestError(f"{path} has an empty command")
    return tokens


def _resolve_alias_path(path: Path, alias_name: str) -> Path:
    base_dir = path.parent
    candidates = _alias_candidates(path, alias_name)
    for candidate in candidates:
        target = base_dir / candidate
        if target.exists():
            return target

    for child in base_dir.iterdir():
        if child == path or not child.is_file():
            continue
        if child.stem in {alias_name, path.stem.removeprefix("test-")}:
            return child

    raise AliasTestError(
        f"Could not find alias file for '{alias_name}'. Checked: {', '.join(str(base_dir / c) for c in candidates)}"
    )
def _alias_candidates(path: Path, alias_name: str) -> Sequence[str]:
    base = path.stem.removeprefix("test-") or alias_name
    names = [alias_name]
    if base not in names:
        names.append(base)
    suffixes = ["", ".alias", ".txt"]
    return [f"{name}{suffix}" for name in names for suffix in suffixes]
