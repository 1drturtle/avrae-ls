from __future__ import annotations

import re
import string
from typing import Sequence


_NUM_PLACEHOLDER_RE = re.compile(r"%(\d+)%|&(\d+)&")


def _escape_quotes(arg: str) -> str:
    """Escape quotes/backslashes for analysis placeholder substitution."""
    return arg.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def _escape_double_quotes(arg: str) -> str:
    """Escape quotes the same way Avrae's alias argument substitution does."""
    return arg.replace('"', '\\"')


def _argquote(arg: str) -> str:
    if any(char in arg for char in string.whitespace):
        return f'"{_escape_double_quotes(arg)}"'
    return arg


def _ensure_args(args: Sequence[str] | None, count: int) -> list[str]:
    out = list(args or [])
    while len(out) < count:
        out.append(f"arg{len(out) + 1}")
    return out


def _apply_argument_parsing_analysis(text: str, args: Sequence[str] | None = None) -> str:
    """Apply placeholder-safe substitutions for static source analysis."""
    max_idx = 0
    for match in _NUM_PLACEHOLDER_RE.finditer(text):
        groups = [g for g in match.groups() if g]
        if groups:
            max_idx = max(max_idx, int(groups[0]))

    args_list = _ensure_args(args, max_idx)

    def get_arg(idx: int) -> str:
        zero = idx - 1
        if zero < 0 or zero >= len(args_list):
            return ""
        return str(args_list[zero])

    def percent_repl(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        val = get_arg(idx)
        return f'"{val}"' if " " in val else val

    def amp_repl(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        val = get_arg(idx)
        return _escape_quotes(val)

    full_args = " ".join(args_list)
    escaped_full_args = _escape_quotes(full_args)
    list_literal = "[" + ", ".join(repr(a) for a in args_list) + "]"

    # order: numeric placeholders first, then multi-arg macros
    text = re.sub(r"%(\d+)%", percent_repl, text)
    text = text.replace("%*%", full_args)
    text = re.sub(r"&(\d+)&", amp_repl, text)
    text = text.replace("&*&", escaped_full_args)
    text = text.replace("&ARGS&", list_literal)
    return text


def _apply_argument_parsing_runtime(
    text: str,
    args: Sequence[str] | None = None,
    raw_args: str | None = None,
) -> str:
    """Apply Avrae-compatible runtime argument substitutions."""
    if args is None and raw_args:
        from avrae_ls.runtime import argparser as avrae_argparser

        parsed_args = [str(arg) for arg in avrae_argparser.argsplit(raw_args)]
    else:
        parsed_args = [str(arg) for arg in (args or [])]

    rendered = text
    raw = raw_args if raw_args is not None else " ".join(_argquote(arg) for arg in parsed_args)

    if "%*%" in text:
        rendered = rendered.replace("%*%", _argquote(raw))
    if "&*&" in text:
        rendered = rendered.replace("&*&", _escape_double_quotes(raw))
    if "&ARGS&" in text:
        rendered = rendered.replace("&ARGS&", str(parsed_args))

    for index, value in enumerate(parsed_args):
        key = f"%{index + 1}%"
        if key in text:
            rendered = rendered.replace(key, _argquote(value))
        key = f"&{index + 1}&"
        if key in text:
            rendered = rendered.replace(key, _escape_double_quotes(value))
    return rendered


def apply_argument_parsing(
    text: str,
    args: Sequence[str] | None = None,
    *,
    raw_args: str | None = None,
    runtime: bool = True,
) -> str:
    """
    Apply Avrae's argument placeholder replacement rules to an alias body.

    Supports:
    - %N%   : non-code replacement (quotes added if arg contains spaces)
    - %*%   : full arg string
    - &N&   : in-code replacement with quote escaping
    - &*&   : full arg string with quote escaping
    - &ARGS&: Python-style list literal of args

    When `runtime` is `True`, `%*%`/`&*&` consume the raw argument string so
    quoted input can be preserved for runtime previews.

    When `runtime` is `False`, missing args are filled with placeholders to keep
    static analysis parseable.
    """
    if runtime:
        return _apply_argument_parsing_runtime(text, args=args, raw_args=raw_args)
    return _apply_argument_parsing_analysis(text, args=args)
