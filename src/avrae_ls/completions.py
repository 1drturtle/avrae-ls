from __future__ import annotations

import ast
import inspect
import re
import typing
from dataclasses import dataclass
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional

from lsprotocol import types

from .context import ContextData, GVarResolver
from .argparser import ParsedArguments
from .runtime import _default_builtins
from .api import (
    AliasAction,
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
    AliasAttack,
    AliasAttackList,
    AliasLevels,
    CategoryAPI,
    ChannelAPI,
    CharacterAPI,
    SimpleCombat,
    SimpleCombatant,
    GuildAPI,
    RoleAPI,
    AuthorAPI,
    SimpleEffect,
    SimpleGroup,
    SimpleRollResult,
)
from .signature_help import FunctionSig


class _BuiltinList:
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = ["append", "extend", "insert", "remove", "pop", "clear", "index", "count", "sort", "reverse", "copy"]

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
    METHODS: ClassVar[list[str]] = ["get", "keys", "values", "items", "pop", "popitem", "update", "setdefault", "clear", "copy"]

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
    TypeSpec("resistances", AliasResistances, parents=("character",), safe_methods=("is_resistant", "is_immune", "is_vulnerable", "is_neutral")),
    TypeSpec("coinpurse", AliasCoinpurse, parents=("character",), safe_methods=("get_coins",)),
    TypeSpec("custom_counter", AliasCustomCounter, parents=("character",)),
    TypeSpec("consumable", AliasCustomCounter, parents=("character",)),
    TypeSpec("death_saves", AliasDeathSaves, parents=("character",), safe_methods=("is_stable", "is_dead")),
    TypeSpec("action", AliasAction, parents=("actions", "character")),
    TypeSpec("spellbook", AliasSpellbook, parents=("character",), safe_methods=("find", "get_slots", "get_max_slots", "remaining_casts_of", "can_cast")),
    TypeSpec("spell", AliasSpellbookSpell, parents=("spellbook",)),
    TypeSpec("guild", GuildAPI, parents=("ctx",)),
    TypeSpec("channel", ChannelAPI, parents=("ctx",)),
    TypeSpec("category", CategoryAPI, parents=("channel",)),
    TypeSpec("author", AuthorAPI, parents=("ctx",)),
    TypeSpec("role", RoleAPI, parents=("author",)),
    TypeSpec("combatant", SimpleCombatant, parents=("combat", "SimpleCombat", "group", "SimpleGroup"), safe_methods=("get_effect",)),
    TypeSpec("SimpleCombatant", SimpleCombatant, parents=("combat", "SimpleCombat", "group", "SimpleGroup"), safe_methods=("get_effect",)),
    TypeSpec("group", SimpleGroup, parents=("combat", "SimpleCombat"), safe_methods=("get_combatant",)),
    TypeSpec("SimpleGroup", SimpleGroup, parents=("combat", "SimpleCombat"), safe_methods=("get_combatant",)),
    TypeSpec("effect", SimpleEffect, parents=("combatant", "SimpleCombatant")),
    TypeSpec("SimpleEffect", SimpleEffect, parents=("combatant", "SimpleCombatant")),
    TypeSpec("list", _BuiltinList),
    TypeSpec("int", int),
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


def _resolve_type_key(type_key: str, receiver_type: str | None = None) -> str | None:
    entry = TYPE_MAP.get(type_key)
    if not entry:
        return None
    return entry.resolver(receiver_type) if entry.resolver else type_key


def _type_cls(type_key: str) -> type | None:
    entry = TYPE_MAP.get(type_key)
    if not entry:
        return None
    return entry.cls


IDENT_RE = re.compile(r"[A-Za-z_]\w*$")
ATTR_RE = re.compile(r"([A-Za-z_][\w\.\(\)]*)\.(?:([A-Za-z_]\w*)\s*)?$")
DICT_GET_RE = re.compile(r"^([A-Za-z_]\w*)\.get\(\s*(['\"])(.+?)\2")
ATTR_AT_CURSOR_RE = re.compile(r"([A-Za-z_][\w\.\(\)]*)\.([A-Za-z_]\w*)")


@dataclass
class Suggestion:
    name: str
    kind: types.CompletionItemKind
    detail: str = ""
    documentation: str = ""


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


