"""Direct S-expression `.kicad_sch` schematic inspection and editing tools."""

from __future__ import annotations

import itertools
import uuid
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from kicad_mcp.bridges.sexpr import (
    QuotedString,
    SExpr,
    Symbol,
    atom_float,
    load_sexpr_file,
    save_sexpr_file,
)
from kicad_mcp.config import get_config
from kicad_mcp.tools.common import (
    DESTRUCTIVE_ANNOTATIONS,
    READ_ONLY_ANNOTATIONS,
    WRITE_ANNOTATIONS,
    resolve_existing_file,
)
from kicad_mcp.tools.libraries import load_symbol_for_embedding, parse_symbol_details

_DELETABLE_TAGS = frozenset({
    "symbol",
    "wire",
    "bus",
    "bus_entry",
    "junction",
    "no_connect",
    "label",
    "global_label",
    "hierarchical_label",
    "text",
    "text_box",
    "polyline",
    "rectangle",
    "circle",
    "arc",
    "sheet",
})


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _get_at(node: SExpr) -> dict[str, float]:
    at = node.first("at")
    if at is None:
        return {"x_mm": 0.0, "y_mm": 0.0, "angle_deg": 0.0}
    x = atom_float(at.items[1]) if len(at.items) > 1 else 0.0
    y = atom_float(at.items[2]) if len(at.items) > 2 else 0.0
    ang = atom_float(at.items[3]) if len(at.items) > 3 else 0.0
    return {"x_mm": x, "y_mm": y, "angle_deg": ang}


def _get_props(node: SExpr) -> dict[str, str]:
    props: dict[str, str] = {}
    for prop in node.children("property"):
        k = prop.first_atom(1)
        v = prop.first_atom(2)
        if k is not None:
            props[k] = v if v is not None else ""
    return props


def _set_or_add_property(
    sym_node: SExpr,
    name: str,
    value: str,
    *,
    x_mm: float = 0.0,
    y_mm: float = 0.0,
    hidden: bool = True,
) -> None:
    for prop in sym_node.children("property"):
        if prop.first_atom(1) == name:
            if len(prop.items) >= 3:
                prop.items[2] = QuotedString(value)
            else:
                prop.items.append(QuotedString(value))
            return

    effects_children: list[Any] = [
        SExpr.node("font", SExpr.node("size", 1.27, 1.27)),
    ]
    if hidden:
        effects_children.append(SExpr.node("hide", Symbol("yes")))
    prop_node = SExpr.node(
        "property",
        name,
        value,
        SExpr.node("at", x_mm, y_mm, 0),
        SExpr.node("effects", *effects_children),
    )
    sym_node.insert_before_end(prop_node, before_tags={"pin", "instances"})


def _set_bool_child(node: SExpr, tag: str, value: bool) -> None:
    child = node.first(tag)
    sym_val = Symbol("yes" if value else "no")
    if child is not None:
        if len(child.items) >= 2:
            child.items[1] = sym_val
        else:
            child.items.append(sym_val)
    else:
        node.insert_before_end(
            SExpr.node(tag, sym_val),
            before_tags={"uuid", "property", "pin", "instances"},
        )


def _find_sch_symbol(root: SExpr, reference_or_uuid: str) -> SExpr:
    for sym in root.children("symbol"):
        uid = sym.child_atom("uuid") or ""
        props = _get_props(sym)
        if uid == reference_or_uuid or props.get("Reference") == reference_or_uuid:
            return sym
    raise ValueError(f"Symbol {reference_or_uuid!r} not found in schematic.")


