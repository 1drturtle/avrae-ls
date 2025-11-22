from pathlib import Path

from avrae_ls.completions import completion_items_for_position, hover_for_position
from avrae_ls.config import AvraeLSConfig
from avrae_ls.context import ContextData, GVarResolver


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
