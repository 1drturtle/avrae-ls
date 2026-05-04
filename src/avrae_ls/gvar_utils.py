from __future__ import annotations

import re


def sanitize_gvar_binding(label: str) -> str:
    cleaned = re.sub(r"\W+", "_", str(label))
    if cleaned and cleaned[0].isdigit():
        cleaned = f"gvar_{cleaned}"
    return cleaned or "gvar_import"
