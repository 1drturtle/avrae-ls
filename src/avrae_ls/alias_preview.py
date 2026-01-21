from __future__ import annotations

import re
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Tuple

from .parser import DRACONIC_RE, INLINE_DRACONIC_RE, INLINE_ROLL_RE
from .runtime import ExecutionResult, MockExecutor, _roll_dice
from .context import ContextData, GVarResolver
from .argument_parsing import apply_argument_parsing


@dataclass
class RenderedAlias:
    command: str
    stdout: str
    error: Optional[BaseException]
    last_value: Any | None = None
    error_line: int | None = None


@dataclass
class EmbedFieldPreview:
    name: str
    value: str
    inline: bool = False


@dataclass
class EmbedPreview:
    title: str | None = None
    description: str | None = None
    footer: str | None = None
    thumbnail: str | None = None
    image: str | None = None
    color: str | None = None
    timeout: int | None = None
    fields: list[EmbedFieldPreview] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "footer": self.footer,
            "thumbnail": self.thumbnail,
            "image": self.image,
            "color": self.color,
            "timeout": self.timeout,
            "fields": [asdict(f) for f in self.fields],
        }


@dataclass
class SimulatedCommand:
    preview: str | None
    command_name: str | None
    validation_error: str | None
    embed: EmbedPreview | None = None


def _strip_alias_header_with_offset(text: str) -> tuple[str, int]:
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("!alias"):
        first = lines[0].lstrip()
        parts = first.split(maxsplit=2)
        remainder = parts[2] if len(parts) > 2 else ""
        body = "\n".join(lines[1:])
        if remainder:
            return remainder + ("\n" + body if body else ""), 0
        return body, 1
    return text, 0


def _strip_alias_header(text: str) -> str:
    body, _ = _strip_alias_header_with_offset(text)
    return body


def _line_index_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset)


def _error_line_for_match(
    body: str, match: re.Match[str], error: BaseException, line_offset: int
) -> int | None:
    base_line = _line_index_for_offset(body, match.start(1))
    lineno = getattr(error, "lineno", None)
    if isinstance(lineno, int) and lineno > 0 and getattr(error, "text", None) is not None:
        line_index = base_line + (lineno - 1)
    else:
        code = match.group(1)
        leading_newlines = len(code) - len(code.lstrip("\n"))
        line_index = base_line + leading_newlines
    return line_index + line_offset + 1


async def render_alias_command(
    text: str,
    executor: MockExecutor,
    ctx_data: ContextData,
    resolver: GVarResolver,
    args: list[str] | None = None,
) -> RenderedAlias:
    """Replace <drac2> blocks with their evaluated values and return final command."""
    body, line_offset = _strip_alias_header_with_offset(text)
    body = apply_argument_parsing(body, args)
    stdout_parts: list[str] = []
    parts: list[str] = []
    last_value = None
    error: BaseException | None = None
    error_line: int | None = None

    pos = 0
    matches: list[tuple[str, re.Match[str]]] = []
    for match in DRACONIC_RE.finditer(body):
        matches.append(("block", match))
    for match in INLINE_DRACONIC_RE.finditer(body):
        matches.append(("inline", match))
    for match in INLINE_ROLL_RE.finditer(body):
        matches.append(("roll", match))

    matches.sort(key=lambda item: item[1].start())

    for kind, match in matches:
        if match.start() < pos:
            continue
        parts.append(body[pos: match.start()])

        if kind in {"block", "inline"}:
            code = match.group(1)
            result: ExecutionResult = await executor.run(code, ctx_data, resolver)
            if result.stdout:
                stdout_parts.append(result.stdout)
            if result.error:
                error = result.error
                error_line = _error_line_for_match(body, match, result.error, line_offset)
                break
            last_value = result.value
            if result.value is not None:
                parts.append(str(result.value))
        else:
            roll_expr = match.group(1)
            roll_total = _roll_dice(roll_expr)
            last_value = roll_total
            parts.append(str(roll_total))
        pos = match.end()

    if error is None:
        parts.append(body[pos:])

    final_command = "".join(parts)
    return RenderedAlias(
        command=final_command,
        stdout="".join(stdout_parts),
        error=error,
        last_value=last_value,
        error_line=error_line,
    )


