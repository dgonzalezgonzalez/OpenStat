# Native compatibility investigation

Continuation date: 18 October 2026. This record extends [the resource inspection](inspection.md). Native inspection guides independent implementation; it does not establish complete compatibility.

## Target and analysis boundary

The selected Windows x64 executable remains byte-identical to the earlier inspection: SHA-256 `adfe21cb34aacc3892c1d7670276bd826e90975a550cf23f389b10d91eb17786`. Its companion DLL remains `81d0b6112869edcea915f9b2ca71a1f00b51e7153ef38727b0ee7769042f6640`. The installed snapshot is not a verified current release.

REA 6.5.0 is available through a scoped local SDK connection, with 207 advertised tools. Ghidra 12.1.4 and an existing 64-bit JDK 21 are selected only for this process. The optional Frida 17.23.2 dependency was installed with a separate REA 6.5.0 runtime solely under ignored `.local/tools/rea-instrumentation/`. No global or client configuration was changed. Full records remain in the ignored `.local/evidence/` directory; proprietary binaries, code, pseudocode and resources are excluded from the public repository.

## Findings ledger

| Question | Classification | Evidence and boundary |
|---|---|---|
| Can the native provider admit this executable? | Observed | `rea-native-resume-open.json` reports PE/x86-64 and the matching SHA-256. Admission alone is not decompilation. |
| Did the first broad native import complete? | Observed failure | `rea-native-resume-exe-overview.json`: `provider_timeout`, configured startup limit 330,000 ms, elapsed 331,964 ms. Owned Ghidra startup diagnostics show entry-point disassembly still working; no function conclusion follows from that attempt. |
| Can focused native inspection avoid repeated broad analysis? | In progress | A fresh persistent REA session uses four caller-declared function seeds, selected from string-reference candidates and chained PE unwind metadata. Six broad discovery analyzers are disabled. These selections and exclusions belong to the analysis profile; seeded names are analyst labels, not recovered source names. |
| Can REA observe native execution on this Windows host? | Observed | Frida observation `ev_35d52bd3f232d2f287f4244c599e4527d55848b6b5638a4e855a59dbbde998c8` successfully spawned only a declared synthetic child and monitored it for 15 seconds. It recorded entries at executable-relative offsets `0x1a3ce50` (7 entries), `0x1b51450` and `0x225e1c0` (at least 20 each; the script deliberately caps each site at 20). No script errors, transport truncation or cleanup error were reported. Function purpose remains a static-analysis inference until decompilation completes. |
| Did that short synthetic script finish? | Ordinary process output | The generated private log records syntax-option/program return `14`, loop values `1,2`, Mata sum `6`, successful named ado loading, and return code `199` when a same-name ado file defines no matching program. A leading display outside an otherwise valid ado program was skipped in this example. This is one bounded probe, not general ado/Mata validation. |

The successful Frida record retains exact target/arguments, injected-script SHA-256 `11fa2b0858d41ebf4cc8cc0af9cb061a0e38472fa6a9bd8edf290b0b46aa99b7`, and all messages. Its own limitations remain explicit: the record has no artifact-identity subject and does not report target OS/architecture as normalized metadata. The separate on-disk SHA-256 above correlates the selected path; no in-memory image digest was established. The generated do-file hash is `38f9183b65adf8234d1704e2047b00d7721e7097da9960549979f09ba6a4251c`; log hash `f6a8ad3e6e4487690bcdf1ccc8d259cb29b172bd6dacb9799f147cd3e8350a2c`. These files and the complete trace remain private.

## Remaining questions

Native ado loading, command dispatch and Mata function behavior require completed decompilation followed by further bounded conformance probes. Numerical algorithms, general compiled Mata libraries, plugin ABI, full historical language semantics and complete UI behavior remain unresolved. The specifically named `observe_native_calls` and `capture_process_scenario` tools do not support Windows in this REA profile; the separate Frida route succeeds. Ordinary shell-driven synthetic comparisons are separate evidence and must not be represented as REA execution traces.

No amount of static inspection alone proves that arbitrary do-files or every downloaded package execute correctly. Package acquisition, ado execution, compiled payload support and numerical equivalence require separate verification.
