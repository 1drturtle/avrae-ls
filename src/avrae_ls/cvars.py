from __future__ import annotations

import math
from typing import Any, Dict, Mapping

ABILITY_KEYS = ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]
SAVE_KEYS = {
    "strength": "str",
    "dexterity": "dex",
    "constitution": "con",
    "intelligence": "int",
    "wisdom": "wis",
    "charisma": "cha",
}


def derive_character_cvars(character: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the documented cvar table values from a character payload."""
    stats = character.get("stats") or {}
    saves = character.get("saves") or {}
    levels = character.get("levels") or {}
    spellbook = character.get("spellbook") or {}
    csettings = character.get("csettings") or {}

    cvars: dict[str, Any] = {}

    for ability in ABILITY_KEYS:
        score = _int_or_none(stats.get(ability))
        save_val = _int_or_none(saves.get(SAVE_KEYS[ability]))

        if score is not None:
            cvars[ability] = score
            cvars[f"{ability}Mod"] = math.floor((score - 10) / 2)
        if save_val is not None:
            cvars[f"{ability}Save"] = save_val

    armor = _int_or_none(character.get("ac"))
    if armor is not None:
        cvars["armor"] = armor

    description = character.get("description")
    if description is not None:
        cvars["description"] = description

    image = character.get("image")
    if image is not None:
        cvars["image"] = image

    name = character.get("name")
    if name is not None:
        cvars["name"] = name

    max_hp = _int_or_none(character.get("max_hp"))
    if max_hp is not None:
        cvars["hp"] = max_hp

    color = _color_hex(csettings.get("color"))
    if color is not None:
        cvars["color"] = color

    prof = _int_or_none(stats.get("prof_bonus"))
    if prof is not None:
        cvars["proficiencyBonus"] = prof

    spell_mod = _spell_mod(spellbook, prof)
    if spell_mod is not None:
        cvars["spell"] = spell_mod

    total_level = _sum_ints(levels.values())
    if total_level is not None:
        cvars["level"] = total_level

    for cls, lvl in levels.items():
        lvl_int = _int_or_none(lvl)
        if lvl_int is None:
            continue
        cvars[f"{str(cls).replace(' ', '')}Level"] = lvl_int

    return cvars


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, dict) and "value" in value:
        value = value.get("value")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _spell_mod(spellbook: Mapping[str, Any], prof_bonus: int | None) -> int | None:
    if "spell_mod" in spellbook:
        return _int_or_none(spellbook.get("spell_mod"))
    if "sab" in spellbook and prof_bonus is not None:
        sab = _int_or_none(spellbook.get("sab"))
        if sab is not None:
            return sab - prof_bonus
    return None


def _sum_ints(values: Any) -> int | None:
    try:
        total = sum(int(v) for v in values)
    except Exception:
        return None
    return total


def _color_hex(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return hex(int(value))[2:]
    except (TypeError, ValueError):
        return None