def gather_suggestions(
    ctx_data: ContextData,
    resolver: GVarResolver,
    sigs: Dict[str, FunctionSig],
) -> List[Suggestion]:
    suggestions: list[Suggestion] = []

    for name, sig in sigs.items():
        suggestions.append(
            Suggestion(
                name=name,
                kind=types.CompletionItemKind.Function,
                detail=sig.label,
                documentation=sig.doc,
            )
        )

    vars_map = ctx_data.vars.to_initial_names()
    for name in vars_map:
        suggestions.append(Suggestion(name=name, kind=types.CompletionItemKind.Variable, detail="var"))

    gvars = resolver.snapshot()
    for name in gvars:
        suggestions.append(Suggestion(name=name, kind=types.CompletionItemKind.Variable, detail="gvar"))

    for name in _default_builtins().keys():
        if name not in sigs:
            suggestions.append(Suggestion(name=name, kind=types.CompletionItemKind.Function))

    # context helpers
    suggestions.append(Suggestion(name="character", kind=types.CompletionItemKind.Function, detail="Alias character()"))
    suggestions.append(Suggestion(name="combat", kind=types.CompletionItemKind.Function, detail="Alias combat()"))
    suggestions.append(Suggestion(name="ctx", kind=types.CompletionItemKind.Variable, detail="Alias context"))

    return suggestions


def completion_items_for_position(
    code: str,
    line: int,
    character: int,
    suggestions: Iterable[Suggestion],
) -> List[types.CompletionItem]:
    attr_ctx = _attribute_receiver_and_prefix(code, line, character)
    if attr_ctx:
        receiver, attr_prefix = attr_ctx
        sanitized = _sanitize_incomplete_line(code, line, character)
        type_map = _infer_type_map(sanitized)
        return _attribute_completions(receiver, attr_prefix, sanitized, type_map)

    line_text = _line_text_to_cursor(code, line, character)
    prefix = _current_prefix(line_text)
    items: list[types.CompletionItem] = []
    for sugg in suggestions:
        if prefix and not sugg.name.startswith(prefix):
            continue
        items.append(
            types.CompletionItem(
                label=sugg.name,
                kind=sugg.kind,
                detail=sugg.detail or None,
                documentation=sugg.documentation or None,
            )
        )
    return items


def _attribute_completions(receiver: str, prefix: str, code: str, type_map: Dict[str, str] | None = None) -> List[types.CompletionItem]:
    items: list[types.CompletionItem] = []
    type_key = _resolve_type_name(receiver, code, type_map)
    if IDENT_RE.fullmatch(receiver) and (not type_map or receiver not in type_map) and type_key == receiver:
        # Avoid treating arbitrary variable names as known API types unless they were inferred.
        return items
    meta = _type_meta(type_key)
    detail = f"{type_key}()"

    for name, attr_meta in meta.attrs.items():
        if prefix and not name.startswith(prefix):
            continue
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Field,
                detail=detail,
                documentation=attr_meta.doc or None,
            )
        )
    for name, method_meta in meta.methods.items():
        if prefix and not name.startswith(prefix):
            continue
        method_detail = method_meta.signature or f"{name}()"
        items.append(
            types.CompletionItem(
                label=name,
                kind=types.CompletionItemKind.Method,
                detail=method_detail,
                documentation=method_meta.doc or None,
            )
        )
    return items


def hover_for_position(
    code: str,
    line: int,
    character: int,
    sigs: Dict[str, FunctionSig],
    ctx_data: ContextData,
    resolver: GVarResolver,
) -> Optional[types.Hover]:
    line_text = _line_text(code, line)
    type_map = _infer_type_map(code)
    bindings = _infer_constant_bindings(code, line, ctx_data)
    attr_ctx = _attribute_receiver_and_prefix(code, line, character, capture_full_token=True)
    if attr_ctx:
        receiver, attr_prefix = attr_ctx
        inferred = _resolve_type_name(receiver, code, type_map)
        meta = _type_meta(inferred)
        if attr_prefix in meta.attrs:
            doc = meta.attrs[attr_prefix].doc
            contents = f"```avrae\n{inferred}().{attr_prefix}\n```"
            if doc:
                contents += f"\n\n{doc}"
            return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))
        if attr_prefix in meta.methods:
            method_meta = meta.methods[attr_prefix]
            signature = method_meta.signature or f"{attr_prefix}()"
            doc = method_meta.doc
            contents = f"```avrae\n{signature}\n```"
            if doc:
                contents += f"\n\n{doc}"
            return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))

    word, _, _ = _word_at_position(line_text, character)
    if not word:
        return None
    if word in bindings:
        return _format_binding_hover(word, bindings[word], "local")
    if word in type_map:
        type_label = _display_type_label(type_map[word])
        contents = f"`{word}` type: `{type_label}`"
        return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))
    if word in sigs:
        sig = sigs[word]
        contents = f"```avrae\n{sig.label}\n```\n\n{sig.doc}"
        return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))

    vars_map = ctx_data.vars.to_initial_names()
    if word in vars_map:
        return _format_binding_hover(word, vars_map[word], "var")

    gvars = resolver.snapshot()
    if word in gvars:
        return _format_binding_hover(word, gvars[word], "gvar")
    return None


