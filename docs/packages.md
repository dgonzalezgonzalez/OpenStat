# Package acquisition

OpenStat installs SSC package descriptors with `ssc install name`. It also accepts
HTTP(S) and local directory sources with `net install name, from(source)`.
`replace` updates an installed package; `all` additionally copies ancillary files
into the current working directory. Installation downloads files and records their
hashes. It does not establish that every command supplied by a package can run.

```text
ssc describe ftools
ssc install ftools, replace
ssc install moremata, all replace
net install mypackage, from("C:/packages") replace
ssc uninstall ftools
```

The descriptor parser handles `f`, `F`, `g`, `G`, and `h`. It selects platform
records, supports renamed platform payloads, checks required files, and stops at
`e`. Repeated identical records are downloaded once. `F` and `G` force a file into
the package installation regardless of its extension. `g` and `G` use the
current platform; an explicit platform can be supplied to the Python API for
testing. `WIN64A` is accepted as the older spelling of `WIN64`; `LINUX64P` falls
back to `LINUX64` only when no matching newer-platform payload exists.

These record and ancillary-file rules follow the primary
[package distribution manual](https://www.stata.com/manuals/rnet.pdf) and
[SSC manual](https://www.stata.com/manuals/rssc.pdf).

Remote references such as `../j/join.ado` resolve against their descriptor's
directory on the same origin. Their installation destinations are plain
filenames inside a private directory for each package. Remote directory names
cannot escape that directory. Local-source references remain within the declared
source directory, including after symlink resolution. Absolute payload URLs,
Windows device names, manifest overwrites, and conflicting flattened destinations
are rejected.

`.ado`, help, Mata source, `.mlib`, `.mo`, plugins, and common support files are
installed into the package directory. Other extensions are ancillary by default;
the exact classification is `SYSTEM_SUFFIXES` in `openstat/packages.py`. `F` and
`G` override that classification. Archives remain archives; installing does not
unpack or execute them. Native payloads are preserved as bytes. There is no native
plugin loader or compiled Mata execution claim.

All downloads finish before replacement starts. Package replacement uses staging
and restores the previous installation if a commit fails. Ancillary writes also
roll back on failure. The ownership manifest stores descriptor and payload
SHA-256 hashes, original payload references, platform, and description. Uninstall
removes unchanged owned files, retaining user edits, unowned files, and ancillary
data. Package installations do not share ownership of another package's files.

The default SSC transport remains the archive's HTTP endpoint. Recorded hashes
identify received bytes; HTTP hashes do not authenticate the archive. Explicit
HTTPS sources use certificate validation. `OPENSTAT_PACKAGE_MAX_BYTES` controls
the per-file size bound, default 512 MiB. No fixed package file-count cap applies.

## Python API

```python
from pathlib import Path
from openstat.packages import PackageManager

packages = PackageManager(Path("./ado"))
description = packages.describe("ftools")
manifest = packages.install("ftools", replace=True, all_files=True,
                            ancillary_dir=Path("./examples"))
path = packages.find("join", recursive=True)
inventory = packages.installed()
result = packages.uninstall("ftools")
```

`describe()` returns `files` as records with `source`, `destination`, `directive`,
`platform`, and `ancillary`. `install()` returns `files` as a dictionary mapping
installed filenames to SHA-256. `uninstall()` reports `removed` and `preserved`.
Third-party files keep their upstream licenses.

## Acquisition verification, 2026-10-18

The live archive supplied all requested files for five packages. Payloads remain
in ignored local storage; no upstream program source is redistributed in this
repository. Every installed file was rehashed after installation.

| Package | Installed files | Ancillary files | Bytes acquired | Sibling references | Compiled files |
| --- | ---: | ---: | ---: | ---: | ---: |
| distinct | 2 | 0 | 7,174 | 0 | 0 |
| ftools | 44 | 0 | 220,742 | 18 | 0 |
| reghdfe | 22 | 0 | 749,164 | 1 | 0 |
| estout | 13 | 1 | 812,045 | 2 | 0 |
| moremata | 81 | 1 | 2,822,410 | 4 | 4 |

The four compiled files are `.mlib` libraries. Acquisition tests do not execute
these packages. The previously verified `distinct.ado` SHA-256 remains
`a3533d8951b745b34b7389049177157767e93a863c795682d9574f422a991dbe`.

Descriptor SHA-256 records:

```text
distinct  87c852a10a56b5a43464442500d682370a365bf9bb5d962efd817c662c37f74b
ftools    615bd04adc290a2f1885538bebd9b2e6943076dc84a314abfb504c0299f0b8b9
reghdfe   f74762bfe6efba763c57208365ce38f3aa53600467b635e52720def90b4fd022
estout    ce6a758916b9d3e8f4dcf3445d7159e830ef80eaaa38c6799dba5be9bf9a3903
moremata  304b44df0790b8ef8642f96c205b074faa9cd08925e9c513559112b7a1c760ec
```

The offline test suite covers descriptor records, platform selection, renamed
files, duplicate records, remote sibling references, local sources, compiled
payload preservation, ancillary data, rollback, ownership, and recursive lookup.
Unimplemented areas include archive-wide installation certification, package
dependency resolution (the descriptor does not declare such dependencies),
automatic updates, checksum sidecar validation, and universal package execution.
