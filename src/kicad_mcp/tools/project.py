"""Project discovery, environment status, and `.kicad_pro` inspection tools."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from kipy.client import ApiError
from mcp.server.mcpserver import MCPServer

from kicad_mcp.bridges.cli import KiCadCLI, KiCadCLIError
from kicad_mcp.bridges.ipc import KiCadIPCError, get_ipc_bridge
from kicad_mcp.config import get_config
from kicad_mcp.tools.common import READ_ONLY_ANNOTATIONS, resolve_existing_file

logger = logging.getLogger(__name__)
_SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", "backups"}


async def _probe_ipc() -> dict[str, Any]:
    bridge = get_ipc_bridge()

    def _sync_probe() -> dict[str, Any]:
        kicad = bridge.connect()
        version_str = ""
        try:
            ver = kicad.get_version()
            version_str = str(ver)
        except (ApiError, RuntimeError, AttributeError) as exc:
            logger.debug("kicad.get_version() failed: %s", exc)
        docs = bridge.list_open_documents()
        return {
            "connected": True,
            "kicad_version": version_str,
            "open_documents": docs,
        }

    try:
        return await asyncio.to_thread(_sync_probe)
    except (KiCadIPCError, ApiError, RuntimeError, OSError) as exc:
        return {
            "connected": False,
            "error": str(exc),
            "open_documents": [],
        }


async def kicad_status_impl() -> dict[str, Any]:
    """Gather KiCad 10 CLI, IPC, read-only, and library directory status."""
    cfg = get_config()
    cli = KiCadCLI(cfg)

    cli_info: dict[str, Any] = {"binary": cfg.kicad_cli}
    try:
        cli_info["available"] = True
        cli_info["version"] = await cli.version("plain")
    except KiCadCLIError as exc:
        cli_info["available"] = False
        cli_info["error"] = str(exc)

    ipc_info = await _probe_ipc()

    return {
        "read_only": cfg.read_only,
        "config_dir": str(cfg.config_dir),
        "config_dir_exists": cfg.config_dir.is_dir(),
        "cli": cli_info,
        "ipc": ipc_info,
        "symbol_lib_dirs": [str(p) for p in cfg.symbol_lib_dirs],
        "footprint_lib_dirs": [str(p) for p in cfg.footprint_lib_dirs],
    }


def _collect_project_files(proj_dir: Path) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {
        "schematics": [],
        "pcbs": [],
        "jobsets": [],
        "symbol_libs": [],
        "footprint_libs": [],
        "other": [],
    }
    for entry in sorted(proj_dir.iterdir(), key=lambda p: p.name.lower()):
        name = entry.name
        if name.startswith(".") or name.endswith(".bak"):
            continue
        if entry.is_dir():
            if name.endswith(".pretty"):
                buckets["footprint_libs"].append(str(entry))
            continue
        suffix = entry.suffix.lower()
        if suffix == ".kicad_sch":
            buckets["schematics"].append(str(entry))
        elif suffix == ".kicad_pcb":
            buckets["pcbs"].append(str(entry))
        elif suffix == ".kicad_jobset":
            buckets["jobsets"].append(str(entry))
        elif suffix == ".kicad_sym":
            buckets["symbol_libs"].append(str(entry))
        elif name in {"sym-lib-table", "fp-lib-table"} or suffix in {".kicad_dru", ".kicad_wks"}:
            buckets["other"].append(str(entry))
    return buckets


def _extract_variants(data: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Extract KiCad 10 variant definitions from parsed `.kicad_pro` JSON."""
    variants_out: list[dict[str, Any]] = []
    active_variant: str | None = None

    raw_active = data.get("active_variant")
    if isinstance(raw_active, str) and raw_active:
        active_variant = raw_active

    candidates: list[Any] = []
    if "variants" in data:
        candidates.append(data["variants"])
    for section in ("schematic", "board", "project"):
        sec = data.get(section)
        if isinstance(sec, dict) and "variants" in sec:
            candidates.append(sec["variants"])
            if active_variant is None and isinstance(sec.get("active_variant"), str):
                active_variant = sec["active_variant"] or None

    seen_names: set[str] = set()
    for raw in candidates:
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item not in seen_names:
                    seen_names.add(item)
                    variants_out.append({"name": item, "description": ""})
                elif isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    if name and name not in seen_names:
                        seen_names.add(name)
                        variants_out.append({
                            "name": name,
                            "description": str(item.get("description", "")),
                            **{
                                k: v
                                for k, v in item.items()
                                if k not in {"name", "description"}
                            },
                        })
        elif isinstance(raw, dict):
            for key, val in raw.items():
                name = str(key).strip()
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                if isinstance(val, dict):
                    variants_out.append({
                        "name": name,
                        "description": str(val.get("description", "")),
                        **{k: v for k, v in val.items() if k != "description"},
                    })
                else:
                    variants_out.append({"name": name, "description": str(val or "")})

    return variants_out, active_variant