def _current_prefix(line_text: str) -> str:
    match = IDENT_RE.search(line_text)
    return match.group(0) if match else ""


def _word_from_line(text: str, cursor: int) -> str:
    return _word_at_position(text, cursor)[0]


def _word_at_position(text: str, cursor: int) -> tuple[str, int, int]:
    cursor = max(0, min(cursor, len(text)))
    start = cursor
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "_"):
        start -= 1
    end = cursor
    while end < len(text) and (text[end].isalnum() or text[end] == "_"):
        end += 1
    return text[start:end], start, end


def _line_text_to_cursor(code: str, line: int, character: int) -> str:
    lines = code.splitlines()
    if line >= len(lines):
        return ""
    return lines[line][:character]


def _attribute_receiver_and_prefix(code: str, line: int, character: int, capture_full_token: bool = False) -> Optional[tuple[str, str]]:
    lines = code.splitlines()
    if line >= len(lines):
        return None
    line_text = lines[line]
    end = character
    if capture_full_token:
        while end < len(line_text) and (line_text[end].isalnum() or line_text[end] == "_"):
            end += 1
    line_text = line_text[: end]
    dot = line_text.rfind(".")
    if dot == -1:
        return None
    tail = line_text[dot + 1 :]
    prefix_match = re.match(r"\s*([A-Za-z_]\w*)?", tail)
    prefix = prefix_match.group(1) or "" if prefix_match else ""

    receiver_fragment = line_text[:dot].rstrip()
    start = len(receiver_fragment)
    paren = bracket = brace = 0

    def _allowed(ch: str) -> bool:
        return ch.isalnum() or ch in {"_", ".", "]", "[", ")", "(", "'", '"'}

    for idx in range(len(receiver_fragment) - 1, -1, -1):
        ch = receiver_fragment[idx]
        if ch in ")]}":
            if ch == ")":
                paren += 1
            elif ch == "]":
                bracket += 1
            else:
                brace += 1
            start = idx
            continue
        if ch in "([{":
            if ch == "(" and paren > 0:
                paren -= 1
                start = idx
                continue
            if ch == "[" and bracket > 0:
                bracket -= 1
                start = idx
                continue
            if ch == "{" and brace > 0:
                brace -= 1
                start = idx
                continue
            break
        if paren or bracket or brace:
            start = idx
            continue
        if ch.isspace():
            break
        if not _allowed(ch):
            break
        start = idx

    receiver_src = receiver_fragment[start:].strip()
    if not receiver_src:
        return None
    return receiver_src, prefix


def _sanitize_incomplete_line(code: str, line: int, character: int) -> str:
    lines = code.splitlines()
    if 0 <= line < len(lines):
        prefix = lines[line][:character]
        trimmed = prefix.rstrip()
        if trimmed.endswith("."):
            prefix = trimmed[:-1]
        else:
            dot = prefix.rfind(".")
            if dot != -1:
                after = prefix[dot + 1 :]
                if not re.match(r"\s*[A-Za-z_]", after):
                    prefix = prefix[:dot] + after
        lines[line] = prefix
        candidate = "\n".join(lines)
        try:
            ast.parse(candidate)
        except SyntaxError:
            indent = re.match(r"[ \t]*", lines[line]).group(0)
            lines[line] = indent + "pass"
    return "\n".join(lines)


