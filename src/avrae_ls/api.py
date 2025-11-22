from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, ClassVar, Iterable, Mapping, MutableMapping, Sequence

import d20

from .dice import RerollableStringifier

UNSET = object()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


class _DirMixin:
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = []

    def __dir__(self) -> list[str]:
        data_keys = list(getattr(self, "data", {}).keys())
        return sorted(set(self.ATTRS + self.METHODS + data_keys))

    def __post_init__(self) -> None:
        # Ensure draconic's approx_len cache is initialized to a numeric value
        # so nested objects don't trip TypeErrors when measured.
        if getattr(self, "__approx_len__", None) is None:
            self.__approx_len__ = 0


class SimpleRollResult(_DirMixin):
    ATTRS: ClassVar[list[str]] = ["dice", "total", "full", "result", "raw"]
    METHODS: ClassVar[list[str]] = ["consolidated"]

    def __init__(self, roll_result: d20.RollResult):
        self._roll = roll_result
        self.dice = d20.MarkdownStringifier().stringify(roll_result.expr.roll)
        self.total = roll_result.total
        self.full = str(roll_result)
        self.result = roll_result
        self.raw = roll_result.expr
        self.__approx_len__ = 0

    def consolidated(self) -> str:
        d20.utils.simplify_expr(self._roll.expr, ambig_inherit="left")
        return RerollableStringifier().stringify(self._roll.expr.roll)

    def __str__(self) -> str:
        return self.full


# === Context API ===
@dataclass
class GuildAPI(_DirMixin):
    data: Mapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["name", "id"]
    METHODS: ClassVar[list[str]] = ["servsettings"]

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Guild"))

    @property
    def id(self) -> int | None:
        raw = self.data.get("id")
        return int(raw) if raw is not None else None

    def servsettings(self) -> Mapping[str, Any] | None:
        return self.data.get("servsettings")

    def __getitem__(self, item: str) -> Any:
        if isinstance(item, int):
            raise TypeError("CustomCounter indices must be strings (e.g., 'value', 'max').")
        return getattr(self, str(item))

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0


@dataclass
class CategoryAPI(_DirMixin):
    data: Mapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["name", "id"]
    METHODS: ClassVar[list[str]] = []

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Category"))

    @property
    def id(self) -> int | None:
        raw = self.data.get("id")
        return int(raw) if raw is not None else None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))


@dataclass
class ChannelAPI(_DirMixin):
    data: Mapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["name", "id", "topic", "category", "parent"]
    METHODS: ClassVar[list[str]] = []

    @property
    def name(self) -> str:
        return str(self.data.get("name", "channel"))

    @property
    def id(self) -> int | None:
        raw = self.data.get("id")
        return int(raw) if raw is not None else None

    @property
    def topic(self) -> str | None:
        val = self.data.get("topic")
        return str(val) if val is not None else None

    @property
    def category(self) -> CategoryAPI | None:
        cat = self.data.get("category")
        return CategoryAPI(cat) if cat is not None else None

    @property
    def parent(self) -> ChannelAPI | None:
        parent = self.data.get("parent")
        return ChannelAPI(parent) if parent is not None else None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))


@dataclass
class RoleAPI(_DirMixin):
    data: Mapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["name", "id"]
    METHODS: ClassVar[list[str]] = []

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Role"))

    @property
    def id(self) -> int | None:
        raw = self.data.get("id")
        return int(raw) if raw is not None else None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))


@dataclass
class AuthorAPI(_DirMixin):
    data: Mapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["name", "id", "discriminator", "display_name", "roles"]
    METHODS: ClassVar[list[str]] = ["get_roles"]

    @property
    def name(self) -> str:
        return str(self.data.get("name", "User"))

    @property
    def id(self) -> int | None:
        raw = self.data.get("id")
        return int(raw) if raw is not None else None

    @property
    def discriminator(self) -> str:
        return str(self.data.get("discriminator", "0000"))

    @property
    def display_name(self) -> str:
        return str(self.data.get("display_name", self.name))

    def get_roles(self) -> list[RoleAPI]:
        roles = self.data.get("roles") or []
        return [RoleAPI(r) for r in roles]

    @property
    def roles(self) -> list[RoleAPI]:
        return self.get_roles()

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))

    def __str__(self) -> str:
        return f"{self.name}#{self.discriminator}"


@dataclass
class AliasContextAPI(_DirMixin):
    data: Mapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["guild", "channel", "author", "prefix", "alias", "message_id"]
    METHODS: ClassVar[list[str]] = []

    @property
    def guild(self) -> GuildAPI | None:
        guild_data = self.data.get("guild")
        return GuildAPI(guild_data) if guild_data is not None else None

    @property
    def channel(self) -> ChannelAPI | None:
        channel_data = self.data.get("channel")
        return ChannelAPI(channel_data) if channel_data is not None else None

    @property
    def author(self) -> AuthorAPI | None:
        author_data = self.data.get("author")
        return AuthorAPI(author_data) if author_data is not None else None

    @property
    def prefix(self) -> str | None:
        val = self.data.get("prefix")
        return str(val) if val is not None else None

    @property
    def alias(self) -> str | None:
        val = self.data.get("alias")
        return str(val) if val is not None else None

    @property
    def message_id(self) -> int | None:
        raw = self.data.get("message_id")
        return int(raw) if raw is not None else None

    def __getattr__(self, item: str) -> Any:
        return self.data.get(item)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))


# === StatBlock primitives ===
_SKILL_CANONICAL = {
    "acrobatics": "acrobatics",
    "animalhandling": "animalHandling",
    "arcana": "arcana",
    "athletics": "athletics",
    "deception": "deception",
    "history": "history",
    "initiative": "initiative",
    "insight": "insight",
    "intimidation": "intimidation",
    "investigation": "investigation",
    "medicine": "medicine",
    "nature": "nature",
    "perception": "perception",
    "performance": "performance",
    "persuasion": "persuasion",
    "religion": "religion",
    "sleightofhand": "sleightOfHand",
    "sleight_of_hand": "sleightOfHand",
    "stealth": "stealth",
    "survival": "survival",
    "strength": "strength",
    "dexterity": "dexterity",
    "constitution": "constitution",
    "intelligence": "intelligence",
    "wisdom": "wisdom",
    "charisma": "charisma",
}

