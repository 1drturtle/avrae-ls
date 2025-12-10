from __future__ import annotations

import ast
import io
import json
import logging
import math
import random
import re
import time
from types import SimpleNamespace
from collections import UserList
import yaml
from dataclasses import dataclass
from typing import Any, Dict, Set, Callable

import d20
import draconic
import httpx
from draconic.interpreter import _Break, _Continue, _Return

from .context import ContextData, GVarResolver
from .config import AvraeServiceConfig, VarSources
from .api import AliasContextAPI, CharacterAPI, SimpleCombat, SimpleRollResult
from . import argparser as avrae_argparser

_VERIFY_SIGNATURE_TIMEOUT = 5.0
_VERIFY_SIGNATURE_RETRIES = 0
# Minimal stand-in for Avrae's AliasException
class AliasException(Exception):
    def __init__(self, msg, pm_user):
        super().__init__(msg)
        self.pm_user = pm_user


try:
    from avrae.aliasing.errors import FunctionRequiresCharacter  # type: ignore
except Exception:  # pragma: no cover - fallback when avrae is unavailable
    class FunctionRequiresCharacter(Exception):
        def __init__(self, msg: str | None = None):
            super().__init__(msg or "This alias requires an active character.")

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


class ModuleExecutionError(Exception):
    def __init__(self, module: str, original: BaseException):
        super().__init__(f"Error in module {module}: {original}")
        self.module = module
        self.original = original


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


def _load_yaml(data: Any) -> Any:
    """Parse YAML, accepting JSON."""
    if data is None:
        return None
    if isinstance(data, (dict, list, tuple, set)):
        return data
    text = str(data)
    return yaml.safe_load(text)


def _yaml_dumper():
    """Create a dumper that knows how to serialize draconic SafeList/SafeDict/SafeSet."""
    class DraconicDumper(yaml.SafeDumper):
        pass

    def _represent_user_list(dumper: yaml.Dumper, data: UserList):
        return dumper.represent_sequence(dumper.DEFAULT_SEQUENCE_TAG, list(data))

    def _represent_dict(dumper: yaml.Dumper, data: dict):
        return dumper.represent_dict(dict(data))

    def _represent_set(dumper: yaml.Dumper, data: set):
        return dumper.represent_sequence(dumper.DEFAULT_SEQUENCE_TAG, list(data))

    DraconicDumper.add_multi_representer(UserList, _represent_user_list)
    DraconicDumper.add_multi_representer(dict, _represent_dict)
    DraconicDumper.add_multi_representer(set, _represent_set)
    return DraconicDumper


_YAML_DUMPER = _yaml_dumper()


def _dump_yaml(obj: Any, indent: int = 2) -> str:
    return yaml.dump(obj, Dumper=_YAML_DUMPER, default_flow_style=False, indent=indent, sort_keys=False)


def _json_default(obj: Any) -> Any:
    """Coerce SafeList/SafeDict or other iterables into JSON-serializable forms."""
    try:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if isinstance(obj, dict):
            return dict(obj)
        if isinstance(obj, (list, tuple, set)):
            return list(obj)
        if hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
            return list(obj)
    except Exception:
        pass
    return str(obj)


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


@dataclass
class _CoinsArgs:
    pp: int = 0
    gp: int = 0
    ep: int = 0
    sp: int = 0
    cp: int = 0
    explicit: bool = False

    @property
    def total(self) -> float:
        return (self.pp * 10) + self.gp + (self.ep * 0.5) + (self.sp * 0.1) + (self.cp * 0.01)


def _parse_coin_args(args: str) -> _CoinsArgs:
    cleaned = str(args).replace(",", "")
    try:
        return _parse_coin_args_float(float(cleaned))
    except ValueError:
        return _parse_coin_args_re(cleaned)


def _parse_coin_args_float(coins: float) -> _CoinsArgs:
    total_copper = int(round(coins * 100, 1))
    if coins < 0:
        return _CoinsArgs(cp=total_copper)
    return _CoinsArgs(
        gp=total_copper // 100,
        sp=(total_copper % 100) // 10,
        cp=total_copper % 10,
    )


