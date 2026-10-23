"""Program-support commands used by third-party ado files.

Third-party packages lean on a small set of primitives that exist only to
support programs: ``marksample``/``mark``/``markout`` (the 0/1 to-use
variable), ``tempvar``/``tempname``/``tempfile`` and the ``args``/``gettoken``
argument helpers.

The marker semantics below were measured against Stata 19.5 (MP) rather than
inferred, because several of them are surprising:

* The marker is 0 where an ``if``/``in`` qualifier excludes the observation,
  where the weight is 0 *or missing*, or where a listed variable is missing;
  it is 1 everywhere else.  ``zeroweight`` keeps zero weights eligible but a
  *missing* weight still marks the observation out.
* ``if`` is evaluated with Stata's ordering, in which a missing numeric value
  is larger than every non-missing value, so ``if x >= 1`` is *true* where
  ``x`` is missing.  That is why ``novarlist`` alone can leave those
  observations in the sample.
* fweights must be non-negative integers; a negative weight is ``r(402)``
  ("negative weights encountered") for any weight type, and a noninteger
  fweight is ``r(401)``.  Both checks are made over the observations the
  ``if``/``in`` qualifiers keep, not over the whole dataset.
* A *string* variable in the varlist or in ``markout``'s list is unusable
  without ``strok``: Stata silently sets the whole marker to 0 (rc 0).  With
  ``strok`` the empty string still counts as missing, exactly as it does for
  ``markout``.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from .parser import split_outside, tokens, unquote

_MARK_OPTIONS = {"novarlist", "strok", "zeroweight", "noby"}
_MARKOUT_OPTIONS = {"strok"}


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
                hidden = f"__00000{self._temp_counter()}"
                self.locals[name] = hidden
                if command == "tempvar":
                    # tempvars become real columns later; keep them out of
                    # default varlists just like marker variables.
                    self._hidden_registry().add(hidden)

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
        ``syntax`` command: the ``if``/``in`` qualifiers it captured, the
        weight it captured and the variables in its varlist.  ``mark
        newvar`` creates a named marker from an explicit ``if``/``in``
        qualifier; like Stata's ``mark`` it never looks at a weight.
        """
        from .engine import StataError

        parts = split_outside(arg)
        head = parts[0].strip()
        options = self.options(",".join(parts[1:]), _MARK_OPTIONS)
        words = tokens(head)
        if not words:
            raise StataError("marksample requires a variable name")
        name = words[0]
        if len(words) == 1:
            # ``marksample touse`` reuses the marker described by ``syntax``.
            if name in self.data:
                return
            value = self._syntax_marker(options)
            self._store_marker(name, value)
            return
        # ``mark newvar if ...`` creates a fresh marker variable.  The name is
        # literal -- unlike ``marksample touse``, whose local points at a
        # hidden variable -- so ``mark __000001`` makes exactly that column.
        if name in self.data:
            raise StataError(f"variable {name} already defined", 110)
        qualifier = self._qualifier_text(" ".join(words[1:]))
        _, mask = self.qualifiers(qualifier) if qualifier else (None, self._all_true())
        self.data[name] = pd.Series(
            np.where(mask.to_numpy(), 1, 0), index=self.data.index, dtype=np.int8)
        if name in self._hidden_registry():
            self._hidden_markers = getattr(self, "_hidden_markers", [])
            self._hidden_markers.append(name)

    def _syntax_marker(self, options) -> pd.Series:
        """Build the to-use marker described by the last ``syntax`` command."""
        varlist = getattr(self, "_syntax_varlist", "") or ""
        qualifier = " ".join(part for part in (
            (getattr(self, "_syntax_if", "") or "").strip(),
            (getattr(self, "_syntax_in", "") or "").strip()) if part)
        if qualifier:
            _, mask = self.qualifiers(self._qualifier_text(qualifier))
            value = pd.Series(mask.to_numpy().astype(float), index=self.data.index)
        else:
            value = pd.Series(1.0, index=self.data.index)
        weight = self._syntax_weight_series(value != 0)
        if weight is not None:
            # A missing weight can never enter the sample, so it is marked out
            # even under ``zeroweight``; only a literal zero weight is spared.
            excluded = weight.isna()
            if "zeroweight" not in options:
                excluded = excluded | weight.eq(0)
            value = value.where(~excluded.to_numpy(), 0.0)
        if varlist and "novarlist" not in options:
            for column in self.varlist(varlist):
                missing = self._missing_mask(column, options)
                value = value.where(~missing, 0.0)
        return value

    def _qualifier_text(self, text: str) -> str:
        """Return ``text`` in the form ``engine.qualifiers`` scans for.

        ``syntax`` stores the qualifiers with their keyword, so the
        reconstructed text can begin with ``if``/``in`` at position 0; the
        engine's scanner looks for ``" if "``/``" in "``, i.e. for a
        blank-delimited keyword.
        """
        text = (text or "").strip()
        if not text:
            return ""
        return " " + text

    def _all_true(self) -> pd.Series:
        return pd.Series(True, index=self.data.index, dtype=bool)

    def _missing_mask(self, column, options) -> np.ndarray:
        """Return a boolean mask of observations to mark out for ``column``.

        A string variable is unusable without ``strok``: Stata sets the whole
        marker to 0 rather than marking it row by row (measured on 19.5).
        """
        series = self.data[column]
        if pd.api.types.is_numeric_dtype(series):
            return series.isna().to_numpy()
        if "strok" not in options:
            return np.ones(len(self.data), dtype=bool)
        return (series.isna() | series.eq("")).to_numpy()

    # -- weights -------------------------------------------------------
    def _syntax_weight(self) -> tuple[str, str]:
        """Return the weight type and expression captured by ``syntax``."""
        weight_type = str(self.locals.get("__openstat_syntax_weight") or "").strip().lower()
        expression = str(self.locals.get("__openstat_syntax_weight_exp") or "").strip()
        if not weight_type:
            # Older/alternate ``syntax`` front ends publish ``weight``/``exp``.
            weight_type = str(self.locals.get("weight") or "").strip().lower()
            expression = str(self.locals.get("exp") or "").strip()
        if expression.startswith("="):
            expression = expression.lstrip("=").strip()
        return weight_type, expression

    def _syntax_weight_series(self, sample: pd.Series):
        """Return the weight as a series, validating it over ``sample``.

        Stata only inspects the observations the ``if``/``in`` qualifiers
        keep: a noninteger fweight outside the sample is not an error.
        """
        from .engine import StataError

        weight_type, expression = self._syntax_weight()
        if not weight_type or not expression:
            return None
        values = np.asarray(self.evaluate(expression), dtype=float).reshape(-1)
        if values.size == 1 and len(self.data) != 1:
            values = np.repeat(float(values[0]), len(self.data))
        if values.size != len(self.data):
            raise StataError("weight expression must give one value per observation", 198)
        series = pd.Series(values, index=self.data.index)
        inside = series[sample.to_numpy().astype(bool)]
        usable = inside.to_numpy()
        usable = usable[np.isfinite(usable)]
        if usable.size and (usable < 0).any():
            raise StataError("negative weights encountered", 402)
        if weight_type in {"fweight", "fw", "frequency"}:
            if usable.size and (usable != np.floor(usable)).any():
                raise StataError("noninteger weight", 401)
        return series

    def _store_marker(self, name: str, value: pd.Series) -> None:
        r"""Store a marker variable as a byte, as Stata's ``mark`` does.

        Stata stores the marker in a hidden variable and points the local
        macro at it, so ``marksample touse`` makes ``\`touse'`` expand to
        the hidden variable's name.  The hidden name is registered in the
        engine's ``_hidden_variables`` set so default varlists skip it.
        """
        hidden = self._hidden_name()
        self.data[hidden] = value.astype(np.int8)
        self._hidden_markers = getattr(self, "_hidden_markers", [])
        self._hidden_markers.append(hidden)
        self._hidden_registry().add(hidden)
        self.locals[name] = hidden

    def _hidden_registry(self) -> set:
        """Return the engine's hidden-variable registry, creating it if absent."""
        registry = getattr(self, "_hidden_variables", None)
        if isinstance(registry, set):
            return registry
        registry = set()
        self._hidden_variables = registry
        return registry

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
        options = self.options(",".join(parts[1:]), _MARKOUT_OPTIONS)
        variables = [word for word in words[1:] if word]
        marker = pd.Series(self.data[name], index=self.data.index).fillna(0)
        excluded = np.zeros(len(self.data), dtype=bool)
        if variables:
            for column in self.varlist(" ".join(variables)):
                excluded |= self._missing_mask(column, options)
        keep = marker.to_numpy().astype(bool) & ~excluded
        self.data[name] = pd.Series(np.where(keep, 1, 0), index=self.data.index,
                                    dtype=np.int8)

    def _analysis_weight(self):
        """Return the current weight variable, if any."""
        names = [name for name in self.data if name.lower() in self._weight_variables()]
        return self.data[names[0]] if names else None

    def _weight_variables(self):
        return set()