_SKILL_ABILITIES = {
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
}


@dataclass
class AliasBaseStats(_DirMixin):
    data: Mapping[str, Any]
    prof_bonus_override: int | None = None
    ATTRS: ClassVar[list[str]] = [
        "prof_bonus",
        "strength",
        "dexterity",
        "constitution",
        "intelligence",
        "wisdom",
        "charisma",
    ]
    METHODS: ClassVar[list[str]] = ["get_mod", "get"]

    @property
    def prof_bonus(self) -> int:
        if self.prof_bonus_override is not None:
            return self.prof_bonus_override
        return _safe_int(self.data.get("prof_bonus"), 2)

    @property
    def strength(self) -> int:
        return _safe_int(self.data.get("strength"), 10)

    @property
    def dexterity(self) -> int:
        return _safe_int(self.data.get("dexterity"), 10)

    @property
    def constitution(self) -> int:
        return _safe_int(self.data.get("constitution"), 10)

    @property
    def intelligence(self) -> int:
        return _safe_int(self.data.get("intelligence"), 10)

    @property
    def wisdom(self) -> int:
        return _safe_int(self.data.get("wisdom"), 10)

    @property
    def charisma(self) -> int:
        return _safe_int(self.data.get("charisma"), 10)

    def get_mod(self, stat: str) -> int:
        stat_lower = str(stat).lower()
        lookup = {
            "str": self.strength,
            "dex": self.dexterity,
            "con": self.constitution,
            "int": self.intelligence,
            "wis": self.wisdom,
            "cha": self.charisma,
            "strength": self.strength,
            "dexterity": self.dexterity,
            "constitution": self.constitution,
            "intelligence": self.intelligence,
            "wisdom": self.wisdom,
            "charisma": self.charisma,
        }
        score = lookup.get(stat_lower, 10)
        return math.floor((score - 10) / 2)

    def get(self, stat: str) -> int:
        stat_lower = str(stat).lower()
        lookup = {
            "strength": self.strength,
            "dexterity": self.dexterity,
            "constitution": self.constitution,
            "intelligence": self.intelligence,
            "wisdom": self.wisdom,
            "charisma": self.charisma,
        }
        return lookup.get(stat_lower, 10)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))


@dataclass
class AliasLevels(_DirMixin):
    data: Mapping[str, Any]
    ATTRS: ClassVar[list[str]] = ["total_level"]
    METHODS: ClassVar[list[str]] = ["get"]

    @property
    def total_level(self) -> int | float:
        total = 0
        for _, value in self.data.items():
            try:
                total += value
            except Exception:
                try:
                    total += int(value)
                except Exception:
                    continue
        return total

    def get(self, cls_name: str, default: int | float = 0) -> int | float:
        val = self.data.get(str(cls_name))
        if val is None:
            return default
        try:
            return val
        except Exception:
            return default

    def __iter__(self) -> Iterable[tuple[str, Any]]:
        return iter(self.data.items())

    def __getitem__(self, item: str) -> Any:
        return self.data[item]


@dataclass
class AliasAttack(_DirMixin):
    data: Mapping[str, Any]
    parent_statblock: Mapping[str, Any]
    ATTRS: ClassVar[list[str]] = ["name", "verb", "proper", "activation_type", "raw"]
    METHODS: ClassVar[list[str]] = []

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Attack"))

    @property
    def verb(self) -> str | None:
        val = self.data.get("verb")
        return str(val) if val is not None else None

    @property
    def proper(self) -> bool:
        return bool(self.data.get("proper", False))

    @property
    def activation_type(self) -> int | None:
        raw = self.data.get("activation_type")
        return int(raw) if raw is not None else None

    @property
    def raw(self) -> Mapping[str, Any]:
        return self.data.get("raw", self.data)

    def __str__(self) -> str:
        damage = self.raw.get("damage")
        verb = f" {self.verb}" if self.verb else ""
        if damage:
            return f"{self.name}{verb}: {damage}"
        return f"{self.name}{verb}".strip()

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))


@dataclass
class AliasAttackList(_DirMixin):
    attacks: Sequence[Mapping[str, Any]]
    parent_statblock: Mapping[str, Any]
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = []

    def __iter__(self) -> Iterable[AliasAttack]:
        for atk in self.attacks:
            yield AliasAttack(atk, self.parent_statblock)

    def __getitem__(self, item: int | slice) -> Any:
        if isinstance(item, slice):
            return AliasAttackList(self.attacks[item], self.parent_statblock)
        return AliasAttack(self.attacks[item], self.parent_statblock)

    def __len__(self) -> int:
        return len(self.attacks)

    def __str__(self) -> str:
        return "\n".join(str(AliasAttack(atk, self.parent_statblock)) for atk in self.attacks)


@dataclass
class AliasSkill(_DirMixin):
    data: Mapping[str, Any]
    ATTRS: ClassVar[list[str]] = ["value", "prof", "bonus", "adv"]
    METHODS: ClassVar[list[str]] = ["d20"]

    @property
    def value(self) -> int:
        return _safe_int(self.data.get("value"), 0)

    @property
    def prof(self) -> float | int:
        raw = self.data.get("prof")
        try:
            return float(raw)
        except Exception:
            return 0

    @property
    def bonus(self) -> int:
        return _safe_int(self.data.get("bonus"), 0)

    @property
    def adv(self) -> bool | None:
        val = self.data.get("adv")
        if val is None:
            return None
        return bool(val)

    def d20(self, base_adv=None, reroll=None, min_val=None, mod_override=None) -> str:
        mod = mod_override if mod_override is not None else self.value
        adv_prefix = "2d20kh1" if base_adv else "2d20kl1" if base_adv is False else "1d20"
        suffix = f"+{mod}" if mod >= 0 else str(mod)
        parts = [adv_prefix + suffix]
        if reroll is not None:
            parts.append(f"(reroll {reroll})")
        if min_val is not None:
            parts.append(f"(min {min_val})")
        return " ".join(parts)

    def __int__(self) -> int:
        return self.value

    def __repr__(self) -> str:
        return f"<AliasSkill value={self.value} prof={self.prof} bonus={self.bonus} adv={self.adv}>"

    def __gt__(self, other: Any) -> bool:
        return self.value > other

    def __ge__(self, other: Any) -> bool:
        return self.value >= other

    def __eq__(self, other: Any) -> bool:
        return self.value == other

    def __le__(self, other: Any) -> bool:
        return self.value <= other

    def __lt__(self, other: Any) -> bool:
        return self.value < other


