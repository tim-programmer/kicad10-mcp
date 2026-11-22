# `kicad10-mcp` — KiCad 10 Model Context Protocol (MCP) Server

`kicad10-mcp` connects AI assistants (Antigravity, Claude Desktop, Cursor, and other MCP clients) to **KiCad 10** so you can inspect and edit live PCBs, run headless ERC/DRC checks, generate manufacturing packages, search KiCad libraries, and edit schematics directly.

---

## Quick Start

### 1. Prerequisites

- **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/) (or `pip`)
- **KiCad 10** (`kicad-cli 10.x` in `PATH`, or pointed to via `KICAD_CLI`)
- *(Optional, for live PCB tools)* KiCad 10 GUI running with the IPC API enabled

### 2. Install

```bash
cd /home/tims/kicad1-mcp
uv sync --extra dev
```

Confirm the server starts:

```bash
uv run kicad10-mcp --help
```

### 3. Enable KiCad 10 IPC API (for live `pcb_*` tools)

1. Open **KiCad 10**.
2. Go to **Preferences → Plugins** and enable **Enable KiCad API**.
3. Open a `.kicad_pcb` board in the PCB Editor.

> Headless tools (`run_drc`, `run_erc`, `export_*`, `sch_*`, `search_*`, `inspect_*`) work **without** the KiCad GUI open.

### 4. Connect Your MCP Client

#### Antigravity / Cursor / Windsurf (`mcp.json`)

```json
{
  "mcpServers": {
    "kicad10": {
      "command": "uv",
      "args": ["run", "--directory", "/home/tims/kicad1-mcp", "kicad10-mcp"],
      "env": {
        "KICAD_READ_ONLY": "0"
      }
    }
  }
}
```

#### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "kicad10": {
      "command": "/home/tims/kicad1-mcp/.venv/bin/kicad10-mcp",
      "args": [],
      "env": {
        "KICAD_READ_ONLY": "0"
      }
    }
  }
}
```

#### Streamable HTTP Transport (for remote or HTTP-based clients)

```bash
uv run kicad10-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

The MCP endpoint is served at `http://127.0.0.1:8000/mcp`.

### 5. Verify Your Setup

Ask your AI client:

> "Check KiCad status."

This calls `kicad_status` and reports:
- `cli.available` and `cli.version` (e.g. `10.0.8`)
- `ipc.connected`, `ipc.kicad_version`, and any `open_documents`
- `read_only` mode status
- Discovered KiCad 10 symbol and footprint library directories

---

## How `kicad10-mcp` Works

The server bridges four KiCad 10 interfaces under one MCP server:

| Capability | Tools | Backend | Requires KiCad GUI Open? |
| --- | --- | --- | --- |
| Live PCB inspect & edit | `pcb_*` | Official KiCad 10 IPC API (`kipy`) | **Yes** (with IPC API enabled) |
| ERC, DRC, stats, 3D renders, fab exports, jobsets | `run_drc`, `run_erc`, `get_board_stats`, `render_pcb_3d`, `export_*`, `run_jobset` | `kicad-cli 10` subprocess | No (headless) |
| Direct schematic inspect & edit | `sch_*` | Native S-expression parser/writer | **No** (close schematic in GUI before editing) |
| Project & library search/inspect | `list_projects`, `inspect_project`, `list_libraries`, `search_*`, `inspect_*` | Direct file & lib-table parsing | No |

---

## Common Workflows & Example Prompts

### 1. Run a Design Review (ERC, DRC, Stats, Stackup)

You can invoke the built-in **`pcb_design_review`** MCP prompt or ask directly:

- *"Find all KiCad projects under `~/hardware` and inspect the rules and variants in `sensor_board.kicad_pro`."*
- *"Run ERC on `sensor_board.kicad_sch` and DRC with schematic parity and zone refill on `sensor_board.kicad_pcb`, then group errors by type."*
- *"Show board dimensions, drill summary, and layer stackup for my open PCB."*
- *"Render a 3D isometric PNG of the top and bottom of `sensor_board.kicad_pcb`."*

### 2. Inspect & Edit a Live PCB in KiCad 10

With a board open in the KiCad 10 PCB Editor:

- *"List all 0603 resistors on `F.Cu` and show the pads and bounding box of `U1`."*
- *"Highlight `C1`, `C2`, and `U1` in the KiCad PCB Editor."* (`pcb_set_selection`)
- *"Move `R1` by +2.5 mm in X, rotate it to 90°, and flip `C4` to the bottom layer."*
- *"Route a 0.25 mm `F.Cu` track on net `+3V3` from `(10, 20)` to `(15, 20)` to `(15, 25)` and drop a 0.6/0.3 mm through via at `(15, 25)`."*
- *"Create a `GND` copper pour on `B.Cu` covering `(0, 0)` to `(50, 40)` and refill zones."*
- *"Find all copper items connected to pad UUID `<uuid>`."* (`pcb_get_connected_items`)

