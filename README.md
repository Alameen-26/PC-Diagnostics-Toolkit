<!-- Added in v0.2.0: "Made by NiyiSoft" mark (switches to the light version in dark mode) -->
<p align="right">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/niyisoft-badge-light.svg">
    <img src="docs/brand/niyisoft-badge.svg" alt="Made by NiyiSoft" width="140">
  </picture>
</p>

# PC Diagnostics Toolkit

A Python tool that explains **what is slowing a PC down**, spots **likely hardware faults**
(failing drives, worn batteries, overheating), and recommends **upgrades that would actually
help**, with free fixes listed first. Windows first; cross-platform wherever
[psutil](https://github.com/giampaolo/psutil) allows (tests run on Linux).

<!-- Added in v0.3.0 -->
## Download (Windows app, no Python needed)

Get the free portable app from the [Releases page](https://github.com/Alameen-26/PC-Diagnostics-Toolkit/releases/latest):
download `PCDiagnosticsToolkit-<version>-windows-x64.exe` and double-click it (Windows 10/11, 64-bit, no install).
It isn't code-signed yet, so if SmartScreen warns, click **More info**, then **Run anyway**. Run it as
Administrator for the full set of checks. The SHA256 checksum is listed with each release.

## Quick start (Windows PowerShell)

**You need:** [Git](https://git-scm.com/download/win) and [Python 3.10 or newer](https://www.python.org/downloads/)
(tick "Add python.exe to PATH" in the installer). Open **PowerShell** and paste one block at a time.

**1. Download and install**

```powershell
git clone https://github.com/Alameen-26/PC-Diagnostics-Toolkit.git
cd PC-Diagnostics-Toolkit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

If PowerShell refuses to run `Activate.ps1`, run this once and then activate again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Next time, just `cd` into the folder
and run `.\.venv\Scripts\Activate.ps1` before using the tool.

**2. Run the tests** (optional, checks the install)

```powershell
pytest -q
```

<!-- Changed in v0.3.0: test count -->
You should see about 365 tests pass.

**3. Scan your PC**

```powershell
python -m pcdiag scan               # normal scan, about 5 s
python -m pcdiag scan --benchmark   # also run a ~20 s speed test
python -m pcdiag scan --quick       # faster, noisier sampling; skips smartctl
python -m pcdiag scan --json > snap.json   # raw data only, saves no report files
python -m pcdiag scan --monitor 300 # watch the PC for 5 minutes first, then scan
```

`pcdiag` on its own is a shortcut for `python -m pcdiag scan`. Other scan options:
`--output-dir DIR`, `--no-save` (write no files) and `--bench-disk-mb N` (size of the
benchmark's temporary file; default 256, 0 skips the disk test).

**4. Open the report**

Each scan saves two files in a `reports` folder inside the folder you ran it from:

- `report-YYYYmmdd-HHMMSS.html`: the readable report; open it in any browser
- `snapshot-YYYYmmdd-HHMMSS.json`: the raw data, used by `compare` later

```powershell
start reports
```

For full drive health (wear, temperature, power-on hours, SMART), run PowerShell with
**"Run as administrator"**. Without admin those items show as "Can't tell" instead of guessing.
Battery wear works without admin. Details are in [Which checks need Administrator](#which-checks-need-administrator).

**5. Try the built-in examples** (no scanning; made-up example PCs)

```powershell
python -m pcdiag demo healthy_gaming_pc
python -m pcdiag demo slow_old_laptop
python -m pcdiag demo failing_disk_machine
```

Each demo prints its findings and saves `reports\report-demo-<name>.html`.

**6. Compare two scans** (e.g. this month vs last month)

```powershell
python -m pcdiag compare reports\snapshot-20261001-090000.json reports\snapshot-20261101-090000.json
```

Use your own file names from the `reports` folder: older scan first, newer scan second.

**7. Watch your PC live** (CPU, RAM, disk space and the busiest programs, refreshed every 2 s)

```powershell
python -m pcdiag monitor                    # 60 s, then a summary
python -m pcdiag monitor --duration 0       # run until you press Ctrl+C
python -m pcdiag monitor --duration 300 --interval 5
python -m pcdiag demo slow_old_laptop --monitor   # example report with 5 minutes of readings
```

Each run saves `reports\monitor-YYYYmmdd-HHMMSS.json` (`--no-save` to skip, `--json` to print it).
Readings Windows can't provide (for example CPU temperature) show as "unavailable", never as 0.
Run PowerShell as Administrator to include system processes in the busiest-programs list.
`scan --monitor 300` is the best way to catch problems that come and go: the report then tells
short spikes apart from constant load.

<!-- Added in v0.3.0: plan and troubleshoot -->
**8. Get your improvement plan** (free fixes first, then cheap upgrades; advice only)

```powershell
python -m pcdiag plan                          # scan, then a step-by-step plan
python -m pcdiag plan --monitor                # watch for 5 minutes first (more reliable)
python -m pcdiag plan --demo slow_old_laptop   # example plan, no scanning
```

Each plan step says why, what pcdiag saw, roughly what it costs and whether an upgrade fits your
PC ("Fits?": yes, no or can't tell). It saves `reports\plan-YYYYmmdd-HHMMSS.md`
(`--no-save` to skip, `--json` to print it, `--snapshot FILE` to plan from an earlier scan).
The same plan appears in the HTML report.

**9. Troubleshoot a symptom** (ordered checks with clear stop points)

```powershell
python -m pcdiag troubleshoot slow-boot --scan   # also: restarts, freezes, overheating, disk-noise
```

`--scan` checks this PC first and marks the steps it found evidence for.

## Features

- **Live scan** of CPU (model, cores, clocks, ~1 s usage sample), RAM and swap
  (plus stick count, slots, speed and DDR type on Windows), every drive (free space, SSD / HDD /
  NVMe, Windows health status, wear, temperature, power-on hours, SMART via `smartctl` if
  installed), disk I/O and busy %, GPU (name, VRAM, driver version and date; usage and
  temperature via `nvidia-smi`), temperatures, battery wear and cycle count
  (`powercfg /batteryreport`), the heaviest processes, and startup programs (registry Run
  keys + Startup folders, including Task Manager's enabled/disabled state).
- **Analysis**: 16 rules turn the data into plain-language findings (RAM pressure, hard-disk
  boot drive, full disks, failing drives, thermal throttling, too many startup apps, old GPU
  driver, worn battery, ...). Missing data is never reported as healthy.
<!-- Added in v0.3.0 -->
- **Improvement plan** (`pcdiag plan`): free fixes (startup apps, heavy programs, disk cleanup,
  graphics driver, power plan, Windows Update, cooling), then cheap upgrades, then a replacement
  only when upgrades can't help. Urgent steps (back up a failing drive) come first.
- **Upgrade compatibility**: soldered vs socketed RAM, maximum RAM, free slots, stick size,
  SATA vs NVMe, drive size, laptop vs desktop. Impossible upgrades are never suggested; unknowns
  say "can't tell".
- **Troubleshooting by symptom** (`pcdiag troubleshoot`): slow boot, restarts, freezes,
  overheating, drive noises.
- **Upgrade recommender** ranks purchases by expected impact (failing disk first and marked
  urgent, then HDD -> SSD, RAM, bigger drive, battery, GPU).
- **Reports**: a terminal summary with a verdict and a 0-100 health score, a self-contained
  HTML report, and the raw JSON snapshot.
- **Optional benchmark** (~10-20 s): CPU single/multi-core, sequential disk read/write and
  memory copy. Scores are **relative**, for comparing your own runs over time.
- **Compare** two scans (e.g. monthly) to see what got better or worse.

## Which checks need Administrator

Everything runs **without admin**. A normal PowerShell gives you nearly everything. A few
checks only show more detail when run from **"Run as administrator"** PowerShell:

| Check | Without admin | With admin |
|---|---|---|
| Drive wear %, temperature, power-on hours (`Get-StorageReliabilityCounter`) | Windows health status only | Full counters |
| SMART attributes via `smartctl` (optional install) | Usually unavailable | Reallocated / pending sectors |
| Temperatures (`MSAcpi_ThermalZoneTemperature`) | Usually unavailable | ACPI zones, if the PC's firmware provides them |
| Some system processes | Skipped | Included in the top-process list |

When something can't be read, the scan says so under "Not collected / limited" rather than
guessing. Per-core CPU temperatures on Windows need a kernel driver and are not supported.
<!-- Added in v0.3.0 -->
If [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) is running,
pcdiag reads its CPU temperature sensors (no admin needed for pcdiag itself).

## Privacy

Everything stays on your computer. The toolkit makes **no network requests**, sends no
telemetry, and only writes files into your `reports` folder (plus a temporary file during the
benchmark, which is deleted). Snapshots include your PC's hostname, process names and startup
commands, so check them before sharing.

## Layout

- `pcdiag/models.py`: dataclasses for every metric, with JSON round-tripping
- `pcdiag/collectors/`: one module per area; never raise, record problems in `snapshot.errors`.
  All PowerShell/external commands go through `collectors/_shell.py`.
- `pcdiag/analysis/`: rules, findings, the upgrade recommender, upgrade compatibility
  (`compat.py`) and the improvement plan (`plan.py`) <!-- Changed in v0.3.0 -->
- `pcdiag/troubleshoot.py`: symptom guides <!-- Added in v0.3.0 -->
- `pcdiag/report/`: HTML and Markdown renderers
- `pcdiag/benchmark.py`, `pcdiag/compare.py`, `pcdiag/terminal.py`, `pcdiag/__main__.py` (CLI)
- `pcdiag/fixtures.py`: example snapshots (healthy gaming PC, slow old laptop, failing disk)
- `tests/`: pytest suite; Windows parsing is tested on any OS using canned outputs in `tests/data/`

## Roadmap

One release every two months. The old roadmap items are folded into the releases below.

<!-- Changed in v0.3.0: marked released -->
- **v0.3.0 (released 2026-11-02) - Make my PC better without buying a new one:** one free -> cheap -> replace
  plan; upgrade compatibility (soldered vs socketed RAM, max RAM, free slots, desktop vs no battery);
  guided troubleshooting by symptom; CPU-sensor flag on temperatures and a GPU-driver age check.
- **v0.4.0 (January 2027) - Remember and hand off:** automatic scan history and baselines
  ("what changed since last time"); an IT-support ticket export; optional post-scan feedback saved
  locally; a portable `.exe` with a checksum.
- **v0.5.0 (March 2027) - Show the working:** a "Why this score" breakdown with per-area
  subscores, `METHODOLOGY.md` and a no-scareware charter; crash evidence from the Event Log and
  Reliability Monitor, with a memory-test hint; a benchmark context guard (battery, power plan,
  pending Windows updates); `--redact`, `NO_COLOR`/`--plain` and accessibility fixes.
- **v0.6.0 (May 2027) - See it at a glance:** a local, offline dashboard with history charts; an
  optional short stress and throttle test; a multi-PC summary from a folder of snapshots.

## License

Copyright (c) 2026 Alameen-26. All rights reserved. Viewing permitted; no license granted for reuse.
