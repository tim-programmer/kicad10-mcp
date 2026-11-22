"""KiCad 10 symbol (`.kicad_sym`) and footprint (`.pretty/*.kicad_mod`) library tools."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from kicad_mcp.bridges.sexpr import (
    QuotedString,
    SExpr,
    Symbol,
    atom_float,
    load_sexpr_file,
    parse_sexpr,
)
from kicad_mcp.config import get_config
from kicad_mcp.tools.common import READ_ONLY_ANNOTATIONS


def _parse_lib_table(table_path: Path, project_dir: Path | None) -> list[dict[str, Any]]:
    if not table_path.is_file():
        return []
    cfg = get_config()
    root = load_sexpr_file(table_path)
    entries: list[dict[str, Any]] = []
    for lib_node in root.children("lib"):
        name = lib_node.child_atom("name") or ""
        lib_type = lib_node.child_atom("type") or ""
        raw_uri = lib_node.child_atom("uri") or ""
        descr = lib_node.child_atom("descr") or ""
        disabled = lib_node.first("disabled") is not None
        expanded = cfg.expand_kicad_path(raw_uri, project_dir=project_dir) if raw_uri else None
        entries.append({
            "name": name,
            "type": lib_type,
            "uri": raw_uri,
            "resolved_path": str(expanded) if expanded else None,
            "exists": bool(expanded and expanded.exists()),
            "description": descr,
            "disabled": disabled,
        })
    return entries


def discover_symbol_libraries(project_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return mapping `lib_name -> info dict` from project table, global table, and standard dirs."""
    cfg = get_config()
    libs: dict[str, dict[str, Any]] = {}

    # 1. Standard directories (lowest precedence)
    for lib_dir in cfg.symbol_lib_dirs:
        if not lib_dir.is_dir():
            continue
        for sym_file in sorted(lib_dir.glob("*.kicad_sym")):
            name = sym_file.stem
            libs.setdefault(
                name,
                {
                    "name": name,
                    "source": "standard_dir",
                    "resolved_path": str(sym_file),
                    "exists": True,
                    "description": "",
                    "disabled": False,
                },
            )

    # 2. Global sym-lib-table
    for entry in _parse_lib_table(cfg.config_dir / "sym-lib-table", project_dir):
        if entry["name"] and not entry["disabled"]:
            libs[entry["name"]] = {"source": "global_table", **entry}

    # 3. Project sym-lib-table (highest precedence)
    if project_dir is not None:
        for entry in _parse_lib_table(project_dir / "sym-lib-table", project_dir):
            if entry["name"] and not entry["disabled"]:
                libs[entry["name"]] = {"source": "project_table", **entry}

    return libs


