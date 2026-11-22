"""Configuration and environment discovery for kicad10-mcp."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_kicad_config_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "kicad" / "10.0"
    if system == "Darwin":
        return Path.home() / "Library" / "Preferences" / "kicad" / "10.0"
    xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(xdg) / "kicad" / "10.0"


def _discover_lib_dirs(env_vars: list[str], candidates: list[Path]) -> list[Path]:
    dirs: list[Path] = []
    seen: set[Path] = set()
    for var in env_vars:
        val = os.environ.get(var)
        if val:
            for part in val.split(os.pathsep):
                p = Path(part).expanduser()
                if p.is_dir() and p not in seen:
                    dirs.append(p)
                    seen.add(p)
    for p in candidates:
        if p.is_dir() and p not in seen:
            dirs.append(p)
            seen.add(p)
    return dirs


@dataclass
class KiCadConfig:
    """Runtime settings for the KiCad 10 MCP server."""

    kicad_cli: str = field(
        default_factory=lambda: os.environ.get("KICAD_CLI")
        or shutil.which("kicad-cli")
        or "kicad-cli"
    )
    socket_path: str | None = field(default_factory=lambda: os.environ.get("KICAD_API_SOCKET"))
    api_token: str | None = field(default_factory=lambda: os.environ.get("KICAD_API_TOKEN"))
    ipc_timeout_ms: int = field(
        default_factory=lambda: int(os.environ.get("KICAD_IPC_TIMEOUT_MS", "5000"))
    )
    cli_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("KICAD_CLI_TIMEOUT_S", "120"))
    )
    read_only: bool = field(default_factory=lambda: _env_bool("KICAD_READ_ONLY", False))
    config_dir: Path = field(default_factory=_default_kicad_config_dir)
    symbol_lib_dirs: list[Path] = field(
        default_factory=lambda: _discover_lib_dirs(
            ["KICAD10_SYMBOL_DIR", "KICAD_SYMBOL_DIR", "KICAD9_SYMBOL_DIR"],
            [
                Path("/usr/share/kicad/symbols"),
                Path("/usr/local/share/kicad/symbols"),
                Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
                Path.home() / ".local" / "share" / "kicad" / "10.0" / "symbols",
            ],
        )
    )
    footprint_lib_dirs: list[Path] = field(
        default_factory=lambda: _discover_lib_dirs(
            ["KICAD10_FOOTPRINT_DIR", "KICAD_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR"],
            [
                Path("/usr/share/kicad/footprints"),
                Path("/usr/local/share/kicad/footprints"),
                Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"),
                Path.home() / ".local" / "share" / "kicad" / "10.0" / "footprints",
            ],
        )
    )

    def ensure_writable(self, action: str) -> None:
        """Raise PermissionError if the server is running in read-only mode."""
        if self.read_only:
            raise PermissionError(
                f"Cannot perform '{action}' while KICAD_READ_ONLY is enabled. "
                "Restart the MCP server without KICAD_READ_ONLY=1 to allow edits."
            )

    def expand_kicad_path(self, raw_path: str, project_dir: Path | None = None) -> Path:
        """Expand KiCad path variables like ${KICAD10_SYMBOL_DIR} or ${KIPRJMOD}."""
        expanded = raw_path
        replacements: dict[str, str] = {}
        if self.symbol_lib_dirs:
            sym = str(self.symbol_lib_dirs[0])
            replacements["KICAD10_SYMBOL_DIR"] = sym
            replacements["KICAD9_SYMBOL_DIR"] = sym
            replacements["KICAD_SYMBOL_DIR"] = sym
        if self.footprint_lib_dirs:
            fp = str(self.footprint_lib_dirs[0])
            replacements["KICAD10_FOOTPRINT_DIR"] = fp
            replacements["KICAD9_FOOTPRINT_DIR"] = fp
            replacements["KICAD_FOOTPRINT_DIR"] = fp
        if project_dir is not None:
            replacements["KIPRJMOD"] = str(project_dir)

        for key, val in os.environ.items():
            replacements.setdefault(key, val)

        for key, val in replacements.items():
            expanded = expanded.replace(f"${{{key}}}", val)
        return Path(expanded).expanduser()


# Singleton default configuration (can be overridden in tests)
_CONFIG: KiCadConfig | None = None


def get_config() -> KiCadConfig:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = KiCadConfig()
    return _CONFIG


def set_config(config: KiCadConfig) -> None:
    global _CONFIG
    _CONFIG = config
