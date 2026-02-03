from __future__ import annotations

import re
from typing import List, Sequence


_NUM_PLACEHOLDER_RE = re.compile(r"%(\d+)%|&(\d+)&")


def _escape_quotes(arg: str) -> str:
    """Escape quotes/backslashes to mimic Avrae's quote-escaping behavior."""
    return arg.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


def _ensure_args(args: Sequence[str] | None, count: int) -> List[str]:
    out = list(args or [])
    while len(out) < count:
        out.append(f"arg{len(out) + 1}")
    return out


def apply_argument_parsing(text: str, args: Sequence[str] | None = None) -> str:
    """
    Apply Avrae's argument placeholder replacement rules to an alias body.

    Supports:
    - %N%   : non-code replacement (quotes added if arg contains spaces)
    - %*%   : full arg string
    - &N&   : in-code replacement with quote escaping
    - &*&   : full arg string with quote escaping
    - &ARGS&: Python-style list literal of args
    """
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

    def percent_repl(match: re.Match) -> str:
        idx = int(match.group(1))
        val = get_arg(idx)
        return f'"{val}"' if " " in val else val

    def amp_repl(match: re.Match) -> str:
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
