"""Variable creation and assignment commands.

``generate``, ``replace`` and ``egen`` follow Stata's evaluation rules:
expressions are vectorised, self-referencing subscripts are evaluated
observation by observation, and storage types truncate or promote the way
Stata's storage types do.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .parser import split_outside
from .storage import DEFAULT_FORMAT, DEFAULT_TYPE, INTEGER_RANGE


class GenerationCommands:
    """Mixin implementing the assignment commands."""

    def _generate(self, command, arg):
        from .engine import StataError

        parts = split_outside(arg)
        options = self.options(",".join(parts[1:]), {"by"} if command == "egen" else set())
        base, mask = self.qualifiers(parts[0])
        match = re.fullmatch(
            r"(?:(byte|int|long|float|double|str\d*|strL)\s+)?([A-Za-z_]\w*)\s*=\s*(.*)",
            base, re.S)
        if not match:
            raise StataError("invalid assignment syntax")
        storage, name, expression = match.groups()
        if command == "replace":
            if storage:
                raise StataError("replace does not accept a storage type")
            if name not in self.data:
                raise StataError(f"variable {name} not found", 111)
        else:
            self._new_name(name)
        if storage is None and command in {"generate", "egen"}:
            # Stata's untyped ``generate`` creates a float variable, unless
            # ``set type`` changed the default.
            storage = self.default_type
        recursive = command in {"generate", "replace"} and re.search(
            r"\b" + re.escape(name) + r"\s*\[", expression)
        if recursive:
            if command == "generate":
                raise StataError(f"variable {name} not found", 111)
            if storage not in {None, "double", "float"}:
                raise StataError("recursive assignments require numeric floating storage", 199)
            try:
                for index in self.data.index[mask]:
                    self._tick()
                    self._observation = int(index)
                    value = self.evaluate(expression)
                    if not np.isscalar(value):
                        raise StataError("unsupported recursive vector expression", 199)
                    self.data.at[index, name] = value
            finally:
                self._observation = None
            return
        if command == "egen":
            value = self._egen_value(name, expression, options, mask)
        else:
            self._assignment_mask = mask
            try:
                value = self.evaluate(expression)
            finally:
                self._assignment_mask = None
        value = (pd.Series(value, index=self.data.index) if np.isscalar(value)
                 else pd.Series(np.asarray(value), index=self.data.index))
        if pd.api.types.is_bool_dtype(value):
            value = value.astype(float)
        if storage and storage.startswith("str"):
            value = value.fillna("").astype(str)
            if storage[3:].isdigit():
                value = value.str.slice(0, int(storage[3:]))
        elif storage:
            numeric = pd.to_numeric(value, errors="raise")
            if storage in INTEGER_RANGE:
                # An integer storage type truncates, and a value outside the
                # type's range becomes missing rather than being clamped.
                low, high = INTEGER_RANGE[storage]
                truncated = np.trunc(numeric)
                numeric = truncated.where(truncated.between(low, high))
            value = numeric.astype(np.float32 if storage == "float" else np.float64)
        if command != "replace":
            self.data[name] = "" if value.dtype == object or pd.api.types.is_string_dtype(value.dtype) else np.nan
        try:
            self.data.loc[mask, name] = value.loc[mask]
        except (ValueError, TypeError) as ex:
            raise StataError(f"type mismatch: {ex}", 109) from ex
        if storage:
            self.storage_types[name] = storage
            self.formats[name] = (DEFAULT_FORMAT[storage] if storage in DEFAULT_FORMAT
                                  else "%9s")

    def _egen_value(self, name, expression, options, mask):
        from .engine import StataError

        match = re.fullmatch(r"(\w+)\((.*)\)", expression, re.S)
        if not match:
            raise StataError("egen function required")
        function, source = match[1], match[2]
        if options.get("by"):
            self._by(f"{options['by']}: egen {name} = {expression}", sort=True)
            return self.data[name]
        if function in {"rowtotal", "rowmean", "rowmin", "rowmax", "rownonmiss", "rowmiss",
                        "rowrank", "rowsd", "rowmean"}:
            columns = self.varlist(source)
            frame = self.data[columns]
            functions = {
                "rowtotal": lambda: frame.sum(axis=1),
                "rowmean": lambda: frame.mean(axis=1),
                "rowmin": lambda: frame.min(axis=1),
                "rowmax": lambda: frame.max(axis=1),
                "rownonmiss": lambda: frame.notna().sum(axis=1),
                "rowmiss": lambda: frame.isna().sum(axis=1),
                "rowsd": lambda: frame.std(axis=1, ddof=1),
            }
            return functions[function]()
        if function in {"group", "tag"}:
            columns = self.varlist(source)
            valid = (self.data[columns].notna().all(axis=1)
                     & self.data[columns].ne("").all(axis=1) & mask)
            if function == "group":
                codes = pd.Series(np.nan, index=self.data.index)
                codes.loc[valid] = (pd.factorize(pd.MultiIndex.from_frame(self.data.loc[valid, columns]),
                                                sort=True)[0] + 1)
                return codes
            value = pd.Series(0, index=self.data.index)
            value.loc[valid] = (~self.data.loc[valid].duplicated(columns)).astype(int)
            return value
        if function in {"count", "mean", "total", "sum", "min", "max", "sd", "median",
                        "p50", "p25", "p75", "first", "last", "nvals"}:
            source_value = self.evaluate(source)
            values = pd.Series(source_value, index=self.data.index).loc[mask].dropna()
            if function == "count":
                values = values.loc[values.ne("")]
            if function == "nvals":
                values = values.loc[values.ne("")]
                return values.groupby([self.data.index[mask]]).size().reindex(self.data.index).fillna(0)
            if function == "first":
                return values.groupby([self.data.index[mask]]).first().reindex(self.data.index)
            if function == "last":
                return values.groupby([self.data.index[mask]]).last().reindex(self.data.index)
            if function in {"p50", "p25", "p75"}:
                quantile = {"p50": 0.5, "p25": 0.25, "p75": 0.75}[function]
                return (values.groupby([self.data.index[mask]]).quantile(quantile)
                        .reindex(self.data.index))
            functions = {"mean": values.mean, "total": values.sum, "sum": values.sum,
                         "min": values.min, "max": values.max, "count": values.count,
                         "sd": values.std, "median": values.median}
            return functions[function]()
        raise StataError(f"egen {function}() not implemented", 199)
