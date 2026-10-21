# Stata inspection record

Inspection date: **15 October 2026**. Scope: the user's installed Windows StataNow 19 artifacts, their resource/menu/help inventories, and current public Stata documentation. This is a bounded compatibility investigation, **not exhaustive reverse engineering or proof of Stata equivalence**.

Extended **21 October 2026** with a second record covering the installed **macOS Stata 19.5 SE** binary: see [Second record: the macOS Stata 19.5 binary](#second-record-the-macos-stata-195-binary) below. The two records are kept separate because they use different artifacts, different providers and different evidence classes.

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

---

## Second record: the macOS Stata 19.5 binary

Record date: **21 October 2026**. Scope: the installed **Stata 19.5 SE** for
macOS at `/Applications/StataNow/StataSE.app`, inspected with REA and with
local `objdump`/`nm` against a thin copy of the shipped dylib. This record is
about *code and data* in the shipped binary; the record above is about
*resources* in the Windows installation. They are kept apart because the
artifacts, providers and evidence classes differ.

### Target identity

| Artifact | SHA-256 | Notes |
|---|---|---|
| `libstata-se-x86_64.dylib` (thin copy, 76 665 472 bytes) | `9d88f74b55f28ffc361b1014ce69e79c045820680f77ba063ebcc6a4924ff2c9` | the analysis target; `mach-o`, `x86_64` |

The copy is byte-identical to the dylib inside the installed app bundle; it
was copied to `/tmp/reaslice/` only so that the analysis tools and
`objdump` could address it directly.

### Providers and their limits on this target

| Provider | Version | State |
|---|---|---|
| Ghidra | 12.1.4 | available; the native macOS decompiler had to be built by hand from the shipped C++ source, otherwise Ghidra opens the binary but cannot decompile it |
| Hopper | — | available |
| Kuna | 1.833 | available; `search_strings` and `xrefs` work, but the *project* export exceeds `REA_KUNA_PROJECT_TIMEOUT_MS`, so `address_name` / `procedure_info` / `resolve_containing_procedure` are unavailable for this target |
| IDA | — | not available (and not usable: `ida-pro-mcp` rejects IDA Free) |

Evidence records produced during this record:

| Question | Operation | Evidence ID |
|---|---|---|
| Where does the literal `%18.0g` occur? | `search_strings({pattern:"%18.0g"})` | `ev_23b5ee983105174561526dc8e133d46ddf5c0bc642396165cbdfc55ed5afb150` |
| Who references the `summarize` variant `%18.0gc`? | `xrefs({address:"0x400ebc0"})` | `ev_c1841866dbace6d2cb18df73d1038efb0845a7f3c5c010e35ff73a3d3c541c7a` |
| Who references the `export delimited` format? | `xrefs({address:"0x40274c8"})` | `ev_1617839931190de0565d0ad0146cb789bf04830fcea074e4b878bea54fc34d60` |
| Who references the Excel-import format? | `xrefs({address:"0x403f44e"})` | `ev_51e15163f13b9cab1b8edd99f432dbad8ad5c1a92254cb9a2f5c9a7312d9b2bf` |
| Who references the macro-expansion format? | `xrefs({address:"0x4062e34"})` | `ev_a434297c38a6167de529c1b2a570c4641b2f014a298b8cc8db15f873480ed988` |

The last row is the one that decided the macro rule. The cross-reference
graph of the Kuna provider is built from a project export that this
target exceeds the timeout for, so the `xrefs` calls return an empty
result if the target is re-opened after the export has failed. The
records above were captured in one session and the same answers can be
reproduced locally with `objdump` on the thin copy, because file offset
equals virtual address for `__text` and `__cstring` in this dylib.

### The finding that mattered: the macro-expansion format

Stata renders a number as *text* when it substitutes it into a macro. The
format it uses was located by searching the binary for the literal and then
reading the cross-references:

| Address | Literal | Referencing routine | What that routine does |
|---|---|---|---|
| `0x400ebc0` | `%18.0gc` | `_sum_of_wgt_message`, `_buildraw` | the `by`-group weight total in `summarize` |
| `0x40274c8` | `%18.0g` | `_export_delimited` | `export delimited` number formatting |
| `0x403f44e` | `%18.0g` | `_StExcelU_GetCellAsString` | Excel import: a number read from a cell |
| `0x4062e34` | `%18.0g` | `_retval_get_as_string`, `_macsub`, `_macgen`, `_macincr`, `_forvals`, `_listall_uu`, … | **the macro machinery** |

The C string at `0x4062e34` carries the compiler-emitted symbol name
`_fmt_18p0g` ("format, 18, point, 0, g"). Disassembling the four macro
routines shows the same three steps in each of them:

1. load `_fmt_18p0g` into the first argument,
2. call `_sreforms` (Stata's general format routine),
3. trim the padding — `_trimblnk` in `_retval_get_as_string` and `_macsub`,
   `_skipblnk` plus a scan-to-the-next-blank in `_macgen` and `_macincr`.

`_retval_get_as_string` is the routine that turns an `r()`, `e()` or `c()`
value into text; it dispatches on the class through a jump table and, for
the numeric case, formats with `_fmt_18p0g` and trims. This is the direct
source of the rule that `` `r(mean)' `` expands to the **unpadded `%18.0g`**
rendering of the value, and it is what `openstat/macros.py` implements
(`MACRO_FORMAT = "%18.0g"`).

`_macgen` — the routine behind `local x = <expr>` and `global x = <expr>` —
selects the format conditionally: it compares the internal `_vernum` with
`220` and uses `_fmt_18p0g` from Stata 14 onwards, an older format before
that. So the `local`/`global` assignment stores the same `%18.0g` text,
which is why a `local` keeps full precision even though its display rounds.

### Other findings

- **The macOS Stata 19 binaries keep their local symbol names.** The claim
  in the first record that the binary is stripped does not hold for the
  macOS build: `nm -n` and `objdump` show Stata's own routine names
  (`_retval_get_as_string`, `_macsub`, `_macgen`, `_macincr`, `_sreforms`,
  `_fmt_18p0g`, `_export_delimited`, `_StExcelU_GetCellAsString`, …). A
  routine can therefore be located by name instead of by hunting for a
  format string.
- **The overflow markers are the extended-missing ladder.** The dta writer
  builds the bit patterns `0x7fe0010000000000 + n * 2**40` for
  `n = 0 … 25` — the same ladder as the missing values `.a` … `.z`. This
  is how `openstat/formats.py` reproduces the marker a value too large to
  print gets.
- **The default display formats are in the binary as plain strings**:
  `%8.0g` (byte/int), `%10.0g` (double), `%9.0g` (float) and `%9s`
  (strN) sit next to the storage-type name table (`float`, `double`,
  `int`, `long`, `byte`, `strL`).
- **File offset equals virtual address** for `__text` and `__cstring` in
  this dylib, so an address reported by REA can be read straight out of the
  file with a short Python script.

### What this record does not establish

- No StataCorp source was read, copied or reconstructed. Only observable
  behaviour — output, string literals, and the structure of a public
  algorithm — was recovered, and it was re-implemented independently in
  this repository.
- Coverage is limited to the questions listed above. The binary was not
  decompiled end to end; the findings are point observations, each tied to
  a specific address and evidence record.
- Behavioural claims in [compatibility.md](compatibility.md) and
  [formats.md](formats.md) rest on **differential measurement** against the
  installed Stata, not on this record. The two agree where they overlap;
  where they differ, the measurement wins.
