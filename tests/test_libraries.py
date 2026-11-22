"""Tests for symbol & footprint library discovery, search, inheritance, and inspection."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_mcp.config import KiCadConfig, set_config
from kicad_mcp.server import create_server

SAMPLE_SYM_LIB = """(kicad_symbol_lib
	(version 20251024)
	(generator "kicad_symbol_editor")
	(symbol "BaseRes"
		(in_bom yes)
		(on_board yes)
		(property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "Value" "BaseRes" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "Datasheet" "https://example.com/res.pdf" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "Description" "Generic resistor" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "ki_keywords" "resistor passive" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(symbol "BaseRes_1_1"
			(pin passive line (at 0 3.81 270) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
			(pin passive line (at 0 -3.81 90) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
		)
	)
	(symbol "PrecisionRes"
		(extends "BaseRes")
		(property "Value" "PrecisionRes" (at 0 0 0) (effects (font (size 1.27 1.27))))
		(property "Description" "0.1% precision resistor" (at 0 0 0) (effects (font (size 1.27 1.27))))
	)
)
"""

SAMPLE_FOOTPRINT = """(footprint "R_0603_Test"
	(version 20260206)
	(layer "F.Cu")
	(descr "Test SMD 0603 resistor")
	(tags "resistor smd 0603")
	(attr smd)
	(property "Reference" "REF**" (at 0 -1.4 0) (layer "F.SilkS") (effects (font (size 1 1))))
	(property "Value" "R_0603_Test" (at 0 1.4 0) (layer "F.Fab") (effects (font (size 1 1))))
	(fp_rect (start -1.5 -0.75) (end 1.5 0.75) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))
	(pad "1" smd roundrect (at -0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Mask" "F.Paste"))
	(pad "2" smd roundrect (at 0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Mask" "F.Paste"))
	(pad "3" thru_hole circle (at 0 0) (size 1.2 1.2) (drill 0.6) (layers "*.Cu" "*.Mask"))
	(model "${KICAD10_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.step")
)
"""


def _setup_fixture_libs(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    sym_dir = proj / "libs"
    fp_pretty = proj / "MyFP.pretty"
    sym_dir.mkdir(parents=True)
    fp_pretty.mkdir(parents=True)

    (sym_dir / "MySym.kicad_sym").write_text(SAMPLE_SYM_LIB, encoding="utf-8")
    (fp_pretty / "R_0603_Test.kicad_mod").write_text(SAMPLE_FOOTPRINT, encoding="utf-8")

    (proj / "sym-lib-table").write_text(
        '(sym_lib_table (version 7) (lib (name "MySym") (type "KiCad") '
        '(uri "${KIPRJMOD}/libs/MySym.kicad_sym") (options "") (descr "Project symbols")))\n',
        encoding="utf-8",
    )
    (proj / "fp-lib-table").write_text(
        '(fp_lib_table (version 7) (lib (name "MyFP") (type "KiCad") '
        '(uri "${KIPRJMOD}/MyFP.pretty") (options "") (descr "Project footprints")))\n',
        encoding="utf-8",
    )
    return proj


async def _call_tool(name: str, args: dict) -> dict:
    server = create_server()
    tool = server._tool_manager.get_tool(name)
    assert tool is not None, f"Tool {name} not registered"
    return await tool.fn(**args)


@pytest.mark.asyncio
async def test_library_discovery_and_search(tmp_path: Path) -> None:
    proj = _setup_fixture_libs(tmp_path)
    cfg = KiCadConfig(
        config_dir=tmp_path / "empty_cfg",
        symbol_lib_dirs=[],
        footprint_lib_dirs=[],
    )
    set_config(cfg)

    listed = await _call_tool("list_libraries", {"kind": "all", "project_dir": str(proj)})
    assert listed["symbol_library_count"] == 1
    assert listed["symbol_libraries"][0]["name"] == "MySym"
    assert listed["symbol_libraries"][0]["exists"] is True
    assert listed["footprint_library_count"] == 1
    assert listed["footprint_libraries"][0]["name"] == "MyFP"

    sym_search = await _call_tool(
        "search_symbols", {"query": "precision", "project_dir": str(proj)}
    )
    assert sym_search["count"] == 1
    assert sym_search["symbols"][0]["lib_id"] == "MySym:PrecisionRes"

    # Inspect derived symbol — verifies `extends` inheritance merges parent pins & datasheet
    sym_info = await _call_tool(
        "inspect_symbol", {"lib_id": "MySym:PrecisionRes", "project_dir": str(proj)}
    )
    assert sym_info["extends_chain"] == ["BaseRes", "PrecisionRes"]
    assert sym_info["properties"]["Description"] == "0.1% precision resistor"
    assert sym_info["properties"]["Datasheet"] == "https://example.com/res.pdf"
    assert sym_info["pin_count"] == 2
    assert {p["number"] for p in sym_info["pins"]} == {"1", "2"}

    # Footprint search & inspect
    fp_search = await _call_tool(
        "search_footprints",
        {"query": "0603", "project_dir": str(proj), "search_metadata": True},
    )
    assert fp_search["count"] == 1
    assert fp_search["footprints"][0]["lib_id"] == "MyFP:R_0603_Test"

    fp_info = await _call_tool(
        "inspect_footprint", {"lib_id_or_path": "MyFP:R_0603_Test", "project_dir": str(proj)}
    )
    assert fp_info["pad_count"] == 3
    assert fp_info["attributes"] == ["smd"]
    assert fp_info["courtyard_bbox"] == {
        "min_x_mm": -1.5,
        "min_y_mm": -0.75,
        "max_x_mm": 1.5,
        "max_y_mm": 0.75,
        "width_mm": 3.0,
        "height_mm": 1.5,
    }
    assert fp_info["pads"][2]["drill"] == {"oval": False, "diameter_mm": 0.6}
    assert len(fp_info["models_3d"]) == 1
