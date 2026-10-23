# Changelog

All notable changes to this project are documented here.

## Unreleased

- Docs: Quick start section added to the top of the README (2026-10-23). Step-by-step Windows
  PowerShell guide: prerequisites, clone, virtual environment (with the execution-policy tip),
  install, tests, scan options, where reports go, when to use Administrator, demos and compare.
  It replaces the old Quick start and "Where reports go" sections.

## v0.1.0 (2026-10-22)

First release.

- Data models (`pcdiag/models.py`): typed dataclasses for OS, CPU, memory (with RAM modules),
  disks (with SMART health and bus type), disk I/O, GPU, temperatures, battery, processes,
  startup items, benchmark results and the aggregate `SystemSnapshot`, with `to_dict()` /
  `from_dict()` / JSON round-tripping. Every possibly-missing field is optional, and
  collector failures are recorded in `SystemSnapshot.errors`.
- Example snapshots (`pcdiag/fixtures.py`): healthy gaming PC, slow old laptop, failing-disk machine.
- Collectors (`pcdiag/collectors/`): OS, CPU, memory, disks, disk I/O, GPU, temperatures,
  battery, processes and startup items. Windows sources: `Get-CimInstance`
  (Win32_PhysicalMemory, Win32_VideoController, MSAcpi_ThermalZoneTemperature),
  `Get-PhysicalDisk` / `Get-StorageReliabilityCounter`, `powercfg /batteryreport`, registry
  Run keys and Startup folders. Also `smartctl` and `nvidia-smi` when installed. All
  PowerShell calls go through one helper with timeouts; collectors never raise, and
  Windows-only parts degrade gracefully on other systems. `collect_snapshot()` runs them all.
- Benchmark (`pcdiag/benchmark.py`): quick pure-Python CPU single/multi-core, sequential disk
  read/write (temporary file, deleted afterwards) and memory copy. Scores are relative.
- CLI (`python -m pcdiag` / `pcdiag`): `scan` (default; `--benchmark`, `--json`, `--quick`),
  `demo <fixture>` and `compare old.json new.json`. Saves `reports/snapshot-*.json` and
  `reports/report-*.html` and prints the verdict, health score and top findings.
- Analysis engine: `Finding` model, rule registry and `analyze()` with 16 rules (RAM pressure/low RAM, HDD system drive, disk space, SMART health, disk busy, CPU/GPU thermals and throttling, CPU busy, startup apps, heavy processes, GPU driver age, uptime, battery wear, collector errors, data coverage). Unknown data is never reported as healthy.
- Upgrade recommender: `recommend_upgrades()` ranks purchases by impact (failing disk first and urgent, then HDD->SSD, RAM, bigger drive, battery, GPU), with free fixes always listed first.
- Reports: `render_html()` (self-contained, dark-mode aware, escaped) and `render_markdown()` with verdict banner, 0-100 health score, metric cards, free fixes, ranked upgrades and findings grouped by severity.
- Tests for analysis, recommender and reports (60 new tests).
- Tests for models, collectors (Windows parsing tested on Linux with canned PowerShell,
  powercfg, smartctl and registry data), benchmark and CLI, including a real
  `python -m pcdiag scan --json` smoke test.
- Analysis follow-up: findings name the detected OS ("Linux runs from a hard disk drive") or say
  "Your operating system" when unknown; SMART `read_errors` > 0 is a warning; new USB system-drive
  rule (`storage.usb_system_drive`); disk-busy rule prefers per-device `disk_io` entries and uses the
  all-disks entry (`device=None`) at most once; Windows unknowns (busy %, max frequency, load average)
  are treated as unknown, not faults.
- Collector errors mentioning "Administrator" are shown as grey "Can't tell ... (needs Administrator)"
  info items, and the matching report cards read "Unknown - run as Administrator".
- Benchmark scores are no longer judged against fixed thresholds; reports show them as raw numbers
  with a note that they are only for comparing runs on the same PC. Disk bus type shown in the overview.
- Follow-up tests for OS wording, benchmarks, read errors, USB boot drives, Windows data shapes and
  Administrator errors (34 new tests).
- Continuous integration: GitHub Actions workflow (`.github/workflows/tests.yml`) runs pytest on
  windows-latest and ubuntu-latest with Python 3.10 and 3.13, on push, pull request, monthly
  (1st of the month, 09:00 UTC) and on demand.
