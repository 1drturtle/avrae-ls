from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

from .alias_preview import render_alias_command, simulate_command
from .context import ContextBuilder
from .runtime import MockExecutor
from .config import VarSources

MISSING_VALUE = "<missing>"


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
        expected = yaml.safe_load(expected_raw) if expected_raw.strip() else ""
        meta = yaml.safe_load(meta_raw) if meta_raw.strip() else None
        if meta is not None and not isinstance(meta, dict):
            raise AliasTestError(f"{path} metadata after second '---' must be a mapping")
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
    cases: Iterable[AliasTestCase], builder: ContextBuilder, executor: MockExecutor
) -> list[AliasTestResult]:
    results: list[AliasTestResult] = []
    for case in cases:
        results.append(await run_alias_test(case, builder, executor))
    return results


async def run_alias_test(case: AliasTestCase, builder: ContextBuilder, executor: MockExecutor) -> AliasTestResult:
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

    ctx_data = builder.build()
    if case.var_overrides:
        ctx_data.vars = ctx_data.vars.merge(VarSources.from_data(case.var_overrides))
    if case.character_overrides:
        ctx_data.character = _deep_merge_dicts(ctx_data.character, case.character_overrides)

    rendered = await render_alias_command(alias_source, executor, ctx_data, builder.gvar_resolver, args=case.args)
    if rendered.error:
        return AliasTestResult(
            case=case,
            passed=False,
            actual=None,
            stdout=rendered.stdout,
            error=str(rendered.error),
            error_line=rendered.error_line,
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

    actual = preview.preview if preview.preview is not None else rendered.last_value
    embed_dict = preview.embed.to_dict() if preview.embed else None

    if embed_dict is not None and isinstance(case.expected, dict):
        passed = _dict_matches(embed_dict, case.expected)
        details = None if passed else "Embed preview did not match expected dictionary"
        actual_display = embed_dict
    else:
        passed = _scalar_matches(case.expected, actual)
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


def _scalar_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str):
        if expected == "":
            return True
        pattern = _compile_expected_pattern(expected)
        if pattern:
            return pattern.search("" if actual is None else str(actual)) is not None
        lhs = expected.strip()
        rhs = "" if actual is None else str(actual).strip()
        return lhs == rhs
    return expected == actual


def _dict_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, expected_val in expected.items():
        if key not in actual:
            return False
        actual_val = actual[key]
        if not _value_matches(expected_val, actual_val):
            return False
    return True


def _value_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and _dict_matches(actual, expected)
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return False
        return all(_value_matches(e, actual[idx]) for idx, e in enumerate(expected))
    return _scalar_matches(expected, actual)


def diff_mismatched_parts(expected: Any, actual: Any) -> tuple[Any, Any] | None:
    if _value_matches(expected, actual):
        return None

    if isinstance(expected, dict) and isinstance(actual, dict):
        expected_diff: dict[str, Any] = {}
        actual_diff: dict[str, Any] = {}
        for key, expected_val in expected.items():
            if key not in actual:
                expected_diff[key] = expected_val
                actual_diff[key] = MISSING_VALUE
                continue
            sub_diff = diff_mismatched_parts(expected_val, actual[key])
            if sub_diff:
                expected_diff[key], actual_diff[key] = sub_diff
        if expected_diff:
            return expected_diff, actual_diff
        return expected, actual

    if isinstance(expected, list) and isinstance(actual, list):
        expected_diff: list[Any] = []
        actual_diff: list[Any] = []
        for idx, expected_val in enumerate(expected):
            if idx >= len(actual):
                expected_diff.append(expected_val)
                actual_diff.append(MISSING_VALUE)
                continue
            sub_diff = diff_mismatched_parts(expected_val, actual[idx])
            if sub_diff:
                expected_diff.append(sub_diff[0])
                actual_diff.append(sub_diff[1])
        if expected_diff:
            return expected_diff, actual_diff
        return expected, actual

    return expected, actual


def _split_command(command: str, path: Path) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
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

def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, val in (override or {}).items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge_dicts(merged[key], val)
        else:
            merged[key] = val
    return merged


def _alias_candidates(path: Path, alias_name: str) -> Sequence[str]:
    base = path.stem.removeprefix("test-") or alias_name
    names = [alias_name]
    if base not in names:
        names.append(base)
    suffixes = ["", ".alias", ".txt"]
    return [f"{name}{suffix}" for name in names for suffix in suffixes]


def _compile_expected_pattern(text: str) -> re.Pattern[str] | None:
    """
    Interpret strings with /.../ segments (or re:prefix) as regex.

    - `/foo/` or `re:foo` => regex `foo`
    - Mixed literals + regex, e.g. `Hello /world.*/` => literal `Hello ` + regex `world.*`
    """
    if not text:
        return None
    if text.startswith("re:"):
        try:
            return re.compile(text[3:])
        except re.error:
            return None

    parts = re.split(r"(?<!\\)/(.*?)(?<!\\)/", text)
    if len(parts) == 1:
        return None

    if len(parts) == 3 and parts[0] == "" and parts[2] == "":
        pattern = parts[1].replace("\\/", "/")
        try:
            return re.compile(pattern)
        except re.error:
            return None

    regex_parts: list[str] = []
    for idx, part in enumerate(parts):
        unescaped = part.replace("\\/", "/")
        if idx % 2 == 0:
            regex_parts.append(re.escape(unescaped))
        else:
            regex_parts.append(unescaped)
    pattern = "^" + "".join(regex_parts) + "$"
    try:
        return re.compile(pattern)
    except re.error:
        return None
