"""Subprocess helpers shared by all collectors.

Every external command (PowerShell, smartctl, nvidia-smi, powercfg) goes
through this module so that:

* there is exactly one place that applies timeouts, hides console windows
  and decodes output;
* nothing here ever raises: failures come back as ``None`` and an optional
  ``on_error`` callback receives a human-readable reason;
* tests can monkeypatch a handful of functions (``is_windows``,
  ``run_powershell``, ``run_powershell_json``, ``run_command``, ``which``)
  to feed canned Windows output on Linux.

Added in v0.1.0
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any, Callable, Optional, Sequence

#: Callback type used to report why a command produced no data.
ErrorCallback = Optional[Callable[[str], None]]

#: Default timeout for PowerShell calls, in seconds. Windows PowerShell 5.1
#: takes ~0.5-1.5 s just to start, and CIM queries can stall on a busy disk,
#: so this is generous but still bounded.
DEFAULT_PS_TIMEOUT = 20.0


def is_windows() -> bool:
    """Return True when running on Windows.

    Wrapped in a function (rather than a module constant) so tests can
    monkeypatch it and exercise the Windows code paths on Linux.
    """
    return sys.platform == "win32"


def which(name: str) -> Optional[str]:
    """Return the full path to an executable on PATH, or None."""
    return shutil.which(name)


def _report(on_error: ErrorCallback, message: str) -> None:
    """Send ``message`` to the callback if one was given."""
    if on_error is not None:
        on_error(message)


def _creation_flags() -> int:
    """Subprocess flags that stop a console window flashing up on Windows."""
    # CREATE_NO_WINDOW only exists on Windows builds of Python.
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if is_windows() else 0


def run_command(
    args: Sequence[str],
    timeout: float = 15.0,
    on_error: ErrorCallback = None,
    accept_returncodes: Sequence[int] = (0,),
) -> Optional[str]:
    """Run a command and return its stdout as text, or None on failure.

    ``accept_returncodes`` exists because some tools use non-zero exit codes
    for "worked, but found a problem" (smartctl sets bit flags in its exit
    status when a disk has logged errors, yet the JSON output is still valid).
    """
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            timeout=timeout,
            creationflags=_creation_flags(),
        )
    except FileNotFoundError:
        _report(on_error, f"{args[0]} not found")
        return None
    except subprocess.TimeoutExpired:
        _report(on_error, f"{args[0]} timed out after {timeout:.0f}s")
        return None
    except OSError as exc:  # permissions, bad executable, etc.
        _report(on_error, f"{args[0]} failed: {exc}")
        return None
    if proc.returncode not in accept_returncodes:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        detail = err[-1] if err else f"exit code {proc.returncode}"
        _report(on_error, f"{args[0]} failed: {detail}")
        return None
    # errors="replace": Windows tools sometimes emit OEM code-page bytes.
    return proc.stdout.decode("utf-8", "replace")


def run_powershell(
    script: str,
    timeout: float = DEFAULT_PS_TIMEOUT,
    on_error: ErrorCallback = None,
) -> Optional[str]:
    """Run a PowerShell snippet and return stdout, or None.

    Returns None immediately on non-Windows systems (no error is reported,
    because "not applicable" is not a failure).

    Uses Windows PowerShell 5.1 (``powershell.exe``), which ships with every
    Windows 10/11 install, rather than PowerShell 7 (``pwsh``), which is
    optional. ``-NoProfile`` keeps user profiles from slowing startup or
    printing banners into our output.
    """
    if not is_windows():
        return None
    exe = which("powershell") or which("pwsh")
    if exe is None:
        _report(on_error, "PowerShell not found on PATH")
        return None
    # Force UTF-8 output: the default console encoding is the OEM code page,
    # which mangles non-ASCII device names (e.g. a "Ü" in a disk model).
    prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; "
    return run_command(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", prefix + script],
        timeout=timeout,
        on_error=on_error,
    )


def run_powershell_json(
    script: str,
    timeout: float = DEFAULT_PS_TIMEOUT,
    on_error: ErrorCallback = None,
) -> Any:
    """Run a PowerShell snippet that ends in ``ConvertTo-Json`` and parse it.

    Returns the decoded JSON value, or None if PowerShell is unavailable,
    failed, printed nothing, or printed something that isn't JSON.
    """
    out = run_powershell(script, timeout=timeout, on_error=on_error)
    if out is None:
        return None
    out = out.strip().lstrip("\ufeff")  # strip a UTF-8 BOM if PowerShell added one
    if not out:
        _report(on_error, "PowerShell returned no data")
        return None
    try:
        return json.loads(out)
    except ValueError:
        _report(on_error, "PowerShell output was not valid JSON")
        return None


def as_list(value: Any) -> list:
    """Normalise ConvertTo-Json output to a list.

    PowerShell quirk: a pipeline that yields one object is serialised as a
    bare JSON object, not a one-element array, and an empty pipeline yields
    nothing at all. Callers always want a list.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
