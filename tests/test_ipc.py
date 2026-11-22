"""Mocked `kipy` tests for live IPC PCB inspection, edits, commit rollback, and read-only guard."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from kipy.board_types import (
    BoardLayer,
    BoardSegment,
    FootprintInstance,
    Track,
    Via,
    ViaType,
    Zone,
    ZoneType,
)
from kipy.geometry import Angle, Box2, Vector2
from kipy.proto.common.types import KIID

from kicad_mcp.bridges.ipc import KiCadIPCBridge, nm, set_ipc_bridge
from kicad_mcp.config import KiCadConfig, set_config
from kicad_mcp.server import create_server


def _make_fake_footprint(ref: str, val: str, fp_id: str, x_mm: float, y_mm: float, uid: str) -> Any:
    fp = FootprintInstance()
    fp.proto.id.value = uid
    fp.reference_field.text.value = ref
    fp.value_field.text.value = val
    fp.datasheet_field.text.value = "https://example.com/ds"
    fp.description_field.text.value = "Test component"
    fp.position = Vector2.from_xy(nm(x_mm), nm(y_mm))
    fp.orientation = Angle.from_degrees(0.0)
    fp.layer = BoardLayer.BL_F_Cu
    fp.locked = False
    fp.attributes.do_not_populate = False
    fp.attributes.exclude_from_bill_of_materials = False
    fp.attributes.exclude_from_position_files = False
    return fp


@dataclass
class FakeBoard:
    name: str = "demo.kicad_pcb"
    footprints: list[Any] = field(default_factory=list)
    tracks: list[Any] = field(default_factory=list)
    vias: list[Any] = field(default_factory=list)
    zones: list[Any] = field(default_factory=list)
    shapes: list[Any] = field(default_factory=list)
    texts: list[Any] = field(default_factory=list)
    selection: list[Any] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    dropped_commits: int = 0
    saved_paths: list[str] = field(default_factory=list)
    refilled: bool = False
    fail_on_update: bool = False

    def __post_init__(self) -> None:
        self._nets = [
            SimpleNamespace(code=0, name=""),
            SimpleNamespace(code=1, name="GND"),
            SimpleNamespace(code=2, name="+3V3"),
        ]

    def get_copper_layer_count(self) -> int:
        return 4

    def get_enabled_layers(self) -> list[int]:
        return [BoardLayer.BL_F_Cu, BoardLayer.BL_In1_Cu, BoardLayer.BL_In2_Cu, BoardLayer.BL_B_Cu]

    def get_layer_by_name(self, name: str) -> int:
        mapping = {
            "F.Cu": BoardLayer.BL_F_Cu,
            "B.Cu": BoardLayer.BL_B_Cu,
            "In1.Cu": BoardLayer.BL_In1_Cu,
            "F.SilkS": BoardLayer.BL_F_SilkS,
            "Edge.Cuts": BoardLayer.BL_Edge_Cuts,
        }
        return mapping.get(name, BoardLayer.BL_UNKNOWN)

    def get_title_block_info(self) -> Any:
        return SimpleNamespace(title="Demo", revision="1.0", date="2026-01-01", company="Acme", comments={})

    def get_origin(self, _origin_type: int) -> Vector2:
        return Vector2.from_xy(0, 0)

    def get_project(self) -> Any:
        nc = SimpleNamespace(name="Default", clearance=nm(0.2), track_width=nm(0.25))
        return SimpleNamespace(get_net_classes=lambda: [nc])

    def get_stackup(self) -> Any:
        ly = SimpleNamespace(
            layer=BoardLayer.BL_F_Cu,
            user_name="F.Cu",
            enabled=True,
            thickness=nm(0.035),
            material_name="Copper",
            dielectric=None,
            soldermask=None,
            silkscreen=None,
        )
        return SimpleNamespace(
            layers=[ly],
            finish=SimpleNamespace(type_name="ENIG"),
            impedance=SimpleNamespace(is_controlled=True),
        )

    def get_nets(self) -> list[Any]:
        return self._nets

    def get_netclass_for_nets(self, nets: list[Any]) -> dict[str, Any]:
        nc = SimpleNamespace(name="Default")
        return {n.name: nc for n in nets}

    def get_footprints(self) -> list[Any]:
        return self.footprints

    def get_tracks(self) -> list[Any]:
        return self.tracks

    def get_vias(self) -> list[Any]:
        return self.vias

    def get_zones(self) -> list[Any]:
        return self.zones

    def get_shapes(self) -> list[Any]:
        return self.shapes

    def get_text(self) -> list[Any]:
        return self.texts

    def get_groups(self) -> list[Any]:
        return [SimpleNamespace(id=KIID(value="grp-1"), name="Power", items=[KIID(value="fp-1")])]

    def get_item_bounding_box(self, _item: Any, include_text: bool = False) -> Box2:
        return Box2.from_xywh(nm(10.0), nm(20.0), nm(3.0), nm(2.0))

    def get_connected_items(self, _kiids: list[Any]) -> list[Any]:
        return list(self.tracks)

    def get_selection(self) -> list[Any]:
        return self.selection

    def clear_selection(self) -> None:
        self.selection.clear()

    def add_to_selection(self, items: list[Any]) -> list[Any]:
        self.selection.extend(items)
        return self.selection

    def get_items_by_id(self, ids: list[Any]) -> list[Any]:
        id_set = {i.value for i in ids}
        all_items = self.footprints + self.tracks + self.vias + self.zones + self.shapes
        return [it for it in all_items if it.id.value in id_set]

    def begin_commit(self) -> object:
        return object()

    def push_commit(self, _txn: object, message: str) -> None:
        self.commits.append(message)

    def drop_commit(self, _txn: object) -> None:
        self.dropped_commits += 1

    def update_items(self, items: list[Any]) -> list[Any]:
        if self.fail_on_update:
            raise RuntimeError("Simulated KiCad IPC update failure")
        return items

    def flip_items(self, items: list[Any], direction: int = 1) -> list[Any]:
        for it in items:
            it.layer = BoardLayer.BL_B_Cu if it.layer == BoardLayer.BL_F_Cu else BoardLayer.BL_F_Cu
        return items

    def create_items(self, items: list[Any]) -> list[Any]:
        for idx, it in enumerate(items, start=1):
            if not it.id.value:
                it.proto.id.value = f"created-{idx}"
            if isinstance(it, Track):
                self.tracks.append(it)
            elif isinstance(it, Via):
                self.vias.append(it)
            elif isinstance(it, Zone):
                self.zones.append(it)
            else:
                self.shapes.append(it)
        return items

    def remove_items_by_id(self, ids: list[Any]) -> None:
        id_set = {i.value for i in ids}
        self.tracks = [t for t in self.tracks if t.id.value not in id_set]
        self.vias = [v for v in self.vias if v.id.value not in id_set]
        self.shapes = [s for s in self.shapes if s.id.value not in id_set]

    def refill_zones(self, block: bool = True) -> None:
        self.refilled = True

    def save(self) -> None:
        self.saved_paths.append(self.name)

    def save_as(self, filename: str, overwrite: bool = False, include_project: bool = True) -> None:
        self.saved_paths.append(filename)


class FakeBridge(KiCadIPCBridge):
    def __init__(self, board: FakeBoard, config: KiCadConfig) -> None:
        super().__init__(config=config)
        self._board = board

    def get_board(self) -> Any:
        return self._board


async def _call(name: str, args: dict | None = None) -> dict:
    server = create_server()
    tool = server._tool_manager.get_tool(name)
    assert tool is not None
    return await tool.fn(**(args or {}))


def _setup_board(read_only: bool = False) -> FakeBoard:
    cfg = KiCadConfig(read_only=read_only)
    set_config(cfg)
    board = FakeBoard()
    board.footprints.append(_make_fake_footprint("R1", "10k", "R_0603", 10.0, 20.0, "fp-1"))

    trk = Track()
    trk.proto.id.value = "trk-1"
    trk.start = Vector2.from_xy(nm(0.0), nm(0.0))
    trk.end = Vector2.from_xy(nm(10.0), nm(0.0))
    trk.width = nm(0.25)
    trk.layer = BoardLayer.BL_F_Cu
    board.tracks.append(trk)

    via = Via()
    via.proto.id.value = "via-1"
    via.position = Vector2.from_xy(nm(5.0), nm(5.0))
    via.diameter = nm(0.6)
    via.drill_diameter = nm(0.3)
    via.type = ViaType.VT_THROUGH
    board.vias.append(via)

    seg = BoardSegment()
    seg.proto.id.value = "seg-1"
    seg.start = Vector2.from_xy(nm(0.0), nm(0.0))
    seg.end = Vector2.from_xy(nm(50.0), nm(0.0))
    seg.layer = BoardLayer.BL_Edge_Cuts
    seg.attributes.stroke.width = nm(0.1)
    board.shapes.append(seg)

    z = Zone()
    z.proto.id.value = "zone-1"
    z.name = "GND_POUR"
    z.type = ZoneType.ZT_COPPER
    z.layers = [BoardLayer.BL_F_Cu]
    board.zones.append(z)

    set_ipc_bridge(FakeBridge(board, cfg))
    return board


@pytest.mark.asyncio
async def test_ipc_inspect_tools() -> None:
    _setup_board(read_only=False)

    info = await _call("pcb_get_board_info")
    assert info["copper_layer_count"] == 4
    assert info["counts"]["footprints"] == 1

    stackup = await _call("pcb_get_stackup")
    assert stackup["finish"] == "ENIG"
    assert stackup["impedance"] is True

    nets = await _call("pcb_list_nets", {"name_pattern": "GND"})
    assert nets["total_matched"] == 1

    fps = await _call("pcb_list_footprints", {"reference_regex": "^R"})
    assert fps["total_matched"] == 1
    assert fps["footprints"][0]["reference"] == "R1"

    fp_det = await _call("pcb_get_footprint_details", {"reference_or_id": "R1"})
    assert fp_det["bounding_box"]["width_mm"] == 3.0

    tracks = await _call("pcb_list_tracks", {"layer": "F.Cu"})
    assert tracks["total_matched"] == 1
    assert tracks["total_length_mm"] == 10.0

    vias = await _call("pcb_list_vias", {"via_type": "through"})
    assert vias["total_matched"] == 1

    zones = await _call("pcb_list_zones", {"layer": "F.Cu"})
    assert zones["count"] == 1

    gfx = await _call("pcb_list_graphics", {"layer": "Edge.Cuts"})
    assert gfx["total_matched"] == 1

    conn = await _call("pcb_get_connected_items", {"item_ids": ["trk-1"]})
    assert conn["total_connected"] == 1

    groups = await _call("pcb_list_groups")
    assert groups["count"] == 1


@pytest.mark.asyncio
async def test_ipc_edit_tools_and_commits() -> None:
    board = _setup_board(read_only=False)

    moved = await _call(
        "pcb_move_footprint",
        {"reference_or_id": "R1", "dx_mm": 5.0, "dy_mm": -2.0, "rotation_deg": 90.0},
    )
    assert moved["updated"]["position"] == {"x_mm": 15.0, "y_mm": 18.0}
    assert moved["updated"]["rotation_deg"] == 90.0

    flipped = await _call("pcb_flip_footprint", {"reference_or_id": "R1", "direction": "left_right"})
    assert flipped["flipped"]["layer"] == "B.Cu"

    fields = await _call(
        "pcb_update_footprint_fields",
        {"reference_or_id": "R1", "value": "4.7k", "dnp": True},
    )
    assert fields["updated"]["value"] == "4.7k"
    assert fields["updated"]["dnp"] is True

    trk = await _call(
        "pcb_add_track",
        {"points_mm": [[0.0, 0.0], [5.0, 0.0], [5.0, 5.0]], "layer": "F.Cu", "width_mm": 0.3},
    )
    assert trk["count"] == 2

    via = await _call(
        "pcb_add_via",
        {"x_mm": 12.0, "y_mm": 12.0, "diameter_mm": 0.8, "drill_mm": 0.4},
    )
    assert via["via"]["diameter_mm"] == 0.8

    zone = await _call(
        "pcb_add_zone",
        {
            "outline_mm": [[0.0, 0.0], [20.0, 0.0], [20.0, 20.0], [0.0, 20.0]],
            "layers": ["B.Cu"],
            "name": "B_GND",
            "refill": True,
        },
    )
    assert zone["refilled"] is True
    assert board.refilled is True

    gfx = await _call(
        "pcb_add_graphic",
        {"kind": "circle", "layer": "F.SilkS", "center_mm": [5.0, 5.0], "radius_mm": 2.0},
    )
    assert gfx["item"]["kind"] == "BoardCircle"

    sel = await _call("pcb_set_selection", {"references": ["R1"]})
    assert sel["selected_count"] == 1
    sel_now = await _call("pcb_get_selection")
    assert sel_now["count"] == 1

    deleted = await _call("pcb_delete_items", {"item_ids": ["via-1"]})
    assert deleted["count"] == 1

    saved = await _call("pcb_save_board")
    assert saved["saved"] is True
    assert len(board.commits) >= 7


@pytest.mark.asyncio
async def test_ipc_commit_rollback_on_failure() -> None:
    board = _setup_board(read_only=False)
    board.fail_on_update = True

    with pytest.raises(RuntimeError, match="Simulated KiCad IPC update failure"):
        await _call("pcb_move_footprint", {"reference_or_id": "R1", "x_mm": 1.0, "y_mm": 1.0})

    assert board.dropped_commits == 1
    assert len(board.commits) == 0


@pytest.mark.asyncio
async def test_ipc_read_only_enforcement() -> None:
    _setup_board(read_only=True)

    # Read tools still succeed
    info = await _call("pcb_get_board_info")
    assert info["board_name"] == "demo.kicad_pcb"

    # Write tools are blocked
    with pytest.raises(PermissionError, match="KICAD_READ_ONLY"):
        await _call("pcb_move_footprint", {"reference_or_id": "R1", "x_mm": 1.0, "y_mm": 1.0})
    with pytest.raises(PermissionError, match="KICAD_READ_ONLY"):
        await _call("pcb_refill_zones")
    with pytest.raises(PermissionError, match="KICAD_READ_ONLY"):
        await _call("pcb_save_board")
