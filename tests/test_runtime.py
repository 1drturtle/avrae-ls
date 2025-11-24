import httpx
import pytest

from avrae_ls.argparser import InvalidArgument
from avrae_ls.config import AvraeServiceConfig, VarSources
from avrae_ls.context import ContextData, GVarResolver
from avrae_ls.runtime import FunctionRequiresCharacter, MockExecutor, _parse_coins


def _ctx():
    return ContextData(vars=VarSources())


def _ctx_with_character(data: dict | None = None):
    return ContextData(character=data or {"name": "Aelar"}, vars=VarSources())


def _resolver(tmp_path):
    from avrae_ls.config import AvraeLSConfig

    cfg = AvraeLSConfig.default(tmp_path)
    res = GVarResolver(cfg)
    res.reset({"foo": "bar", "mod": "answer = 'module-value'"})
    return res


@pytest.mark.parametrize(
    ("args", "include_total", "expected"),
    [
        ("+10", True, {"pp": 0, "gp": 10, "ep": 0, "sp": 0, "cp": 0, "total": 10}),
        ("-10.47", True, {"pp": 0, "gp": 0, "ep": 0, "sp": 0, "cp": -1047, "total": -10.47}),
        ("+10.388888", False, {"pp": 0, "gp": 10, "ep": 0, "sp": 3, "cp": 8}),
        ("10.388888", True, {"pp": 0, "gp": 10, "ep": 0, "sp": 3, "cp": 8, "total": 10.38}),
        ("+10cp +10gp -8ep", True, {"pp": 0, "gp": 10, "ep": -8, "sp": 0, "cp": 10, "total": 6.1}),
        ("+10cp10gp", True, {"pp": 0, "gp": 10, "ep": 0, "sp": 0, "cp": 10, "total": 10.1}),
        ("10cp-10   gp", False, {"pp": 0, "gp": -10, "ep": 0, "sp": 0, "cp": 10}),
        ("10     cp10    gp", True, {"pp": 0, "gp": 10, "ep": 0, "sp": 0, "cp": 10, "total": 10.1}),
        ("+1,001GP -50SP", True, {"pp": 0, "gp": 1001, "ep": 0, "sp": -50, "cp": 0, "total": 996.0}),
    ],
)
def test_parse_coins_parity(args, include_total, expected):
    assert _parse_coins(args, include_total=include_total) == expected


@pytest.mark.parametrize("invalid", ["", "abc", "10xp", "++10gp"])
def test_parse_coins_invalid(invalid):
    with pytest.raises(InvalidArgument):
        _parse_coins(invalid)


@pytest.mark.asyncio
async def test_character_requires_active_character(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)

    result = await executor.run("character()", ctx, resolver)
    assert result.error is not None
    assert isinstance(getattr(result.error, "__cause__", None), FunctionRequiresCharacter)


@pytest.mark.asyncio
async def test_mock_executor_runs_code(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)

    result = await executor.run("x = 1\nx + 2", ctx, resolver)
    assert result.error is None
    assert result.value == 3
    assert result.stdout == ""


@pytest.mark.asyncio
async def test_mock_executor_resolves_gvars(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)

    result = await executor.run("get_gvar('foo')", ctx, resolver)
    assert result.error is None
    assert result.value == "bar"


