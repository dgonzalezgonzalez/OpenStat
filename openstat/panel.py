"""Panel-data commands: ``xtset`` and ``xtsum``.

Stata's panel commands need the panel identifier and the time variable.
``xtset idvar timevar`` records them, and ``xtsum`` then reports the
overall, between and within variation of a variable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .formats import format_value


class PanelCommands:
    """Mixin providing the ``xt`` family of commands."""

    # ------------------------------------------------------------------
    # xtset
    # ------------------------------------------------------------------
    def _xtset(self, arg: str) -> None:
        """Record the panel identifier and the time variable."""
        parts = [part for part in _split_arguments(arg) if part]
        if not parts:
            raise _stata_error("xtset requires an id variable and a time variable", 100)
        if len(parts) < 2:
            raise _stata_error("xtset requires an id variable and a time variable", 100)
        idvar, timevar = parts[0], parts[1]
        for name in (idvar, timevar):
            if name not in self.data.columns:
                raise _stata_error(f"variable {name} not found", 111)
        self.panel_id = idvar
        self.panel_time = timevar
        # Stata sorts the data by the panel variables when they are set.
        self.data = self.data.sort_values([idvar, timevar], kind="stable").reset_index(drop=True)
        self._xtset_report(idvar, timevar)

    def _xtset_report(self, idvar: str, timevar: str) -> None:
        """Print the report ``xtset`` gives about the panel structure."""
        times = self.data[timevar]
        if pd.api.types.is_numeric_dtype(times):
            low, high = times.min(), times.max()
            unique = np.sort(times.dropna().unique())
            step = np.diff(unique)
            if len(unique) < 2:
                delta = "."
            elif (step == step[0]).all():
                delta = "1 unit" if step[0] == 1 else f"{step[0]:g} units"
            else:
                delta = "1 unit"
            span = f"{low:g} to {high:g}"
        else:
            delta = "."
            span = f"{times.min()} to {times.max()}"
        # Stata calls a panel strongly balanced when every group covers
        # the same time periods, weakly balanced when the groups are
        # consecutive but not identical, and unbalanced otherwise.
        counts = self.data.groupby(idvar).size()
        sizes = sorted(set(counts.tolist()))
        if len(sizes) == 1:
            balance = "strongly balanced"
        elif self._time_complete(idvar, timevar):
            balance = "weakly balanced"
        else:
            balance = "unbalanced"
        self.emit(f"       panel variable:  {idvar} ({balance})")
        self.emit(f"        time variable:  {timevar}, {span}")
        self.emit(f"                delta:  {delta}")

    def _time_complete(self, idvar: str, timevar: str) -> bool:
        """Report whether every panel covers the same time periods.

        A panel is complete when each group observes every time period
        that the panel as a whole contains.
        """
        frame = self.data[[idvar, timevar]].dropna()
        if frame.empty:
            return False
        periods = set(frame[timevar].unique())
        for _, group in frame.groupby(idvar):
            if set(group[timevar].unique()) != periods:
                return False
        return True

    # ------------------------------------------------------------------
    # xtsum
    # ------------------------------------------------------------------
    def _xtsum(self, arg: str) -> None:
        """Summarize a variable's overall, between and within variation."""
        names = self.varlist(arg)
        if not names:
            raise _stata_error("no variables specified", 100)
        if getattr(self, "panel_id", None) is None:
            raise _stata_error("no panel variables declared", 460)
        self.emit("Variable         |      Mean   Std. Dev.       Min        Max |    Observations")
        self.emit("-" * 17 + "+" + "-" * 44 + "+" + "-" * 16)
        for name in names:
            if not pd.api.types.is_numeric_dtype(self.data[name]):
                raise _stata_error(f"variable {name} is not numeric", 109)
            self._xtsum_one(self.data, name)

    def _xtsum_one(self, frame: pd.DataFrame, name: str) -> None:
        """Print the three ``xtsum`` rows for one variable."""
        panel = frame[self.panel_id]
        values = frame[name]
        valid = values.notna() & panel.notna()
        groups = frame.loc[valid].groupby(self.panel_id, sort=True)
        overall = values[valid].astype(float)
        means = groups[name].mean()
        counts = groups.size()
        grand_mean = overall.mean()
        # The within deviation is measured from each group's own mean; the
        # between deviation is measured from the grand mean.
        within = overall - groups[name].transform("mean")
        between = means - grand_mean

        overall_sd = overall.std(ddof=1) if len(overall) > 1 else np.nan
        # Stata's between and within standard deviations use the number of
        # groups and the number of observations respectively.
        between_sd = between.std(ddof=1) if len(between) > 1 else np.nan
        within_sd = within.std(ddof=1) if len(within) > 1 else np.nan
        t_bar = len(overall) / len(counts) if len(counts) else np.nan

        self.returns = {
            "N": int(len(overall)),
            "mean": overall.mean(),
            "sd": overall_sd,
            "min": overall.min(),
            "max": overall.max(),
            "n_groups": int(len(counts)),
            "Tbar": t_bar,
        }
        # Stata puts the variable name in a nine column field and the
        # statistic in an eight column field.
        label = name[:9].ljust(9)
        # The within row reports the deviations recentered on the grand
        # mean, which is how Stata presents them.
        recentered = within + grand_mean
        # Stata's numeric block is 44 columns wide: the mean ends in
        # column 10, the standard deviation in 21, the minimum in 32 and
        # the maximum in 43.
        self.emit(f"{label}{'overall':<8}|{_number(overall.mean(), 10)}"
                  f"{_number(overall_sd, 11)}{_number(overall.min(), 11)}"
                  f"{_number(overall.max(), 11)} |     N ={len(overall):>8d}")
        self.emit(f"{'':<9}{'between':<8}|{'':<10}{_number(between_sd, 11)}"
                  f"{_number(means.min(), 11)}{_number(means.max(), 11)}"
                  f" |     n ={len(counts):>8d}")
        # An unbalanced panel reports the average number of observations
        # per group instead of the common number.
        balanced = counts.nunique() == 1
        label = "T" if balanced else "T-bar"
        self.emit(f"{'':<9}{'within':<8}|{'':<10}{_number(within_sd, 11)}"
                  f"{_number(recentered.min(), 11)}{_number(recentered.max(), 11)}"
                  f" | {label} ={t_bar:>8.6g}")


def _number(value, width: int) -> str:
    """Format a number in a fixed-width field, as Stata's tables do.

    Stata's ``xtsum`` lines the numbers up in fixed columns, and the
    layout is part of what a user sees, so the width matters.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ".".rjust(width)
    return format_value(value, f"%{width}.7g")


def _split_arguments(text: str) -> list[str]:
    """Split a command's argument list on whitespace outside quotes."""
    parts: list[str] = []
    current = ""
    quoted = False
    for char in text:
        if char == '"':
            quoted = not quoted
            continue
        if char.isspace() and not quoted:
            if current:
                parts.append(current)
                current = ""
            continue
        current += char
    if current:
        parts.append(current)
    return parts


def _stata_error(message: str, rc: int):
    """Build the engine's error type without importing the engine module."""
    from .engine import StataError

    return StataError(message, rc)