def register_project_tools(server: MCPServer) -> None:
    """Register project & environment tools on the MCP server."""

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def kicad_status() -> dict[str, Any]:
        """Check KiCad 10 environment status: `kicad-cli` version, live IPC connectivity,
        open KiCad documents, read-only mode, and discovered library paths.
        """
        return await kicad_status_impl()

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def list_projects(directory: str, max_depth: int = 4) -> dict[str, Any]:
        """Discover KiCad projects (`.kicad_pro`) within a directory tree.

        Args:
            directory: Root directory to search.
            max_depth: Maximum folder depth to descend (default 4).
        """
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Directory not found: {root}")
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")

        projects: list[dict[str, Any]] = []
        root_depth = len(root.parts)

        for cur_dir, dirnames, filenames in os.walk(root):
            cur_path = Path(cur_dir)
            depth = len(cur_path.parts) - root_depth
            if depth >= max_depth:
                dirnames[:] = []
            else:
                dirnames[:] = sorted(
                    d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
                )

            for fname in sorted(filenames):
                if not fname.endswith(".kicad_pro"):
                    continue
                pro_path = cur_path / fname
                stem = pro_path.stem
                sch_path = cur_path / f"{stem}.kicad_sch"
                pcb_path = cur_path / f"{stem}.kicad_pcb"
                projects.append({
                    "name": stem,
                    "project_file": str(pro_path),
                    "directory": str(cur_path),
                    "schematic_file": str(sch_path) if sch_path.is_file() else None,
                    "pcb_file": str(pcb_path) if pcb_path.is_file() else None,
                })

        return {
            "search_root": str(root),
            "count": len(projects),
            "projects": projects,
        }

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def inspect_project(project_path: str) -> dict[str, Any]:
        """Inspect a `.kicad_pro` file: design rules, net classes, text variables,
        variants, sheets, and project files in the same directory.

        Args:
            project_path: Path to a `.kicad_pro` file (or project directory containing one).
        """
        raw = Path(project_path).expanduser().resolve()
        if raw.is_dir():
            matches = sorted(raw.glob("*.kicad_pro"))
            if not matches:
                raise FileNotFoundError(f"No .kicad_pro file found in directory: {raw}")
            pro_file = matches[0]
        else:
            pro_file = resolve_existing_file(project_path, "Project file")

        data = json.loads(pro_file.read_text(encoding="utf-8"))
        board_cfg = data.get("board", {}) if isinstance(data.get("board"), dict) else {}
        design_settings = (
            board_cfg.get("design_settings", {})
            if isinstance(board_cfg.get("design_settings"), dict)
            else {}
        )
        net_settings = (
            data.get("net_settings", {}) if isinstance(data.get("net_settings"), dict) else {}
        )
        variants, active_variant = _extract_variants(data)

        return {
            "name": pro_file.stem,
            "project_file": str(pro_file),
            "directory": str(pro_file.parent),
            "meta": data.get("meta", {}),
            "text_variables": data.get("text_variables", {}),
            "sheets": data.get("sheets", []),
            "design_rules": design_settings.get("rules", {}),
            "track_widths": design_settings.get("track_widths", []),
            "via_dimensions": design_settings.get("via_dimensions", []),
            "net_classes": net_settings.get("classes", []),
            "netclass_assignments": net_settings.get("netclass_assignments", {}),
            "active_variant": active_variant,
            "variants": variants,
            "files": _collect_project_files(pro_file.parent),
        }

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def get_project_variants(project_path: str) -> dict[str, Any]:
        """List KiCad 10 design variants configured in a `.kicad_pro` project file.

        Args:
            project_path: Path to a `.kicad_pro` file (or project directory containing one).
        """
        raw = Path(project_path).expanduser().resolve()
        if raw.is_dir():
            matches = sorted(raw.glob("*.kicad_pro"))
            if not matches:
                raise FileNotFoundError(f"No .kicad_pro file found in directory: {raw}")
            pro_file = matches[0]
        else:
            pro_file = resolve_existing_file(project_path, "Project file")

        data = json.loads(pro_file.read_text(encoding="utf-8"))
        variants, active_variant = _extract_variants(data)
        return {
            "project_file": str(pro_file),
            "active_variant": active_variant,
            "count": len(variants),
            "variants": variants,
        }
