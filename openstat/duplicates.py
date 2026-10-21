"""Stata's ``duplicates`` command.

``duplicates`` finds groups of observations that share the same values in
a varlist.  The subcommands are ``drop``, ``report``, ``list`` and
``tag``; ``drop`` needs the ``force`` option to actually remove rows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class DuplicatesCommands:
    """Mixin providing the ``duplicates`` command."""

    def _duplicates(self, arg: str) -> None:
        """Implement ``duplicates drop|report|list|tag varlist``."""
        parts = arg.split(None, 1)
        if not parts:
            raise _error("duplicates requires a subcommand", 198)
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if action not in {"drop", "report", "list", "tag"}:
            raise _error(f"invalid subcommand {action}", 198)
        varlist, options = _split_varlist(rest)
        if not varlist:
            raise _error("duplicates requires a varlist", 100)
        names = self.varlist(varlist)
        if action == "tag" and "generate" not in options:
            raise _error("option generate() required", 198)
        frame = self.data[names]
        groups = frame.groupby(names, sort=True, dropna=False).indices
        # ``groups`` maps a key to the row positions that share it.
        ordered = sorted(groups.items(), key=lambda item: item[1][0])
        self.emit(f"Duplicates in terms of {' '.join(names)}")
        if action == "drop":
            self._duplicates_drop(ordered, options)
        elif action == "report":
            self._duplicates_report(ordered)
        elif action == "list":
            self._duplicates_list(ordered, names)
        else:
            self._duplicates_tag(ordered, options)

    def _duplicates_drop(self, ordered, options) -> None:
        """Remove the surplus copies, keeping the first of each group."""
        if "force" not in options:
            raise _error("option force required", 198)
        keep = set()
        for _, positions in ordered:
            keep.add(positions[0])
        mask = self.data.index.isin(keep)
        removed = int((~mask).sum())
        self.data = self.data.loc[mask].reset_index(drop=True)
        self.emit(f"({removed} observations deleted)")

    def _duplicates_report(self, ordered) -> None:
        """Tabulate how many groups have each number of copies.

        The ``observations`` column counts every observation in the groups
        of that size, not the number of groups.
        """
        counts = pd.Series([len(positions) for _, positions in ordered])
        histogram = counts.value_counts().sort_index()
        self.emit("--------------------------------------")
        self.emit("   copies | observations       surplus")
        self.emit("----------+---------------------------")
        for copies, groups in histogram.items():
            observations = int(copies * groups)
            surplus = observations - int(groups)
            self.emit(f"{copies:>9} |{observations:>13}{surplus:>15}")
        self.emit("--------------------------------------")

    def _duplicates_list(self, ordered, names) -> None:
        """List the observations of every group that has more than one."""
        header = "group:   obs:" + "".join(f"{name:>4}" for name in names)
        self.emit("  +" + "-" * (len(header) - 1) + "+")
        self.emit("  |" + header + " |")
        self.emit("  |" + "-" * (len(header) - 1) + "|")
        for number, (_, positions) in enumerate(ordered, start=1):
            for position in positions:
                values = "".join(f"{_show(self.data.iloc[position][name]):>4}"
                                 for name in names)
                self.emit(f"  |{number:>6}{position + 1:>7}{values} |")
        self.emit("  +" + "-" * (len(header) - 1) + "+")

    def _duplicates_tag(self, ordered, options) -> None:
        """Create a variable that tags the first observation of each group."""
        name = options.get("generate")
        if not name or name is True:
            raise _error("option generate() required", 198)
        name = str(name)
        keep = {positions[0] for _, positions in ordered}
        tags = [0 if position in keep else 1 for position in range(len(self.data))]
        self.data[name] = pd.Series(tags, index=self.data.index, dtype="int64")
        self.emit("tagging " + name + " (1 = duplicate)")


def _show(value) -> str:
    """Render a value the way Stata's ``duplicates list`` does."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "."
    if isinstance(value, float) and float(value).is_integer():
        return str(int(value))
    return str(value)


def _split_varlist(text: str) -> tuple[str, dict]:
    """Split ``varlist [, options]`` into its two parts."""
    if "," in text:
        varlist, options = text.split(",", 1)
    else:
        varlist, options = text, ""
    parsed: dict = {}
    for match in __import__("re").finditer(r"([A-Za-z_]\w*)(?:\(([^()]*)\))?", options):
        parsed[match[1].lower()] = match[2] if match[2] is not None else True
    return varlist.strip(), parsed


def _error(message: str, rc: int):
    """Build the engine's error type without importing the engine module."""
    from .engine import StataError

    return StataError(message, rc)
