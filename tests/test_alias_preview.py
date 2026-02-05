import pytest

from avrae_ls.runtime.alias_preview import render_alias_command
from avrae_ls.runtime.alias_preview import simulate_command, validate_embed_payload
from avrae_ls.config import VarSources
from avrae_ls.runtime.context import ContextData, GVarResolver
from avrae_ls.runtime.runtime import MockExecutor


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


@pytest.mark.asyncio
async def test_preview_inline_expression(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    alias_text = "!alias hello echo value {{1 + 2}}"

    rendered = await render_alias_command(alias_text, executor, ctx, resolver)
    assert rendered.error is None
    assert rendered.command == "echo value 3"
    assert rendered.last_value == 3


@pytest.mark.asyncio
async def test_preview_inline_roll(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    alias_text = "!alias hello echo rolled {1}"

    rendered = await render_alias_command(alias_text, executor, ctx, resolver)
    assert rendered.error is None
    assert rendered.command == "echo rolled 1"


def test_simulate_command_accepts_embed():
    payload = '-title "Hello"'
    simulated = simulate_command(f"embed {payload}")
    assert simulated.command_name == "embed"
    assert simulated.preview == payload
    assert simulated.validation_error is None


def test_simulate_command_embeds_validate_unknown_keys():
    payload = "-foo 1"
    simulated = simulate_command(f"embed {payload}")
    assert simulated.validation_error
    ok, err = validate_embed_payload(payload)
    assert not ok
    assert err is not None

    assert "unknown flag" in err


def test_validate_embed_flag_style():
    payload = '-title "Hello" -desc "World" -color #ff00ff -t 120 -f "Donuts|I have 15 donuts|inline"'
    ok, err = validate_embed_payload(payload)
    assert ok
    assert err is None


def test_validate_embed_flag_unknown():
    ok, err = validate_embed_payload("-foo bar")
    assert not ok
    assert err is not None

    assert "unknown flag" in err


def test_validate_embed_flag_color():
    ok, err = validate_embed_payload("-color nothex")
    assert not ok
    assert err is not None
    assert "color must be a 6-hex" in err


def test_validate_embed_field_format():
    ok, err = validate_embed_payload('-f "BadField"')
    assert not ok
    assert err is not None
    assert "field must be" in err.lower()


def test_validate_embed_color_placeholder():
    ok, err = validate_embed_payload("-color <color>")
    assert ok
    assert err is None


def test_validate_embed_supports_avrae_quote_pairs():
    payload = "-title \u201cHello World\u201d -desc \u00abFlavor text\u00bb -f \u300cName|Value|inline\u300d"
    ok, err = validate_embed_payload(payload)
    simulated = simulate_command(f"embed {payload}")

    assert ok
    assert err is None
    assert simulated.embed is not None
    assert simulated.embed.title == "Hello World"
    assert simulated.embed.description == "Flavor text"
    assert simulated.embed.fields and simulated.embed.fields[0].name == "Name"


def test_validate_embed_reports_unclosed_quote_pairs():
    ok, err = validate_embed_payload("-title \u201cHello")
    assert not ok
    assert err is not None
    assert "Expected closing quote" in err


def test_simulate_command_returns_embed_preview():
    payload = '-title "Hello" -desc "World" -color #ABCDEF -t 30 -thumb http://thumb -image http://image -footer "Footer" -f "Name|Value|inline"'
    simulated = simulate_command(f"embed {payload}")
    embed = simulated.embed
    assert simulated.command_name == "embed"
    assert simulated.validation_error is None
    assert embed is not None
    assert embed.title == "Hello"
    assert embed.description == "World"
    assert embed.color == "#ABCDEF"
    assert embed.timeout == 30
    assert embed.thumbnail == "http://thumb"
    assert embed.image == "http://image"
    assert embed.footer == "Footer"
    assert embed.fields and embed.fields[0].name == "Name" and embed.fields[0].inline


def test_simulate_command_detects_embed_without_keyword():
    payload = '-title abc -desc def -f "A|B|inline"'
    simulated = simulate_command(payload)
    assert simulated.command_name == "embed"
    assert simulated.preview == payload
    assert simulated.embed is not None


def test_simulate_command_with_embed_prefix_and_payload_on_newline():
    payload = "-title abc"
    simulated = simulate_command(f"embed\n{payload}")
    assert simulated.command_name == "embed"
    assert simulated.preview is not None

    assert simulated.preview.strip() == payload


def test_simulate_command_finds_embed_after_intro_text():
    payload = "-title abc"
    simulated = simulate_command(f"# heading\nembed {payload}")
    assert simulated.command_name == "embed"
    assert simulated.preview is not None

    assert simulated.preview.strip().endswith(payload)


def test_simulate_command_supports_multiple_flag_lines():
    payload = "-title abc\n-title def"
    simulated = simulate_command(payload)
    assert simulated.command_name == "embed"
    assert simulated.preview is not None
    assert "-title abc" in simulated.preview
    assert "-title def" in simulated.preview


def test_simulate_command_strips_alias_header():
    alias = '!alias next embed\n-title "Done?"'
    simulated = simulate_command(alias)
    assert simulated.command_name == "embed"
    assert simulated.preview is not None
    assert "Done?" in simulated.preview
