from __future__ import annotations

import io
import logging
import time
import ast
import json
import random
import math
try:  # optional dependency
    import yaml
except ImportError:  # pragma: no cover - fallback when PyYAML is absent
    yaml = None  # type: ignore
from dataclasses import dataclass
from typing import Any, Dict, Set, Callable

import d20
import draconic
from draconic.interpreter import _Break, _Continue, _Return

from .context import ContextData, GVarResolver
from .config import VarSources
from .api import AliasContextAPI, CharacterAPI, CombatAPI, SimpleRollResult
from . import argparser as avrae_argparser
# Minimal stand-in for Avrae's AliasException
class AliasException(Exception):
    def __init__(self, msg, pm_user):
        super().__init__(msg)
        self.pm_user = pm_user

log = logging.getLogger(__name__)


class MockNamespace:
    """A minimal attribute-friendly namespace used for ctx/combat/character."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self._data = data or {}

    def __getattr__(self, item: str) -> Any:
        return self._data.get(item)

    def __getitem__(self, item: str) -> Any:
        return self._data.get(item)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"MockNamespace({self._data})"

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)


@dataclass
class ExecutionResult:
    stdout: str
    value: Any = None
    error: BaseException | None = None


def _roll_dice(dice: str) -> int:
    roller = d20.Roller()
    try:
        result = roller.roll(str(dice))
    except d20.RollError:
        return 0
    return result.total


def _vroll_dice(dice: str, multiply: int = 1, add: int = 0) -> SimpleRollResult | None:
    roller = d20.Roller()
    try:
        dice_ast = roller.parse(str(dice))
    except d20.RollError:
        return None

    if multiply != 1 or add != 0:
        def _scale(node):
            if isinstance(node, d20.ast.Dice):
                node.num = (node.num * multiply) + add
            return node

        dice_ast = d20.utils.tree_map(_scale, dice_ast)

    try:
        rolled = roller.roll(dice_ast)
    except d20.RollError:
        return None
    return SimpleRollResult(rolled)


def _parse_coins(args: str):
    try:
        from avrae.aliasing.api.functions import parse_coins as avrae_parse_coins
    except Exception:
        avrae_parse_coins = None

    if avrae_parse_coins:
        try:
            return avrae_parse_coins(str(args))
        except Exception:
            pass

    # Fallback: accept numeric as gp, otherwise empty mapping.
    try:
        gp = float(str(args))
        return {"pp": 0, "gp": gp, "ep": 0, "sp": 0, "cp": 0, "total": gp}
    except Exception:
        return {"pp": 0, "gp": 0, "ep": 0, "sp": 0, "cp": 0, "total": 0}


def _default_builtins() -> Dict[str, Any]:
    return {
        "len": len,
        "min": min,
        "max": max,
        "sum": sum,
        "any": any,
        "all": all,
        "abs": abs,
        "range": range,
        "enumerate": enumerate,
        "sorted": sorted,
        "reversed": reversed,
        "int": int,
        "float": float,
        "str": str,
        "bool": bool,
        "round": round,
        "ceil": math.ceil,
        "floor": math.floor,
        "sqrt": math.sqrt,
        "time": time.time,
        "roll": _roll_dice,
        "vroll": _vroll_dice,
        "rand": random.random,
        "randint": random.randrange,
        "randchoice": random.choice,
        "randchoices": random.choices,
        "typeof": lambda inst: type(inst).__name__,
        "parse_coins": _parse_coins,
        "load_json": lambda s: json.loads(str(s)),
        "dump_json": lambda obj: json.dumps(obj),
        "load_yaml": lambda s: yaml.safe_load(str(s)) if yaml else None,
        "dump_yaml": (
            (lambda obj, indent=2: yaml.safe_dump(obj, indent=indent, sort_keys=False)) if yaml else (lambda obj, indent=2: str(obj))
        ),
    }


class MockExecutor:
    def __init__(self):
        self._base_builtins = _default_builtins()

    def available_names(self, ctx_data: ContextData) -> Set[str]:
        builtin_names = set(self._base_builtins.keys())
        runtime_names = {
            "ctx",
            "combat",
            "character",
            "roll",
            "vroll",
            "rand",
            "randint",
            "randchoice",
            "randchoices",
            "typeof",
            "parse_coins",
            "load_json",
            "dump_json",
            "load_yaml",
            "dump_yaml",
            "get_gvar",
            "get_svar",
            "get_cvar",
            "get_uvar",
            "get_uvars",
            "set_uvar",
            "set_uvar_nx",
            "delete_uvar",
            "uvar_exists",
            "print",
            "argparse",
            "err",
            "exists",
            "get",
            "using",
            "signature",
            "verify_signature",
        }
        variable_names = set(ctx_data.vars.to_initial_names().keys())
        return builtin_names | runtime_names | variable_names

    async def run(
        self,
        code: str,
        ctx_data: ContextData,
        gvar_resolver: GVarResolver | None = None,
    ) -> ExecutionResult:
        buffer = io.StringIO()
        resolver = gvar_resolver
        interpreter_ref: dict[str, draconic.DraconicInterpreter | None] = {"interpreter": None}
        runtime_character: CharacterAPI | None = None

        def _character_provider() -> CharacterAPI:
            nonlocal runtime_character
            interp = interpreter_ref["interpreter"]
            if runtime_character is None and interp is not None:
                runtime_character = _RuntimeCharacter(ctx_data.character, ctx_data.vars, interp)
            return runtime_character  # type: ignore[return-value]

        builtins = self._build_builtins(
            ctx_data,
            resolver,
            buffer,
            character_provider=_character_provider,
            interpreter_ref=interpreter_ref,
        )
        interpreter = draconic.DraconicInterpreter(
            builtins=builtins,
            initial_names=ctx_data.vars.to_initial_names(),
        )
        interpreter_ref["interpreter"] = interpreter

        value = None
        error: BaseException | None = None
        code_to_run = code
        try:
            parsed = interpreter.parse(code_to_run)
        except BaseException:
            wrapped, _ = _wrap_draconic(code_to_run)
            code_to_run = wrapped
            try:
                parsed = interpreter.parse(code_to_run)
            except BaseException as exc:
                error = exc
                log.debug("Mock execution error: %s", exc, exc_info=exc)
                return ExecutionResult(stdout=buffer.getvalue(), value=value, error=error)

        if resolver:
            await _ensure_literal_gvars(code_to_run, resolver)

        try:
            interpreter._preflight()
            value = self._exec_with_value(interpreter, parsed)
        except BaseException as exc:  # draconic raises BaseException subclasses
            error = exc
            log.debug("Mock execution error: %s", exc, exc_info=exc)
        return ExecutionResult(stdout=buffer.getvalue(), value=value, error=error)

    def _build_builtins(
        self,
        ctx_data: ContextData,
        resolver: GVarResolver | None,
        buffer: io.StringIO,
        character_provider: Callable[[], CharacterAPI] | None = None,
        interpreter_ref: Dict[str, draconic.DraconicInterpreter | None] | None = None,
    ) -> Dict[str, Any]:
        builtins = dict(self._base_builtins)
        var_store = ctx_data.vars
        interpreter_ref = interpreter_ref or {"interpreter": None}

        def _print(*args, sep=" ", end="\n"):
            buffer.write(sep.join(map(str, args)) + end)

        def _get_gvar(address: str):
            if resolver is None:
                return None
            return resolver.get_local(address)

        def _get_svar(name: str, default=None):
            return var_store.svars.get(str(name), default)

        def _get_cvar(name: str, default=None):
            return var_store.cvars.get(str(name), default)

        def _get_uvar(name: str, default=None):
            return var_store.uvars.get(str(name), default)

        def _get_uvars():
            return dict(var_store.uvars)

        def _set_uvar(name: str, value: Any):
            var_store.uvars[str(name)] = value
            return value

        def _set_uvar_nx(name: str, value: Any):
            key = str(name)
            if key not in var_store.uvars:
                var_store.uvars[key] = value
            return var_store.uvars[key]

        def _delete_uvar(name: str):
            return var_store.uvars.pop(str(name), None)

        def _uvar_exists(name: str) -> bool:
            return str(name) in var_store.uvars

        def _exists(name: str) -> bool:
            interp = interpreter_ref.get("interpreter")
            if interp is None:
                return False
            return str(name) in getattr(interp, "_names", {})

        def _get(name: str, default=None):
            interp = interpreter_ref.get("interpreter")
            if interp is None:
                return default
            return getattr(interp, "_names", {}).get(str(name), default)

        def _using(**imports):
            interp = interpreter_ref.get("interpreter")
            if interp is None:
                return None
            for ns, addr in imports.items():
                val = None
                if resolver:
                    val = resolver.get_local(addr)
                interp._names[str(ns)] = val  # type: ignore[attr-defined]
            return None

        def _signature(data=0):
            return f"signature:{int(data)}"

        def _verify_signature(sig):
            try:
                return {"signature": str(sig), "valid": True}
            except Exception:
                return {"signature": None, "valid": False}

        def _argparse(args, character=None, splitter=avrae_argparser.argsplit, parse_ephem=True):
            return avrae_argparser.argparse(args, character=character, splitter=splitter, parse_ephem=parse_ephem)

        def _err(reason, pm_user: bool = False):
            raise AliasException(str(reason), pm_user)

        ns_ctx = AliasContextAPI(ctx_data.ctx)
        ns_combat = CombatAPI(ctx_data.combat)
        if character_provider:
            character_fn = character_provider
        else:
            ns_character = CharacterAPI(ctx_data.character)

            def character_fn():
                return ns_character

        builtins.update(
            print=_print,
            roll=_roll_dice,
            vroll=_vroll_dice,
            ctx=ns_ctx,
            combat=lambda: ns_combat,
            character=lambda: character_fn(),
            get_gvar=_get_gvar,
            get_svar=_get_svar,
            get_cvar=_get_cvar,
            get_uvar=_get_uvar,
            get_uvars=_get_uvars,
            set_uvar=_set_uvar,
            set_uvar_nx=_set_uvar_nx,
            delete_uvar=_delete_uvar,
            uvar_exists=_uvar_exists,
            argparse=_argparse,
            err=_err,
            exists=_exists,
            get=_get,
            using=_using,
            signature=_signature,
            verify_signature=_verify_signature,
        )
        return builtins

    def _exec_with_value(self, interpreter: draconic.DraconicInterpreter, body) -> Any:
        last_val = None
        for expression in body:
            retval = interpreter._eval(expression)  # type: ignore[attr-defined]
            if isinstance(retval, (_Break, _Continue)):
                raise draconic.DraconicSyntaxError.from_node(retval.node, msg="Loop control outside loop", expr=interpreter._expr)  # type: ignore[attr-defined]
            if isinstance(retval, _Return):
                return retval.value
            last_val = retval
        return last_val


class _RuntimeCharacter(CharacterAPI):
    """Character wrapper that keeps mock runtime bindings in sync with cvar mutations."""

    def __init__(self, data: Dict[str, Any], var_store: VarSources, interpreter: draconic.DraconicInterpreter):
        super().__init__(data)
        self._var_store = var_store
        self._interpreter = interpreter

    def set_cvar(self, name: str, val: Any) -> Any:
        bound_val = super().set_cvar(name, val)
        key = str(name)
        self._var_store.cvars[key] = bound_val
        try:
            # Mirror Avrae behavior: new cvars are available as locals immediately.
            self._interpreter._names[key] = bound_val  # type: ignore[attr-defined]
        except Exception:
            pass
        return bound_val

    def set_cvar_nx(self, name: str, val: Any) -> Any:
        key = str(name)
        if key in self._var_store.cvars:
            return self._var_store.cvars[key]
        return self.set_cvar(key, val)

    # delete_cvar intentionally does not unbind runtime names, matching Avrae's docs.


def _wrap_draconic(code: str) -> tuple[str, int]:
    indented = "\n".join(f"    {line}" for line in code.splitlines())
    wrapped = f"def __alias_main__():\n{indented}\n__alias_main__()"
    return wrapped, 1


async def _ensure_literal_gvars(code: str, resolver: GVarResolver) -> None:
    for key in _literal_gvars(code):
        try:
            await resolver.ensure(key)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("Failed to prefetch gvar %s: %s", key, exc)


def _literal_gvars(code: str) -> Set[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        wrapped, _ = _wrap_draconic(code)
        try:
            tree = ast.parse(wrapped)
        except SyntaxError:
            return set()

    gvars: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "get_gvar":
            if not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                gvars.add(arg.value)
            elif isinstance(arg, ast.Str):
                gvars.add(arg.s)
    return gvars
