from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .argument_parsing import apply_argument_parsing
from .parser import DraconicBlock, find_draconic_blocks


@dataclass(frozen=True)
class SourceContext:
    source: str
    prepared: str
    blocks: list[DraconicBlock]
    treat_as_module: bool


def build_source_context(source: str, treat_as_module: bool, *, apply_args: bool = True) -> SourceContext:
    prepared = apply_argument_parsing(source) if apply_args and not treat_as_module else source
    blocks = find_draconic_blocks(prepared, treat_as_module=treat_as_module)
    return SourceContext(source=source, prepared=prepared, blocks=blocks, treat_as_module=treat_as_module)


def block_for_line(blocks: Sequence[DraconicBlock], line: int) -> DraconicBlock | None:
    for block in blocks:
        start = block.line_offset
        end = block.line_offset + block.line_count
        if start <= line <= end:
            return block
    return None
