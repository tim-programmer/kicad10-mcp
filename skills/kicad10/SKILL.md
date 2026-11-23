---
name: kicad10
description: Work on KiCad 10 electronics projects through the kicad10-mcp server — inspect and edit live PCBs, run ERC/DRC, generate fabrication and assembly outputs (Gerbers, drill, BOM, pick-and-place, STEP, ODB++/IPC-2581), edit .kicad_sch schematics, and search symbol/footprint libraries. Use whenever the user mentions KiCad, a .kicad_pro/.kicad_pcb/.kicad_sch file, a PCB or schematic, DRC/ERC, Gerbers, a fab/manufacturing release, or finding a part symbol or footprint.
---

# KiCad 10 (kicad10-mcp)

This skill drives the `kicad10-mcp` MCP server. Tool names below are bare; in Claude Code they appear with the server prefix (e.g. `mcp__kicad10__run_drc`). If none of these tools are available, the server isn't connected — tell the user to add it (see the repo README) rather than shelling out to `kicad-cli` by hand.

## Start here

1. Call `kicad_status` first. It reports the `kicad-cli` version, whether the live IPC connection to the KiCad GUI is up, which documents are open, whether `KICAD_READ_ONLY` is set, and the library paths. Its answer decides which tool families you can use.
2. If you don't know where the project is, use `list_projects(directory)`, then `inspect_project(project_path)` for design rules, net classes and text variables, and `get_project_variants` for KiCad 10 design variants.

## Three tool families — pick by what you're touching

| Family | Tools | Needs | Operates on |
|---|---|---|---|
| **Headless (kicad-cli)** | `run_drc`, `run_erc`, `get_board_stats`, `export_*`, `render_pcb_3d`, `run_jobset` | `kicad-cli` only | Files **on disk** |
| **Live PCB (IPC)** | `pcb_*` | KiCad 10 GUI open, PCB Editor showing the board, *Preferences → Plugins → Enable KiCad API* | The board **in the editor's memory** |
| **Schematic / library (S-expression)** | `sch_*`, `search_*`, `inspect_symbol`, `inspect_footprint`, `list_libraries` | Nothing | Files **on disk** |

### Critical consistency rules

- **Live PCB edits aren't on disk until saved.** `pcb_*` changes live in the editor (each is an undoable commit). `run_drc`, `get_board_stats` and every export read the `.kicad_pcb` file, so call `pcb_save_board` before any of them, or you'll be checking the old board. Ask before saving if the user may have unsaved manual edits of their own.
- **Schematic edits go straight to the file.** If that schematic is open in the Schematic Editor, the editor won't see the change, and saving from the editor will overwrite it. Before editing, check `kicad_status` for open documents and ask the user to close the schematic (or to reload it afterward without saving first).
- `sch_*` writers create a `.kicad_sch.bak` by default (`create_backup=True`). Leave that on.
- If IPC isn't connected, don't attempt `pcb_*` tools. Do the read-only work with the headless tools and tell the user how to enable the API.

## Tool reference

**Project:** `kicad_status`, `list_projects`, `inspect_project`, `get_project_variants`

**Live PCB — read:** `pcb_get_board_info`, `pcb_get_stackup`, `pcb_list_nets`, `pcb_list_footprints` (filter by ref regex, value, footprint, layer, DNP), `pcb_get_footprint_details`, `pcb_list_tracks`, `pcb_list_vias`, `pcb_list_zones`, `pcb_list_graphics`, `pcb_list_groups`, `pcb_get_connected_items` (KiCad 10.0.1+), `pcb_get_selection`

**Live PCB — write:** `pcb_move_footprint` (absolute or `dx/dy`, rotation, lock), `pcb_flip_footprint` (10.0.6+), `pcb_update_footprint_fields` (ref/value/DNP/BOM/pos flags), `pcb_add_track`, `pcb_add_via`, `pcb_add_zone`, `pcb_refill_zones`, `pcb_add_graphic`, `pcb_set_selection` (highlight items for the user), `pcb_save_board`
**Live PCB — destructive:** `pcb_delete_items`

**Verification:** `run_erc(schematic_path)`, `run_drc(pcb_path)`, `get_board_stats(pcb_path)`

**Visuals:** `render_pcb_3d`, `export_pcb_visual`, `export_schematic_visual`

**Fabrication / assembly:** `export_gerbers`, `export_drill`, `export_odb_or_ipc2581`, `export_bom`, `export_position_file`, `export_netlist`, `export_3d_model`, `run_jobset`

