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
    assert char.set_cc("Arrows", value=10, maximum=50) == 10
    assert char.get_cc_max("Arrows") == 50
    assert char.delete_cc("Arrows") is not None
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
