from __future__ import annotations

import inspect
import re
import typing
from dataclasses import dataclass
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Iterable, List

from .argparser import ParsedArguments
from .api import (
    AliasAction,
    AliasAttack,
    AliasAttackList,
    AliasBaseStats,
    AliasCoinpurse,
    AliasContextAPI,
    AliasCustomCounter,
    AliasDeathSaves,
    AliasResistances,
    AliasSaves,
    AliasSkill,
    AliasSkills,
    AliasSpellbook,
    AliasSpellbookSpell,
    AliasLevels,
    AuthorAPI,
    CategoryAPI,
    ChannelAPI,
    CharacterAPI,
    GuildAPI,
    RoleAPI,
    SimpleCombat,
    SimpleCombatant,
    SimpleEffect,
    SimpleGroup,
    SimpleRollResult,
)


class _BuiltinList:
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = [
        "append",
        "extend",
        "insert",
        "remove",
        "pop",
        "clear",
        "index",
        "count",
        "sort",
        "reverse",
        "copy",
    ]

    def __iter__(self) -> Iterable[Any]:
        return iter([])

    def append(self, value: Any) -> None: ...
    def extend(self, iterable: Iterable[Any]) -> None: ...
    def insert(self, index: int, value: Any) -> None: ...
    def remove(self, value: Any) -> None: ...
    def pop(self, index: int = -1) -> Any: ...
    def clear(self) -> None: ...
    def index(self, value: Any, start: int = 0, stop: int | None = None) -> int: ...
    def count(self, value: Any) -> int: ...
    def sort(self, *, key=None, reverse: bool = False) -> None: ...
    def reverse(self) -> None: ...
    def copy(self) -> list[Any]: ...


class _BuiltinDict:
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = [
        "get",
        "keys",
        "values",
        "items",
        "pop",
        "popitem",
        "update",
        "setdefault",
        "clear",
        "copy",
    ]

    def __iter__(self) -> Iterable[Any]:
        return iter({})

    def get(self, key: Any, default: Any = None) -> Any: ...
    def keys(self) -> Any: ...
    def values(self) -> Any: ...
    def items(self) -> Any: ...
    def pop(self, key: Any, default: Any = None) -> Any: ...
    def popitem(self) -> tuple[Any, Any]: ...
    def update(self, *args, **kwargs) -> None: ...
    def setdefault(self, key: Any, default: Any = None) -> Any: ...
    def clear(self) -> None: ...
    def copy(self) -> dict[Any, Any]: ...


class _BuiltinStr:
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = [
        "lower",
        "upper",
        "title",
        "split",
        "join",
        "replace",
        "strip",
        "startswith",
        "endswith",
        "format",
    ]

    def __iter__(self) -> Iterable[str]:
        return iter("")

    def lower(self) -> str: ...
    def upper(self) -> str: ...
    def title(self) -> str: ...
    def split(self, sep: str | None = None, maxsplit: int = -1) -> list[str]: ...
    def join(self, iterable: Iterable[str]) -> str: ...
    def replace(self, old: str, new: str, count: int = -1) -> str: ...
    def strip(self, chars: str | None = None) -> str: ...
    def startswith(self, prefix, start: int = 0, end: int | None = None) -> bool: ...
    def endswith(self, suffix, start: int = 0, end: int | None = None) -> bool: ...
    def format(self, *args, **kwargs) -> str: ...


TypeResolver = Callable[[str | None], str | None]


@dataclass(frozen=True)
class TypeEntry:
    cls: type
    resolver: TypeResolver | None = None


@dataclass(frozen=True)
class TypeSpec:
    name: str
    cls: type
    parents: tuple[str, ...] = ()
    safe_methods: tuple[str, ...] = ()


@dataclass
class AttrMeta:
    doc: str = ""
    type_name: str = ""
    element_type: str = ""


@dataclass
class MethodMeta:
    signature: str = ""
    doc: str = ""


@dataclass
class TypeMeta:
    attrs: Dict[str, AttrMeta]
    methods: Dict[str, MethodMeta]
    element_type: str = ""


def _allow_from(type_key: str, *receiver_types: str) -> TypeResolver:
    allowed = set(receiver_types)

    def _resolver(receiver_type: str | None) -> str | None:
        if receiver_type in allowed:
            return type_key
        return None

    return _resolver


