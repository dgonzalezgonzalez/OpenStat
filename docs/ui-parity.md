# Desktop parity with the Stata window

This document records what the Stata 19.5 desktop *is* on this host and how far
OpenStat's desktop is from it. It is a **bounded, measured** record: the counts
come from the installed Stata resources, and every OpenStat statement comes
from reading `openstat/gui.py`. Nothing here is aspirational, and no StataCorp
resource, label table, icon or dialog file is copied into the repository.

- Manifest: `qa/ui_manifest.json` (generated, not committed — rebuild it with
  `python qa/ui_manifest.py --build`).
- Reader / checker / report: `qa/ui_manifest.py` (`--check` validates the
  manifest against the installed tree and prints the summary above).
- Oracle: `C:\Program Files\StataNow19` (StataNow 19.5, Windows). Menu trees are
  read from `ado/base/*.mnu` and the dialog corpus from `ado/base/**/*.dlg`;
  labels resolve through the installed resource table read-only.
- Observation date: 2026-10-22/23.

## What the manifest measures

| Dimension | Stata 19.5 | OpenStat |
|---|---|---|
| Menu roots | 6 (`Data`, `Graphics`, `Statistics`, `User`, …) | 8 top-level menus |
| Menu nodes | 255 | 48 menu items |
| Menu items | 1,505 (1,492 open a dialog, 3 open help) | 48 |
| Dialog files | 1,799 `.dlg` (1,123 dialogs, 2,715 tabs) | 11 command dialogs |
| Dialog fields | 52,566, of which 14,778 bind to a command option | 0 option-bound controls |
| Window surface | Codejock XTP docking: Command/Variables/Properties docks, Results as a child pane, History and Viewer tabbed | one fixed `QSplitter`, three columns |

Field-type distribution (from the `.dlg` corpus, for future dialog work):
`TEXT` 11,687, `BUTTON` 7,543, `RADIO` 7,352, `CHECKBOX` 7,336, `EDIT` 5,678,
`GROUPBOX` 4,062, `COMBOBOX` 3,542, `SPINNER` 1,532, `VARNAME` 1,143,
`VARLIST` 991.

Parser warnings are recorded rather than hidden: 29 statement keywords and 16
malformed control lines in the corpus are counted in `warnings` and left
uninterpreted, so they cannot silently inflate the field totals.

## Recorded gaps

Nine gaps, each with the observed Stata surface, OpenStat's current surface and
a plan. Status is `missing` or `partial`; nothing here is marked implemented
unless the manifest's OpenStat inventory actually contains it.

| # | Area | Status | Stata | OpenStat |
|---|---|---|---|---|
| 1 | Dock model | missing | XTP docking, floatable/tabbed panes, View roster | one fixed `QSplitter`, panels cannot move or float |
| 2 | Review (History) window | partial | table with `#`, `Command`, `_rc`, filtering | `QListWidget` of command text, no return-code column |
| 3 | Viewer | missing | Viewer dock with Back/Forward/Find/Reload, in-app help | help is a message box / markdown dialog |
| 4 | Preferences | missing | `Edit > Preferences` + General dialog | no preferences surface |
| 5 | Settings persistence | missing | geometry, dock layout, recent files survive restart | nothing saved between sessions |
| 6 | Menu accelerators | partial | every top-level label carries `&`; actions advertise shortcuts | top-level mnemonics exist; Edit actions have no shortcut text |
| 7 | Command dialogs | missing | 1,799 `.dlg` files; controls bound to command options | 10 generated dialogs that concatenate text into a command line |
| 8 | Data Editor operations | partial | edit/browse, sort, filter, pin, variable properties | edit toggle and a go-to-observation box |
| 9 | Help subsystem | partial | `help <topic>` opens the Viewer on a `.sthlp` page (3,463 files) | `help` prints a command line; `docs/compatibility.md` in a dialog |

The five OpenStat dialogs that do have a Stata counterpart name — `generate`,
`histogram`, `regress`, `sort`, `summarize` — are recorded in the manifest as
being *backed by* a Stata dialog file, not as implementations of it.

## Non-goals

- Pixel parity, Stata iconography, Stata branding, or a copy of any `.dlg` or
  `.mnu` file. The manifest reads them; it does not vendor them.
- Reproducing every dialog. The target is the window *structure* Stata uses
  (docks, Review, Viewer, Preferences, option-bound dialogs) with OpenStat's own
  controls, not a clone of 1,123 dialogs.
