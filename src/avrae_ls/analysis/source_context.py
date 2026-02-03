from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from avrae_ls.runtime.argument_parsing import apply_argument_parsing
from avrae_ls.analysis.parser import DraconicBlock, find_draconic_blocks


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
        end = block.line_offset + block.line_count - 1
        if start <= line <= end:
            return block
    return None


def code_for_position(source_ctx: SourceContext, line: int, character: int) -> tuple[str, int, int] | None:
    """Return the code view and relative cursor position for a document position."""
    if not source_ctx.blocks:
        return source_ctx.prepared, line, character
    block = block_for_line(source_ctx.blocks, line)
    if block is None:
        return None
    return block.code, line - block.line_offset, character
