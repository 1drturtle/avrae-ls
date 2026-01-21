from __future__ import annotations

import argparse
import asyncio
import difflib
import logging
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

from lsprotocol import types

from .alias_tests import (
    AliasTestError,
    AliasTestResult,
    diff_mismatched_parts,
    discover_test_files,
    parse_alias_tests,
    run_alias_tests,
)
from .config import CONFIG_FILENAME, load_config
from .context import ContextBuilder
from .diagnostics import DiagnosticProvider
from .runtime import MockExecutor
from .server import create_server, __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Avrae draconic alias language server")
    parser.add_argument("--tcp", action="store_true", help="Run in TCP mode instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="TCP host (when --tcp is set)")
    parser.add_argument("--port", type=int, default=2087, help="TCP port (when --tcp is set)")
    parser.add_argument("--stdio", action="store_true", help="Accept stdio flag for VS Code clients (ignored)")
    parser.add_argument("--log-level", default="WARNING", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    parser.add_argument("--analyze", metavar="FILE", help="Run diagnostics for a file and print them to stdout")
    parser.add_argument(
        "--run-tests",
        metavar="PATH",
        nargs="?",
        const=".",
        help="Run alias tests in PATH (defaults to current directory)",
    )
    parser.add_argument(
        "--silent-gvar-fetch",
        action="store_true",
        help="Silently ignore gvar fetch failures and treat them as None",
    )
    parser.add_argument("--token", help="Avrae API token (overrides config)")
    parser.add_argument("--base-url", help="Avrae API base URL (overrides config)")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    args = parser.parse_args(argv)

    _configure_logging(args.log_level)

    if args.version:
        print(__version__)
        return

    if args.run_tests is not None:
        if args.tcp:
            parser.error("--run-tests cannot be combined with --tcp")
        if args.analyze:
            parser.error("--run-tests cannot be combined with --analyze")
        sys.exit(
            _run_alias_tests(
                Path(args.run_tests),
                token_override=args.token,
                base_url_override=args.base_url,
                silent_gvar_fetch=args.silent_gvar_fetch,
            )
        )

    if args.analyze:
        if args.tcp:
            parser.error("--analyze cannot be combined with --tcp")
        sys.exit(
            _run_analysis(
                Path(args.analyze),
                token_override=args.token,
                base_url_override=args.base_url,
                silent_gvar_fetch=args.silent_gvar_fetch,
            )
        )

    server = create_server()
    if args.tcp:
        server.start_tcp(args.host, args.port)
    else:
        server.start_io()


def _configure_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), logging.WARNING)
    if not isinstance(numeric, int):
        numeric = logging.WARNING
    logging.basicConfig(
        level=numeric,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _run_analysis(
    path: Path,
    *,
    token_override: str | None = None,
    base_url_override: str | None = None,
    silent_gvar_fetch: bool = False,
) -> int:
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 2

    workspace_root = _discover_workspace_root(path)
    log = logging.getLogger(__name__)
    log.info("Analyzing %s (workspace root: %s)", path, workspace_root)

    config, warnings = load_config(workspace_root, default_enable_gvar_fetch=True)
    if token_override:
        config.service.token = token_override
    if base_url_override:
        config.service.base_url = base_url_override
    if silent_gvar_fetch:
        config.silent_gvar_fetch = True
    for warning in warnings:
        log.warning(warning)

    builder = ContextBuilder(config)
    ctx_data = builder.build()
    executor = MockExecutor(config.service)
    diagnostics = DiagnosticProvider(executor, config.diagnostics)

    source = path.read_text()
    results = asyncio.run(diagnostics.analyze(source, ctx_data, builder.gvar_resolver))
    _print_diagnostics(path, results)
    return 1 if results else 0


def _run_alias_tests(
    target: Path,
    *,
    token_override: str | None = None,
    base_url_override: str | None = None,
    silent_gvar_fetch: bool = False,
) -> int:
    if not target.exists():
        print(f"Test path not found: {target}", file=sys.stderr)
        return 2

    workspace_root = _discover_workspace_root(target)
    log = logging.getLogger(__name__)
    log.info("Running alias tests in %s (workspace root: %s)", target, workspace_root)

    config, warnings = load_config(workspace_root, default_enable_gvar_fetch=True)
    if token_override:
        config.service.token = token_override
    if base_url_override:
        config.service.base_url = base_url_override
    if silent_gvar_fetch:
        config.silent_gvar_fetch = True
    for warning in warnings:
        log.warning(warning)

    builder = ContextBuilder(config)
    executor = MockExecutor(config.service)

    test_files = discover_test_files(target)
    cases = []
    parse_errors: list[str] = []
    for test_file in test_files:
        try:
            cases.extend(parse_alias_tests(test_file))
        except AliasTestError as exc:
            parse_errors.append(str(exc))

    if parse_errors:
        for err in parse_errors:
            print(err, file=sys.stderr)
    if not cases:
        print(f"No alias tests found under {target}")
        return 1 if parse_errors else 0

    results = asyncio.run(run_alias_tests(cases, builder, executor))
    _print_test_results(results, workspace_root)

    failures = [res for res in results if not res.passed]
    return 1 if failures or parse_errors else 0


def _print_test_results(results: Iterable[AliasTestResult], workspace_root: Path) -> None:
    total = len(results)
    passed = 0
    for res in results:
        rel = _relative_to_workspace(res.case.path, workspace_root)
        label = f"{rel} ({res.case.name})" if res.case.name else rel
        status = "PASS" if res.passed else "FAIL"
        print(f"[{status}] {label} (alias: {res.case.alias_name})")
        if res.passed:
            if res.stdout:
                print(f"  Stdout: {res.stdout.strip()}")
            passed += 1
            continue
        if res.error:
            if res.error_line is not None:
                alias_name = res.case.alias_path.name
                print(f"  Error (line {res.error_line} {alias_name}): {res.error}")
            else:
                print(f"  Error: {res.error}")
        if res.details:
            print(f"  {res.details}")
        expected_val, actual_val = _summarize_mismatch(res.case.expected, res.actual)
        expected = _format_value(expected_val)
        actual = _format_value(actual_val)
        _print_labeled_value("Expected", expected)
        _print_labeled_value("Actual", actual)
        diff = _render_diff(expected, actual)
        if diff:
            print("  Diff:")
            for line in diff.splitlines():
                print(f"    {line}")
        if res.stdout:
            print(f"  Stdout: {res.stdout.strip()}")
    print(f"{passed}/{total} tests passed")


def _summarize_mismatch(expected: Any, actual: Any) -> tuple[Any, Any]:
    diff = diff_mismatched_parts(expected, actual)
    if diff is None:
        return expected, actual
    return diff


def _render_diff(expected: str, actual: str) -> str:
    expected_lines = expected.splitlines() or [""]
    actual_lines = actual.splitlines() or [""]
    if expected_lines == actual_lines:
        return ""
    diff_lines = list(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile="expected",
            tofile="actual",
            lineterm="",
        )
    )
    if not diff_lines:
        return ""
    return "\n".join(_colorize_diff_line(line) for line in diff_lines)


