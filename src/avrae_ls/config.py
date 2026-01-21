from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

CONFIG_FILENAME = ".avraels.json"
log = logging.getLogger(__name__)
_ENV_VAR_PATTERN = re.compile(r"\$(\w+)|\$\{([^}]+)\}")


class ConfigError(Exception):
    """Raised when a workspace config file cannot be parsed."""


@dataclass
class DiagnosticSettings:
    semantic_level: str = "warning"
    runtime_level: str = "error"


@dataclass
class AvraeServiceConfig:
    base_url: str = "https://api.avrae.io"
    token: str | None = None


@dataclass
class VarSources:
    cvars: Dict[str, Any] = field(default_factory=dict)
    uvars: Dict[str, Any] = field(default_factory=dict)
    svars: Dict[str, Any] = field(default_factory=dict)
    gvars: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_data(cls, data: Dict[str, Any] | None) -> "VarSources":
        data = data or {}
        return cls(
            cvars=dict(data.get("cvars") or {}),
            uvars=dict(data.get("uvars") or {}),
            svars=dict(data.get("svars") or {}),
            gvars=dict(data.get("gvars") or {}),
        )

    def merge(self, other: "VarSources") -> "VarSources":
        def _merge(lhs: Dict[str, Any], rhs: Dict[str, Any]) -> Dict[str, Any]:
            merged = dict(lhs)
            merged.update(rhs)
            return merged

        return VarSources(
            cvars=_merge(self.cvars, other.cvars),
            uvars=_merge(self.uvars, other.uvars),
            svars=_merge(self.svars, other.svars),
            gvars=_merge(self.gvars, other.gvars),
        )

    def to_initial_names(self) -> Dict[str, Any]:
        names: Dict[str, Any] = {}
        # Bind uvars first so cvars take precedence, matching Avrae's local > cvar > uvar lookup.
        names.update(self.uvars)
        names.update(self.cvars)
        return names


@dataclass
class ContextProfile:
    name: str
    ctx: Dict[str, Any] = field(default_factory=dict)
    combat: Dict[str, Any] = field(default_factory=dict)
    character: Dict[str, Any] = field(default_factory=dict)
    vars: VarSources = field(default_factory=VarSources)
    description: str = ""