def _serialize_sch_symbol(sym: SExpr, *, include_pins: bool = False) -> dict[str, Any]:
    props = _get_props(sym)
    ref = props.get("Reference", "")
    lib_id = sym.child_atom("lib_id") or ""
    unit_raw = sym.child_atom("unit", default="1") or "1"
    data: dict[str, Any] = {
        "uuid": sym.child_atom("uuid") or "",
        "lib_id": lib_id,
        "reference": ref,
        "value": props.get("Value", ""),
        "footprint": props.get("Footprint", ""),
        "datasheet": props.get("Datasheet", ""),
        "is_power": ref.startswith("#"),
        "unit": int(unit_raw) if unit_raw.isdigit() else 1,
        "position": _get_at(sym),
        "mirror": sym.child_atom("mirror"),
        "dnp": sym.child_atom("dnp", default="no") == "yes",
        "in_bom": sym.child_atom("in_bom", default="yes") != "no",
        "on_board": sym.child_atom("on_board", default="yes") != "no",
        "exclude_from_sim": sym.child_atom("exclude_from_sim", default="no") == "yes",
    }
    if include_pins:
        data["properties"] = props
        data["pins"] = [
            {
                "number": p.first_atom(1) or "",
                "uuid": p.child_atom("uuid") or "",
            }
            for p in sym.children("pin")
        ]
        instances_out: list[dict[str, Any]] = []
        inst = sym.first("instances")
        if inst:
            for proj in inst.children("project"):
                proj_name = proj.first_atom(1) or ""
                for path_node in proj.children("path"):
                    instances_out.append({
                        "project": proj_name,
                        "path": path_node.first_atom(1) or "",
                        "reference": path_node.child_atom("reference") or "",
                        "unit": path_node.child_atom("unit") or "",
                    })
        data["instances"] = instances_out
    return data


def _update_instances_reference(sym: SExpr, new_ref: str) -> None:
    inst = sym.first("instances")
    if inst is None:
        return
    for proj in inst.children("project"):
        for path_node in proj.children("path"):
            ref_node = path_node.first("reference")
            if ref_node is not None and len(ref_node.items) >= 2:
                ref_node.items[1] = QuotedString(new_ref)


def _make_property_node(
    name: str,
    value: str,
    x_mm: float,
    y_mm: float,
    *,
    hidden: bool = False,
) -> SExpr:
    effects_children: list[Any] = [SExpr.node("font", SExpr.node("size", 1.27, 1.27))]
    if hidden:
        effects_children.append(SExpr.node("hide", Symbol("yes")))
    return SExpr.node(
        "property",
        name,
        value,
        SExpr.node("at", x_mm, y_mm, 0),
        SExpr.node("effects", *effects_children),
    )


