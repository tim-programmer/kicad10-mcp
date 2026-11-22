"""Tests for direct S-expression `.kicad_sch` inspection and editing tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_mcp.config import KiCadConfig, set_config
from kicad_mcp.server import create_server

MINIMAL_SCH = """(kicad_sch
	(version 20250114)
	(generator "eeschema")
	(generator_version "10.0")
	(uuid "11111111-1111-4111-8111-111111111111")
	(paper "A4")
	(title_block
		(title "Test Schematic")
		(rev "1.0")
	)
	(lib_symbols
	)
	(sheet_instances
		(path "/"
			(page "1")
		)
	)
)
"""

TEST_SYM_LIB = """(kicad_symbol_lib
	(version 20251024)
	(generator "kicad_symbol_editor")
	(symbol "R"
		(in_bom yes)
		(on_board yes)
		(property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "Value" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "Description" "Resistor" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(symbol "R_1_1"
			(pin passive line (at 0 3.81 270) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
			(pin passive line (at 0 -3.81 90) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
		)
	)
)
"""


async def _call(name: str, args: dict) -> dict:
    server = create_server()
    tool = server._tool_manager.get_tool(name)
    assert tool is not None
    return await tool.fn(**args)


@pytest.mark.asyncio
async def test_schematic_full_edit_workflow(tmp_path: Path) -> None:
    set_config(KiCadConfig(read_only=False))
    sch_file = tmp_path / "demo.kicad_sch"
    sym_file = tmp_path / "Device.kicad_sym"
    sch_file.write_text(MINIMAL_SCH, encoding="utf-8")
    sym_file.write_text(TEST_SYM_LIB, encoding="utf-8")

    # 1. Inspect initial schematic
    initial = await _call("sch_inspect", {"schematic_path": str(sch_file)})
    assert initial["paper"] == "A4"
    assert initial["title_block"]["title"] == "Test Schematic"
    assert initial["counts"]["symbols"] == 0

    # 2. Place a symbol from library
    placed = await _call(
        "sch_place_symbol",
        {
            "schematic_path": str(sch_file),
            "lib_id": "Device:R",
            "reference": "R1",
            "x_mm": 100.0,
            "y_mm": 80.0,
            "value": "10k",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "library_path": str(sym_file),
        },
    )
    assert placed["embedded_lib_symbol"] is True
    r1_uuid = placed["symbol"]["uuid"]
    assert placed["symbol"]["reference"] == "R1"
    assert placed["symbol"]["value"] == "10k"
    assert len(placed["symbol"]["pins"]) == 2
    assert sch_file.with_suffix(".kicad_sch.bak").is_file()

    # 3. Inspect placed symbol + cached lib_symbol pins
    got = await _call(
        "sch_get_symbol", {"schematic_path": str(sch_file), "reference_or_uuid": "R1"}
    )
    assert got["uuid"] == r1_uuid
    assert len(got["lib_symbol_pins"]) == 2
    assert got["instances"][0]["reference"] == "R1"

    # 4. Update properties, custom fields, DNP, and rename reference (syncing instances)
    updated = await _call(
        "sch_update_symbol_properties",
        {
            "schematic_path": str(sch_file),
            "reference_or_uuid": "R1",
            "reference": "R10",
            "value": "4.7k",
            "custom_fields": {"MPN": "RC0603FR-074K7L"},
            "dnp": True,
        },
    )
    assert updated["symbol"]["reference"] == "R10"
    assert updated["symbol"]["value"] == "4.7k"
    assert updated["symbol"]["dnp"] is True
    assert updated["symbol"]["properties"]["MPN"] == "RC0603FR-074K7L"
    assert updated["symbol"]["instances"][0]["reference"] == "R10"

    # 5. Add wires, labels, junction, no-connect, and text
    wire_res = await _call(
        "sch_add_wire",
        {
            "schematic_path": str(sch_file),
            "points_mm": [[100.0, 76.19], [110.0, 76.19], [110.0, 70.0]],
        },
    )
    assert wire_res["count"] == 2

    lbl_res = await _call(
        "sch_add_label",
        {
            "schematic_path": str(sch_file),
            "text": "VCC_3V3",
            "x_mm": 110.0,
            "y_mm": 70.0,
            "kind": "global",
            "shape": "input",
        },
    )
    junc_res = await _call(
        "sch_add_junction_or_no_connect",
        {"schematic_path": str(sch_file), "kind": "junction", "x_mm": 110.0, "y_mm": 76.19},
    )
    nc_res = await _call(
        "sch_add_junction_or_no_connect",
        {"schematic_path": str(sch_file), "kind": "no_connect", "x_mm": 100.0, "y_mm": 83.81},
    )
    txt_res = await _call(
        "sch_add_text",
        {"schematic_path": str(sch_file), "text": "Pull-up resistor", "x_mm": 115.0, "y_mm": 80.0},
    )

    inspected = await _call("sch_inspect", {"schematic_path": str(sch_file)})
    assert inspected["counts"]["symbols"] == 1
    assert inspected["counts"]["wires"] == 2
    assert inspected["counts"]["labels"] == 1
    assert inspected["counts"]["junctions"] == 1
    assert inspected["counts"]["no_connects"] == 1

    # 6. Delete items by UUID
    deleted = await _call(
        "sch_delete_items",
        {
            "schematic_path": str(sch_file),
            "uuids": [lbl_res["uuid"], junc_res["uuid"], nc_res["uuid"], txt_res["uuid"]],
        },
    )
    assert deleted["deleted_count"] == 4
    after_del = await _call("sch_inspect", {"schematic_path": str(sch_file)})
    assert after_del["counts"]["labels"] == 0
    assert after_del["counts"]["junctions"] == 0
    assert after_del["counts"]["no_connects"] == 0


@pytest.mark.asyncio
async def test_schematic_read_only_guard(tmp_path: Path) -> None:
    set_config(KiCadConfig(read_only=True))
    sch_file = tmp_path / "ro.kicad_sch"
    sch_file.write_text(MINIMAL_SCH, encoding="utf-8")

    with pytest.raises(PermissionError, match="KICAD_READ_ONLY"):
        await _call(
            "sch_add_text",
            {"schematic_path": str(sch_file), "text": "blocked", "x_mm": 10.0, "y_mm": 10.0},
        )
    set_config(KiCadConfig(read_only=False))
