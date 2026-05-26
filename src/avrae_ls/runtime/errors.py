from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

import draconic
from draconic.exceptions import AnnotatedException, NestedException, WrappedException


_MODULE_SOURCES_ATTR = "__avrae_module_sources__"


@dataclass
class RuntimeErrorDetails:
    message: str
    kind: str
    module: str | None = None
    module_line: int | None = None
    module_col: int | None = None
    cause: str | None = None
    import_chain: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def attach_runtime_error_context(error: BaseException, *, module_sources: dict[str, str]) -> None:
    """Attach per-run source metadata to an exception without changing its semantics."""
    try:
        setattr(error, _MODULE_SOURCES_ATTR, dict(module_sources))
    except Exception:
        return


def runtime_error_details(error: BaseException) -> RuntimeErrorDetails:
    module_sources = _module_sources(error)
    module_error = _find_module_execution_error(error)
    if module_error is not None:
        return _module_execution_details(module_error)

    module_details = _module_source_details(error, module_sources)
    if module_details is not None:
        return module_details

    circular_error = _find_error(error, lambda exc: _circular_import_details(exc) is not None)
    circular = _circular_import_details(circular_error) if circular_error is not None else None
    if circular is not None:
        return circular

    missing_error = _find_error(error, lambda exc: _missing_module_details(exc) is not None)
    missing = _missing_module_details(missing_error) if missing_error is not None else None
    if missing is not None:
        return missing

    message = _error_message(error)
    return RuntimeErrorDetails(message=message, kind="runtime", cause=message)


def format_runtime_error(error: BaseException) -> str:
    return runtime_error_details(error).message


def _module_sources(error: BaseException) -> dict[str, str]:
    raw = getattr(error, _MODULE_SOURCES_ATTR, None)
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _is_module_execution_error(error: BaseException) -> bool:
    return error.__class__.__name__ == "ModuleExecutionError" and hasattr(error, "module")


def _find_module_execution_error(error: BaseException | None, seen: set[int] | None = None) -> BaseException | None:
    if error is None:
        return None
    seen = seen or set()
    if id(error) in seen:
        return None
    seen.add(id(error))
    if _is_module_execution_error(error):
        return error
    for child in (
        getattr(error, "original", None),
        getattr(error, "last_exc", None),
        getattr(error, "__cause__", None),
        getattr(error, "__context__", None),
    ):
        if isinstance(child, BaseException):
            found = _find_module_execution_error(child, seen)
            if found is not None:
                return found
    return None


def _find_error(error: BaseException | None, predicate: Any, seen: set[int] | None = None) -> BaseException | None:
    if error is None:
        return None
    seen = seen or set()
    if id(error) in seen:
        return None
    seen.add(id(error))
    if predicate(error):
        return error
    for child in (
        getattr(error, "original", None),
        getattr(error, "last_exc", None),
        getattr(error, "__cause__", None),
        getattr(error, "__context__", None),
    ):
        if isinstance(child, BaseException):
            found = _find_error(child, predicate, seen)
            if found is not None:
                return found
    return None


def _module_execution_details(error: BaseException) -> RuntimeErrorDetails:
    chain: list[str] = []
    current: BaseException | None = error
    cause: BaseException = error
    seen: set[int] = set()
    while current is not None and _is_module_execution_error(current) and id(current) not in seen:
        seen.add(id(current))
        module = str(getattr(current, "module"))
        if not chain or chain[-1] != module:
            chain.append(module)
        original = getattr(current, "original", None)
        cause = original if isinstance(original, BaseException) else current
        current = _find_module_execution_error(cause, set(seen))

    root = _unwrap_draconic(cause)
    circular = _circular_import_details(root)
    if circular is not None:
        return circular
    line, col = _error_line_col(cause)
    if line is None:
        line, col = _error_line_col(root)
    cause_message = _error_message(root)
    kind = _kind_for_error(root)
    if kind == "runtime":
        kind = "module"
    module = chain[-1] if chain else None
    message = _format_module_message(chain, line, cause_message)
    return RuntimeErrorDetails(
        message=message,
        kind=kind,
        module=module,
        module_line=line,
        module_col=col,
        cause=cause_message,
        import_chain=chain or None,
    )


