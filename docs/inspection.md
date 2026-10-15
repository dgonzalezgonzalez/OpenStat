# Stata inspection record

Inspection date: **15 October 2026**. Scope: the user's installed Windows StataNow 19 artifacts, their resource/menu/help inventories, and current public Stata documentation. This is a bounded compatibility investigation, **not exhaustive reverse engineering or proof of Stata equivalence**.

## Target identity and evidence

REA `6.5.0`, skill `42`, connected through MCP. `inspect_pe_resources` read both original installed files without executing them. Resource-tree coverage was complete within the selected files and budgets; implementation and runtime coverage were not.

| Artifact | Bytes | SHA-256 | REA Evidence ID |
|---|---:|---|---|
| `StataMP-64.exe` | 86,286,992 | `adfe21cb34aacc3892c1d7670276bd826e90975a550cf23f389b10d91eb17786` | `ev_5f24ecfea9af6c4d2e3f1abd986703ac20307235621dc0347b321e3a40e16ac5` |
| `mp-64.dll` | 58,438,800 | `81d0b6112869edcea915f9b2ca71a1f00b51e7153ef38727b0ee7769042f6640` | `ev_b920495291ae48c80a12efdaf84647816c19d7eaa765fb17a00d54e6dab65adf` |

Both files are PE32+ with machine `34404` (`0x8664`, AMD64). Windows file-version metadata for the executable reports `521,19,5,009`; product version `521.19.5.009`. That metadata is a file observation, not a verified statement of runtime patch level.

The first executable resource request hit REA's default 64 MiB artifact limit. A second request with `max_file_bytes=104857600` succeeded. No partial first result was used. The DLL succeeded with the default limit. No binary bytes or installed implementation were copied into OpenStat.

Original full REA records remain local in ignored `.local/evidence/rea-stata-static.json` (2 records; 1,559,582 bytes when exported). Session/provider status remains in `.local/evidence/rea-session.json`. These machine-local files are intentionally absent from the public repository. Evidence IDs above identify the original records; public summaries cannot replace those records for a byte-level audit.

## Findings and limits

