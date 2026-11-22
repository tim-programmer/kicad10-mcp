"""KiCad 10 MCP server assembly and CLI entry point (`kicad10-mcp`)."""

from __future__ import annotations

import argparse
from typing import Literal

from mcp.server.mcpserver import MCPServer

from kicad_mcp import __version__
from kicad_mcp.config import KiCadConfig, get_config, set_config
from kicad_mcp.resources import register_resources_and_prompts
from kicad_mcp.tools import register_all_tools

SERVER_INSTRUCTIONS = (
    "KiCad 10 MCP server providing live PCB inspection/editing via the official KiCad 10 IPC API "
    "(kicad-python/kipy), headless verification & manufacturing exports via kicad-cli 10, "
    "direct S-expression .kicad_sch schematic editing, and KiCad symbol/footprint library search."
)


def create_server() -> MCPServer:
    """Create and configure the `kicad10-mcp` MCPServer with all tools, resources, and prompts."""
    server = MCPServer(
        name="kicad10-mcp",
        title="KiCad 10 MCP Server",
        description=SERVER_INSTRUCTIONS,
        instructions=SERVER_INSTRUCTIONS,
        version=__version__,
    )
    register_all_tools(server)
    register_resources_and_prompts(server)
    return server


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kicad10-mcp",
        description="Model Context Protocol (MCP) server for KiCad 10.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="MCP transport protocol (default: stdio).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind for streamable-http transport (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind for streamable-http transport (default: 8000).",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=None,
        help="Disable all PCB and schematic write/edit operations (overrides KICAD_READ_ONLY).",
    )
    parser.add_argument(
        "--socket",
        default=None,
        help="KiCad 10 IPC API socket path (overrides KICAD_API_SOCKET).",
    )
    parser.add_argument(
        "--kicad-cli",
        dest="kicad_cli",
        default=None,
        help="Path to the kicad-cli executable (overrides KICAD_CLI).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for `kicad10-mcp`."""
    args = _parse_args(argv)

    cfg = KiCadConfig()
    if args.read_only:
        cfg.read_only = True
    if args.socket:
        cfg.socket_path = args.socket
    if args.kicad_cli:
        cfg.kicad_cli = args.kicad_cli
    set_config(cfg)

    server = create_server()
    transport: Literal["stdio", "streamable-http"] = args.transport
    if transport == "streamable-http":
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()


# Keep get_config exported for convenience
__all__ = ["create_server", "get_config", "main"]
