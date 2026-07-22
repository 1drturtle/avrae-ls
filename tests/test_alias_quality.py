import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from lsprotocol import types
from pygls.workspace import Workspace

from avrae_ls.analysis.source_context import build_source_context
from avrae_ls.config import VarSources
from avrae_ls.lsp.server import AvraeLanguageServer, on_hover
from avrae_ls.runtime.alias_preview import render_alias_command


@dataclass(frozen=True)
class AliasQualityCase:
    """A complete alias and the deterministic mock result it should produce."""

    name: str
    source: str
    expected_value: Any
    args: tuple[str, ...] = ()
    vars: VarSources = field(default_factory=VarSources)


# add cases here when finding aliases that don't work
# regression ;)
CASES = (
    AliasQualityCase(
        name="check_ac",
        source="""!alias checkAc echo <drac2>
ch = character()
ac = ch.ac
return ac > 10
</drac2>""",
        expected_value=True,
    ),
    AliasQualityCase(
        name="character_name",
        source="""!alias characterName echo <drac2>
ch = character()
name = ch.name
return name
</drac2>""",
        expected_value="Aelar Wyn",
    ),
    AliasQualityCase(
        name="attack_name",
        source="""!alias attackName echo <drac2>
attack = character().attacks[0]
return attack.name
</drac2>""",
        expected_value="Longsword",
    ),
    AliasQualityCase(
        name="skill_value",
        source="""!alias athleticsBonus echo <drac2>
skill = character().skills.athletics
return skill.value
</drac2>""",
        expected_value=6,
    ),
    AliasQualityCase(
        name="combat_round",
        source="""!alias combatRound echo <drac2>
encounter = combat()
round_number = encounter.round_num
return round_number
</drac2>""",
        expected_value=2,
    ),
    AliasQualityCase(
        name="parsed_arguments",
        source="""!alias parseMode echo <drac2>
parsed = argparse(&ARGS&)
return parsed.last("mode")
</drac2>""",
        expected_value="stealth",
        args=("-mode", "stealth"),
    ),
    AliasQualityCase(
        name="cvar_lookup",
        source="""!alias favoriteEnemy echo <drac2>
return get("favorite_enemy")
</drac2>""",
        expected_value="goblinoids",
    ),
    AliasQualityCase(
        name="uvar_lookup",
        source="""!alias partyName echo <drac2>
return get_uvar("party")
</drac2>""",
        expected_value="Emerald Enclave",
        vars=VarSources(uvars={"party": "Emerald Enclave"}),
    ),
    AliasQualityCase(
        name="gvar_lookup",
        source="""!alias greeting echo <drac2>
return get_gvar("greeting")
</drac2>""",
        expected_value="hello from a local gvar",
        vars=VarSources(gvars={"greeting": "hello from a local gvar"}),
    ),
    AliasQualityCase(
        name="deterministic_dice",
        source="""!alias certainRoll echo <drac2>
return roll("1d1")
</drac2>""",
        expected_value=1,
    ),
    AliasQualityCase(
        name="embed_title",
        source="""!alias characterCard embed -title "<drac2>
return character().name
</drac2>""",
        expected_value="Aelar Wyn",
    ),
)


def _server_with_source(case: AliasQualityCase, tmp_path: Path) -> tuple[AvraeLanguageServer, str]:
    server = AvraeLanguageServer()
    server.load_workspace(tmp_path)
    profile = server.state.config.profiles["default"]
    profile.vars = profile.vars.merge(case.vars)

    uri = f"file:///alias-quality-{case.name}.alias"
    server.protocol._workspace = Workspace(None, sync_kind=types.TextDocumentSyncKind.Incremental)
    server.protocol.workspace.put_text_document(
        types.TextDocumentItem(uri=uri, language_id="avrae", version=1, text=case.source)
    )
    return server, uri


def _hover_locations(source: str) -> list[tuple[str, int, int]]:
    """Return each identifier and accessed member in a full alias source."""
    locations: set[tuple[str, int, int]] = set()
    source_context = build_source_context(source, treat_as_module=False)

    for block in source_context.blocks:
        tree = ast.parse(block.code)

        class SymbolVisitor(ast.NodeVisitor):
            def visit_Name(self, node: ast.Name) -> None:
                locations.add(
                    (
                        node.id,
                        block.line_offset + node.lineno - 1,
                        node.col_offset + (block.char_offset if node.lineno == 1 else 0),
                    )
                )
                self.generic_visit(node)

            def visit_Attribute(self, node: ast.Attribute) -> None:
                # Attribute offsets begin at the receiver, so use the end offset
                # to ask hover for the member itself (for example, ``ac`` in
                # ``character().ac``).
                end_line = node.end_lineno or node.lineno
                end_col = node.end_col_offset or node.col_offset + len(node.attr)
                locations.add(
                    (
                        node.attr,
                        block.line_offset + end_line - 1,
                        end_col - len(node.attr),
                    )
                )
                self.generic_visit(node)

        SymbolVisitor().visit(tree)

    return sorted(locations, key=lambda location: (location[1], location[2], location[0]))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
async def test_valid_aliases_execute_cleanly_and_expose_hints(case: AliasQualityCase, tmp_path: Path):
    server, uri = _server_with_source(case, tmp_path)
    context = server.state.context_builder.build()
    resolver = server.state.context_builder.gvar_resolver

    diagnostics = await server.state.diagnostics.analyze(case.source, context, resolver)
    assert not diagnostics, "\n".join(diagnostic.message for diagnostic in diagnostics)

    rendered = await render_alias_command(
        case.source,
        server.state.executor,
        context,
        resolver,
        args=list(case.args) or None,
    )
    assert rendered.error is None, str(rendered.error)
    assert rendered.last_value == case.expected_value

    missing_hints: list[str] = []
    for symbol, line, character in _hover_locations(case.source):
        hover = on_hover(
            server,
            types.HoverParams(
                text_document=types.TextDocumentIdentifier(uri=uri),
                position=types.Position(line=line, character=character),
            ),
        )
        contents = getattr(hover, "contents", None)
        hint = getattr(contents, "value", "")
        if not hint or not hint.strip():
            missing_hints.append(f"{symbol!r} at {line + 1}:{character + 1}")

    assert not missing_hints, "Missing LSP hover hints:\n" + "\n".join(missing_hints)