| Finding | Classification | Basis | Boundary |
|---|---|---|---|
| Executable contains 2,063 resource payloads and 4,149 examined directory entries. | Observed | Executable REA ID above | Complete resource tree only. |
| Resource types include 19 menus, 100 dialogs, 96 string-table payloads, 163 icon groups, 1,176 icon images, 154 bitmaps, 38 named PNG payloads and 71 named XAML payloads. | Observed | Executable REA ID above | These include framework/internal assets; counts do not count user-visible features. |
| DLL contains one string-table payload and no menu or icon-group resources. | Observed | DLL REA ID above | Does not mean the DLL lacks statistical features or string data elsewhere. |
| Static menus include File/Edit/View/Window/Help, graph printing/plot selection, docking, matching options, and framework customization. | Derived | Independent standard/MENUEX parser over REA-bound menu file ranges | 19 resources; 156 items, including separators; 125 nonempty labels. Zero unconsumed payload bytes. Static presence does not prove the app displays those entries. |
| Installed menu scripts cover Data, Graphics and Statistics plus import/export and Data Editor variants. | Observed inventory | Seven installed `.mnu` files | Simple quoted-action scan: 1,498 action occurrences; 253 group occurrences, 143 distinct group identifiers. Duplicates/context variants remain counted; these are not unique supported commands. |
| Broad compatibility needs multiple languages, numerical subsystems, data formats, GUI editors and external integrations. | Inferred design requirement | Manual/menu families; [public programming features](https://www.stata.com/features/programming-language/) | A small do-file parser cannot establish this requirement. |
| Native numerical algorithms, parser internals, compiled Mata libraries and plugin ABI implementation remain uninspected. | Unknown | Provider status below | No decompilation or algorithm recovery occurred. |

The local menu decoder and its derived menu records remain under ignored `.local/inspection/` and `.local/evidence/`. The decoder checked each artifact's SHA-256 against the REA record before using reported payload offsets. It did not execute Stata or turn static resources into runtime claims. Installed `.mnu` inspection is ordinary filesystem evidence, not a separate REA Evidence record.

## Deep-provider availability

`binary_session({})` reported no active target and no configured usable deep-analysis provider:

- Ghidra: `not_configured`; `GHIDRA_INSTALL_DIR` absent. Detected Java was a 32-bit Java 8 runtime, which did not meet the provider's JDK requirements.
- Hopper: `unsupported_host` on Windows.
- IDA MCP adapter: `not_configured`.
- REA native UI observation tools: macOS-only on this host.

No provider was installed, no client configuration was changed, and no disassembler was opened. Portable resource inspection remained available. Consequently there are **no observed function bodies, call graphs, native parser semantics or runtime UI traces from REA in this record**. Any separate screen inspection and executable conformance tests must be assessed on their own evidence.

## Installed support inventory

Ordinary recursive file inventory of `ado/base` found:

| File type | Count | Meaning of count |
|---|---:|---|
| `.ado` | 3,954 | Installed script files, including helpers and legacy/versioned code. |
| `.sthlp` | 3,463 | Help files, including historical topics. |
| `.ihlp` | 947 | Help fragments. |
| `.dlg` / `.idlg` | 1,799 / 586 | Dialog definitions/fragments. |
| `.mata` / `.matah` / `.mlib` | 421 / 24 / 30 | Mata text/header/library files. |
| `.class` | 355 | Class implementation files. |
| `.mnu` | 7 | Main include file plus six menu definitions. |
| `.scheme` / `.style` | 26 / 910 | Graphics style files. |
| `.jar` / `.py` | 3 / 2 | Shipped integration files. |
| Documentation PDFs | 37 | Files in the installed `docs` directory, including platform Getting Started guides. |

These counts are a search boundary, **not an enumeration of executable commands or unique features**. No installed source, help corpus, libraries, datasets, screenshots containing license details, icons or other proprietary assets are part of the public deliverable.

## Local snapshot versus latest release

The installed `whatsnew19.sthlp` states StataNow 19.5 updated to **16 April 2025**. Its SHA-256 is `bbbb2f4ab3eab637dd79c7f0f679113d02e6147948c2c73827664de8d13e6338`. The installed `docs/update` contains **08 Apr 2025**; SHA-256 `974e84afc1a86f2e889112264e4b7c0211bc8e52ef0be00cb2c9a17e149610c8`. These dates describe those installed files.

On 15 October 2026, the official [Stata update page](https://www.stata.com/support/updates/) identified **14 October 2026** as the current revision date. Current [StataNow release notes](https://www.stata.com/news/statanow-updates/) and [Stata 19 feature updates](https://www.stata.com/new-in-stata/features/) list features added after the inspected snapshot. No update was performed on the user's installation. Local test results therefore cannot demonstrate compatibility with the latest StataNow revision.

## Investigation checklist

| Original question | Status | Evidence / next requirement |
|---|---|---|
| Identify installed Stata artifacts. | Answered for selected files | Byte identity and format above. |
| Inspect packaged UI resources using REA. | Answered within resource scope | Complete type/name/language trees; independent menu decoding. |
| Catalog feature families. | Partially answered | [Feature inventory](feature-inventory.md), all installed manual families and bounded menu families. Command/option-level completeness remains open. |
| Understand every Stata implementation feature. | Unresolved | No deep provider; bounded inspection cannot establish global completeness. |
| Establish newest StataNow target. | Partially answered | Official public revision and new-feature scope; newest binary not inspected or tested. |
| Prove any do-file, ado, Mata and package will run. | Unresolved | Requires full semantic implementation and much broader conformance testing. |
| Prove UI equivalence. | Unresolved | Resource presence is insufficient; interactions, editors, dialogs, graph/SEM builders, preferences and accessibility require runtime coverage. |

Use this record to guide independent implementation and prioritize conformance probes. Do not describe OpenStat as fully Stata-compatible based on this inspection.
