"""Program-support commands used by third-party ado files.

Third-party packages lean on a small set of primitives that exist only to
support programs: ``marksample``/``mark``/``markout`` (the 0/1 to-use
variable), ``tempvar``/``tempname``/``tempfile`` and the ``args``/``gettoken``
argument helpers.

The behaviour implemented here follows Stata's documented rules for
``mark`` (see the ``mark`` help entry): the marker variable is 0 where the
weight is 0, where an ``if``/``in`` restriction excludes the observation, or
where a listed variable is missing; it is 1 everywhere else.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from .parser import split_outside, tokens, unquote


class ProgramSupportCommands:
    """Mixin implementing the program-support commands."""

    # ------------------------------------------------------------------
    # Temporary names
    # ------------------------------------------------------------------
    def _temp_names(self, command, arg):
        """Implement ``tempfile``, ``tempname`` and ``tempvar``.

        Stata allocates hidden names from one counter, so ``tempvar`` and
        ``tempname`` share the ``__000000`` series.  ``tempfile`` returns a
        path in a temporary directory.
        """
        names = [name for name in arg.split() if name]
        if not names:
            return
        for name in names:
            if command == "tempfile":
                root = Path(tempfile.mkdtemp(prefix="openstat-"))
                self.locals[name] = str(root / f"{name}.dta")
            else:
                self.locals[name] = f"__00000{self._temp_counter()}"

    def _temp_counter(self) -> int:
        """Return the next value of Stata's hidden-name counter."""
        self._temp_id = getattr(self, "_temp_id", 0) + 1
        return self._temp_id

    def _hidden_name(self) -> str:
        """Allocate a hidden variable name such as ``__000007``."""
        return f"__00000{self._temp_counter()}"

    # ------------------------------------------------------------------
    # mark / marksample / markout
    # ------------------------------------------------------------------
    def _marksample(self, arg):
        """Implement ``marksample`` and ``mark``.

        ``marksample touse`` builds the marker described by the preceding
        ``syntax`` command: the ``if``/``in`` qualifiers it captured plus
        the variables in its varlist, following the rules documented in
        Stata's ``mark`` help entry.  ``mark newvar`` creates a named
        marker from an explicit ``if``/``in`` qualifier.
        """
        from .engine import StataError

        parts = split_outside(arg)
        head = parts[0].strip()
        options = self.options(",".join(parts[1:]),
                               {"novarlist", "strok", "zeroweight", "noby", "sysmisok"})
        words = tokens(head)
        if not words:
            raise StataError("marksample requires a variable name")
        name = words[0]
        if len(words) == 1:
            # ``marksample touse`` reuses the marker created by ``syntax``.
            if name in self.data:
                return
            value = self._syntax_marker(options)
            self._store_marker(name, value)
            return
        # ``mark newvar if ...`` creates a fresh marker variable.
        if name in self.data:
            raise StataError(f"variable {name} already defined", 110)
        base = " ".join(words[1:])
        _, mask = self.qualifiers(base)
        value = mask.astype(float)
        if "zeroweight" not in options:
            weight = self._analysis_weight()
            if weight is not None:
                value = value.where(weight != 0, 0.0)
        self._store_marker(name, value)

    def _syntax_marker(self, options) -> pd.Series:
        """Build the to-use marker described by the last ``syntax`` command."""
        varlist = getattr(self, "_syntax_varlist", "") or ""
        if_condition = getattr(self, "_syntax_if", "") or ""
        in_range = getattr(self, "_syntax_in", "") or ""
        _, mask = self.qualifiers(f"{if_condition} {in_range}".strip())
        value = mask.astype(float)
        if "zeroweight" not in options:
            weight = self._analysis_weight()
            if weight is not None:
                value = value.where(weight != 0, 0.0)
        if varlist and "novarlist" not in options:
            columns = self.varlist(varlist)
            for column in columns:
                series = self.data[column]
                if pd.api.types.is_numeric_dtype(series):
                    missing = series.isna()
                else:
                    missing = series.isna() | series.astype(str).eq("")
                    if "strok" in options:
                        missing = series.isna()
                value = value.where(~missing, 0.0)
        return value

    def _store_marker(self, name: str, value: pd.Series) -> None:
        """Store a marker variable as a byte, as Stata's ``mark`` does.

        Stata stores the marker in a hidden variable and points the local
        macro at it, so ``marksample touse`` makes ``\`touse'`` expand to
        the hidden variable's name.
        """
        hidden = self._hidden_name()
        self.data[hidden] = value.astype(np.int8)
        self._hidden_markers = getattr(self, "_hidden_markers", [])
        self._hidden_markers.append(hidden)
        self.locals[name] = hidden

    def _markout(self, arg):
        """Implement ``markout``: exclude observations with missing values."""
        from .engine import StataError

        parts = split_outside(arg)
        words = tokens(parts[0])
        if not words:
            raise StataError("markout requires a marker variable")
        name = words[0]
        if name not in self.data:
            raise StataError(f"variable {name} not found", 111)
        options = self.options(",".join(parts[1:]), {"strok", "sysmisok"})
        variables = [word for word in words[1:] if word]
        marker = pd.Series(self.data[name], index=self.data.index).fillna(0)
        if variables:
            columns = self.varlist(" ".join(variables))
            for column in columns:
                series = self.data[column]
                if pd.api.types.is_numeric_dtype(series):
                    missing = series.isna()
                else:
                    if "strok" in options:
                        missing = series.isna()
                    else:
                        missing = series.isna() | series.astype(str).eq("")
                marker = marker.where(~missing, 0.0)
        self.data[name] = marker

    def _analysis_weight(self):
        """Return the current weight variable, if any."""
        names = [name for name in self.data if name.lower() in self._weight_variables()]
        return self.data[names[0]] if names else None

    def _weight_variables(self):
        return set()
