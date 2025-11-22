from pathlib import Path

from avrae_ls.api import AliasContextAPI, CharacterAPI, CombatAPI
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
