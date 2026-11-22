"""Headless verification and visual export tools powered by KiCad 10 `kicad-cli`."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from kicad_mcp.bridges.cli import KiCadCLI, append_define_vars, append_variants
from kicad_mcp.config import get_config
from kicad_mcp.tools.common import (
    EXPORT_ANNOTATIONS,
    READ_ONLY_ANNOTATIONS,
    resolve_existing_file,
    validate_multi_variant_output,
)


def _summarize_violations(
    items: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int]]:
    by_severity: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    for v in items:
        by_severity[str(v.get("severity", "unknown"))] += 1
        by_type[str(v.get("type", "unknown"))] += 1
    return dict(by_severity), dict(by_type)


def register_cli_verify_tools(server: MCPServer) -> None:
    """Register headless DRC, ERC, stats, 3D render, and visual export tools."""

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def run_drc(
        pcb_path: str,
        *,
        schematic_parity: bool = False,
        refill_zones: bool = False,
        save_board: bool = False,
        all_track_errors: bool = False,
        severity: Literal["all", "error", "warning", "error_and_warning"] = "all",
        include_exclusions: bool = False,
        units: Literal["mm", "in", "mils"] = "mm",
        define_vars: dict[str, str] | None = None,
        output_file: str | None = None,
        max_violations: int = 200,
    ) -> dict[str, Any]:
        """Run KiCad 10 Design Rules Check (DRC) on a `.kicad_pcb` file and return a structured JSON summary.

        Args:
            pcb_path: Path to the `.kicad_pcb` file.
            schematic_parity: Check PCB parity against the project schematic.
            refill_zones: Refill copper zones before running DRC.
            save_board: Save board after refilling zones (requires `refill_zones=True` and writable mode).
            all_track_errors: Report all errors per track segment.
            severity: Severity filter: `all`, `error`, `warning`, or `error_and_warning`.
            include_exclusions: Include excluded DRC violations in the report.
            units: Report coordinate units (`mm`, `in`, or `mils`).
            define_vars: Optional `-D KEY=VALUE` project text variable overrides.
            output_file: Optional path to keep the raw JSON report on disk.
            max_violations: Max violations returned inline in each section (default 200).
        """
        pcb = resolve_existing_file(pcb_path, "PCB file")
        if save_board:
            if not refill_zones:
                raise ValueError("save_board=True requires refill_zones=True")
            get_config().ensure_writable("run_drc(save_board=True)")

        def build_args(out_path: Path) -> list[str]:
            args = ["pcb", "drc", "--format", "json", "--units", units, "--output", str(out_path)]
            if schematic_parity:
                args.append("--schematic-parity")
            if refill_zones:
                args.append("--refill-zones")
            if save_board:
                args.append("--save-board")
            if all_track_errors:
                args.append("--all-track-errors")
            if severity == "all":
                args.append("--severity-all")
            elif severity == "error":
                args.append("--severity-error")
            elif severity == "warning":
                args.append("--severity-warning")
            elif severity == "error_and_warning":
                args.extend(["--severity-error", "--severity-warning"])
            if include_exclusions and severity != "all":
                args.append("--severity-exclusions")
            append_define_vars(args, define_vars)
            args.append(str(pcb))
            return args

        cli = KiCadCLI()
        report, _res, saved_path = await cli.run_json_report(
            build_args,
            output_file=output_file,
            allow_exit_codes={0, 5},
        )

        violations = report.get("violations", [])
        unconnected = report.get("unconnected_items", [])
        parity = report.get("schematic_parity", [])

        sev_counts, type_counts = _summarize_violations(violations)
        error_count = sev_counts.get("error", 0) + len(unconnected) + len(parity)

        return {
            "source": str(pcb),
            "kicad_version": report.get("kicad_version", ""),
            "coordinate_units": report.get("coordinate_units", units),
            "passed": error_count == 0,
            "summary": {
                "violation_count": len(violations),
                "unconnected_count": len(unconnected),
                "schematic_parity_count": len(parity),
                "by_severity": sev_counts,
                "by_type": type_counts,
            },
            "violations": violations[:max_violations],
            "violations_truncated": len(violations) > max_violations,
            "unconnected_items": unconnected[:max_violations],
            "schematic_parity": parity[:max_violations],
            "report_file": str(saved_path) if saved_path else None,
        }

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def run_erc(
        schematic_path: str,
        *,
        severity: Literal["all", "error", "warning", "error_and_warning"] = "all",
        include_exclusions: bool = False,
        units: Literal["mm", "in", "mils"] = "mm",
        define_vars: dict[str, str] | None = None,
        output_file: str | None = None,
        max_violations: int = 200,
    ) -> dict[str, Any]:
        """Run KiCad 10 Electrical Rules Check (ERC) on a `.kicad_sch` file and return a grouped summary by sheet.

        Args:
            schematic_path: Path to the root `.kicad_sch` file.
            severity: Severity filter (`all`, `error`, `warning`, or `error_and_warning`).
            include_exclusions: Include excluded ERC violations.
            units: Report coordinate units (`mm`, `in`, or `mils`).
            define_vars: Optional `-D KEY=VALUE` variable overrides.
            output_file: Optional path to save the raw JSON report on disk.
            max_violations: Max violations per sheet returned inline (default 200).
        """
        sch = resolve_existing_file(schematic_path, "Schematic file")

        def build_args(out_path: Path) -> list[str]:
            args = ["sch", "erc", "--format", "json", "--units", units, "--output", str(out_path)]
            if severity == "all":
                args.append("--severity-all")
            elif severity == "error":
                args.append("--severity-error")
            elif severity == "warning":
                args.append("--severity-warning")
            elif severity == "error_and_warning":
                args.extend(["--severity-error", "--severity-warning"])
            if include_exclusions and severity != "all":
                args.append("--severity-exclusions")
            append_define_vars(args, define_vars)
            args.append(str(sch))
            return args

        cli = KiCadCLI()
        report, _res, saved_path = await cli.run_json_report(
            build_args,
            output_file=output_file,
            allow_exit_codes={0, 5},
        )

        sheets_out: list[dict[str, Any]] = []
        all_violations: list[dict[str, Any]] = []
        for sheet in report.get("sheets", []):
            viols = sheet.get("violations", [])
            all_violations.extend(viols)
            s_sev, s_type = _summarize_violations(viols)
            sheets_out.append({
                "path": sheet.get("path", ""),
                "uuid_path": sheet.get("uuid_path", ""),
                "violation_count": len(viols),
                "by_severity": s_sev,
                "by_type": s_type,
                "violations": viols[:max_violations],
                "truncated": len(viols) > max_violations,
            })

        total_sev, total_type = _summarize_violations(all_violations)
        return {
            "source": str(sch),
            "kicad_version": report.get("kicad_version", ""),
            "coordinate_units": report.get("coordinate_units", units),
            "passed": total_sev.get("error", 0) == 0,
            "summary": {
                "sheet_count": len(sheets_out),
                "total_violations": len(all_violations),
                "by_severity": total_sev,
                "by_type": total_type,
            },
            "sheets": sheets_out,
            "report_file": str(saved_path) if saved_path else None,
        }

    @server.tool(annotations=READ_ONLY_ANNOTATIONS)
    async def get_board_stats(
        pcb_path: str,
        *,
        units: Literal["mm", "in"] = "mm",
        exclude_footprints_without_pads: bool = False,
        subtract_holes_from_board: bool = False,
        subtract_holes_from_copper: bool = False,
        output_file: str | None = None,
    ) -> dict[str, Any]:
        """Return KiCad 10 board statistics (dimensions, pads, vias, drills, footprints, tracks, copper areas).

        Args:
            pcb_path: Path to the `.kicad_pcb` file.
            units: Output units (`mm` or `in`).
            exclude_footprints_without_pads: Exclude mechanical/logo footprints without pads.
            subtract_holes_from_board: Subtract drill holes from reported board area.
            subtract_holes_from_copper: Subtract drill holes from copper areas.
            output_file: Optional path to persist the JSON report.
        """
        pcb = resolve_existing_file(pcb_path, "PCB file")

        def build_args(out_path: Path) -> list[str]:
            args = [
                "pcb",
                "export",
                "stats",
                "--format",
                "json",
                "--units",
                units,
                "--output",
                str(out_path),
            ]
            if exclude_footprints_without_pads:
                args.append("--exclude-footprints-without-pads")
            if subtract_holes_from_board:
                args.append("--subtract-holes-from-board")
            if subtract_holes_from_copper:
                args.append("--subtract-holes-from-copper")
            args.append(str(pcb))
            return args

        cli = KiCadCLI()
        report, _res, saved_path = await cli.run_json_report(build_args, output_file=output_file)
        return {
            "source": str(pcb),
            "stats": report,
            "report_file": str(saved_path) if saved_path else None,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def render_pcb_3d(
        pcb_path: str,
        output_path: str,
        *,
        side: Literal["top", "bottom", "left", "right", "front", "back"] = "top",
        width: int = 1600,
        height: int = 1200,
        rotate: str | None = None,
        quality: Literal["basic", "high", "user", "job_settings"] = "basic",
        background: Literal["default", "transparent", "opaque"] | None = None,
        perspective: bool = False,
        floor: bool = False,
        zoom: float | None = None,
        use_board_stackup_colors: bool = True,
        variants: str | list[str] | None = None,
        define_vars: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Render a 3D PNG or JPEG image of a PCB via `kicad-cli pcb render`.

        Args:
            pcb_path: Path to the `.kicad_pcb` file.
            output_path: Output `.png` or `.jpg`/`.jpeg` file path (use `${VARIANT}` for multiple variants).
            side: Camera side (`top`, `bottom`, `left`, `right`, `front`, `back`).
            width: Image width in pixels (default 1600).
            height: Image height in pixels (default 1200).
            rotate: Optional rotation angles `'X,Y,Z'` in degrees (e.g. `'-45,0,45'` for isometric).
            quality: Render quality (`basic`, `high`, `user`, or `job_settings`).
            background: Background mode (`default`, `transparent`, or `opaque`).
            perspective: Use perspective projection instead of orthographic.
            floor: Enable floor shadows and post-processing.
            zoom: Optional camera zoom factor.
            use_board_stackup_colors: Render soldermask/silkscreen using board stackup colors.
            variants: KiCad 10 design variant name or list of variant names.
            define_vars: Optional `-D KEY=VALUE` project variable overrides.
        """
        pcb = resolve_existing_file(pcb_path, "PCB file")
        validate_multi_variant_output(variants, output_path)
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        args = [
            "pcb",
            "render",
            "--output",
            str(out),
            "--side",
            side,
            "--width",
            str(width),
            "--height",
            str(height),
            "--quality",
            quality,
        ]
        if rotate:
            args.extend(["--rotate", rotate])
        if background:
            args.extend(["--background", background])
        if perspective:
            args.append("--perspective")
        if floor:
            args.append("--floor")
        if zoom is not None:
            args.extend(["--zoom", str(zoom)])
        if use_board_stackup_colors:
            args.append("--use-board-stackup-colors")
        append_variants(args, variants)
        append_define_vars(args, define_vars)
        args.append(str(pcb))

        cli = KiCadCLI()
        res = await cli.run(args)
        return {
            "source": str(pcb),
            "output_path": str(out),
            "exists": out.is_file(),
            "size_bytes": out.stat().st_size if out.is_file() else None,
            "side": side,
            "quality": quality,
            "stdout": res.stdout,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def export_schematic_visual(
        schematic_path: str,
        output_path: str,
        *,
        format: Literal["pdf", "svg"] = "pdf",
        black_and_white: bool = False,
        exclude_drawing_sheet: bool = False,
        no_background_color: bool = False,
        theme: str | None = None,
        pages: str | None = None,
        variants: str | list[str] | None = None,
        define_vars: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Export a schematic to PDF (single file) or SVG (directory of per-sheet SVGs).

        Args:
            schematic_path: Path to the `.kicad_sch` file.
            output_path: Output `.pdf` file path (for `pdf`) or output directory (for `svg`).
            format: Visual format (`pdf` or `svg`).
            black_and_white: Export in monochrome.
            exclude_drawing_sheet: Omit title block and drawing border.
            no_background_color: Transparent/no background fill.
            theme: Optional KiCad color theme name.
            pages: Optional comma-separated page numbers (e.g. `'1,2'`).
            variants: KiCad 10 design variant name or list of names.
            define_vars: Optional `-D KEY=VALUE` overrides.
        """
        sch = resolve_existing_file(schematic_path, "Schematic file")
        validate_multi_variant_output(variants, output_path)
        out = Path(output_path).expanduser().resolve()
        if format == "svg":
            out.mkdir(parents=True, exist_ok=True)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)

        args = ["sch", "export", format, "--output", str(out)]
        if black_and_white:
            args.append("--black-and-white")
        if exclude_drawing_sheet:
            args.append("--exclude-drawing-sheet")
        if no_background_color:
            args.append("--no-background-color")
        if theme:
            args.extend(["--theme", theme])
        if pages:
            args.extend(["--pages", pages])
        append_variants(args, variants)
        append_define_vars(args, define_vars)
        args.append(str(sch))

        cli = KiCadCLI()
        res = await cli.run(args)
        generated = (
            sorted(str(p) for p in out.glob("*.svg"))
            if format == "svg" and out.is_dir()
            else ([str(out)] if out.is_file() else [])
        )
        return {
            "source": str(sch),
            "format": format,
            "output_path": str(out),
            "files": generated,
            "stdout": res.stdout,
        }

    @server.tool(annotations=EXPORT_ANNOTATIONS)
    async def export_pcb_visual(
        pcb_path: str,
        output_path: str,
        *,
        format: Literal["pdf", "svg"] = "pdf",
        layers: list[str] | None = None,
        common_layers: list[str] | None = None,
        black_and_white: bool = False,
        mirror: bool = False,
        exclude_drawing_sheet: bool = False,
        theme: str | None = None,
        check_zones: bool = False,
        mode_single: bool = True,
        variants: str | list[str] | None = None,
        define_vars: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Export PCB layers to PDF or SVG via `kicad-cli pcb export pdf|svg`.

        Args:
            pcb_path: Path to the `.kicad_pcb` file.
            output_path: Output file path (or directory when `format='svg'` and `mode_single=False`).
            format: Visual format (`pdf` or `svg`).
            layers: Layer list (defaults to `['F.Cu', 'B.Cu', 'F.SilkS', 'Edge.Cuts']`).
            common_layers: Optional layers repeated on every page/file (multi-file mode).
            black_and_white: Monochrome output.
            mirror: Mirror output (useful for bottom views).
            exclude_drawing_sheet: Omit drawing sheet border/title block.
            theme: Optional KiCad color theme name.
            check_zones: Check and refill copper zones before plotting.
            mode_single: Combine all layers into a single file (default True).
            variants: KiCad 10 design variant name or list of names.
            define_vars: Optional `-D KEY=VALUE` overrides.
        """
        pcb = resolve_existing_file(pcb_path, "PCB file")
        validate_multi_variant_output(variants, output_path)
        layer_list = layers or ["F.Cu", "B.Cu", "F.SilkS", "Edge.Cuts"]
        out = Path(output_path).expanduser().resolve()

        if format == "svg" and not mode_single:
            out.mkdir(parents=True, exist_ok=True)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)

        args = [
            "pcb",
            "export",
            format,
            "--output",
            str(out),
            "--layers",
            ",".join(layer_list),
        ]
        if common_layers:
            args.extend(["--common-layers", ",".join(common_layers)])
        if black_and_white:
            args.append("--black-and-white")
        if mirror:
            args.append("--mirror")
        if exclude_drawing_sheet:
            args.append("--exclude-drawing-sheet")
        if theme:
            args.extend(["--theme", theme])
        if check_zones:
            args.append("--check-zones")
        if format == "svg":
            args.append("--mode-single" if mode_single else "--mode-multi")
        else:
            args.append("--mode-single" if mode_single else "--mode-multifile")

        append_variants(args, variants)
        append_define_vars(args, define_vars)
        args.append(str(pcb))

        cli = KiCadCLI()
        res = await cli.run(args)
        if out.is_dir():
            files = sorted(str(p) for p in out.iterdir() if p.is_file())
        else:
            files = [str(out)] if out.is_file() else []

        return {
            "source": str(pcb),
            "format": format,
            "layers": layer_list,
            "output_path": str(out),
            "files": files,
            "stdout": res.stdout,
        }