@pytest.mark.asyncio
async def test_alias_block_executes(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    from avrae_ls.parser import primary_block_or_source

    alias_text = "!alias hello echo\n<drac2>\nx = 3\nreturn x\n</drac2>"
    code, _, _ = primary_block_or_source(alias_text)
    result = await executor.run(code, ctx, resolver)
    assert result.error is None
    assert result.value == 3


@pytest.mark.asyncio
async def test_argparse_available(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    result = await executor.run("pa = argparse('one two'); pa.get('one')", ctx, resolver)
    assert result.error is None


@pytest.mark.asyncio
async def test_err_raises_alias_exception(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    result = await executor.run("err('boom')", ctx, resolver)
    assert result.error is not None
    assert "boom" in str(result.error)


@pytest.mark.asyncio
async def test_consumables_list_safe(tmp_path):
    executor = MockExecutor()
    ctx = ContextData(character={"consumables": {"Ki": {"name": "Ki", "value": 3, "max": 5}}}, vars=VarSources())
    resolver = _resolver(tmp_path)
    result = await executor.run("x=character(); x.consumables", ctx, resolver)
    assert result.error is None
    assert result.value is not None
    first = list(result.value)[0]
    assert getattr(first, "name") == "Ki"


@pytest.mark.asyncio
async def test_actions_list_safe(tmp_path):
    executor = MockExecutor()
    ctx = ContextData(
        character={
            "actions": [
                {"name": "Second Wind", "activation_type": 3, "activation_type_name": "BONUS_ACTION", "description": "+1d10+5"}
            ]
        },
        vars=VarSources(),
    )
    resolver = _resolver(tmp_path)
    result = await executor.run("x=character(); x.actions", ctx, resolver)
    assert result.error is None
    actions = list(result.value)
    assert actions and actions[0].name == "Second Wind"


@pytest.mark.asyncio
async def test_character_attributes_accessible(tmp_path):
    executor = MockExecutor()
    ctx = ContextData(
        character={
            "name": "A",
            "stats": {"strength": 10, "dexterity": 10, "constitution": 10, "intelligence": 10, "wisdom": 10, "charisma": 10},
            "levels": {"Fighter": 1},
            "attacks": [{"name": "Punch"}],
            "skills": {"athletics": {"value": 2}},
            "saves": {"str": 2},
            "resistances": {"resist": [], "vuln": [], "immune": [], "neutral": []},
            "ac": 10,
            "max_hp": 10,
            "hp": 10,
            "temp_hp": 0,
            "spellbook": {"spells": []},
            "creature_type": "humanoid",
            "actions": [{"name": "Action"}],
            "coinpurse": {"gp": 1},
            "csettings": {},
            "race": "Human",
            "background": "Soldier",
            "owner": 1,
            "upstream": "up",
            "cvars": {"foo": "bar"},
            "consumables": {"Ki": {"name": "Ki", "value": 1, "max": 1}},
            "death_saves": {"successes": 0, "fails": 0},
            "description": "",
            "image": "",
        },
        vars=VarSources(),
    )
    resolver = _resolver(tmp_path)
    code = "\n".join(
        [
            "x=character()",
            "x.name",
            "x.stats",
            "x.levels",
            "x.attacks",
            "x.skills",
            "x.saves",
            "x.resistances",
            "x.ac",
            "x.max_hp",
            "x.hp",
            "x.temp_hp",
            "x.spellbook",
            "x.creature_type",
            "x.actions",
            "x.coinpurse",
            "x.csettings",
            "x.race",
            "x.background",
            "x.owner",
            "x.upstream",
            "x.cvars",
            "x.consumables",
            "x.death_saves",
            "x.description",
            "x.image",
        ]
    )
    result = await executor.run(code, ctx, resolver)
    assert result.error is None


@pytest.mark.asyncio
async def test_combat_returns_none_when_missing(tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)

    result = await executor.run("combat()", ctx, resolver)
    assert result.error is None
    assert result.value is None


@pytest.mark.asyncio
async def test_combat_combatants_executes(tmp_path):
    from avrae_ls.config import AvraeLSConfig
    from avrae_ls.context import ContextBuilder

    cfg = AvraeLSConfig.default(tmp_path)
    ctx = ContextBuilder(cfg).build()
    resolver = _resolver(tmp_path)
    executor = MockExecutor()

    result = await executor.run("combat().combatants", ctx, resolver)
    assert result.error is None
    from collections.abc import Sequence

    assert isinstance(result.value, Sequence)
    assert len(result.value) == 2


class _EnsuringResolver(GVarResolver):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.calls: list[str] = []

    async def ensure(self, key: str) -> bool:
        self.calls.append(str(key))
        self._cache[str(key)] = f"fetched-{key}"
        return True


@pytest.mark.asyncio
async def test_get_gvar_prefetches_literal(tmp_path):
    from avrae_ls.config import AvraeLSConfig

    cfg = AvraeLSConfig.default(tmp_path)
    resolver = _EnsuringResolver(cfg)
    ctx = _ctx()
    executor = MockExecutor()

    result = await executor.run("get_gvar('abc123')", ctx, resolver)
    assert result.error is None
    assert resolver.calls == ["abc123"]
    assert result.value == "fetched-abc123"


@pytest.mark.asyncio
async def test_using_prefetches_literal(tmp_path):
    from avrae_ls.config import AvraeLSConfig

    cfg = AvraeLSConfig.default(tmp_path)

    class _ModuleResolver(GVarResolver):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.calls: list[str] = []

        async def ensure(self, key: str) -> bool:
            key = str(key)
            self.calls.append(key)
            self._cache[key] = "answer = 'ensured'"
            return True

    resolver = _ModuleResolver(cfg)
    ctx = _ctx()
    executor = MockExecutor()

    result = await executor.run("using(mod='abc123')\nmod.answer", ctx, resolver)
    assert result.error is None
    assert result.value == "ensured"
    assert resolver.calls == ["abc123"]


@pytest.mark.asyncio
async def test_using_missing_gvar_errors(tmp_path):
    from avrae_ls.config import AvraeLSConfig

    cfg = AvraeLSConfig.default(tmp_path)
    resolver = GVarResolver(cfg)
    ctx = _ctx()
    executor = MockExecutor()

    result = await executor.run("using(mod='missing')", ctx, resolver)
    assert result.error is not None
    assert "No gvar named 'missing'" in str(result.error)


@pytest.mark.asyncio
async def test_using_detects_circular_imports(tmp_path):
    from avrae_ls.config import AvraeLSConfig

    cfg = AvraeLSConfig.default(tmp_path)
    resolver = GVarResolver(cfg)
    resolver.seed(
        {
            "mod_a": "using(b='mod_b')",
            "mod_b": "using(a='mod_a')",
        }
    )
    ctx = _ctx()
    executor = MockExecutor()

    result = await executor.run("using(a='mod_a')", ctx, resolver)
    assert result.error is not None
    assert "Circular import" in str(result.error)


@pytest.mark.asyncio
async def test_using_rejects_builtin_shadow(tmp_path):
    from avrae_ls.config import AvraeLSConfig

    cfg = AvraeLSConfig.default(tmp_path)
    resolver = GVarResolver(cfg)
    resolver.seed({"mod": "answer = 1"})
    ctx = _ctx()
    executor = MockExecutor()

    result = await executor.run("using(len='mod')", ctx, resolver)
    assert result.error is not None
    assert "builtin" in str(result.error)


@pytest.mark.asyncio
async def test_uvar_helpers_available(tmp_path):
    executor = MockExecutor()
    ctx = ContextData(vars=VarSources(uvars={"foo": "orig"}))
    resolver = _resolver(tmp_path)
    code = "\n".join(
        [
            "set_uvar_nx('foo', 'new')",
            "set_uvar_nx('bar', 2)",
            "set_uvar('baz', 3)",
            "get_uvars()",
        ]
    )

    result = await executor.run(code, ctx, resolver)
    assert result.error is None
    uvars = result.value
    assert uvars["foo"] == "orig"
    assert uvars["bar"] == "2"
    assert uvars["baz"] == "3"


@pytest.mark.asyncio
async def test_set_cvar_binds_runtime_name(tmp_path):
    executor = MockExecutor()
    ctx = _ctx_with_character()
    resolver = _resolver(tmp_path)
    result = await executor.run("c = character(); c.set_cvar('pet', 'hawk'); pet", ctx, resolver)
    assert result.error is None
    assert result.value == "hawk"


@pytest.mark.asyncio
async def test_cvar_uvar_helpers_return_strings(tmp_path):
    executor = MockExecutor()
    ctx = _ctx_with_character({"name": "Aelar", "cvars": {"foo": 1}})
    resolver = _resolver(tmp_path)
    code = "\n".join(
        [
            "c = character()",
            "c.set_cvar('num', 5)",
            "c.get_cvar('num')",
            "set_uvar('test', 9)",
            "get_uvar('test')",
        ]
    )
    result = await executor.run(code, ctx, resolver)
    assert result.error is None
    assert result.value == "9"
    # ensure cvar stored as string
    assert ctx.character["cvars"]["num"] == "5"


@pytest.mark.asyncio
async def test_reference_helpers_available(tmp_path):
    executor = MockExecutor()
    ctx = ContextData(vars=VarSources(cvars={"foo": 1}))
    resolver = _resolver(tmp_path)
    code = "\n".join(
        [
            "exists('foo')",
            "get('foo')",
            "typeof(123)",
            "ceil(2.1)",
            "floor(2.9)",
            "sqrt(9)",
            "round(2.51, 1)",
            "randchoice([1,2,3])",
            "load_json(dump_json({'a': 1}))['a']",
            "parse_coins('1')",
            "using(mod='mod')",
            "mod.answer",
        ]
    )
    result = await executor.run(code, ctx, resolver)
    assert result.error is None
    assert result.value == "module-value"


@pytest.mark.asyncio
async def test_get_resolution_order(tmp_path):
    executor = MockExecutor()
    resolver = _resolver(tmp_path)

    ctx = ContextData(vars=VarSources(cvars={"foo": "cvar"}, uvars={"foo": "uvar"}))
    result = await executor.run("foo = 'local'\nget('foo')", ctx, resolver)
    assert result.error is None
    assert result.value == "local"

    result = await executor.run("get('foo')", ctx, resolver)
    assert result.error is None
    assert result.value == "cvar"

    ctx_uvar = ContextData(vars=VarSources(uvars={"bar": "uvar"}))
    result = await executor.run("get('bar')", ctx_uvar, resolver)
    assert result.error is None
    assert result.value == "uvar"

    ctx_svar = ContextData(vars=VarSources(svars={"baz": "svar"}))
    result = await executor.run("get('baz', 'missing')", ctx_svar, resolver)
    assert result.error is None
    assert result.value == "missing"


@pytest.mark.asyncio
async def test_verify_signature_cached(monkeypatch, tmp_path):
    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)

    calls: list[str] = []

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json.get("signature") if isinstance(json, dict) else None)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "message_id": 1,
                    "channel_id": 2,
                    "author_id": 3,
                    "timestamp": 0.0,
                    "scope": "UNKNOWN",
                    "user_data": len(calls),
                    "workshop_collection_id": None,
                },
            },
        )

    monkeypatch.setattr("avrae_ls.runtime.httpx.post", _fake_post)

    result = await executor.run("verify_signature('sig1')\nverify_signature('sig1')", ctx, resolver)
    assert result.error is None
    assert len(calls) == 1
    assert result.value["user_data"] == 1

    calls.clear()
    result = await executor.run("verify_signature('sig1')\nverify_signature('sig2')", ctx, resolver)
    assert result.error is None
    assert calls == ["sig1", "sig2"]
    assert result.value["user_data"] == 2


