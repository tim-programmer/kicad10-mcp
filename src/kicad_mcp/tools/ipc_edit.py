"""Live KiCad 10 IPC API PCB edit (write) tools — commit-wrapped and read-only guarded."""

from __future__ import annotations

import asyncio
import itertools
from typing import Any, Literal

from kipy.board_types import (
    BoardCircle,
    BoardRectangle,
    BoardSegment,
    BoardText,
    FootprintInstance,
    Track,
    Via,
    ViaType,
    Zone,
    ZoneType,
)
from kipy.geometry import Angle, PolygonWithHoles, PolyLine, PolyLineNode, Vector2
from kipy.proto.board import board_commands_pb2
from kipy.proto.common.types import KIID
from mcp.server.mcpserver import MCPServer

from kicad_mcp.bridges.ipc import (
    find_net_by_name,
    get_ipc_bridge,
    nm,
    resolve_layer,
    serialize_board_item,
    serialize_footprint,
    serialize_track,
    serialize_via,
    serialize_zone,
)
from kicad_mcp.config import get_config
from kicad_mcp.tools.common import DESTRUCTIVE_ANNOTATIONS, WRITE_ANNOTATIONS


def _find_footprint(board: Any, reference_or_id: str) -> FootprintInstance:
    for fp in board.get_footprints():
        ref = fp.reference_field.text.value if fp.reference_field else ""
        if ref == reference_or_id or fp.id.value == reference_or_id:
            return fp
    raise ValueError(f"Footprint {reference_or_id!r} not found on open board.")


_VIA_TYPE_MAP = {
    "through": ViaType.VT_THROUGH,
    "blind_buried": ViaType.VT_BLIND_BURIED,
    "micro": ViaType.VT_MICRO,
    "blind": ViaType.VT_BLIND,
    "buried": ViaType.VT_BURIED,
}


