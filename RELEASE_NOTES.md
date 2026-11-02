<!-- Added in v1.0.0 -->
# PC Diagnostics Toolkit v1.0.0

Released: 2026-11-02. The first complete edition. It finds what is slowing your PC down and gives you one plan to fix it: free fixes first, then upgrades that fit, then replace. Advice only: pcdiag never changes your PC, and nothing leaves your computer.

## New in 1.0

- **Light and dark report.** The HTML report has a new green "health check" look that follows your Windows light/dark setting. Printing always uses the light version.
- **Opens by itself.** Double-click the app: it scans, opens the report in your browser and keeps the window open until you press Enter.
- **Repair guides for phones and consoles.** `pcdiag guide phone` or `pcdiag guide console` lists common problems (storage full, battery drain, not charging, overheating, controllers and more). Each guide gives free steps first, the tools you'd need, and clear signs to stop and see a technician.
- **macOS and Linux** (from source): the scan fills in battery health, drive type and health, graphics and more using built-in tools.
- **Microsoft Store version** on the way, with a `pcdiag` command that works in any terminal.

Future updates may add more.

Copyright (c) NiyiSoft. All rights reserved.

---

<!-- Added in v0.3.0 -->
# PC Diagnostics Toolkit v0.3.0: Make my PC better without buying a new one

Released: 2026-11-02. Includes v0.2.0 (live monitoring and the NiyiSoft mark), which ships to main in the same release.

## Highlights

- **Your improvement plan.** `python -m pcdiag plan` turns a scan into one ordered list: free fixes
  first (startup apps, heavy programs, disk cleanup, graphics driver, power plan, Windows Update,
  cooling), then cheap upgrades with price ranges, and a new PC only when upgrades can't help.
  A failing drive always comes first: back up, then replace. The plan is also in the HTML report.
- **Only upgrades that fit.** pcdiag checks soldered vs socketed RAM, the maximum the board takes,
  free slots, stick size and DDR type, SATA vs NVMe and laptop vs desktop. Upgrades that can't fit
  are never suggested; anything Windows doesn't report says "can't tell".
- **Troubleshooting by symptom.** `python -m pcdiag troubleshoot slow-boot --scan` (also restarts,
  freezes, overheating, disk-noise) gives ordered checks with clear stop points, and marks where
  this PC's evidence says to start.
- **Better temperature readings.** With LibreHardwareMonitor running, pcdiag reads real CPU sensors
  and says whether a temperature came from a confirmed CPU sensor or a guess from its label.

- **How it works, explained.** New `docs/HOW_IT_WORKS.md` walks through the code with a diagram,
  and the key modules have plain-English comments on what each part does and why.

## Review fixes

- More reliable plans: missing readings show "?" instead of hiding the plan, no "Windows" wording on
  Linux, correct advice for NVMe and SATA drives of unknown type, replacement drives up to 16 TB, and
  real RAM stick sizes on three-slot boards.
- Fairer drive and battery advice: SMART warnings aren't called "failing", drives with reallocated
  sectors get a backup step, and desktops with a UPS don't get laptop battery advice.
- Clearer reports: no repeated advice under the plan, no "Nothing needed" while warnings remain, and
  "Couldn't check" lists everything that couldn't be read.
- Monitor only logs real CPU sensors as the CPU temperature.
- No RAM upgrade is suggested for soldered RAM, in every output.
- The thermal paste step relies on a throttling flag, not on wording.
- The Markdown report escapes Markdown characters in names and other text.
- Battery advice asks you to check whether your model's battery is user-replaceable.

Advice only: pcdiag never changes settings, installs anything or makes network requests.
Not in this release: detecting an empty M.2 slot, crash history from the Event Log (v0.5.0), live prices.
