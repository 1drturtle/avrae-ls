from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from dataclasses import dataclass, field
from functools import partial
from os.path import abspath
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Tuple, cast

import httpx

from avrae_ls.config import AvraeLSConfig, ContextProfile, VarSources
from avrae_ls.runtime.cvars import derive_character_cvars

log = logging.getLogger(__name__)
_SKIP_GVAR = object()
GVAR_VALUE_KEY = "value"
GVAR_SCRIPT_WRITABLE_KEY = "scriptWritable"
GVAR_SOURCE_PATH_KEY = "sourcePath"


class _GVarRecord(dict[str, Any]):
    """A normalized record whose source path is already canonical."""

    def copy(self) -> _GVarRecord:
        return _GVarRecord(self)


@dataclass(frozen=True)
class _GVarSource:
    path: Path
    content: str | OSError | UnicodeError | RuntimeError


def _read_gvar_source(path: Path, *, cached_contents: dict[Path, _GVarSource] | None = None) -> _GVarSource:
    canonical_path = path
    try:
        canonical_path = path.resolve()
        if cached_contents is not None and canonical_path in cached_contents:
            return cached_contents[canonical_path]
        return _GVarSource(canonical_path, canonical_path.read_text())
    except (OSError, UnicodeError, RuntimeError) as exc:
        return _GVarSource(canonical_path, exc.with_traceback(None))


def make_gvar_record(value: Any, *, script_writable: bool = False, source_path: Path | None = None) -> Dict[str, Any]:
    record = _GVarRecord({GVAR_VALUE_KEY: value, GVAR_SCRIPT_WRITABLE_KEY: bool(script_writable)})
    if source_path is not None:
        record[GVAR_SOURCE_PATH_KEY] = str(source_path.resolve())
    return record


_SILENT_GVAR_FAILURE = make_gvar_record(None)


def gvar_value(entry: Any) -> Any:
    if isinstance(entry, dict) and GVAR_VALUE_KEY in entry:
        return entry.get(GVAR_VALUE_KEY)
    return entry


def gvar_script_writable(entry: Any) -> bool:
    if isinstance(entry, dict) and GVAR_VALUE_KEY in entry:
        return bool(entry.get(GVAR_SCRIPT_WRITABLE_KEY, False))
    return False


def gvar_source_path(entry: Any) -> Path | None:
    if not isinstance(entry, dict):
        return None
    raw_path = entry.get(GVAR_SOURCE_PATH_KEY)
    if not isinstance(raw_path, str) or not raw_path:
        return None
    return Path(raw_path)


def normalize_gvar_entry(
    key: Any,
    value: Any,
    *,
    relative_to: Path | None = None,
    source_label: Path | None = None,
    load_source: Callable[[Path], _GVarSource] = _read_gvar_source,
) -> Dict[str, Any] | object:
    if isinstance(value, _GVarRecord):
        return value.copy()
    if isinstance(value, dict):
        script_writable = bool(value.get(GVAR_SCRIPT_WRITABLE_KEY, False))
        file_path = value.get("filePath")
        if file_path is None:
            file_path = value.get("path")
        if file_path is not None:
            if not isinstance(file_path, str) or not file_path.strip():
                log.warning("Invalid gvar file path for '%s' in %s; expected a non-empty string.", key, source_label)
                return _SKIP_GVAR
            gvar_path = Path(file_path)
            if not gvar_path.is_absolute() and relative_to is not None:
                gvar_path = relative_to / gvar_path
            source = load_source(gvar_path)
            if isinstance(source.content, FileNotFoundError):
                log.warning("Gvar content file not found for '%s': %s", key, gvar_path)
                return _SKIP_GVAR
            if not isinstance(source.content, str):
                log.warning("Failed to read gvar content file for '%s' (%s): %s", key, gvar_path, source.content)
                return _SKIP_GVAR
            record = make_gvar_record(source.content, script_writable=script_writable)
            record[GVAR_SOURCE_PATH_KEY] = str(source.path)
            return record
        if GVAR_VALUE_KEY in value or GVAR_SCRIPT_WRITABLE_KEY in value:
            return make_gvar_record(
                value.get(GVAR_VALUE_KEY),
                script_writable=script_writable,
                source_path=gvar_source_path(value),
            )
    return make_gvar_record(value, script_writable=False)


