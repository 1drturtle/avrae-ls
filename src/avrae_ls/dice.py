from __future__ import annotations

import d20


class RerollableStringifier(d20.SimpleStringifier):
    """Stringifier that emits rerollable expressions and skips dropped dice."""

    def _stringify(self, node):
        if not node.kept:
            return None
        return super()._stringify(node)

    def _str_expression(self, node):
        return self._stringify(node.roll)

    def _str_literal(self, node):
        return str(node.total)

    def _str_parenthetical(self, node):
        return f"({self._stringify(node.value)})"

    def _str_set(self, node):
        out = f"{', '.join([self._stringify(v) for v in node.values if v.kept])}"
        if len(node.values) == 1:
            return f"({out},)"
        return f"({out})"

    def _str_dice(self, node):
        return self._str_set(node)

    def _str_die(self, node):
        return str(node.total)
