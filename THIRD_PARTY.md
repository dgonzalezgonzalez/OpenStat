# Third-party software and provenance

OpenStat's original source, documentation, synthetic fixtures and vector logo use the MIT license in LICENSE. The project does not include StataCorp binaries, sources, ado/Mata libraries, datasets, screenshots, icons, manuals or license material. Descriptive compatibility names and factual public documentation links do not imply affiliation.

Runtime dependencies keep their own licenses. Consult the installed distributions for authoritative full notices: NumPy/pandas/SciPy/statsmodels (BSD families), Matplotlib (its project license), ReadStat/pyreadstat (BSD/MIT families), openpyxl (MIT), and PySide6/Qt (LGPL/GPL/commercial options). This list is descriptive; OpenStat's MIT license does not relicense these components. PySide6 is an optional separately installed dependency, not statically linked or bundled in this repository. Windows fonts are discovered from the local OS; no font files are distributed.

Downloaded SSC/net packages are separate upstream works stored outside this repository. They retain their licenses, recorded source URL and file hashes. Installing a package neither relicenses it nor establishes that OpenStat can execute it.

Optional QA uses a locally licensed Stata executable as a behavior oracle. Its input scripts and data are original bounded synthetic examples. Reports contain selected numeric outputs, test hashes and factual metadata, not licensed source implementations or personal data.
