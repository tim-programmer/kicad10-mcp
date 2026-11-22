"""Central registration for all kicad10-mcp tool modules."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from kicad_mcp.tools.cli_fab import register_cli_fab_tools
from kicad_mcp.tools.cli_verify import register_cli_verify_tools
from kicad_mcp.tools.ipc_edit import register_ipc_edit_tools
from kicad_mcp.tools.ipc_inspect import register_ipc_inspect_tools
from kicad_mcp.tools.libraries import register_library_tools
from kicad_mcp.tools.project import register_project_tools
from kicad_mcp.tools.schematic import register_schematic_tools


def register_all_tools(server: MCPServer) -> None:
    """Register every KiCad 10 MCP tool module on `server`."""
    register_project_tools(server)
    register_ipc_inspect_tools(server)
    register_ipc_edit_tools(server)
    register_cli_verify_tools(server)
    register_cli_fab_tools(server)
    register_schematic_tools(server)
    register_library_tools(server)