def discover_footprint_libraries(project_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return mapping `lib_name -> info dict` from project table, global table, and standard dirs."""
    cfg = get_config()
    libs: dict[str, dict[str, Any]] = {}

    for lib_dir in cfg.footprint_lib_dirs:
        if not lib_dir.is_dir():
            continue
        for pretty in sorted(lib_dir.glob("*.pretty")):
            if not pretty.is_dir():
                continue
            name = pretty.stem
            libs.setdefault(
                name,
                {
                    "name": name,
                    "source": "standard_dir",
                    "resolved_path": str(pretty),
                    "exists": True,
                    "description": "",
                    "disabled": False,
                },
            )

    for entry in _parse_lib_table(cfg.config_dir / "fp-lib-table", project_dir):
        if entry["name"] and not entry["disabled"]:
            libs[entry["name"]] = {"source": "global_table", **entry}

    if project_dir is not None:
        for entry in _parse_lib_table(project_dir / "fp-lib-table", project_dir):
            if entry["name"] and not entry["disabled"]:
                libs[entry["name"]] = {"source": "project_table", **entry}

    return libs


# Fast top-level symbol header scanner for `.kicad_sym` files
_TOP_SYM_RE = re.compile(r'^\t\(symbol\s+"((?:[^"\\]|\\.)*)"', re.MULTILINE)
_PROP_RE = re.compile(
    r'\(property\s+"(Description|ki_description|ki_keywords|Datasheet|Footprint|Reference|Value)"\s+"((?:[^"\\]|\\.)*)"'
)


def _unescape(s: str) -> str:
    return (
        s.replace('\\"', '"')
        .replace("\\\\", "\\")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
    )


def _scan_sym_file_summaries(sym_file: Path, lib_name: str) -> list[dict[str, str]]:
    """Fast scan of top-level symbols in a `.kicad_sym` file without full AST parse."""
    text = sym_file.read_text(encoding="utf-8", errors="replace")
    matches = list(_TOP_SYM_RE.finditer(text))
    results: list[dict[str, str]] = []
    for idx, m in enumerate(matches):
        sym_name = _unescape(m.group(1))
        start = m.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        # Only inspect the header chunk (properties appear near the top of each symbol)
        chunk = text[start : min(end, start + 4096)]
        props: dict[str, str] = {}
        for pm in _PROP_RE.finditer(chunk):
            key = pm.group(1)
            if key not in props:
                props[key] = _unescape(pm.group(2))
        descr = props.get("Description") or props.get("ki_description") or ""
        results.append({
            "library": lib_name,
            "name": sym_name,
            "lib_id": f"{lib_name}:{sym_name}",
            "description": descr,
            "keywords": props.get("ki_keywords", ""),
            "datasheet": props.get("Datasheet", ""),
            "footprint": props.get("Footprint", ""),
            "library_path": str(sym_file),
        })
    return results


def _extract_single_symbol_node(sym_file: Path, symbol_name: str) -> SExpr | None:
    """Extract and parse a single top-level `(symbol "<name>" ...)` node from a `.kicad_sym` file."""
    text = sym_file.read_text(encoding="utf-8", errors="replace")
    matches = list(_TOP_SYM_RE.finditer(text))
    for idx, m in enumerate(matches):
        if _unescape(m.group(1)) == symbol_name:
            start = m.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            block = text[start:end].rstrip()
            # If last symbol in file, trim the root closing ')'
            if idx + 1 == len(matches) and block.endswith(")"):
                # Balance parens cleanly by parsing prefix until depth returns to 0
                depth = 0
                in_str = False
                esc = False
                cut = len(block)
                for pos, ch in enumerate(block):
                    if in_str:
                        if esc:
                            esc = False
                        elif ch == "\\":
                            esc = True
                        elif ch == '"':
                            in_str = False
                    else:
                        if ch == '"':
                            in_str = True
                        elif ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                            if depth == 0:
                                cut = pos + 1
                                break
                block = block[:cut]
            return parse_sexpr(block)
    # Fallback: full parse in case formatting uses spaces instead of tabs
    root = load_sexpr_file(sym_file)
    for sym in root.children("symbol"):
        if sym.first_atom(1) == symbol_name:
            return sym
    return None


def _resolve_symbol_with_extends(
    sym_file: Path,
    symbol_name: str,
    _seen: set[str] | None = None,
) -> tuple[SExpr, list[str]]:
    """Return `(flattened_symbol_sexpr, inheritance_chain)` resolving `(extends ...)`."""
    seen = _seen if _seen is not None else set()
    if symbol_name in seen:
        raise ValueError(f"Circular symbol inheritance detected at {symbol_name!r}")
    seen.add(symbol_name)

    node = _extract_single_symbol_node(sym_file, symbol_name)
    if node is None:
        raise FileNotFoundError(f"Symbol {symbol_name!r} not found in {sym_file}")

    parent_name = node.child_atom("extends")
    if not parent_name:
        return node, [symbol_name]

    parent_node, chain = _resolve_symbol_with_extends(sym_file, parent_name, seen)
    merged = copy.deepcopy(parent_node)
    # Set merged symbol name to child symbol name
    merged.items[1] = QuotedString(symbol_name)

    # Rename parent sub-unit symbols `<parent>_u_s` -> `<child>_u_s`
    prefix = f"{parent_name}_"
    for sub in merged.children("symbol"):
        sub_name = sub.first_atom(1) or ""
        if sub_name.startswith(prefix):
            sub.items[1] = QuotedString(symbol_name + sub_name[len(parent_name) :])

    # Override/add properties from child
    child_props = {
        p.first_atom(1): p for p in node.children("property") if p.first_atom(1) is not None
    }
    new_items = []
    seen_props: set[str] = set()
    for item in merged.items:
        if isinstance(item, SExpr) and item.tag == "property":
            pname = item.first_atom(1)
            if pname in child_props:
                new_items.append(copy.deepcopy(child_props[pname]))
                seen_props.add(pname)
                continue
        new_items.append(item)
    merged.items = new_items
    for pname, pnode in child_props.items():
        if pname not in seen_props:
            merged.insert_before_end(copy.deepcopy(pnode), before_tags={"symbol"})

    # Copy any extra flags/sub-symbols from child (excluding extends & properties)
    for child in node.children():
        if child.tag in {"extends", "property"}:
            continue
        if child.tag == "symbol":
            merged.append(copy.deepcopy(child))
        else:
            merged.remove_children(child.tag)
            merged.insert_before_end(copy.deepcopy(child), before_tags={"property", "symbol"})

    return merged, [*chain, symbol_name]


def parse_symbol_details(sym_node: SExpr, *, library: str = "", library_path: str = "") -> dict[str, Any]:
    """Parse a KiCad 10 symbol `SExpr` node into a structured inspection dictionary."""
    sym_name = sym_node.first_atom(1) or ""
    props: dict[str, str] = {}
    for prop in sym_node.children("property"):
        key = prop.first_atom(1)
        val = prop.first_atom(2)
        if key is not None:
            props[key] = val if val is not None else ""

    pins: list[dict[str, Any]] = []
    units: set[int] = set()
    for sub in sym_node.children("symbol"):
        sub_name = sub.first_atom(1) or ""
        parts = sub_name.rsplit("_", 2)
        unit_num = int(parts[1]) if len(parts) == 3 and parts[1].isdigit() else 0
        if unit_num > 0:
            units.add(unit_num)
        for pin in sub.children("pin"):
            elec_type = pin.first_atom(1) or ""
            graphic_style = pin.first_atom(2) or ""
            at = pin.first("at")
            px = atom_float(at.items[1]) if at and len(at.items) > 1 else 0.0
            py = atom_float(at.items[2]) if at and len(at.items) > 2 else 0.0
            rot = atom_float(at.items[3]) if at and len(at.items) > 3 else 0.0
            length_node = pin.first("length")
            length = atom_float(length_node.items[1]) if length_node and len(length_node.items) > 1 else 0.0
            pin_name = pin.child_atom("name") or ""
            pin_number = pin.child_atom("number") or ""
            pins.append({
                "number": pin_number,
                "name": pin_name,
                "electrical_type": elec_type,
                "style": graphic_style,
                "unit": unit_num,
                "x_mm": px,
                "y_mm": py,
                "angle_deg": rot,
                "length_mm": length,
            })

    return {
        "name": sym_name,
        "library": library,
        "lib_id": f"{library}:{sym_name}" if library else sym_name,
        "library_path": library_path,
        "in_bom": sym_node.child_atom("in_bom", default="yes") != "no",
        "on_board": sym_node.child_atom("on_board", default="yes") != "no",
        "exclude_from_sim": sym_node.child_atom("exclude_from_sim", default="no") == "yes",
        "is_power": sym_node.first("power") is not None,
        "unit_count": max(units) if units else 1,
        "properties": props,
        "pin_count": len(pins),
        "pins": pins,
    }


def load_symbol_for_embedding(
    lib_id: str,
    *,
    project_dir: Path | None = None,
    library_path: str | None = None,
) -> tuple[SExpr, dict[str, Any]]:
    """Resolve a symbol (`'Lib:Sym'`) and return an embeddable `SExpr` + parsed metadata."""
    if ":" in lib_id:
        lib_name, sym_name = lib_id.split(":", 1)
    else:
        lib_name, sym_name = "", lib_id

    if library_path:
        sym_file = Path(library_path).expanduser().resolve()
        if not sym_file.is_file():
            raise FileNotFoundError(f"Symbol library file not found: {sym_file}")
        if not lib_name:
            lib_name = sym_file.stem
    else:
        if not lib_name:
            raise ValueError(
                "Provide a qualified lib_id ('Library:Symbol', e.g. 'Device:R') or explicit library_path."
            )
        libs = discover_symbol_libraries(project_dir)
        entry = libs.get(lib_name)
        if not entry or not entry.get("resolved_path") or not Path(entry["resolved_path"]).is_file():
            raise FileNotFoundError(
                f"Symbol library {lib_name!r} not found in sym-lib-table or standard KiCad library directories."
            )
        sym_file = Path(entry["resolved_path"])

    resolved_node, chain = _resolve_symbol_with_extends(sym_file, sym_name)
    details = parse_symbol_details(resolved_node, library=lib_name, library_path=str(sym_file))
    details["extends_chain"] = chain

    embed_node = copy.deepcopy(resolved_node)
    embed_node.items[1] = QuotedString(f"{lib_name}:{sym_name}")
    # Ensure `extends` is removed since it's flattened
    embed_node.remove_children("extends")
    return embed_node, details


def _parse_footprint_file(mod_path: Path, library: str = "") -> dict[str, Any]:
    root = load_sexpr_file(mod_path)
    name = root.first_atom(1) or mod_path.stem
    layer = root.child_atom("layer") or "F.Cu"
    descr = root.child_atom("descr") or ""
    tags = root.child_atom("tags") or ""

    attrs: list[str] = []
    attr_node = root.first("attr")
    if attr_node:
        for item in attr_node.args:
            if isinstance(item, (Symbol, QuotedString)):
                attrs.append(item.value)

    properties: dict[str, str] = {}
    for prop in root.children("property"):
        k = prop.first_atom(1)
        v = prop.first_atom(2)
        if k is not None:
            properties[k] = v if v is not None else ""

    pads: list[dict[str, Any]] = []
    for pad in root.children("pad"):
        num = pad.first_atom(1) or ""
        ptype = pad.first_atom(2) or ""
        shape = pad.first_atom(3) or ""
        at = pad.first("at")
        px = atom_float(at.items[1]) if at and len(at.items) > 1 else 0.0
        py = atom_float(at.items[2]) if at and len(at.items) > 2 else 0.0
        rot = atom_float(at.items[3]) if at and len(at.items) > 3 else 0.0
        size = pad.first("size")
        sw = atom_float(size.items[1]) if size and len(size.items) > 1 else 0.0
        sh = atom_float(size.items[2]) if size and len(size.items) > 2 else 0.0
        drill_node = pad.first("drill")
        drill: dict[str, Any] | None = None
        if drill_node and len(drill_node.items) > 1:
            if drill_node.first_atom(1) == "oval" and len(drill_node.items) >= 4:
                drill = {
                    "oval": True,
                    "x_mm": atom_float(drill_node.items[2]),
                    "y_mm": atom_float(drill_node.items[3]),
                }
            else:
                try:
                    d_val = atom_float(drill_node.items[1])
                    drill = {"oval": False, "diameter_mm": d_val}
                except (ValueError, TypeError):
                    drill = None
        layers_node = pad.first("layers")
        pad_layers = (
            [item.value for item in layers_node.args if isinstance(item, (Symbol, QuotedString))]
            if layers_node
            else []
        )
        pads.append({
            "number": num,
            "type": ptype,
            "shape": shape,
            "x_mm": px,
            "y_mm": py,
            "rotation_deg": rot,
            "size_mm": {"width": sw, "height": sh},
            "drill": drill,
            "layers": pad_layers,
        })

    # Courtyard bounding box from fp_line / fp_rect / fp_poly / fp_circle on *.CrtYd
    xs: list[float] = []
    ys: list[float] = []
    for tag in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for g in root.children(tag):
            glayer = g.child_atom("layer") or ""
            if "CrtYd" not in glayer:
                continue
            for pt_tag in ("start", "end", "mid", "center"):
                pt = g.first(pt_tag)
                if pt and len(pt.items) >= 3:
                    xs.append(atom_float(pt.items[1]))
                    ys.append(atom_float(pt.items[2]))
            pts = g.first("pts")
            if pts:
                for xy in pts.children("xy"):
                    if len(xy.items) >= 3:
                        xs.append(atom_float(xy.items[1]))
                        ys.append(atom_float(xy.items[2]))

    courtyard_bbox: dict[str, float] | None = None
    if xs and ys:
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        courtyard_bbox = {
            "min_x_mm": round(min_x, 4),
            "min_y_mm": round(min_y, 4),
            "max_x_mm": round(max_x, 4),
            "max_y_mm": round(max_y, 4),
            "width_mm": round(max_x - min_x, 4),
            "height_mm": round(max_y - min_y, 4),
        }

    models_3d = [
        {
            "path": m.first_atom(1) or "",
            "hide": m.first("hide") is not None,
        }
        for m in root.children("model")
    ]

    return {
        "name": name,
        "library": library,
        "lib_id": f"{library}:{name}" if library else name,
        "file_path": str(mod_path),
        "layer": layer,
        "description": descr,
        "tags": tags,
        "attributes": attrs,
        "properties": properties,
        "pad_count": len(pads),
        "pads": pads,
        "courtyard_bbox": courtyard_bbox,
        "models_3d": models_3d,
    }


def register_library_tools(server: MCPServer) -> None:
    """Register symbol & footprint library discovery, search, and inspection tools."""

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def list_libraries(
        kind: Literal["all", "symbol", "footprint"] = "all",
        project_dir: str | None = None,
    ) -> dict[str, Any]:
        """List available KiCad 10 symbol and footprint libraries from global/project
        `sym-lib-table` & `fp-lib-table` files and standard library directories.
        """
        proj = Path(project_dir).expanduser().resolve() if project_dir else None
        out: dict[str, Any] = {}
        if kind in {"all", "symbol"}:
            sym_libs = discover_symbol_libraries(proj)
            out["symbol_libraries"] = sorted(sym_libs.values(), key=lambda x: x["name"].lower())
            out["symbol_library_count"] = len(sym_libs)
        if kind in {"all", "footprint"}:
            fp_libs = discover_footprint_libraries(proj)
            out["footprint_libraries"] = sorted(fp_libs.values(), key=lambda x: x["name"].lower())
            out["footprint_library_count"] = len(fp_libs)
        return out

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def search_symbols(
        query: str,
        *,
        library: str | None = None,
        project_dir: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search KiCad `.kicad_sym` symbol libraries by symbol name, description, keywords, or datasheet.

        Args:
            query: Case-insensitive search query (space-separated tokens must all match).
            library: Optional library nickname to restrict search (e.g. `'Device'`, `'MCU_ST_STM32F4'`).
            project_dir: Optional project directory for project-local `sym-lib-table`.
            limit: Maximum number of results (default 50).
        """
        proj = Path(project_dir).expanduser().resolve() if project_dir else None
        libs = discover_symbol_libraries(proj)
        if library:
            entry = libs.get(library)
            if not entry:
                raise FileNotFoundError(f"Symbol library {library!r} not found.")
            target_libs = {library: entry}
        else:
            target_libs = libs

        tokens = [t.lower() for t in query.strip().split() if t]
        matches: list[dict[str, str]] = []

        for lib_name, info in sorted(target_libs.items(), key=lambda kv: kv[0].lower()):
            raw_path = info.get("resolved_path")
            if not raw_path:
                continue
            sym_file = Path(raw_path)
            if not sym_file.is_file():
                continue
            for item in _scan_sym_file_summaries(sym_file, lib_name):
                haystack = " ".join(
                    (
                        item["lib_id"],
                        item["description"],
                        item["keywords"],
                        item["datasheet"],
                    )
                ).lower()
                if all(tok in haystack for tok in tokens):
                    matches.append(item)
                    if len(matches) >= limit:
                        return {
                            "query": query,
                            "count": len(matches),
                            "truncated": True,
                            "symbols": matches,
                        }

        return {
            "query": query,
            "count": len(matches),
            "truncated": False,
            "symbols": matches,
        }

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def inspect_symbol(
        lib_id: str,
        *,
        library_path: str | None = None,
        project_dir: str | None = None,
    ) -> dict[str, Any]:
        """Inspect a KiCad 10 symbol definition (`.kicad_sym`), resolving `(extends ...)`
        inheritance and returning properties, unit count, and full pin table.

        Args:
            lib_id: Qualified `'Library:Symbol'` (e.g. `'Device:R'`), or bare `'Symbol'` when `library_path` is given.
            library_path: Optional explicit path to a `.kicad_sym` file.
            project_dir: Optional project directory for project-local `sym-lib-table`.
        """
        proj = Path(project_dir).expanduser().resolve() if project_dir else None
        _node, details = load_symbol_for_embedding(
            lib_id, project_dir=proj, library_path=library_path
        )
        return details

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def search_footprints(
        query: str,
        *,
        library: str | None = None,
        project_dir: str | None = None,
        search_metadata: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search `.pretty/*.kicad_mod` footprint libraries by name (and optionally descr/tags).

        Args:
            query: Case-insensitive space-separated search terms.
            library: Optional footprint library nickname (e.g. `'Resistor_SMD'`, `'Package_QFP'`).
            project_dir: Optional project directory for project-local `fp-lib-table`.
            search_metadata: If True, also scan `(descr ...)` and `(tags ...)` inside `.kicad_mod` files.
            limit: Maximum number of results (default 50).
        """
        proj = Path(project_dir).expanduser().resolve() if project_dir else None
        libs = discover_footprint_libraries(proj)
        if library:
            entry = libs.get(library)
            if not entry:
                raise FileNotFoundError(f"Footprint library {library!r} not found.")
            target_libs = {library: entry}
        else:
            target_libs = libs

        tokens = [t.lower() for t in query.strip().split() if t]
        matches: list[dict[str, str]] = []
        descr_rx = re.compile(r'\(descr\s+"((?:[^"\\]|\\.)*)"\)')
        tags_rx = re.compile(r'\(tags\s+"((?:[^"\\]|\\.)*)"\)')

        for lib_name, info in sorted(target_libs.items(), key=lambda kv: kv[0].lower()):
            raw_path = info.get("resolved_path")
            if not raw_path:
                continue
            pretty_dir = Path(raw_path)
            if not pretty_dir.is_dir():
                continue
            for mod_file in sorted(pretty_dir.glob("*.kicad_mod")):
                fp_name = mod_file.stem
                lib_id = f"{lib_name}:{fp_name}"
                descr = ""
                tags = ""
                matched = all(tok in lib_id.lower() for tok in tokens)
                if not matched and search_metadata:
                    head = mod_file.read_text(encoding="utf-8", errors="replace")[:2048]
                    dm = descr_rx.search(head)
                    tm = tags_rx.search(head)
                    descr = _unescape(dm.group(1)) if dm else ""
                    tags = _unescape(tm.group(1)) if tm else ""
                    hay = f"{lib_id} {descr} {tags}".lower()
                    matched = all(tok in hay for tok in tokens)
                if matched:
                    matches.append({
                        "library": lib_name,
                        "name": fp_name,
                        "lib_id": lib_id,
                        "file_path": str(mod_file),
                        "description": descr,
                        "tags": tags,
                    })
                    if len(matches) >= limit:
                        return {
                            "query": query,
                            "count": len(matches),
                            "truncated": True,
                            "footprints": matches,
                        }

        return {
            "query": query,
            "count": len(matches),
            "truncated": False,
            "footprints": matches,
        }

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def inspect_footprint(
        lib_id_or_path: str,
        *,
        project_dir: str | None = None,
    ) -> dict[str, Any]:
        """Inspect a KiCad 10 `.kicad_mod` footprint: attributes, pads, drills, courtyard bounding box, and 3D models.

        Args:
            lib_id_or_path: Either `'Library:Footprint'` (e.g. `'Resistor_SMD:R_0603_1608Metric'`)
                or a direct file path to a `.kicad_mod` file.
            project_dir: Optional project directory for project-local `fp-lib-table`.
        """
        direct = Path(lib_id_or_path).expanduser()
        if direct.is_file():
            lib_name = direct.parent.stem if direct.parent.suffix == ".pretty" else ""
            return _parse_footprint_file(direct.resolve(), library=lib_name)

        if ":" not in lib_id_or_path:
            raise FileNotFoundError(
                f"Footprint file not found: {lib_id_or_path!r}. "
                "Provide either a valid .kicad_mod path or 'Library:Footprint'."
            )

        lib_name, fp_name = lib_id_or_path.split(":", 1)
        proj = Path(project_dir).expanduser().resolve() if project_dir else None
        libs = discover_footprint_libraries(proj)
        entry = libs.get(lib_name)
        if not entry or not entry.get("resolved_path"):
            raise FileNotFoundError(f"Footprint library {lib_name!r} not found.")
        mod_file = Path(entry["resolved_path"]) / f"{fp_name}.kicad_mod"
        if not mod_file.is_file():
            raise FileNotFoundError(f"Footprint {fp_name!r} not found in {entry['resolved_path']}")
        return _parse_footprint_file(mod_file, library=lib_name)
