"""GPU collector: adapter name, VRAM, driver version/date, usage and temperature.

* Windows: ``Win32_VideoController`` (no admin needed).
* Any OS: ``nvidia-smi`` when an NVIDIA driver is installed, which adds
  live usage and temperature and an accurate VRAM figure.

AMD/Intel live usage needs vendor tools or Windows performance counters and
is out of scope for v0.1.0, so those fields stay None.

Added in v0.1.0
"""
from __future__ import annotations

from typing import Any, Optional

from ..models import GPUInfo, SystemSnapshot
from . import _shell
from ._util import to_float, to_int

# DriverDate is a CIM datetime; formatting it inside PowerShell avoids the
# PS 5.1 "/Date(1234567890000)/" JSON encoding.
_PS_GPU = r"""
$v = @(Get-CimInstance Win32_VideoController | ForEach-Object {
  [pscustomobject]@{
    Name = $_.Name; AdapterRAM = $_.AdapterRAM; DriverVersion = $_.DriverVersion
    DriverDate = if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { $null }
  }
})
ConvertTo-Json -Depth 3 -Compress -InputObject $v
"""

# Virtual/remote adapters that aren't real GPUs.
_VIRTUAL = ("microsoft basic display", "microsoft remote display", "parsec", "virtual display", "citrix")

_NVIDIA_QUERY = "name,memory.total,driver_version,utilization.gpu,temperature.gpu"


def guess_integrated(name: str) -> Optional[bool]:
    """Heuristic: is this an integrated (shares system RAM) GPU?

    Intel graphics are integrated except the Arc line; AMD's iGPUs are named
    plain "Radeon Graphics" / "Vega N Graphics" without a model number.
    """
    n = name.lower()
    if "intel" in n:
        return "arc" not in n
    if "nvidia" in n or "geforce" in n or "quadro" in n or "rtx" in n:
        return False
    if "radeon" in n:
        return n.rstrip().endswith("graphics") and not any(c.isdigit() for c in n.replace("vega", ""))
    return None


def parse_win32_video(data: Any) -> list[GPUInfo]:
    """Parse the Win32_VideoController JSON into GPUInfo objects."""
    gpus = []
    for raw in _shell.as_list(data):
        if not isinstance(raw, dict) or not raw.get("Name"):
            continue
        name = str(raw["Name"]).strip()
        if name.lower().startswith(_VIRTUAL):
            continue
        ram = to_int(raw.get("AdapterRAM"))
        gpus.append(GPUInfo(
            name=name,
            # Windows quirk: AdapterRAM is a 32-bit field, so cards with
            # 4 GB or more report ~4095 MB (or a negative/garbage value).
            # Keep it as a lower bound; nvidia-smi replaces it when present.
            vram_mb=ram // (1024 * 1024) if ram and ram > 0 else None,
            driver_version=raw.get("DriverVersion") or None,
            driver_date=raw.get("DriverDate") or None,
            is_integrated=guess_integrated(name),
        ))
    return gpus


def parse_nvidia_smi(text: str) -> list[GPUInfo]:
    """Parse ``nvidia-smi --query-gpu=... --format=csv,noheader,nounits``."""
    gpus = []
    for line in text.strip().splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 5:
            continue
        # nvidia-smi prints "[N/A]" or "[Not Supported]" for missing values;
        # to_float/to_int turn those into None.
        gpus.append(GPUInfo(
            name=cols[0], vram_mb=to_int(cols[1]), driver_version=cols[2] or None,
            usage_percent=to_float(cols[3]), temperature_c=to_float(cols[4]), is_integrated=False,
        ))
    return gpus


def _merge_nvidia(gpus: list[GPUInfo], nvidia: list[GPUInfo]) -> list[GPUInfo]:
    """Overlay nvidia-smi readings onto matching Win32 entries (or append)."""
    for nv in nvidia:
        match = next((g for g in gpus if g.name and nv.name and nv.name.lower() in g.name.lower()), None)
        if match is None:
            gpus.append(nv)
            continue
        match.vram_mb = nv.vram_mb or match.vram_mb  # nvidia-smi has no 4 GB cap
        match.usage_percent = nv.usage_percent
        match.temperature_c = nv.temperature_c
        match.is_integrated = False
        # Prefer nvidia-smi's driver number ("566.14"): it's the one NVIDIA's
        # download page and release notes use. Windows reports an internal
        # version ("32.0.15.6614") for the same driver.
        match.driver_version = nv.driver_version or match.driver_version
    return gpus


def collect(snapshot: SystemSnapshot) -> list[GPUInfo]:
    """Collect GPU information from every available source."""
    gpus: list[GPUInfo] = []
    errors: list[str] = []
    if _shell.is_windows():
        gpus = parse_win32_video(_shell.run_powershell_json(_PS_GPU, on_error=errors.append))
        if not gpus:
            snapshot.add_error("gpu", "Win32_VideoController unavailable: " + ("; ".join(errors) or "no data"))
    smi = _shell.which("nvidia-smi")
    if smi:
        out = _shell.run_command([smi, f"--query-gpu={_NVIDIA_QUERY}", "--format=csv,noheader,nounits"],
                                 timeout=10, on_error=errors.append)
        if out:
            gpus = _merge_nvidia(gpus, parse_nvidia_smi(out))
    if not gpus and not _shell.is_windows():
        snapshot.add_error("gpu", "no GPU details on this platform (only nvidia-smi is supported outside Windows)")
    return gpus
