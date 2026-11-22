# KiCad 10 MCP Server (`kicad10-mcp`) Worklist

Project root: `/home/tims/kicad1-mcp`
Target stack: Python 3.11+ (`uv`), `mcp 2.3.0` (`MCPServer`), `kicad-python 0.8.0` (`kipy`), `kicad-cli 10.0.8`

---

## Completed

- [x] **Project scaffold & environment**
  - [x] `pyproject.toml` with `mcp>=2.0.0`, `kicad-python>=0.8.0`, `pydantic`, `pytest`, `pytest-asyncio`, `ruff`
  - [x] `.venv` created and synced via `uv` (`mcp==2.3.0`, `kicad-python==0.8.0`)
  - [x] `src/kicad_mcp/__init__.py`
- [x] **Configuration**
  - [x] `src/kicad_mcp/config.py` — `KiCadConfig`, `KICAD_READ_ONLY` guard, `kicad-cli` discovery, IPC socket/token config, KiCad 10 library directory & path variable (`${KICAD10_SYMBOL_DIR}`, `${KIPRJMOD}`, etc.) expansion
- [x] **Core Bridges**
  - [x] `src/kicad_mcp/bridges/__init__.py`
  - [x] `src/kicad_mcp/bridges/sexpr.py` — KiCad S-expression parser, AST helpers, canonical formatter, and atomic file writer with optional `.bak` backup
  - [x] `src/kicad_mcp/bridges/cli.py` — async `KiCadCLI` subprocess runner, writable HOME/XDG fallback, JSON report loader, `-D` variable & KiCad 10 `--variant` helpers
  - [x] `src/kicad_mcp/bridges/ipc.py` — live `KiCadIPCBridge` (`kipy`), atomic undo/redo `commit()` context manager, layer resolution, net lookup, and millimeter JSON serializers

### 1. Tool Modules (`src/kicad_mcp/tools/`)

- [x] `src/kicad_mcp/tools/common.py` — shared `ToolAnnotations` presets, existing-file validation, and multi-variant `${VARIANT}` checks
- [x] `src/kicad_mcp/tools/__init__.py` — central `register_all_tools(server)` wiring
- [x] `src/kicad_mcp/tools/project.py` — Project & environment tools
  - [x] `kicad_status` (CLI version, live IPC connectivity/version, open documents, read-only status, library paths)
  - [x] `list_projects` (discover `.kicad_pro` projects in a directory tree)
  - [x] `inspect_project` (parse `.kicad_pro` JSON, design rules, net classes, text vars, project files)
  - [x] `get_project_variants` (list KiCad 10 design variants from `.kicad_pro`)
- [x] `src/kicad_mcp/tools/ipc_inspect.py` — Live KiCad 10 IPC PCB read tools
  - [x] `pcb_get_board_info` (layers, copper count, title block, origins, item counts, net classes)
  - [x] `pcb_get_stackup` (KiCad 10 dielectric/copper/soldermask/silkscreen/finish/impedance stackup)
  - [x] `pcb_list_nets` (filter by netclass or name pattern)
  - [x] `pcb_list_footprints` (filter by reference regex, value, footprint ID, layer, DNP)
  - [x] `pcb_get_footprint_details` (fields, pads, nets, drills, 3D models, bounding box)
  - [x] `pcb_list_tracks` (tracks & arc tracks filtered by net/layer)
  - [x] `pcb_list_vias` (vias filtered by net/type)
  - [x] `pcb_list_zones` (copper pours & rule areas)
  - [x] `pcb_list_graphics` (board shapes, outlines on `Edge.Cuts`, and board text items)
  - [x] `pcb_get_connected_items` (KiCad 10.0.1+ copper connectivity query)
  - [x] `pcb_get_selection` (current PCB Editor selection)
  - [x] `pcb_list_groups` (KiCad 10 layout groups)
- [x] `src/kicad_mcp/tools/ipc_edit.py` — Live KiCad 10 IPC PCB write tools (commit-wrapped & read-only guarded)
  - [x] `pcb_move_footprint` (absolute or relative move, rotation, lock state)
  - [x] `pcb_flip_footprint` (KiCad 10.0.6+ `board.flip_items` left-right / top-bottom)
  - [x] `pcb_update_footprint_fields` (reference, value, datasheet, description, DNP, BOM/POS flags)
  - [x] `pcb_add_track` (route single or multi-segment polyline tracks on a copper layer)
  - [x] `pcb_add_via` (place through / blind-buried / micro vias)
  - [x] `pcb_add_zone` (create copper pour or rule area polygon, optional refill)
  - [x] `pcb_refill_zones` (trigger zone refill)
  - [x] `pcb_add_graphic` (segment, rectangle, circle, or text on any layer)
  - [x] `pcb_delete_items` (delete board items by KIID UUID)
  - [x] `pcb_set_selection` (highlight items in KiCad GUI by reference or UUID, or clear selection)
  - [x] `pcb_save_board` (save or `save_as`)
