from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lsprotocol import types

from .runtime import _default_builtins


@dataclass
class FunctionSig:
    name: str
    params: List[str]
    doc: str = ""

    @property
    def label(self) -> str:
        params = ", ".join(self.params)
        return f"{self.name}({params})"


def load_signatures() -> Dict[str, FunctionSig]:
    sigs: dict[str, FunctionSig] = {}
    sigs.update(_builtin_sigs())
    sigs.update(_runtime_helper_sigs())
    sigs.update(_avrae_function_sigs())
    return sigs


def _builtin_sigs() -> Dict[str, FunctionSig]:
    sigs: dict[str, FunctionSig] = {}
    for name, fn in _default_builtins().items():
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            continue
        params = [p.name for p in sig.parameters.values()]
        sigs[name] = FunctionSig(name=name, params=params, doc=fn.__doc__ or "")
    return sigs


def _runtime_helper_sigs() -> Dict[str, FunctionSig]:
    helpers = {
        "get_gvar": ["address"],
        "get_svar": ["name", "default=None"],
        "get_cvar": ["name", "default=None"],
        "get_uvar": ["name", "default=None"],
        "get_uvars": [],
        "set_uvar": ["name", "value"],
        "set_uvar_nx": ["name", "value"],
        "delete_uvar": ["name"],
        "uvar_exists": ["name"],
        "exists": ["name"],
        "get": ["name", "default=None"],
        "using": ["**imports"],
        "signature": ["data=0"],
        "verify_signature": ["sig"],
        "print": ["*values"],
        "character": [],
        "combat": [],
        "argparse": ["args", "character=None", "splitter=argsplit", "parse_ephem=True"],
    }
    return {name: FunctionSig(name=name, params=params) for name, params in helpers.items()}


def _avrae_function_sigs() -> Dict[str, FunctionSig]:
    sigs: dict[str, FunctionSig] = {}
    module_path = Path(__file__).resolve().parent.parent / "avrae" / "aliasing" / "api" / "functions.py"
    if not module_path.exists():
        return sigs
    try:
        tree = ast.parse(module_path.read_text())
    except Exception:
        return sigs

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            params: list[str] = []
            defaults = list(node.args.defaults)
            default_offset = len(node.args.args) - len(defaults)
            for idx, arg in enumerate(node.args.args):
                default_val = None
                if idx >= default_offset:
                    default_node = defaults[idx - default_offset]
                    try:
                        default_val = ast.literal_eval(default_node)
                    except Exception:
                        default_val = None
                params.append(f"{arg.arg}={default_val}" if default_val is not None else arg.arg)
            doc = ast.get_docstring(node) or ""
            sigs[node.name] = FunctionSig(name=node.name, params=params, doc=doc)
    return sigs


def signature_help_for_code(code: str, line: int, character: int, sigs: Dict[str, FunctionSig]) -> Optional[types.SignatureHelp]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    target_call: ast.Call | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if hasattr(node, "lineno") and hasattr(node, "col_offset"):
                start = (node.lineno - 1, node.col_offset)
                end_line = getattr(node, "end_lineno", node.lineno) - 1
                end_col = getattr(node, "end_col_offset", node.col_offset)
                if _pos_within((line, character), start, (end_line, end_col)):
                    target_call = node
                    break

    if not target_call:
        return None

    if isinstance(target_call.func, ast.Name):
        name = target_call.func.id
    else:
        return None

    if name not in sigs:
        return None

    fsig = sigs[name]
    sig_info = types.SignatureInformation(
        label=fsig.label,
        documentation=fsig.doc,
        parameters=[types.ParameterInformation(label=p) for p in fsig.params],
    )
    active_param = min(len(target_call.args), max(len(fsig.params) - 1, 0))
    return types.SignatureHelp(signatures=[sig_info], active_signature=0, active_parameter=active_param)


def _pos_within(pos: Tuple[int, int], start: Tuple[int, int], end: Tuple[int, int]) -> bool:
    (line, col) = pos
    (sl, sc) = start
    (el, ec) = end
    if line < sl or line > el:
        return False
    if line == sl and col < sc:
        return False
    if line == el and col > ec:
        return False
    return True
