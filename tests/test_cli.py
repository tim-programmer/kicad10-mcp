"""Real integration tests against local `kicad-cli 10.0.8`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from kicad_mcp.bridges.cli import KiCadCLI
from kicad_mcp.config import KiCadConfig, set_config
from kicad_mcp.server import create_server

pytestmark = pytest.mark.skipif(
    shutil.which("kicad-cli") is None,
    reason="kicad-cli not installed in PATH",
)

MINIMAL_PRO = {
    "meta": {"filename": "board.kicad_pro", "version": 3},
    "board": {"design_settings": {"rules": {"min_track_width": 0.15}}},
    "net_settings": {"classes": [{"name": "Default", "clearance": 0.2, "track_width": 0.25}]},
    "text_variables": {"REV": "A"},
}

MINIMAL_SCH = """(kicad_sch
	(version 20250114)
	(generator "eeschema")
	(generator_version "10.0")
	(uuid "11111111-1111-4111-8111-111111111111")
	(paper "A4")
	(lib_symbols
		(symbol "Device:R"
			(pin_numbers (hide yes))
			(pin_names (offset 0))
			(exclude_from_sim no)
			(in_bom yes)
			(on_board yes)
			(property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))
			(property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))
			(property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
			(property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
			(symbol "R_0_1"
				(rectangle (start -1.016 -2.54) (end 1.016 2.54) (stroke (width 0.254) (type default)) (fill (type none)))
			)
			(symbol "R_1_1"
				(pin passive line (at 0 3.81 270) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
				(pin passive line (at 0 -3.81 90) (length 1.27) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
			)
		)
	)
	(symbol
		(lib_id "Device:R")
		(at 100 80 0)
		(unit 1)
		(exclude_from_sim no)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(uuid "22222222-2222-4222-8222-222222222222")
		(property "Reference" "R1" (at 102.54 78.73 0) (effects (font (size 1.27 1.27))))
		(property "Value" "10k" (at 102.54 81.27 0) (effects (font (size 1.27 1.27))))
		(property "Footprint" "Resistor_SMD:R_0603_1608Metric" (at 100 80 0) (effects (font (size 1.27 1.27)) (hide yes)))
		(property "Datasheet" "~" (at 100 80 0) (effects (font (size 1.27 1.27)) (hide yes)))
		(pin "1" (uuid "33333333-3333-4333-8333-333333333331"))
		(pin "2" (uuid "33333333-3333-4333-8333-333333333332"))
		(instances
			(project "board"
				(path "/11111111-1111-4111-8111-111111111111"
					(reference "R1")
					(unit 1)
				)
			)
		)
	)
	(no_connect (at 100 76.19) (uuid "44444444-4444-4444-8444-444444444441"))
	(no_connect (at 100 83.81) (uuid "44444444-4444-4444-8444-444444444442"))
	(sheet_instances
		(path "/"
			(page "1")
		)
	)
)
"""

MINIMAL_PCB = """(kicad_pcb
	(version 20241229)
	(generator "pcbnew")
	(generator_version "10.0")
	(general
		(thickness 1.6)
		(legacy_teardrops no)
	)
	(paper "A4")
	(layers
		(0 "F.Cu" signal)
		(2 "B.Cu" signal)
		(9 "F.Adhes" user "F.Adhesive")
		(11 "B.Adhes" user "B.Adhesive")
		(13 "F.Paste" user)
		(15 "B.Paste" user)
		(5 "F.SilkS" user "F.Silkscreen")
		(7 "B.SilkS" user "B.Silkscreen")
		(1 "F.Mask" user)
		(3 "B.Mask" user)
		(17 "Dwgs.User" user "User.Drawings")
		(19 "Cmts.User" user "User.Comments")
		(21 "Eco1.User" user "User.Eco1")
		(23 "Eco2.User" user "User.Eco2")
		(25 "Edge.Cuts" user)
		(27 "Margin" user)
		(31 "F.CrtYd" user "F.Courtyard")
		(29 "B.CrtYd" user "B.Courtyard")
		(35 "F.Fab" user)
		(33 "B.Fab" user)
	)
	(setup
		(pad_to_mask_clearance 0)
	)
	(net 0 "")
	(net 1 "NET1")
	(net 2 "NET2")
	(gr_rect
		(start 100 100)
		(end 120 115)
		(stroke (width 0.1) (type default))
		(fill none)
		(layer "Edge.Cuts")
		(uuid "55555555-5555-4555-8555-555555555551")
	)
	(footprint "Resistor_SMD:R_0603_1608Metric"
		(layer "F.Cu")
		(uuid "55555555-5555-4555-8555-555555555552")
		(at 110 107.5)
		(property "Reference" "R1" (at 0 -1.5 0) (layer "F.SilkS") (uuid "55555555-5555-4555-8555-555555555553") (effects (font (size 1 1) (thickness 0.15))))
		(property "Value" "10k" (at 0 1.5 0) (layer "F.Fab") (uuid "55555555-5555-4555-8555-555555555554") (effects (font (size 1 1) (thickness 0.15))))
		(attr smd)
		(pad "1" smd roundrect (at -0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Mask" "F.Paste") (roundrect_rratio 0.25) (net 1 "NET1") (uuid "55555555-5555-4555-8555-555555555555"))
		(pad "2" smd roundrect (at 0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Mask" "F.Paste") (roundrect_rratio 0.25) (net 2 "NET2") (uuid "55555555-5555-4555-8555-555555555556"))
	)
	(via (at 105 105) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "55555555-5555-4555-8555-555555555557"))
	(segment (start 105 105) (end 109.175 107.5) (width 0.25) (layer "F.Cu") (net 1) (uuid "55555555-5555-4555-8555-555555555558"))
)
"""


def _write_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    pro = tmp_path / "board.kicad_pro"
    sch = tmp_path / "board.kicad_sch"
    pcb = tmp_path / "board.kicad_pcb"
    pro.write_text(json.dumps(MINIMAL_PRO, indent=2), encoding="utf-8")
    sch.write_text(MINIMAL_SCH, encoding="utf-8")
    pcb.write_text(MINIMAL_PCB, encoding="utf-8")
    return pro, sch, pcb


async def _call(name: str, args: dict) -> dict:
    server = create_server()
    tool = server._tool_manager.get_tool(name)
    assert tool is not None
    return await tool.fn(**args)


@pytest.mark.asyncio
async def test_cli_version() -> None:
    set_config(KiCadConfig())
    cli = KiCadCLI()
    ver = await cli.version()
    assert ver.startswith("10.")


@pytest.mark.asyncio
async def test_cli_verify_and_fab_pipeline(tmp_path: Path) -> None:
    set_config(KiCadConfig(read_only=False))
    _pro, sch, pcb = _write_project(tmp_path)

    # ERC
    erc = await _call("run_erc", {"schematic_path": str(sch)})
    assert erc["source"] == str(sch)
    assert "summary" in erc
    assert erc["summary"]["sheet_count"] >= 1

    # DRC
    drc = await _call("run_drc", {"pcb_path": str(pcb)})
    assert drc["source"] == str(pcb)
    assert "summary" in drc
    assert isinstance(drc["passed"], bool)

    # Board stats
    stats = await _call("get_board_stats", {"pcb_path": str(pcb)})
    assert stats["source"] == str(pcb)
    assert "stats" in stats

    # BOM
    bom_csv = tmp_path / "out" / "bom.csv"
    bom = await _call("export_bom", {"schematic_path": str(sch), "output_path": str(bom_csv)})
    assert bom_csv.is_file()
    assert bom["row_count"] >= 1
    assert any("R1" in str(r) for r in bom["rows"])

    # Netlist
    net_out = tmp_path / "out" / "board.net"
    net = await _call(
        "export_netlist",
        {"schematic_path": str(sch), "output_path": str(net_out), "format": "kicadsexpr"},
    )
    assert net["exists"] is True
    assert net["size_bytes"] > 0

    # Gerbers
    gerber_dir = tmp_path / "out" / "gerbers"
    gerbers = await _call(
        "export_gerbers",
        {
            "pcb_path": str(pcb),
            "output_dir": str(gerber_dir),
            "layers": ["F.Cu", "B.Cu", "Edge.Cuts"],
        },
    )
    assert gerbers["file_count"] >= 3

    # Drill
    drill_dir = tmp_path / "out" / "drill"
    drill = await _call(
        "export_drill",
        {
            "pcb_path": str(pcb),
            "output_dir": str(drill_dir),
            "generate_map": True,
            "generate_report": True,
        },
    )
    assert drill["file_count"] >= 1

    # Position file
    pos_csv = tmp_path / "out" / "pos.csv"
    pos = await _call(
        "export_position_file",
        {"pcb_path": str(pcb), "output_path": str(pos_csv), "format": "csv", "units": "mm"},
    )
    assert pos["exists"] is True
    assert pos["row_count"] == 1

    # Schematic visual (PDF & SVG)
    sch_pdf = tmp_path / "out" / "sch.pdf"
    res_pdf = await _call(
        "export_schematic_visual",
        {"schematic_path": str(sch), "output_path": str(sch_pdf), "format": "pdf"},
    )
    assert sch_pdf.is_file()
    assert len(res_pdf["files"]) == 1

    sch_svg_dir = tmp_path / "out" / "sch_svg"
    res_svg = await _call(
        "export_schematic_visual",
        {"schematic_path": str(sch), "output_path": str(sch_svg_dir), "format": "svg"},
    )
    assert len(res_svg["files"]) >= 1

    # PCB visual (PDF & SVG)
    pcb_pdf = tmp_path / "out" / "pcb.pdf"
    res_pcb_pdf = await _call(
        "export_pcb_visual",
        {"pcb_path": str(pcb), "output_path": str(pcb_pdf), "format": "pdf"},
    )
    assert pcb_pdf.is_file()
    assert len(res_pcb_pdf["files"]) == 1

    pcb_svg = tmp_path / "out" / "pcb.svg"
    res_pcb_svg = await _call(
        "export_pcb_visual",
        {"pcb_path": str(pcb), "output_path": str(pcb_svg), "format": "svg", "mode_single": True},
    )
    assert pcb_svg.is_file()
    assert len(res_pcb_svg["files"]) == 1

    # 3D render
    render_png = tmp_path / "out" / "top.png"
    rendered = await _call(
        "render_pcb_3d",
        {
            "pcb_path": str(pcb),
            "output_path": str(render_png),
            "width": 320,
            "height": 240,
            "quality": "basic",
        },
    )
    assert rendered["exists"] is True
    assert rendered["size_bytes"] > 0

    # ODB++
    odb_zip = tmp_path / "out" / "board_odb.zip"
    odb = await _call(
        "export_odb_or_ipc2581",
        {"pcb_path": str(pcb), "output_path": str(odb_zip), "format": "odb"},
    )
    assert odb["exists"] is True


@pytest.mark.asyncio
async def test_multi_variant_requires_placeholder(tmp_path: Path) -> None:
    _pro, sch, _pcb = _write_project(tmp_path)
    with pytest.raises(ValueError, match=r"\$\{VARIANT\}"):
        await _call(
            "export_bom",
            {
                "schematic_path": str(sch),
                "output_path": str(tmp_path / "bom.csv"),
                "variants": ["v1", "v2"],
            },
        )