@dataclass
class AvraeLSConfig:
    workspace_root: Path
    enable_gvar_fetch: bool = False
    silent_gvar_fetch: bool = False
    service: AvraeServiceConfig = field(default_factory=AvraeServiceConfig)
    var_files: Tuple[Path, ...] = field(default_factory=tuple)
    default_profile: str = "default"
    profiles: Dict[str, ContextProfile] = field(default_factory=dict)
    diagnostics: DiagnosticSettings = field(default_factory=DiagnosticSettings)

    @classmethod
    def default(cls, workspace_root: Path) -> "AvraeLSConfig":
        abilities = {
            "strength": 16,
            "dexterity": 14,
            "constitution": 15,
            "intelligence": 10,
            "wisdom": 12,
            "charisma": 13,
            "prof_bonus": 3,
        }

        def _mod(score: int) -> int:
            return math.floor((score - 10) / 2)

        skill_profs = {"athletics", "perception", "stealth", "survival"}
        skills = {}
        for name, ability in {
            "acrobatics": "dexterity",
            "animalHandling": "wisdom",
            "arcana": "intelligence",
            "athletics": "strength",
            "deception": "charisma",
            "history": "intelligence",
            "initiative": "dexterity",
            "insight": "wisdom",
            "intimidation": "charisma",
            "investigation": "intelligence",
            "medicine": "wisdom",
            "nature": "intelligence",
            "perception": "wisdom",
            "performance": "charisma",
            "persuasion": "charisma",
            "religion": "intelligence",
            "sleightOfHand": "dexterity",
            "stealth": "dexterity",
            "survival": "wisdom",
            "strength": "strength",
            "dexterity": "dexterity",
            "constitution": "constitution",
            "intelligence": "intelligence",
            "wisdom": "wisdom",
            "charisma": "charisma",
        }.items():
            base = _mod(abilities[ability])
            prof = 1 if name in skill_profs else 0
            skills[name] = {"value": base + abilities["prof_bonus"] * prof, "prof": prof, "bonus": 0, "adv": None}

        saves = {
            "str": _mod(abilities["strength"]) + abilities["prof_bonus"],
            "dex": _mod(abilities["dexterity"]),
            "con": _mod(abilities["constitution"]) + abilities["prof_bonus"],
            "int": _mod(abilities["intelligence"]),
            "wis": _mod(abilities["wisdom"]),
            "cha": _mod(abilities["charisma"]),
        }

        attacks = [
            {
                "name": "Longsword",
                "verb": "swings",
                "proper": False,
                "activation_type": 1,
                "raw": {"name": "Longsword", "bonus": "+7", "damage": "1d8+4 slashing"},
            },
            {
                "name": "Shortbow",
                "verb": "looses",
                "proper": False,
                "activation_type": 1,
                "raw": {"name": "Shortbow", "bonus": "+6", "damage": "1d6+3 piercing"},
            },
        ]

        spellbook = {
            "dc": 14,
            "sab": 6,
            "caster_level": 5,
            "spell_mod": 3,
            "pact_slot_level": None,
            "num_pact_slots": None,
            "max_pact_slots": None,
            "slots": {1: 4, 2: 2},
            "max_slots": {1: 4, 2: 2},
            "spells": [
                {"name": "Cure Wounds", "dc": None, "sab": None, "mod": None, "prepared": True},
                {"name": "Hunter's Mark", "dc": None, "sab": None, "mod": None, "prepared": True},
                {"name": "Fire Bolt", "dc": None, "sab": None, "mod": None, "prepared": True},
            ],
        }

        consumables = {
            "Hit Dice": {
                "name": "Hit Dice",
                "value": 5,
                "max": 5,
                "min": 0,
                "reset_on": "long",
                "display_type": None,
                "reset_to": None,
                "reset_by": None,
                "title": "d10 hit dice",
                "desc": "Hit dice pool",
            },
            "Bardic Inspiration": {
                "name": "Bardic Inspiration",
                "value": 3,
                "max": 3,
                "min": 0,
                "reset_on": "long",
                "display_type": "bubble",
                "reset_to": "max",
                "reset_by": None,
                "title": None,
                "desc": None,
            },
        }

        character = {
            "name": "Aelar Wyn",
            "race": "Half-Elf",
            "background": "Outlander",
            "description": "Scout of the Emerald Enclave.",
            "image": "https://example.invalid/aelar.png",
            "owner": 1010101010,
            "upstream": "char_aelar_wyn",
            "sheet_type": "beyond",
            "creature_type": "humanoid",
            "stats": abilities,
            "levels": {"Fighter": 3, "Rogue": 2},
            "attacks": attacks,
            "actions": [
                {
                    "name": "Second Wind",
                    "activation_type": 3,
                    "activation_type_name": "BONUS_ACTION",
                    "description": "+1d10+5 hp",
                    "snippet": "+1d10+5 hp",
                },
                {
                    "name": "Action Surge",
                    "activation_type": 1,
                    "activation_type_name": "ACTION",
                    "description": "Take one additional action.",
                    "snippet": "Take one additional action.",
                },
            ],
            "skills": skills,
            "saves": saves,
            "resistances": {"resist": [{"dtype": "fire", "unless": [], "only": []}], "vuln": [], "immune": [], "neutral": []},
            "spellbook": spellbook,
            "consumables": consumables,
            "cvars": {"favorite_enemy": "goblinoids", "fighting_style": "defense"},
            "coinpurse": {"pp": 1, "gp": 47, "ep": 0, "sp": 12, "cp": 34},
            "death_saves": {"successes": 0, "fails": 0},
            "max_hp": 44,
            "hp": 38,
            "temp_hp": 3,
            "ac": 17,
            "passive_perception": 15,
            "speed": 30,
            "class_levels": {"Fighter": 3, "Rogue": 2},
            "csettings": {"compact_coins": False},
        }

        me_combatant = {
            "name": character["name"],
            "id": "cmb_aelar",
            "controller": character["owner"],
            "group": None,
            "race": character["race"],
            "monster_name": None,
            "is_hidden": False,
            "init": 18,
            "initmod": 4,
            "type": "combatant",
            "note": "On watch",
            "effects": [
                {
                    "name": "Hunter's Mark",
                    "duration": 600,
                    "remaining": 540,
                    "desc": "Mark one target; deal +1d6 damage to it.",
                    "concentration": True,
                    "combatant_name": character["name"],
                }
            ],
            "stats": character["stats"],
            "levels": character["levels"],
            "skills": character["skills"],
            "saves": character["saves"],
            "resistances": character["resistances"],
            "spellbook": character["spellbook"],
            "attacks": character["attacks"],
            "max_hp": character["max_hp"],
            "hp": character["hp"],
            "temp_hp": character["temp_hp"],
            "ac": character["ac"],
            "creature_type": character["creature_type"],
        }

        goblin = {
            "name": "Goblin Cutter",
            "id": "cmb_gob1",
            "controller": None,
            "group": "Goblins",
            "race": None,
            "monster_name": "Goblin",
            "is_hidden": False,
            "init": 12,
            "initmod": 2,
            "type": "combatant",
            "note": "",
            "effects": [],
            "stats": {"strength": 8, "dexterity": 14, "constitution": 10, "intelligence": 8, "wisdom": 10, "charisma": 8, "prof_bonus": 2},
            "levels": {},
            "skills": {"stealth": {"value": 6, "prof": 1, "bonus": 0, "adv": None}},
            "saves": {"dex": 4},
            "resistances": {"resist": [], "vuln": [], "immune": [], "neutral": []},
            "spellbook": {"spells": []},
            "attacks": [{"name": "Scimitar", "verb": "slashes", "proper": False, "activation_type": 1, "raw": {"name": "Scimitar", "bonus": "+4", "damage": "1d6+2 slashing"}}],
            "max_hp": 11,
            "hp": 11,
            "temp_hp": 0,
            "ac": 15,
            "creature_type": "humanoid",
        }

        group = {"name": "Goblins", "id": "grp_goblins", "init": 12, "type": "group", "combatants": [goblin]}

        combat = {
            "name": "Goblin Ambush",
            "round_num": 2,
            "turn_num": 15,
            "combatants": [me_combatant, goblin],
            "groups": [group],
            "me": me_combatant,
            "current": group,
            "metadata": {"scene": "Forest road", "weather": "light rain"},
        }

        ctx = {
            "guild": {"id": 123456789012345678, "name": "Fabled Realms"},
            "channel": {
                "id": 234567890123456789,
                "name": "tavern-rp",
                "topic": "Adventuring party chat",
                "category": {"id": 9876543210, "name": "Adventures"},
                "parent": None,
            },
            "author": {
                "id": 345678901234567890,
                "name": "AelarW",
                "discriminator": "0420",
                "display_name": "Aelar Wyn",
                "roles": [{"id": 4567, "name": "DM"}, {"id": 4568, "name": "Player"}],
            },
            "prefix": "!",
            "alias": "mockalias",
            "message_id": 456789012345678901,
        }

        profile = ContextProfile(
            name="default",
            ctx=ctx,
            combat=combat,
            character=character,
            description="Built-in Avrae LS mock profile with realistic sample data.",
        )
        return cls(
            workspace_root=workspace_root,
            profiles={"default": profile},
        )