TYPE_SPECS: list[TypeSpec] = [
    TypeSpec("character", CharacterAPI, safe_methods=("get_cvar", "get_cc")),
    TypeSpec("combat", SimpleCombat, safe_methods=("get_combatant", "get_group", "get_metadata")),
    TypeSpec("SimpleCombat", SimpleCombat, safe_methods=("get_combatant", "get_group", "get_metadata")),
    TypeSpec("ctx", AliasContextAPI),
    TypeSpec("SimpleRollResult", SimpleRollResult),
    TypeSpec("stats", AliasBaseStats),
    TypeSpec("levels", AliasLevels, parents=("character",), safe_methods=("get",)),
    TypeSpec("attacks", AliasAttackList, parents=("character",)),
    TypeSpec("attack", AliasAttack, parents=("attacks", "actions")),
    TypeSpec("skills", AliasSkills, parents=("character",)),
    TypeSpec("AliasSkills", AliasSkills, parents=("character",)),
    TypeSpec("skill", AliasSkill, parents=("skills",)),
    TypeSpec("AliasSkill", AliasSkill, parents=("skills",)),
    TypeSpec("saves", AliasSaves, parents=("character",), safe_methods=("get",)),
    TypeSpec(
        "resistances",
        AliasResistances,
        parents=("character",),
        safe_methods=("is_resistant", "is_immune", "is_vulnerable", "is_neutral"),
    ),
    TypeSpec("coinpurse", AliasCoinpurse, parents=("character",), safe_methods=("get_coins",)),
    TypeSpec("custom_counter", AliasCustomCounter, parents=("character",)),
    TypeSpec("consumable", AliasCustomCounter, parents=("character",)),
    TypeSpec("death_saves", AliasDeathSaves, parents=("character",), safe_methods=("is_stable", "is_dead")),
    TypeSpec("action", AliasAction, parents=("actions", "character")),
    TypeSpec(
        "spellbook",
        AliasSpellbook,
        parents=("character",),
        safe_methods=("find", "get_slots", "get_max_slots", "remaining_casts_of", "can_cast"),
    ),
    TypeSpec("spell", AliasSpellbookSpell, parents=("spellbook",)),
    TypeSpec("guild", GuildAPI, parents=("ctx",)),
    TypeSpec("channel", ChannelAPI, parents=("ctx",)),
    TypeSpec("category", CategoryAPI, parents=("channel",)),
    TypeSpec("author", AuthorAPI, parents=("ctx",)),
    TypeSpec("role", RoleAPI, parents=("author",)),
    TypeSpec(
        "combatant",
        SimpleCombatant,
        parents=("combat", "SimpleCombat", "group", "SimpleGroup"),
        safe_methods=("get_effect",),
    ),
    TypeSpec(
        "SimpleCombatant",
        SimpleCombatant,
        parents=("combat", "SimpleCombat", "group", "SimpleGroup"),
        safe_methods=("get_effect",),
    ),
    TypeSpec("group", SimpleGroup, parents=("combat", "SimpleCombat"), safe_methods=("get_combatant",)),
    TypeSpec("SimpleGroup", SimpleGroup, parents=("combat", "SimpleCombat"), safe_methods=("get_combatant",)),
    TypeSpec("effect", SimpleEffect, parents=("combatant", "SimpleCombatant")),
    TypeSpec("SimpleEffect", SimpleEffect, parents=("combatant", "SimpleCombatant")),
    TypeSpec("list", _BuiltinList),
    TypeSpec("int", int),
    TypeSpec("float", float),
    TypeSpec("dict", _BuiltinDict, safe_methods=("get",)),
    TypeSpec("str", _BuiltinStr),
    TypeSpec("ParsedArguments", ParsedArguments),
]


def _build_type_maps(specs: list[TypeSpec]) -> tuple[Dict[str, TypeEntry], dict[type, set[str]]]:
    type_map: dict[str, TypeEntry] = {}
    safe_methods: dict[type, set[str]] = {}
    for spec in specs:
        resolver = _allow_from(spec.name, *spec.parents) if spec.parents else None
        type_map[spec.name] = TypeEntry(spec.cls, resolver=resolver)
        if spec.safe_methods:
            safe_methods.setdefault(spec.cls, set()).update(spec.safe_methods)
    return type_map, safe_methods


TYPE_MAP, SAFE_METHODS = _build_type_maps(TYPE_SPECS)


def resolve_type_key(type_key: str, receiver_type: str | None = None) -> str | None:
    entry = TYPE_MAP.get(type_key)
    if not entry:
        return None
    return entry.resolver(receiver_type) if entry.resolver else type_key


