from pathlib import Path

from avrae_ls.cvars import derive_character_cvars
from avrae_ls.config import AvraeLSConfig, VarSources
from avrae_ls.context import ContextBuilder


def test_var_sources_scope_order():
    sources = VarSources(cvars={"foo": "c"}, uvars={"foo": "u"}, svars={"server": "s"})
    names = sources.to_initial_names()
    assert names["foo"] == "c"
    assert "server" not in names


def test_derive_character_cvars_matches_table():
    character = {
        "name": "Ally",
        "stats": {"strength": 15, "dexterity": 12, "constitution": 8, "intelligence": 10, "wisdom": 13, "charisma": 9, "prof_bonus": 3},
        "saves": {"str": 5, "dex": 3, "con": 1, "int": 0, "wis": 4, "cha": 2},
        "levels": {"Fighter": 2, "Wizard": 1},
        "ac": 17,
        "max_hp": 30,
        "spellbook": {"spell_mod": 4},
        "description": "Ranger of the north",
        "image": "https://example.invalid/ally.png",
        "csettings": {"color": 255},
    }

    cvars = derive_character_cvars(character)
    assert cvars["strength"] == 15
    assert cvars["strengthMod"] == 2
    assert cvars["strengthSave"] == 5
    assert cvars["dexterity"] == 12
    assert cvars["dexterityMod"] == 1
    assert cvars["dexteritySave"] == 3
    assert cvars["constitutionSave"] == 1
    assert cvars["charismaMod"] == -1
    assert cvars["armor"] == 17
    assert cvars["hp"] == 30
    assert cvars["level"] == 3
    assert cvars["FighterLevel"] == 2
    assert cvars["WizardLevel"] == 1
    assert cvars["proficiencyBonus"] == 3
    assert cvars["spell"] == 4
    assert cvars["description"] == "Ranger of the north"
    assert cvars["image"].startswith("https://example.invalid/")
    assert cvars["color"] == "ff"


def test_context_builder_binds_character_and_cvar_table(tmp_path: Path):
    cfg = AvraeLSConfig.default(tmp_path)
    cfg.profiles["default"].character["cvars"] = {"pet": "hawk"}
    cfg.profiles["default"].vars = VarSources.from_data({"uvars": {"timezone": "UTC"}, "svars": {"ignored": True}})

    ctx = ContextBuilder(cfg).build()
    names = ctx.vars.to_initial_names()

    assert names["pet"] == "hawk"
    assert names["timezone"] == "UTC"
    assert names["strength"] == cfg.profiles["default"].character["stats"]["strength"]
    assert "ignored" not in names