Every live edit (`pcb_move_footprint`, `pcb_flip_footprint`, `pcb_add_track`, `pcb_add_via`, `pcb_add_zone`, `pcb_add_graphic`, `pcb_delete_items`, `pcb_update_footprint_fields`) is wrapped in an atomic KiCad undo/redo commit — press **Ctrl+Z** in KiCad to undo any tool action in one step.

### 3. Generate a Manufacturing Release (and KiCad 10 Variants)

Invoke the built-in **`manufacturing_release_checklist`** MCP prompt or ask:

- *"Check `sensor_board.kicad_pro` for KiCad 10 variants, run ERC + DRC with parity, and if clean, export Gerbers, Excellon drills (with map & report), BOM CSV (excluding DNP), pick-and-place CSV in mm, STEP 3D model, and schematic PDF into `./fab_release`."*
- *"Export an ODB++ zip package for the `high_temp` variant."*
- *"Run the `release.kicad_jobset` jobset on `sensor_board.kicad_pro`."*

#### Exporting Multiple KiCad 10 Variants at Once

When passing a list of multiple variants (e.g. `variants=["standard", "high_temp"]`), KiCad 10 requires `${VARIANT}` in `output_path` / `output_dir` so each variant writes to its own file or folder:

- Example: `output_path="out/bom_${VARIANT}.csv"` or `output_dir="out/gerbers_${VARIANT}"`

### 4. Search Libraries & Edit Schematics Directly

- *"Search KiCad symbol libraries for `STM32F4` and inspect the pin table for `MCU_ST_STM32F4:STM32F405RGTx`."*
- *"Search footprints for `QFN-48` and inspect its pad sizes and courtyard bounding box."*
- *"Place `Device:R` as `R12` (`10k`, footprint `Resistor_SMD:R_0603_1608Metric`) at `(100, 80)` in `power.kicad_sch`, wire pin 1 to `(110, 76.19)`, and add a global label `+3V3`."*
- *"Update `R12` in `power.kicad_sch` to add custom field `MPN=RC0603FR-0710KL` and mark `R13` as DNP."*

---

## Safety, Backups, and Read-Only Mode

- **Read-only mode:** Launch with `--read-only` or set `KICAD_READ_ONLY=1` to permit all inspection, search, ERC/DRC, and export tools while blocking all PCB edits (`pcb_move_*`, `pcb_add_*`, `pcb_delete_*`, `pcb_save_board`, `pcb_refill_zones`), schematic edits (`sch_place_symbol`, `sch_update_*`, `sch_add_*`, `sch_delete_items`), and `run_drc(save_board=True)`.
- **MCP Tool Annotations:** Every tool carries MCP `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`) so compatible MCP clients can auto-approve read tools and prompt before writes or deletions.
- **Schematic backups:** Direct `.kicad_sch` write tools write atomically (temp file + `os.replace`) and save a `.kicad_sch.bak` backup by default (`create_backup=True`). Close the schematic in KiCad's Schematic Editor before running `sch_*` write tools so KiCad does not overwrite external edits when saving.
- **Live PCB undo/redo:** All IPC board edits run inside `begin_commit` / `push_commit`, rolling back via `drop_commit` if any error occurs.

---

## CLI Options & Environment Variables

### CLI Flags (`kicad10-mcp`)

| Flag | Default | Description |
| --- | --- | --- |
| `--transport {stdio,streamable-http}` | `stdio` | MCP transport protocol. |
| `--host HOST` | `127.0.0.1` | Bind host for `streamable-http`. |
| `--port PORT` | `8000` | Bind port for `streamable-http`. |
| `--read-only` | Off | Disable all design write/edit tools (overrides `KICAD_READ_ONLY`). |
| `--socket PATH` | KiCad default | Override KiCad 10 IPC API socket URI. |
| `--kicad-cli PATH` | Auto-detected | Explicit path to `kicad-cli` binary. |

### Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `KICAD_CLI` | Found via `PATH` | Path to `kicad-cli` executable. |
| `KICAD_API_SOCKET` | Platform default (e.g. `ipc:///tmp/kicad/api.sock`) | KiCad 10 IPC API socket path. |
| `KICAD_API_TOKEN` | Unset | Optional KiCad IPC API authentication token. |
| `KICAD_IPC_TIMEOUT_MS` | `5000` | Timeout in ms for live IPC requests. |
| `KICAD_CLI_TIMEOUT_S` | `120` | Subprocess timeout in seconds for `kicad-cli`. |
| `KICAD_READ_ONLY` | `0` | Set to `1`, `true`, `yes`, or `on` to block all edits. |
| `KICAD10_SYMBOL_DIR` | OS standard KiCad paths | Override/prepend KiCad 10 symbol library directory. |
| `KICAD10_FOOTPRINT_DIR` | OS standard KiCad paths | Override/prepend KiCad 10 footprint library directory. |

---

## Troubleshooting

- **`Could not connect to KiCad 10 IPC API`**
  - Verify KiCad 10 is running and **Preferences → Plugins → Enable KiCad API** is checked.
  - Ensure a `.kicad_pcb` board is open in the PCB Editor.
  - On Linux/macOS/Windows with custom sockets, pass `--socket` or set `KICAD_API_SOCKET`.
- **`kicad-cli executable not found`**
  - Add KiCad 10's `bin` directory to `PATH`, or pass `--kicad-cli /full/path/to/kicad-cli` (or set `KICAD_CLI`).
- **Schematic edits don't show up in KiCad**
  - KiCad's Schematic Editor does not auto-reload `.kicad_sch` files modified on disk. Close and reopen the schematic (or close it before running `sch_*` write tools).
- **`When specifying multiple variants, output_path must include '${VARIANT}'`**
  - Either pass a single variant string (e.g. `variants="high_temp"`) or include `${VARIANT}` in the output path (e.g. `out/board_${VARIANT}.pdf`).
- **`PermissionError: Cannot perform '...' while KICAD_READ_ONLY is enabled`**
  - Remove `--read-only` and unset `KICAD_READ_ONLY` (or set it to `0`), then restart the MCP server.

---

## Tool, Resource, and Prompt Reference

### Project & Environment (4 tools)
| Tool | Description |
| --- | --- |
| `kicad_status` | CLI version, live IPC connectivity/version, open documents, read-only status, library paths. |
| `list_projects` | Recursively discover `.kicad_pro` projects in a directory tree. |
| `inspect_project` | Parse `.kicad_pro` design rules, net classes, text variables, variants, and sibling files. |
| `get_project_variants` | List KiCad 10 design variants and active variant from `.kicad_pro`. |

### Live PCB Inspection — IPC (12 tools)
| Tool | Description |
| --- | --- |
| `pcb_get_board_info` | Layers, copper count, title block, grid/drill origins, item counts, net classes. |
| `pcb_get_stackup` | Copper, dielectric (`epsilon_r`, `loss_tangent`), soldermask, silkscreen, finish, impedance. |
| `pcb_list_nets` | Filter nets by regex pattern or netclass. |
| `pcb_list_footprints` | Filter footprints by reference regex, value, footprint ID, layer, or DNP flag. |
| `pcb_get_footprint_details` | Full fields, pads, nets, drills, 3D models, and bounding box. |
| `pcb_list_tracks` | Straight and arc tracks filtered by net/layer with total routed length. |
| `pcb_list_vias` | Vias filtered by net or type (`through`, `blind_buried`, `micro`, `blind`, `buried`). |
| `pcb_list_zones` | Copper pours and rule/keepout areas filtered by net/layer/type. |
| `pcb_list_graphics` | Board shapes (`Edge.Cuts`, silkscreen, fab) and board text items. |
| `pcb_get_connected_items` | KiCad 10.0.1+ copper connectivity query from item KIID UUIDs. |
| `pcb_get_selection` | Items currently selected in the KiCad PCB Editor GUI. |
| `pcb_list_groups` | KiCad 10 layout groups and member item IDs. |

### Live PCB Editing — IPC (11 tools)
| Tool | Description |
| --- | --- |
| `pcb_move_footprint` | Move (absolute or relative), rotate, or lock/unlock a footprint. |
| `pcb_flip_footprint` | Flip a footprint `left_right` or `top_bottom` (KiCad 10.0.6+ `flip_items`). |
| `pcb_update_footprint_fields` | Update reference, value, datasheet, description, DNP, and BOM/POS exclusion flags. |
| `pcb_add_track` | Route single or multi-segment polyline tracks on any copper layer. |
| `pcb_add_via` | Place through, blind/buried, or micro vias with optional start/end layers. |
| `pcb_add_zone` | Create a copper pour or rule area polygon, with optional zone refill. |
| `pcb_refill_zones` | Refill all copper zones on the open board. |
| `pcb_add_graphic` | Add a `segment`, `rectangle`, `circle`, or `text` item on any layer. |
| `pcb_delete_items` | Delete board items by KIID UUID. |
| `pcb_set_selection` | Select/highlight footprints or items in KiCad GUI, or clear selection. |
| `pcb_save_board` | Save or `save_as` the open PCB. |

