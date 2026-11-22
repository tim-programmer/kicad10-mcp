"""Tests for MCP server registration, JSON schemas, resources, prompts, and project tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_mcp.config import KiCadConfig, set_config
from kicad_mcp.server import create_server


@pytest.mark.asyncio
async def test_all_tools_resources_and_prompts_register_cleanly() -> None:
    server = create_server()
    tools = await server.list_tools()
    resources = await server.list_resources()
    prompts = await server.list_prompts()

    tool_names = {t.name for t in tools}
    assert len(tools) == 55
    expected_subset = {
        "kicad_status",
        "list_projects",
        "inspect_project",
        "get_project_variants",
        "pcb_get_board_info",
        "pcb_get_stackup",
        "pcb_list_graphics",
        "pcb_move_footprint",
        "pcb_flip_footprint",
        "run_drc",
        "run_erc",
        "get_board_stats",
        "render_pcb_3d",
        "export_gerbers",
        "export_bom",
        "run_jobset",
        "sch_inspect",
        "sch_place_symbol",
        "sch_delete_items",
        "list_libraries",
        "inspect_symbol",
        "inspect_footprint",
    }
    assert expected_subset.issubset(tool_names)

    for tool in tools:
        assert tool.description and len(tool.description.strip()) > 10
        assert isinstance(tool.input_schema, dict)
        assert tool.input_schema.get("type") == "object"
        assert tool.annotations is not None

    resource_uris = {str(r.uri) for r in resources}
    assert resource_uris == {"kicad://status", "kicad://board/summary"}

    prompt_names = {p.name for p in prompts}
    assert prompt_names == {"pcb_design_review", "manufacturing_release_checklist"}


@pytest.mark.asyncio
async def test_project_tools_and_resources(tmp_path: Path) -> None:
    set_config(KiCadConfig(read_only=False))
    proj_dir = tmp_path / "sensor_board"
    proj_dir.mkdir()
    pro_file = proj_dir / "sensor_board.kicad_pro"
    (proj_dir / "sensor_board.kicad_sch").write_text("(kicad_sch)\n", encoding="utf-8")
    (proj_dir / "sensor_board.kicad_pcb").write_text("(kicad_pcb)\n", encoding="utf-8")
    pro_file.write_text(
        json.dumps(
            {
                "meta": {"filename": "sensor_board.kicad_pro", "version": 3},
                "text_variables": {"BOARD_REV": "2.1"},
                "board": {"design_settings": {"rules": {"min_track_width": 0.127}}},
                "net_settings": {"classes": [{"name": "Default"}]},
                "active_variant": "high_temp",
                "variants": [
                    {"name": "default", "description": "Standard assembly"},
                    {"name": "high_temp", "description": "Industrial -40C..125C"},
                ],
            }
        ),
        encoding="utf-8",
    )

    server = create_server()

    listed_res = await server.call_tool("list_projects", {"directory": str(tmp_path)})
    listed = listed_res.structured_content
    assert listed is not None
    assert listed["count"] == 1
    assert listed["projects"][0]["name"] == "sensor_board"

    inspected = await server._tool_manager.get_tool("inspect_project").fn(
        project_path=str(pro_file)
    )
    assert inspected["text_variables"]["BOARD_REV"] == "2.1"
    assert inspected["active_variant"] == "high_temp"
    assert len(inspected["variants"]) == 2
    assert len(inspected["files"]["schematics"]) == 1

    variants = await server._tool_manager.get_tool("get_project_variants").fn(
        project_path=str(proj_dir)
    )
    assert variants["count"] == 2
    assert variants["active_variant"] == "high_temp"

    # Resource reading
    status_contents = await server.read_resource("kicad://status")
    assert status_contents
    parsed_status = json.loads(status_contents[0].content)
    assert "cli" in parsed_status
    assert "ipc" in parsed_status

    # Prompt rendering
    prompt_res = await server.get_prompt(
        "pcb_design_review", {"project_path": str(pro_file), "variant": "high_temp"}
    )
    assert prompt_res.messages
    assert "high_temp" in prompt_res.messages[0].content.text
