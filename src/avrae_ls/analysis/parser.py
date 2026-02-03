from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class DraconicBlock:
    code: str
    line_offset: int
    char_offset: int = 0
    line_count: int = 0
    inline: bool = False


DRACONIC_RE = re.compile(r"<drac2>([\s\S]*?)</drac2>", re.IGNORECASE)
INLINE_DRACONIC_RE = re.compile(r"\{\{([\s\S]*?)\}\}", re.DOTALL)
INLINE_ROLL_RE = re.compile(r"(?<!\{)\{(?!\{)([\s\S]*?)(?<!\})\}(?!\})", re.DOTALL)
ALIAS_MODULE_SUFFIX = ".alias-module"


def is_alias_module_path(path: str | Path | None) -> bool:
    if path is None:
        return False
    return str(path).endswith(ALIAS_MODULE_SUFFIX)


def _full_source_block(source: str) -> DraconicBlock:
    line_count = source.count("\n") + 1 if source else 1
    return DraconicBlock(
        code=source,
        line_offset=0,
        char_offset=0,
        line_count=line_count,
        inline=False,
    )


def find_draconic_blocks(source: str, *, treat_as_module: bool = False) -> List[DraconicBlock]:
    if treat_as_module:
        return [_full_source_block(source)]
    matches: list[tuple[int, DraconicBlock]] = []

    def _block_from_match(match: re.Match[str], inline: bool = False) -> tuple[int, int, DraconicBlock]:
        raw = match.group(1)
        prefix = source[: match.start(1)]
        line_offset = prefix.count("\n")
        # Column where draconic content starts on its first line
        last_nl = prefix.rfind("\n")
        start_col = match.start(1) - (last_nl + 1 if last_nl != -1 else 0)
        char_offset = start_col
        # Trim leading blank lines inside the block while tracking the line shift
        while raw.startswith("\n"):
            raw = raw[1:]
            line_offset += 1
            char_offset = 0
        line_count = raw.count("\n") + 1 if raw else 1
        return match.start(), match.end(), DraconicBlock(
            code=raw,
            line_offset=line_offset,
            char_offset=char_offset,
            line_count=line_count,
            inline=inline,
        )

    blocks: list[DraconicBlock] = []
    for match in DRACONIC_RE.finditer(source):
        matches.append(_block_from_match(match))
    for match in INLINE_DRACONIC_RE.finditer(source):
        matches.append(_block_from_match(match, inline=True))

    matches.sort(key=lambda item: item[0])
    last_end = -1
    for start, end, block in matches:
        if start < last_end:
            continue
        blocks.append(block)
        last_end = end
    return blocks


def primary_block_or_source(source: str, *, treat_as_module: bool = False) -> tuple[str, int, int]:
    blocks = find_draconic_blocks(source, treat_as_module=treat_as_module)
    if not blocks:
        return source, 0, 0
    block = blocks[0]
    return block.code, block.line_offset, block.char_offset


def wrap_draconic(code: str) -> tuple[str, int]:
    indented = "\n".join(f"    {line}" for line in code.splitlines())
    wrapped = f"def __alias_main__():\n{indented}\n__alias_main__()"
    return wrapped, 1