def validate_embed_payload(payload: str) -> Tuple[bool, str | None]:
    """
    Light validation for embed previews using Avrae-style flags.

    Accepts strings such as "-title Foo -f \"T|Body\"" and validates arguments.
    Returns (is_valid, error_message) without attempting to parse JSON objects.
    """
    text = payload.strip()
    if not text:
        return False, "Embed payload is empty."

    return _validate_embed_flags(text)


def parse_embed_payload(payload: str) -> EmbedPreview:
    """Parse an embed payload into a structured preview object."""
    tokens = shlex.split(payload.strip())
    preview = EmbedPreview()

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith("-"):
            i += 1
            continue
        key = tok.lower()
        next_val = tokens[i + 1] if i + 1 < len(tokens) else None
        if key == "-title":
            preview.title = next_val or ""
            i += 2
            continue
        if key == "-desc":
            preview.description = next_val or ""
            i += 2
            continue
        if key == "-footer":
            preview.footer = next_val or ""
            i += 2
            continue
        if key == "-thumb":
            preview.thumbnail = next_val or ""
            i += 2
            continue
        if key == "-image":
            preview.image = next_val or ""
            i += 2
            continue
        if key == "-color":
            preview.color = _normalize_color(next_val)
            i += 2 if next_val is not None else 1
            continue
        if key == "-t":
            preview.timeout = _parse_timeout(next_val)
            i += 2
            continue
        if key == "-f":
            field = _parse_field_value(next_val)
            if field:
                preview.fields.append(field)
            i += 2
            continue
        i += 1

    return preview


def _validate_embed_flags(text: str) -> Tuple[bool, str | None]:
    """Validate embed flags according to Avrae's help text."""
    if not text:
        return False, "Embed payload is empty."

    try:
        tokens = shlex.split(text)
    except ValueError as exc:  # pragma: no cover - defensive only
        return False, f"Embed payload could not be parsed: {exc}"

    flag_handlers = {
        "-title": lambda val: _require_value("-title", val),
        "-desc": lambda val: _require_value("-desc", val),
        "-thumb": lambda val: _require_value("-thumb", val),
        "-image": lambda val: _require_value("-image", val),
        "-footer": lambda val: _require_value("-footer", val),
        "-f": _validate_field_arg,
        "-color": _validate_color_arg,
        "-t": _validate_timeout_arg,
    }

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        key = tok.lower()
        if not tok.startswith("-"):
            i += 1
            continue
        if key not in flag_handlers:
            return False, f"Embed payload contains unknown flag '{tok}'."
        next_val = tokens[i + 1] if i + 1 < len(tokens) else None
        ok, err, consumed = flag_handlers[key](next_val)
        if not ok:
            return False, err
        i += consumed + 1
    return True, None


def _require_value(flag: str, value: str | None) -> Tuple[bool, str | None, int]:
    if value is None or value.startswith("-"):
        return False, f"Embed flag '{flag}' requires a value.", 0
    return True, None, 1


def _validate_field_arg(value: str | None) -> Tuple[bool, str | None, int]:
    ok, err, consumed = _require_value("-f", value)
    if not ok:
        return ok, err, consumed
    assert value is not None  # for type checker
    parts = value.split("|")
    if len(parts) < 2 or len(parts) > 3:
        return False, "Embed field must be in the form \"Title|Text[|inline]\".", consumed
    if not parts[0] or not parts[1]:
        return False, "Embed field title and text cannot be empty.", consumed
    if len(parts) == 3 and parts[2].lower() not in ("inline", ""):
        return False, "Embed field inline value must be 'inline' or omitted.", consumed
    return True, None, consumed


