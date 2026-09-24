"""Rewrite entity IDs inside rows, JSON columns, and checkpoint snapshots when copying a timeline."""

from typing import Any


def remap_ids(value: Any, mapping: dict[str, str]) -> Any:
    """Replace every string that is exactly an old ID. UUID strings do not collide with prose."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [remap_ids(entry, mapping) for entry in value]
    if isinstance(value, dict):
        return {key: remap_ids(entry, mapping) for key, entry in value.items()}
    return value
