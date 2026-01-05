from __future__ import annotations

import ast
from typing import Iterable, List


def collect_target_names(targets: Iterable[ast.AST]) -> List[str]:
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            names.extend(collect_target_names(target.elts))
    return names