@dataclass
class AliasSkills(_DirMixin):
    data: Mapping[str, Any]
    prof_bonus: int
    abilities: Mapping[str, int]
    ATTRS: ClassVar[list[str]] = list(_SKILL_ABILITIES.keys())
    METHODS: ClassVar[list[str]] = []

    def __getattr__(self, item: str) -> AliasSkill:
        return self._get_skill(item)

    def __getitem__(self, item: str) -> AliasSkill:
        return self._get_skill(str(item))

    def _get_skill(self, name: str) -> AliasSkill:
        normalized = _SKILL_CANONICAL.get(name.lower().replace(" ", "").replace("_", ""), name)
        skill_data = self.data.get(normalized) or self.data.get(name) or {}
        if not skill_data:
            ability = _SKILL_ABILITIES.get(normalized)
            ability_mod = 0
            if ability:
                ability_score = self.abilities.get(ability, 10)
                ability_mod = math.floor((ability_score - 10) / 2)
            skill_data = {"value": ability_mod, "prof": 0, "bonus": 0, "adv": None}
        return AliasSkill(skill_data)

    def __iter__(self) -> Iterable[tuple[str, AliasSkill]]:
        for name in _SKILL_ABILITIES.keys():
            yield name, self._get_skill(name)

    def __str__(self) -> str:
        return ", ".join(f"{name}: {skill.value}" for name, skill in self)


@dataclass
class AliasSaves(_DirMixin):
    data: Mapping[str, Any]
    prof_bonus: int
    abilities: Mapping[str, int]
    ATTRS: ClassVar[list[str]] = []
    METHODS: ClassVar[list[str]] = ["get"]

    def get(self, base_stat: str) -> AliasSkill:
        normalized = base_stat.lower()
        raw = self.data.get(normalized) or self.data.get(base_stat) or {}
        if isinstance(raw, (int, float)):
            raw = {"value": raw}
        if not raw:
            ability_score = self.abilities.get(normalized, 10)
            raw = {"value": math.floor((ability_score - 10) / 2)}
        return AliasSkill(raw)

    def __iter__(self) -> Iterable[tuple[str, AliasSkill]]:
        for key in ("str", "dex", "con", "int", "wis", "cha"):
            yield key, self.get(key)

    def __str__(self) -> str:
        return ", ".join(f"{name}: {skill.value}" for name, skill in self)


@dataclass
class ResistanceEntry:
    dtype: str
    unless: set[str] = field(default_factory=set)
    only: set[str] = field(default_factory=set)


@dataclass
class AliasResistances(_DirMixin):
    data: Mapping[str, Any]
    ATTRS: ClassVar[list[str]] = ["resist", "vuln", "immune", "neutral"]
    METHODS: ClassVar[list[str]] = ["is_resistant", "is_immune", "is_vulnerable", "is_neutral"]

    @staticmethod
    def _entries(key: str, data: Mapping[str, Any]) -> list[ResistanceEntry]:
        entries = []
        for entry in data.get(key, []):
            entries.append(
                ResistanceEntry(
                    dtype=str(entry.get("dtype", "")),
                    unless=set(entry.get("unless", []) or []),
                    only=set(entry.get("only", []) or []),
                )
            )
        return entries

    @property
    def resist(self) -> list[ResistanceEntry]:
        return self._entries("resist", self.data)

    @property
    def vuln(self) -> list[ResistanceEntry]:
        return self._entries("vuln", self.data)

    @property
    def immune(self) -> list[ResistanceEntry]:
        return self._entries("immune", self.data)

    @property
    def neutral(self) -> list[ResistanceEntry]:
        return self._entries("neutral", self.data)

    def is_resistant(self, damage_type: str) -> bool:
        token = str(damage_type).lower()
        return self._matches(self.resist, token)

    def is_immune(self, damage_type: str) -> bool:
        token = str(damage_type).lower()
        return self._matches(self.immune, token)

    def is_vulnerable(self, damage_type: str) -> bool:
        token = str(damage_type).lower()
        return self._matches(self.vuln, token)

    def is_neutral(self, damage_type: str) -> bool:
        token = str(damage_type).lower()
        return self._matches(self.neutral, token)

    @staticmethod
    def _matches(entries: Iterable[ResistanceEntry], token: str) -> bool:
        for entry in entries:
            if entry.only and token not in entry.only:
                continue
            if entry.unless and token in entry.unless:
                continue
            if entry.dtype.lower() == token:
                return True
        return False


@dataclass
class AliasSpellbookSpell(_DirMixin):
    data: Mapping[str, Any]
    ATTRS: ClassVar[list[str]] = ["name", "dc", "sab", "mod", "prepared"]
    METHODS: ClassVar[list[str]] = []

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Spell"))

    @property
    def dc(self) -> int | None:
        raw = self.data.get("dc")
        return int(raw) if raw is not None else None

    @property
    def sab(self) -> int | None:
        raw = self.data.get("sab")
        return int(raw) if raw is not None else None

    @property
    def mod(self) -> int | None:
        raw = self.data.get("mod")
        return int(raw) if raw is not None else None

    @property
    def prepared(self) -> bool:
        return bool(self.data.get("prepared", True))

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))

    def __str__(self) -> str:
        return self.name