def type_cls(type_key: str) -> type | None:
    entry = TYPE_MAP.get(type_key)
    if not entry:
        return None
    return entry.cls


def display_type_label(type_key: str) -> str:
    cls = type_cls(type_key)
    if cls is None:
        return type_key
    name = cls.__name__
    if name.startswith("_Builtin"):
        return type_key
    return name


_SKILL_DOCS: dict[str, str] = {
    "acrobatics": "Acrobatics skill bonus.",
    "animalHandling": "Animal Handling skill bonus.",
    "arcana": "Arcana skill bonus.",
    "athletics": "Athletics skill bonus.",
    "deception": "Deception skill bonus.",
    "history": "History skill bonus.",
    "initiative": "Initiative modifier.",
    "insight": "Insight skill bonus.",
    "intimidation": "Intimidation skill bonus.",
    "investigation": "Investigation skill bonus.",
    "medicine": "Medicine skill bonus.",
    "nature": "Nature skill bonus.",
    "perception": "Perception skill bonus.",
    "performance": "Performance skill bonus.",
    "persuasion": "Persuasion skill bonus.",
    "religion": "Religion skill bonus.",
    "sleightOfHand": "Sleight of Hand skill bonus.",
    "stealth": "Stealth skill bonus.",
    "survival": "Survival skill bonus.",
    "strength": "Strength ability score for this skill block.",
    "dexterity": "Dexterity ability score for this skill block.",
    "constitution": "Constitution ability score for this skill block.",
    "intelligence": "Intelligence ability score for this skill block.",
    "wisdom": "Wisdom ability score for this skill block.",
    "charisma": "Charisma ability score for this skill block.",
}

_COUNTER_DOCS: dict[str, str] = {
    "name": "Internal name of the counter.",
    "title": "Display title for the counter.",
    "desc": "Description text for the counter.",
    "value": "Current counter value.",
    "max": "Maximum value for the counter.",
    "min": "Minimum value for the counter.",
    "reset_on": "Reset cadence for the counter (e.g., long/short rest).",
    "display_type": "Display style for the counter.",
    "reset_to": "Value to reset the counter to.",
    "reset_by": "Increment applied when the counter resets.",
}

_EFFECT_DOCS: dict[str, str] = {
    "name": "Effect name.",
    "duration": "Configured duration for the effect.",
    "remaining": "Remaining duration for the effect.",
    "effect": "Raw effect payload.",
    "attacks": "Attack data attached to the effect, if any.",
    "buttons": "Buttons provided by the effect.",
    "conc": "Whether the effect requires concentration.",
    "desc": "Effect description text.",
    "ticks_on_end": "Whether the effect ticks when it ends.",
    "combatant_name": "Name of the owning combatant.",
    "parent": "Parent effect, if nested.",
    "children": "Child effects nested under this effect.",
}

