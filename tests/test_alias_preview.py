import pytest

from avrae_ls.alias_preview import render_alias_command
from avrae_ls.alias_preview import simulate_command, validate_embed_payload
from avrae_ls.config import VarSources
from avrae_ls.context import ContextData, GVarResolver
from avrae_ls.runtime import MockExecutor


def _ctx():
    return ContextData(vars=VarSources())


def _resolver(tmp_path):
    from avrae_ls.config import AvraeLSConfig

    cfg = AvraeLSConfig.default(tmp_path)
    res = GVarResolver(cfg)
    res.reset({})
    return res


@pytest.mark.asyncio
async def test_preview_preserves_newlines(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    alias_text = "!alias hello echo\n<drac2>\nx = 3\ny = 4\nreturn x + y\n</drac2>\nworld"

    rendered = await render_alias_command(alias_text, executor, ctx, resolver)
    assert rendered.command == "echo\n7\nworld"


@pytest.mark.asyncio
async def test_preview_plain_alias(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    alias_text = "!alias hello echo world!"

    rendered = await render_alias_command(alias_text, executor, ctx, resolver)
    assert rendered.command == "echo world!"


@pytest.mark.asyncio
async def test_preview_argument_parsing(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    alias_text = '!alias hello echo %1% <drac2>return "&1&"</drac2>'

    rendered = await render_alias_command(alias_text, executor, ctx, resolver, args=["first arg"])
    assert rendered.command.startswith('echo "first arg"')
    assert rendered.last_value == "first arg"


def test_simulate_command_accepts_embed():
    payload = '-title "Hello"'
    preview, name, validation = simulate_command(f"embed {payload}")
    assert name == "embed"
    assert preview == payload
    assert validation is None


def test_simulate_command_embeds_validate_unknown_keys():
    payload = "-foo 1"
    _preview, _name, validation = simulate_command(f"embed {payload}")
    assert validation
    ok, err = validate_embed_payload(payload)
    assert not ok
    assert "unknown flag" in err


def test_validate_embed_flag_style():
    payload = '-title "Hello" -desc "World" -color #ff00ff -t 120 -f "Donuts|I have 15 donuts|inline"'
    ok, err = validate_embed_payload(payload)
    assert ok
    assert err is None


def test_validate_embed_flag_unknown():
    ok, err = validate_embed_payload("-foo bar")
    assert not ok
    assert "unknown flag" in err


def test_validate_embed_flag_color():
    ok, err = validate_embed_payload("-color nothex")
    assert not ok
    assert "color must be a 6-hex" in err


def test_validate_embed_field_format():
    ok, err = validate_embed_payload('-f "BadField"')
    assert not ok
    assert "field must be" in err.lower()
