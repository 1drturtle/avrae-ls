from pathlib import Path

import pytest

from avrae_ls.completions import (
    completion_items_for_position,
    gather_suggestions,
    hover_for_position,
)
from avrae_ls.config import AvraeLSConfig, VarSources
from avrae_ls.context import ContextBuilder, ContextData, GVarResolver
from avrae_ls.signature_help import load_signatures


def test_hover_out_of_bounds_cursor_does_not_crash():
    cfg = AvraeLSConfig.default(Path("."))
    ctx_data = ContextData()
    resolver = GVarResolver(cfg)
    # cursor beyond line length should not raise
    hover = hover_for_position("character.name\n", 0, 999, {}, ctx_data, resolver)
    # optional hover may be None; the important part is no exception
    assert hover is None or hover.contents


def test_attribute_completion_from_variable_binding():
    code = "x = character()\ny = x.\n"
    items = completion_items_for_position(code, 1, len("y = x."), [])
    labels = {item.label for item in items}
    assert "levels" in labels
    assert "name" in labels


def test_nested_attributes_completion():
    code = "x = character()\ny = x.levels\nz = y."
    items = completion_items_for_position(code, line=2, character=len("z = y."), suggestions=[])
    labels = {item.label for item in items}
    assert len(labels) != 0

def test_completions_attributes_correct():
    code = "x = character()."
    items = completion_items_for_position(code, line=0, character=len(code), suggestions=[])
    labels = {item.label for item in items}
    assert "name" in labels
    assert "argparse" not in labels

def test_completions_attributes_correct_complex():
    code = "x = {'a': character()}\ny = x.get('a')."
    items = completion_items_for_position(code, line=1, character=len("y = x.get('a')."), suggestions=[])
    labels = {item.label for item in items}
    assert "name" in labels
    assert "argparse" not in labels


def test_character_method_completion_has_signature():
    code = "x = character()."
    items = completion_items_for_position(code, line=0, character=len(code), suggestions=[])
    detail = {item.label: item.detail for item in items}
    assert any(sig for name, sig in detail.items() if name == "set_cvar" and "set_cvar(" in (sig or ""))