@dataclass
class AliasSpellbook(_DirMixin):
    data: MutableMapping[str, Any]
    _spells_cache: list[AliasSpellbookSpell] = field(default_factory=list, init=False)
    ATTRS: ClassVar[list[str]] = [
        "dc",
        "sab",
        "caster_level",
        "spell_mod",
        "spells",
        "pact_slot_level",
        "num_pact_slots",
        "max_pact_slots",
    ]
    METHODS: ClassVar[list[str]] = [
        "find",
        "slots_str",
        "get_max_slots",
        "get_slots",
        "set_slots",
        "use_slot",
        "reset_slots",
        "reset_pact_slots",
        "remaining_casts_of",
        "cast",
        "can_cast",
    ]

    @property
    def dc(self) -> int:
        return _safe_int(self.data.get("dc"), 10)

    @property
    def sab(self) -> int:
        return _safe_int(self.data.get("sab"), 0)

    @property
    def caster_level(self) -> int:
        return _safe_int(self.data.get("caster_level"), 0)

    @property
    def spell_mod(self) -> int:
        return _safe_int(self.data.get("spell_mod"), 0)

    @property
    def spells(self) -> list[AliasSpellbookSpell]:
        if not self._spells_cache:
            spell_list = self.data.get("spells") or []
            self._spells_cache = [AliasSpellbookSpell(s) for s in spell_list]
        return self._spells_cache

    @property
    def pact_slot_level(self) -> int | None:
        raw = self.data.get("pact_slot_level")
        return int(raw) if raw is not None else None

    @property
    def num_pact_slots(self) -> int | None:
        raw = self.data.get("num_pact_slots")
        return int(raw) if raw is not None else None

    @property
    def max_pact_slots(self) -> int | None:
        raw = self.data.get("max_pact_slots")
        return int(raw) if raw is not None else None

    def find(self, spell_name: str) -> list[AliasSpellbookSpell]:
        needle = str(spell_name).lower()
        return [spell for spell in self.spells if spell.name.lower() == needle]

    def slots_str(self, level: int) -> str:
        slots = self.get_slots(level)
        max_slots = self.get_max_slots(level)
        return f"{slots}/{max_slots}"

    def get_max_slots(self, level: int) -> int:
        slots = self.data.get("max_slots") or {}
        return _safe_int(slots.get(int(level)), 0)

    def get_slots(self, level: int) -> int:
        slots = self.data.get("slots") or {}
        if int(level) == 0:
            return 1
        return _safe_int(slots.get(int(level)), 0)

    def set_slots(self, level: int, value: int, pact: bool = True) -> int:
        slots = self.data.setdefault("slots", {})
        slots[int(level)] = int(value)
        return slots[int(level)]

    def use_slot(self, level: int) -> int:
        current = self.get_slots(level)
        return self.set_slots(level, max(0, current - 1))

    def reset_slots(self) -> None:
        slots = self.data.get("slots") or {}
        max_slots = self.data.get("max_slots") or {}
        for level, maximum in max_slots.items():
            slots[int(level)] = _safe_int(maximum)
        self.data["slots"] = slots

    def reset_pact_slots(self) -> None:
        if self.max_pact_slots is None:
            return
        self.data["num_pact_slots"] = self.max_pact_slots

    def remaining_casts_of(self, spell: str, level: int) -> str:
        return self.slots_str(level)

    def cast(self, spell: str, level: int) -> str:
        self.use_slot(level)
        return f"Casted {spell} at level {level}"

    def can_cast(self, spell: str, level: int) -> bool:
        return self.get_slots(level) > 0

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, str):
            return False
        return any(spell.name.lower() == item.lower() for spell in self.spells)


@dataclass
class AliasCoinpurse(_DirMixin):
    data: MutableMapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["pp", "gp", "ep", "sp", "cp", "total"]
    METHODS: ClassVar[list[str]] = ["coin_str", "compact_str", "modify_coins", "set_coins", "autoconvert", "get_coins"]

    def __getattr__(self, item: str) -> Any:
        if item in {"pp", "gp", "ep", "sp", "cp"}:
            return _safe_int(self.data.get(item), 0)
        return self.data.get(item)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))

    @property
    def total(self) -> float:
        return self.pp * 10 + self.gp + self.ep * 0.5 + self.sp * 0.1 + self.cp * 0.01

    def coin_str(self, cointype: str) -> str:
        cointype = str(cointype)
        value = getattr(self, cointype)
        return f"{value} {cointype}"

    def compact_str(self) -> str:
        return f"{self.total:.2f} gp"

    def modify_coins(
        self,
        pp: int = 0,
        gp: int = 0,
        ep: int = 0,
        sp: int = 0,
        cp: int = 0,
        autoconvert: bool = True,
    ) -> dict[str, Any]:
        self.data["pp"] = self.pp + int(pp)
        self.data["gp"] = self.gp + int(gp)
        self.data["ep"] = self.ep + int(ep)
        self.data["sp"] = self.sp + int(sp)
        self.data["cp"] = self.cp + int(cp)
        return self.get_coins()

    def set_coins(self, pp: int, gp: int, ep: int, sp: int, cp: int) -> None:
        self.data["pp"] = int(pp)
        self.data["gp"] = int(gp)
        self.data["ep"] = int(ep)
        self.data["sp"] = int(sp)
        self.data["cp"] = int(cp)

    def autoconvert(self) -> None:
        total_cp = int(self.total * 100)
        self.data["pp"], remainder = divmod(total_cp, 1000)
        self.data["gp"], remainder = divmod(remainder, 100)
        self.data["ep"], remainder = divmod(remainder, 50)
        self.data["sp"], self.data["cp"] = divmod(remainder, 10)

    def get_coins(self) -> dict[str, Any]:
        return {"pp": self.pp, "gp": self.gp, "ep": self.ep, "sp": self.sp, "cp": self.cp, "total": self.total}


@dataclass
class AliasDeathSaves(_DirMixin):
    data: MutableMapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["successes", "fails"]
    METHODS: ClassVar[list[str]] = ["succeed", "fail", "is_stable", "is_dead", "reset"]

    @property
    def successes(self) -> int:
        return _safe_int(self.data.get("successes"), 0)

    @property
    def fails(self) -> int:
        return _safe_int(self.data.get("fails"), 0)

    def succeed(self, num: int = 1) -> None:
        self.data["successes"] = self.successes + int(num)

    def fail(self, num: int = 1) -> None:
        self.data["fails"] = self.fails + int(num)

    def is_stable(self) -> bool:
        return self.successes >= 3 and self.fails < 3

    def is_dead(self) -> bool:
        return self.fails >= 3

    def reset(self) -> None:
        self.data["successes"] = 0
        self.data["fails"] = 0

    def __str__(self) -> str:
        return f"{self.successes} successes / {self.fails} failures"