def normalize_gvars(
    gvars: Dict[str, Any] | None,
    *,
    relative_to: Path | None = None,
    source_label: Path | None = None,
    load_source: Callable[[Path], _GVarSource] = _read_gvar_source,
) -> Dict[str, Dict[str, Any]]:
    normalized: dict[str, Dict[str, Any]] = {}
    for key, value in (gvars or {}).items():
        parsed = normalize_gvar_entry(
            key, value, relative_to=relative_to, source_label=source_label, load_source=load_source
        )
        if parsed is _SKIP_GVAR:
            continue
        normalized[str(key)] = cast(Dict[str, Any], parsed)
    return normalized


def gvar_values_snapshot(gvars: Dict[str, Any] | None) -> Dict[str, Any]:
    return {str(key): gvar_value(value) for key, value in (gvars or {}).items()}


@dataclass
class ContextData:
    ctx: Dict[str, Any] = field(default_factory=dict)
    combat: Dict[str, Any] = field(default_factory=dict)
    character: Dict[str, Any] = field(default_factory=dict)
    vars: VarSources = field(default_factory=VarSources)
    time: float | None = None


@dataclass(frozen=True)
class _BaselineCacheEntry:
    context: ContextData
    sources: set[Path]


class ContextBuilder:
    def __init__(self, config: AvraeLSConfig):
        self._config = config
        self._gvar_resolver = GVarResolver(config)
        self._baseline_cache: dict[str, _BaselineCacheEntry] = {}
        self._manifest_signature: Tuple[tuple[str, int, int], ...] | None = None
        self._gvar_sources: dict[Path, _GVarSource] = {}
        self._gvar_contents: dict[Path, _GVarSource] = {}
        self._source_aliases: dict[Path, set[Path]] = {}

    @property
    def gvar_resolver(self) -> "GVarResolver":
        return self._gvar_resolver

    def build(self, profile_name: str | None = None) -> ContextData:
        baseline = self.build_baseline(profile_name)
        return self.build_from_baseline(baseline)

    def build_baseline(self, profile_name: str | None = None) -> ContextData:
        started = time.perf_counter()
        profile = self._select_profile(profile_name)
        cache_key = profile.name
        var_file_sig = self._var_file_signature()
        if var_file_sig != self._manifest_signature:
            self._manifest_signature = var_file_sig
            self._baseline_cache.clear()
            self._gvar_sources.clear()
            self._gvar_contents.clear()
            self._source_aliases.clear()
        cached = self._baseline_cache.get(cache_key)
        if cached is not None:
            return cached.context

        # Clone profile payload once; per-run contexts are cloned from this baseline.
        profile_character = _fast_clone(profile.character)
        profile_combat = _fast_clone(profile.combat)
        profile_ctx = _fast_clone(profile.ctx)

        combat = self._ensure_me_combatant(profile_combat, profile_character, profile_ctx.get("author"))
        sources: set[Path] = set()
        load_source = partial(self._load_gvar_source, dependencies=sources)
        merged_vars = self._merge_character_cvars(
            profile_character, self._load_var_files(load_source).merge(profile.vars)
        )
        merged_vars.gvars = normalize_gvars(
            merged_vars.gvars,
            relative_to=self._config.workspace_root,
            source_label=self._config.workspace_root,
            load_source=load_source,
        )
        baseline = ContextData(
            ctx=profile_ctx,
            combat=combat,
            character=profile_character,
            vars=merged_vars,
            time=profile.time,
        )
        self._baseline_cache[cache_key] = _BaselineCacheEntry(context=baseline, sources=sources)
        log.debug(
            "Context baseline built for profile '%s' in %.2fms",
            profile.name,
            (time.perf_counter() - started) * 1000,
        )
        return baseline

    def build_from_baseline(
        self,
        baseline: ContextData | None = None,
        profile_name: str | None = None,
    ) -> ContextData:
        started = time.perf_counter()
        if baseline is None:
            baseline = self.build_baseline(profile_name)
        vars_copy = VarSources(
            cvars=dict(baseline.vars.cvars),
            uvars=dict(baseline.vars.uvars),
            svars=dict(baseline.vars.svars),
            gvars={
                key: value.copy() if isinstance(value, _GVarRecord) else value
                for key, value in baseline.vars.gvars.items()
            },
        )
        self._gvar_resolver.reset(vars_copy.gvars)
        ctx_data = ContextData(
            ctx=_fast_clone(baseline.ctx),
            combat=_fast_clone(baseline.combat),
            character=_fast_clone(baseline.character),
            vars=vars_copy,
            time=baseline.time,
        )
        log.debug("Context clone built in %.2fms", (time.perf_counter() - started) * 1000)
        return ctx_data

    def _select_profile(self, profile_name: str | None) -> ContextProfile:
        if profile_name and profile_name in self._config.profiles:
            return self._config.profiles[profile_name]
        if self._config.default_profile in self._config.profiles:
            return self._config.profiles[self._config.default_profile]
        return next(iter(self._config.profiles.values()))

    def invalidate_var_file(self, path: Path) -> bool:
        absolute_path = abspath(path)
        if not any(absolute_path == abspath(var_file) for var_file in self._config.var_files):
            return False
        self._manifest_signature = None
        return True

    def invalidate_gvar_source(self, path: Path) -> bool:
        if not path.is_absolute():
            path = Path.cwd() / path
        source = self._gvar_sources.get(path)
        canonical_paths = {path} if source is None else {path, source.path}
        affected: set[Path] = set()
        for canonical_path in canonical_paths:
            affected.update(self._source_aliases.pop(canonical_path, set()))
            self._gvar_contents.pop(canonical_path, None)
        if not affected:
            return False

        for configured_path in affected:
            del self._gvar_sources[configured_path]
        self._baseline_cache = {
            name: entry for name, entry in self._baseline_cache.items() if entry.sources.isdisjoint(affected)
        }
        return True

    def _load_gvar_source(self, path: Path, *, dependencies: set[Path]) -> _GVarSource:
        if not path.is_absolute():
            path = Path.cwd() / path
        dependencies.add(path)
        source = self._gvar_sources.get(path)
        if source is None:
            source = _read_gvar_source(path, cached_contents=self._gvar_contents)
            self._gvar_sources[path] = source
            self._gvar_contents[source.path] = source
            self._source_aliases.setdefault(source.path, set()).add(path)
        return source

    def _load_var_files(self, load_source: Callable[[Path], _GVarSource]) -> VarSources:
        merged = VarSources()
        for path in self._config.var_files:
            data = _read_json_file(path)
            if data is None:
                continue
            parsed = VarSources.from_data(data)
            parsed.gvars = normalize_gvars(
                parsed.gvars, relative_to=path.parent, source_label=path, load_source=load_source
            )
            merged = merged.merge(parsed)
        return merged

    def _var_file_signature(self) -> Tuple[tuple[str, int, int], ...]:
        sig: list[tuple[str, int, int]] = []
        for path in self._config.var_files:
            try:
                stat = path.stat()
                sig.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                sig.append((str(path), -1, -1))
        return tuple(sig)

    def _merge_character_cvars(self, character: Dict[str, Any], vars: VarSources) -> VarSources:
        merged = vars
        char_cvars = character.get("cvars") or {}
        if char_cvars:
            merged = merged.merge(VarSources(cvars=dict(char_cvars)))

        builtin_cvars = derive_character_cvars(character)
        if builtin_cvars:
            merged = merged.merge(VarSources(cvars=builtin_cvars))
        return merged

    def _ensure_me_combatant(
        self,
        profile_combat: Dict[str, Any],
        character: Dict[str, Any],
        ctx_author: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        combat = dict(profile_combat or {})
        combatants = list(combat.get("combatants") or [])
        me = combat.get("me")
        author_id = (ctx_author or {}).get("id")

        def _matches_author(combatant: Dict[str, Any]) -> bool:
            try:
                return author_id is not None and str(combatant.get("controller")) == str(author_id)
            except Exception:
                return False

        def _same_combatant(lhs: Dict[str, Any], rhs: Dict[str, Any]) -> bool:
            lhs_id = lhs.get("id")
            rhs_id = rhs.get("id")
            if lhs_id is None or rhs_id is None:
                return False
            return str(lhs_id) == str(rhs_id)

        # Use an existing combatant controlled by the author if me is missing.
        if me is None:
            for existing in combatants:
                if _matches_author(existing):
                    me = existing
                    break

        # If still missing, synthesize a combatant from the character sheet.
        if me is None and character:
            me = {
                "name": character.get("name", "Player"),
                "id": "cmb_player",
                "controller": author_id,
                "group": None,
                "race": character.get("race"),
                "monster_name": None,
                "is_hidden": False,
                "init": character.get("stats", {}).get("dexterity", 10),
                "initmod": 0,
                "type": "combatant",
                "note": "Mock combatant for preview",
                "effects": [],
                "stats": character.get("stats") or {},
                "levels": character.get("levels") or character.get("class_levels") or {},
                "skills": character.get("skills") or {},
                "saves": character.get("saves") or {},
                "resistances": character.get("resistances") or {},
                "spellbook": character.get("spellbook") or {},
                "attacks": character.get("attacks") or [],
                "max_hp": character.get("max_hp"),
                "hp": character.get("hp"),
                "temp_hp": character.get("temp_hp"),
                "ac": character.get("ac"),
                "creature_type": character.get("creature_type"),
            }

        if me is not None:
            combat["me"] = me
            if not any((c is me) or _same_combatant(c, me) for c in combatants) and not any(
                _matches_author(c) for c in combatants
            ):
                combatants.insert(0, me)
            combat["combatants"] = combatants
            if "current" not in combat or combat.get("current") is None:
                combat["current"] = me
        else:
            combat["combatants"] = combatants

        return combat


class GVarResolver:
    _CONCURRENCY = 5

    def __init__(self, config: AvraeLSConfig):
        self._config = config
        self._cache: Dict[str, Any] = {}

    def _silent_failure(self, key: str) -> bool:
        if not self._config.silent_gvar_fetch:
            return False
        self._cache.setdefault(str(key), _SILENT_GVAR_FAILURE)
        return True

    def _silent_failure_many(self, keys: Iterable[str]) -> bool:
        if not self._config.silent_gvar_fetch:
            return False
        for key in keys:
            self._cache[str(key)] = None
        return True

    def _request_target(self, key: str) -> tuple[str, dict[str, str]]:
        base_url = self._config.service.base_url.rstrip("/")
        url = f"{base_url}/customizations/gvars/{key}"
        headers = {"Authorization": str(self._config.service.token)}
        return url, headers

    def _handle_gvar_response(self, key: str, resp: httpx.Response, *, blocking: bool) -> bool:
        prefix = "GVAR blocking fetch" if blocking else "GVAR fetch"
        if resp.status_code != 200:
            if not self._config.silent_gvar_fetch:
                log.warning(
                    "%s returned %s for %s (body: %s)",
                    prefix,
                    resp.status_code,
                    key,
                    (resp.text or "").strip(),
                )
            return self._silent_failure(key)

        value: Any = None
        script_writable = False
        try:
            payload = resp.json()
        except Exception:
            payload = None

        if isinstance(payload, dict) and "value" in payload:
            value = payload["value"]
            script_writable = bool(payload.get("script_writable", False))

        if not blocking:
            log.debug("GVAR fetch parsed value for %s (type=%s)", key, type(value).__name__)

        if value is None:
            if not self._config.silent_gvar_fetch:
                log.error("GVAR %s payload missing value", key)
            return self._silent_failure(key)
        self._cache[key] = make_gvar_record(value, script_writable=script_writable)
        return True

    def reset(self, gvars: Dict[str, Any] | None = None) -> None:
        self.load_snapshot(gvars)

    def seed(self, gvars: Dict[str, Any] | None = None) -> None:
        """Merge provided gvars into the cache without dropping fetched values."""
        if not gvars:
            return
        for k, v in gvars.items():
            normalized = normalize_gvar_entry(
                k, v, relative_to=self._config.workspace_root, source_label=self._config.workspace_root
            )
            if normalized is _SKIP_GVAR:
                continue
            self._cache[str(k)] = normalized

    def get_local(self, key: str) -> Any:
        return gvar_value(self._cache.get(str(key)))

    def discard(self, key: str) -> None:
        self._cache.pop(key, None)

    def get_record(self, key: str) -> Dict[str, Any] | None:
        entry = self._cache.get(str(key))
        if entry is None:
            return None
        normalized = normalize_gvar_entry(
            key,
            entry,
            relative_to=self._config.workspace_root,
            source_label=self._config.workspace_root,
        )
        if normalized is _SKIP_GVAR:
            return None
        return cast(Dict[str, Any], normalized)

    def is_script_writable(self, key: str) -> bool:
        return gvar_script_writable(self._cache.get(str(key)))

    def set_value(self, key: str, value: Any, *, script_writable: bool | None = None) -> Dict[str, Any]:
        record = self.get_record(key) or make_gvar_record(None, script_writable=False)
        record[GVAR_VALUE_KEY] = value
        if script_writable is not None:
            record[GVAR_SCRIPT_WRITABLE_KEY] = bool(script_writable)
        self._cache[str(key)] = record
        return record

    async def ensure(self, key: str) -> bool:
        key = str(key)
        if key in self._cache:
            log.debug("GVAR ensure cache hit for %s", key)
            return True
        return await self._fetch_remote(key)

    async def ensure_many(self, keys: Iterable[str]) -> Dict[str, bool]:
        results: dict[str, bool] = {}
        missing = [str(k) for k in keys if str(k) not in self._cache]
        for key in keys:
            results[str(key)] = str(key) in self._cache

        if not missing:
            return results
        if not self._config.enable_gvar_fetch:
            if not self._config.silent_gvar_fetch:
                log.warning("GVAR fetch disabled; skipping %s", missing)
            if self._silent_failure_many(missing):
                for key in missing:
                    results[key] = True
            return results
        if not self._config.service.token:
            if not self._config.silent_gvar_fetch:
                log.debug("GVAR fetch skipped for %s: no token configured", missing)
            if self._silent_failure_many(missing):
                for key in missing:
                    results[key] = True
            return results

        sem = asyncio.Semaphore(self._CONCURRENCY)

        async def _fetch(key: str, client: httpx.AsyncClient) -> None:
            if key in self._cache:
                results[key] = True
                return
            try:
                ensured = await self._fetch_remote(key, client=client, sem=sem)
            except Exception as exc:  # pragma: no cover - defensive
                if not self._config.silent_gvar_fetch:
                    log.error("GVAR fetch failed for %s: %s", key, exc)
                ensured = self._silent_failure(key)
            results[key] = ensured

        async with httpx.AsyncClient(timeout=5) as client:
            await asyncio.gather(*(_fetch(key, client) for key in missing))
        return results

    def ensure_blocking(self, key: str) -> bool:
        key = str(key)
        if key in self._cache:
            log.debug("GVAR ensure_blocking cache hit for %s", key)
            return True
        if not self._config.enable_gvar_fetch:
            if not self._config.silent_gvar_fetch:
                log.warning("GVAR fetch disabled; skipping %s", key)
            return self._silent_failure(key)
        if not self._config.service.token:
            if not self._config.silent_gvar_fetch:
                log.debug("GVAR fetch skipped for %s: no token configured", key)
            return self._silent_failure(key)

        url, headers = self._request_target(key)
        try:
            log.debug("GVAR blocking fetch %s from %s", key, url)
            with httpx.Client(timeout=5) as client:
                resp = client.get(url, headers=headers)
        except Exception as exc:
            if not self._config.silent_gvar_fetch:
                log.error("GVAR blocking fetch failed for %s: %s", key, exc)
            return self._silent_failure(key)
        return self._handle_gvar_response(key, resp, blocking=True)

    def snapshot(self) -> Dict[str, Any]:
        return {
            str(key): value.copy() if isinstance(value, dict) else make_gvar_record(value, script_writable=False)
            for key, value in self._cache.items()
        }

    def value_snapshot(self) -> Dict[str, Any]:
        return gvar_values_snapshot(self._cache)

    def load_snapshot(self, gvars: Dict[str, Any] | None = None) -> None:
        self._cache = {}
        if gvars:
            self.seed(gvars)

    async def refresh(self, seed: Dict[str, Any] | None = None, keys: Iterable[str] | None = None) -> Dict[str, Any]:
        self.reset(seed)
        if keys:
            await self.ensure_many(keys)
        return self.snapshot()

    async def _fetch_remote(
        self, key: str, client: httpx.AsyncClient | None = None, sem: asyncio.Semaphore | None = None
    ) -> bool:
        key = str(key)
        if key in self._cache:
            return True
        if not self._config.enable_gvar_fetch:
            return self._silent_failure(key)
        if not self._config.service.token:
            return self._silent_failure(key)

        url, headers = self._request_target(key)

        async def _do_request(session: httpx.AsyncClient) -> httpx.Response:
            if sem:
                async with sem:
                    return await session.get(url, headers=headers)
            return await session.get(url, headers=headers)

        close_client = False
        session = client
        if session is None:
            session = httpx.AsyncClient(timeout=5)
            close_client = True

        resp = None
        try:
            log.debug("GVAR fetching %s from %s", key, url)
            resp = await _do_request(session)
        except Exception as exc:
            if not self._config.silent_gvar_fetch:
                log.error("GVAR fetch failed for %s: %s", key, exc)
        finally:
            if close_client:
                await session.aclose()
        cached = self._cache.get(key)
        if cached is not None and cached is not _SILENT_GVAR_FAILURE:
            return True
        if resp is None:
            return self._silent_failure(key)
        return self._handle_gvar_response(key, resp, blocking=False)


def _read_json_file(path: Path) -> Dict[str, Any] | None:
    try:
        text = path.read_text()
    except FileNotFoundError:
        log.debug("Var file not found: %s", path)
        return None
    except OSError as exc:
        log.warning("Failed to read var file %s: %s", path, exc)
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("Failed to parse var file %s: %s", path, exc)
        return None


def _fast_clone(value: Any) -> Any:
    """Clone nested container primitives faster than copy.deepcopy for context payloads."""
    if isinstance(value, (str, int, float, bool, type(None), bytes)):
        return value
    if type(value) is dict:
        return {k: _fast_clone(v) for k, v in value.items()}
    if type(value) is list:
        return [_fast_clone(v) for v in value]
    if type(value) is tuple:
        return tuple(_fast_clone(v) for v in value)
    if type(value) is set:
        return {_fast_clone(v) for v in value}
    return copy.deepcopy(value)
