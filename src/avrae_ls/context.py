from __future__ import annotations

import asyncio
import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable

import httpx

from .config import AvraeLSConfig, ContextProfile, VarSources
from .cvars import derive_character_cvars

log = logging.getLogger(__name__)


@dataclass
class ContextData:
    ctx: Dict[str, Any] = field(default_factory=dict)
    combat: Dict[str, Any] = field(default_factory=dict)
    character: Dict[str, Any] = field(default_factory=dict)
    vars: VarSources = field(default_factory=VarSources)


class ContextBuilder:
    def __init__(self, config: AvraeLSConfig):
        self._config = config
        self._gvar_resolver = GVarResolver(config)

    @property
    def gvar_resolver(self) -> "GVarResolver":
        return self._gvar_resolver

    def build(self, profile_name: str | None = None) -> ContextData:
        profile = self._select_profile(profile_name)
        # Deep copy profile data so mutations during a run do not persist.
        profile_character = copy.deepcopy(profile.character)
        profile_combat = copy.deepcopy(profile.combat)
        profile_ctx = copy.deepcopy(profile.ctx)

        combat = self._ensure_me_combatant(profile_combat, profile_ctx.get("author"))
        merged_vars = self._merge_character_cvars(profile_character, self._load_var_files().merge(profile.vars))
        self._gvar_resolver.reset(merged_vars.gvars)
        return ContextData(
            ctx=profile_ctx,
            combat=combat,
            character=profile_character,
            vars=merged_vars,
        )

    def _select_profile(self, profile_name: str | None) -> ContextProfile:
        if profile_name and profile_name in self._config.profiles:
            return self._config.profiles[profile_name]
        if self._config.default_profile in self._config.profiles:
            return self._config.profiles[self._config.default_profile]
        return next(iter(self._config.profiles.values()))

    def _load_var_files(self) -> VarSources:
        merged = VarSources()
        for path in self._config.var_files:
            data = _read_json_file(path)
            if data is None:
                continue
            merged = merged.merge(VarSources.from_data(data))
        return merged

    def _merge_character_cvars(self, character: Dict[str, Any], vars: VarSources) -> VarSources:
        merged = vars
        char_cvars = character.get("cvars") or {}
        if char_cvars:
            merged = merged.merge(VarSources(cvars=dict(char_cvars)))

        builtin_cvars = derive_character_cvars(character)
        if builtin_cvars:
            merged = merged.merge(VarSources(cvars=builtin_cvars))
        return merged

    def _ensure_me_combatant(self, profile: Dict[str, Any], ctx_author: Dict[str, Any] | None) -> Dict[str, Any]:
        combat = dict(profile or {})
        combatants = list(combat.get("combatants") or [])
        me = combat.get("me")
        author_id = (ctx_author or {}).get("id")

        def _matches_author(combatant: Dict[str, Any]) -> bool:
            try:
                return author_id is not None and str(combatant.get("controller")) == str(author_id)
            except Exception:
                return False

        # Use an existing combatant controlled by the author if me is missing.
        if me is None:
            for existing in combatants:
                if _matches_author(existing):
                    me = existing
                    break

        # If still missing, synthesize a combatant from the character sheet.
        if me is None and profile.character:
            me = {
                "name": profile.character.get("name", "Player"),
                "id": "cmb_player",
                "controller": author_id,
                "group": None,
                "race": profile.character.get("race"),
                "monster_name": None,
                "is_hidden": False,
                "init": profile.character.get("stats", {}).get("dexterity", 10),
                "initmod": 0,
                "type": "combatant",
                "note": "Mock combatant for preview",
                "effects": [],
                "stats": profile.character.get("stats") or {},
                "levels": profile.character.get("levels") or profile.character.get("class_levels") or {},
                "skills": profile.character.get("skills") or {},
                "saves": profile.character.get("saves") or {},
                "resistances": profile.character.get("resistances") or {},
                "spellbook": profile.character.get("spellbook") or {},
                "attacks": profile.character.get("attacks") or [],
                "max_hp": profile.character.get("max_hp"),
                "hp": profile.character.get("hp"),
                "temp_hp": profile.character.get("temp_hp"),
                "ac": profile.character.get("ac"),
                "creature_type": profile.character.get("creature_type"),
            }

        if me is not None:
            combat["me"] = me
            if not any(c is me for c in combatants) and not any(_matches_author(c) for c in combatants):
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
        self._cache[str(key)] = None
        return True

    def _silent_failure_many(self, keys: Iterable[str]) -> bool:
        if not self._config.silent_gvar_fetch:
            return False
        for key in keys:
            self._cache[str(key)] = None
        return True

    def reset(self, gvars: Dict[str, Any] | None = None) -> None:
        self._cache = {}
        if gvars:
            self._cache.update({str(k): v for k, v in gvars.items()})

    def seed(self, gvars: Dict[str, Any] | None = None) -> None:
        """Merge provided gvars into the cache without dropping fetched values."""
        if not gvars:
            return
        for k, v in gvars.items():
            self._cache[str(k)] = v

    def get_local(self, key: str) -> Any:
        return self._cache.get(str(key))

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

        base_url = self._config.service.base_url.rstrip("/")
        url = f"{base_url}/customizations/gvars/{key}"
        headers = {"Authorization": str(self._config.service.token)}
        try:
            log.debug("GVAR blocking fetch %s from %s", key, url)
            with httpx.Client(timeout=5) as client:
                resp = client.get(url, headers=headers)
        except Exception as exc:
            if not self._config.silent_gvar_fetch:
                log.error("GVAR blocking fetch failed for %s: %s", key, exc)
            return self._silent_failure(key)

        if resp.status_code != 200:
            if not self._config.silent_gvar_fetch:
                log.warning(
                    "GVAR blocking fetch returned %s for %s (body: %s)",
                    resp.status_code,
                    key,
                    (resp.text or "").strip(),
                )
            return self._silent_failure(key)

        value: Any = None
        try:
            payload = resp.json()
        except Exception:
            payload = None

        if isinstance(payload, dict) and "value" in payload:
            value = payload["value"]

        if value is None:
            if not self._config.silent_gvar_fetch:
                log.error("GVAR %s payload missing value", key)
            return self._silent_failure(key)
        self._cache[key] = value
        return True

    def snapshot(self) -> Dict[str, Any]:
        return dict(self._cache)

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

        base_url = self._config.service.base_url.rstrip("/")
        url = f"{base_url}/customizations/gvars/{key}"
        headers = {"Authorization": str(self._config.service.token)}

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

        try:
            log.debug("GVAR fetching %s from %s", key, url)
            resp = await _do_request(session)
        except Exception as exc:
            if not self._config.silent_gvar_fetch:
                log.error("GVAR fetch failed for %s: %s", key, exc)
            if close_client:
                await session.aclose()
            return self._silent_failure(key)
        if close_client:
            await session.aclose()

        if resp.status_code != 200:
            if not self._config.silent_gvar_fetch:
                log.warning(
                    "GVAR fetch returned %s for %s (body: %s)",
                    resp.status_code,
                    key,
                    (resp.text or "").strip(),
                )
            return self._silent_failure(key)

        value: Any = None
        try:
            payload = resp.json()
        except Exception:
            payload = None

        if isinstance(payload, dict) and "value" in payload:
            value = payload["value"]

        log.debug("GVAR fetch parsed value for %s (type=%s)", key, type(value).__name__)

        if value is None:
            if not self._config.silent_gvar_fetch:
                log.error("GVAR %s payload missing value", key)
            return self._silent_failure(key)
        self._cache[key] = value
        return True



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
