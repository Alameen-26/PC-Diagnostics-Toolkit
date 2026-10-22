# PC Diagnostics Toolkit

A Python tool that explains **what is slowing a PC down**, spots **likely hardware faults**
(failing drives, worn batteries, overheating), and recommends **upgrades that would actually
help**, with free fixes listed first. Windows first; cross-platform wherever
[psutil](https://github.com/giampaolo/psutil) allows (tests run on Linux).

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
- **Upgrade recommender** ranks purchases by expected impact (failing disk first and marked
  urgent, then HDD -> SSD, RAM, bigger drive, battery, GPU).
- **Reports**: a terminal summary with a verdict and a 0-100 health score, a self-contained
  HTML report, and the raw JSON snapshot.
- **Optional benchmark** (~10-20 s): CPU single/multi-core, sequential disk read/write and
  memory copy. Scores are **relative**, for comparing your own runs over time.
- **Compare** two scans (e.g. monthly) to see what got better or worse.

## Quick start (Windows PowerShell)

```powershell
cd pc-diagnostics-toolkit
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e ".[dev]"

pcdiag                            # same as: python -m pcdiag scan
pcdiag scan --benchmark           # add the ~20 s relative benchmark
pcdiag scan --json > snap.json    # JSON only, writes no files
pcdiag demo slow_old_laptop       # try it on a built-in example PC
pcdiag compare reports\snapshot-20261001-090000.json reports\snapshot-20261101-090000.json
pytest                            # run the test suite
```

Other options: `scan --quick` (shorter sampling, skips smartctl), `--output-dir DIR`,
`--no-save`, and `--bench-disk-mb N` (size of the benchmark's temporary file; default 256,
0 skips the disk test).

### Where reports go

Each `scan` writes two files to `.\reports\` (relative to where you run it):

- `snapshot-YYYYmmdd-HHMMSS.json`: the raw data, for `pcdiag compare` later
- `report-YYYYmmdd-HHMMSS.html`: the readable report; open it in any browser

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

## Privacy

Everything stays on your computer. The toolkit makes **no network requests**, sends no
telemetry, and only writes files into your `reports` folder (plus a temporary file during the
benchmark, which is deleted). Snapshots include your PC's hostname, process names and startup
commands, so check them before sharing.

## Layout

- `pcdiag/models.py`: dataclasses for every metric, with JSON round-tripping
- `pcdiag/collectors/`: one module per area; never raise, record problems in `snapshot.errors`.
  All PowerShell/external commands go through `collectors/_shell.py`.
- `pcdiag/analysis/`: rules, findings and the upgrade recommender
- `pcdiag/report/`: HTML and Markdown renderers
- `pcdiag/benchmark.py`, `pcdiag/compare.py`, `pcdiag/terminal.py`, `pcdiag/__main__.py` (CLI)
- `pcdiag/fixtures.py`: example snapshots (healthy gaming PC, slow old laptop, failing disk)
- `tests/`: pytest suite; Windows parsing is tested on any OS using canned outputs in `tests/data/`

## Roadmap

- Soldered vs socketed RAM detection (so we don't suggest adding RAM to a laptop that can't take it)
- Desktop vs "no battery detected" detection
- Averaging readings over time (several samples instead of one snapshot)
- Latest-GPU-driver check (compare against the vendor's current release)
- Windows power plan check (e.g. "Power saver" on a desktop)
- Pending Windows updates
- CPU-sensor flag on temperature readings (tell CPU sensors apart from board/ACPI zones)

## License

Copyright (c) 2026 Alameen-26. All rights reserved. Viewing permitted; no license granted for reuse.
