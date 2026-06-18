from __future__ import annotations

import re
from collections import UserList
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import yaml

MISSING_VALUE = "<missing>"


@dataclass(frozen=True)
class TestMetadata:
    name: str | None = None
    profile: str | None = None
    var_overrides: dict[str, Any] | None = None
    character_overrides: dict[str, Any] | None = None


def parse_expected_value(raw: str) -> Any:
    return yaml.safe_load(raw) if raw.strip() else ""


def parse_metadata_mapping(raw: str, path_label: str) -> dict[str, Any] | None:
    meta = yaml.safe_load(raw) if raw.strip() else None
    if meta is not None and not isinstance(meta, dict):
        raise ValueError(f"{path_label} metadata after second '---' must be a mapping")
    return meta


def parse_test_metadata(raw: str, path_label: str) -> TestMetadata:
    meta = parse_metadata_mapping(raw, path_label)
    if not isinstance(meta, dict):
        return TestMetadata()
    name = meta.get("name")
    profile = meta.get("profile")
    var_overrides = meta.get("vars")
    character_overrides = meta.get("character")
    return TestMetadata(
        name=str(name) if name is not None else None,
        profile=str(profile) if profile is not None else None,
        var_overrides=var_overrides if isinstance(var_overrides, dict) else None,
        character_overrides=character_overrides if isinstance(character_overrides, dict) else None,
    )


def deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base or {})
    for key, val in (override or {}).items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = deep_merge_dicts(merged[key], val)
        else:
            merged[key] = val
    return merged


def scalar_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str):
        if expected == "":
            return True
        pattern = compile_expected_pattern(expected)
        if pattern:
            return pattern.search("" if actual is None else str(actual)) is not None
        lhs = expected.strip()
        rhs = "" if actual is None else str(actual).strip()
        return lhs == rhs
    return expected == actual


def dict_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, expected_val in expected.items():
        if key not in actual:
            return False
        actual_val = actual[key]
        if not value_matches(expected_val, actual_val):
            return False
    return True


def value_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and dict_matches(actual, expected)
    if isinstance(expected, list):
        if not isinstance(actual, (list, UserList)) and not (
            isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray))
        ):
            return False
        actual_items = list(actual)
        if len(actual_items) < len(expected):
            return False
        return all(value_matches(e, actual_items[idx]) for idx, e in enumerate(expected))
    return scalar_matches(expected, actual)


def diff_mismatched_parts(expected: Any, actual: Any) -> tuple[Any, Any] | None:
    if value_matches(expected, actual):
        return None

    if isinstance(expected, dict) and isinstance(actual, dict):
        expected_diff: dict[str, Any] = {}
        actual_diff: dict[str, Any] = {}
        for key, expected_val in expected.items():
            if key not in actual:
                expected_diff[key] = expected_val
                actual_diff[key] = MISSING_VALUE
                continue
            sub_diff = diff_mismatched_parts(expected_val, actual[key])
            if sub_diff:
                expected_diff[key], actual_diff[key] = sub_diff
        if expected_diff:
            return expected_diff, actual_diff
        return expected, actual

    if isinstance(expected, list) and (
        isinstance(actual, (list, UserList))
        or (isinstance(actual, Sequence) and not isinstance(actual, (str, bytes, bytearray)))
    ):
        actual_items = list(actual)
        expected_list_diff: list[Any] = []
        actual_list_diff: list[Any] = []
        for idx, expected_val in enumerate(expected):
            if idx >= len(actual_items):
                expected_list_diff.append(expected_val)
                actual_list_diff.append(MISSING_VALUE)
                continue
            sub_diff = diff_mismatched_parts(expected_val, actual_items[idx])
            if sub_diff:
                expected_list_diff.append(sub_diff[0])
                actual_list_diff.append(sub_diff[1])
        if expected_list_diff:
            return expected_list_diff, actual_list_diff
        return expected, actual

    return expected, actual


def merge_new_gvars_into_suite_cache(
    suite_cache: dict[str, Any], active_cache: dict[str, Any], *, exclude_keys: set[str] | None = None
) -> None:
    excluded = {str(key) for key in (exclude_keys or set())}
    for key, value in active_cache.items():
        key_str = str(key)
        if key_str in excluded or key_str in suite_cache:
            continue
        suite_cache[key_str] = value


def compile_expected_pattern(text: str) -> re.Pattern[str] | None:
    """
    Interpret strings with /.../ segments (or re:prefix) as regex.

    - `/foo/` or `re:foo` => regex `foo`
    - Mixed literals + regex, e.g. `Hello /world.*/` => literal `Hello ` + regex `world.*`
    """
    if not text:
        return None
    if text.startswith("re:"):
        try:
            return re.compile(text[3:])
        except re.error:
            return None

    parts = re.split(r"(?<!\\)/(.*?)(?<!\\)/", text)
    if len(parts) == 1:
        return None

    if len(parts) == 3 and parts[0] == "" and parts[2] == "":
        pattern = parts[1].replace("\\/", "/")
        try:
            return re.compile(pattern)
        except re.error:
            return None

    regex_parts: list[str] = []
    for idx, part in enumerate(parts):
        unescaped = part.replace("\\/", "/")
        if idx % 2 == 0:
            regex_parts.append(re.escape(unescaped))
        else:
            regex_parts.append(unescaped)
    pattern = "^" + "".join(regex_parts) + "$"
    try:
        return re.compile(pattern)
    except re.error:
        return None