def register_ipc_edit_tools(server: MCPServer) -> None:
    """Register live KiCad 10 IPC PCB write tools."""

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_move_footprint(
        reference_or_id: str,
        *,
        x_mm: float | None = None,
        y_mm: float | None = None,
        dx_mm: float | None = None,
        dy_mm: float | None = None,
        rotation_deg: float | None = None,
        relative_rotation: bool = False,
        locked: bool | None = None,
    ) -> dict[str, Any]:
        """Move, rotate, or lock/unlock a footprint on the open PCB inside an atomic undo commit.

        Provide absolute (`x_mm`, `y_mm`) and/or relative (`dx_mm`, `dy_mm`) coordinates.
        """
        def _run() -> dict[str, Any]:
            bridge = get_ipc_bridge()
            board = bridge.get_board()
            fp = _find_footprint(board, reference_or_id)

            cur_x = fp.position.x
            cur_y = fp.position.y
            new_x = nm(x_mm) if x_mm is not None else cur_x
            new_y = nm(y_mm) if y_mm is not None else cur_y
            if dx_mm is not None:
                new_x += nm(dx_mm)
            if dy_mm is not None:
                new_y += nm(dy_mm)
            fp.position = Vector2.from_xy(new_x, new_y)

            if rotation_deg is not None:
                target_deg = (
                    fp.orientation.degrees + rotation_deg if relative_rotation else rotation_deg
                )
                fp.orientation = Angle.from_degrees(target_deg)
            if locked is not None:
                fp.locked = locked

            ref = fp.reference_field.text.value if fp.reference_field else reference_or_id
            with bridge.commit(board, f"Move footprint {ref}"):
                updated = board.update_items([fp])
            result_fp = updated[0] if updated else fp
            return {"updated": serialize_footprint(result_fp)}

        return await asyncio.to_thread(_run)

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_flip_footprint(
        reference_or_id: str,
        direction: Literal["left_right", "top_bottom"] = "left_right",
    ) -> dict[str, Any]:
        """Flip a footprint to the opposite side of the board (KiCad 10.0.6+ `board.flip_items`).

        Args:
            reference_or_id: Footprint reference designator (e.g. `'R1'`) or KIID UUID.
            direction: Flip axis (`left_right` or `top_bottom`).
        """
        dir_enum = (
            board_commands_pb2.BoardFlipDirection.BFD_LEFT_RIGHT
            if direction == "left_right"
            else board_commands_pb2.BoardFlipDirection.BFD_TOP_BOTTOM
        )

        def _run() -> dict[str, Any]:
            bridge = get_ipc_bridge()
            board = bridge.get_board()
            fp = _find_footprint(board, reference_or_id)
            ref = fp.reference_field.text.value if fp.reference_field else reference_or_id
            with bridge.commit(board, f"Flip footprint {ref}"):
                flipped = board.flip_items([fp], direction=dir_enum)
            result_fp = flipped[0] if flipped else fp
            return {"flipped": serialize_footprint(result_fp), "direction": direction}

        return await asyncio.to_thread(_run)

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_update_footprint_fields(
        reference_or_id: str,
        *,
        reference: str | None = None,
        value: str | None = None,
        datasheet: str | None = None,
        description: str | None = None,
        dnp: bool | None = None,
        exclude_from_bom: bool | None = None,
        exclude_from_pos: bool | None = None,
    ) -> dict[str, Any]:
        """Update fields and assembly flags on a PCB footprint inside an atomic commit."""
        def _run() -> dict[str, Any]:
            bridge = get_ipc_bridge()
            board = bridge.get_board()
            fp = _find_footprint(board, reference_or_id)

            if reference is not None and fp.reference_field is not None:
                fp.reference_field.text.value = reference
            if value is not None and fp.value_field is not None:
                fp.value_field.text.value = value
            if datasheet is not None and fp.datasheet_field is not None:
                fp.datasheet_field.text.value = datasheet
            if description is not None and fp.description_field is not None:
                fp.description_field.text.value = description
            if fp.attributes is not None:
                if dnp is not None:
                    fp.attributes.do_not_populate = dnp
                if exclude_from_bom is not None:
                    fp.attributes.exclude_from_bill_of_materials = exclude_from_bom
                if exclude_from_pos is not None:
                    fp.attributes.exclude_from_position_files = exclude_from_pos

            with bridge.commit(board, f"Update footprint {reference_or_id}"):
                updated = board.update_items([fp])
            result_fp = updated[0] if updated else fp
            return {"updated": serialize_footprint(result_fp, include_pads=False)}

        return await asyncio.to_thread(_run)

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_add_track(
        points_mm: list[list[float]],
        *,
        layer: str = "F.Cu",
        width_mm: float = 0.25,
        net: str | None = None,
        locked: bool = False,
    ) -> dict[str, Any]:
        """Route one or more connected straight copper track segments along a polyline.

        Args:
            points_mm: Ordered list of `[x_mm, y_mm]` coordinates (at least 2 points).
            layer: Copper layer name (e.g. `'F.Cu'`, `'B.Cu'`, `'In1.Cu'`).
            width_mm: Track width in millimeters (default 0.25).
            net: Optional net name to assign to the track segments.
            locked: Whether to lock the created tracks.
        """
        if len(points_mm) < 2:
            raise ValueError("points_mm must contain at least two [x_mm, y_mm] points")
        if width_mm <= 0:
            raise ValueError("width_mm must be positive")

        def _run() -> dict[str, Any]:
            bridge = get_ipc_bridge()
            board = bridge.get_board()
            layer_enum = resolve_layer(board, layer)
            net_obj = find_net_by_name(board, net) if net else None

            tracks: list[Track] = []
            for p1, p2 in itertools.pairwise(points_mm):
                if len(p1) != 2 or len(p2) != 2:
                    raise ValueError("Each point in points_mm must be a [x_mm, y_mm] pair")
                t = Track()
                t.start = Vector2.from_xy(nm(p1[0]), nm(p1[1]))
                t.end = Vector2.from_xy(nm(p2[0]), nm(p2[1]))
                t.width = nm(width_mm)
                t.layer = layer_enum
                t.locked = locked
                if net_obj is not None:
                    t.net = net_obj
                tracks.append(t)

            with bridge.commit(board, f"Add {len(tracks)} track segment(s) on {layer}"):
                created = board.create_items(tracks)
            return {
                "count": len(created),
                "tracks": [serialize_track(t) for t in created],
            }

        return await asyncio.to_thread(_run)

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_add_via(
        x_mm: float,
        y_mm: float,
        *,
        diameter_mm: float = 0.6,
        drill_mm: float = 0.3,
        net: str | None = None,
        via_type: Literal["through", "blind_buried", "micro", "blind", "buried"] = "through",
        start_layer: str | None = None,
        end_layer: str | None = None,
        locked: bool = False,
    ) -> dict[str, Any]:
        """Place a via on the open PCB inside an atomic commit.

        Args:
            x_mm: Via center X in millimeters.
            y_mm: Via center Y in millimeters.
            diameter_mm: Copper via pad diameter in mm (default 0.6).
            drill_mm: Drill hole diameter in mm (default 0.3).
            net: Optional net name to assign.
            via_type: Via type (`through`, `blind_buried`, `micro`, `blind`, `buried`).
            start_layer: Optional start copper layer (for blind/buried/micro vias).
            end_layer: Optional end copper layer (for blind/buried/micro vias).
            locked: Lock the via.
        """
        if diameter_mm <= 0 or drill_mm <= 0 or drill_mm >= diameter_mm:
            raise ValueError("diameter_mm and drill_mm must be positive with drill_mm < diameter_mm")

        def _run() -> dict[str, Any]:
            bridge = get_ipc_bridge()
            board = bridge.get_board()
            net_obj = find_net_by_name(board, net) if net else None

            via = Via()
            via.position = Vector2.from_xy(nm(x_mm), nm(y_mm))
            via.diameter = nm(diameter_mm)
            via.drill_diameter = nm(drill_mm)
            via.type = _VIA_TYPE_MAP[via_type]
            via.locked = locked
            if net_obj is not None:
                via.net = net_obj
            if start_layer is not None:
                via.padstack.drill.start_layer = resolve_layer(board, start_layer)
            if end_layer is not None:
                via.padstack.drill.end_layer = resolve_layer(board, end_layer)

            with bridge.commit(board, f"Add via at ({x_mm}, {y_mm}) mm"):
                created = board.create_items([via])
            result_via = created[0] if created else via
            return {"via": serialize_via(result_via)}

        return await asyncio.to_thread(_run)

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_add_zone(
        outline_mm: list[list[float]],
        *,
        layers: list[str] | None = None,
        net: str | None = None,
        name: str = "",
        is_rule_area: bool = False,
        priority: int = 0,
        clearance_mm: float | None = None,
        min_thickness_mm: float | None = None,
        refill: bool = False,
    ) -> dict[str, Any]:
        """Create a copper pour zone or rule/keepout area polygon on the open PCB.

        Args:
            outline_mm: Polygon vertices as `[[x1, y1], [x2, y2], ...]` (at least 3 points).
            layers: Layer list (defaults to `['F.Cu']`).
            net: Net name for copper pours (ignored when `is_rule_area=True`).
            name: Optional zone name.
            is_rule_area: If True, create a rule/keepout area instead of a copper pour.
            priority: Zone fill priority (higher fills first).
            clearance_mm: Copper pour clearance in mm.
            min_thickness_mm: Minimum copper width in mm.
            refill: Trigger zone refill after creating the zone.
        """
        if len(outline_mm) < 3:
            raise ValueError("outline_mm must contain at least 3 [x_mm, y_mm] vertices")

        def _run() -> dict[str, Any]:
            bridge = get_ipc_bridge()
            board = bridge.get_board()
            layer_names = layers or ["F.Cu"]
            layer_enums = [resolve_layer(board, ly) for ly in layer_names]

            pl = PolyLine()
            for pt in outline_mm:
                if len(pt) != 2:
                    raise ValueError("Each vertex in outline_mm must be [x_mm, y_mm]")
                pl.append(PolyLineNode.from_xy(nm(pt[0]), nm(pt[1])))
            pl.closed = True
            poly = PolygonWithHoles()
            poly.outline = pl

            zone = Zone()
            zone.name = name
            zone.type = ZoneType.ZT_RULE_AREA if is_rule_area else ZoneType.ZT_COPPER
            zone.layers = layer_enums
            zone.priority = priority
            zone.outline = poly
            if not is_rule_area:
                if net:
                    zone.net = find_net_by_name(board, net)
                if clearance_mm is not None:
                    zone.clearance = nm(clearance_mm)
                if min_thickness_mm is not None:
                    zone.min_thickness = nm(min_thickness_mm)

            with bridge.commit(board, f"Add zone {name or net or ''}".strip()):
                created = board.create_items([zone])
            if refill:
                board.refill_zones()

            result_zone = created[0] if created else zone
            return {"zone": serialize_zone(result_zone), "refilled": refill}

        return await asyncio.to_thread(_run)

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_refill_zones(block: bool = True) -> dict[str, Any]:
        """Trigger a zone refill across all copper zones on the open PCB."""
        def _run() -> dict[str, Any]:
            get_config().ensure_writable("pcb_refill_zones")
            board = get_ipc_bridge().get_board()
            board.refill_zones(block=block)
            return {"refilled": True, "block": block}

        return await asyncio.to_thread(_run)

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_add_graphic(
        kind: Literal["segment", "rectangle", "circle", "text"],
        *,
        layer: str = "F.SilkS",
        stroke_width_mm: float = 0.15,
        filled: bool = False,
        start_mm: list[float] | None = None,
        end_mm: list[float] | None = None,
        center_mm: list[float] | None = None,
        radius_mm: float | None = None,
        text: str | None = None,
        text_size_mm: float = 1.0,
        rotation_deg: float = 0.0,
    ) -> dict[str, Any]:
        """Add a graphic shape (`segment`, `rectangle`, `circle`) or `text` to any PCB layer
        (e.g. `Edge.Cuts`, `F.SilkS`, `F.Fab`, `User.Drawings`).
        """
        def _run() -> dict[str, Any]:
            bridge = get_ipc_bridge()
            board = bridge.get_board()
            layer_enum = resolve_layer(board, layer)

            item: Any
            if kind == "segment":
                if not start_mm or not end_mm or len(start_mm) != 2 or len(end_mm) != 2:
                    raise ValueError("kind='segment' requires start_mm=[x,y] and end_mm=[x,y]")
                seg = BoardSegment()
                seg.start = Vector2.from_xy(nm(start_mm[0]), nm(start_mm[1]))
                seg.end = Vector2.from_xy(nm(end_mm[0]), nm(end_mm[1]))
                seg.layer = layer_enum
                seg.attributes.stroke.width = nm(stroke_width_mm)
                item = seg
            elif kind == "rectangle":
                if not start_mm or not end_mm or len(start_mm) != 2 or len(end_mm) != 2:
                    raise ValueError("kind='rectangle' requires start_mm=[x1,y1] and end_mm=[x2,y2]")
                rect = BoardRectangle()
                rect.top_left = Vector2.from_xy(nm(start_mm[0]), nm(start_mm[1]))
                rect.bottom_right = Vector2.from_xy(nm(end_mm[0]), nm(end_mm[1]))
                rect.layer = layer_enum
                rect.attributes.stroke.width = nm(stroke_width_mm)
                rect.attributes.fill.filled = filled
                item = rect
            elif kind == "circle":
                if not center_mm or len(center_mm) != 2 or radius_mm is None or radius_mm <= 0:
                    raise ValueError("kind='circle' requires center_mm=[x,y] and positive radius_mm")
                circ = BoardCircle()
                circ.center = Vector2.from_xy(nm(center_mm[0]), nm(center_mm[1]))
                circ.radius_point = Vector2.from_xy(
                    nm(center_mm[0] + radius_mm), nm(center_mm[1])
                )
                circ.layer = layer_enum
                circ.attributes.stroke.width = nm(stroke_width_mm)
                circ.attributes.fill.filled = filled
                item = circ
            else:
                pos = center_mm or start_mm
                if not text or not pos or len(pos) != 2:
                    raise ValueError("kind='text' requires text and center_mm (or start_mm) [x,y]")
                bt = BoardText()
                bt.value = text
                bt.position = Vector2.from_xy(nm(pos[0]), nm(pos[1]))
                bt.layer = layer_enum
                bt.attributes.size = Vector2.from_xy(nm(text_size_mm), nm(text_size_mm))
                bt.attributes.stroke_width = nm(stroke_width_mm)
                bt.attributes.angle = Angle.from_degrees(rotation_deg)
                item = bt

            with bridge.commit(board, f"Add {kind} on {layer}"):
                created = board.create_items([item])
            result_item = created[0] if created else item
            return {"item": serialize_board_item(result_item)}

        return await asyncio.to_thread(_run)

    @server.tool(annotations=DESTRUCTIVE_ANNOTATIONS)
    async def pcb_delete_items(item_ids: list[str]) -> dict[str, Any]:
        """Delete one or more board items by their KIID UUID inside an atomic undo commit.

        Args:
            item_ids: List of KIID UUID strings to remove.
        """
        if not item_ids:
            raise ValueError("item_ids must contain at least one item UUID")

        def _run() -> dict[str, Any]:
            bridge = get_ipc_bridge()
            board = bridge.get_board()
            kiids = [KIID(value=uid) for uid in item_ids]
            with bridge.commit(board, f"Delete {len(kiids)} item(s)"):
                board.remove_items_by_id(kiids)
            return {"deleted_ids": item_ids, "count": len(item_ids)}

        return await asyncio.to_thread(_run)

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_set_selection(
        *,
        references: list[str] | None = None,
        item_ids: list[str] | None = None,
        clear_existing: bool = True,
    ) -> dict[str, Any]:
        """Highlight/select items in the live KiCad 10 PCB Editor GUI by footprint reference
        or KIID UUID, or clear the selection when neither is supplied.
        """
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            if clear_existing:
                board.clear_selection()

            to_select: list[Any] = []
            if references:
                ref_set = set(references)
                for fp in board.get_footprints():
                    ref = fp.reference_field.text.value if fp.reference_field else ""
                    if ref in ref_set:
                        to_select.append(fp)
            if item_ids:
                found = board.get_items_by_id([KIID(value=uid) for uid in item_ids])
                to_select.extend(found)

            if to_select:
                board.add_to_selection(to_select)

            current = board.get_selection()
            return {
                "selected_count": len(current),
                "items": [serialize_board_item(it) for it in current],
            }

        return await asyncio.to_thread(_run)

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def pcb_save_board(
        save_as_path: str | None = None,
        overwrite: bool = False,
        include_project: bool = True,
    ) -> dict[str, Any]:
        """Save the currently open PCB in KiCad 10 (or save to a new path via `save_as_path`)."""
        def _run() -> dict[str, Any]:
            get_config().ensure_writable("pcb_save_board")
            board = get_ipc_bridge().get_board()
            if save_as_path:
                board.save_as(
                    save_as_path,
                    overwrite=overwrite,
                    include_project=include_project,
                )
                return {"saved": True, "path": save_as_path}
            board.save()
            return {"saved": True, "board_name": board.name}

        return await asyncio.to_thread(_run)
