"""Live KiCad 10 IPC API PCB inspection (read-only) tools."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from kipy.board import BoardOriginType
from kipy.board_types import ArcTrack, BoardLayer, FootprintInstance, ViaType
from kipy.client import ApiError
from kipy.proto.common.types import KIID
from mcp.server.mcpserver import MCPServer

from kicad_mcp.bridges.ipc import (
    box_to_dict,
    get_ipc_bridge,
    layer_name,
    mm,
    resolve_layer,
    serialize_board_item,
    serialize_footprint,
    serialize_shape,
    serialize_stackup,
    serialize_track,
    serialize_via,
    serialize_zone,
    vec_to_dict,
)
from kicad_mcp.tools.common import READ_ONLY_ANNOTATIONS

logger = logging.getLogger(__name__)


def _serialize_netclass(nc: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"name": nc.name}
    for attr, key in (
        ("clearance", "clearance_mm"),
        ("track_width", "track_width_mm"),
        ("via_diameter", "via_diameter_mm"),
        ("via_drill", "via_drill_mm"),
        ("microvia_diameter", "microvia_diameter_mm"),
        ("microvia_drill", "microvia_drill_mm"),
        ("diff_pair_track_width", "diff_pair_track_width_mm"),
        ("diff_pair_gap", "diff_pair_gap_mm"),
    ):
        val = getattr(nc, attr, None)
        if val is not None:
            out[key] = mm(val)
    return out


def _board_info_sync() -> dict[str, Any]:
    board = get_ipc_bridge().get_board()
    tb = board.get_title_block_info()
    enabled_layers = [layer_name(ly) for ly in board.get_enabled_layers()]

    origins: dict[str, Any] = {}
    for label, ot in (("grid", BoardOriginType.BOT_GRID), ("drill", BoardOriginType.BOT_DRILL)):
        try:
            origins[label] = vec_to_dict(board.get_origin(ot))
        except (ApiError, RuntimeError, AttributeError):
            origins[label] = None

    net_classes: list[dict[str, Any]] = []
    try:
        proj = board.get_project()
        net_classes = [_serialize_netclass(nc) for nc in proj.get_net_classes()]
    except (ApiError, RuntimeError, AttributeError) as exc:
        logger.debug("Could not query project net classes: %s", exc)

    footprints = board.get_footprints()
    tracks = board.get_tracks()
    vias = board.get_vias()
    zones = board.get_zones()
    nets = board.get_nets()
    shapes = board.get_shapes()

    return {
        "board_name": board.name,
        "copper_layer_count": board.get_copper_layer_count(),
        "enabled_layers": enabled_layers,
        "title_block": {
            "title": tb.title,
            "revision": tb.revision,
            "date": tb.date,
            "company": tb.company,
            "comments": dict(tb.comments) if getattr(tb, "comments", None) else {},
        },
        "origins": origins,
        "counts": {
            "footprints": len(footprints),
            "nets": len(nets),
            "tracks": len(tracks),
            "vias": len(vias),
            "zones": len(zones),
            "shapes": len(shapes),
        },
        "net_classes": net_classes,
    }


async def pcb_get_board_info_impl() -> dict[str, Any]:
    return await asyncio.to_thread(_board_info_sync)


def _find_footprint_by_ref_or_id(board: Any, reference_or_id: str) -> FootprintInstance:
    footprints = board.get_footprints()
    for fp in footprints:
        ref = fp.reference_field.text.value if fp.reference_field else ""
        if ref == reference_or_id or fp.id.value == reference_or_id:
            return fp
    raise ValueError(f"Footprint {reference_or_id!r} not found on the open board.")


def register_ipc_inspect_tools(server: MCPServer) -> None:
    """Register live KiCad 10 IPC PCB read tools."""

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_get_board_info() -> dict[str, Any]:
        """Get high-level metadata for the currently open PCB in KiCad 10: layers,
        copper layer count, title block, grid/drill origins, item counts, and net classes.
        """
        return await pcb_get_board_info_impl()

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_get_stackup() -> dict[str, Any]:
        """Get the KiCad 10 physical board stackup (copper, dielectric layers with Er/tanD,
        soldermask, silkscreen, surface finish, and impedance control flag).
        """
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            return serialize_stackup(board.get_stackup())

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_list_nets(
        name_pattern: str | None = None,
        netclass: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """List nets on the open PCB, optionally filtered by regex pattern or netclass.

        Args:
            name_pattern: Optional case-insensitive regex to match net names.
            netclass: Optional exact netclass name filter (e.g. `'Power'`, `'Default'`).
            limit: Maximum number of nets to return (default 500).
        """
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            nets = board.get_nets()
            nc_map: dict[str, Any] = {}
            try:
                if nets:
                    nc_map = board.get_netclass_for_nets(nets)
            except (ApiError, RuntimeError, AttributeError):
                nc_map = {}

            rx = re.compile(name_pattern, re.IGNORECASE) if name_pattern else None
            results: list[dict[str, Any]] = []
            for net in nets:
                if rx and not rx.search(net.name):
                    continue
                nc_obj = nc_map.get(net.name)
                nc_name = nc_obj.name if nc_obj is not None else ""
                if netclass and nc_name != netclass:
                    continue
                results.append({
                    "code": net.code,
                    "name": net.name,
                    "netclass": nc_name,
                })

            return {
                "total_matched": len(results),
                "truncated": len(results) > limit,
                "nets": results[:limit],
            }

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_list_footprints(
        reference_regex: str | None = None,
        value_contains: str | None = None,
        footprint_contains: str | None = None,
        layer: str | None = None,
        dnp_only: bool | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """List footprints on the open PCB with optional filtering.

        Args:
            reference_regex: Regex matching reference designator (e.g. `'^R[0-9]+'`).
            value_contains: Case-insensitive substring filter on component value.
            footprint_contains: Case-insensitive substring filter on library footprint ID.
            layer: Layer filter (e.g. `'F.Cu'` or `'B.Cu'`).
            dnp_only: If True, only DNP parts; if False, only populated parts; if None, all.
            limit: Maximum footprints returned (default 500).
        """
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            rx = re.compile(reference_regex, re.IGNORECASE) if reference_regex else None
            target_layer = resolve_layer(board, layer) if layer else None
            val_sub = value_contains.lower() if value_contains else None
            fp_sub = footprint_contains.lower() if footprint_contains else None

            matched: list[dict[str, Any]] = []
            for fp in board.get_footprints():
                if target_layer is not None and fp.layer != target_layer:
                    continue
                serialized = serialize_footprint(fp, include_pads=False)
                if rx and not rx.search(serialized["reference"]):
                    continue
                if val_sub and val_sub not in serialized["value"].lower():
                    continue
                if fp_sub and fp_sub not in serialized["footprint_id"].lower():
                    continue
                if dnp_only is not None and serialized["dnp"] != dnp_only:
                    continue
                matched.append(serialized)

            matched.sort(key=lambda f: f["reference"])
            return {
                "total_matched": len(matched),
                "truncated": len(matched) > limit,
                "footprints": matched[:limit],
            }

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_get_footprint_details(reference_or_id: str) -> dict[str, Any]:
        """Get full details for a single PCB footprint by reference designator (e.g. `'U1'`)
        or KIID UUID, including fields, pads, nets, drills, 3D models, and bounding box.
        """
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            fp = _find_footprint_by_ref_or_id(board, reference_or_id)
            data = serialize_footprint(fp, include_pads=True)
            try:
                bbox = board.get_item_bounding_box(fp, include_text=False)
                if isinstance(bbox, list):
                    bbox = bbox[0] if bbox else None
                data["bounding_box"] = box_to_dict(bbox)
            except (ApiError, RuntimeError, AttributeError, IndexError):
                data["bounding_box"] = None
            return data

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_list_tracks(
        net: str | None = None,
        layer: str | None = None,
        arcs_only: bool = False,
        limit: int = 500,
    ) -> dict[str, Any]:
        """List copper tracks and arc tracks on the open PCB, filtered by net and/or layer.

        Args:
            net: Exact net name filter (e.g. `'GND'`, `'+3V3'`).
            layer: Copper layer filter (e.g. `'F.Cu'`, `'B.Cu'`, `'In1.Cu'`).
            arcs_only: Return only arc tracks when True.
            limit: Maximum number of track items returned (default 500).
        """
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            target_layer = resolve_layer(board, layer) if layer else None
            results: list[dict[str, Any]] = []
            total_length_mm = 0.0
            for trk in board.get_tracks():
                if arcs_only and not isinstance(trk, ArcTrack):
                    continue
                if target_layer is not None and trk.layer != target_layer:
                    continue
                net_name = trk.net.name if trk.net else ""
                if net is not None and net_name != net:
                    continue
                item = serialize_track(trk)
                total_length_mm += item["length_mm"]
                results.append(item)

            return {
                "total_matched": len(results),
                "total_length_mm": round(total_length_mm, 4),
                "truncated": len(results) > limit,
                "tracks": results[:limit],
            }

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_list_vias(
        net: str | None = None,
        via_type: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """List vias on the open PCB, optionally filtered by net or via type (`through`, `blind_buried`, `micro`)."""
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            type_filter = via_type.strip().upper() if via_type else None
            results: list[dict[str, Any]] = []
            for via in board.get_vias():
                net_name = via.net.name if via.net else ""
                if net is not None and net_name != net:
                    continue
                serialized = serialize_via(via)
                if type_filter:
                    vt_name = serialized["type"].upper()
                    if type_filter not in vt_name and f"VT_{type_filter}" != vt_name:
                        continue
                results.append(serialized)
            return {
                "total_matched": len(results),
                "truncated": len(results) > limit,
                "vias": results[:limit],
            }

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_list_zones(
        net: str | None = None,
        layer: str | None = None,
        rule_areas_only: bool | None = None,
    ) -> dict[str, Any]:
        """List copper pour zones and rule/keepout areas on the open PCB.

        Args:
            net: Optional exact net name filter.
            layer: Optional layer filter (e.g. `'F.Cu'`, `'In1.Cu'`).
            rule_areas_only: True for only rule areas, False for only copper pours, None for all.
        """
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            target_layer = resolve_layer(board, layer) if layer else None
            results: list[dict[str, Any]] = []
            for zone in board.get_zones():
                if rule_areas_only is not None and zone.is_rule_area() != rule_areas_only:
                    continue
                net_name = zone.net.name if zone.net else ""
                if net is not None and net_name != net:
                    continue
                if target_layer is not None and target_layer not in zone.layers:
                    continue
                results.append(serialize_zone(zone))
            return {"count": len(results), "zones": results}

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_list_graphics(
        layer: str | None = None,
        include_text: bool = True,
        limit: int = 500,
    ) -> dict[str, Any]:
        """List board graphic shapes (lines, rectangles, circles, arcs, polygons such as `Edge.Cuts`
        board outlines or silkscreen) and optionally board text items.

        Args:
            layer: Optional layer filter (e.g. `'Edge.Cuts'`, `'F.SilkS'`, `'F.Fab'`).
            include_text: Include `BoardText` / `BoardTextBox` items (default True).
            limit: Maximum number of items returned (default 500).
        """
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            target_layer = resolve_layer(board, layer) if layer else None
            items: list[dict[str, Any]] = []
            for shape in board.get_shapes():
                if target_layer is not None and shape.layer != target_layer:
                    continue
                items.append({"item_type": "shape", **serialize_shape(shape)})
            if include_text:
                for txt in board.get_text():
                    if target_layer is not None and txt.layer != target_layer:
                        continue
                    items.append(serialize_board_item(txt))
            return {
                "total_matched": len(items),
                "truncated": len(items) > limit,
                "items": items[:limit],
            }

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_get_connected_items(item_ids: list[str], limit: int = 500) -> dict[str, Any]:
        """Query copper connectivity (KiCad 10.0.1+) starting from one or more item KIID UUIDs.

        Args:
            item_ids: List of KIID UUID strings (pads, tracks, vias, or zones).
            limit: Maximum connected items returned (default 500).
        """
        if not item_ids:
            raise ValueError("item_ids must contain at least one item UUID")

        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            kiids = [KIID(value=uid) for uid in item_ids]
            connected = board.get_connected_items(kiids)
            serialized = [serialize_board_item(it) for it in connected]
            return {
                "seed_ids": item_ids,
                "total_connected": len(serialized),
                "truncated": len(serialized) > limit,
                "items": serialized[:limit],
            }

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_get_selection() -> dict[str, Any]:
        """Return items currently selected in the live KiCad 10 PCB Editor GUI."""
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            items = board.get_selection()
            return {
                "count": len(items),
                "items": [serialize_board_item(it) for it in items],
            }

        return await asyncio.to_thread(_run)

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def pcb_list_groups() -> dict[str, Any]:
        """List KiCad 10 layout groups on the open PCB."""
        def _run() -> dict[str, Any]:
            board = get_ipc_bridge().get_board()
            groups = board.get_groups()
            out = [
                {
                    "id": g.id.value,
                    "name": g.name,
                    "item_ids": [it.value for it in g.items],
                    "item_count": len(g.items),
                }
                for g in groups
            ]
            return {"count": len(out), "groups": out}

        return await asyncio.to_thread(_run)


# Keep unused enum imports referenced for clarity / type exports
_ = (BoardLayer, ViaType)
