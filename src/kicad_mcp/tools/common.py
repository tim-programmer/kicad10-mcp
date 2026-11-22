"""Shared helpers and MCP ToolAnnotations presets for kicad10-mcp tools."""

from __future__ import annotations

from pathlib import Path

from mcp.types import ToolAnnotations

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

EXPORT_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

WRITE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=False,
)

DESTRUCTIVE_ANNOTATIONS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=False,
)


def resolve_existing_file(path: str, label: str = "File") -> Path:
    """Resolve and verify that a file exists on disk."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"{label} not found: {p}")
    return p


def validate_multi_variant_output(
    variants: str | list[str] | None,
    output_path: str | Path | None,
) -> None:
    """Validate KiCad 10's `${VARIANT}` requirement when multiple variants are requested."""
    if isinstance(variants, list):
        non_empty = [v for v in variants if v]
        if len(non_empty) > 1 and output_path is not None and "${VARIANT}" not in str(output_path):
            raise ValueError(
                "When specifying multiple variants, output_path must include '${VARIANT}' "
                "(e.g. 'out/board_${VARIANT}.pdf') so KiCad 10 writes a separate file per variant."
            )