def register_schematic_tools(server: MCPServer) -> None:
    """Register direct S-expression `.kicad_sch` read & write tools."""

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def sch_inspect(schematic_path: str) -> dict[str, Any]:
        """Parse and summarize a KiCad 10 `.kicad_sch` schematic file: title block, paper,
        symbols, power symbols, labels, wires, junctions, no-connects, and sub-sheets.
        """
        sch = resolve_existing_file(schematic_path, "Schematic file")
        root = load_sexpr_file(sch)

        tb = root.first("title_block")
        title_block: dict[str, Any] = {}
        if tb:
            for field_name in ("title", "date", "rev", "company"):
                val = tb.child_atom(field_name)
                if val is not None:
                    title_block[field_name] = val
            comments = {}
            for cm in tb.children("comment"):
                num = cm.first_atom(1)
                txt = cm.first_atom(2)
                if num is not None and txt is not None:
                    comments[num] = txt
            if comments:
                title_block["comments"] = comments

        symbols: list[dict[str, Any]] = []
        power_symbols: list[dict[str, Any]] = []
        for sym in root.children("symbol"):
            info = _serialize_sch_symbol(sym, include_pins=False)
            if info["is_power"]:
                power_symbols.append(info)
            else:
                symbols.append(info)

        labels: list[dict[str, Any]] = []
        for tag, kind in (
            ("label", "local"),
            ("global_label", "global"),
            ("hierarchical_label", "hierarchical"),
        ):
            for node in root.children(tag):
                entry: dict[str, Any] = {
                    "kind": kind,
                    "text": node.first_atom(1) or "",
                    "position": _get_at(node),
                    "uuid": node.child_atom("uuid") or "",
                }
                shape = node.child_atom("shape")
                if shape:
                    entry["shape"] = shape
                labels.append(entry)

        wires: list[dict[str, Any]] = []
        for w in root.children("wire"):
            pts_node = w.first("pts")
            pts = []
            if pts_node:
                for xy in pts_node.children("xy"):
                    if len(xy.items) >= 3:
                        pts.append([atom_float(xy.items[1]), atom_float(xy.items[2])])
            wires.append({"uuid": w.child_atom("uuid") or "", "points_mm": pts})

        junctions = [
            {"uuid": j.child_atom("uuid") or "", "position": _get_at(j)}
            for j in root.children("junction")
        ]
        no_connects = [
            {"uuid": nc.child_atom("uuid") or "", "position": _get_at(nc)}
            for nc in root.children("no_connect")
        ]

        sheets: list[dict[str, Any]] = []
        for sh in root.children("sheet"):
            sprops = _get_props(sh)
            pins = [
                {
                    "name": p.first_atom(1) or "",
                    "shape": p.first_atom(2) or "",
                    "uuid": p.child_atom("uuid") or "",
                }
                for p in sh.children("pin")
            ]
            sheets.append({
                "uuid": sh.child_atom("uuid") or "",
                "name": sprops.get("Sheetname", ""),
                "file": sprops.get("Sheetfile", ""),
                "position": _get_at(sh),
                "pins": pins,
            })

        return {
            "schematic_file": str(sch),
            "version": root.child_atom("version") or "",
            "generator": root.child_atom("generator") or "",
            "uuid": root.child_atom("uuid") or "",
            "paper": root.child_atom("paper") or "",
            "title_block": title_block,
            "counts": {
                "symbols": len(symbols),
                "power_symbols": len(power_symbols),
                "labels": len(labels),
                "wires": len(wires),
                "junctions": len(junctions),
                "no_connects": len(no_connects),
                "sheets": len(sheets),
            },
            "symbols": symbols,
            "power_symbols": power_symbols,
            "labels": labels,
            "wires": wires,
            "junctions": junctions,
            "no_connects": no_connects,
            "sheets": sheets,
        }

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def sch_get_symbol(schematic_path: str, reference_or_uuid: str) -> dict[str, Any]:
        """Inspect a placed symbol in a `.kicad_sch` file by reference designator (e.g. `'R1'`)
        or UUID, including all properties, pin UUIDs, instances, and the cached `lib_symbols` pin table.
        """
        sch = resolve_existing_file(schematic_path, "Schematic file")
        root = load_sexpr_file(sch)
        sym = _find_sch_symbol(root, reference_or_uuid)
        data = _serialize_sch_symbol(sym, include_pins=True)

        lib_id = data["lib_id"]
        lib_symbols = root.first("lib_symbols")
        cached_pins: list[dict[str, Any]] = []
        if lib_symbols is not None:
            for lib_sym in lib_symbols.children("symbol"):
                if lib_sym.first_atom(1) == lib_id:
                    cached = parse_symbol_details(lib_sym)
                    cached_pins = cached["pins"]
                    break
        data["lib_symbol_pins"] = cached_pins
        return data

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def sch_update_symbol_properties(
        schematic_path: str,
        reference_or_uuid: str,
        *,
        reference: str | None = None,
        value: str | None = None,
        footprint: str | None = None,
        datasheet: str | None = None,
        description: str | None = None,
        custom_fields: dict[str, str] | None = None,
        dnp: bool | None = None,
        in_bom: bool | None = None,
        on_board: bool | None = None,
        exclude_from_sim: bool | None = None,
        create_backup: bool = True,
    ) -> dict[str, Any]:
        """Update properties, custom fields (e.g. MPN, Manufacturer), and BOM/DNP flags on a
        schematic symbol, keeping KiCad 10 `instances` reference paths synchronized.
        """
        get_config().ensure_writable("sch_update_symbol_properties")
        sch = resolve_existing_file(schematic_path, "Schematic file")
        root = load_sexpr_file(sch)
        sym = _find_sch_symbol(root, reference_or_uuid)
        pos = _get_at(sym)

        if reference is not None:
            _set_or_add_property(
                sym, "Reference", reference, x_mm=pos["x_mm"], y_mm=pos["y_mm"], hidden=False
            )
            _update_instances_reference(sym, reference)
        if value is not None:
            _set_or_add_property(
                sym, "Value", value, x_mm=pos["x_mm"], y_mm=pos["y_mm"], hidden=False
            )
        if footprint is not None:
            _set_or_add_property(
                sym, "Footprint", footprint, x_mm=pos["x_mm"], y_mm=pos["y_mm"], hidden=True
            )
        if datasheet is not None:
            _set_or_add_property(
                sym, "Datasheet", datasheet, x_mm=pos["x_mm"], y_mm=pos["y_mm"], hidden=True
            )
        if description is not None:
            _set_or_add_property(
                sym, "Description", description, x_mm=pos["x_mm"], y_mm=pos["y_mm"], hidden=True
            )
        if custom_fields:
            for field_name, field_val in custom_fields.items():
                _set_or_add_property(
                    sym, field_name, field_val, x_mm=pos["x_mm"], y_mm=pos["y_mm"], hidden=True
                )

        if dnp is not None:
            _set_bool_child(sym, "dnp", dnp)
        if in_bom is not None:
            _set_bool_child(sym, "in_bom", in_bom)
        if on_board is not None:
            _set_bool_child(sym, "on_board", on_board)
        if exclude_from_sim is not None:
            _set_bool_child(sym, "exclude_from_sim", exclude_from_sim)

        save_sexpr_file(sch, root, create_backup=create_backup)
        return {"schematic_file": str(sch), "symbol": _serialize_sch_symbol(sym, include_pins=True)}

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def sch_add_wire(
        schematic_path: str,
        points_mm: list[list[float]],
        *,
        stroke_width: float = 0.0,
        stroke_type: Literal["default", "solid", "dash", "dot", "dash_dot"] = "default",
        create_backup: bool = True,
    ) -> dict[str, Any]:
        """Add one or more connected wire segments along a list of `[x_mm, y_mm]` coordinates."""
        get_config().ensure_writable("sch_add_wire")
        if len(points_mm) < 2:
            raise ValueError("points_mm must contain at least two [x_mm, y_mm] coordinates")
        sch = resolve_existing_file(schematic_path, "Schematic file")
        root = load_sexpr_file(sch)

        added: list[dict[str, Any]] = []
        for p1, p2 in itertools.pairwise(points_mm):
            if len(p1) != 2 or len(p2) != 2:
                raise ValueError("Each point must be [x_mm, y_mm]")
            uid = _new_uuid()
            wire_node = SExpr.node(
                "wire",
                SExpr.node(
                    "pts",
                    SExpr.node("xy", float(p1[0]), float(p1[1])),
                    SExpr.node("xy", float(p2[0]), float(p2[1])),
                ),
                SExpr.node(
                    "stroke",
                    SExpr.node("width", float(stroke_width)),
                    SExpr.node("type", Symbol(stroke_type)),
                ),
                SExpr.node("uuid", uid),
            )
            root.insert_before_end(wire_node, before_tags={"sheet_instances", "embedded_fonts"})
            added.append({"uuid": uid, "start_mm": p1, "end_mm": p2})

        save_sexpr_file(sch, root, create_backup=create_backup)
        return {"schematic_file": str(sch), "count": len(added), "wires": added}

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def sch_add_label(
        schematic_path: str,
        text: str,
        x_mm: float,
        y_mm: float,
        *,
        kind: Literal["local", "global", "hierarchical"] = "local",
        angle_deg: float = 0.0,
        shape: Literal["input", "output", "bidirectional", "tri_state", "passive"] = "input",
        create_backup: bool = True,
    ) -> dict[str, Any]:
        """Add a local, global, or hierarchical net label to a `.kicad_sch` schematic."""
        get_config().ensure_writable("sch_add_label")
        sch = resolve_existing_file(schematic_path, "Schematic file")
        root = load_sexpr_file(sch)

        uid = _new_uuid()
        tag = {
            "local": "label",
            "global": "global_label",
            "hierarchical": "hierarchical_label",
        }[kind]

        children: list[Any] = [text]
        if kind in {"global", "hierarchical"}:
            children.append(SExpr.node("shape", Symbol(shape)))
        children.extend([
            SExpr.node("at", float(x_mm), float(y_mm), float(angle_deg)),
            SExpr.node(
                "effects",
                SExpr.node("font", SExpr.node("size", 1.27, 1.27)),
                SExpr.node("justify", Symbol("left"), Symbol("bottom")),
            ),
            SExpr.node("uuid", uid),
        ])
        node = SExpr.node(tag, *children)
        root.insert_before_end(node, before_tags={"sheet_instances", "embedded_fonts"})
        save_sexpr_file(sch, root, create_backup=create_backup)

        return {
            "schematic_file": str(sch),
            "uuid": uid,
            "kind": kind,
            "text": text,
            "position": {"x_mm": x_mm, "y_mm": y_mm, "angle_deg": angle_deg},
        }

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def sch_add_junction_or_no_connect(
        schematic_path: str,
        kind: Literal["junction", "no_connect"],
        x_mm: float,
        y_mm: float,
        *,
        create_backup: bool = True,
    ) -> dict[str, Any]:
        """Add a wire `junction` dot or a `no_connect` (X) marker at `(x_mm, y_mm)`."""
        get_config().ensure_writable("sch_add_junction_or_no_connect")
        sch = resolve_existing_file(schematic_path, "Schematic file")
        root = load_sexpr_file(sch)

        uid = _new_uuid()
        if kind == "junction":
            node = SExpr.node(
                "junction",
                SExpr.node("at", float(x_mm), float(y_mm)),
                SExpr.node("diameter", 0),
                SExpr.node("color", 0, 0, 0, 0),
                SExpr.node("uuid", uid),
            )
        else:
            node = SExpr.node(
                "no_connect",
                SExpr.node("at", float(x_mm), float(y_mm)),
                SExpr.node("uuid", uid),
            )

        root.insert_before_end(node, before_tags={"sheet_instances", "embedded_fonts"})
        save_sexpr_file(sch, root, create_backup=create_backup)
        return {
            "schematic_file": str(sch),
            "uuid": uid,
            "kind": kind,
            "position": {"x_mm": x_mm, "y_mm": y_mm},
        }

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def sch_add_text(
        schematic_path: str,
        text: str,
        x_mm: float,
        y_mm: float,
        *,
        angle_deg: float = 0.0,
        size_mm: float = 1.27,
        bold: bool = False,
        create_backup: bool = True,
    ) -> dict[str, Any]:
        """Add a schematic text annotation at `(x_mm, y_mm)`."""
        get_config().ensure_writable("sch_add_text")
        sch = resolve_existing_file(schematic_path, "Schematic file")
        root = load_sexpr_file(sch)

        uid = _new_uuid()
        font_children: list[Any] = [SExpr.node("size", float(size_mm), float(size_mm))]
        if bold:
            font_children.append(SExpr.node("bold", Symbol("yes")))
        node = SExpr.node(
            "text",
            text,
            SExpr.node("exclude_from_sim", Symbol("no")),
            SExpr.node("at", float(x_mm), float(y_mm), float(angle_deg)),
            SExpr.node(
                "effects",
                SExpr.node("font", *font_children),
                SExpr.node("justify", Symbol("left")),
            ),
            SExpr.node("uuid", uid),
        )
        root.insert_before_end(node, before_tags={"sheet_instances", "embedded_fonts"})
        save_sexpr_file(sch, root, create_backup=create_backup)
        return {
            "schematic_file": str(sch),
            "uuid": uid,
            "text": text,
            "position": {"x_mm": x_mm, "y_mm": y_mm, "angle_deg": angle_deg},
        }

    @server.tool(annotations=WRITE_ANNOTATIONS)
    async def sch_place_symbol(
        schematic_path: str,
        lib_id: str,
        reference: str,
        x_mm: float,
        y_mm: float,
        *,
        value: str | None = None,
        footprint: str | None = None,
        datasheet: str | None = None,
        unit: int = 1,
        angle_deg: float = 0.0,
        mirror: Literal["x", "y"] | None = None,
        dnp: bool = False,
        in_bom: bool = True,
        on_board: bool = True,
        project_name: str | None = None,
        library_path: str | None = None,
        create_backup: bool = True,
    ) -> dict[str, Any]:
        """Embed a symbol definition from a KiCad `.kicad_sym` library into `lib_symbols`
        (if not already present) and place a valid KiCad 10 symbol instance on the schematic.

        Args:
            schematic_path: Path to the `.kicad_sch` file.
            lib_id: Qualified `'Library:Symbol'` (e.g. `'Device:R'`, `'Device:C'`).
            reference: Reference designator (e.g. `'R1'`, `'C1'`, `'U1'`).
            x_mm: Placement X coordinate in mm.
            y_mm: Placement Y coordinate in mm.
            value: Component value (defaults to the library symbol's Value property).
            footprint: Optional KiCad footprint assignment (e.g. `'Resistor_SMD:R_0603_1608Metric'`).
            datasheet: Optional datasheet URL/path.
            unit: Symbol unit number for multi-unit symbols (default 1).
            angle_deg: Rotation in degrees (0, 90, 180, 270).
            mirror: Optional mirror axis (`'x'` or `'y'`).
            dnp: Mark Do Not Populate.
            in_bom: Include in BOM (default True).
            on_board: Include on PCB (default True).
            project_name: Project name for KiCad 10 `instances` block (defaults to schematic stem).
            library_path: Optional explicit path to a `.kicad_sym` file.
            create_backup: Write `.kicad_sch.bak` before saving (default True).
        """
        get_config().ensure_writable("sch_place_symbol")
        sch = resolve_existing_file(schematic_path, "Schematic file")
        root = load_sexpr_file(sch)

        embed_node, details = load_symbol_for_embedding(
            lib_id, project_dir=sch.parent, library_path=library_path
        )
        qualified_id = details["lib_id"]

        # Ensure (lib_symbols ...) exists and contains the symbol definition
        lib_symbols = root.first("lib_symbols")
        if lib_symbols is None:
            lib_symbols = SExpr.node("lib_symbols")
            root.insert_before_end(
                lib_symbols, before_tags={"symbol", "wire", "junction", "label", "sheet_instances"}
            )
        already_embedded = any(
            s.first_atom(1) == qualified_id for s in lib_symbols.children("symbol")
        )
        if not already_embedded:
            lib_symbols.append(embed_node)

        lib_props = details["properties"]
        final_value = value if value is not None else lib_props.get("Value", details["name"])
        final_fp = footprint if footprint is not None else lib_props.get("Footprint", "")
        final_ds = datasheet if datasheet is not None else lib_props.get("Datasheet", "~")
        final_desc = lib_props.get("Description", lib_props.get("ki_description", ""))

        sym_uuid = _new_uuid()
        sheet_uuid = root.child_atom("uuid") or ""
        proj = project_name or sch.stem

        sym_children: list[Any] = [
            SExpr.node("lib_id", qualified_id),
            SExpr.node("at", float(x_mm), float(y_mm), float(angle_deg)),
        ]
        if mirror is not None:
            sym_children.append(SExpr.node("mirror", Symbol(mirror)))
        sym_children.extend([
            SExpr.node("unit", int(unit)),
            SExpr.node("exclude_from_sim", Symbol("no")),
            SExpr.node("in_bom", Symbol("yes" if in_bom else "no")),
            SExpr.node("on_board", Symbol("yes" if on_board else "no")),
            SExpr.node("dnp", Symbol("yes" if dnp else "no")),
            SExpr.node("fields_autoplaced", Symbol("yes")),
            SExpr.node("uuid", sym_uuid),
            _make_property_node("Reference", reference, float(x_mm) + 2.54, float(y_mm) - 1.27, hidden=False),
            _make_property_node("Value", final_value, float(x_mm) + 2.54, float(y_mm) + 1.27, hidden=False),
            _make_property_node("Footprint", final_fp, float(x_mm), float(y_mm), hidden=True),
            _make_property_node("Datasheet", final_ds, float(x_mm), float(y_mm), hidden=True),
            _make_property_node("Description", final_desc, float(x_mm), float(y_mm), hidden=True),
        ])

        # Add pin UUID entries for pins belonging to this unit (or shared unit 0)
        seen_pin_nums: set[str] = set()
        for pin in details["pins"]:
            p_unit = pin.get("unit", 0)
            p_num = pin.get("number", "")
            if p_unit in {0, unit} and p_num and p_num not in seen_pin_nums:
                seen_pin_nums.add(p_num)
                sym_children.append(SExpr.node("pin", p_num, SExpr.node("uuid", _new_uuid())))

        instance_path = f"/{sheet_uuid}" if sheet_uuid else "/"
        sym_children.append(
            SExpr.node(
                "instances",
                SExpr.node(
                    "project",
                    proj,
                    SExpr.node(
                        "path",
                        instance_path,
                        SExpr.node("reference", reference),
                        SExpr.node("unit", int(unit)),
                    ),
                ),
            )
        )

        placed = SExpr.node("symbol", *sym_children)
        root.insert_before_end(placed, before_tags={"sheet_instances", "embedded_fonts"})
        save_sexpr_file(sch, root, create_backup=create_backup)

        return {
            "schematic_file": str(sch),
            "embedded_lib_symbol": not already_embedded,
            "symbol": _serialize_sch_symbol(placed, include_pins=True),
        }

    @server.tool(annotations=DESTRUCTIVE_ANNOTATIONS)
    async def sch_delete_items(
        schematic_path: str,
        uuids: list[str],
        *,
        create_backup: bool = True,
    ) -> dict[str, Any]:
        """Delete one or more items (symbols, wires, labels, junctions, no-connects, text, sheets)
        from a `.kicad_sch` file by UUID.
        """
        get_config().ensure_writable("sch_delete_items")
        if not uuids:
            raise ValueError("uuids must contain at least one UUID")
        sch = resolve_existing_file(schematic_path, "Schematic file")
        root = load_sexpr_file(sch)

        target_set = set(uuids)
        deleted: list[dict[str, str]] = []
        kept_items = [root.items[0]] if root.items else []
        for item in root.items[1:]:
            if isinstance(item, SExpr) and item.tag in _DELETABLE_TAGS:
                uid = item.child_atom("uuid") or ""
                if uid in target_set:
                    deleted.append({"tag": item.tag, "uuid": uid})
                    continue
            kept_items.append(item)

        root.items = kept_items
        if deleted:
            save_sexpr_file(sch, root, create_backup=create_backup)

        found_ids = {d["uuid"] for d in deleted}
        missing = [u for u in uuids if u not in found_ids]
        return {
            "schematic_file": str(sch),
            "deleted_count": len(deleted),
            "deleted": deleted,
            "not_found": missing,
        }
