from pathlib import Path

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


def test_loop_iteration_completions_use_element_type():
    code = "\n".join(
        [
            "x = character().attacks",
            "for i in x:",
            "    i.",
        ]
    )
    items = completion_items_for_position(code, line=2, character=len("    i."), suggestions=[])
    labels = {item.label for item in items}
    assert "name" in labels
    assert "verb" in labels


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