**Schematic:** `sch_inspect`, `sch_get_symbol`, `sch_update_symbol_properties` (incl. custom fields such as MPN/Manufacturer, DNP, in-BOM), `sch_place_symbol`, `sch_add_wire`, `sch_add_label`, `sch_add_junction_or_no_connect`, `sch_add_text`
**Schematic — destructive:** `sch_delete_items`

**Libraries:** `list_libraries`, `search_symbols`, `inspect_symbol`, `search_footprints`, `inspect_footprint`

All coordinates are in **millimetres** (`x_mm`, `points_mm`, …). Items are addressed by reference designator (`"U1"`) or KIID UUID.

## Safety

- **Ask before** `pcb_delete_items`, `sch_delete_items`, `pcb_save_board` with `overwrite=True` or a `save_as_path`, and `run_drc(save_board=True)`.
- For bulk edits (many moves, field updates or deletions), state the plan and get a go-ahead first. Then do it, and use `pcb_set_selection` so the user can see what changed.
- If a tool fails with a `KICAD_READ_ONLY` error, the server was started read-only on purpose. Report that; don't look for a workaround. Note that read-only mode blocks design edits only (`sch_*` and `pcb_*` writes, `pcb_save_board`, `run_drc(save_board=True)`). The `export_*` tools and `render_pcb_3d` still write output files.
- Writes to exports go to the paths you pass. Don't overwrite an existing output directory without checking what's in it.

## Workflows

### Placing a new part in a schematic

1. `search_symbols(query)` → pick a candidate → `inspect_symbol(lib_id)` to confirm the pins and units.
2. `search_footprints(query)` → `inspect_footprint` to confirm the pad count matches the symbol's pins and the package is right.
3. `sch_inspect` lists the existing symbols, labels, wires and their positions, but it doesn't compute free space or the next reference designator. Work both out yourself: take the highest existing number for that prefix (e.g. `R7` → `R8`) and pick coordinates clear of the listed items.
4. `sch_place_symbol(..., footprint="Lib:Name")`, then wire it with `sch_add_wire` / `sch_add_label`, and mark unused pins with `sch_add_junction_or_no_connect(kind="no_connect")`.
5. `run_erc` and report any new errors.

### PCB design review

1. **Rules:** `inspect_project` for clearances, track/via constraints, net classes and variants. If there are variants, ask which to review (or review the default).
2. **Schematic:** `run_erc` on the root schematic; `sch_inspect` for power symbols, labels and unconnected pins.
3. **PCB:** `run_drc(schematic_parity=True, refill_zones=True)` and `get_board_stats` (dimensions, minimum drill and track, copper area).
4. **Live inspection (if IPC is up):** `pcb_get_board_info`, `pcb_get_stackup`, `pcb_list_footprints`, `pcb_list_zones` for stackup symmetry, pour clearances, DNP flags and critical placement.
5. **Visuals (optional):** top and bottom `render_pcb_3d`; `export_pcb_visual` for key layers.
6. **Report** in three groups: **blockers** (ERC/DRC/parity errors), **DFM warnings** (minimum trace, space, drill and annular ring against the fab's limits; ask which fab if it isn't known), and **assembly/BOM issues** (missing footprints or values, DNP mismatches), each with a concrete fix.

### Manufacturing release

Run the gates in order, and **stop and report at the first failing gate**. Don't generate fab files from a board with errors.

1. **Clean ERC/DRC:** `run_erc` gives 0 errors; `run_drc(schematic_parity=True, refill_zones=True)` gives 0 errors, 0 unconnected items and 0 parity mismatches. (Save the live board first if it was edited.)
2. **Board record:** `get_board_stats` (plus `pcb_get_stackup` if IPC is up) for size, layer count, finish and minimum features. The fab order needs these.
3. **Fabrication:** if the project has a `.kicad_jobset`, offer `run_jobset` instead. Otherwise run `export_gerbers(check_zones=True)` and `export_drill(generate_map=True, generate_report=True)`, or a single `export_odb_or_ipc2581` if the fab accepts it.
4. **Assembly and docs:** `export_bom(exclude_dnp=True)`, `export_position_file(format="csv", units="mm", exclude_dnp=True)`, `export_schematic_visual` (PDF), `export_3d_model` (STEP), and top and bottom `render_pcb_3d`.
5. **Variants:** if releasing a variant, pass `variants=` to every export that takes it. `export_drill` has no variant option, so the drill files are the same for every variant.
6. **Summary:** list every generated file with its size, name the DNP parts that were excluded, and say plainly whether the package is ready to upload.

Default output directory: `<project_dir>/fab_release/` unless the user names one.
