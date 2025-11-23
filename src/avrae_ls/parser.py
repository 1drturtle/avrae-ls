from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class DraconicBlock:
    code: str
    line_offset: int
    char_offset: int = 0
    line_count: int = 0


DRACONIC_RE = re.compile(r"<drac2>([\s\S]*?)</drac2>", re.IGNORECASE)


def find_draconic_blocks(source: str) -> List[DraconicBlock]:
    blocks: list[DraconicBlock] = []
    for match in DRACONIC_RE.finditer(source):
        raw = match.group(1)
        prefix = source[: match.start()]
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
        blocks.append(DraconicBlock(code=raw, line_offset=line_offset, char_offset=char_offset, line_count=line_count))
    return blocks


def primary_block_or_source(source: str) -> tuple[str, int, int]:
    blocks = find_draconic_blocks(source)
    if not blocks:
        return source, 0, 0
    block = blocks[0]
    return block.code, block.line_offset, block.char_offset