- [x] `src/kicad_mcp/tools/cli_verify.py` — Headless verification & visual exports (`kicad-cli` 10)
  - [x] `run_drc` (`pcb drc --format json` with schematic parity, refill-zones, severity filters, grouped summary)
  - [x] `run_erc` (`sch erc --format json` with grouped summary by sheet)
  - [x] `get_board_stats` (`pcb export stats --format json`)
  - [x] `render_pcb_3d` (`pcb render` PNG/JPEG with side, rotate, quality, perspective, floor, `--variant`)
  - [x] `export_schematic_visual` (`sch export pdf` / `svg` with `--variant`)
  - [x] `export_pcb_visual` (`pcb export pdf` / `svg` with layer list & `--variant`)
- [x] `src/kicad_mcp/tools/cli_fab.py` — Manufacturing outputs, variants & jobsets (`kicad-cli` 10)
  - [x] `export_gerbers` (`pcb export gerbers` with layers, board plot params, `--check-zones`, `--variant`)
  - [x] `export_drill` (`pcb export drill` Excellon/Gerber + map + report)
  - [x] `export_bom` (`sch export bom` with `--variant`, `--preset`, `--fields`, `--group-by`, parsed rows + file)
  - [x] `export_position_file` (`pcb export pos` CSV/ASCII with `--side`, `--units`, `--smd-only`, `--exclude-dnp`, `--variant`)
  - [x] `export_netlist` (`sch export netlist` in `kicadsexpr`/`kicadxml`/`spice` with `--variant`)
  - [x] `export_3d_model` (`pcb export step` / `glb` with `--variant`, `--no-dnp`, `--include-tracks`, `--board-only`)
  - [x] `export_odb_or_ipc2581` (`pcb export odb` / `ipc2581` with `--variant`)
  - [x] `run_jobset` (`jobset run` with `--stop-on-error` and optional `--output`)
- [x] `src/kicad_mcp/tools/schematic.py` — Direct S-expression `.kicad_sch` workflows (read-only guarded & `.bak` backup supported)
  - [x] `sch_inspect` (parse title block, paper, symbols, power symbols, labels, wires, junctions, no-connects, sub-sheets)
  - [x] `sch_get_symbol` (full properties, pin UUIDs, unit, DNP/BOM flags, cached lib_symbol pin table)
  - [x] `sch_update_symbol_properties` (edit/add Value, Footprint, Datasheet, MPN/custom fields, DNP/in_bom/on_board; syncs `instances` reference)
  - [x] `sch_add_wire` (add wire segments with fresh UUIDs)
  - [x] `sch_add_label` (add local, global, or hierarchical labels)
  - [x] `sch_add_junction_or_no_connect` (add junction or no-connect markers)
  - [x] `sch_add_text` (add schematic text annotations)
  - [x] `sch_place_symbol` (embed symbol definition from `.kicad_sym` into `lib_symbols` and place a valid KiCad 10 symbol instance)
  - [x] `sch_delete_items` (delete schematic items by UUID)
- [x] `src/kicad_mcp/tools/libraries.py` — Symbol & footprint library workflows
  - [x] `list_libraries` (parse global & project `sym-lib-table` / `fp-lib-table` + discover standard library dirs)
  - [x] `search_symbols` (search `.kicad_sym` files by name, description, keywords, datasheet)
  - [x] `inspect_symbol` (parse `.kicad_sym` properties, units, `extends` inheritance, full pin table)
  - [x] `search_footprints` (search `.pretty/*.kicad_mod` by name, description, tags)
  - [x] `inspect_footprint` (parse `.kicad_mod` attributes, pads, drills, courtyard bbox, 3D models)

### 2. Server Entry Point, Resources & Prompts

- [x] `src/kicad_mcp/resources.py`
  - [x] Resource `kicad://status`
  - [x] Resource `kicad://board/summary`
  - [x] Prompt `pcb_design_review`
  - [x] Prompt `manufacturing_release_checklist`
- [x] `src/kicad_mcp/server.py`
  - [x] Create `MCPServer("kicad10-mcp", ...)` and register all tools, resources, and prompts
  - [x] CLI entry point (`kicad10-mcp`) supporting `--transport stdio|streamable-http`, `--host`, `--port`, `--read-only`, `--socket`, `--kicad-cli`

### 3. Tests, Lint & Documentation

- [x] `tests/test_sexpr.py` — tokenizer, parser, round-trip formatting, escaping, atomic file save + backup
- [x] `tests/test_schematic.py` — inspect, property edits, wire/label/junction/text additions, symbol placement from library, delete items, read-only guard
- [x] `tests/test_libraries.py` — lib-table parsing, symbol search/pin inspection (with `extends`), footprint search/pad/courtyard inspection
- [x] `tests/test_cli.py` — real integration tests against local `kicad-cli 10.0.8` (version, ERC, DRC, BOM, stats, Gerbers, drill, POS, netlist, SVG/PDF, 3D render, ODB++)
- [x] `tests/test_ipc.py` — mocked `kipy` board read/write + commit rollback + `KICAD_READ_ONLY` enforcement tests
- [x] `tests/test_server.py` — verify all 55 MCP tools, 2 resources, and 2 prompts register cleanly with valid JSON schemas
- [x] Run `pytest` and `ruff check` — all tests pass and zero lint errors
- [x] `README.md` — installation, enabling KiCad 10 IPC API, MCP client configs (Antigravity, Claude Desktop, Cursor), environment variables, and tool reference
