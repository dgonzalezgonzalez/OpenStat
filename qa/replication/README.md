# Replication-package validation

Published replication packages contain real-world Stata code.  Running that
code through OpenStat is a much harsher test than the hand-written corpus
in `qa/conformance/`: the packages use command combinations nobody writes
by hand, and they were written against Stata's real behaviour.

Each case names a do-file from a published package plus the working
directory the do-file expects.  The harness in `qa/replication_run.py` runs
the same do-file in Stata (the oracle) and in OpenStat, each with its own
copy of the working directory, and compares the return codes and any
`__OPENSTAT__` sentinel values.

## Sources

| Case | Package | Journal | Notes |
|------|---------|---------|-------|
| `dropout_jpe_descriptives` | [Dropout-JPE](https://github.com/tyleransom/Dropout-JPE) | Journal of Political Economy | Arcidiacono, Aucejo, Maurel & Ransom, "College Attrition and the Dynamics of Information Revelation".  The package ships MATLAB for the structural estimation and Stata for data construction and descriptives. |

## Running

    python qa/replication_run.py --list
    python qa/replication_run.py --case dropout_jpe_descriptives --keep

The `--keep` flag writes both transcripts under
`qa/replication/_artifacts/<case>/` for inspection.