def _line_text(code: str, line: int) -> str:
    lines = code.splitlines()
    if line < 0 or line >= len(lines):
        return ""
    return lines[line]


def _display_type_label(type_key: str) -> str:
    cls = _type_cls(type_key)
    return cls.__name__ if cls else type_key


def _split_annotation_string(text: str) -> tuple[Optional[str], Optional[str]]:
    stripped = text.strip().strip("'\"")
    if not stripped:
        return None, None
    match = re.match(r"^([A-Za-z_][\w]*)\s*(?:\[\s*([A-Za-z_][\w]*)(?:\s*,\s*([A-Za-z_][\w]*))?\s*\])?$", stripped)
    if not match:
        return stripped, None
    base = match.group(1)
    elem = match.group(3) or match.group(2)
    base_norm = base.lower() if base.lower() in {"list", "dict", "set", "tuple"} else base
    return base_norm, elem


def _annotation_types(node: ast.AST | None) -> tuple[Optional[str], Optional[str]]:
    if node is None:
        return None, None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _split_annotation_string(node.value)
    if isinstance(node, ast.Str):
        return _split_annotation_string(node.s)
    if isinstance(node, ast.Name):
        return node.id, None
    if isinstance(node, ast.Attribute):
        return node.attr, None
    try:
        text = ast.unparse(node)
    except Exception:
        text = ""
    if text:
        return _split_annotation_string(text)
    return None, None


def _infer_receiver_type(code: str, name: str) -> Optional[str]:
    return _infer_type_map(code).get(name)