@dataclass
class AliasAction(_DirMixin):
    data: Mapping[str, Any]
    parent_statblock: Mapping[str, Any]
    ATTRS: ClassVar[list[str]] = ["name", "activation_type", "activation_type_name", "description", "snippet"]
    METHODS: ClassVar[list[str]] = []

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Action"))

    @property
    def activation_type(self) -> int | None:
        raw = self.data.get("activation_type")
        return int(raw) if raw is not None else None

    @property
    def activation_type_name(self) -> str | None:
        val = self.data.get("activation_type_name")
        return str(val) if val is not None else None

    @property
    def description(self) -> str:
        return str(self.data.get("description", ""))

    @property
    def snippet(self) -> str:
        return str(self.data.get("snippet", self.description))

    def __str__(self) -> str:
        return f"**{self.name}**: {self.description}"

    def __getitem__(self, item: str) -> Any:
        if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
            raise TypeError("AliasAction attributes must be accessed by name (e.g., 'name', 'activation_type').")
        return getattr(self, str(item))

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0


@dataclass
class AliasCustomCounter(_DirMixin):
    data: MutableMapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = [
        "name",
        "title",
        "desc",
        "value",
        "max",
        "min",
        "reset_on",
        "display_type",
        "reset_to",
        "reset_by",
    ]
    METHODS: ClassVar[list[str]] = ["mod", "set", "reset", "full_str"]

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Counter"))

    @property
    def title(self) -> str | None:
        val = self.data.get("title")
        return str(val) if val is not None else None

    @property
    def desc(self) -> str | None:
        val = self.data.get("desc")
        return str(val) if val is not None else None

    @property
    def value(self) -> int:
        return _safe_int(self.data.get("value"), 0)

    @property
    def max(self) -> int:
        return _safe_int(self.data.get("max"), 2**31 - 1)

    @property
    def min(self) -> int:
        return _safe_int(self.data.get("min"), -(2**31))

    @property
    def reset_on(self) -> str | None:
        val = self.data.get("reset_on")
        return str(val) if val is not None else None

    @property
    def display_type(self) -> str | None:
        val = self.data.get("display_type")
        return str(val) if val is not None else None

    @property
    def reset_to(self) -> int | None:
        raw = self.data.get("reset_to")
        return int(raw) if raw is not None else None

    @property
    def reset_by(self) -> str | None:
        val = self.data.get("reset_by")
        return str(val) if val is not None else None

    def mod(self, value: int, strict: bool = False) -> int:
        return self.set(self.value + int(value), strict)

    def set(self, new_value: int, strict: bool = False) -> int:
        val = int(new_value)
        if strict:
            if val > self.max or val < self.min:
                raise ValueError("Counter out of bounds")
        val = max(self.min, min(val, self.max))
        self.data["value"] = val
        return val

    def reset(self) -> dict[str, Any]:
        if self.reset_to is not None:
            target = self.reset_to
        elif self.reset_by is not None:
            target = self.value + _safe_int(self.reset_by, 0)
        else:
            target = self.max
        old_value = self.value
        new_value = self.set(target)
        return {"new_value": new_value, "old_value": old_value, "target_value": target, "delta": new_value - old_value}

    def full_str(self, include_name: bool = False) -> str:
        prefix = f"**{self.name}**\n" if include_name else ""
        content = f"{self.value}/{self.max}"
        if self.display_type:
            content = self.display_type
        return prefix + content

    def __str__(self) -> str:
        return f"{self.value}/{self.max}"

    def __getitem__(self, item: str) -> Any:
        if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
            raise TypeError("CustomCounter indices must be strings (e.g., 'value', 'max').")
        return getattr(self, str(item))

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0


# === StatBlock + Character ===
@dataclass
class AliasStatBlock(_DirMixin):
    data: MutableMapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = [
        "name",
        "stats",
        "levels",
        "attacks",
        "skills",
        "saves",
        "resistances",
        "ac",
        "max_hp",
        "hp",
        "temp_hp",
        "spellbook",
        "creature_type",
    ]
    METHODS: ClassVar[list[str]] = ["set_hp", "modify_hp", "hp_str", "reset_hp", "set_temp_hp"]

    def _prof_bonus(self) -> int:
        stats = self.data.get("stats") or {}
        if "prof_bonus" in stats:
            return _safe_int(stats.get("prof_bonus"), 2)
        levels = self.data.get("levels") or self.data.get("class_levels") or {}
        total_level = sum(_safe_int(val, 0) for val in levels.values()) or 1
        return max(2, 2 + (math.ceil(total_level / 4)))

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Statblock"))

    @property
    def stats(self) -> AliasBaseStats:
        return AliasBaseStats(self.data.get("stats") or {}, prof_bonus_override=self._prof_bonus())

    @property
    def levels(self) -> AliasLevels:
        return AliasLevels(self.data.get("levels") or self.data.get("class_levels") or {})

    @property
    def attacks(self) -> AliasAttackList:
        return AliasAttackList(self.data.get("attacks") or [], self.data)

    @property
    def skills(self) -> AliasSkills:
        abilities = {
            "strength": self.stats.strength,
            "dexterity": self.stats.dexterity,
            "constitution": self.stats.constitution,
            "intelligence": self.stats.intelligence,
            "wisdom": self.stats.wisdom,
            "charisma": self.stats.charisma,
        }
        return AliasSkills(self.data.get("skills") or {}, self._prof_bonus(), abilities)

    @property
    def saves(self) -> AliasSaves:
        abilities = {
            "strength": self.stats.strength,
            "dexterity": self.stats.dexterity,
            "constitution": self.stats.constitution,
            "intelligence": self.stats.intelligence,
            "wisdom": self.stats.wisdom,
            "charisma": self.stats.charisma,
            "str": self.stats.strength,
            "dex": self.stats.dexterity,
            "con": self.stats.constitution,
            "int": self.stats.intelligence,
            "wis": self.stats.wisdom,
            "cha": self.stats.charisma,
        }
        return AliasSaves(self.data.get("saves") or {}, self._prof_bonus(), abilities)

    @property
    def resistances(self) -> AliasResistances:
        return AliasResistances(self.data.get("resistances") or {})

    @property
    def ac(self) -> int | None:
        raw = self.data.get("ac")
        return int(raw) if raw is not None else None

    @property
    def max_hp(self) -> int | None:
        raw = self.data.get("max_hp")
        return int(raw) if raw is not None else None

    @property
    def hp(self) -> int | None:
        raw = self.data.get("hp")
        return int(raw) if raw is not None else None

    @property
    def temp_hp(self) -> int:
        return _safe_int(self.data.get("temp_hp"), 0)

    @property
    def spellbook(self) -> AliasSpellbook:
        return AliasSpellbook(self.data.get("spellbook") or {})

    @property
    def creature_type(self) -> str | None:
        val = self.data.get("creature_type")
        return str(val) if val is not None else None

    def set_hp(self, new_hp: int) -> int:
        self.data["hp"] = int(new_hp)
        return self.data["hp"]

    def modify_hp(self, amount: int, ignore_temp: bool = False, overflow: bool = True) -> int:
        hp = self.hp or 0
        new_hp = hp + int(amount)
        if not overflow and self.max_hp is not None:
            new_hp = max(0, min(new_hp, self.max_hp))
        self.data["hp"] = new_hp
        return new_hp

    def hp_str(self) -> str:
        return f"{self.hp}/{self.max_hp} (+{self.temp_hp} temp)"

    def reset_hp(self) -> int:
        if self.max_hp is not None:
            self.data["hp"] = self.max_hp
        self.data["temp_hp"] = 0
        return self.hp or 0

    def set_temp_hp(self, new_temp: int) -> int:
        self.data["temp_hp"] = int(new_temp)
        return self.temp_hp

    def __getattr__(self, item: str) -> Any:
        return self.data.get(item)

    def __getitem__(self, item: str) -> Any:
        if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
            raise TypeError("AliasStatBlock attributes must be accessed by name (e.g., 'name', 'stats').")
        return getattr(self, str(item))

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0


