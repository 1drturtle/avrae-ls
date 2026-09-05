from pathlib import Path

from avrae_ls.runtime.api import AliasContextAPI, CharacterAPI, CombatAPI
from avrae_ls.config import AvraeLSConfig


def _profile():
    cfg = AvraeLSConfig.default(Path("."))
    return cfg.profiles[cfg.default_profile]


def test_character_api_mock_schema():
    profile = _profile()
    ch = CharacterAPI(profile.character)
    assert ch.name == "Aelar Wyn"
    assert ch.stats.prof_bonus == 3
    assert ch.levels.total_level == 5
    assert ch.skills.athletics.value >= 5
    assert ch.saves.get("str").value >= 5
    assert ch.coinpurse.total > 0
    assert ch.cc_exists("Hit Dice")
    start = ch.get_cc("Hit Dice")
    ch.mod_cc("Hit Dice", -1)
    assert ch.get_cc("Hit Dice") == start - 1
    ch.reset_hp()
    assert ch.hp == ch.max_hp
    assert ch.spellbook.can_cast("Cure Wounds", 1)


def test_get_cvar_does_not_mutate_character():
    data: dict[str, object] = {}
    ch = CharacterAPI(data)

    assert ch.get_cvar("missing", default="fallback") == "fallback"
    assert "cvars" not in data


def test_combat_api_mock_schema():
    profile = _profile()
    cmb = CombatAPI(profile.combat)
    assert cmb.round_num >= 1
    assert cmb.me is not None
    assert cmb.get_combatant(cmb.me.name) is not None
    assert cmb.get_group("Goblins") is not None
    assert cmb.get_metadata("scene") == "Forest road"
    cmb.set_metadata("note", "testing")
    assert cmb.get_metadata("note") == "testing"
    cmb.end_round()
    assert cmb.turn_num == 0


def test_ctx_api_mock_schema():
    profile = _profile()
    ctx = AliasContextAPI(profile.ctx)
    assert ctx.guild.name
    assert ctx.channel.name
    assert ctx.author.display_name
    assert len(ctx.author.get_roles()) >= 1


def test_character_attack_list_helpers():
    char = CharacterAPI(
        {
            "attacks": [
                {"name": "Claw", "verb": "slashes", "raw": {"damage": "1d4+2 slashing"}},
                {"name": "Bite"},
            ]
        }
    )
    attacks = char.attacks
    assert len(attacks) == 2
    first = attacks[0]
    assert first.verb == "slashes"
    assert str(first) == "Claw slashes: 1d4+2 slashing"
    assert str(attacks).splitlines()[-1] == "Bite"


def test_character_consumables_normalized_and_mutated():
    char = CharacterAPI({"consumables": [{"name": "Arrows", "value": 20, "max": 40}]})
    assert char.cc_exists("Arrows")
    assert char.get_cc("Arrows") == 20
    assert char.mod_cc("Arrows", -5) == 15
    char.edit_cc("Arrows", maxVal="50")
    assert char.set_cc("Arrows", value=10, strict=True) == 10
    assert char.get_cc_max("Arrows") == 50
    assert char.delete_cc("Arrows") is None
    assert not char.cc_exists("Arrows")


def test_spellbook_slot_helpers_and_casting():
    book = CharacterAPI(
        {
            "spellbook": {
                "slots": {1: 2, 2: 1},
                "max_slots": {1: 3, 2: 2},
                "max_pact_slots": 2,
                "num_pact_slots": 1,
                "spells": [{"name": "Magic Missile", "prepared": True}],
            }
        }
    ).spellbook

    assert book.slots_str(1) == "2/3"
    assert book.can_cast("Magic Missile", 1)
    assert book.find("magic missile")[0].prepared is True

    book.cast("Magic Missile", 2)
    assert book.get_slots(2) == 0

    book.reset_slots()
    assert book.get_slots(1) == 3

    book.reset_pact_slots()
    assert book.num_pact_slots == 2