@pytest.mark.asyncio
async def test_verify_signature_retries(monkeypatch, tmp_path):
    calls: list[dict] = []

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"json": json, "headers": headers, "timeout": timeout})
        if len(calls) < 3:
            raise httpx.TimeoutException("network timeout")
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "user_data": len(calls),
                },
            },
        )

    monkeypatch.setattr("avrae_ls.runtime.httpx.post", _fake_post)

    cfg = AvraeServiceConfig(verify_timeout=9.5, verify_retries=2, token="secret-token")
    executor = MockExecutor(cfg)
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    result = await executor.run("verify_signature('sig-retry')", ctx, resolver)
    assert result.error is None
    assert len(calls) == 3
    assert all(call["timeout"] == 9.5 for call in calls)
    assert all(call["json"] == {"signature": "sig-retry"} for call in calls)
    assert all(call["headers"].get("Authorization") == "secret-token" for call in calls)
    assert result.value["user_data"] == 3


@pytest.mark.asyncio
async def test_verify_signature_http_error(monkeypatch, tmp_path):
    def _fake_post(url, json=None, headers=None, timeout=None):
        return httpx.Response(500, json={"error": "nope"})

    monkeypatch.setattr("avrae_ls.runtime.httpx.post", _fake_post)

    executor = MockExecutor()
    ctx = _ctx()
    resolver = _resolver(tmp_path)
    result = await executor.run("verify_signature('bad')", ctx, resolver)
    assert result.error is not None
    assert "HTTP 500" in str(result.error)
    assert "nope" in str(result.error)


def test_documented_builtins_present():
    executor = MockExecutor()
    ctx = _ctx()
    names = executor.available_names(ctx)
    documented = {
        "abs",
        "all",
        "any",
        "ceil",
        "enumerate",
        "float",
        "floor",
        "int",
        "len",
        "max",
        "min",
        "range",
        "round",
        "str",
        "sum",
        "time",
        "sqrt",
    }
    assert documented.issubset(names)