### Headless Verification & Visual Exports — `kicad-cli` (6 tools)
| Tool | Description |
| --- | --- |
| `run_drc` | JSON DRC with schematic parity, zone refill, severity filters, and grouped summary. |
| `run_erc` | JSON ERC grouped by schematic sheet. |
| `get_board_stats` | Board statistics report (`pcb export stats --format json`). |
| `render_pcb_3d` | 3D PNG/JPEG board render (`side`, `rotate`, `quality`, `perspective`, `floor`, `--variant`). |
| `export_schematic_visual` | Schematic PDF (single file) or SVG (directory of sheets) with `--variant`. |
| `export_pcb_visual` | PCB layer PDF or SVG export with `--variant`. |

### Manufacturing Outputs & Jobsets — `kicad-cli` (8 tools)
| Tool | Description |
| --- | --- |
| `export_gerbers` | Gerber fabrication files (`--layers`, `--board-plot-params`, `--check-zones`, `--variant`). |
| `export_drill` | Excellon or Gerber drill files, plus optional drill map and drill hit report. |
| `export_bom` | BOM CSV export (`--variant`, `--preset`, `--fields`, `--group-by`) with parsed inline rows. |
| `export_position_file` | Pick-and-place CSV/ASCII/Gerber (`--side`, `--units`, `--smd-only`, `--exclude-dnp`, `--variant`). |
| `export_netlist` | Schematic netlist (`kicadsexpr`, `kicadxml`, `spice`, etc.) with `--variant`. |
| `export_3d_model` | STEP or GLB 3D CAD export (`--no-dnp`, `--include-tracks`, `--board-only`, `--variant`). |
| `export_odb_or_ipc2581` | ODB++ or IPC-2581 manufacturing package export with `--variant`. |
| `run_jobset` | Execute a KiCad 10 `.kicad_jobset` pipeline (`--stop-on-error`, optional `--output`). |

### Schematic S-Expression Workflows (9 tools)
| Tool | Description |
| --- | --- |
| `sch_inspect` | Parse title block, paper, symbols, power symbols, labels, wires, junctions, no-connects, sub-sheets. |
| `sch_get_symbol` | Full properties, pin UUIDs, instances, and cached `lib_symbols` pin table. |
| `sch_update_symbol_properties` | Edit/add standard & custom fields (MPN, etc.) and DNP/BOM flags; syncs KiCad 10 `instances` reference. |
| `sch_add_wire` | Add one or more connected wire segments. |
| `sch_add_label` | Add `local`, `global`, or `hierarchical` net labels. |
| `sch_add_junction_or_no_connect` | Add wire `junction` dots or `no_connect` markers. |
| `sch_add_text` | Add schematic text annotations. |
| `sch_place_symbol` | Embed a symbol from `.kicad_sym` into `lib_symbols` and place a KiCad 10 symbol instance. |
| `sch_delete_items` | Delete schematic items (symbols, wires, labels, junctions, etc.) by UUID. |

### Symbol & Footprint Libraries (5 tools)
| Tool | Description |
| --- | --- |
| `list_libraries` | Parse global & project `sym-lib-table` / `fp-lib-table` plus standard KiCad 10 library directories. |
| `search_symbols` | Fast search across `.kicad_sym` files by name, description, keywords, and datasheet. |
| `inspect_symbol` | Parse symbol properties, unit count, `(extends ...)` inheritance chain, and full pin table. |
| `search_footprints` | Search `.pretty/*.kicad_mod` libraries by name (and optionally description/tags). |
| `inspect_footprint` | Parse footprint attributes, pads, drills, courtyard bounding box, and 3D models. |

### MCP Resources & Prompts
- **Resources:**
  - `kicad://status` — Live JSON status snapshot (`kicad-cli`, IPC, open docs, read-only, library dirs).
  - `kicad://board/summary` — Live JSON summary of the currently open PCB.
- **Prompts:**
  - `pcb_design_review` — Guided electrical, DRC/ERC, stackup, and DFM review workflow.
  - `manufacturing_release_checklist` — Gated pre-release verification and fab package generation workflow.

---

## Development

```bash
uv run pytest -v
uv run ruff check src tests
```