def _module_source_details(error: BaseException, module_sources: dict[str, str]) -> RuntimeErrorDetails | None:
    if not module_sources:
        return None
    source_to_modules: dict[str, list[str]] = {}
    for module, source in module_sources.items():
        source_to_modules.setdefault(source, []).append(module)

    frames = _draconic_frames(error)
    chain: list[str] = []
    deepest_error: BaseException | None = None
    for frame in frames:
        expr = getattr(frame, "expr", None)
        if not isinstance(expr, str):
            continue
        modules = source_to_modules.get(expr)
        if not modules:
            continue
        module = modules[0]
        if not chain or chain[-1] != module:
            chain.append(module)
        deepest_error = frame

    if not chain or deepest_error is None:
        return None

    root = _unwrap_draconic(error)
    if _frame_is_in_known_module(root, source_to_modules):
        deepest_error = root
    line, col = _error_line_col(deepest_error)
    cause_message = _error_message(root)
    message = _format_module_message(chain, line, cause_message)
    return RuntimeErrorDetails(
        message=message,
        kind="module" if _kind_for_error(root) == "runtime" else _kind_for_error(root),
        module=chain[-1],
        module_line=line,
        module_col=col,
        cause=cause_message,
        import_chain=chain,
    )


def _draconic_frames(error: BaseException) -> list[BaseException]:
    frames: list[BaseException] = []
    current: BaseException = error
    seen: set[int] = set()
    while isinstance(current, NestedException) and id(current) not in seen:
        seen.add(id(current))
        frames.append(current)
        current = current.last_exc
    frames.append(current)
    return frames


def _frame_is_in_known_module(error: BaseException, source_to_modules: dict[str, list[str]]) -> bool:
    expr = getattr(error, "expr", None)
    return isinstance(expr, str) and expr in source_to_modules


def _unwrap_draconic(error: BaseException) -> BaseException:
    current: BaseException = error
    seen: set[int] = set()
    while id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, NestedException):
            current = current.last_exc
            continue
        if isinstance(current, AnnotatedException):
            current = current.original
            continue
        if isinstance(current, WrappedException) and isinstance(getattr(current, "original", None), BaseException):
            current = current.original
            continue
        break
    return current


def _error_line_col(error: BaseException) -> tuple[int | None, int | None]:
    if isinstance(error, draconic.DraconicSyntaxError):
        line = error.lineno if isinstance(error.lineno, int) and error.lineno > 0 else None
        offset = error.offset if isinstance(error.offset, int) and error.offset > 0 else None
        return line, offset

    node = getattr(error, "node", None)
    if node is not None:
        node_line = getattr(node, "lineno", None)
        node_col = getattr(node, "col_offset", None)
        line = node_line if isinstance(node_line, int) and node_line > 0 else None
        col = (node_col + 1) if isinstance(node_col, int) and node_col >= 0 else None
        return line, col

    lineno = getattr(error, "lineno", None)
    offset = getattr(error, "offset", None)
    line = lineno if isinstance(lineno, int) and lineno > 0 else None
    col = offset if isinstance(offset, int) and offset > 0 else None
    return line, col


def _error_message(error: BaseException) -> str:
    if isinstance(error, draconic.DraconicException):
        return str(error.msg)
    return str(error)


def _kind_for_error(error: BaseException) -> str:
    if _circular_import_details(error) is not None:
        return "circular_import"
    if _missing_module_details(error) is not None:
        return "missing_module"
    if _is_module_execution_error(error):
        return "module"
    return "runtime"


def _format_module_message(chain: list[str], line: int | None, cause: str) -> str:
    module_path = " -> ".join(chain) if chain else "<unknown>"
    location = f" at line {line}" if line is not None else ""
    return f"Error in module {module_path}{location}: {cause}"


def _missing_module_details(error: BaseException) -> RuntimeErrorDetails | None:
    if not isinstance(error, ModuleNotFoundError):
        return None
    message = str(error)
    module_match = re.search(r"No gvar named ['\"]([^'\"]+)['\"]", message)
    module = module_match.group(1) if module_match else None
    return RuntimeErrorDetails(
        message=message,
        kind="missing_module",
        module=module,
        cause=message,
        import_chain=[module] if module else None,
    )


def _circular_import_details(error: BaseException) -> RuntimeErrorDetails | None:
    if not isinstance(error, ImportError):
        return None
    message = str(error)
    if "Circular import" not in message:
        return None
    chain_text = message.split("!\n", 1)[1] if "!\n" in message else ""
    chain = [part.strip() for part in chain_text.split(" imports\n") if part.strip()]
    pretty = f"Circular import detected: {' -> '.join(chain)}" if chain else message
    return RuntimeErrorDetails(
        message=pretty,
        kind="circular_import",
        module=chain[-1] if chain else None,
        cause=pretty,
        import_chain=chain or None,
    )