@dataclass
class CharacterAPI(AliasStatBlock):
    ATTRS: ClassVar[list[str]] = AliasStatBlock.ATTRS + [
        "actions",
        "coinpurse",
        "csettings",
        "race",
        "background",
        "owner",
        "upstream",
        "sheet_type",
        "cvars",
        "consumables",
        "death_saves",
        "description",
        "image",
    ]
    METHODS: ClassVar[list[str]] = AliasStatBlock.METHODS + [
        "cc",
        "get_cc",
        "get_cc_max",
        "get_cc_min",
        "set_cc",
        "mod_cc",
        "delete_cc",
        "create_cc_nx",
        "create_cc",
        "edit_cc",
        "cc_exists",
        "cc_str",
        "get_cvar",
        "set_cvar",
        "set_cvar_nx",
        "delete_cvar",
    ]

    def _consumable_map(self) -> MutableMapping[str, MutableMapping[str, Any]]:
        consumables = self.data.setdefault("consumables", {})
        if isinstance(consumables, list):
            # normalize list payloads to map
            mapped: dict[str, MutableMapping[str, Any]] = {}
            for item in consumables:
                name = str(item.get("name", f"cc_{len(mapped)}"))
                mapped[name] = dict(item)
            self.data["consumables"] = mapped
            consumables = mapped
        return consumables  # type: ignore[return-value]

    @property
    def actions(self) -> list[AliasAction]:
        acts = self.data.get("actions") or []
        return [AliasAction(a, self.data) for a in acts]

    @property
    def coinpurse(self) -> AliasCoinpurse:
        return AliasCoinpurse(self.data.get("coinpurse") or {})

    @property
    def csettings(self) -> Mapping[str, Any]:
        return self.data.get("csettings", {})

    @property
    def race(self) -> str | None:
        val = self.data.get("race")
        return str(val) if val is not None else None

    @property
    def background(self) -> str | None:
        val = self.data.get("background")
        return str(val) if val is not None else None

    @property
    def owner(self) -> int | None:
        raw = self.data.get("owner")
        return int(raw) if raw is not None else None

    @property
    def upstream(self) -> str | None:
        val = self.data.get("upstream")
        return str(val) if val is not None else None

    @property
    def sheet_type(self) -> str | None:
        val = self.data.get("sheet_type")
        return str(val) if val is not None else None

    @property
    def cvars(self) -> Mapping[str, Any]:
        return dict(self.data.get("cvars") or {})

    def get_cvar(self, name: str, default: Any = None) -> Any:
        return self.data.setdefault("cvars", {}).get(str(name), default)

    def set_cvar(self, name: str, val: Any) -> Any:
        self.data.setdefault("cvars", {})[str(name)] = val
        return val

    def set_cvar_nx(self, name: str, val: Any) -> Any:
        cvars = self.data.setdefault("cvars", {})
        return cvars.setdefault(str(name), val)

    def delete_cvar(self, name: str) -> Any:
        return self.data.setdefault("cvars", {}).pop(str(name), None)

    @property
    def consumables(self) -> list[AliasCustomCounter]:
        return [AliasCustomCounter(v) for v in self._consumable_map().values()]

    def cc(self, name: str) -> AliasCustomCounter:
        return AliasCustomCounter(self._consumable_map()[str(name)])

    def get_cc(self, name: str) -> int:
        return self.cc(name).value

    def get_cc_max(self, name: str) -> int:
        return self.cc(name).max

    def get_cc_min(self, name: str) -> int:
        return self.cc(name).min

    def set_cc(self, name: str, value: int | None = None, maximum: int | None = None, minimum: int | None = None) -> int:
        con = self._consumable_map().setdefault(str(name), {"name": str(name)})
        if value is not None:
            con["value"] = int(value)
        if maximum is not None:
            con["max"] = int(maximum)
        if minimum is not None:
            con["min"] = int(minimum)
        return _safe_int(con.get("value"), 0)

    def mod_cc(self, name: str, val: int, strict: bool = False) -> int:
        counter = self.cc(name)
        return counter.mod(val, strict)

    def delete_cc(self, name: str) -> Any:
        return self._consumable_map().pop(str(name), None)

    def create_cc_nx(
        self,
        name: str,
        minVal: str | None = None,
        maxVal: str | None = None,
        reset: str | None = None,
        dispType: str | None = None,
        reset_to: str | None = None,
        reset_by: str | None = None,
        title: str | None = None,
        desc: str | None = None,
        initial_value: str | None = None,
    ) -> AliasCustomCounter:
        if not self.cc_exists(name):
            self.create_cc(
                name,
                minVal=minVal,
                maxVal=maxVal,
                reset=reset,
                dispType=dispType,
                reset_to=reset_to,
                reset_by=reset_by,
                title=title,
                desc=desc,
                initial_value=initial_value,
            )
        return self.cc(name)

    def create_cc(
        self,
        name: str,
        minVal: str | None = None,
        maxVal: str | None = None,
        reset: str | None = None,
        dispType: str | None = None,
        reset_to: str | None = None,
        reset_by: str | None = None,
        title: str | None = None,
        desc: str | None = None,
        initial_value: str | None = None,
    ) -> AliasCustomCounter:
        payload = {
            "name": str(name),
            "min": _safe_int(minVal, -(2**31)) if minVal is not None else -(2**31),
            "max": _safe_int(maxVal, 2**31 - 1) if maxVal is not None else 2**31 - 1,
            "reset_on": reset,
            "display_type": dispType,
            "reset_to": _safe_int(reset_to) if reset_to is not None else None,
            "reset_by": reset_by,
            "title": title,
            "desc": desc,
            "value": _safe_int(initial_value, 0) if initial_value is not None else 0,
        }
        self._consumable_map()[str(name)] = payload
        return AliasCustomCounter(payload)

    def edit_cc(
        self,
        name: str,
        minVal: Any = UNSET,
        maxVal: Any = UNSET,
        reset: Any = UNSET,
        dispType: Any = UNSET,
        reset_to: Any = UNSET,
        reset_by: Any = UNSET,
        title: Any = UNSET,
        desc: Any = UNSET,
        new_name: str | None = None,
    ) -> AliasCustomCounter:
        counter = dict(self._consumable_map().get(str(name)) or {"name": str(name)})
        for key, val in (
            ("min", minVal),
            ("max", maxVal),
            ("reset_on", reset),
            ("display_type", dispType),
            ("reset_to", reset_to),
            ("reset_by", reset_by),
            ("title", title),
            ("desc", desc),
        ):
            if val is not UNSET:
                counter[key] = val
        counter["name"] = str(new_name) if new_name else counter.get("name", str(name))
        self._consumable_map().pop(str(name), None)
        self._consumable_map()[counter["name"]] = counter
        return AliasCustomCounter(counter)

    def cc_exists(self, name: str) -> bool:
        return str(name) in self._consumable_map()

    def cc_str(self, name: str) -> str:
        return str(self.cc(name))

    @property
    def death_saves(self) -> AliasDeathSaves:
        return AliasDeathSaves(self.data.get("death_saves") or {})

    @property
    def description(self) -> str | None:
        val = self.data.get("description")
        return str(val) if val is not None else None

    @property
    def image(self) -> str | None:
        val = self.data.get("image")
        return str(val) if val is not None else None