def test_damage_mutates_hp_and_absorbs_temp_hp():
    from avrae_ls.runtime.api import SimpleCombatant

    target = SimpleCombatant({"hp": 20, "max_hp": 20, "temp_hp": 3})
    result = target.damage("5")
    assert result["total"] == 5
    assert target.hp == 18 and target.temp_hp == 0
    target.damage("-10")
    assert target.hp == 20
    target.damage("-10", overheal=True)
    assert target.hp == 30
    target.damage("100")
    assert target.hp == 0


def test_critical_damage_doubles_dice_only():
    from avrae_ls.runtime.api import SimpleCombatant

    target = SimpleCombatant({"hp": 100})
    assert target.damage("2d1+3", crit=True, d="1d1", c="1d1", critdice=1)["total"] == 11
    assert target.damage("2d1+3", crit=False, critdice=20, c="100")["total"] == 5


def test_damage_applies_typed_and_conditional_resistances():
    from avrae_ls.runtime.api import SimpleCombatant

    target = SimpleCombatant(
        {
            "hp": 100,
            "resistances": {
                "resist": [{"dtype": "fire"}, {"dtype": "slashing", "unless": ["magical"]}],
                "vuln": [{"dtype": "cold"}],
                "immune": [{"dtype": "poison"}],
            },
        }
    )
    assert target.damage("5[fire]+3[cold]+10[poison]")["total"] == 8
    assert target.damage("6[magical slashing]")["total"] == 6
    assert target.damage("6[slashing]")["total"] == 3
    assert target.hp == 83


def test_hp_mutator_contracts():
    from avrae_ls.runtime.api import SimpleCombatant

    target = SimpleCombatant({"hp": 10, "max_hp": 20, "temp_hp": 4})
    assert target.modify_hp(-3, ignore_temp=True) == "7/20 (4 temp)"
    assert target.temp_hp == 4
    assert target.set_hp(-1) is None and target.hp == 0
    assert target.set_temp_hp(-1) is None and target.temp_hp == 0


def test_counter_reset_contract_and_bounds():
    import pytest
    from avrae_ls.runtime.api import NoReset, CounterOutOfBounds

    char = CharacterAPI({"stats": {"strength": 16}})
    cc = char.create_cc("Uses", minVal="0", maxVal="strengthMod", reset_by="2d1", initial_value="2")
    result = cc.reset()
    assert (result.new_value, result.old_value, result.target_value) == (3, 2, 4)
    assert isinstance(result.delta, str)
    assert tuple(result)[:3] == (3, 2, 4)
    with pytest.raises(CounterOutOfBounds):
        char.set_cc("Uses", 4, strict=True)
    assert char.set_cc("Uses", 4) == 3
    with pytest.raises(NoReset):
        char.create_cc("Unbounded").reset()
    with pytest.raises(NoReset):
        char.create_cc("Never", maxVal="3", reset="none").reset()
    fixed = char.create_cc("Fixed", maxVal="10", reset_to="5", initial_value="2").reset()
    assert fixed.delta == "+3"


def test_counter_missing_does_not_create_placeholder():
    import pytest
    from avrae_ls.runtime.api import ConsumableException

    char = CharacterAPI({})
    with pytest.raises(ConsumableException):
        char.set_cc("missing", 1)
    assert not char.cc_exists("missing")


def test_slots_exhaustion_json_keys_and_pact_preference():
    import pytest
    from avrae_ls.runtime.api import AliasSpellbook, CounterOutOfBounds

    book = AliasSpellbook(
        {"slots": {"2": 3}, "max_slots": {"2": 4}, "pact_slot_level": 2, "num_pact_slots": 2, "max_pact_slots": 2}
    )
    book.use_slot(2)
    assert book.get_slots(2) == 2 and book.num_pact_slots == 1
    book.set_slots(2, 1, pact=False)
    assert book.num_pact_slots == 1
    book.reset_pact_slots()
    assert book.get_slots(2) == 2 and book.num_pact_slots == 2
    book.set_slots(2, 0)
    with pytest.raises(CounterOutOfBounds):
        book.use_slot(2)