def test_character_method_hover_shows_signature():
    cfg = AvraeLSConfig.default(Path("."))
    builder = ContextBuilder(cfg)
    ctx_data = builder.build()
    resolver = builder.gvar_resolver
    code = "character().set_cvar"
    hover = hover_for_position(code, 0, code.index("set_cvar") + 2, {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "set_cvar(" in text


def test_character_nested_attribute_completions():
    code = "character().levels."
    items = completion_items_for_position(code, line=0, character=len(code), suggestions=[])
    labels = {item.label for item in items}
    # AliasCustomCounter attrs from API should be exposed
    assert "total_level" in labels


def test_character_consumables_attribute_completions():
    code = "character().consumables[0]."
    items = completion_items_for_position(code, line=0, character=len(code), suggestions=[])
    labels = {item.label for item in items}
    assert "name" in labels
    assert "value" in labels
    assert "max" in labels
    assert "reset_on" in labels


def test_character_attacks_attribute_completions():
    code = "character().attacks[0]."
    items = completion_items_for_position(code, line=0, character=len(code), suggestions=[])
    labels = {item.label for item in items}
    assert "name" in labels
    assert "verb" in labels
    assert "raw" in labels


def test_character_attacks_attribute_completions_with_binding():
    code = "\n".join(
        [
            "x = character().attacks",
            "y = x[0].",
        ]
    )
    items = completion_items_for_position(code, line=1, character=len("y = x[0]."), suggestions=[])
    labels = {item.label for item in items}
    assert "name" in labels
    assert "verb" in labels
    assert "raw" in labels


def test_character_skill_completion_uses_alias_skill():
    code = "x = character().skills.arcana\nx."
    items = completion_items_for_position(code, line=1, character=len("x."), suggestions=[])
    labels = {item.label for item in items}
    assert {"value", "bonus", "adv"}.issubset(labels)
    assert "levels" not in labels


def test_builtin_list_completions():
    code = "arr = []\narr."
    items = completion_items_for_position(code, line=1, character=len("arr."), suggestions=[])
    labels = {item.label for item in items}
    assert "append" in labels
    assert "pop" in labels


def test_builtin_dict_completions():
    code = "data = {'a': 1}\ndata."
    items = completion_items_for_position(code, line=1, character=len("data."), suggestions=[])
    labels = {item.label for item in items}
    assert "keys" in labels
    assert "items" in labels
    assert "get" in labels


def test_builtin_str_completions():
    code = "name = 'test'\nname."
    items = completion_items_for_position(code, line=1, character=len("name."), suggestions=[])
    labels = {item.label for item in items}
    assert "lower" in labels
    assert "split" in labels


def test_attribute_completion_inside_call_does_not_use_builtin_suggestions():
    cfg = AvraeLSConfig.default(Path("."))
    ctx_data = ContextData()
    resolver = GVarResolver(cfg)
    sigs = load_signatures()
    suggestions = gather_suggestions(ctx_data, resolver, sigs)

    code_no_index = "\n".join(
        [
            "x = []",
            "y = character().actions",
            "x.append(y.)",
        ]
    )
    items = completion_items_for_position(code_no_index, line=2, character=len("x.append(y."), suggestions=suggestions)
    labels = {item.label for item in items}
    assert "append" in labels  # list methods should surface
    assert "name" not in labels  # element attrs should not surface until indexed
    assert "abs" not in labels

    # When indexed, element attributes should surface (and still no builtins)
    code_indexed = "\n".join(
        [
            "x = []",
            "y = character().actions",
            "x.append(y[0].)",
        ]
    )
    items_idx = completion_items_for_position(code_indexed, line=2, character=len("x.append(y[0]."), suggestions=suggestions)
    labels_idx = {item.label for item in items_idx}
    assert "name" in labels_idx
    assert "abs" not in labels_idx


@pytest.mark.parametrize(
    ("code", "line", "character", "expected_labels"),
    [
        (
            "\n".join(["x = character().attacks", "for i in x:", "    i."]),
            2,
            len("    i."),
            {"name", "verb"},
        ),
        (
            "\n".join(["chars = [character()]", "for c in chars:", "    c."]),
            2,
            len("    c."),
            {"name", "actions"},
        ),
        (
            "\n".join(["data = {'hero': character()}", "hero = data.get('hero')", "hero."]),
            2,
            len("hero."),
            {"name", "skills"},
        ),
    ],
)
def test_inferred_element_type_completions(code: str, line: int, character: int, expected_labels: set[str]):
    items = completion_items_for_position(code, line=line, character=character, suggestions=[])
    labels = {item.label for item in items}
    for label in expected_labels:
        assert label in labels


def test_character_actions_attribute_completions():
    code = "character().actions[0]."
    items = completion_items_for_position(code, line=0, character=len(code), suggestions=[])
    labels = {item.label for item in items}
    assert "name" in labels
    assert "snippet" in labels


def test_completion_includes_signature_and_doc():
    cfg = AvraeLSConfig.default(Path("."))
    ctx_data = ContextData(vars=VarSources())
    resolver = GVarResolver(cfg)
    sigs = load_signatures()
    suggestions = gather_suggestions(ctx_data, resolver, sigs)
    get_sugg = next(s for s in suggestions if s.name == "get")
    assert "get(name" in get_sugg.detail
    assert "cvar" in (get_sugg.documentation or "")


def test_argparse_completions_return_parsed_arguments_methods():
    code = "\n".join(
        [
            "arg_list = 'one two'",
            "args = argparse(arg_list)",
            "args.",
        ]
    )
    items = completion_items_for_position(code, line=2, character=len("args."), suggestions=[])
    labels = {item.label for item in items}
    assert "get" in labels
    assert "adv" in labels
    assert "update_nx" in labels


def test_argparse_hover_shows_parsed_arguments_type():
    cfg = AvraeLSConfig.default(Path("."))
    ctx_data = ContextData(vars=VarSources())
    resolver = GVarResolver(cfg)
    code = "args = argparse('one two')\nargs"
    hover = hover_for_position(code, line=1, character=len("args"), sigs={}, ctx_data=ctx_data, resolver=resolver)
    assert hover is not None
    assert "ParsedArguments" in hover.contents.value


def test_parameter_annotation_infers_type():
    code = 'def use_roll(res: "SimpleRollResult"):\n    res.'
    items = completion_items_for_position(code, line=1, character=len("    res."), suggestions=[])
    labels = {item.label for item in items}
    assert "dice" in labels


def test_plain_identifier_named_like_type_does_not_infer():
    code = "category = None\ncategory."
    items = completion_items_for_position(code, line=1, character=len("category."), suggestions=[])
    labels = {item.label for item in items}
    assert "name" not in labels
    assert "id" not in labels


def test_spellbook_completions_only_from_character():
    bare_code = "spellbook = None\nspellbook."
    bare_items = completion_items_for_position(bare_code, line=1, character=len("spellbook."), suggestions=[])
    bare_labels = {item.label for item in bare_items}
    assert "spells" not in bare_labels

    via_char_code = "sb = character().spellbook\nsb."
    via_char_items = completion_items_for_position(via_char_code, line=1, character=len("sb."), suggestions=[])
    via_char_labels = {item.label for item in via_char_items}
    assert "spells" in via_char_labels
    assert "dc" in via_char_labels


def test_channel_completions_only_from_ctx():
    bare_code = "channel = None\nchannel."
    bare_items = completion_items_for_position(bare_code, line=1, character=len("channel."), suggestions=[])
    bare_labels = {item.label for item in bare_items}
    assert "topic" not in bare_labels

    via_ctx_code = "chan = ctx.channel\nchan."
    via_ctx_items = completion_items_for_position(via_ctx_code, line=1, character=len("chan."), suggestions=[])
    via_ctx_labels = {item.label for item in via_ctx_items}
    assert "topic" in via_ctx_labels
    assert "parent" in via_ctx_labels


def test_effect_completions_only_from_combatant():
    bare_code = "effect = None\neffect."
    bare_items = completion_items_for_position(bare_code, line=1, character=len("effect."), suggestions=[])
    bare_labels = {item.label for item in bare_items}
    assert "duration" not in bare_labels

    via_combat_code = "eff = combat().combatants[0].effects[0]\neff."
    via_combat_items = completion_items_for_position(via_combat_code, line=1, character=len("eff."), suggestions=[])
    via_combat_labels = {item.label for item in via_combat_items}
    assert "duration" in via_combat_labels
    assert "name" in via_combat_labels


def test_safe_methods_evaluated_for_constant_bindings():
    cfg = AvraeLSConfig.default(Path("."))
    builder = ContextBuilder(cfg)
    ctx_data = builder.build()
    resolver = builder.gvar_resolver

    resist_code = "val = character().resistances.is_resistant('fire')\nval"
    resist_hover = hover_for_position(resist_code, line=1, character=len("val"), sigs={}, ctx_data=ctx_data, resolver=resolver)
    assert resist_hover is not None
    assert "bool" in resist_hover.contents.value

    slots_code = "slots = character().spellbook.get_slots(1)\nslots"
    slots_hover = hover_for_position(slots_code, line=1, character=len("slots"), sigs={}, ctx_data=ctx_data, resolver=resolver)
    assert slots_hover is not None
    assert "int" in slots_hover.contents.value