# === Combat API ===
@dataclass
class SimpleEffectAPI(_DirMixin):
    data: MutableMapping[str, Any]
    ATTRS: ClassVar[list[str]] = [
        "name",
        "duration",
        "remaining",
        "effect",
        "attacks",
        "buttons",
        "conc",
        "desc",
        "ticks_on_end",
        "combatant_name",
        "parent",
        "children",
    ]
    METHODS: ClassVar[list[str]] = ["set_parent"]

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Effect"))

    @property
    def duration(self) -> int | None:
        raw = self.data.get("duration")
        return int(raw) if raw is not None else None

    @property
    def remaining(self) -> int | None:
        raw = self.data.get("remaining")
        return int(raw) if raw is not None else None

    @property
    def effect(self) -> Mapping[str, Any]:
        return self.data.get("effects") or self.data.get("effect") or {}

    @property
    def attacks(self) -> list[Mapping[str, Any]]:
        return list(self.data.get("attacks") or [])

    @property
    def buttons(self) -> list[Mapping[str, Any]]:
        return list(self.data.get("buttons") or [])

    @property
    def conc(self) -> bool:
        return bool(self.data.get("conc") or self.data.get("concentration", False))

    @property
    def desc(self) -> str | None:
        val = self.data.get("desc")
        return str(val) if val is not None else None

    @property
    def ticks_on_end(self) -> bool:
        return bool(self.data.get("ticks_on_end") or self.data.get("end_on_turn_end", False))

    @property
    def combatant_name(self) -> str | None:
        val = self.data.get("combatant_name")
        return str(val) if val is not None else None

    @property
    def parent(self) -> SimpleEffectAPI | None:
        parent = self.data.get("parent")
        return SimpleEffectAPI(parent) if parent else None

    @property
    def children(self) -> list[SimpleEffectAPI]:
        return [SimpleEffectAPI(c) for c in self.data.get("children", [])]

    def set_parent(self, parent: "SimpleEffectAPI") -> None:
        self.data["parent"] = parent.data

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))


