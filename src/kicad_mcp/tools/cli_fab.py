"""Manufacturing exports, BOM, netlist, 3D CAD, ODB++/IPC-2581, and jobset tools."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from kicad_mcp.bridges.cli import KiCadCLI, append_define_vars, append_variants
from kicad_mcp.tools.common import (
    EXPORT_ANNOTATIONS,
    resolve_existing_file,
    validate_multi_variant_output,
)


def _list_files_in_dir(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(str(p) for p in directory.iterdir() if p.is_file())


def register_cli_fab_tools(server: MCPServer) -> None:
    """Register manufacturing output & KiCad 10 jobset tools."""

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def export_gerbers(
        pcb_path: str,
        output_dir: str,
        *,
        layers: list[str] | None = None,
        use_board_plot_params: bool = False,
        check_zones: bool = True,
        use_drill_file_origin: bool = False,
        subtract_soldermask: bool = False,
        no_x2: bool = False,
        no_protel_ext: bool = False,
        variants: str | list[str] | None = None,
        define_vars: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Export Gerber fabrication files for a PCB via `kicad-cli pcb export gerbers`.

        Args:
            pcb_path: Path to the `.kicad_pcb` file.
            output_dir: Directory where Gerber files will be written.
            layers: Optional explicit layer list (e.g. `['F.Cu','B.Cu','F.Mask','B.Mask','F.SilkS','B.SilkS','Edge.Cuts']`).
            use_board_plot_params: Use Gerber plot parameters stored inside the `.kicad_pcb` file.
            check_zones: Check and refill copper zones before plotting (default True).
            use_drill_file_origin: Use the drill/place file origin instead of page origin.
            subtract_soldermask: Subtract soldermask openings from silkscreen.
            no_x2: Disable Gerber X2 attributes.
            no_protel_ext: Use `.gbr` extensions instead of Protel extensions.
            variants: KiCad 10 design variant name or list of names.
            define_vars: Optional `-D KEY=VALUE` project variable overrides.
        """
        pcb = resolve_existing_file(pcb_path, "PCB file")
        validate_multi_variant_output(variants, output_dir)
        out = Path(output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)

        args = ["pcb", "export", "gerbers", "--output", str(out)]
        if layers:
            args.extend(["--layers", ",".join(layers)])
        if use_board_plot_params:
            args.append("--board-plot-params")
        if check_zones:
            args.append("--check-zones")
        if use_drill_file_origin:
            args.append("--use-drill-file-origin")
        if subtract_soldermask:
            args.append("--subtract-soldermask")
        if no_x2:
            args.append("--no-x2")
        if no_protel_ext:
            args.append("--no-protel-ext")
        append_variants(args, variants)
        append_define_vars(args, define_vars)
        args.append(str(pcb))

        cli = KiCadCLI()
        res = await cli.run(args)
        files = _list_files_in_dir(out)
        return {
            "source": str(pcb),
            "output_dir": str(out),
            "file_count": len(files),
            "files": files,
            "stdout": res.stdout,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def export_drill(
        pcb_path: str,
        output_dir: str,
        *,
        format: Literal["excellon", "gerber"] = "excellon",
        drill_origin: Literal["absolute", "plot"] = "absolute",
        excellon_units: Literal["mm", "in"] = "mm",
        separate_th: bool = True,
        generate_map: bool = False,
        map_format: Literal["pdf", "gerberx2", "ps", "dxf", "svg"] = "pdf",
        generate_report: bool = False,
    ) -> dict[str, Any]:
        """Export Excellon or Gerber drill files (plus optional drill map and report).

        Args:
            pcb_path: Path to the `.kicad_pcb` file.
            output_dir: Output directory for drill files.
            format: Drill file format (`excellon` or `gerber`).
            drill_origin: Coordinate origin (`absolute` or `plot`).
            excellon_units: Excellon units (`mm` or `in`).
            separate_th: Generate separate PTH and NPTH files (Excellon).
            generate_map: Generate a drill map file.
            map_format: Drill map format (`pdf`, `gerberx2`, `ps`, `dxf`, `svg`).
            generate_report: Generate a drill hit summary report.
        """
        pcb = resolve_existing_file(pcb_path, "PCB file")
        out = Path(output_dir).expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)

        args = [
            "pcb",
            "export",
            "drill",
            "--output",
            str(out) + "/",
            "--format",
            format,
            "--drill-origin",
            drill_origin,
        ]
        if format == "excellon":
            args.extend(["--excellon-units", excellon_units])
            if separate_th:
                args.append("--excellon-separate-th")
        if generate_map:
            args.extend(["--generate-map", "--map-format", map_format])
        if generate_report:
            report_path = out / f"{pcb.stem}-drl_report.txt"
            args.extend(["--generate-report", "--report-path", str(report_path)])
        args.append(str(pcb))

        cli = KiCadCLI()
        res = await cli.run(args)
        files = _list_files_in_dir(out)
        return {
            "source": str(pcb),
            "format": format,
            "output_dir": str(out),
            "file_count": len(files),
            "files": files,
            "stdout": res.stdout,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def export_bom(
        schematic_path: str,
        output_path: str,
        *,
        variants: str | list[str] | None = None,
        preset: str | None = None,
        fields: str | None = None,
        labels: str | None = None,
        group_by: str | None = None,
        sort_field: str | None = None,
        exclude_dnp: bool = False,
        field_delimiter: str = ",",
        max_rows: int = 500,
    ) -> dict[str, Any]:
        """Export a Bill of Materials (BOM) CSV via `kicad-cli sch export bom` and return parsed rows.

        Args:
            schematic_path: Path to the root `.kicad_sch` file.
            output_path: Output CSV path (use `${VARIANT}` for multiple variants).
            variants: KiCad 10 variant name or list of variant names.
            preset: Optional named BOM preset from the schematic (e.g. `'Grouped By Value'`).
            fields: Comma-separated fields (e.g. `'Reference,Value,Footprint,QUANTITY,DNP'`).
            labels: Comma-separated header labels corresponding to `fields`.
            group_by: Comma-separated fields to group references by (e.g. `'Value,Footprint'`).
            sort_field: Field name to sort rows by.
            exclude_dnp: Exclude Do-Not-Populate symbols.
            field_delimiter: Column delimiter (default `,`).
            max_rows: Maximum parsed rows returned inline (default 500).
        """
        sch = resolve_existing_file(schematic_path, "Schematic file")
        validate_multi_variant_output(variants, output_path)
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        args = ["sch", "export", "bom", "--output", str(out)]
        append_variants(args, variants)
        if preset:
            args.extend(["--preset", preset])
        if fields:
            args.extend(["--fields", fields])
        if labels:
            args.extend(["--labels", labels])
        if group_by:
            args.extend(["--group-by", group_by])
        if sort_field:
            args.extend(["--sort-field", sort_field])
        if exclude_dnp:
            args.append("--exclude-dnp")
        if field_delimiter != ",":
            args.extend(["--field-delimiter", field_delimiter])
        args.append(str(sch))

        cli = KiCadCLI()
        res = await cli.run(args)

        rows: list[dict[str, str]] = []
        headers: list[str] = []
        if out.is_file():
            with out.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh, delimiter=field_delimiter)
                headers = list(reader.fieldnames or [])
                for row in reader:
                    rows.append({k: v for k, v in row.items() if k is not None})

        return {
            "source": str(sch),
            "output_path": str(out),
            "headers": headers,
            "row_count": len(rows),
            "rows": rows[:max_rows],
            "truncated": len(rows) > max_rows,
            "stdout": res.stdout,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def export_position_file(
        pcb_path: str,
        output_path: str,
        *,
        format: Literal["csv", "ascii", "gerber"] = "csv",
        side: Literal["both", "front", "back"] = "both",
        units: Literal["mm", "in"] = "mm",
        smd_only: bool = False,
        exclude_dnp: bool = True,
        exclude_fp_th: bool = False,
        use_drill_file_origin: bool = False,
        bottom_negate_x: bool = False,
        variants: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """Export a component pick-and-place / position file via `kicad-cli pcb export pos`.

        Args:
            pcb_path: Path to the `.kicad_pcb` file.
            output_path: Destination file path.
            format: Output format (`csv`, `ascii`, or `gerber`).
            side: Board side (`both`, `front`, or `back`).
            units: Coordinate units (`mm` or `in`, for `csv`/`ascii`).
            smd_only: Include only SMD footprints.
            exclude_dnp: Exclude footprints marked Do Not Populate (default True).
            exclude_fp_th: Exclude footprints with through-hole pads.
            use_drill_file_origin: Use drill/place origin.
            bottom_negate_x: Negate X coordinates for bottom-side footprints.
            variants: KiCad 10 design variant name or list of names.
        """
        pcb = resolve_existing_file(pcb_path, "PCB file")
        validate_multi_variant_output(variants, output_path)
        if format == "gerber" and side == "both":
            raise ValueError("Gerber position format only supports side='front' or side='back'")

        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        args = [
            "pcb",
            "export",
            "pos",
            "--output",
            str(out),
            "--format",
            format,
            "--side",
            side,
        ]
        if format in {"csv", "ascii"}:
            args.extend(["--units", units])
            if smd_only:
                args.append("--smd-only")
            if exclude_fp_th:
                args.append("--exclude-fp-th")
            if use_drill_file_origin:
                args.append("--use-drill-file-origin")
            if bottom_negate_x:
                args.append("--bottom-negate-x")
        if exclude_dnp:
            args.append("--exclude-dnp")
        append_variants(args, variants)
        args.append(str(pcb))

        cli = KiCadCLI()
        res = await cli.run(args)
        row_count: int | None = None
        if format == "csv" and out.is_file():
            with out.open("r", encoding="utf-8-sig", newline="") as fh:
                row_count = max(0, sum(1 for _ in csv.reader(fh)) - 1)

        return {
            "source": str(pcb),
            "output_path": str(out),
            "format": format,
            "side": side,
            "units": units,
            "row_count": row_count,
            "exists": out.is_file(),
            "stdout": res.stdout,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def export_netlist(
        schematic_path: str,
        output_path: str,
        *,
        format: Literal[
            "kicadsexpr",
            "kicadxml",
            "spice",
            "spicemodel",
            "cadstar",
            "orcadpcb2",
            "pads",
            "allegro",
        ] = "kicadsexpr",
        variants: str | list[str] | None = None,
    ) -> dict[str, Any]:
        """Export a schematic netlist via `kicad-cli sch export netlist`.

        Args:
            schematic_path: Path to the root `.kicad_sch` file.
            output_path: Output netlist file path.
            format: Netlist format (`kicadsexpr`, `kicadxml`, `spice`, etc.).
            variants: KiCad 10 design variant name or list of names.
        """
        sch = resolve_existing_file(schematic_path, "Schematic file")
        validate_multi_variant_output(variants, output_path)
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        args = ["sch", "export", "netlist", "--format", format, "--output", str(out)]
        append_variants(args, variants)
        args.append(str(sch))

        cli = KiCadCLI()
        res = await cli.run(args)
        return {
            "source": str(sch),
            "format": format,
            "output_path": str(out),
            "exists": out.is_file(),
            "size_bytes": out.stat().st_size if out.is_file() else None,
            "stdout": res.stdout,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def export_3d_model(
        pcb_path: str,
        output_path: str,
        *,
        format: Literal["step", "glb"] = "step",
        variants: str | list[str] | None = None,
        no_dnp: bool = True,
        include_tracks: bool = False,
        include_pads: bool = False,
        include_zones: bool = False,
        include_silkscreen: bool = False,
        include_soldermask: bool = False,
        board_only: bool = False,
        subst_models: bool = True,
        define_vars: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Export a 3D STEP or GLB model of a PCB via `kicad-cli pcb export step|glb`.

        Args:
            pcb_path: Path to the `.kicad_pcb` file.
            output_path: Output `.step`/`.stp` or `.glb` file path.
            format: 3D model format (`step` or `glb`).
            variants: KiCad 10 design variant name or list of names.
            no_dnp: Exclude 3D models of DNP footprints (default True).
            include_tracks: Include copper tracks and vias in the 3D model.
            include_pads: Include pad geometry.
            include_zones: Include copper zones.
            include_silkscreen: Include silkscreen graphics.
            include_soldermask: Include soldermask layer geometry.
            board_only: Export board body only (no component 3D models).
            subst_models: Substitute STEP/IGES models in place of VRML where available.
            define_vars: Optional `-D KEY=VALUE` overrides.
        """
        pcb = resolve_existing_file(pcb_path, "PCB file")
        validate_multi_variant_output(variants, output_path)
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        args = ["pcb", "export", format, "--force", "--output", str(out)]
        if no_dnp:
            args.append("--no-dnp")
        if include_tracks:
            args.append("--include-tracks")
        if include_pads:
            args.append("--include-pads")
        if include_zones:
            args.append("--include-zones")
        if include_silkscreen:
            args.append("--include-silkscreen")
        if include_soldermask:
            args.append("--include-soldermask")
        if board_only:
            args.append("--board-only")
        if subst_models:
            args.append("--subst-models")
        append_variants(args, variants)
        append_define_vars(args, define_vars)
        args.append(str(pcb))

        cli = KiCadCLI()
        res = await cli.run(args)
        return {
            "source": str(pcb),
            "format": format,
            "output_path": str(out),
            "exists": out.is_file(),
            "size_bytes": out.stat().st_size if out.is_file() else None,
            "stdout": res.stdout,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def export_odb_or_ipc2581(
        pcb_path: str,
        output_path: str,
        *,
        format: Literal["odb", "ipc2581"] = "odb",
        units: Literal["mm", "in"] = "mm",
        compress: bool = True,
        check_zones: bool = True,
        variants: str | list[str] | None = None,
        define_vars: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Export unified manufacturing data in ODB++ or IPC-2581 format.

        Args:
            pcb_path: Path to the `.kicad_pcb` file.
            output_path: Output archive/file path (e.g. `.tgz`/`.zip` for ODB++ or `.xml`/`.cvg` for IPC-2581).
            format: Manufacturing package format (`odb` or `ipc2581`).
            units: Units (`mm` or `in`).
            compress: Compress output archive (zip for ODB++, gzip/zip for IPC-2581).
            check_zones: Check and refill zones before export (ODB++).
            variants: KiCad 10 design variant name or list of names.
            define_vars: Optional `-D KEY=VALUE` overrides.
        """
        pcb = resolve_existing_file(pcb_path, "PCB file")
        validate_multi_variant_output(variants, output_path)
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        args = ["pcb", "export", format, "--output", str(out), "--units", units]
        if format == "odb":
            args.extend(["--compression", "zip" if compress else "none"])
            if check_zones:
                args.append("--check-zones")
        else:
            if compress:
                args.append("--compress")
        append_variants(args, variants)
        append_define_vars(args, define_vars)
        args.append(str(pcb))

        cli = KiCadCLI()
        res = await cli.run(args)
        return {
            "source": str(pcb),
            "format": format,
            "output_path": str(out),
            "exists": out.exists(),
            "size_bytes": out.stat().st_size if out.is_file() else None,
            "stdout": res.stdout,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def run_jobset(
        project_path: str,
        jobset_file: str,
        *,
        output_name: str | None = None,
        stop_on_error: bool = True,
    ) -> dict[str, Any]:
        """Execute a KiCad 10 `.kicad_jobset` pipeline via `kicad-cli jobset run`.

        Args:
            project_path: Path to the `.kicad_pro` file.
            jobset_file: Path to the `.kicad_jobset` file.
            output_name: Optional single jobset output name to generate (default: all outputs).
            stop_on_error: Stop immediately if any job fails (default True).
        """
        pro = resolve_existing_file(project_path, "Project file")
        jobset = resolve_existing_file(jobset_file, "Jobset file")

        args = ["jobset", "run", "--file", str(jobset)]
        if stop_on_error:
            args.append("--stop-on-error")
        if output_name:
            args.extend(["--output", output_name])
        args.append(str(pro))

        cli = KiCadCLI()
        res = await cli.run(args)
        return {
            "project_file": str(pro),
            "jobset_file": str(jobset),
            "output_name": output_name,
            "returncode": res.returncode,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
