"""Async subprocess bridge for KiCad 10 `kicad-cli`."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kicad_mcp.config import KiCadConfig, get_config


class KiCadCLIError(RuntimeError):
    """Raised when `kicad-cli` fails or cannot be invoked."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
        command: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.command = command or []


@dataclass
class CLIResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def _cli_env() -> dict[str, str]:
    """Ensure kicad-cli has writable HOME and XDG dirs even in restricted environments."""
    env = dict(os.environ)
    tmp_home = Path(tempfile.gettempdir()) / "kicad10-mcp-home"

    def _ensure_writable_dir(env_key: str, default_path: Path, fallback_sub: str) -> None:
        raw = env.get(env_key)
        target = Path(raw) if raw else default_path
        # Check whether target or its nearest existing ancestor is writable
        probe = target
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        if not os.access(probe, os.W_OK):
            fb = tmp_home / fallback_sub if fallback_sub else tmp_home
            fb.mkdir(parents=True, exist_ok=True)
            env[env_key] = str(fb)

    home_raw = env.get("HOME", "")
    home_path = Path(home_raw) if home_raw else Path("/")
    if not home_raw or not os.access(home_path, os.W_OK):
        tmp_home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(tmp_home)
        home_path = tmp_home

    _ensure_writable_dir("XDG_DATA_HOME", home_path / ".local" / "share", "data")
    _ensure_writable_dir("XDG_CONFIG_HOME", home_path / ".config", "config")
    _ensure_writable_dir("XDG_CACHE_HOME", home_path / ".cache", "cache")
    return env


class KiCadCLI:
    """Wrapper for executing KiCad 10 `kicad-cli` commands asynchronously."""

    def __init__(self, config: KiCadConfig | None = None) -> None:
        self._config = config or get_config()

    @property
    def binary(self) -> str:
        return self._config.kicad_cli

    async def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        timeout_s: float | None = None,
        check: bool = True,
        allow_exit_codes: set[int] | None = None,
    ) -> CLIResult:
        """Execute `kicad-cli <args>` and return captured output."""
        cmd = [self.binary, *args]
        timeout = timeout_s if timeout_s is not None else self._config.cli_timeout_s

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd) if cwd is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_cli_env(),
            )
        except FileNotFoundError as exc:
            raise KiCadCLIError(
                f"kicad-cli executable not found at '{self.binary}'. "
                "Install KiCad 10 or set KICAD_CLI to the full path of kicad-cli.",
                command=cmd,
            ) from exc

        try:
            raw_out, raw_err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise KiCadCLIError(
                f"kicad-cli timed out after {timeout:.0f}s: {' '.join(cmd)}",
                command=cmd,
            ) from exc

        stdout = raw_out.decode("utf-8", errors="replace").strip()
        stderr = raw_err.decode("utf-8", errors="replace").strip()
        code = proc.returncode if proc.returncode is not None else -1
        allowed = allow_exit_codes if allow_exit_codes is not None else {0}

        if check and code not in allowed:
            detail = stderr or stdout or f"exit code {code}"
            raise KiCadCLIError(
                f"kicad-cli failed (exit {code}): {detail}",
                returncode=code,
                stdout=stdout,
                stderr=stderr,
                command=cmd,
            )

        return CLIResult(command=cmd, returncode=code, stdout=stdout, stderr=stderr)

    async def version(self, fmt: str = "plain") -> str:
        """Return the installed `kicad-cli` version string."""
        res = await self.run(["version", "--format", fmt])
        # Some KiCad builds log wx warnings on stdout/stderr; the last non-empty line is the version
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        return lines[-1] if lines else res.stdout

    async def run_json_report(
        self,
        args_builder: Any,
        *,
        output_file: str | Path | None = None,
        allow_exit_codes: set[int] | None = None,
    ) -> tuple[dict[str, Any], CLIResult, Path | None]:
        """Run a `kicad-cli` command that writes a JSON file and return parsed JSON.

        `args_builder` is a callable accepting `output_path: Path` and returning `list[str]`.
        """
        if output_file is not None:
            out_path = Path(output_file).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            res = await self.run(
                args_builder(out_path),
                allow_exit_codes=allow_exit_codes,
            )
            data = json.loads(out_path.read_text(encoding="utf-8"))
            return data, res, out_path

        with tempfile.TemporaryDirectory(prefix="kicad10-mcp-") as tmpdir:
            out_path = Path(tmpdir) / "report.json"
            res = await self.run(
                args_builder(out_path),
                allow_exit_codes=allow_exit_codes,
            )
            data = json.loads(out_path.read_text(encoding="utf-8"))
            return data, res, None


def append_define_vars(args: list[str], define_vars: dict[str, str] | None) -> None:
    """Append repeated `-D KEY=VALUE` flags to a `kicad-cli` argument list."""
    if not define_vars:
        return
    for key, value in define_vars.items():
        args.extend(["-D", f"{key}={value}"])


def append_variants(args: list[str], variants: str | list[str] | None) -> None:
    """Append KiCad 10 `--variant` flags to a `kicad-cli` argument list."""
    if not variants:
        return
    if isinstance(variants, str):
        args.extend(["--variant", variants])
    else:
        for v in variants:
            if v:
                args.extend(["--variant", v])
