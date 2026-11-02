# Changelog

All notable changes to this project are documented here.

## Unreleased

- Nothing yet.

<!-- Changed in v0.3.0: release dated; the Quick start note moved here from Unreleased -->
## v0.3.0 (2026-11-02)

Theme: make my PC better without buying a new one. Advice only: pcdiag never changes settings or buys anything.

- Docs: Quick start section added to the top of the README (2026-10-23). Step-by-step Windows
  PowerShell guide: prerequisites, clone, virtual environment (with the execution-policy tip),
  install, tests, scan options, where reports go, when to use Administrator, demos and compare.
  It replaces the old Quick start and "Where reports go" sections.

- Docs (Explained in v0.3.0): new `docs/HOW_IT_WORKS.md` (architecture diagram, scan flow, each module
  in plain English, design decisions, test layout, known limitations) and beginner-friendly comments
  in the key modules explaining what each part does and why. Comments only; no behaviour changes.

- New `pcdiag plan` command: one prioritized improvement plan, free fixes first, then cheap upgrades,
  then (only when upgrades can't help) a replacement. Every step has a reason, the evidence it came
  from, a cost range, a confidence level and, for upgrades, a "Fits?" compatibility check.
  Options: `--demo FIXTURE`, `--snapshot FILE`, `--monitor [S]` (watch first, default 300 s; the plan
  then uses the sustained readings), `--json`, `--output-dir DIR`, `--no-save`. Saves
  `reports/plan-YYYYmmdd-HHMMSS.md` (or `plan-demo-<fixture>.md`).
- Urgent steps come before everything else: a failing drive gives "Back up now" (a stop point) and
  "Replace the drive" ahead of any free fix.
- Free fixes: startup apps, programs hogging memory/CPU (Microsoft Defender is explained, never
  disabled), disk cleanup with a target free-space figure, outdated graphics driver (age from the
  driver date), power plan (Power saver while plugged in), Windows Update (pending restart and days since
  the last installed update), and cleaning vents for a hot CPU.
- Cheap upgrades: HDD -> SSD sized to the data in use, RAM, thermal paste, bigger drive, battery
  and GPU, with static price ranges (USD, October 2026).
- Upgrade compatibility (`pcdiag/analysis/compat.py`): soldered vs socketed RAM, the board's maximum
  RAM, free slots, stick size (DIMM/SODIMM) and DDR type (DDR3L for DDR3 laptops), connector
  (SATA/NVMe), drive size, and desktop vs no battery. An upgrade that can't fit is never suggested;
  anything Windows doesn't report is shown as "can't tell".
- New `pcdiag troubleshoot SYMPTOM` (slow-boot, restarts, freezes, overheating, disk-noise): ordered
  checks with stop points (e.g. back up before anything else for a noisy drive). `--scan`, `--demo`
  or `--snapshot` mark the steps pcdiag found evidence for and where to start.
- Reports: a "Your improvement plan" section in the HTML and Markdown reports and a short plan block
  at the end of the terminal output of `scan`/`demo`. The existing "Free fixes first" and
  "Upgrades worth buying" lists stay.
- Collectors: device type (laptop/desktop/all-in-one from the SMBIOS chassis), active power plan
  (`powercfg /getactivescheme`; built-in plans recognized by GUID on any Windows language), newest
  installed update and pending-restart flag, RAM form factor, maximum capacity (`MaxCapacityEx`) and
  soldered memory, and LibreHardwareMonitor CPU temperatures when it is running.
- Temperatures: `TemperatureReading.is_cpu` (CPU-sensor flag). The CPU temperature check uses only
  confirmed CPU sensors when the flag is set and says whether the sensor was confirmed or guessed
  from its label. Old snapshots without the new fields still load.
- Models: `DeviceInfo`, `SoftwareState`, new memory fields (`max_capacity_gb`, `form_factor`,
  `soldered`). Health scores and existing findings are unchanged.
- Fixtures: the demo PCs now include device type, power plan, update status and RAM details.
- Tests: `tests/test_compat.py`, `tests/test_plan.py`, `tests/test_v030_collectors.py`,
  `tests/test_v030_cli_reports.py` (330 tests in total).
- Deferred: detecting a free M.2 slot (Windows doesn't report empty slots, so it's "can't tell");
  crash evidence from the Event Log for `troubleshoot restarts` (planned for v0.5.0); live prices
  (static ranges for now).

### Review fixes

Found while reviewing the v0.3.0 build; all fixed before release.

- Plan: a missing reading no longer makes the plan section disappear or `pcdiag plan` crash; it shows "?".
- Plan: Linux PCs no longer see "Windows" wording.
- Drives: an NVMe drive of unknown type is no longer told to "swap to SSD", and an unknown SATA drive
  no longer blames the M.2 slot. Replacement drive sizes now go up to 16 TB (were capped at 4 TB).
- Drives: a drive with only SMART warnings isn't headlined "failing", and a drive reporting OK but with
  reallocated sectors now gets a backup step.
- Plan: no more "Nothing needed" while warnings are uncovered; the replace-only headline reads naturally;
  "Couldn't check" now lists admin-blocked and uncollected items.
- Power plan: doesn't say "plugged in" when the charger state is unknown.
- RAM: a three-slot board gets real stick sizes (3 × 4 GB or 3 × 8 GB, never 3 × 5 GB).
- Battery: a desktop reporting a UPS battery no longer gets laptop battery advice.
- Reports: "What to do" no longer repeats steps already in the plan (including RAM on a soldered laptop).
- Temperature card: uses the same CPU-sensor check as the temperature rule.
- Monitor: only CPU sensors are logged as the CPU temperature; with none, it's recorded as unavailable
  (a graphics card or NVMe drive was sometimes logged as the CPU).
- RAM: no RAM upgrade is suggested when the RAM is soldered, in the terminal and JSON output too,
  not only in the reports.
- Cooling: the thermal paste step checks a throttling flag instead of matching words in the evidence.
- Markdown report: Markdown characters in names and other text are escaped, so they can't add bold,
  links or broken tables.
- Battery: says "check if your model's battery is user-replaceable" instead of claiming a new one fits.
- Tests: `tests/test_v030_review.py` and `tests/test_v030_review_fixes.py` (365 tests in total).

<!-- Changed in v0.3.0: v0.2.0 shipped to main together with v0.3.0 -->
## v0.2.0 (2026-11-02, released together with v0.3.0)

- Live monitoring (roadmap phase 2): new `pcdiag monitor` command samples CPU (overall and per core),
  RAM, swap, free space on every fixed drive, disk I/O and busy %, CPU temperature (where psutil exposes it;
  not on Windows) and the top processes by CPU and memory every `--interval` seconds (default 2, minimum 0.5)
  for `--duration` seconds (default 60; 0 runs until Ctrl+C). It shows a live view that redraws in place
  (one line per reading when output isn't a terminal), then a summary with average, p95, peak, % of time at or
  above the threshold, longest stretch, and the busiest programs. It saves `reports/monitor-YYYYmmdd-HHMMSS.json`
  (`--no-save` to skip, `--json` prints the session instead, `--output-dir DIR`, `--top N`).
- Terminal: when a snapshot has monitor data, scan/demo print an "Over time" block listing the sustained findings.
- Ctrl+C stops monitoring early and keeps the readings taken so far (the session is marked `interrupted`).
- Unavailable data is never a 0: a reading that fails or isn't supported is `None`, with a plain-language note
  on the sample and the session (e.g. no page file, CPU temperature not exposed, processes hidden by
  access denied - run as Administrator to include them).
- `scan --monitor S` (e.g. `scan --monitor 300`) watches the PC for S seconds before the normal scan and stores
  the session in the snapshot; `--monitor-interval` sets the spacing. The analysis' sustained-pressure checks and
  the report's "Over time" section use it.
- Models: `DiskUsageSample`, `MonitorSample`, `MonitorSession` (stats helpers `mean`, `min`, `max`, `p95`,
  `percent_above`, `longest_streak_above`, `stats`, `process_summary`; JSON round-trip), `MetricStats`,
  `ProcessSummary`, and the optional `SystemSnapshot.monitor` field. Old snapshot files without it still load.
- Sampler (`pcdiag/monitor.py`): `Monitor` / `run_monitor()`, with injectable psutil and clocks for testing.
- Fixtures: example monitor sessions for the three demo PCs (`ALL_MONITOR_FIXTURES`, `with_monitor(name)`),
  kept separate so the plain fixtures still have no monitor data. `demo <fixture> --monitor` uses them.
- Tests: `tests/test_monitor.py` (36 tests).
- Analysis: sustained-pressure checks in `pcdiag/analysis/sustained.py` read `scan --monitor` samples and tell
  brief spikes from sustained load. New findings: `memory.sustained` (RAM above 85% for at least 50% of the
  time or p95 at 90%+ is a warning; 20-50% of the time is info), `cpu.sustained` (90%+ for at least 50% of the
  time or one unbroken 60 s streak is a warning; bursts are info; spikes are info; names the top processes),
  `storage.disk_filling` (steady fall in free space of at least 0.5 GB with R^2 >= 0.7 over at least 60 s;
  projected full within 1 h is critical, within 24 h is a warning, otherwise info), and optionally
  `storage.disk_busy_sustained` and `thermal.cpu_sustained` when those samples have readings.
- Analysis: confidence comes from the monitor window: under 60 s or under 10 readings is low, 1-5 min is
  medium, 5 min or more is high. Every sustained finding's evidence holds the duration, sample count, mean,
  max, p95, % above threshold, longest streak and a plain-language uncertainty note.
- Analysis: no double-reporting. On a medium or high confidence window, a sustained finding supersedes
  `memory.pressure`, `cpu.busy`, `storage.disk_busy` or `thermal.cpu`. On a low-confidence window, its stats are
  merged into the snapshot finding instead. Without monitor data, no sustained findings are produced.
- Analysis: the RAM upgrade advice and the HDD-to-SSD evidence also use the sustained findings.
- Report: new "Over time" section (HTML and Markdown) with the monitor window, per-metric mean/p95/max/%
  above threshold, self-contained SVG (HTML) or text (Markdown) sparklines for CPU and RAM, and the
  confidence in plain words. Without monitor data it explains how to run `pcdiag scan --monitor 300`.
- Tests: `tests/test_sustained.py` (47 tests).
- Branding: small "Made by NiyiSoft" mark (calligraphic, crown above "Made by" and "NiyiSoft").
  HTML report: bottom-right after the last section, 140 px wide, embedded as data URIs so the report
  stays one self-contained file (navy version, cream version in dark mode; alt text "Made by NiyiSoft").
  It is not fixed-position, so it never covers the report or prints over tables. Markdown report: a
  right-aligned "Made by NiyiSoft" line at the end. Terminal: the scan header reads
  "PC Diagnostics Toolkit <version> - Made by NiyiSoft" (still ASCII only). README: mark at the top.
  New `pcdiag/report/brand.py`; the badge PNGs (about 6 KB each) are stored as base64 text in
  `pcdiag/report/niyisoft_badge_data.py` and, for the README, as SVG wrappers in `docs/brand/`, so the
  repo has no binary files and can be pushed through the GitHub API. Tests: `tests/test_brand.py` (5 tests); the self-contained check in
  `tests/test_report.py` now allows only embedded `data:` image sources.
- Docs: Roadmap rewritten for v0.3.0-v0.6.0 (bi-monthly) from the PC-diagnostics history research.

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