def _parse_coin_args_re(args: str) -> _CoinsArgs:
    is_valid = re.fullmatch(r"(([+-]?\d+)\s*([pgesc]p)\s*)+", args, re.IGNORECASE)
    if not is_valid:
        raise avrae_argparser.InvalidArgument(
            "Coins must be a number or a currency string, e.g. `+101.2` or `10cp +101gp -2sp`."
        )

    out = _CoinsArgs(explicit=True)
    for coin_match in re.finditer(r"(?P<amount>[+-]?\d+)\s*(?P<currency>[pgesc]p)", args, re.IGNORECASE):
        amount = int(coin_match["amount"])
        currency = coin_match["currency"].lower()

        if currency == "pp":
            out.pp += amount
        elif currency == "gp":
            out.gp += amount
        elif currency == "ep":
            out.ep += amount
        elif currency == "sp":
            out.sp += amount
        else:
            out.cp += amount

    return out


def _parse_coins(args: str, include_total: bool = True):
    try:
        from avrae.aliasing.api.functions import parse_coins as avrae_parse_coins
    except Exception:
        avrae_parse_coins = None

    if avrae_parse_coins:
        try:
            return avrae_parse_coins(str(args), include_total=include_total)
        except Exception:
            pass

    coin_args = _parse_coin_args(str(args))
    parsed = {
        "pp": coin_args.pp,
        "gp": coin_args.gp,
        "ep": coin_args.ep,
        "sp": coin_args.sp,
        "cp": coin_args.cp,
    }
    if include_total:
        parsed["total"] = coin_args.total
    return parsed


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
        "int": int,
        "float": float,
        "str": str,
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
        "dump_json": lambda obj: json.dumps(obj, default=_json_default),
        "load_yaml": _load_yaml,
        "dump_yaml": _dump_yaml,
    }