_ATTR_DOC_OVERRIDES: dict[str, dict[str, str]] = {
    "SimpleRollResult": {
        "dice": "Markdown representation of the dice that were rolled.",
        "total": "Numeric total of the resolved roll.",
        "full": "Rendered roll result string.",
        "result": "Underlying d20 RollResult object.",
        "raw": "Original d20 expression for the roll.",
    },
    "stats": {
        "prof_bonus": "Proficiency bonus for the character.",
        "strength": "Strength ability score.",
        "dexterity": "Dexterity ability score.",
        "constitution": "Constitution ability score.",
        "intelligence": "Intelligence ability score.",
        "wisdom": "Wisdom ability score.",
        "charisma": "Charisma ability score.",
    },
    "AliasBaseStats": {
        "prof_bonus": "Proficiency bonus for the character.",
        "strength": "Strength ability score.",
        "dexterity": "Dexterity ability score.",
        "constitution": "Constitution ability score.",
        "intelligence": "Intelligence ability score.",
        "wisdom": "Wisdom ability score.",
        "charisma": "Charisma ability score.",
    },
    "levels": {
        "total_level": "Sum of all class levels.",
    },
    "AliasLevels": {
        "total_level": "Sum of all class levels.",
    },
    "attack": {
        "name": "Attack name.",
        "verb": "Attack verb or action phrase.",
        "proper": "Whether the attack name is treated as proper.",
        "activation_type": "Activation type identifier for this attack.",
        "raw": "Raw attack payload from the statblock.",
    },
    "AliasAttack": {
        "name": "Attack name.",
        "verb": "Attack verb or action phrase.",
        "proper": "Whether the attack name is treated as proper.",
        "activation_type": "Activation type identifier for this attack.",
        "raw": "Raw attack payload from the statblock.",
    },
    "skills": _SKILL_DOCS,
    "AliasSkills": _SKILL_DOCS,
    "skill": {
        "value": "Total modifier for the skill.",
        "prof": "Proficiency value applied to the skill.",
        "bonus": "Base bonus before rolling.",
        "adv": "Advantage state for the skill roll (True/False/None).",
    },
    "AliasSkill": {
        "value": "Total modifier for the skill.",
        "prof": "Proficiency value applied to the skill.",
        "bonus": "Base bonus before rolling.",
        "adv": "Advantage state for the skill roll (True/False/None).",
    },
    "resistances": {
        "resist": "Damage types resisted.",
        "vuln": "Damage types this target is vulnerable to.",
        "immune": "Damage types the target is immune to.",
        "neutral": "Damage types with no modifiers.",
    },
    "AliasResistances": {
        "resist": "Damage types resisted.",
        "vuln": "Damage types this target is vulnerable to.",
        "immune": "Damage types the target is immune to.",
        "neutral": "Damage types with no modifiers.",
    },
    "coinpurse": {
        "pp": "Platinum pieces carried.",
        "gp": "Gold pieces carried.",
        "ep": "Electrum pieces carried.",
        "sp": "Silver pieces carried.",
        "cp": "Copper pieces carried.",
        "total": "Total value of all coins.",
    },
    "AliasCoinpurse": {
        "pp": "Platinum pieces carried.",
        "gp": "Gold pieces carried.",
        "ep": "Electrum pieces carried.",
        "sp": "Silver pieces carried.",
        "cp": "Copper pieces carried.",
        "total": "Total value of all coins.",
    },
    "custom_counter": _COUNTER_DOCS,
    "consumable": _COUNTER_DOCS,
    "AliasCustomCounter": _COUNTER_DOCS,
    "death_saves": {
        "successes": "Number of successful death saves.",
        "fails": "Number of failed death saves.",
    },
    "AliasDeathSaves": {
        "successes": "Number of successful death saves.",
        "fails": "Number of failed death saves.",
    },
    "spellbook": {
        "dc": "Save DC for spells in this spellbook.",
        "sab": "Spell attack bonus for this spellbook.",
        "caster_level": "Caster level used for the spellbook.",
        "spell_mod": "Spellcasting ability modifier.",
        "spells": "Spells grouped by level.",
        "pact_slot_level": "Level of pact slots, if any.",
        "num_pact_slots": "Number of pact slots available.",
        "max_pact_slots": "Maximum pact slots available.",
    },
    "AliasSpellbook": {
        "dc": "Save DC for spells in this spellbook.",
        "sab": "Spell attack bonus for this spellbook.",
        "caster_level": "Caster level used for the spellbook.",
        "spell_mod": "Spellcasting ability modifier.",
        "spells": "Spells grouped by level.",
        "pact_slot_level": "Level of pact slots, if any.",
        "num_pact_slots": "Number of pact slots available.",
        "max_pact_slots": "Maximum pact slots available.",
    },
    "spell": {
        "name": "Spell name.",
        "dc": "Save DC for this spell.",
        "sab": "Spell attack bonus for this spell.",
        "mod": "Spellcasting modifier applied to the spell.",
        "prepared": "Whether the spell is prepared/known.",
    },
    "AliasSpellbookSpell": {
        "name": "Spell name.",
        "dc": "Save DC for this spell.",
        "sab": "Spell attack bonus for this spell.",
        "mod": "Spellcasting modifier applied to the spell.",
        "prepared": "Whether the spell is prepared/known.",
    },
    "guild": {
        "name": "Guild (server) name.",
        "id": "Guild (server) id.",
    },
    "channel": {
        "name": "Channel name.",
        "id": "Channel id.",
        "topic": "Channel topic, if set.",
        "category": "Parent category for the channel.",
        "parent": "Parent channel, if present.",
    },
    "category": {
        "name": "Category name.",
        "id": "Category id.",
    },
    "author": {
        "name": "User name for the invoking author.",
        "id": "User id for the invoking author.",
        "discriminator": "User discriminator/tag.",
        "display_name": "Display name for the author.",
        "roles": "Roles held by the author.",
    },
    "role": {
        "name": "Role name.",
        "id": "Role id.",
    },
    "effect": _EFFECT_DOCS,
    "SimpleEffect": _EFFECT_DOCS,
}

