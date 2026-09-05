"""Damage rolls using d20's AST, preserving typed damage and dice modifiers."""

from __future__ import annotations

import copy

import d20


def roll_damage(dice_str: str, resistances, *, crit=False, d=None, c=None, critdice=0) -> d20.RollResult:
    expression = str(dice_str)
    if d is not None:
        expression = f"({expression})+({d})"
    tree = copy.deepcopy(d20.parse(expression))
    extra = int(critdice) if crit else 0

    def critical(node):
        nonlocal extra
        if crit and isinstance(node, d20.ast.Dice):
            node.num = node.num * 2 + extra
            extra = 0
        return node

    tree = d20.utils.tree_map(critical, tree)
    if crit and c is not None:
        tree = d20.parse(f"({tree})+({c})")
    result = d20.roll(tree)
    d20.utils.simplify_expr_annotations(result.expr.roll, ambig_inherit="left")

    def apply(node):
        annotation = node.annotation
        if annotation:
            dtype = annotation.strip("[]").lower()
            if resistances.is_neutral(dtype):
                return node
            if resistances.is_immune(dtype):
                return d20.BinOp(node, "*", d20.Literal(0))
            if resistances.is_resistant(dtype):
                node = d20.BinOp(node, "//", d20.Literal(2))
            if resistances.is_vulnerable(dtype):
                node = d20.BinOp(node, "*", d20.Literal(2))
            return node
        for i, child in enumerate(node.children):
            node.set_child(i, apply(child))
        return node

    result.expr.roll = apply(result.expr.roll)
    return result
