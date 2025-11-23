from __future__ import annotations

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
        merged_vars = self._merge_character_cvars(profile.character, self._load_var_files().merge(profile.vars))
        self._gvar_resolver.seed(merged_vars.gvars)
        return ContextData(
            ctx=dict(profile.ctx),
            combat=dict(profile.combat),
            character=dict(profile.character),
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


class GVarResolver:
    def __init__(self, config: AvraeLSConfig):
        self._config = config
        self._cache: Dict[str, Any] = {}

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
            return True
        if not self._config.enable_gvar_fetch:
            return False
        if not self._config.service.token:
            log.debug("GVAR fetch skipped for %s: no token configured", key)
            return False

        base_url = self._config.service.base_url.rstrip("/")
        url = f"{base_url}/gvars/{key}"
        headers = {"Authorization": f"Bearer {self._config.service.token}"}
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers=headers)
        except Exception as exc:
            log.debug("GVAR fetch failed for %s: %s", key, exc)
            return False

        if resp.status_code != 200:
            log.debug("GVAR fetch returned %s for %s", resp.status_code, key)
            return False

        payload = resp.json()
        value = payload.get("value") or payload.get("content") or payload.get("body")
        if value is None:
            log.debug("GVAR %s payload missing value", key)
            return False
        self._cache[key] = value
        return True

    def snapshot(self) -> Dict[str, Any]:
        return dict(self._cache)

    async def refresh(self, seed: Dict[str, Any] | None = None, keys: Iterable[str] | None = None) -> Dict[str, Any]:
        self.reset(seed)
        if keys:
            for key in keys:
                await self.ensure(key)
        return self.snapshot()


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