@dataclass
class CombatantAPI(AliasStatBlock):
    ATTRS: ClassVar[list[str]] = AliasStatBlock.ATTRS + [
        "effects",
        "init",
        "initmod",
        "type",
        "note",
        "controller",
        "group",
        "race",
        "monster_name",
        "is_hidden",
    ]
    METHODS: ClassVar[list[str]] = AliasStatBlock.METHODS + [
        "save",
        "damage",
        "set_ac",
        "set_maxhp",
        "set_init",
        "set_name",
        "set_group",
        "set_note",
        "get_effect",
        "add_effect",
        "remove_effect",
    ]

    def __post_init__(self) -> None:
        super().__post_init__()
        self.data.setdefault("type", "combatant")

    @property
    def effects(self) -> list[SimpleEffectAPI]:
        return [SimpleEffectAPI(e) for e in self.data.get("effects", [])]

    @property
    def init(self) -> int:
        return _safe_int(self.data.get("init"), 0)

    @property
    def initmod(self) -> int:
        return _safe_int(self.data.get("initmod"), 0)

    @property
    def type(self) -> str:
        return str(self.data.get("type", "combatant"))

    @property
    def note(self) -> str | None:
        val = self.data.get("note")
        return str(val) if val is not None else None

    @property
    def controller(self) -> int | None:
        raw = self.data.get("controller")
        return int(raw) if raw is not None else None

    @property
    def group(self) -> str | None:
        val = self.data.get("group")
        return str(val) if val is not None else None

    @property
    def race(self) -> str | None:
        val = self.data.get("race")
        return str(val) if val is not None else None

    @property
    def monster_name(self) -> str | None:
        val = self.data.get("monster_name")
        return str(val) if val is not None else None

    @property
    def is_hidden(self) -> bool:
        return bool(self.data.get("is_hidden", False))

    def save(self, ability: str, adv: bool | None = None) -> dict[str, Any]:
        roll = self.saves.get(ability)
        return {"roll": roll.d20(base_adv=adv), "mod": roll.value}

    def damage(self, dice_str: str, crit: bool = False, d=None, c=None, critdice: int = 0, overheal: bool = False) -> dict[str, Any]:
        return {"damage": f"{dice_str}{' (CRIT)' if crit else ''}", "total": dice_str, "roll": {"crit": crit}}

    def set_ac(self, ac: int) -> None:
        self.data["ac"] = int(ac)

    def set_maxhp(self, maxhp: int) -> None:
        self.data["max_hp"] = int(maxhp)

    def set_init(self, init: int) -> None:
        self.data["init"] = int(init)

    def set_name(self, name: str) -> None:
        self.data["name"] = str(name)

    def set_group(self, group: str | None) -> str | None:
        self.data["group"] = str(group) if group is not None else None
        return self.group

    def set_note(self, note: str) -> None:
        self.data["note"] = str(note)

    def get_effect(self, name: str, strict: bool = False) -> SimpleEffectAPI | None:
        name_lower = str(name).lower()
        for effect in self.effects:
            if strict and effect.name.lower() == name_lower:
                return effect
            if not strict and name_lower in effect.name.lower():
                return effect
        return None

    def add_effect(self, name: str, duration: int | None = None, **kwargs: Any) -> SimpleEffectAPI:
        payload = {"name": str(name), "duration": duration, **kwargs}
        self.data.setdefault("effects", []).append(payload)
        return SimpleEffectAPI(payload)

    def remove_effect(self, name: str, strict: bool = False) -> None:
        effect = self.get_effect(name, strict)
        if effect:
            self.data["effects"].remove(effect.data)


@dataclass
class SimpleGroupAPI(_DirMixin):
    data: MutableMapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["combatants", "type", "init", "name", "id"]
    METHODS: ClassVar[list[str]] = ["get_combatant", "set_init"]

    @property
    def combatants(self) -> list[CombatantAPI]:
        return [CombatantAPI(c) for c in self.data.get("combatants", [])]

    @property
    def type(self) -> str:
        return str(self.data.get("type", "group"))

    @property
    def init(self) -> int:
        return _safe_int(self.data.get("init"), 0)

    @property
    def name(self) -> str:
        return str(self.data.get("name", "Group"))

    @property
    def id(self) -> str | None:
        val = self.data.get("id")
        return str(val) if val is not None else None

    def get_combatant(self, name: str, strict: bool | None = None) -> CombatantAPI | None:
        name_lower = str(name).lower()
        for combatant in self.combatants:
            if strict is True and combatant.name.lower() == name_lower:
                return combatant
            if strict is None and combatant.name.lower() == name_lower:
                return combatant
            if strict is False and name_lower in combatant.name.lower():
                return combatant
        if strict is None:
            for combatant in self.combatants:
                if name_lower in combatant.name.lower():
                    return combatant
        return None

    def set_init(self, init: int) -> None:
        self.data["init"] = int(init)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))


@dataclass
class CombatAPI(_DirMixin):
    data: MutableMapping[str, Any] = field(default_factory=dict)
    ATTRS: ClassVar[list[str]] = ["combatants", "groups", "me", "current", "name", "round_num", "turn_num", "metadata"]
    METHODS: ClassVar[list[str]] = ["get_combatant", "get_group", "set_metadata", "get_metadata", "delete_metadata", "set_round", "end_round"]

    @property
    def combatants(self) -> list[CombatantAPI]:
        return [CombatantAPI(c) for c in self.data.get("combatants", [])]

    @property
    def groups(self) -> list[SimpleGroupAPI]:
        return [SimpleGroupAPI(g) for g in self.data.get("groups", [])]

    @property
    def me(self) -> CombatantAPI | None:
        me_data = self.data.get("me")
        return CombatantAPI(me_data) if me_data is not None else None

    @property
    def current(self) -> CombatantAPI | SimpleGroupAPI | None:
        cur = self.data.get("current")
        if cur is None:
            return None
        if cur.get("type") == "group":
            return SimpleGroupAPI(cur)
        return CombatantAPI(cur)

    @property
    def name(self) -> str | None:
        val = self.data.get("name")
        return str(val) if val is not None else None

    @property
    def round_num(self) -> int:
        return _safe_int(self.data.get("round_num"), 1)

    @property
    def turn_num(self) -> int:
        return _safe_int(self.data.get("turn_num"), 1)

    @property
    def metadata(self) -> MutableMapping[str, Any]:
        return self.data.setdefault("metadata", {})

    def get_combatant(self, name: str, strict: bool | None = None) -> CombatantAPI | None:
        name_lower = str(name).lower()
        for combatant in self.combatants:
            if strict is True and combatant.name.lower() == name_lower:
                return combatant
            if strict is None and combatant.name.lower() == name_lower:
                return combatant
            if strict is False and name_lower in combatant.name.lower():
                return combatant
        if strict is None:
            for combatant in self.combatants:
                if name_lower in combatant.name.lower():
                    return combatant
        return None

    def get_group(self, name: str, strict: bool | None = None) -> SimpleGroupAPI | None:
        name_lower = str(name).lower()
        for group in self.groups:
            if strict is True and group.name.lower() == name_lower:
                return group
            if strict is None and group.name.lower() == name_lower:
                return group
            if strict is False and name_lower in group.name.lower():
                return group
        if strict is None:
            for group in self.groups:
                if name_lower in group.name.lower():
                    return group
        return None

    def set_metadata(self, k: str, v: str) -> None:
        self.metadata[str(k)] = str(v)

    def get_metadata(self, k: str, default: Any = None) -> Any:
        return self.metadata.get(str(k), default)

    def delete_metadata(self, k: str) -> Any:
        return self.metadata.pop(str(k), None)

    def set_round(self, round_num: int) -> None:
        self.data["round_num"] = int(round_num)

    def end_round(self) -> None:
        self.data["turn_num"] = 0
        self.data["round_num"] = self.round_num + 1

    def __getattr__(self, item: str) -> Any:
        return self.data.get(item)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, str(item))
