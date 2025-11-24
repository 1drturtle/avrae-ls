from lsprotocol import types

from pathlib import Path

from pygls.workspace import Workspace

from avrae_ls.signature_help import FunctionSig, signature_help_for_code
from avrae_ls.server import AvraeLanguageServer, on_signature_help


def _call_sig(code: str, cursor: tuple[int, int]):
    sigs = {
        "outer": FunctionSig(name="outer", params=["a", "b", "c"]),
        "inner": FunctionSig(name="inner", params=["x", "foo", "y"]),
    }
    help_ = signature_help_for_code(code, cursor[0], cursor[1], sigs)
    assert help_ is not None
    return help_


def test_signature_help_prefers_innermost_call():
    code = "outer(inner(1, foo=2), 3)"
    cursor = (0, code.index("foo") + 1)
    help_ = _call_sig(code, cursor)
    assert help_.active_signature == 0
    assert help_.active_parameter == 1  # foo kw -> inner second param
    assert help_.signatures[0].label.startswith("inner(")


def test_signature_help_tracks_position_after_comma():
    code = "outer(inner(1, foo=2), 3)"
    cursor = (0, code.index(", 3") + 2)  # inside second arg of outer
    help_ = _call_sig(code, cursor)
    assert help_.signatures[0].label.startswith("outer(")
    assert help_.active_parameter == 1


def test_signature_help_defaults_to_next_slot_when_not_inside_arg():
    code = "outer(1, )"
    cursor = (0, code.index(")"))  # after comma, before closing
    help_ = signature_help_for_code(code, cursor[0], cursor[1], {"outer": FunctionSig("outer", ["a", "b"])})
    assert help_ is not None
    assert help_.active_parameter == 1


def test_signature_help_clamps_when_more_args_than_params():
    code = "outer(1, 2, 3, 4)"
    cursor = (0, code.index("4"))
    help_ = signature_help_for_code(code, cursor[0], cursor[1], {"outer": FunctionSig("outer", ["a", "b", "c"])})
    assert help_ is not None
    # Extra args should clamp to last known param
    assert help_.active_parameter == 2


def test_signature_help_handles_empty_params():
    code = "outer()"
    cursor = (0, code.index(")"))
    help_ = signature_help_for_code(code, cursor[0], cursor[1], {"outer": FunctionSig("outer", [])})
    assert help_ is not None
    assert help_.active_parameter == 0


def test_signature_help_keyword_not_in_params_falls_back_to_position():
    code = "outer(foo=1, bar=2)"
    cursor = (0, code.index("bar") + 1)
    help_ = signature_help_for_code(code, cursor[0], cursor[1], {"outer": FunctionSig("outer", ["a", "b"])})
    assert help_ is not None
    # Unknown kw should map by position order (second argument)
    assert help_.active_parameter == 1


def _setup_server_with_doc(source: str, uri: str = "file:///sig.alias") -> AvraeLanguageServer:
    server = AvraeLanguageServer()
    server.load_workspace(Path("."))
    server.protocol._workspace = Workspace(None, sync_kind=types.TextDocumentSyncKind.Incremental)
    server.protocol.workspace.put_text_document(
        types.TextDocumentItem(uri=uri, language_id="avrae", version=1, text=source)
    )
    return server


def test_signature_help_with_draconic_block_offsets():
    source = "\n".join(
        [
            "plain",
            "<drac2>",
            "outer(1, inner(2, 3))",
            "</drac2>",
        ]
    )
    server = _setup_server_with_doc(source)
    server._signatures.update(
        {
            "outer": FunctionSig("outer", ["a", "b"]),
            "inner": FunctionSig("inner", ["x", "y"]),
        }
    )
    line = 2
    col = source.splitlines()[line].index("3")
    params = types.SignatureHelpParams(
        text_document=types.TextDocumentIdentifier(uri="file:///sig.alias"),
        position=types.Position(line=line, character=col),
        context=types.SignatureHelpContext(trigger_kind=types.SignatureHelpTriggerKind.Invoked, is_retrigger=False),
    )
    help_ = on_signature_help(server, params)
    assert help_ is not None
    assert help_.signatures[0].label.startswith("inner(")
    assert help_.active_parameter == 1


def test_signature_help_multiline_args_tracks_line_breaks():
    source = "\n".join(
        [
            "<drac2>",
            "outer(",
            "  1,",
            "  2,",
            "  3",
            ")",
            "</drac2>",
        ]
    )
    server = _setup_server_with_doc(source, uri="file:///sig2.alias")
    server._signatures["outer"] = FunctionSig("outer", ["a", "b", "c"])
    line = 3  # line with second arg
    col = source.splitlines()[line].index("2")
    params = types.SignatureHelpParams(
        text_document=types.TextDocumentIdentifier(uri="file:///sig2.alias"),
        position=types.Position(line=line, character=col),
        context=types.SignatureHelpContext(trigger_kind=types.SignatureHelpTriggerKind.Invoked, is_retrigger=False),
    )
    help_ = on_signature_help(server, params)
    assert help_ is not None
    assert help_.signatures[0].label.startswith("outer(")
    assert help_.active_parameter == 1