def _infer_type_map(code: str) -> Dict[str, str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    visitor = _TypeInferencer(code)
    visitor.visit(tree)
    return visitor.type_map


class _TypeInferencer(ast.NodeVisitor):
    def __init__(self, code: str) -> None:
        self.code = code
        self.type_map: dict[str, str] = {}

    def visit_Assign(self, node: ast.Assign):
        val_type, elem_type = self._value_type(node.value)
        for target in node.targets:
            self._bind_target(target, val_type, elem_type, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        val_type, elem_type = self._value_type(node.value) if node.value else (None, None)
        ann_type, ann_elem = _annotation_types(getattr(node, "annotation", None))
        val_type = val_type or ann_type
        elem_type = elem_type or ann_elem
        self._bind_target(node.target, val_type, elem_type, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        val_type, elem_type = self._value_type(node.value)
        self._bind_target(
            node.target,
            val_type or self._existing_type(node.target),
            elem_type or self._existing_element(node.target),
            None,
        )
        self.generic_visit(node)

    def visit_For(self, node: ast.For):
        _, elem_type = self._value_type(node.iter)
        if not elem_type and isinstance(node.iter, ast.Name):
            elem_type = self.type_map.get(f"{node.iter.id}.__element__")
        self._bind_target(node.target, elem_type, None, None)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor):
        _, elem_type = self._value_type(node.iter)
        if not elem_type and isinstance(node.iter, ast.Name):
            elem_type = self.type_map.get(f"{node.iter.id}.__element__")
        self._bind_target(node.target, elem_type, None, None)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._bind_function_args(node.args)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._bind_function_args(node.args)
        self.generic_visit(node)

    def visit_If(self, node: ast.If):
        self.visit(node.test)
        base_map = self.type_map.copy()
        body_map = self._visit_block(node.body, base_map.copy())
        orelse_seed = base_map.copy()
        orelse_map = self._visit_block(node.orelse, orelse_seed) if node.orelse else orelse_seed
        self.type_map = self._merge_branch_types(base_map, body_map, orelse_map)

    def _visit_block(self, nodes: Iterable[ast.stmt], seed: dict[str, str]) -> dict[str, str]:
        walker = _TypeInferencer(self.code)
        walker.type_map = seed
        for stmt in nodes:
            walker.visit(stmt)
        return walker.type_map

    def _merge_branch_types(self, base: dict[str, str], left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
        merged = base.copy()
        for key in set(left) | set(right):
            l_val = left.get(key)
            r_val = right.get(key)
            if l_val and r_val and l_val == r_val:
                merged[key] = l_val
            elif key in base:
                merged[key] = base[key]
            elif l_val and not r_val:
                merged[key] = l_val
            elif r_val and not l_val:
                merged[key] = r_val
            elif key in merged:
                merged.pop(key, None)
        return merged

    def _bind_target(self, target: ast.AST, val_type: Optional[str], elem_type: Optional[str], source: ast.AST | None):
        if isinstance(target, ast.Name):
            if val_type:
                self.type_map[target.id] = val_type
            if elem_type:
                self.type_map[f"{target.id}.__element__"] = elem_type
            if source is not None:
                self._record_dict_key_types(target.id, source)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind_target(elt, val_type, elem_type, source)

    def _bind_function_args(self, args: ast.arguments) -> None:
        for arg in getattr(args, "posonlyargs", []):
            self._bind_arg_annotation(arg)
        for arg in args.args:
            self._bind_arg_annotation(arg)
        if args.vararg:
            self._bind_arg_annotation(args.vararg)
        for arg in args.kwonlyargs:
            self._bind_arg_annotation(arg)
        if args.kwarg:
            self._bind_arg_annotation(args.kwarg)

    def _bind_arg_annotation(self, arg: ast.arg) -> None:
        ann_type, elem_type = _annotation_types(getattr(arg, "annotation", None))
        if ann_type:
            self.type_map[arg.arg] = ann_type
        if elem_type:
            self.type_map[f"{arg.arg}.__element__"] = elem_type

    def _existing_type(self, target: ast.AST) -> Optional[str]:
        if isinstance(target, ast.Name):
            return self.type_map.get(target.id)
        return None

    def _existing_element(self, target: ast.AST) -> Optional[str]:
        if isinstance(target, ast.Name):
            return self.type_map.get(f"{target.id}.__element__")
        return None

    def _value_type(self, value: ast.AST | None) -> tuple[Optional[str], Optional[str]]:
        if isinstance(value, ast.Call):
            if isinstance(value.func, ast.Name):
                if value.func.id in {"character", "combat"}:
                    return value.func.id, None
                if value.func.id == "vroll":
                    return "SimpleRollResult", None
                if value.func.id == "argparse":
                    return "ParsedArguments", None
                if value.func.id == "range":
                    return "range", "int"
                if value.func.id in {"list", "dict", "str"}:
                    return value.func.id, None
            if isinstance(value.func, ast.Attribute):
                base_type, base_elem = self._value_type(value.func.value)
                if value.func.attr == "get" and value.args:
                    key_literal = self._literal_key(value.args[0])
                    val_type, elem_type = self._subscript_type(value.func.value, key_literal, base_type, base_elem)
                    if val_type:
                        return val_type, elem_type
                    if base_elem:
                        return base_elem, None
        if isinstance(value, ast.List):
            elem_type, _ = self._iterable_element_from_values(value.elts)
            return "list", elem_type
        if isinstance(value, ast.Tuple):
            elem_type, _ = self._iterable_element_from_values(getattr(value, "elts", []))
            return "tuple", elem_type
        if isinstance(value, ast.Set):
            elem_type, _ = self._iterable_element_from_values(getattr(value, "elts", []))
            return "set", elem_type
        if isinstance(value, ast.ListComp):
            comp_type, comp_elem = self._value_type(value.elt)
            return "list", comp_type or comp_elem
        if isinstance(value, ast.Dict):
            elem_type, _ = self._iterable_element_from_values(value.values or [])
            return "dict", elem_type
        if isinstance(value, ast.Subscript):
            return self._subscript_value_type(value)
        if isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                return "str", None
        if isinstance(value, ast.Name):
            if value.id in self.type_map:
                return self.type_map[value.id], self.type_map.get(f"{value.id}.__element__")
            if value.id in {"character", "combat", "ctx"}:
                return value.id, None
        if isinstance(value, ast.Attribute):
            attr_name = value.attr
            base_type = None
            base_elem = None
            if isinstance(value.value, ast.Name):
                base_type = self.type_map.get(value.value.id)
                base_elem = self.type_map.get(f"{value.value.id}.__element__")
            if base_type is None:
                base_type, base_elem = self._value_type(value.value)
            if base_type:
                meta = _type_meta(base_type)
                attr_meta = meta.attrs.get(attr_name)
                if attr_meta:
                    if attr_meta.type_name:
                        return attr_meta.type_name, attr_meta.element_type or None
                    if attr_meta.element_type:
                        return base_type, attr_meta.element_type
                if base_elem:
                    return base_elem, None
                resolved_attr_type = _resolve_type_key(attr_name, base_type)
                if resolved_attr_type:
                    return resolved_attr_type, None
            return None, None
        if isinstance(value, ast.IfExp):
            t_type, t_elem = self._value_type(value.body)
            e_type, e_elem = self._value_type(value.orelse)
            if t_type and e_type and t_type == e_type:
                merged_elem = t_elem or e_elem
                if t_elem and e_elem and t_elem != e_elem:
                    merged_elem = None
                return t_type, merged_elem
            return t_type or e_type, t_elem or e_elem
        return None, None

    def _iterable_element_from_values(self, values: Iterable[ast.AST]) -> tuple[Optional[str], Optional[str]]:
        elem_type: Optional[str] = None
        nested_elem: Optional[str] = None
        for node in values:
            val_type, inner_elem = self._value_type(node)
            if not val_type:
                return None, None
            if elem_type is None:
                elem_type = val_type
                nested_elem = inner_elem
            elif elem_type != val_type:
                return None, None
            if inner_elem:
                if nested_elem is None:
                    nested_elem = inner_elem
                elif nested_elem != inner_elem:
                    nested_elem = None
        return elem_type, nested_elem

    def _literal_key(self, node: ast.AST | None) -> str | int | None:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (str, int)):
                return node.value
        if hasattr(ast, "Index") and isinstance(node, getattr(ast, "Index")):
            return self._literal_key(getattr(node, "value", None))
        return None

    def _subscript_type(
        self,
        base_expr: ast.AST,
        key_literal: str | int | None,
        base_type: Optional[str],
        base_elem: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        base_name = base_expr.id if isinstance(base_expr, ast.Name) else None
        if base_name and key_literal is not None:
            dict_key = f"{base_name}.{key_literal}"
            if dict_key in self.type_map:
                return self.type_map[dict_key], self.type_map.get(f"{dict_key}.__element__")
        elem_hint = base_elem
        if base_name and not elem_hint:
            elem_hint = self.type_map.get(f"{base_name}.__element__")
        if base_type:
            meta = _type_meta(base_type)
            if key_literal is not None and key_literal in meta.attrs:
                attr_meta = meta.attrs[key_literal]
                if attr_meta.type_name:
                    return attr_meta.type_name, attr_meta.element_type or None
                if attr_meta.element_type:
                    return base_type, attr_meta.element_type
            elem_hint = elem_hint or meta.element_type
        if elem_hint:
            return elem_hint, None
        return base_type, None

    def _subscript_value_type(self, node: ast.Subscript) -> tuple[Optional[str], Optional[str]]:
        base_type, base_elem = self._value_type(node.value)
        key_literal = self._literal_key(getattr(node, "slice", None))
        return self._subscript_type(node.value, key_literal, base_type, base_elem)

    def _record_dict_key_types(self, var_name: str, value: ast.AST | None) -> None:
        if not isinstance(value, ast.Dict):
            return
        for key_node, val_node in zip(value.keys or [], value.values or []):
            key_literal = self._literal_key(key_node)
            if key_literal is None:
                continue
            val_type, elem_type = self._value_type(val_node)
            if val_type:
                self.type_map[f"{var_name}.{key_literal}"] = val_type
            if elem_type:
                self.type_map[f"{var_name}.{key_literal}.__element__"] = elem_type


def _resolve_type_name(receiver: str, code: str, type_map: Dict[str, str] | None = None) -> str:
    mapping = type_map or _infer_type_map(code)
    get_match = DICT_GET_RE.match(receiver)
    if get_match:
        base, _, key = get_match.groups()
        dict_key = f"{base}.{key}"
        if dict_key in mapping:
            return mapping[dict_key]
    bracket = receiver.rfind("[")
    if bracket != -1 and receiver.endswith("]"):
        base_expr = receiver[:bracket]
        elem_hint = mapping.get(f"{base_expr}.__element__")
        if elem_hint:
            return elem_hint
        base_type = _resolve_type_name(base_expr, code, mapping)
        if base_type:
            base_meta = _type_meta(base_type)
            if base_meta.element_type:
                return base_meta.element_type
            return base_type
    receiver = receiver.rstrip("()")
    if "." in receiver:
        base_expr, attr_name = receiver.rsplit(".", 1)
        base_type = _resolve_type_name(base_expr, code, mapping)
        if base_type:
            meta = _type_meta(base_type)
            attr_key = attr_name.split("[", 1)[0]
            attr_meta = meta.attrs.get(attr_key)
            if attr_meta:
                if attr_meta.element_type:
                    return attr_meta.element_type
                if attr_meta.type_name:
                    return attr_meta.type_name

    if receiver in mapping:
        return mapping[receiver]
    elem_key = f"{receiver}.__element__"
    if elem_key in mapping:
        return mapping[elem_key]
    resolved_receiver = _resolve_type_key(receiver)
    if resolved_receiver:
        return resolved_receiver
    tail = receiver.split(".")[-1].split("[", 1)[0]
    resolved_tail = _resolve_type_key(tail)
    if resolved_tail:
        return resolved_tail
    return receiver


def _type_meta(type_name: str) -> TypeMeta:
    return _type_meta_map().get(type_name, TypeMeta(attrs={}, methods={}, element_type=""))


@lru_cache()
def _type_meta_map() -> Dict[str, TypeMeta]:
    meta: dict[str, TypeMeta] = {}
    reverse_type_map: dict[type, str] = {entry.cls: key for key, entry in TYPE_MAP.items()}

    def _iter_element_for_type_name(type_name: str) -> str:
        cls = _type_cls(type_name)
        if not cls:
            return ""
        return _element_type_from_iterable(cls, reverse_type_map)

    def _getitem_element_for_type_name(type_name: str) -> str:
        cls = _type_cls(type_name)
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

    # handle list/sequence typing to detect element type
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


def _infer_constant_bindings(code: str, upto_line: int | None, ctx_data: ContextData) -> Dict[str, Any]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    bindings: dict[str, Any] = {}
    limit = None if upto_line is None else upto_line + 1

    def _value_for(node: ast.AST) -> Any | None:
        value = _literal_value(node)
        if value is None:
            value = _evaluated_value(node, ctx_data, bindings)
        return value

    def _loop_binding(node: ast.AST) -> Any | None:
        value = _value_for(node)
        if value is None:
            return _LoopVarBinding()
        try:
            iterator = iter(value)
        except TypeError:
            return _LoopVarBinding()
        try:
            return next(iterator)
        except StopIteration:
            return _LoopVarBinding()

    class Visitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign):
            if limit is not None and node.lineno > limit:
                return
            value = _value_for(node.value)
            if value is None:
                self.generic_visit(node)
                return
            for name in _names_from_target(node.targets):
                bindings[name] = value

        def visit_AnnAssign(self, node: ast.AnnAssign):
            if limit is not None and node.lineno > limit:
                return
            if node.value is None:
                return
            value = _value_for(node.value)
            if value is None:
                self.generic_visit(node)
                return
            for name in _names_from_target([node.target]):
                bindings[name] = value

        def visit_For(self, node: ast.For):
            if limit is not None and node.lineno > limit:
                return
            loop_val = _loop_binding(node.iter)
            for name in _names_from_target([node.target]):
                bindings[name] = loop_val
            self.generic_visit(node)

        def visit_AsyncFor(self, node: ast.AsyncFor):
            if limit is not None and node.lineno > limit:
                return
            loop_val = _loop_binding(node.iter)
            for name in _names_from_target([node.target]):
                bindings[name] = loop_val
            self.generic_visit(node)

    Visitor().visit(tree)
    return bindings


def _names_from_target(targets: Iterable[ast.expr]) -> List[str]:
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(_names_from_target(target.elts))
    return names


def _literal_value(node: ast.AST) -> Any | None:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        val = _literal_value(node.operand)
        if isinstance(val, (int, float, complex)):
            return val if isinstance(node.op, ast.UAdd) else -val
        return None
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = []
        for elt in node.elts:
            val = _literal_value(elt)
            if val is None:
                return None
            items.append(val)
        if isinstance(node, ast.List):
            return items
        if isinstance(node, ast.Tuple):
            return tuple(items)
        return set(items)
    if isinstance(node, ast.Dict):
        keys = []
        values = []
        for k, v in zip(node.keys, node.values):
            key_val = _literal_value(k) if k is not None else None
            val_val = _literal_value(v)
            if key_val is None or val_val is None:
                return None
            keys.append(key_val)
            values.append(val_val)
        return dict(zip(keys, values))
    return None


def _evaluated_value(node: ast.AST, ctx_data: ContextData, bindings: Dict[str, Any] | None = None) -> Any | None:
    bindings = bindings or {}
    try:
        return _eval_node(node, ctx_data, bindings)
    except Exception:
        return None


def _eval_node(node: ast.AST, ctx_data: ContextData, bindings: Dict[str, Any]) -> Any | None:
    if isinstance(node, ast.Attribute):
        base = _eval_node(node.value, ctx_data, bindings)
        if base is None:
            return None
        return getattr(base, node.attr, None)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id == "character":
                return CharacterAPI(ctx_data.character)
            if node.func.id == "combat":
                return SimpleCombat(ctx_data.combat)
            if node.func.id == "range":
                args = []
                for arg in node.args:
                    val = _literal_value(arg)
                    if val is None:
                        return None
                    args.append(val)
                try:
                    return range(*args)
                except Exception:
                    return None
        if isinstance(node.func, ast.Attribute):
            base = _eval_node(node.func.value, ctx_data, bindings)
            if base is None:
                return None
            method_name = node.func.attr
            if not _is_safe_call(base, method_name):
                return None
            args = []
            for arg in node.args:
                val = _literal_value(arg)
                if val is None:
                    val = _eval_node(arg, ctx_data, bindings)
                if val is None:
                    return None
                args.append(val)
            kwargs = {}
            for kw in node.keywords:
                if kw.arg is None:
                    return None
                val = _literal_value(kw.value)
                if val is None:
                    val = _eval_node(kw.value, ctx_data, bindings)
                if val is None:
                    return None
                kwargs[kw.arg] = val
            callee = getattr(base, method_name, None)
            if not callable(callee):
                return None
            try:
                return callee(*args, **kwargs)
            except Exception:
                return None
    if isinstance(node, ast.Name):
        if node.id in bindings:
            return bindings[node.id]
        if node.id == "ctx":
            return AliasContextAPI(ctx_data.ctx)
    return None


def _is_safe_call(base: Any, method: str) -> bool:
    for cls, allowed in SAFE_METHODS.items():
        if isinstance(base, cls) and method in allowed:
            return True
    return False


def _format_binding_hover(name: str, value: Any, label: str) -> types.Hover:
    type_name = _describe_type(value)
    preview = _preview_value(value)
    contents = f"**{label}** `{name}`\n\nType: `{type_name}`\nValue: `{preview}`"
    return types.Hover(contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value=contents))


def _describe_type(value: Any) -> str:
    # Provide light element-type hints for common iterables so hover shows list[Foo].
    def _iterable_type(iterable: Iterable[Any], container: str) -> str:
        try:
            seen = {type(item).__name__ for item in iterable if item is not None}
        except Exception:
            return container
        return f"{container}[{seen.pop()}]" if len(seen) == 1 else container

    try:
        if isinstance(value, list):
            return _iterable_type(value, "list")
        if isinstance(value, tuple):
            return _iterable_type(value, "tuple")
        if isinstance(value, set):
            return _iterable_type(value, "set")
    except Exception:
        pass
    return type(value).__name__


def _preview_value(value: Any) -> str:
    def _short(val: Any, max_len: int = 30) -> str:
        try:
            text = repr(val)
        except Exception:
            text = type(val).__name__
        return text if len(text) <= max_len else text[: max_len - 3] + "..."

    try:
        if isinstance(value, dict):
            items = list(value.items())
            parts = [f"{_short(k)}: {_short(v)}" for k, v in items[:3]]
            suffix = ", …" if len(items) > 3 else ""
            return "{" + ", ".join(parts) + suffix + f"}} ({len(items)} items)"
        if isinstance(value, (list, tuple, set)):
            seq = list(value)
            parts = [_short(v) for v in seq[:3]]
            suffix = ", …" if len(seq) > 3 else ""
            bracket = ("[", "]") if isinstance(value, list) else ("(", ")") if isinstance(value, tuple) else ("{", "}")
            return f"{bracket[0]}" + ", ".join(parts) + suffix + f"{bracket[1]} ({len(seq)} items)"
    except Exception:
        pass
    return _short(value, max_len=120)


class _LoopVarBinding:
    def __repr__(self) -> str:
        return "<loop item>"