def _colorize_diff_line(line: str) -> str:
    if not sys.stdout.isatty():
        return line
    if line.startswith("-"):
        return f"\x1b[31m{line}\x1b[0m"
    if line.startswith("+"):
        return f"\x1b[32m{line}\x1b[0m"
    if line.startswith("@@") or line.startswith("---") or line.startswith("+++"):
        return f"\x1b[36m{line}\x1b[0m"
    return line


def _print_labeled_value(label: str, value: str) -> None:
    lines = value.splitlines() or [""]
    if len(lines) == 1:
        print(f"  {label}: {lines[0]}")
        return
    print(f"  {label}:")
    for line in lines:
        print(f"    {line}")


def _relative_to_workspace(path: Path, workspace_root: Path) -> str:
    try:
        return str(path.relative_to(workspace_root))
    except ValueError:
        return str(path)


def _format_value(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, (dict, list)):
        return (yaml.safe_dump(value, sort_keys=False) or "").strip()
    return str(value)


def _discover_workspace_root(target: Path) -> Path:
    current = target if target.is_dir() else target.parent
    for folder in [current, *current.parents]:
        if (folder / CONFIG_FILENAME).exists():
            return folder
    return current


def _print_diagnostics(path: Path, diagnostics: Iterable[types.Diagnostic]) -> None:
    diags = list(diagnostics)
    if not diags:
        print(f"{path}: no issues found")
        return

    for diag in diags:
        start = diag.range.start
        severity = _severity_label(diag.severity)
        source = diag.source or "avrae-ls"
        print(f"{path}:{start.line + 1}:{start.character + 1}: {severity} [{source}] {diag.message}")


def _severity_label(severity: types.DiagnosticSeverity | None) -> str:
    if severity is None:
        return "info"
    try:
        return types.DiagnosticSeverity(severity).name.lower()
    except Exception:
        return str(severity).lower()


if __name__ == "__main__":
    main()
