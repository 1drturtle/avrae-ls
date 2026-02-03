from __future__ import annotations

from lsprotocol import types


def range_from_positions(
    lineno: int | None,
    col_offset: int | None,
    end_lineno: int | None,
    end_col_offset: int | None,
    *,
    one_based: bool = False,
    ensure_nonempty: bool = False,
) -> types.Range:
    start_line = max((lineno or 1) - 1, 0)
    if one_based:
        start_char = max((col_offset or 1) - 1, 0)
    else:
        start_char = max(col_offset or 0, 0)
    end_line = max(((end_lineno or lineno or 1) - 1), 0)
    if one_based:
        end_char = max(((end_col_offset or col_offset or 1) - 1), 0)
    else:
        raw_end_char = end_col_offset if end_col_offset is not None else col_offset
        end_char = max(raw_end_char if raw_end_char is not None else start_char, start_char)
    if ensure_nonempty and end_char <= start_char:
        end_char = start_char + 1
    return types.Range(
        start=types.Position(line=start_line, character=start_char),
        end=types.Position(line=end_line, character=end_char),
    )


def shift_range(rng: types.Range, line_offset: int, char_offset: int) -> types.Range:
    def _shift_pos(pos: types.Position) -> types.Position:
        return types.Position(
            line=max(pos.line + line_offset, 0),
            character=max(pos.character + (char_offset if pos.line == 0 else 0), 0),
        )

    return types.Range(start=_shift_pos(rng.start), end=_shift_pos(rng.end))
