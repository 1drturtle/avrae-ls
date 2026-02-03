from pathlib import Path

from lsprotocol import types
from pygls.workspace import Workspace

from avrae_ls.lsp.server import AvraeLanguageServer, on_completion, on_hover


def _server_with_doc(source: str, uri: str = "file:///integration.alias") -> AvraeLanguageServer:
    server = AvraeLanguageServer()
    server.load_workspace(Path("."))
    server.protocol._workspace = Workspace(None, sync_kind=types.TextDocumentSyncKind.Incremental)
    server.protocol.workspace.put_text_document(types.TextDocumentItem(uri=uri, language_id="avrae", version=1, text=source))
    return server


def test_completion_round_trip_integration():
    code = "character()."
    uri = "file:///completion.alias"
    server = _server_with_doc(code, uri)
    params = types.CompletionParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=0, character=len(code)),
        context=None,
    )
    items = on_completion(server, params)
    labels = {item.label for item in items}
    assert "name" in labels
    assert "attacks" in labels


def test_hover_round_trip_integration():
    code = "val = character().spellbook\nval"
    uri = "file:///hover.alias"
    server = _server_with_doc(code, uri)
    params = types.HoverParams(
        text_document=types.TextDocumentIdentifier(uri=uri),
        position=types.Position(line=1, character=len("val")),
    )
    hover = on_hover(server, params)
    assert hover is not None
    assert "AliasSpellbook" in hover.contents.value