class MockExecutor:
    def __init__(self, service_config: AvraeServiceConfig | None = None):
        self._base_builtins = _default_builtins()
        self._service_config = service_config or AvraeServiceConfig()

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
            if not ctx_data.character:
                raise FunctionRequiresCharacter()
            if runtime_character is None and interp is not None:
                runtime_character = _RuntimeCharacter(ctx_data.character, ctx_data.vars, interp)
            if runtime_character is None:
                runtime_character = CharacterAPI(ctx_data.character)
            return runtime_character  # type: ignore[return-value]

        import_cache: dict[str, SimpleNamespace] = {}
        import_stack: list[str] = []
        builtins = self._build_builtins(
            ctx_data,
            resolver,
            buffer,
            character_provider=_character_provider,
            interpreter_ref=interpreter_ref,
            import_cache=import_cache,
            import_stack=import_stack,
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
            await _ensure_nested_gvars(resolver)
            await _ensure_nested_gvars(resolver)

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
        import_cache: Dict[str, SimpleNamespace] | None = None,
        import_stack: list[str] | None = None,
    ) -> Dict[str, Any]:
        builtins = dict(self._base_builtins)
        var_store = ctx_data.vars
        interpreter_ref = interpreter_ref or {"interpreter": None}
        import_cache = import_cache or {}
        import_stack = import_stack or []
        service_cfg = self._service_config
        verify_cache_sig: str | None = None
        verify_cache_result: Dict[str, Any] | None = None
        verify_cache_error: ValueError | None = None

        def _print(*args, sep=" ", end="\n"):
            buffer.write(sep.join(map(str, args)) + end)

        def _get_gvar(address: str):
            if resolver is None:
                return None
            return resolver.get_local(address)

        def _get_svar(name: str, default=None):
            return var_store.svars.get(str(name), default)

        def _get_uvar(name: str, default=None):
            val = var_store.uvars.get(str(name), default)
            return str(val) if val is not None else default

        def _get_uvars():
            return {k: (str(v) if v is not None else v) for k, v in var_store.uvars.items()}

        def _set_uvar(name: str, value: Any):
            str_val = str(value) if value is not None else None
            var_store.uvars[str(name)] = str_val
            return str_val

        def _set_uvar_nx(name: str, value: Any):
            key = str(name)
            if key not in var_store.uvars:
                var_store.uvars[key] = str(value) if value is not None else None
            return var_store.uvars[key]

        def _delete_uvar(name: str):
            return var_store.uvars.pop(str(name), None)

        def _uvar_exists(name: str) -> bool:
            return str(name) in var_store.uvars

        def _resolve_name(key: str) -> tuple[bool, Any]:
            key = str(key)
            interp = interpreter_ref.get("interpreter")
            if interp is not None:
                names = getattr(interp, "_names", {})
                if key in names:
                    return True, names[key]

            if key in var_store.cvars:
                return True, var_store.cvars[key]

            if key in var_store.uvars:
                return True, var_store.uvars[key]

            return False, None

        def _exists(name: str) -> bool:
            found, _ = _resolve_name(name)
            return found

        def _get(name: str, default=None):
            found, value = _resolve_name(name)
            return value if found else default

        def _using(**imports):
            interp = interpreter_ref.get("interpreter")
            if interp is None:
                return None
            user_ns = getattr(interp, "_names", {})

            def _load_module(addr: str) -> SimpleNamespace:
                if addr in import_cache:
                    return import_cache[addr]
                if resolver is None:
                    raise ModuleNotFoundError(f"No gvar named {addr!r}")
                mod_contents = resolver.get_local(addr)
                if mod_contents is None:
                    try:
                        resolver.ensure_blocking(addr)
                    except Exception as exc:  # pragma: no cover - defensive
                        log.debug("Blocking gvar fetch failed for %s: %s", addr, exc)
                    mod_contents = resolver.get_local(addr)
                if mod_contents is None:
                    raise ModuleNotFoundError(f"No gvar named {addr!r}")

                old_names = getattr(interp, "_names", {})
                depth_increased = False
                try:
                    interp._names = {}
                    interp._depth += 1
                    depth_increased = True
                    if interp._depth > interp._config.max_recursion_depth:
                        raise RecursionError("Maximum recursion depth exceeded")
                    try:
                        interp.execute_module(str(mod_contents), module_name=addr)
                    except Exception as exc:
                        raise ModuleExecutionError(addr, exc) from exc
                    mod_ns = SimpleNamespace(**getattr(interp, "_names", {}))
                    import_cache[addr] = mod_ns
                    return mod_ns
                finally:
                    if depth_increased:
                        interp._depth -= 1
                    interp._names = old_names

            for ns, addr in imports.items():
                addr_str = str(addr)
                if addr_str in import_stack:
                    circle = " imports\n".join(import_stack)
                    raise ImportError(f"Circular import detected!\n{circle} imports\n{addr_str}")
                import_stack.append(addr_str)
                try:
                    mod_ns = _load_module(addr_str)
                finally:
                    import_stack.pop()
                name = str(ns)
                if name in interp.builtins:
                    raise ValueError(f"{name} is already builtin (no shadow assignments).")
                user_ns[name] = mod_ns

            interp._names = user_ns
            return None

        def _signature(data=0):
            try:
                data = int(data)
            except ValueError:
                raise TypeError(f"Data {data} could not be converted to integer.")
            return f"mock-signature:{int(data)}"

        def _verify_signature(sig):
            nonlocal verify_cache_sig, verify_cache_result, verify_cache_error
            sig_str = str(sig)
            if sig_str == verify_cache_sig:
                if verify_cache_error:
                    raise verify_cache_error
                return verify_cache_result

            verify_cache_sig = sig_str
            verify_cache_error = None
            verify_cache_result = None
            timeout = float(_VERIFY_SIGNATURE_TIMEOUT)
            retries = max(0, _VERIFY_SIGNATURE_RETRIES)

            def _call_verify_api(signature: str) -> Dict[str, Any]:
                base_url = (service_cfg.base_url if service_cfg else AvraeServiceConfig.base_url).rstrip("/")
                url = f"{base_url}/bot/signature/verify"
                headers = {"Content-Type": "application/json"}
                if service_cfg and service_cfg.token:
                    headers["Authorization"] = str(service_cfg.token)
                last_exc: Exception | None = None
                for attempt in range(retries + 1):
                    try:
                        resp = httpx.post(url, json={"signature": signature}, headers=headers, timeout=timeout)
                        break
                    except Exception as exc:
                        last_exc = exc
                        if attempt >= retries:
                            raise ValueError(f"Failed to verify signature: {exc}") from exc
                        continue
                else:  # pragma: no cover - defensive
                    raise ValueError(f"Failed to verify signature: {last_exc}") from last_exc

                try:
                    payload = resp.json()
                except Exception as exc:
                    raise ValueError("Failed to verify signature: invalid response body") from exc

                if resp.status_code != 200:
                    message = None
                    if isinstance(payload, dict):
                        message = payload.get("error") or payload.get("message")
                    detail = f"{message} (HTTP {resp.status_code})" if message else f"HTTP {resp.status_code}"
                    raise ValueError(f"Failed to verify signature: {detail}")

                if not isinstance(payload, dict):
                    raise ValueError("Failed to verify signature: invalid response")
                if payload.get("success") is not True:
                    message = payload.get("error")
                    raise ValueError(f"Failed to verify signature: {message or 'unsuccessful response'}")

                data = payload.get("data")
                if not isinstance(data, dict):
                    raise ValueError("Failed to verify signature: malformed response")
                return data

            try:
                verify_cache_result = _call_verify_api(sig_str)
            except ValueError as exc:
                verify_cache_error = exc
                raise
            return verify_cache_result

        def _argparse(args, character=None, splitter=avrae_argparser.argsplit, parse_ephem=True):
            return avrae_argparser.argparse(args, character=character, splitter=splitter, parse_ephem=parse_ephem)

        def _err(reason, pm_user: bool = False):
            raise AliasException(str(reason), pm_user)

        ns_ctx = AliasContextAPI(ctx_data.ctx)
        ns_combat = SimpleCombat(ctx_data.combat) if ctx_data.combat else None
        if character_provider:
            character_fn = character_provider
        else:
            ns_character = CharacterAPI(ctx_data.character) if ctx_data.character else None

            def character_fn():
                if ns_character is None:
                    raise FunctionRequiresCharacter()
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
    keys = _literal_gvars(code)
    try:
        await resolver.ensure_many(keys)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Failed to prefetch gvars %s: %s", keys, exc)


async def _ensure_nested_gvars(resolver: GVarResolver) -> None:
    """Recursively prefetch gvars referenced by already-fetched gvar modules."""
    visited: set[str] = set()
    while True:
        snapshot = resolver.snapshot()
        newly_fetched: set[str] = set()
        for key, value in snapshot.items():
            if key in visited:
                continue
            visited.add(key)
            if not isinstance(value, str):
                continue
            for nested in _literal_gvars(value):
                if nested in snapshot or nested in newly_fetched:
                    continue
                try:
                    results = await resolver.ensure_many([nested])
                except Exception as exc:  # pragma: no cover - defensive
                    log.debug("Failed to prefetch nested gvar %s: %s", nested, exc)
                    continue
                if results.get(nested):
                    newly_fetched.add(nested)
        if not newly_fetched:
            break


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
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "get_gvar":
                if not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    gvars.add(arg.value)
                elif isinstance(arg, ast.Str):
                    gvars.add(arg.s)
            elif node.func.id == "using":
                for kw in node.keywords:
                    val = kw.value
                    if isinstance(val, ast.Constant) and isinstance(val.value, str):
                        gvars.add(val.value)
                    elif isinstance(val, ast.Str):
                        gvars.add(val.s)
    return gvars