_METHOD_DOC_OVERRIDES: dict[str, dict[str, str]] = {
    "ParsedArguments": {
        "get": "returns all values for the arg cast to the given type.",
        "last": "returns the most recent value cast to the given type.",
        "adv": "returns -1/0/1/2 indicator for dis/normal/adv/elven accuracy.",
        "join": "joins all argument values with a separator into a string.",
        "ignore": "removes argument values so later reads skip them.",
        "update": "replaces values for an argument.",
        "update_nx": "sets values only if the argument is missing.",
        "set_context": "associates a context bucket for nested parsing.",
        "add_context": "appends a context bucket for nested parsing.",
    },
}


def _load_method_docs_from_html(path: Path | str = "tmp_avrae_api.html") -> dict[str, dict[str, str]]:
    docs: dict[str, dict[str, str]] = {}
    try:
        html = Path(path).read_text(encoding="utf-8")
    except Exception:
        return docs
    pattern = re.compile(
        r'<dt class="sig[^"]*" id="aliasing\.api\.[^\.]+\.(?P<class>\w+)\.(?P<method>\w+)">.*?</dt>\s*(?P<body><dd.*?</dd>)',
        re.DOTALL,
    )
    tag_re = re.compile(r"<[^>]+>")
    for match in pattern.finditer(html):
        cls = match.group("class")
        method = match.group("method")
        body = match.group("body")
        raw_text = unescape(tag_re.sub("", body)).strip()
        text = _strip_signature_prefix(raw_text)
        if not text:
            continue
        docs.setdefault(cls, {})[method] = text
    return docs


def _strip_signature_prefix(text: str) -> str:
    cleaned = re.sub(r"^[A-Za-z_][\w]*\s*\([^)]*\)\s*(?:->|→)?\s*", "", text)
    if cleaned != text:
        return cleaned.strip()
    # Fallback: split on common dash separators after a signature-like prefix.
    for sep in ("–", "—", "-"):
        parts = text.split(sep, 1)
        if len(parts) == 2 and "(" in parts[0] and ")" in parts[0]:
            return parts[1].strip()
    return text.strip()


# Enrich method docs from the bundled API HTML when available.
_METHOD_DOC_OVERRIDES.update(_load_method_docs_from_html())


def type_meta(type_name: str) -> TypeMeta:
    return _type_meta_map().get(type_name, TypeMeta(attrs={}, methods={}, element_type=""))


@lru_cache()
def _type_meta_map() -> Dict[str, TypeMeta]:
    meta: dict[str, TypeMeta] = {}
    reverse_type_map: dict[type, str] = {entry.cls: key for key, entry in TYPE_MAP.items()}

    def _iter_element_for_type_name(type_name: str) -> str:
        cls = type_cls(type_name)
        if not cls:
            return ""
        return _element_type_from_iterable(cls, reverse_type_map)

    def _getitem_element_for_type_name(type_name: str) -> str:
        cls = type_cls(type_name)
        if not cls:
            return ""
        return _element_type_from_getitem(cls, reverse_type_map)

    for type_name, entry in TYPE_MAP.items():
        cls = entry.cls
        attrs: dict[str, AttrMeta] = {}
        methods: dict[str, MethodMeta] = {}
        iterable_element = _iter_element_for_type_name(type_name)
        getitem_element = _getitem_element_for_type_name(type_name)
        element_hint = iterable_element or getitem_element
        override_docs = {
            **_ATTR_DOC_OVERRIDES.get(type_name, {}),
            **_ATTR_DOC_OVERRIDES.get(cls.__name__, {}),
        }
        method_override_docs = {
            **_METHOD_DOC_OVERRIDES.get(type_name, {}),
            **_METHOD_DOC_OVERRIDES.get(cls.__name__, {}),
        }

        for attr in getattr(cls, "ATTRS", []):
            doc = ""
            type_name_hint = ""
            element_type_hint = ""
            try:
                attr_obj = getattr(cls, attr)
            except Exception:
                attr_obj = None
            if isinstance(attr_obj, property) and attr_obj.fget:
                doc = (attr_obj.fget.__doc__ or "").strip()
                ann = _return_annotation(attr_obj.fget, cls)
                type_name_hint, element_type_hint = _type_names_from_annotation(ann, reverse_type_map)
            elif attr_obj is not None:
                doc = (getattr(attr_obj, "__doc__", "") or "").strip()
            if not type_name_hint and not element_type_hint:
                ann = _class_annotation(cls, attr)
                type_name_hint, element_type_hint = _type_names_from_annotation(ann, reverse_type_map)
            if not type_name_hint and element_hint:
                type_name_hint = element_hint
            if type_name_hint and not element_type_hint:
                element_type_hint = _iter_element_for_type_name(type_name_hint)
            if not doc:
                doc = override_docs.get(attr, doc)
            attrs[attr] = AttrMeta(doc=doc, type_name=type_name_hint, element_type=element_type_hint)

        for meth in getattr(cls, "METHODS", []):
            doc = ""
            sig_label = ""
            try:
                meth_obj = getattr(cls, meth)
            except Exception:
                meth_obj = None
            if callable(meth_obj):
                sig_label = _format_method_signature(meth, meth_obj)
                doc = (meth_obj.__doc__ or "").strip()
            if not doc:
                doc = method_override_docs.get(meth, doc)
            methods[meth] = MethodMeta(signature=sig_label, doc=doc)

        meta[type_name] = TypeMeta(attrs=attrs, methods=methods, element_type=element_hint)
    return meta


