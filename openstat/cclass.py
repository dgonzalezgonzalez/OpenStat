"""Stata's ``c()`` system values.

``c()`` holds the values of the session rather than of the data: the
number of observations, the constant pi, the release name, and so on.
The engine keeps a table of them and refreshes it whenever a command
changes the state it reports.

The table below lists **only the names that Stata 19.5 actually
defines**.  A name that is not in the table is *undefined* in Stata:
`` `c(nosuch)' `` expands to the empty string, while ``c(nosuch)`` in
expression position is the error ``c(nosuch) undefined`` (rc 133).  A
long list of plausible-looking names -- ``c(SCS)``, ``c(n_processors)``,
``c(endian)``, ``c(release)``, ``c(cmd)``, ``c(S_level)`` and so on -- was
measured against Stata 19.5 and found to be undefined there, so it is
absent here too.

The values were measured on Stata 19.5 SE in batch mode on this host.
"""
from __future__ import annotations

import os
import platform
import time

#: Values that do not change during a session.  Measured against Stata
#: 19.5 SE on this host; see ``docs/compatibility.md``.
_CONSTANTS = {
    "pi": 3.14159265358979323846,
    "byteorder": "lohi",
    "version": "19.5",
    "flavor": "IC",
    "edition": "BE",
    "os": "Unix",
    "machine_type": "Macintosh (Intel 64-bit)",
    "username": os.environ.get("USER", ""),
    "hostname": platform.node(),
    # Stata reports an unset maximum memory as the missing value.
    "max_memory": ".",
    "console": "console",
    "more": "off",
    "SE": 1,
    "maxint": 32740,
    "current_date": time.strftime("%d %b %Y"),
    "dirsep": os.sep,
}

def build_cclass(engine) -> dict:
    """Return the ``c()`` table for the current state of ``engine``."""
    values = dict(_CONSTANTS)
    values["N"] = len(engine.data)
    values["k"] = len(engine.data.columns)
    values["pwd"] = str(engine.cwd)
    values["seed"] = engine.rng.seed
    values["filename"] = getattr(engine, "last_filename", "") or ""
    packages = getattr(engine, "packages", None)
    values["adopath"] = str(packages.root) if packages is not None else ""
    values["mode"] = getattr(engine, "mode", "batch")
    values["rc"] = getattr(engine, "last_rc", 0) or 0
    return values