def _expand_env_vars(data: Any, env: Mapping[str, str], missing_vars: set[str]) -> Any:
    if isinstance(data, dict):
        return {key: _expand_env_vars(value, env, missing_vars) for key, value in data.items()}
    if isinstance(data, list):
        return [_expand_env_vars(value, env, missing_vars) for value in data]
    if isinstance(data, str):
        def _replace(match: re.Match[str]) -> str:
            var = match.group(1) or match.group(2) or ""
            if var in env:
                return env[var]
            missing_vars.add(var)
            return ""

        return _ENV_VAR_PATTERN.sub(_replace, data)
    return data


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    value_str = str(value)
    return value_str if value_str.strip() else None


def load_config(workspace_root: Path, *, default_enable_gvar_fetch: bool = False) -> Tuple[AvraeLSConfig, Iterable[str]]:
    """Load `.avraels.json` from the workspace root, returning config and warnings."""
    path = workspace_root / CONFIG_FILENAME
    if not path.exists():
        cfg = AvraeLSConfig.default(workspace_root)
        cfg.enable_gvar_fetch = default_enable_gvar_fetch
        return cfg, []

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        warning = f"Failed to parse {CONFIG_FILENAME}: {exc}"
        log.warning(warning)
        return AvraeLSConfig.default(workspace_root), [warning]

    warnings: list[str] = []
    env_missing: set[str] = set()
    env = dict(os.environ)
    env.setdefault("workspaceRoot", str(workspace_root))
    env.setdefault("workspaceFolder", str(workspace_root))
    raw = _expand_env_vars(raw, env, env_missing)
    for var in sorted(env_missing):
        warning = f"{CONFIG_FILENAME}: environment variable '{var}' is not set; substituting an empty string."
        warnings.append(warning)
        log.warning(warning)

    enable_gvar_fetch = bool(raw.get("enableGvarFetch", default_enable_gvar_fetch))

    service_cfg = raw.get("avraeService") or {}
    service = AvraeServiceConfig(
        base_url=str(service_cfg.get("baseUrl") or AvraeServiceConfig.base_url),
        token=_coerce_optional_str(service_cfg.get("token")),
    )

    diag_cfg = raw.get("diagnostics") or {}
    diagnostics = DiagnosticSettings(
        semantic_level=str(diag_cfg.get("semanticLevel") or "warning").lower(),
        runtime_level=str(diag_cfg.get("runtimeLevel") or "error").lower(),
    )

    var_files = tuple(
        _resolve_var_file(workspace_root, file_path)
        for file_path in raw.get("varFiles", [])
        if isinstance(file_path, str)
    )

    profiles: Dict[str, ContextProfile] = {}
    raw_profiles = raw.get("profiles") or {}
    for name, data in raw_profiles.items():
        profiles[name] = ContextProfile(
            name=name,
            ctx=dict(data.get("ctx") or {}),
            combat=dict(data.get("combat") or {}),
            character=dict(data.get("character") or {}),
            vars=VarSources.from_data(data.get("vars")),
            description=str(data.get("description") or ""),
        )

    default_profile = str(raw.get("defaultProfile") or "default")
    if default_profile not in profiles and profiles:
        warnings.append(
            f"defaultProfile '{default_profile}' not found; falling back to first profile in file."
        )
        default_profile = next(iter(profiles))

    if not profiles:
        base = AvraeLSConfig.default(workspace_root)
        profiles = base.profiles
        default_profile = base.default_profile

    cfg = AvraeLSConfig(
        workspace_root=workspace_root,
        enable_gvar_fetch=enable_gvar_fetch,
        service=service,
        var_files=var_files,
        default_profile=default_profile,
        profiles=profiles,
        diagnostics=diagnostics,
    )
    return cfg, warnings


def _resolve_var_file(root: Path, file_path: str) -> Path:
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate
