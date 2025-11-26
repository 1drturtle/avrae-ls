from pathlib import Path

import pytest

from avrae_ls.api import CharacterAPI
from avrae_ls.completions import hover_for_position
from avrae_ls.config import AvraeLSConfig, VarSources
from avrae_ls.context import ContextData, ContextBuilder, GVarResolver
from avrae_ls.signature_help import load_signatures


def _ctx_with_vars() -> tuple[ContextData, GVarResolver]:
    ctx = ContextData(vars=VarSources.from_data({"cvars": {"foo": 123}}))
    resolver = GVarResolver(AvraeLSConfig.default(Path(".")))
    resolver.reset({"g1": "hello"})
    return ctx, resolver


def test_hover_shows_var_value_and_type():
    ctx_data, resolver = _ctx_with_vars()
    hover = hover_for_position("foo\n", 0, 1, {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "foo" in text
    assert "int" in text
    assert "123" in text


def test_hover_shows_local_constant_value():
    ctx_data = ContextData()
    resolver = GVarResolver(AvraeLSConfig.default(Path(".")))
    hover = hover_for_position("x = 3\n", 0, 0, {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "x" in text
    assert "int" in text
    assert "3" in text


def test_hover_handles_attribute_inside_call():
    ctx_data, resolver = _ctx_with_vars()
    code = "res = character().attacks\n"
    hover = hover_for_position(code, 0, code.index("attacks") + 2, {}, ctx_data, resolver)
    assert hover is not None
    assert "character().attacks" in hover.contents.value


@pytest.mark.parametrize(
    ("code", "needle", "expected"),
    [
        ("character().coinpurse.pp", "pp", "Platinum"),
        ("character().resistances.resist", "resist", "resist"),
        ("character().spellbook.dc", "dc", "Save DC"),
    ],
)
def test_hover_populates_missing_property_docs(code: str, needle: str, expected: str):
    ctx_data = ContextData()
    resolver = GVarResolver(AvraeLSConfig.default(Path(".")))
    hover = hover_for_position(code, 0, code.index(needle) + 1, {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert expected in text


def test_hover_shows_function_signature_and_doc():
    ctx_data = ContextData()
    resolver = GVarResolver(AvraeLSConfig.default(Path(".")))
    sigs = load_signatures()
    code = "get('foo')\n"
    hover = hover_for_position(code, 0, code.index("get") + 1, sigs, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "get(name" in text
    assert "local > cvar > uvar" in text


def test_hover_resolves_attribute_binding_value():
    config = AvraeLSConfig.default(Path("."))
    builder = ContextBuilder(config)
    ctx_data = builder.build()
    resolver = builder.gvar_resolver
    code = "x = character()\ny = character().name\n"
    hover = hover_for_position(code, 1, 0, {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "y" in text
    assert "str" in text
    assert "Aelar" in text


def test_hover_resolves_attribute_via_variable():
    config = AvraeLSConfig.default(Path("."))
    builder = ContextBuilder(config)
    ctx_data = builder.build()
    resolver = builder.gvar_resolver
    code = "x = character()\ny = x.name\n"
    hover = hover_for_position(code, 1, 0, {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "y" in text
    assert "str" in text
    assert "Aelar" in text


def test_hover_resolves_method_call_on_binding():
    config = AvraeLSConfig.default(Path("."))
    builder = ContextBuilder(config)
    ctx_data = builder.build()
    resolver = builder.gvar_resolver
    code = "x = character()\ny = x.levels\nz = y.get('Fighter')\n"
    hover = hover_for_position(code, 2, 0, {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "z" in text
    assert "int" in text
    assert "3" in text


def test_hover_prefers_binding_type_over_type_map():
    config = AvraeLSConfig.default(Path("."))
    builder = ContextBuilder(config)
    ctx_data = builder.build()
    resolver = builder.gvar_resolver
    code = "x = character().attacks\n"
    hover = hover_for_position(code, 0, 0, {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "AliasAttackList" in text


def test_hover_shows_element_type_for_list_bindings():
    config = AvraeLSConfig.default(Path("."))
    builder = ContextBuilder(config)
    ctx_data = builder.build()
    resolver = builder.gvar_resolver
    code = "x = character().actions\n"
    hover = hover_for_position(code, 0, 0, {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "list[AliasAction]" in text


def test_hover_does_not_call_unsafe_methods(monkeypatch: pytest.MonkeyPatch):
    config = AvraeLSConfig.default(Path("."))
    builder = ContextBuilder(config)
    ctx_data = builder.build()
    resolver = builder.gvar_resolver

    def _boom(*_args, **_kwargs):
        raise AssertionError("unsafe method should not be called")

    monkeypatch.setattr(CharacterAPI, "set_cvar", _boom)
    code = "x = character()\ny = x.set_cvar('foo', 'bar')\n"
    hover = hover_for_position(code, 1, 0, {}, ctx_data, resolver)
    assert hover is None


def test_hover_for_loop_target():
    ctx_data = ContextData()
    resolver = GVarResolver(AvraeLSConfig.default(Path(".")))
    code = "for i in range(3):\n    return i\n"
    hover = hover_for_position(code, 1, code.index("i", code.index("return")), {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "i" in text
    assert "int" in text


def test_hover_inside_if():
    ctx_data = ContextData()
    resolver = GVarResolver(AvraeLSConfig.default(Path(".")))
    code = "for i in range(3):\n    x = 7\n    x"
    hover = hover_for_position(code, 2, len('    x'), {}, ctx_data, resolver)
    assert hover is not None
    text = hover.contents.value
    assert "7" in text