def _format_method_signature(name: str, obj: Any) -> str:
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return f"{name}()"
    params = list(sig.parameters.values())
    if params and params[0].name in {"self", "cls"}:
        params = params[1:]
    sig = sig.replace(parameters=params)
    return f"{name}{sig}"


def _return_annotation(func: Any, cls: type) -> Any:
    try:
        module = inspect.getmodule(func) or inspect.getmodule(cls)
        globalns = module.__dict__ if module else None
        hints = typing.get_type_hints(func, globalns=globalns, include_extras=False)
        return hints.get("return")
    except Exception:
        return getattr(func, "__annotations__", {}).get("return")


def _class_annotation(cls: type, attr: str) -> Any:
    try:
        module = inspect.getmodule(cls)
        globalns = module.__dict__ if module else None
        hints = typing.get_type_hints(cls, globalns=globalns, include_extras=False)
        if attr in hints:
            return hints[attr]
    except Exception:
        pass
    return getattr(getattr(cls, "__annotations__", {}), "get", lambda _k: None)(attr)


def _type_names_from_annotation(ann: Any, reverse_type_map: Dict[type, str]) -> tuple[str, str]:
    if ann is None:
        return "", ""
    if isinstance(ann, str):
        return "", ""
    try:
        origin = getattr(ann, "__origin__", None)
    except Exception:
        origin = None
    args = getattr(ann, "__args__", ()) if origin else ()

    if ann in reverse_type_map:
        return reverse_type_map[ann], ""

    iterable_origins = {list, List, Iterable, typing.Sequence, typing.Iterable}
    try:
        from collections.abc import Iterable as ABCIterable, Sequence as ABCSequence

        iterable_origins.update({ABCIterable, ABCSequence})
    except Exception:
        pass
    if origin in iterable_origins:
        if args:
            elem = args[0]
            elem_name, _ = _type_names_from_annotation(elem, reverse_type_map)
            container_name = reverse_type_map.get(origin) or "list"
            return container_name, elem_name
        return reverse_type_map.get(origin) or "list", ""

    if isinstance(ann, type) and ann in reverse_type_map:
        return reverse_type_map[ann], ""
    return "", ""


def _element_type_from_iterable(cls: type, reverse_type_map: Dict[type, str]) -> str:
    try:
        hints = typing.get_type_hints(cls.__iter__, globalns=inspect.getmodule(cls).__dict__, include_extras=False)
        ret_ann = hints.get("return")
        _, elem = _type_names_from_annotation(ret_ann, reverse_type_map)
        return elem
    except Exception:
        return ""


def _element_type_from_getitem(cls: type, reverse_type_map: Dict[type, str]) -> str:
    try:
        hints = typing.get_type_hints(cls.__getitem__, globalns=inspect.getmodule(cls).__dict__, include_extras=False)
        ret_ann = hints.get("return")
        name, elem = _type_names_from_annotation(ret_ann, reverse_type_map)
        return name or elem
    except Exception:
        return ""


def is_safe_call(base: Any, method: str) -> bool:
    for cls, allowed in SAFE_METHODS.items():
        if isinstance(base, cls) and method in allowed:
            return True
    return False