def _validate_color_arg(value: str | None) -> Tuple[bool, str | None, int]:
    if value is None or value.startswith("-"):
        # Random color is allowed when omitted
        return True, None, 0
    if str(value).strip().lower() == "<color>":
        # Avrae placeholder accepted
        return True, None, 1
    if not re.match(r"^(?:#|0x)?[0-9a-fA-F]{6}$", value):
        return False, "Embed color must be a 6-hex value (e.g. #ff00ff).", 1
    return True, None, 1


def _validate_timeout_arg(value: str | None) -> Tuple[bool, str | None, int]:
    ok, err, consumed = _require_value("-t", value)
    if not ok:
        return ok, err, consumed
    assert value is not None  # for type checker
    try:
        num = int(value)
    except ValueError:
        return False, "Embed timeout (-t) must be an integer.", consumed
    if num < 0 or num > 600:
        return False, "Embed timeout (-t) must be between 0 and 600 seconds.", consumed
    return True, None, consumed


def _parse_timeout(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_color(value: str | None) -> str | None:
    if value is None:
        return None
    if not value:
        return None
    if str(value).strip().lower() == "<color>":
        return "<color>"
    match = re.match(r"^(?:#|0x)?([0-9a-fA-F]{6})$", value)
    if not match:
        return value
    return f"#{match.group(1)}"


def _parse_field_value(value: str | None) -> EmbedFieldPreview | None:
    if value is None:
        return None
    parts = value.split("|")
    if len(parts) < 2:
        return None
    inline_flag = parts[2].lower() == "inline" if len(parts) == 3 else False
    return EmbedFieldPreview(name=parts[0], value=parts[1], inline=inline_flag)


def simulate_command(command: str) -> SimulatedCommand:
    """Very small shim to preview common commands."""
    text = _strip_alias_header(command).strip()
    if not text:
        return SimulatedCommand(None, None, None, None)
    head, payload = _extract_command_head_and_payload(text)
    if not head:
        return SimulatedCommand(None, None, None, None)
    lowered = head.lower()
    if lowered == "echo":
        return SimulatedCommand(payload, "echo", None, None)
    if lowered == "embed":
        valid, error = validate_embed_payload(payload)
        embed_preview = parse_embed_payload(payload) if valid else None
        return SimulatedCommand(payload, "embed", error, embed_preview)
    if head.startswith("-") and _is_embed_flag(head):
        payload = text
        valid, error = validate_embed_payload(payload)
        embed_preview = parse_embed_payload(payload) if valid else None
        return SimulatedCommand(payload, "embed", error, embed_preview)
    return SimulatedCommand(None, head, None, None)


def _extract_command_head_and_payload(text: str) -> tuple[str | None, str]:
    """Prefer the first non-empty line; fall back to any embed line later."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None, ""
    head, payload = _split_head_and_payload_from_line(lines[0])
    if _is_embed_flag(head):
        # Treat the entire payload (including the head line) as embed flags so multiple lines are preserved.
        return head, "\n".join(lines)
    if head and head.lower() in ("embed", "echo"):
        return head, _merge_payload(payload, lines[1:])
    for idx, line in enumerate(lines[1:], start=1):
        possible_head, possible_payload = _split_head_and_payload_from_line(line)
        if possible_head and (possible_head.lower() == "embed" or _is_embed_flag(possible_head)):
            return possible_head, _merge_payload(possible_payload, lines[idx + 1 :])
    return head, _merge_payload(payload, lines[1:])


def _split_head_and_payload_from_line(line: str) -> tuple[str | None, str]:
    if not line:
        return None, ""
    parts = line.split(maxsplit=1)
    head = parts[0]
    payload = parts[1] if len(parts) > 1 else ""
    return head, payload


def _merge_payload(first_payload: str, trailing_lines: list[str]) -> str:
    payload = first_payload
    if trailing_lines:
        payload = (payload + "\n" if payload else "") + "\n".join(trailing_lines)
    return payload


def _is_embed_flag(flag: str) -> bool:
    return flag.lower() in {"-title", "-desc", "-thumb", "-image", "-footer", "-f", "-color", "-t"}
