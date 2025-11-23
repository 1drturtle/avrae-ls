from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Iterable

from lsprotocol import types

from .config import CONFIG_FILENAME, load_config
from .context import ContextBuilder
from .diagnostics import DiagnosticProvider
from .runtime import MockExecutor
from .server import create_server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Avrae draconic alias language server")
    parser.add_argument("--tcp", action="store_true", help="Run in TCP mode instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="TCP host (when --tcp is set)")
    parser.add_argument("--port", type=int, default=2087, help="TCP port (when --tcp is set)")
    parser.add_argument("--stdio", action="store_true", help="Accept stdio flag for VS Code clients (ignored)")
    parser.add_argument("--log-level", default="WARNING", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    parser.add_argument("--analyze", metavar="FILE", help="Run diagnostics for a file and print them to stdout")
    args = parser.parse_args(argv)

    _configure_logging(args.log_level)

    if args.analyze:
        if args.tcp:
            parser.error("--analyze cannot be combined with --tcp")
        sys.exit(_run_analysis(Path(args.analyze)))

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


def _run_analysis(path: Path) -> int:
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 2

    workspace_root = _discover_workspace_root(path)
    log = logging.getLogger(__name__)
    log.info("Analyzing %s (workspace root: %s)", path, workspace_root)

    config, warnings = load_config(workspace_root)
    for warning in warnings:
        log.warning(warning)

    builder = ContextBuilder(config)
    ctx_data = builder.build()
    executor = MockExecutor()
    diagnostics = DiagnosticProvider(executor, config.diagnostics)

    source = path.read_text()
    results = asyncio.run(diagnostics.analyze(source, ctx_data, builder.gvar_resolver))
    _print_diagnostics(path, results)
    return 1 if results else 0


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
