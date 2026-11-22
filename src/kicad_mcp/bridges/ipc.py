"""Live KiCad 10 IPC API bridge built on official `kicad-python` (`kipy`)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from kipy import KiCad
from kipy.board import Board, BoardStackup
from kipy.board_types import (
    ArcTrack,
    BoardArc,
    BoardCircle,
    BoardItem,
    BoardLayer,
    BoardPolygon,
    BoardRectangle,
    BoardSegment,
    BoardShape,
    BoardText,
    BoardTextBox,
    FootprintInstance,
    Group,
    Net,
    Pad,
    PadType,
    Track,
    Via,
    ViaType,
    Zone,
    ZoneType,
)
from kipy.client import ApiError
from kipy.geometry import Box2, Vector2
from kipy.proto.common.types import DocumentType
from kipy.util.board_layer import CANONICAL_LAYER_NAMES, canonical_name, layer_from_canonical_name
from kipy.util.units import from_mm, to_mm

from kicad_mcp.config import KiCadConfig, get_config

logger = logging.getLogger(__name__)


class KiCadIPCError(RuntimeError):
    """Raised when connecting to or talking with a live KiCad 10 instance fails."""


def mm(value_nm: float) -> float:
    """Convert nanometers to rounded millimeters for clean JSON responses."""
    return round(to_mm(int(value_nm)), 6)


def nm(value_mm: float) -> int:
    """Convert millimeters to KiCad IPC nanometers."""
    return from_mm(value_mm)


def vec_to_dict(v: Vector2) -> dict[str, float]:
    return {"x_mm": mm(v.x), "y_mm": mm(v.y)}


def box_to_dict(b: Box2 | None) -> dict[str, float] | None:
    if b is None:
        return None
    return {
        "x_mm": mm(b.pos.x),
        "y_mm": mm(b.pos.y),
        "width_mm": mm(b.size.x),
        "height_mm": mm(b.size.y),
    }


def layer_name(layer: Any) -> str:
    name = canonical_name(layer)
    if name != "Unknown":
        return name
    try:
        return BoardLayer.Name(layer)
    except (ValueError, TypeError):
        return str(layer)


def resolve_layer(board: Board | None, name: str) -> Any:
    """Resolve a canonical or user-defined layer name to a `BoardLayer` enum value."""
    normalized = name.strip()
    if board is not None:
        try:
            resolved = board.get_layer_by_name(normalized)
            if resolved not in (BoardLayer.BL_UNKNOWN, BoardLayer.BL_UNDEFINED):
                return resolved
        except (ApiError, RuntimeError, ValueError) as exc:
            logger.debug("board.get_layer_by_name(%r) failed: %s", normalized, exc)

    resolved = layer_from_canonical_name(normalized)
    if resolved != BoardLayer.BL_UNKNOWN:
        return resolved

    # Case-insensitive fallback over canonical layer names
    lower = normalized.lower()
    for enum_val, cname in CANONICAL_LAYER_NAMES.items():
        if cname.lower() == lower:
            return enum_val

    sample = "F.Cu, B.Cu, In1.Cu, F.SilkS, B.SilkS, F.Fab, Edge.Cuts, F.CrtYd"
    raise ValueError(f"Unknown KiCad layer {name!r}. Examples of valid layers: {sample}")


class KiCadIPCBridge:
    """Manages connections and helper queries against a live KiCad 10 GUI session."""

    def __init__(self, config: KiCadConfig | None = None) -> None:
        self._config = config or get_config()
        self._kicad: KiCad | None = None

    def connect(self, *, force_reconnect: bool = False) -> KiCad:
        """Connect to the running KiCad 10 IPC API server."""
        if self._kicad is not None and not force_reconnect:
            try:
                self._kicad.ping()
                return self._kicad
            except (ApiError, RuntimeError, OSError):
                self._kicad = None

        try:
            kicad = KiCad(
                socket_path=self._config.socket_path,
                client_name="kicad10-mcp",
                kicad_token=self._config.api_token,
                timeout_ms=self._config.ipc_timeout_ms,
            )
            kicad.ping()
            self._kicad = kicad
            return kicad
        except Exception as exc:
            sock = self._config.socket_path or "default platform socket (e.g. ipc:///tmp/kicad/api.sock)"
            raise KiCadIPCError(
                f"Could not connect to KiCad 10 IPC API on {sock}: {exc}. "
                "Make sure KiCad 10 is running and the API is enabled under "
                "Preferences -> Plugins -> Enable KiCad API."
            ) from exc

    def get_board(self) -> Board:
        """Return the currently open PCB document in KiCad 10."""
        kicad = self.connect()
        try:
            return kicad.get_board()
        except ApiError as exc:
            raise KiCadIPCError(
                f"No PCB board is currently open in KiCad 10 ({exc}). "
                "Open a .kicad_pcb file in the KiCad PCB Editor and try again."
            ) from exc

    def list_open_documents(self) -> list[dict[str, str]]:
        """List all open documents across KiCad editors."""
        kicad = self.connect()
        docs: list[dict[str, str]] = []
        for doc_type, label in (
            (DocumentType.DOCTYPE_PCB, "pcb"),
            (DocumentType.DOCTYPE_SCHEMATIC, "schematic"),
            (DocumentType.DOCTYPE_SYMBOL, "symbol"),
            (DocumentType.DOCTYPE_FOOTPRINT, "footprint"),
            (DocumentType.DOCTYPE_PROJECT, "project"),
        ):
            try:
                for doc in kicad.get_open_documents(doc_type):
                    docs.append({
                        "type": label,
                        "project_name": doc.project.name,
                        "project_path": doc.project.path,
                        "board_filename": getattr(doc, "board_filename", ""),
                    })
            except (ApiError, RuntimeError, OSError) as exc:
                logger.debug("Skipping open document query for %s: %s", label, exc)
                continue
        return docs

    @contextmanager
    def commit(self, board: Board, message: str) -> Iterator[Any]:
        """Run board mutations inside a single atomic KiCad undo/redo commit."""
        self._config.ensure_writable(message)
        txn = board.begin_commit()
        try:
            yield txn
            board.push_commit(txn, message)
        except BaseException:
            try:
                board.drop_commit(txn)
            except (ApiError, RuntimeError, OSError) as exc:
                logger.debug("drop_commit failed during rollback: %s", exc)
            raise


# ---------------------------------------------------------------------------
# Serializers from kipy objects to clean, compact JSON dicts (units in mm)
# ---------------------------------------------------------------------------


def serialize_footprint(fp: FootprintInstance, *, include_pads: bool = False) -> dict[str, Any]:
    ref = fp.reference_field.text.value if fp.reference_field else ""
    val = fp.value_field.text.value if fp.value_field else ""
    lib_id = str(fp.definition.id) if fp.definition and fp.definition.id else ""
    attrs = fp.attributes
    data: dict[str, Any] = {
        "id": fp.id.value,
        "reference": ref,
        "value": val,
        "footprint_id": lib_id,
        "position": vec_to_dict(fp.position),
        "rotation_deg": round(fp.orientation.degrees, 4),
        "layer": layer_name(fp.layer),
        "locked": fp.locked,
        "dnp": bool(attrs.do_not_populate) if attrs else False,
        "exclude_from_bom": bool(attrs.exclude_from_bill_of_materials) if attrs else False,
        "exclude_from_pos": bool(attrs.exclude_from_position_files) if attrs else False,
        "pad_count": len(fp.definition.pads) if fp.definition else 0,
    }
    if include_pads and fp.definition:
        data["datasheet"] = fp.datasheet_field.text.value if fp.datasheet_field else ""
        data["description"] = fp.description_field.text.value if fp.description_field else ""
        data["pads"] = [serialize_pad(p) for p in fp.definition.pads]
        data["models_3d"] = [
            {"filename": m.filename, "visible": m.visible}
            for m in fp.definition.models
        ]
    return data


def serialize_pad(pad: Pad) -> dict[str, Any]:
    try:
        ptype = PadType.Name(pad.pad_type)
    except (ValueError, TypeError):
        ptype = str(pad.pad_type)
    drill = pad.padstack.drill.diameter
    return {
        "id": pad.id.value,
        "number": pad.number,
        "net": pad.net.name if pad.net else "",
        "type": ptype,
        "position": vec_to_dict(pad.position),
        "drill_mm": {"x_mm": mm(drill.x), "y_mm": mm(drill.y)},
        "layers": [layer_name(ly) for ly in pad.padstack.layers],
    }


def serialize_track(track: Track | ArcTrack) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": track.id.value,
        "kind": "arc" if isinstance(track, ArcTrack) else "track",
        "net": track.net.name if track.net else "",
        "layer": layer_name(track.layer),
        "start": vec_to_dict(track.start),
        "end": vec_to_dict(track.end),
        "width_mm": mm(track.width),
        "length_mm": mm(track.length()),
        "locked": track.locked,
    }
    if isinstance(track, ArcTrack):
        base["mid"] = vec_to_dict(track.mid)
    return base


def serialize_via(via: Via) -> dict[str, Any]:
    try:
        vtype = ViaType.Name(via.type)
    except (ValueError, TypeError):
        vtype = str(via.type)
    return {
        "id": via.id.value,
        "position": vec_to_dict(via.position),
        "net": via.net.name if via.net else "",
        "type": vtype,
        "diameter_mm": mm(via.diameter),
        "drill_mm": mm(via.drill_diameter),
        "start_layer": layer_name(via.padstack.drill.start_layer),
        "end_layer": layer_name(via.padstack.drill.end_layer),
        "locked": via.locked,
    }


def serialize_zone(zone: Zone) -> dict[str, Any]:
    try:
        ztype = ZoneType.Name(zone.type)
    except (ValueError, TypeError):
        ztype = str(zone.type)
    try:
        bbox = box_to_dict(zone.bounding_box())
    except (IndexError, ValueError):
        bbox = None
    info: dict[str, Any] = {
        "id": zone.id.value,
        "name": zone.name,
        "type": ztype,
        "is_rule_area": zone.is_rule_area(),
        "net": zone.net.name if zone.net else "",
        "layers": [layer_name(ly) for ly in zone.layers],
        "priority": zone.priority,
        "filled": zone.filled,
        "locked": zone.locked,
        "bounding_box": bbox,
    }
    if not zone.is_rule_area():
        if zone.clearance is not None:
            info["clearance_mm"] = mm(zone.clearance)
        if zone.min_thickness is not None:
            info["min_thickness_mm"] = mm(zone.min_thickness)
    return info


def serialize_shape(shape: BoardShape) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": shape.id.value,
        "kind": type(shape).__name__,
        "layer": layer_name(shape.layer),
        "locked": shape.locked,
        "stroke_width_mm": mm(shape.attributes.stroke.width),
        "filled": shape.attributes.fill.filled,
    }
    if isinstance(shape, BoardSegment):
        info["start"] = vec_to_dict(shape.start)
        info["end"] = vec_to_dict(shape.end)
    elif isinstance(shape, BoardRectangle):
        info["top_left"] = vec_to_dict(shape.top_left)
        info["bottom_right"] = vec_to_dict(shape.bottom_right)
    elif isinstance(shape, BoardCircle):
        info["center"] = vec_to_dict(shape.center)
        info["radius_mm"] = mm(shape.radius())
    elif isinstance(shape, BoardArc):
        info["start"] = vec_to_dict(shape.start)
        info["mid"] = vec_to_dict(shape.mid)
        info["end"] = vec_to_dict(shape.end)
    elif isinstance(shape, BoardPolygon):
        info["polygon_count"] = len(shape.polygons)
    return info


def serialize_board_item(item: Any) -> dict[str, Any]:
    if isinstance(item, FootprintInstance):
        return {"item_type": "footprint", **serialize_footprint(item)}
    if isinstance(item, Pad):
        return {"item_type": "pad", **serialize_pad(item)}
    if isinstance(item, (Track, ArcTrack)):
        return {"item_type": "track", **serialize_track(item)}
    if isinstance(item, Via):
        return {"item_type": "via", **serialize_via(item)}
    if isinstance(item, Zone):
        return {"item_type": "zone", **serialize_zone(item)}
    if isinstance(item, BoardShape):
        return {"item_type": "shape", **serialize_shape(item)}
    if isinstance(item, (BoardText, BoardTextBox)):
        return {
            "item_type": "text",
            "id": item.id.value,
            "layer": layer_name(item.layer),
            "value": item.value,
            "locked": item.locked,
        }
    if isinstance(item, Group):
        return {
            "item_type": "group",
            "id": item.id.value,
            "name": item.name,
            "item_count": len(item.items),
        }
    return {
        "item_type": type(item).__name__,
        "id": item.id.value if isinstance(item, BoardItem) else "",
        "repr": repr(item),
    }


def serialize_stackup(stackup: BoardStackup) -> dict[str, Any]:
    layers_out: list[dict[str, Any]] = []
    total_thickness_nm = 0
    for layer in stackup.layers:
        total_thickness_nm += layer.thickness
        entry: dict[str, Any] = {
            "layer": layer_name(layer.layer) if layer.layer else "dielectric",
            "user_name": layer.user_name,
            "enabled": layer.enabled,
            "thickness_mm": mm(layer.thickness),
            "material": layer.material_name,
        }
        if layer.dielectric is not None:
            entry["dielectric_sublayers"] = [
                {
                    "material": sub.material_name,
                    "thickness_mm": mm(sub.thickness),
                    "epsilon_r": sub.epsilon_r,
                    "loss_tangent": sub.loss_tangent,
                }
                for sub in layer.dielectric.layers
            ]
        elif layer.soldermask is not None:
            entry["soldermask"] = {
                "material": layer.soldermask.material_name,
                "thickness_mm": mm(layer.soldermask.thickness),
                "epsilon_r": layer.soldermask.epsilon_r,
            }
        elif layer.silkscreen is not None:
            entry["silkscreen"] = {"material": layer.silkscreen.material_name}
        layers_out.append(entry)

    result: dict[str, Any] = {
        "total_thickness_mm": mm(total_thickness_nm),
        "layer_count": len(layers_out),
        "layers": layers_out,
    }
    for attr_name in ("finish", "impedance", "edge_connector"):
        val = getattr(stackup, attr_name, None)
        if val is not None:
            if hasattr(val, "type_name"):
                result[attr_name] = val.type_name
            elif hasattr(val, "is_controlled"):
                result[attr_name] = val.is_controlled
    return result


def find_net_by_name(board: Board, net_name: str) -> Net:
    """Look up a `Net` on the board by exact name (or raise a helpful ValueError)."""
    nets = board.get_nets()
    for net in nets:
        if net.name == net_name:
            return net
    # Case-insensitive fallback suggestion
    lower = net_name.lower()
    suggestions = [n.name for n in nets if lower in n.name.lower()][:10]
    hint = f" Similar nets: {suggestions}" if suggestions else ""
    raise ValueError(f"Net {net_name!r} not found on board.{hint}")


_IPC_BRIDGE: KiCadIPCBridge | None = None


def get_ipc_bridge() -> KiCadIPCBridge:
    global _IPC_BRIDGE
    if _IPC_BRIDGE is None:
        _IPC_BRIDGE = KiCadIPCBridge()
    return _IPC_BRIDGE


def set_ipc_bridge(bridge: KiCadIPCBridge) -> None:
    global _IPC_BRIDGE
    _IPC_BRIDGE = bridge
