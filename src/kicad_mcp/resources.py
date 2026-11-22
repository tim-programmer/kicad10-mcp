"""MCP resources and guided engineering prompts for KiCad 10."""

from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from kicad_mcp.bridges.ipc import KiCadIPCError
from kicad_mcp.tools.ipc_inspect import pcb_get_board_info_impl
from kicad_mcp.tools.project import kicad_status_impl


def register_resources_and_prompts(server: MCPServer) -> None:
    """Register KiCad 10 MCP resources and reusable workflow prompts."""

    @server.resource(
        "kicad://status",
        name="kicad_status_resource",
        title="KiCad 10 Environment & Connection Status",
        description="Live JSON snapshot of kicad-cli version, IPC connectivity, open documents, read-only status, and library paths.",
        mime_type="application/json",
    )
    async def resource_kicad_status() -> str:
        data = await kicad_status_impl()
        return json.dumps(data, indent=2)

    @server.resource(
        "kicad://board/summary",
        name="kicad_board_summary",
        title="Open KiCad 10 PCB Board Summary",
        description="JSON summary of the currently open PCB in KiCad 10 via IPC (layers, title block, origins, item counts, net classes).",
        mime_type="application/json",
    )
    async def resource_board_summary() -> str:
        try:
            data = await pcb_get_board_info_impl()
        except KiCadIPCError as exc:
            data = {"connected": False, "error": str(exc)}
        return json.dumps(data, indent=2)

    @server.prompt(
        name="pcb_design_review",
        title="KiCad 10 PCB Design Review",
        description="Structured electrical, layout, DFM, and verification review workflow for a KiCad 10 project.",
    )
    def prompt_pcb_design_review(
        project_path: str,
        variant: str = "",
        fab_house: str = "standard 2/4-layer PCB fab",
    ) -> str:
        variant_note = (
            f"Evaluate specifically for KiCad 10 design variant `{variant}`."
            if variant
            else "Check `get_project_variants` first and review the default variant (and any configured variants)."
        )
        return f"""Perform a thorough KiCad 10 PCB design review for `{project_path}` targeting {fab_house}.
{variant_note}

Follow this review workflow using the available KiCad MCP tools:
1. **Project & Rules Audit**: Call `inspect_project` to check design rules, clearances, track/via constraints, net classes, and variants.
2. **Schematic Verification**: Run `run_erc` on the root schematic and `sch_inspect` to verify power symbols, labels, and any unconnected pins.
3. **PCB Verification & Stats**: Run `run_drc` (with `schematic_parity=True` and `refill_zones=True`) and `get_board_stats` to inspect dimensions, drill sizes, copper areas, and any DRC/unconnected/parity errors.
4. **Live Board Inspection (if KiCad IPC is connected)**: Use `pcb_get_board_info`, `pcb_get_stackup`, `pcb_list_footprints`, and `pcb_list_zones` to check stackup symmetry, copper pour clearances, DNP flags, and critical component placement.
5. **Visual Check**: Render top and bottom 3D views with `render_pcb_3d` (and optionally `export_pcb_visual` for key layers) if visual inspection is helpful.
6. **Report**: Summarize blockers (must-fix ERC/DRC/parity errors), DFM warnings (min trace/space/drill/annular ring vs. fab limits), assembly/BOM issues (missing footprints/values/DNP mismatches), and concrete recommendations.
"""

    @server.prompt(
        name="manufacturing_release_checklist",
        title="KiCad 10 Manufacturing Release Checklist",
        description="End-to-end pre-release verification and fabrication package generation checklist for KiCad 10.",
    )
    def prompt_manufacturing_release_checklist(
        project_path: str,
        output_dir: str = "./fab_release",
        variant: str = "",
    ) -> str:
        variant_clause = (
            f"Pass `variants='{variant}'` to every export tool."
            if variant
            else "Inspect `get_project_variants` and confirm whether a specific variant or the default variant should be released."
        )
        return f"""Execute a KiCad 10 manufacturing release for `{project_path}` into `{output_dir}`.
{variant_clause}

Checklist:
1. **Gate 1 — Clean ERC & DRC**:
   - Run `run_erc` on the schematic (confirm 0 errors).
   - Run `run_drc` on the PCB with `schematic_parity=True` and `refill_zones=True` (confirm 0 errors, 0 unconnected items, 0 schematic parity mismatches).
   - Stop and report any failures before generating fab artifacts.
2. **Gate 2 — Board Statistics & Stackup**:
   - Run `get_board_stats` (and `pcb_get_stackup` if IPC is active) to record board dimensions, layer count, finish, and min drill/trace metrics for fab ordering.
3. **Gate 3 — Fabrication Outputs**:
   - Export Gerbers (`export_gerbers` with `check_zones=True`) and Excellon drill files (`export_drill` with `generate_map=True`, `generate_report=True`), OR unified `export_odb_or_ipc2581`.
   - If the project defines a `.kicad_jobset`, check whether `run_jobset` should be used instead.
4. **Gate 4 — Assembly & Documentation Outputs**:
   - Export BOM (`export_bom` with `exclude_dnp=True` and appropriate grouping).
   - Export Pick-and-Place (`export_position_file` in `csv`, `mm`, `exclude_dnp=True`).
   - Export schematic PDF (`export_schematic_visual`), 3D STEP model (`export_3d_model`), and top/bottom 3D renders (`render_pcb_3d`).
5. **Summary**: List all generated files with sizes, highlight any DNP components excluded, and confirm readiness for fab upload.
"""
